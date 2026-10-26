import hashlib
import json
import time
from datetime import datetime, timezone
from .policy import (Blocked, UnusableOutput, cost_of, dependencies, inspect_text, low_risk, matches,
                     redact, safe_path, validate_changes)
from .review import ci_green, judge, reviewable

TERMINAL = {"DONE", "BLOCKED"}


def redact_title(title):
    return redact(" ".join(title.split()))[:180]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def eligible(issue, cfg):
    return issue.get("state") == "open" and "pull_request" not in issue and \
        issue["user"]["login"] in cfg["trusted_authors"] and \
        cfg["issue_label"] in {x["name"] for x in issue["labels"]}


class Engine:
    def __init__(self, config, store, github, agent, reviewer=None):
        self.config, self.store, self.gh, self.agent = config, store, github, agent
        self.reviewer = reviewer
        self.repos = {r["name"]: r for r in config["repositories"]}

    def specification(self, repo, issue):
        body = issue.get("body") or ""
        if len(body) + len(issue["title"]) > 20000:
            raise Blocked("Issue exceeds specification budget")
        inspect_text(body)
        inspect_text(issue["title"])
        if "## Acceptance criteria" not in body:
            raise Blocked("Issue requires an explicit ## Acceptance criteria section")
        for number in dependencies(body):
            if number == issue["number"]:
                raise Blocked("Issue depends on itself")
            dep = self.gh.issue(repo, number)
            if "pull_request" in dep or dep["state"] != "closed" or dep.get("state_reason") != "completed":
                raise Blocked("Dependency is not completed: #" + str(number))

    def tick(self):
        with self.store.lock():
            if self.config["dry_run"]:
                planned = []
                for name, cfg in self.repos.items():
                    self.gh.metadata(name)
                    for issue in self.gh.issues(name):
                        if not eligible(issue, cfg):
                            continue
                        try:
                            self.specification(name, issue)
                            result = "ELIGIBLE"
                        except Blocked as exc:
                            result = str(exc)
                        planned.append({"repo": name, "issue": issue["number"], "title": redact_title(issue["title"]), "result": result})
                self.store.event(None, "DRY_RUN", planned=planned)
                return {"dry_run": True, "planned": planned, "agent_called": False, "github_writes": False}
            active = [r for r in self.store.rows() if r["state"] not in TERMINAL]
            if not active:
                for name, cfg in self.repos.items():
                    self.gh.metadata(name)
                    for issue in self.gh.issues(name):
                        if eligible(issue, cfg) and self.store.get(name, issue["number"]) is None:
                            active = [self.store.add(name, issue["number"])]
                            break
                    if active:
                        break
            if not active:
                return {"state": "IDLE"}
            row = active[0]
            data = json.loads(row["data"])
            phase = row["state"]
            try:
                if phase in ("BUILDING", "REVIEWING"):
                    raise Blocked("Interrupted model/review call; automatic replay forbidden")
                self.step(row, data)
            except Exception as exc:
                # Fail closed on transport failure, quota failure, policy failure or unexpected bug.
                resume = self.store.get(row["repo"], row["issue"])["state"]
                data.update(resume=resume, reason=str(exc) if isinstance(exc, Blocked) else type(exc).__name__)
                self.store.save(row, "BLOCKED", data)
            current = self.store.get(row["repo"], row["issue"])
            return {"job": current["id"], "repo": current["repo"], "issue": current["issue"],
                    "state": current["state"], "reason": json.loads(current["data"]).get("reason")}

    def current_issue(self, row, cfg, data):
        issue = self.gh.issue(row["repo"], row["issue"])
        data["issue_title"] = redact_title(issue["title"])
        if not eligible(issue, cfg):
            raise Blocked("Issue opt-in/author/open state changed")
        self.specification(row["repo"], issue)
        spec_hash = digest([issue["title"], issue.get("body")])
        if data.get("spec_hash", spec_hash) != spec_hash:
            raise Blocked("Specification changed; create a new reviewed issue")
        data["spec_hash"] = spec_hash
        return issue

    def activity(self, row, stage, **fields):
        self.store.event(row["id"], "STEP", stage=stage, **fields)

    def step(self, row, data):
        repo, phase = row["repo"], row["state"]
        if repo not in self.repos:
            raise Blocked("Repository removed from configuration")
        cfg = self.repos[repo]
        self.gh.metadata(repo)
        policy_hash = digest(cfg)
        if data.get("policy_hash", policy_hash) != policy_hash:
            raise Blocked("Repository policy changed during job")
        data["policy_hash"] = policy_hash
        # Reconcile a completed remote merge before requiring the issue to be open.
        if phase in ("REVIEW", "AWAITING_HUMAN"):
            self.activity(row, "review")
            pr = self.gh.pr(repo, data["pr"])
            self.verify_pr(row, data, pr)
            if pr.get("merged"):
                self.activity(row, "done")
                self.gh.close_issue(repo, row["issue"])
                self.store.save(row, "DONE", data)
                return
            if pr["state"] != "open":
                raise Blocked("PR closed without merge")
        if phase == "QUEUED":
            self.activity(row, "spec")
        issue = self.current_issue(row, cfg, data)
        if phase == "QUEUED":
            meta = self.gh.metadata(repo)
            if "repairs" not in data:
                # A fresh job. A job that comes back to QUEUED after a rejected output
                # keeps its counters: the call that was already billed must stay
                # counted, or the budget could be exceeded by repeated retries.
                data.update(repairs=0, calls=0)
            # The base is the current main, not the one this job happened to start
            # with. A job in this phase has published nothing, so there is nothing to
            # keep consistent with an older base -- and a stale one is worse than
            # useless: the change is built on it, and if main moved on in the
            # meantime the pull request conflicts with the branch it targets, which no
            # repair can undo because the change is bound to the head the job made.
            data.update(base_branch=meta["default_branch"], branch=f"ado/issue-{row['issue']}",
                        base=self.gh.branch_head(repo, meta["default_branch"]))
            self.build(row, data, cfg, issue, [], repair=bool(data.get("output_reason")))
        elif phase == "PUBLISHING":
            self.publish(row, data)
        elif phase in ("REVIEW", "AWAITING_HUMAN"):
            self.activity(row, "checks")
            evidence = self.gh.evidence(repo, pr)
            current_digest = digest(data["all_changes"])
            decision, feedback = judge(pr, evidence, cfg, data.get("minimax_review"), current_digest)
            if decision != "WAIT":
                self.activity(row, "review")
            if time.time() - data["review_started"] > self.config["review_timeout_seconds"]:
                raise Blocked("Review/CI/human-merge deadline exceeded")
            # The MiniMax review is a gate on the current head, so it runs as soon as the
            # head is reviewable and its CI is green -- whatever else is outstanding. It
            # used to run only when the evidence was otherwise completely clean, which
            # stopped it from ever running once a finding from an earlier head was
            # outstanding. The findings it returns join the repair feedback on the next pass.
            if cfg.get("review_provider", "coderabbit") == "minimax" and phase == "REVIEW" and \
                    not self.reviewed(data, current_digest) and \
                    ci_green(pr, evidence, cfg) and reviewable(pr):
                if self.reviewer is None:
                    raise Blocked("MiniMax reviewer unavailable; no fallback")
                self.verify_diff(row, data)
                # Recoverable GitHub reads must happen before REVIEWING is persisted.
                self.activity(row, "review")
                originals, _ = self.gh.snapshot(repo, data["base"], cfg)
                # Persist the in-flight review BEFORE using the subscription.
                # Interrupted calls cannot be automatically replayed.
                self.store.save(row, "REVIEWING", data)
                for review_attempt in range(2):
                    try:
                        result = self.reviewer.review(issue, originals, data["all_changes"], data["head"])
                        break
                    except UnusableOutput as exc:
                        if review_attempt == 0:
                            # The invocation completed but its answer was unusable. Retry
                            # once in this same review stage without spending the Repair
                            # budget. A plain Blocked is deliberately not caught here.
                            self.activity(row, "review",
                                          reason="MiniMax review output was unusable; retrying once")
                            continue
                        # A second unusable answer is terminal for this invocation.
                        # Persist BLOCKED directly instead of saving REVIEW and relying
                        # on tick()'s exception handler: a worker stop between those
                        # writes could otherwise replay the review. resume=REVIEW keeps
                        # an explicit human retry available.
                        data.update(
                            resume="REVIEW",
                            reason="MiniMax review output was unusable after one retry "
                                   "(レビュー出力が使用不能であった)",
                        )
                        self.store.save(row, "BLOCKED", data)
                        return
                # The remote head and diff must remain identical during review.
                fresh_pr = self.gh.pr(repo, data["pr"])
                self.verify_pr(row, data, fresh_pr)
                self.verify_diff(row, data)
                data["minimax_review"] = {
                    "sha": data["head"], "changes_digest": current_digest,
                    "verdict": result["verdict"], "findings": result["findings"]}
                self.store.save(row, "REVIEW", data)
                return
            if decision == "WAIT":
                # A previously clean, handed-off PR may regress when the base moves.
                # A human would have to change the head to fix "behind"/"dirty",
                # invalidating the job's SHA-bound identity. Revoke the handoff
                # explicitly rather than leaving an impossible merge pending.
                if phase == "AWAITING_HUMAN" and pr.get("mergeable_state") in ("behind", "dirty"):
                    raise Blocked("Manual handoff invalidated: PR became behind or conflicted; "
                                  "resolve PR and issue manually; do not resume this job")
                # Keep conflicted, behind and unknown-mergeability PRs in REVIEW.
                # Never synthesize a clean state to bypass the review gate.
                stage = "checks" if any(x.startswith(("missing CI", "running CI")) for x in feedback) else "review"
                self.store.event(row["id"], "WAIT", stage=stage, reasons=feedback)
                return
            if decision == "REPAIR":
                if phase == "AWAITING_HUMAN":
                    raise Blocked("Review changed after handoff; human attention required")
                if data["repairs"] >= self.config["max_repairs"]:
                    # Every finding left is raised against an earlier head, so the model
                    # has nothing new to act on: the repair that was just published is
                    # what the reviewer has to look at. Wait for that human instead of
                    # blocking the job on a budget it can no longer spend.
                    if self.stale_only(feedback, data.get("head")):
                        self.store.event(row["id"], "WAIT", stage="review",
                                         reasons=["waiting for review threads to be resolved"])
                        return
                    raise Blocked("Maximum repair attempts reached")
                self.build(row, data, cfg, issue, feedback, repair=True)
                return
            # Re-fetch all evidence immediately before merge, tied to same PR and head.
            fresh = self.gh.pr(repo, data["pr"])
            self.verify_pr(row, data, fresh)
            self.verify_diff(row, data)
            auto = self.config["auto_merge"] and cfg.get("auto_merge", False) and \
                "ado:low-risk" in {x["name"] for x in issue["labels"]} and low_risk(data["all_changes"], cfg)
            if auto and cfg.get("review_provider") == "minimax":
                raise Blocked("MiniMax self-review never authorizes auto-merge")
            if not auto:
                if phase != "AWAITING_HUMAN":
                    self.store.save(row, "AWAITING_HUMAN", data)
                return
            if fresh["base"]["sha"] != data["base"] or self.gh.branch_head(repo, data["base_branch"]) != data["base"]:
                raise Blocked("Base branch moved; rebase/re-review required")
            if judge(fresh, self.gh.evidence(repo, fresh), cfg)[0] != "PASS":
                raise Blocked("Review evidence changed before merge")
            self.gh.assert_protection(repo, data["base_branch"], cfg, self.gh.evidence(repo, fresh))
            self.activity(row, "merge")
            self.gh.merge(repo, data["pr"], data["head"])
            # Closing is a separate, retry-safe step on the next poll.
            self.store.save(row, "REVIEW", data)
        else:
            raise Blocked("Unknown state")

    def usable(self, changes, cfg, files, modes, data):
        """Check the output of a call that has already returned. Raises only UnusableOutput.

        Everything here runs after the model call completed, so a failure means the answer
        was rejected, not that a call was interrupted. That distinction is the whole reason
        these raise a recoverable type: an interrupted call may already have been billed and
        must never be replayed, while a completed one is spent either way.
        """
        try:
            if isinstance(changes, dict) and not changes:
                raise Blocked("Model refused the work (no changes were proposed)")
            validate_changes(changes, cfg, files)
            for path in changes:
                parts = path.split("/")
                for i in range(1, len(parts) + 1):
                    mode = modes.get("/".join(parts[:i]))
                    if mode and mode not in ("040000", "100644"):
                        raise Blocked("Symlink, executable or submodule boundary")
                    if i < len(parts) and mode == "100644":
                        raise Blocked("File used as parent directory")
            all_changes = {**data.get("all_changes", {}), **changes}
            if len(all_changes) > cfg.get("max_files", 5):
                raise Blocked("Empty/oversized change set")
            # The budget is measured in the bytes the model wrote. A file that was
            # patched costs what the patch cost, not what the file weighs, so a
            # surgical change in a large file stays affordable. A file that was
            # replaced in full costs what it weighs, as before.
            spent = 0
            for path, content in all_changes.items():
                if path in changes:
                    spent += cost_of(changes, path, content)
                else:
                    spent += data.get("costs", {}).get(path, len(content.encode()))
            if spent > cfg.get("max_change_bytes", 50000):
                raise Blocked("Change byte budget exceeded")
        except UnicodeEncodeError as exc:
            # A value that cannot be encoded as UTF-8 -- a lone surrogate in a JSON
            # string, say -- can never be committed. The call completed, so this is a
            # rejected answer, not an interrupted one: it must follow the bounded repair
            # path instead of leaving the job in BUILDING, where a retry is refused.
            raise UnusableOutput("Model output is not valid UTF-8 text") from exc
        except Blocked as exc:
            # One exit type for the whole post-call window, so build() can tell a rejected
            # answer from an interrupted call without re-inspecting which check fired.
            raise UnusableOutput(str(exc)) from exc
        return all_changes

    def reviewed(self, data, digest):
        """Is there a MiniMax review bound to this head and this exact content?"""
        review = data.get("minimax_review")
        return isinstance(review, dict) and review.get("sha") == data.get("head") and \
            review.get("changes_digest") == digest

    def unchanged(self, changes, cfg, files):
        """Is this a valid proposal that changes nothing?

        The paths and the values are checked first: a malformed answer can compare
        equal to the snapshot -- a null value where the snapshot has none -- and must
        not be mistaken for a confirmation that there is nothing to do.
        """
        if not isinstance(changes, dict) or not changes:
            return False
        for path, content in changes.items():
            if not isinstance(content, str):
                return False
            try:
                safe_path(path)
            except Blocked:
                return False
            if not matches(path, cfg["allowed_paths"]):
                return False
        return all(files.get(path) == content for path, content in changes.items())

    def stale_only(self, feedback, head):
        """Is every finding raised against a head other than the one being reviewed?"""
        items = [item for item in feedback if isinstance(item, dict)]
        return bool(items) and all(item.get("kind") == "thread" and item.get("sha") != head
                                   for item in items)

    def outstanding(self, feedback, head):
        """Is there a finding that applies to the head that is being repaired?

        A thread raised against an earlier head is stale, but it is still an outstanding
        finding: the change it refers to may well survive in the current head. Only a
        finding that applies to the current head obliges the model to change something.
        """
        return any(item.get("kind") != "thread" or item.get("sha") == head
                   for item in feedback if isinstance(item, dict))

    def settled_for(self, settled, findings, head):
        """Has this head already been found to have nothing outstanding for these findings?

        A thread a human resolved disappears from the findings, so the set shrinks. That is
        not a new finding: the ones that remain were already evaluated against this head,
        so the answer would be the same. A finding that is new or different is not among
        the settled ones, and it must go back to the model.
        """
        if settled.get("head") != head:
            return False
        stored = settled.get("items")
        if stored is None:
            # A settlement recorded before the items were kept: compare it as a whole.
            return settled.get("feedback") == digest(findings)
        return set(digest(item) for item in findings) <= set(stored)

    def build(self, row, data, cfg, issue, feedback, repair=False):
        parent = data.get("head", data["base"])
        # The review findings and the reason the last answer was rejected are kept apart.
        # The reason is for the model only: it must not decide what counts as an
        # outstanding finding, nor change which findings a head was settled against.
        findings = list(feedback)
        reason = data.get("output_reason")
        model_feedback = findings + ([{"kind": "output", "body": reason}] if reason else [])
        settled = data.get("settled") or {}
        # A settlement is checked before the budget, because it spends no call. A job
        # whose budget is spent but whose head has nothing outstanding must still be
        # able to wait for a human rather than block on a budget it cannot spend.
        if repair and not self.outstanding(findings, parent) and \
                self.settled_for(settled, findings, parent):
            # This head was already found to have nothing outstanding for these findings.
            # Do not spend another model call to learn the same thing -- and do not charge
            # a call to the budget either: no call is made here.
            self.activity(row, "settled", reason="no change outstanding at this head")
            self.store.save(row, "REVIEW", data)
            return
        if data["calls"] >= 1 + self.config["max_repairs"]:
            raise Blocked("Model call budget exhausted")
        self.activity(row, "context")
        files, modes = self.gh.snapshot(row["repo"], parent, cfg)
        data["calls"] += 1
        if repair:
            data["repairs"] += 1
        data["output_reason"] = None            # the reason is now in the feedback
        self.store.save(row, "BUILDING", data)  # reserve budget BEFORE any model invocation
        try:
            self.activity(row, "implement", repairing=bool(model_feedback), file_count=len(files))
            changes = self.agent.generate(issue, files, cfg, model_feedback)
            if repair and data.get("pr") and not self.outstanding(findings, parent) and \
                    self.unchanged(changes, cfg, files):
                # Every outstanding finding was raised against an earlier head and the
                # current head already answers it, so the model had nothing to change.
                # Publishing a no-op is forbidden; waiting for a human to resolve the stale
                # threads is the correct outcome, and it must not cost a repair attempt.
                # This only applies to a job that already published a pull request: before
                # that, an answer that changes nothing is a refusal, and it is rejected as
                # such rather than leaving a job with no pull request behind.
                data["repairs"] -= 1
                data["settled"] = {"head": parent, "feedback": digest(findings),
                                   "items": [digest(item) for item in findings]}
                self.activity(row, "settled", reason="no change outstanding at this head")
                self.store.save(row, "REVIEW", data)
                return
            self.activity(row, "validate")
            all_changes = self.usable(changes, cfg, files, modes, data)
        except UnusableOutput as exc:
            # The call completed and was billed; only its answer was rejected. Replaying
            # spends another call, but the job is otherwise unrecoverable, so feed the
            # reason back as one bounded repair instead of discarding published work.
            #
            # Every other failure escapes untouched: it happened before the call returned,
            # so it may already have been billed and must not be replayed. The BUILDING
            # state persisted above is what marks that -- retry() refuses to resume from it.
            if data["repairs"] >= self.config["max_repairs"]:
                raise
            self.activity(row, "reject", reason=str(exc))
            # Remember why the answer was rejected, so the next attempt -- this retry
            # or a later repair driven by the review -- can tell the model. It is
            # persisted before the state is restored, so a manual retry finds it too.
            data["output_reason"] = str(exc)
            # The call finished, so nothing is in flight. Restore a phase a human can
            # retry from before the next build does any pre-call work: if that work
            # fails transiently the job would otherwise be left in BUILDING, which
            # retry() refuses as an interrupted call that was never interrupted.
            self.store.save(row, "REVIEW" if data.get("pr") else "QUEUED", data)
            # The original review findings are carried forward too. The retry is still
            # working on the same findings; replacing them with the rejection reason
            # alone left the model with no idea what it had been asked to fix.
            self.build(row, data, cfg, issue, feedback, repair=True)
            return
        data.update(changes=changes, all_changes=all_changes, parent=parent,
                    date=datetime.now(timezone.utc).isoformat(), intent_sha=None,
                    costs={**data.get("costs", {}), **getattr(changes, "costs", {})})
        self.store.save(row, "PUBLISHING", data)

    def publish(self, row, data):
        self.activity(row, "publish")
        # Commit generation is deterministic for stored parent/content/date; repeated POSTs are safe.
        if not data.get("intent_sha"):
            data["intent_sha"] = self.gh.make_commit(row["repo"], data["parent"], data["changes"], data["date"], row["issue"])
            self.store.save(row, "PUBLISHING", data)
        self.gh.publish_ref(row["repo"], data["branch"], data["intent_sha"], data.get("head"))
        data["pr"] = self.gh.ensure_pr(row["repo"], data["branch"], data["base_branch"], row["issue"])
        data.update(head=data["intent_sha"], review_started=time.time())
        self.store.save(row, "REVIEW", data)

    def verify_pr(self, row, data, pr):
        if pr["head"]["sha"] != data["head"] or pr["head"]["ref"] != data["branch"] or \
           pr["base"]["ref"] != data["base_branch"] or \
           (pr["head"].get("repo") or {}).get("full_name") != row["repo"] or \
           pr["base"]["repo"]["full_name"] != row["repo"]:
            raise Blocked("PR identity/head changed externally")

    def verify_diff(self, row, data):
        files = self.gh.changed_files(row["repo"], data["pr"])
        if {f["filename"] for f in files} != set(data["all_changes"]):
            raise Blocked("Remote PR file set differs from validated intent")
        for file in files:
            raw = data["all_changes"][file["filename"]].encode()
            expected = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            if file["status"] not in ("added", "modified") or file["sha"] != expected:
                raise Blocked("Remote PR content differs from validated intent")
