import json
import tempfile
import time
import unittest
from unittest.mock import patch
from ado.engine import Engine
from ado.policy import Blocked
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

    def test_base_movement_nonclean_hands_off_after_review(self):
        self.publish()
        self.gh.base = 'c' * 40
        self.gh.mergeable_state = "behind"
        self.assertEqual(self.tick(), "AWAITING_HUMAN")
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
