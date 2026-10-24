import hashlib
import json
import tempfile
import time
import unittest

from ado.engine import Engine
from ado.minimax_review import MiniMaxReviewer
from ado.policy import Blocked, UnusableOutput
from ado.state import Store


class StubModel:
    def __init__(self, result):
        self.result = result

    def invoke(self, _prompt):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class SequenceReviewer:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def review(self, _issue, _originals, _changes, _sha):
        self.calls += 1
        if not self.outcomes:
            raise AssertionError("unexpected extra reviewer call")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeGitHub:
    def __init__(self, sha, changes):
        self.sha = sha
        self.changes = changes

    def metadata(self, _repo):
        return {"default_branch": "main"}

    def issues(self, _repo):
        return []

    def issue(self, _repo, _number):
        return {
            "number": 24,
            "title": "Recover unusable review output",
            "body": "## Acceptance criteria\nRecover completed unusable review output.",
            "state": "open",
            "user": {"login": "trusted"},
            "labels": [{"name": "ado:ready"}],
        }

    def pr(self, _repo, _number):
        return {
            "number": 1,
            "state": "open",
            "merged": False,
            "draft": False,
            "mergeable": True,
            "mergeable_state": "clean",
            "head": {
                "sha": self.sha,
                "ref": "ado/issue-24",
                "repo": {"full_name": "owner/repo"},
            },
            "base": {
                "sha": "b" * 40,
                "ref": "main",
                "repo": {"full_name": "owner/repo"},
            },
        }

    def evidence(self, _repo, _pr):
        return {
            "checks": [{
                "id": 1,
                "name": "unit",
                "head_sha": self.sha,
                "status": "completed",
                "conclusion": "success",
                "app": {"slug": "github-actions"},
            }],
            "reviews": [],
            "threads": [],
        }

    def changed_files(self, _repo, _number):
        files = []
        for path, content in self.changes.items():
            raw = content.encode("utf-8")
            blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            files.append({"filename": path, "status": "modified", "sha": blob})
        return files

    def snapshot(self, _repo, _sha, _config):
        originals = {path: "original\n" for path in self.changes}
        modes = {path: "100644" for path in self.changes}
        return originals, modes


class Issue24ReviewerTests(unittest.TestCase):
    SHA = "a" * 40
    CHANGES = {"docs/review.md": "updated review content\n"}

    def make_engine(self, root, reviewer):
        store = Store(root)
        row = store.add("owner/repo", 24)
        data = {
            "base": "b" * 40,
            "base_branch": "main",
            "branch": "ado/issue-24",
            "pr": 1,
            "head": self.SHA,
            "changes": dict(self.CHANGES),
            "all_changes": dict(self.CHANGES),
            "calls": 1,
            "repairs": 0,
            "review_started": time.time(),
        }
        store.save(row, "REVIEW", data)
        repo = {
            "name": "owner/repo",
            "allowed_paths": ["docs/**/*.md"],
            "context_paths": ["README.md"],
            "trusted_authors": ["trusted"],
            "issue_label": "ado:ready",
            "required_checks": [{"name": "unit", "app": "github-actions"}],
            "coderabbit_login": "coderabbitai[bot]",
            "review_provider": "minimax",
            "auto_merge": False,
        }
        config = {
            "dry_run": False,
            "auto_merge": False,
            "max_repairs": 3,
            "review_timeout_seconds": 86400,
            "repositories": [repo],
        }
        engine = Engine(config, store, FakeGitHub(self.SHA, self.CHANGES), object(), reviewer)
        return store, engine

    def test_completed_unusable_review_is_retried_once_without_repair_budget(self):
        reviewer = SequenceReviewer(
            UnusableOutput("invalid review JSON"),
            {"verdict": "APPROVE", "findings": []},
        )
        with tempfile.TemporaryDirectory() as root:
            store, engine = self.make_engine(root, reviewer)
            result = engine.tick()
            current = store.get("owner/repo", 24)
            data = json.loads(current["data"])
            self.assertEqual("REVIEW", result["state"])
            self.assertEqual("REVIEW", current["state"])
            self.assertEqual(2, reviewer.calls)
            self.assertEqual(0, data["repairs"])
            self.assertEqual(1, data["calls"])
            self.assertEqual("APPROVE", data["minimax_review"]["verdict"])

    def test_two_unusable_reviews_fail_in_a_human_retryable_state(self):
        reviewer = SequenceReviewer(
            UnusableOutput("first invalid review"),
            UnusableOutput("second invalid review"),
        )
        with tempfile.TemporaryDirectory() as root:
            store, engine = self.make_engine(root, reviewer)
            result = engine.tick()
            current = store.get("owner/repo", 24)
            data = json.loads(current["data"])
            self.assertEqual("BLOCKED", result["state"])
            self.assertEqual("BLOCKED", current["state"])
            self.assertNotEqual("REVIEWING", current["state"])
            self.assertEqual("REVIEW", data["resume"])
            self.assertIn("review output was unusable", data["reason"].lower())
            self.assertIn("レビュー出力が使用不能であった", data["reason"])
            self.assertEqual(0, data["repairs"])
            self.assertEqual(1, data["calls"])
            self.assertEqual(2, reviewer.calls)

            # A blocked job must not start another review automatically.
            idle = engine.tick()
            self.assertEqual("IDLE", idle["state"])
            self.assertEqual(2, reviewer.calls)

            store.retry(current["id"])
            self.assertEqual("REVIEW", store.get("owner/repo", 24)["state"])

    def test_interrupted_review_is_not_retried_and_remains_reviewing(self):
        reviewer = SequenceReviewer(Blocked("review invocation interrupted"))
        with tempfile.TemporaryDirectory() as root:
            store, engine = self.make_engine(root, reviewer)
            result = engine.tick()
            current = store.get("owner/repo", 24)
            data = json.loads(current["data"])
            self.assertEqual("BLOCKED", result["state"])
            self.assertEqual("REVIEWING", data["resume"])
            self.assertEqual(1, reviewer.calls)
            self.assertEqual(0, data["repairs"])
            with self.assertRaises(Blocked):
                store.retry(current["id"])

    def test_reviewer_marks_rejected_completed_output_as_unusable(self):
        reviewer = MiniMaxReviewer(StubModel("not JSON"))
        with self.assertRaises(UnusableOutput):
            reviewer.review(
                {"title": "Review", "body": "## Acceptance criteria\nReview."},
                {},
                {},
                "a" * 40,
            )

    def test_reviewer_marks_deeply_nested_completed_output_as_unusable(self):
        raw = "[" * 2000 + "0" + "]" * 2000
        reviewer = MiniMaxReviewer(StubModel(raw))
        with self.assertRaises(UnusableOutput):
            reviewer.review(
                {"title": "Review", "body": "## Acceptance criteria\nReview."},
                {},
                {},
                "a" * 40,
            )

    def test_reviewer_keeps_invocation_failure_as_plain_blocked(self):
        reviewer = MiniMaxReviewer(StubModel(Blocked("invocation interrupted")))
        with self.assertRaises(Blocked) as raised:
            reviewer.review(
                {"title": "Review", "body": "## Acceptance criteria\nReview."},
                {},
                {},
                "a" * 40,
            )
        self.assertNotIsInstance(raised.exception, UnusableOutput)

        reviewer = MiniMaxReviewer(StubModel(RuntimeError("adapter failed")))
        with self.assertRaises(Blocked) as raised:
            reviewer.review(
                {"title": "Review", "body": "## Acceptance criteria\nReview."},
                {},
                {},
                "a" * 40,
            )
        self.assertNotIsInstance(raised.exception, UnusableOutput)


if __name__ == "__main__":
    unittest.main()
