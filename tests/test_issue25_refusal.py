import unittest

from ado.engine import Engine
from ado.policy import UnusableOutput, parse_changes


class Issue25RefusalTests(unittest.TestCase):
    REFUSAL_REASON = "Model refused the work (no changes were proposed)"

    def make_engine(self):
        return Engine({}, None, None, None)

    def config(self):
        return {
            "allowed_paths": ["docs/**/*.md"],
            "max_files": 1,
            "max_change_bytes": 100,
        }

    def test_empty_changes_are_reported_as_model_refusal(self):
        engine = self.make_engine()
        changes = parse_changes('{"files":{}}')

        with self.assertRaises(UnusableOutput) as raised:
            engine.usable(changes, self.config(), {}, {}, {})

        reason = str(raised.exception)
        self.assertIn(self.REFUSAL_REASON, reason)
        self.assertNotIn("Empty/oversized change set", reason)

    def test_file_count_budget_has_a_distinct_reason(self):
        engine = self.make_engine()
        changes = {"docs/new.md": "new\n"}
        data = {"all_changes": {"docs/old.md": "old\n"}}

        with self.assertRaises(UnusableOutput) as raised:
            engine.usable(changes, self.config(), {}, {}, data)

        reason = str(raised.exception)
        self.assertEqual("Empty/oversized change set", reason)
        self.assertNotIn(self.REFUSAL_REASON, reason)
        self.assertNotIn("refused", reason.lower())

    def test_byte_budget_has_a_distinct_reason(self):
        engine = self.make_engine()
        changes = {"docs/new.md": "b" * 60}
        data = {"all_changes": {"docs/old.md": "a" * 60}}

        with self.assertRaises(UnusableOutput) as raised:
            engine.usable(changes, self.config(), {}, {}, data)

        reason = str(raised.exception)
        self.assertEqual("Change byte budget exceeded", reason)
        self.assertNotIn(self.REFUSAL_REASON, reason)
        self.assertNotIn("refused", reason.lower())


if __name__ == "__main__":
    unittest.main()
