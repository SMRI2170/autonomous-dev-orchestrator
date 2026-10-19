import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ado.keychain import login_keychain


class KeychainTests(unittest.TestCase):
    def test_explicit_login_keychain_path(self):
        result = subprocess.CompletedProcess([], 0, stdout='"/Users/test/Library/Keychains/login.keychain-db"\n')
        with patch("subprocess.run", return_value=result) as run:
            self.assertEqual(login_keychain(), "/Users/test/Library/Keychains/login.keychain-db")
        self.assertEqual(run.call_args.args[0], ["/usr/bin/security", "login-keychain", "-d", "user"])

    def test_invalid_keychain_response_rejected(self):
        for stdout in ("", "relative", '"unterminated'):
            with self.subTest(stdout=stdout), patch("subprocess.run",
                    return_value=subprocess.CompletedProcess([], 0, stdout=stdout)):
                with self.assertRaises(RuntimeError):
                    login_keychain()

    def test_failed_login_command_falls_back_only_to_verified_standard_keychain(self):
        with tempfile.TemporaryDirectory() as tmp:
            standard = Path(tmp) / "Library" / "Keychains" / "login.keychain-db"
            standard.parent.mkdir(parents=True)
            standard.touch()
            responses = [
                subprocess.CompletedProcess([], 1, stdout="", stderr="not available"),
                subprocess.CompletedProcess([], 0, stdout=f'"{standard}"\n'),
                subprocess.CompletedProcess([], 0, stdout=f'    "{standard}"\n'),
            ]
            with patch("ado.keychain.Path.home", return_value=Path(tmp)), \
                 patch("subprocess.run", side_effect=responses) as run:
                self.assertEqual(login_keychain(), str(standard))
            self.assertEqual(run.call_count, 3)
            self.assertEqual(run.call_args_list[1].args[0],
                             ["/usr/bin/security", "default-keychain", "-d", "user"])
            self.assertEqual(run.call_args_list[2].args[0],
                             ["/usr/bin/security", "list-keychains", "-d", "user"])

    def test_failed_login_command_without_standard_file_refuses_fallback(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch("ado.keychain.Path.home", return_value=Path(tmp)), \
             patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1)) as run:
            with self.assertRaisesRegex(RuntimeError, "Unable to locate"):
                login_keychain()
            self.assertEqual(run.call_count, 1)

    def test_fallback_rejects_different_default_or_missing_search_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            standard = Path(tmp) / "Library" / "Keychains" / "login.keychain-db"
            standard.parent.mkdir(parents=True)
            standard.touch()
            other = standard.parent / "other.keychain-db"
            for default, listed in ((str(other), str(standard)), (str(standard), str(other))):
                responses = [
                    subprocess.CompletedProcess([], 1, stdout=""),
                    subprocess.CompletedProcess([], 0, stdout=f'"{default}"\n'),
                    subprocess.CompletedProcess([], 0, stdout=f'"{listed}"\n'),
                ]
                with self.subTest(default=default, listed=listed), \
                     patch("ado.keychain.Path.home", return_value=Path(tmp)), \
                     patch("subprocess.run", side_effect=responses), \
                     self.assertRaisesRegex(RuntimeError, "fallback could not be safely validated"):
                    login_keychain()

    def test_fallback_rejects_security_command_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            standard = Path(tmp) / "Library" / "Keychains" / "login.keychain-db"
            standard.parent.mkdir(parents=True)
            standard.touch()
            for failure in (1, 2):
                responses = [
                    subprocess.CompletedProcess([], 1, stdout=""),
                    subprocess.CompletedProcess([], failure == 1, stdout=f'"{standard}"\n'),
                    subprocess.CompletedProcess([], failure == 2, stdout=f'"{standard}"\n'),
                ]
                with self.subTest(failure=failure), \
                     patch("ado.keychain.Path.home", return_value=Path(tmp)), \
                     patch("subprocess.run", side_effect=responses), \
                     self.assertRaisesRegex(RuntimeError, "fallback could not be safely validated"):
                    login_keychain()
