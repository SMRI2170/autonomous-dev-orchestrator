import json
import tempfile
import unittest

from ado.engine import Engine
from ado.policy import Blocked, UnusableOutput, accumulate_costs, parse_changes, validate_changes
from ado.state import Store
from tests.fakes import GitHub, config

ORIGINAL = "# Glossary\n\nPR: pull request.\n\nA line that must not change.\n"


def resolve(patches, snapshot=None):
    return parse_changes(json.dumps({"patches": patches}), snapshot or {"docs/a.md": ORIGINAL})


def config_for(max_bytes=50000, max_files=1, allowed=("docs/**/*.md",), **extra):
    cfg = {"allowed_paths": list(allowed), "max_files": max_files,
           "max_change_bytes": max_bytes}
    cfg.update(extra)
    return cfg


class AnchorAmbiguityTests(unittest.TestCase):
    def test_an_overlapping_anchor_is_ambiguous_and_refused(self):
        # Regression: str.count does not count overlapping occurrences, so "aa" in
        # "aaa" was reported once and the patch was applied to the first match,
        # leaving the file in a state the model never asked for.
        with self.assertRaises(UnusableOutput) as raised:
            resolve({"docs/a.md": [{"op": "replace", "search": "aa", "replace": "x"}]},
                    snapshot={"docs/a.md": "aaa"})
        self.assertIn("not unique", str(raised.exception))
        self.assertIn("2 matches", str(raised.exception))

    def test_a_single_overlapping_anchor_is_still_applied(self):
        changes = resolve({"docs/a.md": [{"op": "replace", "search": "aa", "replace": "x"}]},
                          snapshot={"docs/a.md": "aab"})
        self.assertEqual(changes["docs/a.md"], "xb")


class ContextLimitTests(unittest.TestCase):
    def test_a_resolved_file_over_the_per_file_limit_is_refused(self):
        # Regression: the resolved file was never checked against the limit the
        # context is read under, so a patch could publish a file that the next
        # round of the same job cannot read back.
        changes = resolve({"docs/a.md": [
            {"op": "append", "text": "x" * 500}]})
        with self.assertRaises(Blocked) as raised:
            validate_changes(changes, config_for(max_context_file_bytes=100), {"docs/a.md": ORIGINAL})
        self.assertIn("per-file context limit", str(raised.exception))

    def test_a_resolved_file_within_the_per_file_limit_is_accepted(self):
        changes = resolve({"docs/a.md": [{"op": "append", "text": "x" * 50}]})
        validate_changes(changes, config_for(max_context_file_bytes=200), {"docs/a.md": ORIGINAL})

    def test_the_context_budget_is_checked_after_the_change(self):
        changes = resolve({"docs/a.md": [{"op": "append", "text": "x" * 60}]})
        with self.assertRaises(Blocked) as raised:
            validate_changes(changes, config_for(max_context_bytes=100), {"docs/a.md": ORIGINAL})
        self.assertIn("Context budget exceeded", str(raised.exception))

    def test_the_context_budget_counts_the_changed_file_once(self):
        changes = resolve({"docs/a.md": [{"op": "append", "text": "x" * 40}]})
        # The unchanged context plus the resolved file is 100 bytes, the budget.
        validate_changes(changes, config_for(max_context_bytes=100), {"docs/a.md": ORIGINAL})


class CumulativeBudgetTests(unittest.TestCase):
    def test_the_costs_of_a_path_add_up_over_the_rounds(self):
        first = resolve({"docs/a.md": [{"op": "replace", "search": "PR: pull request.",
                                        "replace": "PR: pull request."}]})
        second = resolve({"docs/a.md": [{"op": "replace", "search": "PR: pull request.",
                                         "replace": "PR: pull request."}]})
        total = accumulate_costs(accumulate_costs({}, first), second)
        self.assertEqual(total["docs/a.md"], 2 * len("PR: pull request.".encode()))

    def test_two_rounds_cannot_spend_the_budget_twice(self):
        # Regression: the recorded cost of a path was overwritten by the next round,
        # so two rounds of 80 bytes passed a budget of 100.
        temp = tempfile.TemporaryDirectory()
        try:
            cfg = config(temp.name)
            cfg["repositories"][0]["max_change_bytes"] = 100
            store = Store(temp.name)
            gh = GitHub()
            gh.files = {"docs/glossary.md": ORIGINAL}
            engine = Engine(cfg, store, gh, None)
            changes = parse_changes(json.dumps({"patches": {"docs/glossary.md": [
                {"op": "replace", "search": "PR: pull request.", "replace": "P" * 80}]}}),
                {"docs/glossary.md": ORIGINAL})
            data = {"costs": accumulate_costs({}, changes)}
            with self.assertRaises(UnusableOutput) as raised:
                engine.usable(changes, cfg["repositories"][0], gh.files,
                              {"docs/glossary.md": "100644"}, data)
            self.assertIn("Change byte budget exceeded", str(raised.exception))
            store.db.close()
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
