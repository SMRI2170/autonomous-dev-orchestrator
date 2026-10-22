import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from ado.github import GitHub
from ado.policy import Blocked
from tests.fakes import GitHub as Fake, repo_config


class GitHubTests(unittest.TestCase):
    def test_dry_run_cannot_write(self):
        with self.assertRaises(Blocked):
            GitHub().api("repos/owner/project/issues", "POST", {"title": "never"})

    def test_live_requires_explicit_token(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(Blocked):
            GitHub(False)

    def test_transport_does_not_leak_failure_body(self):
        # A real failing `gh`, not a mock: the message is what api() actually builds, so
        # this fails if the failure text ever grows a query string or the CLI's stderr.
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "gh"
            fake.write_text("#!/bin/sh\necho SECRET_PRIVATE_RESPONSE >&2\nexit 1\n")
            fake.chmod(0o755)
            with patch.dict(os.environ, {"PATH": tmp + os.pathsep + os.environ["PATH"]}):
                with self.assertRaises(Blocked) as caught:
                    GitHub().api("repos/owner/project?per_page=100")
        self.assertEqual(str(caught.exception), "GitHub request failed: GET repos/owner/project")
        self.assertNotIn("SECRET_PRIVATE_RESPONSE", str(caught.exception))
        self.assertNotIn("per_page", str(caught.exception))

    def test_slow_request_keeps_the_worker_looking_alive(self):
        # Regression for #9: a request outliving the dashboard's 15 second online window
        # used to read as an offline worker, because the transport never pulsed.
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "gh"
            fake.write_text("#!/bin/sh\nsleep 0.6\nprintf '{}'\n")
            fake.chmod(0o755)
            beats = []
            with patch.dict(os.environ, {"PATH": tmp + os.pathsep + os.environ["PATH"]}):
                gh = GitHub(pulse=lambda: beats.append(time.monotonic()))
                self.assertEqual(gh.api("repos/owner/project"), {})
            # Before the fix this list stayed empty: the transport never pulsed, so a
            # request outliving the dashboard's online window showed as an offline
            # worker. No timing assertion -- run() waits up to a second after the
            # process exits for descendants holding the pipes open.
            self.assertGreaterEqual(len(beats), 2)

    def test_oversized_response_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "gh"
            fake.write_text("#!/bin/sh\nyes 0123456789012345678901234567890123456789 | head -c 9000000\n")
            fake.chmod(0o755)
            with patch.dict(os.environ, {"PATH": tmp + os.pathsep + os.environ["PATH"]}):
                with self.assertRaises(Blocked) as caught:
                    GitHub().api("repos/owner/project")
            self.assertIn("output budget", str(caught.exception))

    def test_pagination_collects_all_evidence(self):
        gh = GitHub()
        with patch.object(gh, "api", side_effect=[[{}] * 100, [{"last": True}]]) as api:
            self.assertEqual(len(gh.pages("repos/owner/project/pulls")), 101)
            self.assertIn("page=2", api.call_args.args[0])
        with patch.object(gh, "api", return_value=[{}] * 100), self.assertRaises(Blocked):
            gh.pages("repos/owner/project/pulls")

    def test_private_repositories_rejected(self):
        gh = GitHub()
        with patch.object(gh, "api", return_value={"private": True}), self.assertRaises(Blocked):
            gh.metadata("owner/private")

    def test_ref_recovery_and_conflict(self):
        gh = GitHub()
        ref = [{"ref": "refs/heads/ado/issue-1", "object": {"sha": "expected"}}]
        with patch.object(gh, "api", return_value=ref) as api:
            gh.publish_ref("owner/project", "ado/issue-1", "expected")
            self.assertEqual(api.call_count, 1)
        with patch.object(gh, "api", return_value=ref), self.assertRaises(Blocked):
            gh.publish_ref("owner/project", "ado/issue-1", "new", "other")

    def test_pr_recovery_uses_same_repo_head(self):
        gh = GitHub()
        pr = {"number": 42, "state": "open", "head": {"ref": "ado/issue-1", "repo": {"full_name": "owner/project"}},
              "base": {"ref": "main"}}
        with patch.object(gh, "pages", return_value=[pr]), patch.object(gh, "api") as api:
            self.assertEqual(gh.ensure_pr("owner/project", "ado/issue-1", "main", 1), 42)
            api.assert_not_called()

    def test_merge_request_is_sha_conditional(self):
        gh = GitHub()
        with patch.object(gh, "api", return_value={"merged": True}) as api:
            gh.merge("owner/project", 1, "validated-head")
            self.assertEqual(api.call_args.args[2]["sha"], "validated-head")

    def test_branch_protection_is_app_bound_and_admin_enforced(self):
        gh, fake = GitHub(), Fake()
        fake.head = "head"
        ev = fake.evidence("owner/project", fake.pr("owner/project", 10))
        protection = {"required_status_checks": {"strict": True, "checks": [{"context": "unit", "app_id": 15368}]},
                      "enforce_admins": {"enabled": True}, "required_pull_request_reviews": {
                          "dismiss_stale_reviews": True, "required_approving_review_count": 1}}
        with patch.object(gh, "api", return_value=protection):
            gh.assert_protection("owner/project", "main", repo_config(), ev)
            protection["required_status_checks"]["checks"][0]["app_id"] = None
            with self.assertRaises(Blocked):
                gh.assert_protection("owner/project", "main", repo_config(), ev)

    def test_total_count_mismatch_fails_closed(self):
        gh = GitHub()
        with patch.object(gh, "api", return_value={"total_count": 101, "check_runs": [{}]}), self.assertRaises(Blocked):
            gh.pages("repos/owner/project/commits/head/check-runs", "check_runs")
