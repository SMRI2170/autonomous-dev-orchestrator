import unittest

from ado.engine import Engine
from ado.policy import UnusableOutput


class Issue25RefusalTest(unittest.TestCase):
    def setUp(self):
        self.engine = Engine.__new__(Engine)

    def test_empty_change_set_is_reported_as_refusal(self):
        cfg = {
            "allowed_paths": ["docs/**/*.md"],
            "max_files": 1,
            "max_change_bytes": 1000,
        }

        with self.assertRaises(UnusableOutput) as raised:
            self.engine.usable({}, cfg, {}, {}, {"all_changes": {}})

        reason = str(raised.exception)
        self.assertIn("Model refused", reason)
        self.assertIn("no changes", reason)
        self.assertNotIn("Empty/oversized change set", reason)

    def test_oversized_change_set_keeps_budget_reason(self):
        cases = [
            (
                {"docs/one.md": "one\n", "docs/two.md": "two\n"},
                {
                    "allowed_paths": ["docs/**/*.md"],
                    "max_files": 1,
                    "max_change_bytes": 1000,
                },
            ),
            (
                {"docs/one.md": "four\n"},
                {
                    "allowed_paths": ["docs/**/*.md"],
                    "max_files": 5,
                    "max_change_bytes": 4,
                },
            ),
        ]

        for changes, cfg in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(UnusableOutput) as raised:
                    self.engine.usable(changes, cfg, {}, {}, {"all_changes": {}})

                reason = str(raised.exception)
                self.assertIn("Empty/oversized change set", reason)
                self.assertNotIn("Model refused", reason)
