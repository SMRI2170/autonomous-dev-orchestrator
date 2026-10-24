import json
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from ado.engine import Engine, digest
from ado.policy import Blocked, UnusableOutput
from ado.state import Store
from tests.fakes import Agent, GitHub, config, issue


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = config(self.temp.name)
        self.store = Store(self.temp.name)
        self.gh = GitHub()
        self.agent = Agent()
        self.engine = Engine(self.cfg, self.store, self.gh, self.agent)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def tick(self):
        return self.engine.tick()["state"]

    def publish(self):
        self.assertEqual(self.tick(), "PUBLISHING")
        self.assertEqual(self.tick(), "REVIEW")


    def setup_minimax_review(self, verdict="APPROVE", findings=None):
        self.cfg["repositories"][0]["review_provider"] = "minimax"
        self.engine.reviewer = Mock()
        self.engine.reviewer.review.return_value = {
            "verdict": verdict, "findings": findings if findings is not None else []}
        return self.engine.reviewer

    def test_minimax_review_approval_is_bound_to_current_sha(self):
        reviewer = self.setup_minimax_review()
        self.publish()
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(reviewer.review.call_count, 1)
        record = json.loads(self.store.get("owner/project", 1)["data"])
        self.assertEqual(record["minimax_review"]["sha"], self.gh.head)
        self.assertEqual(record["minimax_review"]["verdict"], "APPROVE")
        self.assertEqual(self.tick(), "AWAITING_HUMAN")
        self.assertEqual(reviewer.review.call_count, 1)
        self.assertEqual(self.gh.merge_calls, 0)

    def test_minimax_review_findings_feed_normal_repair_loop(self):
        reviewer = self.setup_minimax_review(
            "REQUEST_CHANGES", [{"path": "docs/glossary.md", "severity": "major",
                                 "message": "Glossary definition is wrong"}])
        self.publish()
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(self.tick(), "PUBLISHING")
        self.assertEqual(self.agent.calls, 2)
        self.assertIn("Glossary definition is wrong", self.agent.inputs[1][0]["body"])
        self.assertEqual(reviewer.review.call_count, 1)

    def test_minimax_does_not_review_before_ci_passes(self):
        reviewer = self.setup_minimax_review()
        self.publish()
        original = self.gh.evidence
        def pending(repo, pr):
            evidence = original(repo, pr)
            evidence["checks"][0]["status"] = "in_progress"
            return evidence
        self.gh.evidence = pending
        self.assertEqual(self.tick(), "REVIEW")
        reviewer.review.assert_not_called()

    def test_minimax_snapshot_failure_can_retry_before_model_call(self):
        reviewer = self.setup_minimax_review()
        self.publish()
        original_snapshot = self.gh.snapshot
        def fail_snapshot(*args):
            raise Blocked("temporary GitHub snapshot failure")
        self.gh.snapshot = fail_snapshot
        self.assertEqual(self.tick(), "BLOCKED")
        data = json.loads(self.store.get("owner/project", 1)["data"])
        self.assertEqual(data["resume"], "REVIEW")
        reviewer.review.assert_not_called()
        self.gh.snapshot = original_snapshot
        self.store.retry(1)
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(reviewer.review.call_count, 1)
        self.assertEqual(self.gh.merge_calls, 0)

    def test_minimax_review_interruption_is_not_replayed(self):
        reviewer = self.setup_minimax_review()
        reviewer.review.side_effect = RuntimeError("interrupted")
        self.publish()
        self.assertEqual(self.tick(), "BLOCKED")
        data = json.loads(self.store.get("owner/project", 1)["data"])
        self.assertEqual(data["resume"], "REVIEWING")
        with self.assertRaises(Blocked):
            self.store.retry(1)
        self.assertEqual(reviewer.review.call_count, 1)

    def test_minimax_never_automerges_even_if_enabled(self):
        self.setup_minimax_review()
        self.cfg["auto_merge"] = True
        self.cfg["repositories"][0]["auto_merge"] = True
        self.publish()
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(self.tick(), "BLOCKED")
        self.assertEqual(self.gh.merge_calls, 0)

    def test_missing_minimax_reviewer_fails_closed(self):
        self.cfg["repositories"][0]["review_provider"] = "minimax"
        self.publish()
        self.assertEqual(self.tick(), "BLOCKED")
        self.assertEqual(self.agent.calls, 1)

    def test_dry_run_never_calls_minimax_reviewer(self):
        reviewer = self.setup_minimax_review()
        self.cfg["dry_run"] = True
        self.assertTrue(self.engine.tick()["dry_run"])
        reviewer.review.assert_not_called()

    def test_dry_run_has_no_agent_or_remote_write_or_live_claim(self):
        self.cfg["dry_run"] = True
        result = self.engine.tick()
        self.assertEqual(result["planned"][0]["result"], "ELIGIBLE")
        self.assertEqual(self.agent.calls, 0)
        self.assertEqual(self.gh.published, 0)
        self.assertEqual(len(self.store.rows()), 0)

    def test_default_waits_for_human_then_closes_issue(self):
        self.publish()
        self.assertEqual(self.tick(), "AWAITING_HUMAN")
        self.assertEqual(self.gh.merge_calls, 0)
        self.gh.merged = True
        self.assertEqual(self.tick(), "DONE")
        self.assertEqual(self.gh.items[1]["state"], "closed")
        self.assertEqual(self.tick(), "IDLE")

    def test_full_loop_repair_merge_close_next_issue(self):
        self.cfg["auto_merge"] = True
        self.cfg["repositories"][0]["auto_merge"] = True
        self.publish()
        self.gh.review = "FAIL"
        self.assertEqual(self.tick(), "PUBLISHING")
        self.assertEqual(self.agent.calls, 2)
        self.assertEqual(self.agent.inputs[1][0]["kind"], "review")
        self.assertEqual(self.tick(), "REVIEW")
        self.gh.review = "PASS"
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(self.gh.merge_calls, 1)
        self.assertEqual(self.tick(), "DONE")
        self.gh.items[2] = issue(2)
        self.assertEqual(self.tick(), "PUBLISHING")

    def test_max_three_repairs_persist_across_restart_and_retry(self):
        self.publish()
        self.gh.review = "FAIL"
        for _ in range(3):
            self.assertEqual(self.tick(), "PUBLISHING")
            self.assertEqual(self.tick(), "REVIEW")
        self.engine = Engine(self.cfg, self.store, self.gh, self.agent)
        self.assertEqual(self.tick(), "BLOCKED")
        self.assertEqual(self.agent.calls, 4)
        # A retry cannot buy another repair: the engine enforces the budget, so the
        # job blocks again without spending a model call.
        self.store.retry(1)
        self.assertEqual(self.tick(), "BLOCKED")
        self.assertEqual(self.agent.calls, 4)
        data = json.loads(self.store.get("owner/project", 1)["data"])
        self.assertEqual(data["reason"], "Maximum repair attempts reached")

    def test_the_store_does_not_refuse_a_retry_because_of_the_repair_budget(self):
        # Regression: the store compared the repair counter against a limit of its own,
        # so once the budget was spent every retry was refused -- including retries of
        # failures that spend no repair at all, such as a transient publish error.
        row = self.store.add("owner/project", 1)
        self.store.save(row, "BLOCKED", {"repairs": 3, "resume": "REVIEW"})
        self.store.retry(row["id"])
        self.assertEqual(self.store.get("owner/project", 1)["state"], "REVIEW")

    def unusable_agent(self, rejections):
        """Make the next `rejections` calls fail as unusable output, then behave normally."""
        remaining = [rejections]
        inner = self.agent.generate

        def generate(issue, files, config, feedback):
            if remaining[0] > 0:
                remaining[0] -= 1
                self.agent.calls += 1
                self.agent.inputs.append(list(feedback))
                raise UnusableOutput("Model returned output that is not a strict changes object")
            return inner(issue, files, config, feedback)

        self.agent.generate = generate

    def test_a_finding_the_head_already_answers_waits_instead_of_burning_a_repair(self):
        # A finding raised against an earlier head is fed back so the model can act on it.
        # When the current head already answers it, the model has nothing to change, and a
        # no-op must not be published, must not cost a repair, and must not be asked again.
        content = "# Glossary\nPR: pull request. Revision 1.\n"
        calls = []

        def generate(issue, files, config, feedback):
            calls.append(feedback)
            if len(calls) == 1:
                return {"docs/glossary.md": content}
            # Later calls return the published content unchanged: nothing left to do.
            return dict(files)

        self.agent.generate = generate
        self.gh.threads = [{"isResolved": False, "isOutdated": True,
                            "comments": {"nodes": [{"body": "old finding", "commit": {"oid": "old"}}]}}]
        self.assertEqual(self.tick(), "PUBLISHING")
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(self.tick(), "REVIEW")          # repair attempt finds nothing to do
        record = json.loads(self.store.get("owner/project", 1)["data"])
        self.assertEqual(record["repairs"], 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.tick(), "REVIEW")          # must not ask the model again
        self.assertEqual(len(calls), 2)
        record = json.loads(self.store.get("owner/project", 1)["data"])
        self.assertEqual(record["repairs"], 0)
        self.assertEqual(self.gh.published, 1)           # no no-op publication

    def test_the_minimax_review_runs_while_a_third_party_status_is_pending(self):
        # Regression: the review used to require mergeable_state == "clean". On a
        # repository whose ruleset requires review threads to be resolved, a PR with
        # threads is "blocked" and one with a pending bot status is "unstable", so the
        # review never ran. No real job in this project had ever produced a review.
        for state in ("unstable", "blocked"):
            with self.subTest(state=state):
                self.setUp()
                reviewer = self.setup_minimax_review()
                self.publish()
                self.gh.mergeable_state = state
                self.assertEqual(self.tick(), "REVIEW")
                self.assertEqual(reviewer.review.call_count, 1)

    def test_the_minimax_review_does_not_run_on_a_conflicted_or_behind_head(self):
        for state in ("dirty", "behind", "unknown"):
            with self.subTest(state=state):
                self.setUp()
                reviewer = self.setup_minimax_review()
                self.publish()
                self.gh.mergeable_state = state
                self.assertEqual(self.tick(), "REVIEW")
                reviewer.review.assert_not_called()

    def test_a_stale_thread_does_not_stop_the_minimax_review(self):
        # An unresolved thread from an earlier head waits for a human. It must not stop
        # the MiniMax review, which is the only remaining automatable step.
        reviewer = self.setup_minimax_review()
        self.publish()
        self.gh.threads = [{"isResolved": False, "isOutdated": True,
                            "comments": {"nodes": [{"body": "old finding", "commit": {"oid": "old"}}]}}]
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(reviewer.review.call_count, 1)

    def test_a_finding_at_the_current_head_still_obliges_the_model_to_change_something(self):
        # The counterpart of the test above: a finding raised against the head being
        # repaired is outstanding, so the model is asked again even when the head did
        # not move and the previous answer was a no-op.
        content = "# Glossary\nPR: pull request. Revision 1.\n"
        calls = []

        def generate(issue, files, config, feedback):
            calls.append(feedback)
            if len(calls) == 1:
                return {"docs/glossary.md": content}
            return dict(files)

        self.agent.generate = generate
        self.assertEqual(self.tick(), "PUBLISHING")
        self.assertEqual(self.tick(), "REVIEW")
        self.gh.threads = [{"isResolved": False, "isOutdated": False,
                            "comments": {"nodes": [{"body": "current finding",
                                                    "commit": {"oid": self.gh.head}}]}}]
        self.tick()
        self.assertGreater(len(calls), 2)

    def test_a_settled_head_does_not_spend_the_model_call_budget(self):
        # Regression: a settled head was charged a call on every poll even though no
        # model call was made, so the job blocked on the call budget within a few polls.
        content = "# Glossary\nPR: pull request. Revision 1.\n"
        calls = []

        def generate(issue, files, config, feedback):
            calls.append(feedback)
            return {"docs/glossary.md": content} if len(calls) == 1 else dict(files)

        self.agent.generate = generate
        self.gh.threads = [{"isResolved": False, "isOutdated": True,
                            "comments": {"nodes": [{"body": "old finding", "commit": {"oid": "old"}}]}}]
        self.assertEqual(self.tick(), "PUBLISHING")
        self.assertEqual(self.tick(), "REVIEW")
        for _ in range(6):
            self.assertEqual(self.tick(), "REVIEW")
        record = json.loads(self.store.get("owner/project", 1)["data"])
        self.assertEqual(record["calls"], 2)      # one real call, one rejected retry
        self.assertEqual(record["repairs"], 0)
        self.assertEqual(len(calls), 2)

    def test_a_transient_failure_in_the_output_retry_leaves_a_retryable_phase(self):
        # Regression: the retry after a rejected output did pre-call work (a snapshot)
        # while the job still sat in BUILDING, so a transient failure there stranded it
        # in a phase retry() refuses as an interrupted call that was never interrupted.
        self.agent.generate = Mock(side_effect=UnusableOutput("rejected output"))
        original = self.gh.snapshot
        state = {"calls": 0}

        def flaky_snapshot(*args):
            state["calls"] += 1
            if state["calls"] == 2:
                raise Blocked("temporary GitHub snapshot failure")
            return original(*args)

        self.gh.snapshot = flaky_snapshot
        self.assertEqual(self.tick(), "BLOCKED")
        data = json.loads(self.store.get("owner/project", 1)["data"])
        self.assertNotEqual(data.get("resume"), "BUILDING")
        self.gh.snapshot = original
        self.store.retry(1)
        self.assertIn(self.store.get("owner/project", 1)["state"], ("QUEUED", "REVIEW"))

    def test_stale_findings_wait_for_a_human_once_the_repair_budget_is_spent(self):
        # Regression: after the last permitted repair, the finding that caused it stays
        # open against the old head. The job must wait for that human, not block.
        self.publish()
        self.gh.review = "FAIL"
        for _ in range(3):
            self.assertEqual(self.tick(), "PUBLISHING")
            self.assertEqual(self.tick(), "REVIEW")
        head = self.gh.head
        # The bot is satisfied; only a finding against an earlier head is left.
        self.gh.review = "PASS"
        self.gh.threads = [{"isResolved": False, "isOutdated": True,
                            "comments": {"nodes": [{"body": "old finding", "commit": {"oid": "old"}}]}}]
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(self.tick(), "REVIEW")
        data = json.loads(self.store.get("owner/project", 1)["data"])
        self.assertEqual(self.store.get("owner/project", 1)["state"], "REVIEW")
        self.assertNotIn("Maximum repair attempts", data.get("reason", ""))
        self.assertEqual(data.get("head"), head)

    def test_a_malformed_answer_is_not_taken_for_an_unchanged_confirmation(self):
        # Regression: a null value where the snapshot has none compares equal, so a
        # malformed answer could mark the head settled and suppress every later repair.
        cfg = self.cfg["repositories"][0]
        snapshot = {"docs/glossary.md": "# Glossary\n"}
        self.assertFalse(self.engine.unchanged({"docs/glossary.md": None}, cfg, snapshot))
        self.assertFalse(self.engine.unchanged({"docs/notes.md": None}, cfg, snapshot))
        self.assertFalse(self.engine.unchanged({"docs/notes.md": "x"}, cfg, snapshot))
        self.assertFalse(self.engine.unchanged({}, cfg, snapshot))
        self.assertTrue(self.engine.unchanged({"docs/glossary.md": "# Glossary\n"}, cfg, snapshot))

    def test_a_new_finding_for_a_settled_head_is_sent_to_the_model(self):
        # Regression: the settled head was remembered without the findings it settled,
        # so a thread that arrived later for the same head was swallowed and the job
        # waited for ever without the model ever seeing it.
        content = "# Glossary\nPR: pull request. Revision 1.\n"
        calls = []

        def generate(issue, files, config, feedback):
            calls.append(feedback)
            return {"docs/glossary.md": content} if len(calls) == 1 else dict(files)

        self.agent.generate = generate
        self.gh.threads = [{"isResolved": False, "isOutdated": True,
                            "comments": {"nodes": [{"body": "old finding", "commit": {"oid": "old"}}]}}]
        self.assertEqual(self.tick(), "PUBLISHING")
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(self.tick(), "REVIEW")          # settles on the first stale finding
        self.assertEqual(len(calls), 2)
        # A second, different finding for the same head must reach the model.
        self.gh.threads = [{"isResolved": False, "isOutdated": True,
                            "comments": {"nodes": [{"body": "newer finding", "commit": {"oid": "old"}}]}}]
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(len(calls), 3)
        self.assertIn("newer finding", calls[2][0]["body"])

    def test_a_resumed_job_keeps_the_calls_it_has_already_spent(self):
        # Regression: a job that came back to QUEUED after a rejected output had its
        # counters reset, so the billed call was forgotten and the budget could be
        # exceeded by repeated manual retries.
        original = self.gh.snapshot
        state = {"snapshot": 0}

        def flaky_snapshot(*args):
            state["snapshot"] += 1
            if state["snapshot"] == 2:
                raise Blocked("temporary GitHub snapshot failure")
            return original(*args)

        def generate(issue, files, config, feedback):
            if not hasattr(generate, "used"):
                generate.used = True
                generate.feedback = feedback
                raise UnusableOutput("rejected output")
            generate.resumed = feedback
            return {"docs/glossary.md": "# Glossary\nPR: pull request. Revision 2.\n"}

        self.agent.generate = generate
        self.gh.snapshot = flaky_snapshot
        self.assertEqual(self.tick(), "BLOCKED")
        data = json.loads(self.store.get("owner/project", 1)["data"])
        self.assertEqual(data["calls"], 1)            # the rejected call was billed
        repairs = data["repairs"]
        self.gh.snapshot = original
        self.store.retry(1)
        self.assertEqual(self.tick(), "PUBLISHING")
        data = json.loads(self.store.get("owner/project", 1)["data"])
        self.assertEqual(data["calls"], 2)            # the spent call is still counted
        self.assertEqual(data["repairs"], repairs + 1)   # the resumed attempt is a repair
        # The rejection reason survives the resume, so the model is told why.
        self.assertIn("rejected output", generate.resumed[-1]["body"])

    def test_a_rejection_reason_reaches_the_model_after_a_review_retry(self):
        # Regression: the reason an answer was rejected was only kept for the immediate
        # retry. After a manual retry the model saw the review finding again but not the
        # reason the answer had been rejected, and produced the same unusable answer.
        self.publish()
        self.gh.review = "FAIL"
        original = self.gh.snapshot
        state = {"n": 0}
        seen = []

        def flaky_snapshot(*args):
            state["n"] += 1
            if state["n"] == 2:
                raise Blocked("temporary GitHub snapshot failure")
            return original(*args)

        def generate(issue, files, config, feedback):
            seen.append(feedback)
            if len(seen) == 1:
                raise UnusableOutput("rejected output")
            return {"docs/glossary.md": "# Glossary\nPR: pull request. Revision 2.\n"}

        self.agent.generate = generate
        self.gh.snapshot = flaky_snapshot
        self.assertEqual(self.tick(), "BLOCKED")
        data = json.loads(self.store.get("owner/project", 1)["data"])
        self.assertEqual(data["output_reason"], "rejected output")
        self.gh.snapshot = original
        self.store.retry(1)
        self.tick()
        self.assertTrue(any(item.get("body") == "rejected output"
                            for item in seen[-1] if isinstance(item, dict)))

    def test_a_rejection_reason_does_not_change_what_counts_as_outstanding(self):
        # Regression: the reason an answer was rejected was appended to the same list
        # the outstanding check and the settled digest were computed from, so a job
        # that only had stale findings could never settle while a reason was pending.
        content = "# Glossary\nPR: pull request. Revision 1.\n"
        seen = []

        def generate(issue, files, config, feedback):
            seen.append(feedback)
            if len(seen) == 2:
                raise UnusableOutput("rejected output")
            return {"docs/glossary.md": content}          # the same as the snapshot

        self.agent.generate = generate
        self.gh.threads = [{"isResolved": False, "isOutdated": True,
                            "comments": {"nodes": [{"body": "old finding", "commit": {"oid": "old"}}]}}]
        self.assertEqual(self.tick(), "PUBLISHING")
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(self.tick(), "REVIEW")
        data = json.loads(self.store.get("owner/project", 1)["data"])
        # The model was told why its answer was refused.
        self.assertTrue(any(item.get("kind") == "output" for item in seen[-1]))
        # The head was settled against the findings alone: the reason is not in it.
        settled = data.get("settled") or {}
        self.assertTrue(settled)
        findings = [{"kind": "thread", "body": "old finding", "sha": "old"}]
        self.assertEqual(settled["feedback"], digest(findings))
        self.assertNotEqual(settled["feedback"], digest(findings + [{"kind": "output",
                                                                      "body": "rejected output"}]))
        # And a reason on its own is not a finding about the head.
        self.assertFalse(self.engine.outstanding(
            [{"kind": "thread", "body": "old finding", "sha": "old"}], "head"))

    def test_an_unchanged_first_answer_is_rejected_rather_than_settled(self):
        # Regression: the no-op shortcut applied before a pull request existed, so a
        # first answer that changed nothing left the job in REVIEW with no pull request
        # behind it. An answer that changes nothing is a refusal until a PR exists.
        self.gh.files = {"docs/glossary.md": "# Glossary\n"}
        self.agent.generate = lambda issue, files, config, feedback: {"docs/glossary.md": "# Glossary\n"}
        self.assertEqual(self.tick(), "BLOCKED")
        data = json.loads(self.store.get("owner/project", 1)["data"])
        self.assertEqual(data.get("settled"), None)
        self.assertIn("Unchanged file", data.get("reason", ""))
        self.assertNotEqual(self.store.get("owner/project", 1)["state"], "REVIEW")

    def test_a_settled_head_is_not_blocked_by_the_call_budget(self):
        # Regression: the call budget was checked before the settlement. A job whose
        # budget was spent while its head had nothing outstanding blocked on the
        # budget instead of waiting for the human that has to resolve the stale
        # thread -- and a retry could not fix it, because the budget was spent.
        self.cfg["max_repairs"] = 1
        content = "# Glossary\nPR: pull request. Revision 1.\n"
        calls = []

        def generate(issue, files, config, feedback):
            calls.append(feedback)
            return {"docs/glossary.md": content} if len(calls) == 1 else dict(files)

        self.agent.generate = generate
        self.gh.threads = [{"isResolved": False, "isOutdated": True,
                            "comments": {"nodes": [{"body": "old finding", "commit": {"oid": "old"}}]}}]
        self.assertEqual(self.tick(), "PUBLISHING")
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(self.tick(), "REVIEW")       # settles on the first stale finding
        self.assertEqual(len(calls), 2)
        # The budget is spent now (two calls, one repair allowed), but the head has
        # nothing outstanding: the job keeps waiting for the human.
        self.assertEqual(self.tick(), "REVIEW")
        record = json.loads(self.store.get("owner/project", 1)["data"])
        self.assertEqual(record["calls"], 2)
        self.assertNotIn("budget", record.get("reason", ""))
        self.assertEqual(len(calls), 2)

    def test_a_resolved_thread_does_not_invalidate_the_settlement(self):
        # Regression: the settlement remembered a digest of the whole finding set, so a
        # human resolving one of two stale threads changed that digest. Every partial
        # resolution cost another model call, and the job ran out of budget with a
        # thread still open. A finding that is gone is not a new finding.
        content = "# Glossary\nPR: pull request. Revision 1.\n"
        calls = []

        def generate(issue, files, config, feedback):
            calls.append(feedback)
            return {"docs/glossary.md": content} if len(calls) == 1 else dict(files)

        self.agent.generate = generate
        self.gh.threads = [
            {"isResolved": False, "isOutdated": True,
             "comments": {"nodes": [{"body": "first finding", "commit": {"oid": "old"}}]}},
            {"isResolved": False, "isOutdated": True,
             "comments": {"nodes": [{"body": "second finding", "commit": {"oid": "old"}}]}}]
        self.assertEqual(self.tick(), "PUBLISHING")
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(len(calls), 2)
        # A human resolves one of the two stale threads. The other one was already
        # evaluated against this head, so no new call is needed.
        self.gh.threads[0]["isResolved"] = True
        self.assertEqual(self.tick(), "REVIEW")
        record = json.loads(self.store.get("owner/project", 1)["data"])
        self.assertEqual(record["calls"], 2)
        self.assertEqual(len(calls), 2)
        # A finding that is new is not settled, so it still reaches the model.
        self.gh.threads[1]["comments"]["nodes"][0]["body"] = "changed finding"
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(len(calls), 3)
        self.assertIn("changed finding", calls[2][0]["body"])

    def test_a_value_that_cannot_be_encoded_is_rejected_as_unusable_output(self):
        # Regression: a completed answer holding a lone surrogate raised
        # UnicodeEncodeError while the byte budget was computed. That is not a
        # Blocked, so the job was left in BUILDING: a manual retry was refused as an
        # interrupted call that never happened, and the answer was never rejected as
        # the unusable answer it was.
        calls = []

        def generate(issue, files, config, feedback):
            calls.append(feedback)
            if len(calls) == 1:
                return {"docs/glossary.md": "\ud800"}
            return {"docs/glossary.md": "# Glossary\nPR: pull request.\n"}

        self.agent.generate = generate
        self.assertEqual(self.tick(), "PUBLISHING")
        self.assertEqual(self.store.get("owner/project", 1)["state"], "PUBLISHING")
        self.assertEqual(len(calls), 2)
        # The model was told why its answer was refused.
        self.assertTrue(any(item.get("kind") == "output" for item in calls[1]))

    def test_unusable_output_is_repaired_instead_of_blocking_the_job(self):
        # Regression: real job 2 died "Agent must return a single strict JSON object with
        # files" after publishing PR #13, because every agent error was recorded as an
        # interrupted call. A call that already returned was billed either way.
        self.unusable_agent(1)
        self.assertEqual(self.tick(), "PUBLISHING")
        record = json.loads(self.store.get("owner/project", 1)["data"])
        self.assertEqual((record["calls"], record["repairs"]), (2, 1))
        # The rejection reason went back to the model on the retry.
        self.assertEqual(self.agent.inputs[1],
                         [{"kind": "output", "body": "Model returned output that is not a strict changes object"}])
        self.assertEqual(self.tick(), "REVIEW")

    def test_output_retry_keeps_the_review_findings_that_caused_the_repair(self):
        # Regression: real job 3 discarded the ten review threads on its first repair
        # retry, so the regenerated file still carried every finding it had been given.
        self.publish()
        self.gh.review = "FAIL"
        self.assertEqual(self.tick(), "PUBLISHING")          # repair on the review findings
        self.assertEqual(self.tick(), "REVIEW")
        record = json.loads(self.store.get("owner/project", 1)["data"])
        self.assertEqual(record["repairs"], 1)
        self.assertEqual(record["calls"], 2)
        self.unusable_agent(1)
        self.assertEqual(self.tick(), "PUBLISHING")          # output rejected, retried
        self.assertEqual(self.agent.calls, 4)
        findings = self.agent.inputs[3]
        self.assertEqual(findings, [
            {"kind": "review", "body": "Please fix the glossary"},
            {"kind": "output", "body": "Model returned output that is not a strict changes object"}])

    def test_unusable_output_is_terminal_only_once_repairs_run_out(self):
        self.unusable_agent(99)
        self.assertEqual(self.tick(), "BLOCKED")
        record = json.loads(self.store.get("owner/project", 1)["data"])
        self.assertEqual((record["calls"], record["repairs"]), (4, 3))
        self.assertIn("not a strict changes object", record["reason"])

    def test_an_interrupted_call_is_still_never_replayed(self):
        # The other half of the boundary: a call that did not return may already have been
        # billed, so it keeps resume=BUILDING and spends no repair attempt.
        for error in (OSError("connection reset"), Blocked("MiniMax execution deadline exceeded")):
            with self.subTest(error=type(error).__name__):
                self.setUp()
                self.agent.generate = Mock(side_effect=error)
                self.assertEqual(self.tick(), "BLOCKED")
                record = json.loads(self.store.get("owner/project", 1)["data"])
                self.assertEqual(record["resume"], "BUILDING")
                self.assertEqual((record["calls"], record["repairs"]), (1, 0))
                self.assertEqual(self.agent.generate.call_count, 1)

    def test_post_call_checks_all_report_a_recoverable_type(self):
        # validate_changes and the tree-mode checks run after the call returned, so every
        # one of them must exit as UnusableOutput; a bare Blocked would disable the repair.
        cfg = self.cfg["repositories"][0]
        cases = [
            ({}, {}, {}),                                    # empty change set
            ({"docs/glossary.md": "# Glossary\n"}, {"docs/glossary.md": "# Glossary\n"}, {}),
            ({"secrets.txt": "hello\n"}, {}, {}),            # outside the allowlist
            ({"docs/glossary.md": "x\n"}, {}, {"docs": "100644"}),   # file used as a directory
            ({"docs/glossary.md": "x\n"}, {}, {"docs": "120000"}),   # symlink/executable boundary
        ]
        for changes, snapshot, modes in cases:
            with self.subTest(modes=modes), self.assertRaises(UnusableOutput):
                self.engine.usable(changes, cfg, snapshot, modes, {})

    def test_missing_or_old_review_never_merges(self):
        self.cfg["auto_merge"] = True
        self.cfg["repositories"][0]["auto_merge"] = True
        self.publish()
        self.gh.review = "MISSING"
        self.assertEqual(self.tick(), "REVIEW")
        self.gh.review, self.gh.bot_sha = "PASS", "old"
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(self.gh.merge_calls, 0)

    def test_lost_ref_response_retry_is_idempotent(self):
        self.assertEqual(self.tick(), "PUBLISHING")
        self.gh.fail_publish_once = True
        self.assertEqual(self.tick(), "BLOCKED")
        self.store.retry(1)
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(self.agent.calls, 1)
        self.assertEqual(self.gh.pr_creates, 1)

    def test_lost_pr_response_retry_does_not_duplicate_pr(self):
        self.assertEqual(self.tick(), "PUBLISHING")
        self.gh.fail_after_pr_once = True
        self.assertEqual(self.tick(), "BLOCKED")
        self.store.retry(1)
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(self.gh.pr_creates, 1)

    def test_close_failure_after_merge_can_be_reconciled(self):
        self.publish()
        self.gh.merged = True
        self.gh.fail_close_once = True
        self.assertEqual(self.tick(), "BLOCKED")
        self.store.retry(1)
        self.assertEqual(self.tick(), "DONE")
        self.assertEqual(self.agent.calls, 1)

    def test_single_active_job(self):
        self.gh.items[2] = issue(2)
        self.publish()
        self.gh.review = "MISSING"
        for _ in range(3):
            self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(len(self.store.rows()), 1)
        with self.store.lock(), self.assertRaises(Blocked):
            self.engine.tick()

    def test_untrusted_author_or_missing_label_not_claimed(self):
        self.gh.items[1]["user"]["login"] = "attacker"
        self.assertEqual(self.tick(), "IDLE")
        self.gh.items[1]["user"]["login"] = "owner"
        self.gh.items[1]["labels"] = []
        self.assertEqual(self.tick(), "IDLE")

    def test_dependency_and_missing_acceptance_block_before_model(self):
        self.gh.items[1]["body"] = "No criteria"
        self.assertEqual(self.tick(), "BLOCKED")
        self.assertEqual(self.agent.calls, 0)

    def test_open_dependency_blocks(self):
        self.gh.items[1]["body"] += "\nDepends-On: #2"
        self.gh.items[2] = issue(2)
        self.assertEqual(self.tick(), "BLOCKED")
        self.assertEqual(self.agent.calls, 0)

    def test_external_head_change_blocks(self):
        self.publish()
        self.gh.head = "attacker"
        self.assertEqual(self.tick(), "BLOCKED")

    def test_changed_spec_blocks_publication(self):
        self.assertEqual(self.tick(), "PUBLISHING")
        self.gh.items[1]["body"] += "\nChanged"
        self.assertEqual(self.tick(), "BLOCKED")
        self.assertEqual(self.gh.published, 0)

    def test_changed_policy_blocks(self):
        self.publish()
        self.cfg["repositories"][0]["allowed_paths"].append("**")
        self.assertEqual(self.tick(), "BLOCKED")

    def test_timeout_blocks_without_repair(self):
        self.publish()
        self.gh.review = "MISSING"
        with patch("ado.engine.time.time", return_value=time.time() + 10000):
            self.assertEqual(self.tick(), "BLOCKED")
        self.assertEqual(self.agent.calls, 1)

    def test_model_failure_not_automatically_replayed(self):
        self.agent.generate = lambda *args: (_ for _ in ()).throw(RuntimeError("failed"))
        self.assertEqual(self.tick(), "BLOCKED")
        with self.assertRaises(Blocked):
            self.store.retry(1)
        self.assertEqual(self.tick(), "IDLE")

    def test_protected_model_output_never_published(self):
        self.agent.path = ".github/workflows/steal.yml"
        self.assertEqual(self.tick(), "BLOCKED")
        self.assertEqual(self.gh.published, 0)

    def test_source_code_never_auto_merges_even_with_broad_glob(self):
        self.cfg["auto_merge"] = True
        self.cfg["repositories"][0].update(auto_merge=True, auto_merge_paths=["**"])
        self.agent.path = "src/feature.py"
        self.publish()
        self.assertEqual(self.tick(), "AWAITING_HUMAN")
        self.assertEqual(self.gh.merge_calls, 0)

    def test_remote_diff_tamper_blocks(self):
        self.publish()
        self.gh.extra_files = [{"filename": ".github/workflows/x.yml", "sha": "bad", "status": "added"}]
        self.assertEqual(self.tick(), "BLOCKED")

    def test_unprotected_branch_blocks_auto_merge(self):
        self.cfg["auto_merge"] = True
        self.cfg["repositories"][0]["auto_merge"] = True
        self.gh.protected = False
        self.publish()
        self.assertEqual(self.tick(), "BLOCKED")
        self.assertEqual(self.gh.merge_calls, 0)

    def test_base_movement_allows_human_handoff_without_auto_merge(self):
        self.publish()
        self.gh.base = 'c' * 40
        self.assertEqual(self.tick(), 'AWAITING_HUMAN')
        self.assertEqual(self.gh.merge_calls, 0)

    def test_base_movement_behind_stays_in_review_even_after_approval(self):
        self.publish()
        self.gh.base = 'c' * 40
        self.gh.mergeable_state = "behind"
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(self.gh.merge_calls, 0)

    def test_handoff_behind_regression_blocks_with_manual_recovery(self):
        self.publish()
        self.assertEqual(self.tick(), "AWAITING_HUMAN")
        self.gh.base = 'c' * 40
        self.gh.mergeable_state = "behind"
        self.assertEqual(self.tick(), "BLOCKED")
        row = self.store.get("owner/project", 1)
        data = json.loads(row["data"])
        self.assertIn("Manual handoff invalidated", data["reason"])
        self.assertEqual(self.agent.calls, 1)
        self.assertEqual(self.gh.close_calls, 0)
        self.assertEqual(self.gh.merge_calls, 0)

    def test_handoff_conflict_regression_blocks_without_head_update(self):
        self.publish()
        self.assertEqual(self.tick(), "AWAITING_HUMAN")
        self.gh.base = 'c' * 40
        self.gh.mergeable = False
        self.gh.mergeable_state = "dirty"
        self.assertEqual(self.tick(), "BLOCKED")
        self.assertEqual(self.gh.close_calls, 0)
        self.assertEqual(self.gh.merge_calls, 0)

    def test_handoff_transient_unknown_does_not_block(self):
        self.publish()
        self.assertEqual(self.tick(), "AWAITING_HUMAN")
        self.gh.base = 'c' * 40
        self.gh.mergeable = None
        self.gh.mergeable_state = "unknown"
        self.assertEqual(self.tick(), "AWAITING_HUMAN")
        self.assertEqual(self.gh.close_calls, 0)

    def test_behind_pr_never_hands_off_after_ci_and_approval(self):
        self.publish()
        self.gh.base = 'c' * 40
        self.gh.mergeable_state = "behind"
        self.gh.review = "MISSING"
        self.assertEqual(self.tick(), "REVIEW")
        self.gh.review = "PASS"
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(self.gh.merge_calls, 0)

    def test_base_movement_not_clean_still_waits_for_ci_and_review(self):
        self.publish()
        self.gh.base = 'c' * 40
        self.gh.mergeable_state = "dirty"
        self.gh.review = "MISSING"
        self.assertEqual(self.tick(), "REVIEW")
        self.gh.review = "PASS"
        self.gh.ci = "neutral"
        self.assertEqual(self.tick(), "PUBLISHING")
        self.assertEqual(self.gh.merge_calls, 0)

    def test_base_movement_conflicted_pr_must_not_handoff(self):
        self.publish()
        self.gh.base = 'c' * 40
        self.gh.mergeable = False
        self.gh.mergeable_state = "dirty"
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(self.gh.merge_calls, 0)

    def test_base_movement_dirty_state_with_true_mergeable_must_not_handoff(self):
        self.publish()
        self.gh.base = 'c' * 40
        self.gh.mergeable = True
        self.gh.mergeable_state = "dirty"
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(self.gh.merge_calls, 0)

    def test_base_movement_unknown_mergeability_must_not_handoff(self):
        self.publish()
        self.gh.base = 'c' * 40
        self.gh.mergeable = None
        self.gh.mergeable_state = "unknown"
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(self.gh.merge_calls, 0)

    def test_base_movement_behind_without_approved_review_still_waits(self):
        self.publish()
        self.gh.base = 'c' * 40
        self.gh.mergeable_state = "behind"
        self.gh.review = "MISSING"
        self.assertEqual(self.tick(), "REVIEW")
        self.assertEqual(self.gh.merge_calls, 0)

    def test_nonclean_without_base_movement_does_not_handoff(self):
        self.publish()
        self.gh.mergeable_state = "dirty"
        self.assertEqual(self.tick(), "REVIEW")

    def test_base_movement_blocks_auto_merge(self):
        self.cfg['auto_merge'] = True
        self.cfg['repositories'][0]['auto_merge'] = True
        self.publish()
        self.gh.base = 'c' * 40
        self.assertEqual(self.tick(), 'BLOCKED')
        self.assertEqual(self.gh.merge_calls, 0)
