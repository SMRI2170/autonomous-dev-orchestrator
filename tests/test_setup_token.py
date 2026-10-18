import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import setup_token

TOKEN = 'github_pat_' + 'testvalue' * 8


class TokenSetupTests(unittest.TestCase):
    def setUp(self):
        self.config = {'github_login': 'SMRI2170', 'repositories': [{'name': 'SMRI2170/example'}]}
        self.patch_config = patch.object(setup_token, 'load', return_value=self.config)
        self.patch_config.start(); self.addCleanup(self.patch_config.stop)
        self.patch_platform = patch('sys.platform', 'darwin')
        self.patch_platform.start(); self.addCleanup(self.patch_platform.stop)
        self.patch_keychain = patch.object(setup_token, "login_keychain", return_value="/Users/test/Library/Keychains/login.keychain-db")
        self.patch_keychain.start(); self.addCleanup(self.patch_keychain.stop)

    def test_cli_help_works_when_invoked_outside_repo(self):
        script = Path(setup_token.__file__).resolve()
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run([sys.executable, str(script), "--help"],
                                    cwd=cwd, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--config", result.stdout)

    def test_existing_token_is_never_overwritten(self):
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0)), \
             patch('getpass.getpass') as prompt, self.assertRaises(SystemExit):
            setup_token.main([])
        prompt.assert_not_called()

    def test_token_uses_configured_repositories_and_stdin_without_printing(self):
        self.config['repositories'] += [{'name': 'SMRI2170/second'}]
        calls = []
        def security(args, **kwargs):
            calls.append((args, kwargs))
            if '-i' in args:
                commands = kwargs['input'].strip().splitlines()
                if len(commands) != 1 or not commands[0].startswith('add-generic-password '):
                    return subprocess.CompletedProcess(args, 1, stdout='', stderr='unknown command')
                return subprocess.CompletedProcess(args, 0, stdout='', stderr='')
            if '-w' in args:
                return subprocess.CompletedProcess(args, 0, stdout=TOKEN+'\n', stderr='')
            return subprocess.CompletedProcess(args, 44)
        output = io.StringIO()
        with patch('subprocess.run', side_effect=security), patch('getpass.getpass', return_value=TOKEN), \
             patch.object(setup_token, 'request', side_effect=[{'login': 'SMRI2170'},
                {'full_name':'SMRI2170/example','private': False,'permissions':{'push':True}},
                {'full_name':'SMRI2170/second','private':False,'permissions':{'push':True}}]) as api, \
             contextlib.redirect_stdout(output):
            setup_token.main(['--config','another.toml'])
        setup_token.load.assert_called_with('another.toml')
        self.assertEqual(api.call_args_list[-1].args[1], 'repos/SMRI2170/second')
        self.assertNotIn(TOKEN, output.getvalue())
        self.assertIn("Contents/Issues/PR write permissions require", output.getvalue())
        self.assertTrue(all("/Users/test/Library/Keychains/login.keychain-db" in str(args) or "-i" in args for args, _ in calls))
        self.assertTrue(any(TOKEN in kwargs.get('input', '') for _, kwargs in calls))
        for args, _ in calls:
            self.assertNotIn(TOKEN, ' '.join(args))

    def test_other_account_is_rejected_before_storage(self):
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([],44)) as run, \
             patch('getpass.getpass', return_value=TOKEN), patch.object(setup_token, 'request', return_value={'login':'someone-else'}), \
             contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            setup_token.main([])
        self.assertEqual(run.call_count, 1)

    def test_any_missing_target_prevents_storage(self):
        self.config['repositories'] += [{'name':'SMRI2170/second'}]
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([],44)) as run, \
             patch('getpass.getpass', return_value=TOKEN), patch.object(setup_token, 'request', side_effect=[
                {'login':'SMRI2170'},{'full_name':'SMRI2170/example','private':False,'permissions':{'push':True}},
                {'full_name':'SMRI2170/second','private':True,'permissions':{'push':False}}]), \
             contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            setup_token.main([])
        self.assertEqual(run.call_count,1)

    def test_missing_repositories_produces_controlled_error(self):
        self.config.pop('repositories')
        with self.assertRaisesRegex(SystemExit, "Configure at least one repository"):
            setup_token.main([])

    def test_false_push_flag_does_not_pretend_to_validate_pat_write(self):
        with patch('subprocess.run', side_effect=[
                subprocess.CompletedProcess([],44),
                subprocess.CompletedProcess([],0),
                subprocess.CompletedProcess([],0,stdout=TOKEN+'\n'),
            ]), patch('getpass.getpass',return_value=TOKEN), \
            patch.object(setup_token,'request',side_effect=[
                {'login':'SMRI2170'}, {'full_name':'SMRI2170/example','private':False,
                                       'permissions':{'push':False}}]), \
            contextlib.redirect_stdout(io.StringIO()):
            setup_token.main([])

    def test_failed_readback_rolls_back_new_item(self):
        for failure in (subprocess.CompletedProcess([],1,stdout=''), subprocess.TimeoutExpired('security',15)):
            responses=[subprocess.CompletedProcess([],44),subprocess.CompletedProcess([],0),failure,subprocess.CompletedProcess([],0)]
            with self.subTest(failure=type(failure).__name__), patch('subprocess.run',side_effect=responses) as run, \
                 patch('getpass.getpass',return_value=TOKEN), patch.object(setup_token,'request',side_effect=[
                    {'login':'SMRI2170'},{'full_name':'SMRI2170/example','private':False,'permissions':{'push':True}}]), \
                 contextlib.redirect_stdout(io.StringIO()), self.assertRaises((RuntimeError,subprocess.TimeoutExpired)):
                setup_token.main([])
            self.assertIn('delete-generic-password',run.call_args.args[0])
