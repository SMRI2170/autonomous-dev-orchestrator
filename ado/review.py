"""SHA-bound review evidence. Free-text praise never grants approval."""
from .policy import Blocked, redact


def required_check(pr, evidence, required):
    """The latest check-run for one required check at this head, or None."""
    sha = pr["head"]["sha"]
    relevant = [c for c in evidence["checks"] if c["name"] == required["name"] and
                c.get("app", {}).get("slug") == required["app"] and c.get("head_sha") == sha]
    if not relevant:
        return None
    return max(relevant, key=lambda c: c["id"])


def ci_green(pr, evidence, config):
    """Is every required check complete and successful at this head?"""
    for required in config["required_checks"]:
        check = required_check(pr, evidence, required)
        if check is None or check.get("status") != "completed" or check.get("conclusion") != "success":
            return False
    return True


def reviewable(pr):
    """Can this head be reviewed at all?

    The head must be stable: not a draft, not conflicted, not behind, and not held by a
    pre-receive hook. A pending third-party status ("unstable") or an unresolved review
    thread ("blocked") does not stop the model from reviewing: the first is somebody
    else's bot, the second is the human gate this tool deliberately leaves in place.
    Requiring "clean" made the review unreachable on any repository whose ruleset
    requires review threads to be resolved, which is every repository it guards.
    """
    if pr.get("draft") or pr.get("mergeable") is not True:
        return False
    return pr.get("mergeable_state") not in ("behind", "dirty", "unknown", "draft", "has_hooks")


def judge(pr, evidence, config, minimax_review=None, changes_digest=None):
    sha = pr["head"]["sha"]
    checks = evidence["checks"]
    feedback, waiting = [], []
    for required in config["required_checks"]:
        check = required_check(pr, evidence, required)
        if check is None:
            waiting.append("missing CI " + required["name"])
            continue
        if check.get("status") != "completed":
            waiting.append("running CI " + required["name"])
        elif check.get("conclusion") != "success":
            feedback.append({"kind": "ci", "name": required["name"],
                             "conclusion": check.get("conclusion"),
                             "summary": redact((check.get("output") or {}).get("summary") or "")[:8000]})
    latest, verdicts = {}, {}
    for review in sorted(evidence["reviews"], key=lambda r: r["id"]):
        latest[review["user"]["login"]] = review
        if review.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            verdicts[review["user"]["login"]] = review
    provider = config.get("review_provider", "coderabbit")
    bot = latest.get(config["coderabbit_login"])
    if provider == "coderabbit":
        if not bot or bot.get("commit_id") != sha or bot.get("user", {}).get("type") != "Bot":
            waiting.append("missing CodeRabbit review at current SHA")
        elif bot["state"] == "CHANGES_REQUESTED":
            feedback.append({"kind": "review", "body": redact(bot.get("body") or "")[:8000]})
        elif bot["state"] != "APPROVED":
            waiting.append("CodeRabbit has not APPROVED current SHA")
    elif provider == "minimax":
        if not isinstance(minimax_review, dict) or minimax_review.get("sha") != sha or \
                minimax_review.get("changes_digest") != changes_digest:
            waiting.append("missing MiniMax review at current SHA")
        elif minimax_review.get("verdict") == "REQUEST_CHANGES":
            findings = minimax_review.get("findings")
            if not isinstance(findings, list) or not findings:
                waiting.append("invalid MiniMax review evidence")
            else:
                for finding in findings:
                    feedback.append({"kind": "review",
                                     "body": redact(finding["path"] + ": " + finding["message"])[:8000]})
        elif minimax_review.get("verdict") != "APPROVE" or minimax_review.get("findings") != []:
            waiting.append("invalid MiniMax approval evidence")
    else:
        raise Blocked("Unknown review provider")
    for review in verdicts.values():
        if review.get("state") == "CHANGES_REQUESTED":
            # CodeRabbit is optional in MiniMax mode. Human review vetoes persist.
            if provider == "minimax" and review["user"]["login"] == config["coderabbit_login"]:
                continue
            # Outstanding changes-requested remains a veto even from an earlier commit.
            if review.get("commit_id") != sha:
                waiting.append("stale changes-requested review requires dismissal/re-review")
            elif review is not bot:
                feedback.append({"kind": "review", "body": redact(review.get("body") or "")[:8000]})
    for thread in evidence["threads"]:
        if thread["isResolved"]:
            continue
        comments = thread["comments"]["nodes"]
        if not comments:
            waiting.append("unresolved old review thread")
            continue
        body = redact(comments[0]["body"])[:8000]
        # The commit can be null: a force push can drop the commit a thread refers to.
        thread_sha = (comments[0].get("commit") or {}).get("oid")
        if not thread["isOutdated"] and thread_sha == sha:
            feedback.append({"kind": "thread", "body": body, "sha": sha})
        else:
            # The finding was raised against an earlier head, but it is still unresolved
            # and the change it refers to may well survive in the current head. Leaving it
            # out of the feedback silently dropped the finding: the model was never told
            # about it, so the repair loop could not converge and the job stalled. Feed it
            # back, and keep blocking PASS until a human resolves the thread.
            feedback.append({"kind": "thread", "body": body, "sha": thread_sha})
            waiting.append("unresolved old review thread")
    # Never repair while a required check is still running/missing: avoid burning retries.
    if any(x.startswith(("missing CI", "running CI")) for x in waiting):
        return "WAIT", waiting
    if feedback:
        return "REPAIR", feedback
    if waiting:
        return "WAIT", waiting
    if pr.get("draft") or pr.get("mergeable") is not True or pr.get("mergeable_state") != "clean":
        return "WAIT", ["PR is draft, conflicted, behind or mergeability is unknown"]
    return "PASS", []
