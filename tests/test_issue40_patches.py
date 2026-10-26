import json
import tempfile
import unittest

from ado.engine import Engine
from ado.policy import Blocked, UnusableOutput, parse_changes, validate_changes
from ado.state import Store
from tests.fakes import GitHub, config

ORIGINAL = "# Glossary\n\nPR: pull request.\n\nA line that must not change.\n"


class PatchAgent:
    """An agent that answers with a patch instead of a whole file."""

    def __init__(self, patches):
        self.patches = patches
        self.calls = 0
        self.inputs = []

    def generate(self, issue, files, repo_config, feedback):
        self.calls += 1
        self.inputs.append(feedback)
        return parse_changes(json.dumps({"patches": self.patches}), files)


def resolve(patches, snapshot=None):
    return parse_changes(json.dumps({"patches": patches}), snapshot or {"docs/a.md": ORIGINAL})


def config_for(max_bytes=50000, max_files=1, allowed=("docs/**/*.md",)):
    return {"allowed_paths": list(allowed), "max_files": max_files,
            "max_change_bytes": max_bytes}


class PatchContractTests(unittest.TestCase):
    def test_patch_changes_only_the_named_region(self):
        changes = resolve({"docs/a.md": [
            {"op": "replace", "search": "PR: pull request.", "replace": "PR: プルリクエスト。"}]})

        self.assertEqual(changes["docs/a.md"],
                         ORIGINAL.replace("PR: pull request.", "PR: プルリクエスト。"))
        self.assertIn("A line that must not change.", changes["docs/a.md"])
        self.assertEqual(changes.costs["docs/a.md"], len("PR: プルリクエスト。".encode()))

    def test_anchor_must_match_exactly_once(self):
        with self.assertRaises(UnusableOutput) as raised:
            resolve({"docs/a.md": [{"op": "replace", "search": "e", "replace": "x"}]})
        self.assertIn("not unique", str(raised.exception))

    def test_missing_anchor_is_rejected(self):
        with self.assertRaises(UnusableOutput) as raised:
            resolve({"docs/a.md": [{"op": "replace", "search": "nowhere", "replace": "x"}]})
        self.assertIn("not unique", str(raised.exception))

    def test_empty_anchor_is_rejected(self):
        with self.assertRaises(UnusableOutput):
            resolve({"docs/a.md": [{"op": "replace", "search": "", "replace": "x"}]})

    def test_unknown_operation_is_rejected(self):
        with self.assertRaises(UnusableOutput) as raised:
            resolve({"docs/a.md": [{"op": "rewrite_everything"}]})
        self.assertIn("Unknown patch operation", str(raised.exception))

    def test_patch_target_must_exist_in_the_snapshot(self):
        with self.assertRaises(UnusableOutput) as raised:
            resolve({"docs/other.md": [{"op": "append", "text": "x"}]})
        self.assertIn("not among the current files", str(raised.exception))

    def test_operations_run_in_order(self):
        changes = resolve({"docs/a.md": [
            {"op": "replace", "search": "PR: pull request.", "replace": "PR: PR."},
            {"op": "replace", "search": "PR: PR.", "replace": "PR: done."}]})
        self.assertIn("PR: done.", changes["docs/a.md"])

    def test_insert_prepend_and_append(self):
        changes = resolve({"docs/a.md": [
            {"op": "prepend", "text": "top\n"},
            {"op": "insert_after", "search": "PR: pull request.", "text": "\ninserted."},
            {"op": "append", "text": "bottom\n"}]})
        self.assertTrue(changes["docs/a.md"].startswith("top\n# Glossary\n"))
        self.assertIn("PR: pull request.\ninserted.", changes["docs/a.md"])
        self.assertTrue(changes["docs/a.md"].endswith("bottom\n"))

    def test_patches_and_files_are_mutually_exclusive(self):
        for text in ['{"patches":{"a":[]}}', '{"patches":{"a":{}}}']:
            with self.subTest(text=text), self.assertRaises(Blocked):
                # A snapshot is given so that each answer reaches the check of the
                # operation format, instead of stopping at the missing snapshot.
                parse_changes(text, snapshot={"a": ""})

    def test_a_path_cannot_be_both_written_and_patched(self):
        with self.assertRaises(UnusableOutput) as raised:
            parse_changes('{"files":{"docs/a.md":"new\\n"},'
                           '"patches":{"docs/a.md":[{"op":"replace",'
                           '"search":"PR: pull request.","replace":"x"}]}}',
                           {"docs/a.md": ORIGINAL})
        self.assertIn("cannot be both written and patched", str(raised.exception))

    def test_full_content_form_still_works(self):
        changes = parse_changes('{"files":{"docs/a.md":"new\\n"}}')
        self.assertEqual(changes, {"docs/a.md": "new\n"})
        self.assertEqual(changes.costs, {"docs/a.md": 4})

    def test_a_patch_to_a_protected_file_is_refused(self):
        changes = resolve({"ado/policy.py": [{"op": "append", "text": "# x\n"}]},
                          snapshot={"ado/policy.py": "x = 1\n"})
        with self.assertRaises(Blocked) as raised:
            validate_changes(changes, config_for(), {"ado/policy.py": "x = 1\n"})
        self.assertIn("Protected file", str(raised.exception))

    def test_patch_result_is_still_checked_against_the_allowlist(self):
        with self.assertRaises(Blocked) as raised:
            validate_changes({"src/a.py": "x = 1\n"}, config_for(), {})
        self.assertIn("outside configured allowlist", str(raised.exception))

    def test_budget_is_measured_in_the_patch_not_in_the_file(self):
        big = ORIGINAL + ("filler line\n" * 4000)
        changes = resolve({"docs/a.md": [
            {"op": "replace", "search": "PR: pull request.", "replace": "PR: x."}]},
            snapshot={"docs/a.md": big})
        # The file is far larger than the budget, but the patch is not.
        validate_changes(changes, config_for(max_bytes=100), {"docs/a.md": big})

    def test_a_patch_that_writes_too_much_is_refused(self):
        changes = resolve({"docs/a.md": [
            {"op": "replace", "search": "PR: pull request.", "replace": "x" * 200}]})
        with self.assertRaises(Blocked) as raised:
            validate_changes(changes, config_for(max_bytes=100), {"docs/a.md": ORIGINAL})
        self.assertIn("Change byte budget exceeded", str(raised.exception))

    def test_a_patch_that_changes_nothing_is_refused(self):
        changes = resolve({"docs/a.md": [
            {"op": "replace", "search": "PR: pull request.", "replace": "PR: pull request."}]})
        with self.assertRaises(Blocked) as raised:
            validate_changes(changes, config_for(), {"docs/a.md": ORIGINAL})
        self.assertIn("Unchanged file returned", str(raised.exception))


class PatchEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = config(self.temp.name)
        self.store = Store(self.temp.name)
        self.gh = GitHub()
        self.gh.files = {"docs/glossary.md": ORIGINAL}
        self.agent = PatchAgent({"docs/glossary.md": [
            {"op": "replace", "search": "PR: pull request.", "replace": "PR: プルリクエスト。"}]})
        self.engine = Engine(self.cfg, self.store, self.gh, self.agent)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_engine_publishes_the_patched_file(self):
        self.assertEqual(self.engine.tick()["state"], "PUBLISHING")
        self.assertEqual(self.engine.tick()["state"], "REVIEW")
        published = self.gh.files["docs/glossary.md"]
        self.assertEqual(published, ORIGINAL.replace("PR: pull request.", "PR: プルリクエスト。"))
        self.assertIn("A line that must not change.", published)

    def test_a_rejected_patch_is_repaired_within_the_budget(self):
        self.agent.patches = {"docs/glossary.md": [
            {"op": "replace", "search": "not in the file", "replace": "x"}]}
        # The answer is rejected, so the engine spends a bounded number of calls and
        # then stops with the reason the model has to correct.
        self.assertEqual(self.engine.tick()["state"], "BLOCKED")
        record = json.loads(self.store.get("owner/project", 1)["data"])
        self.assertIn("not unique", record["reason"])
        self.assertLessEqual(self.agent.calls, 1 + self.cfg["max_repairs"])


if __name__ == "__main__":
    unittest.main()
