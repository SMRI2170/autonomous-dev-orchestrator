import subprocess
import unittest
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
