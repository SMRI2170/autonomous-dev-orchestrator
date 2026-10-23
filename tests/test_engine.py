import json
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from ado.engine import Engine
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
        with self.assertRaises(Blocked):
            self.store.retry(1)

    def test_the_retry_budget_is_the_configured_one(self):
        # Regression: the store compared against a hardcoded 3 while the engine used the
        # configured max_repairs. A job configured with 4 could never be resumed, so the
        # extra round the engine allowed was unreachable.
        wide = Store(self.temp.name + "-wide", max_repairs=4)
        row = wide.add("owner/project", 1)
        wide.save(row, "BLOCKED", {"repairs": 3, "resume": "REVIEW"})
        wide.retry(row["id"])
        self.assertEqual(wide.get("owner/project", 1)["state"], "REVIEW")
        # The default budget still refuses the same job.
        narrow = Store(self.temp.name + "-narrow")
        row = narrow.add("owner/project", 1)
        narrow.save(row, "BLOCKED", {"repairs": 3, "resume": "REVIEW"})
        with self.assertRaises(Blocked):
            narrow.retry(row["id"])
        wide.db.close()
        narrow.db.close()

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
