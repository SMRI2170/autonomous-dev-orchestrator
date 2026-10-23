import unittest
from ado.review import judge
from tests.fakes import GitHub, repo_config


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.gh = GitHub()
        self.gh.head = "head"
        self.pr = self.gh.pr("owner/project", 10)
        self.ev = self.gh.evidence("owner/project", self.pr)
        self.cfg = repo_config()


    def test_minimax_approval_requires_current_head_and_diff(self):
        self.cfg["review_provider"] = "minimax"
        review = {"sha": "head", "changes_digest": "bound", "verdict": "APPROVE", "findings": []}
        self.assertEqual(judge(self.pr, self.ev, self.cfg, review, "bound")[0], "PASS")
        self.assertEqual(judge(self.pr, self.ev, self.cfg, review, "different")[0], "WAIT")
        self.assertEqual(judge(self.pr, self.ev, self.cfg, {**review, "sha": "stale"}, "bound")[0], "WAIT")
        self.assertEqual(judge(self.pr, self.ev, self.cfg)[0], "WAIT")

    def test_minimax_review_optional_coderabbit_does_not_veto(self):
        self.cfg["review_provider"] = "minimax"
        self.ev["reviews"][0]["state"] = "CHANGES_REQUESTED"
        self.ev["reviews"][0]["commit_id"] = "stale"
        review = {"sha": "head", "changes_digest": "bound", "verdict": "APPROVE", "findings": []}
        self.assertEqual(judge(self.pr, self.ev, self.cfg, review, "bound")[0], "PASS")
        self.ev["reviews"].append({"id": 2, "user": {"login": "human", "type": "User"},
                                   "commit_id": "stale", "state": "CHANGES_REQUESTED", "body": "unsafe"})
        self.assertEqual(judge(self.pr, self.ev, self.cfg, review, "bound")[0], "WAIT")

    def test_minimax_rejection_requires_actionable_findings(self):
        self.cfg["review_provider"] = "minimax"
        bad = {"sha": "head", "changes_digest": "bound", "verdict": "REQUEST_CHANGES", "findings": []}
        self.assertEqual(judge(self.pr, self.ev, self.cfg, bad, "bound")[0], "WAIT")
        good = {**bad, "findings": [{"path": "docs/a.md", "message": "incorrect", "severity": "major"}]}
        self.assertEqual(judge(self.pr, self.ev, self.cfg, good, "bound")[0], "REPAIR")

    def test_valid_evidence_passes(self):
        self.assertEqual(judge(self.pr, self.ev, self.cfg)[0], "PASS")

    def test_stale_ci_and_spoofed_app_do_not_pass(self):
        for field, value in [("head_sha", "old"), ("app", {"slug": "fake"}), ("status", "in_progress")]:
            with self.subTest(field=field):
                ev = self.gh.evidence("owner/project", self.pr)
                ev["checks"][0][field] = value
                self.assertEqual(judge(self.pr, ev, self.cfg)[0], "WAIT")

    def test_latest_rerun_failure_beats_old_success(self):
        self.ev["checks"].append({**self.ev["checks"][0], "id": 2, "conclusion": "failure"})
        self.assertEqual(judge(self.pr, self.ev, self.cfg)[0], "REPAIR")

    def test_bot_comment_or_human_spoof_not_approval(self):
        for state, user_type in [("COMMENTED", "Bot"), ("APPROVED", "User"), ("DISMISSED", "Bot")]:
            self.ev["reviews"][0].update(state=state)
            self.ev["reviews"][0]["user"]["type"] = user_type
            self.assertEqual(judge(self.pr, self.ev, self.cfg)[0], "WAIT")

    def test_unresolved_thread_repairs(self):
        self.ev["threads"] = [{"isResolved": False, "isOutdated": False,
                                "comments": {"nodes": [{"body": "fix this", "commit": {"oid": "head"}}]}}]
        self.assertEqual(judge(self.pr, self.ev, self.cfg)[0], "REPAIR")
        self.ev["threads"][0]["isOutdated"] = True
        self.assertEqual(judge(self.pr, self.ev, self.cfg)[0], "REPAIR")
        self.ev["threads"][0]["isResolved"] = True
        self.assertEqual(judge(self.pr, self.ev, self.cfg)[0], "PASS")

    def test_finding_raised_against_an_earlier_head_still_reaches_the_model(self):
        # Regression: real job 3 stalled because findings posted against the previous
        # head were recorded as "unresolved old review thread" and never appeared in the
        # repair feedback. The model was never told about them, so it could not fix them.
        self.ev["threads"] = [{"isResolved": False, "isOutdated": True,
                                "comments": {"nodes": [{"body": "still broken", "commit": {"oid": "old"}}]}}]
        decision, feedback = judge(self.pr, self.ev, self.cfg)
        self.assertEqual(decision, "REPAIR")
        self.assertEqual(feedback, [{"kind": "thread", "body": "still broken", "sha": "old"}])

    def test_a_stale_finding_still_blocks_a_pass(self):
        self.ev["threads"] = [{"isResolved": False, "isOutdated": True,
                                "comments": {"nodes": [{"body": "still broken", "commit": {"oid": "old"}}]}}]
        decision, feedback = judge(self.pr, self.ev, self.cfg)
        self.assertEqual(decision, "REPAIR")
        # Nothing about a stale thread may let a job through on its own.
        self.ev["threads"][0]["comments"]["nodes"][0]["body"] = "still broken"
        self.assertNotEqual(judge(self.pr, self.ev, self.cfg)[0], "PASS")

    def test_a_thread_whose_commit_is_gone_does_not_crash_the_judge(self):
        # A force push can drop the commit a thread refers to, and GitHub then returns
        # a null commit. The judge must treat that as an old thread, not raise.
        self.ev["threads"] = [{"isResolved": False, "isOutdated": True,
                                "comments": {"nodes": [{"body": "fix this", "commit": None}]}}]
        decision, feedback = judge(self.pr, self.ev, self.cfg)
        self.assertEqual(decision, "REPAIR")
        self.assertEqual(feedback, [{"kind": "thread", "body": "fix this", "sha": None}])

    def test_human_changes_requested_remains_veto(self):
        self.ev["reviews"].append({"id": 2, "user": {"login": "human", "type": "User"},
                                    "commit_id": "old", "state": "CHANGES_REQUESTED", "body": "fix"})
        self.assertEqual(judge(self.pr, self.ev, self.cfg)[0], "WAIT")

    def test_missing_ci_does_not_burn_repairs(self):
        self.ev["checks"] = []
        self.ev["reviews"][0]["state"] = "CHANGES_REQUESTED"
        self.assertEqual(judge(self.pr, self.ev, self.cfg)[0], "WAIT")

    def test_unknown_mergeability_never_passes(self):
        for value in [None, False]:
            self.pr["mergeable"] = value
            self.assertEqual(judge(self.pr, self.ev, self.cfg)[0], "WAIT")

    def test_comment_does_not_clear_previous_human_change_request(self):
        self.ev["reviews"] += [
            {"id": 2, "user": {"login": "human", "type": "User"}, "commit_id": "head",
             "state": "CHANGES_REQUESTED", "body": "Must fix"},
            {"id": 3, "user": {"login": "human", "type": "User"}, "commit_id": "head",
             "state": "COMMENTED", "body": "Another comment"}]
        self.assertEqual(judge(self.pr, self.ev, self.cfg)[0], "REPAIR")
