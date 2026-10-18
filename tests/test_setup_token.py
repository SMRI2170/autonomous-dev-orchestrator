import contextlib
import io
import subprocess
import unittest
from unittest.mock import patch
from scripts import setup_token


class TokenSetupTests(unittest.TestCase):
    def test_existing_token_is_never_overwritten(self):
        with patch('sys.platform', 'darwin'), patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0)), \
             patch('getpass.getpass') as prompt, self.assertRaises(SystemExit):
            setup_token.main()
        prompt.assert_not_called()

    def test_token_is_stored_via_stdin_and_never_printed(self):
        token = 'github_pat_' + 'testvalue' * 8
        calls = []
        def security(args, **kwargs):
            calls.append((args, kwargs))
            if '-i' in args:
                commands = kwargs['input'].strip().splitlines()
                if len(commands) != 1 or not commands[0].startswith('add-generic-password '):
                    return subprocess.CompletedProcess(args, 1, stdout='', stderr='unknown command')
                return subprocess.CompletedProcess(args, 0, stdout='', stderr='')
            if '-w' in args:
                return subprocess.CompletedProcess(args, 0, stdout=token+'\n', stderr='')
            return subprocess.CompletedProcess(args, 44)
        output = io.StringIO()
        with patch('sys.platform', 'darwin'), patch('subprocess.run', side_effect=security), \
             patch('getpass.getpass', return_value=token), patch.object(setup_token, 'request', side_effect=[
                 {'login': 'SMRI2170'}, {'private': False, 'permissions': {'push': True}}]), \
             contextlib.redirect_stdout(output):
            setup_token.main()
        self.assertNotIn(token, output.getvalue())
        self.assertTrue(any(token in kwargs.get('input', '') for _, kwargs in calls))
        for args, _ in calls:
            self.assertNotIn(token, ' '.join(args))

    def test_other_account_is_rejected_before_storage(self):
        with patch('sys.platform', 'darwin'), patch('subprocess.run', return_value=subprocess.CompletedProcess([],44)) as run, \
             patch('getpass.getpass', return_value='github_pat_'+'testvalue'*8), \
             patch.object(setup_token, 'request', side_effect=[{'login':'someone-else'}, {'private':False,'permissions':{'push':True}}]), \
             contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            setup_token.main()
        self.assertEqual(run.call_count, 1)
