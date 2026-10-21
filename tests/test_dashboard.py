import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from ado.dashboard import create_server, demo_snapshot, snapshot
from ado.engine import Engine
from ado.state import Store
from tests.fakes import Agent, GitHub, config


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = config(self.root / 'state')

    def tearDown(self):
        self.temp.cleanup()

    def test_empty_snapshot_and_demo_never_create_live_state(self):
        self.assertEqual(snapshot(self.cfg)['jobs'], [])
        self.assertTrue(demo_snapshot(self.cfg)['demo'])
        self.assertFalse(Path(self.cfg['state_dir']).exists())

    def test_projection_excludes_prompts_contents_and_redacts_credentials(self):
        store = Store(self.cfg['state_dir'])
        try:
            row = store.add('owner/project', 1)
            secret = 'ghp_' + 'A' * 36
            store.save(row, 'BLOCKED', {'resume': 'BUILDING', 'issue_title': '<script>text</script>',
                                      'reason': 'failed ' + secret, 'prompt': 'private prompt',
                                      'all_changes': {'docs/a.md': 'private model content'}})
            store.event(row['id'], 'STEP', stage='validate', prompt='private prompt', output='private model content')
            response = snapshot(self.cfg)
            body = json.dumps(response)
            self.assertNotIn(secret, body)
            self.assertNotIn('private prompt', body)
            self.assertNotIn('private model content', body)
            self.assertEqual(response['jobs'][0]['stage'], 'validate')
            self.assertEqual(response['jobs'][0]['files'], ['docs/a.md'])
        finally:
            store.db.close()

    def test_model_call_is_visible_while_it_is_in_flight(self):
        store = Store(self.cfg['state_dir'])
        agent = Agent()
        generate = agent.generate
        observed = []

        def inspect(*args):
            observed.append(snapshot(self.cfg)['jobs'][0])
            return generate(*args)

        agent.generate = inspect
        try:
            Engine(self.cfg, store, GitHub(), agent).tick()
            self.assertEqual(observed[0]['state'], 'BUILDING')
            self.assertEqual(observed[0]['stage'], 'implement')
            self.assertEqual(observed[0]['title'], 'Add glossary')
            self.assertEqual(snapshot(self.cfg)['jobs'][0]['stage'], 'publish')
        finally:
            store.db.close()

    def test_dry_run_candidates_do_not_appear_as_live_jobs(self):
        store = Store(self.cfg['state_dir'])
        self.cfg['dry_run'] = True
        try:
            Engine(self.cfg, store, GitHub(), Agent()).tick()
            result = snapshot(self.cfg)
            self.assertEqual(result['jobs'], [])
            self.assertEqual(result['planned'][0]['title'], 'Add glossary')
            self.assertEqual(result['planned'][0]['result'], 'ELIGIBLE')
        finally:
            store.db.close()

    def test_wait_reasons_and_partial_log_lines(self):
        store = Store(self.cfg['state_dir'])
        try:
            row = store.add('owner/project', 1)
            store.save(row, 'REVIEW', {})
            store.event(row['id'], 'WAIT', stage='checks', reasons=['running CI unit'])
            with (store.root / 'events.jsonl').open('a') as file:
                file.write('{"incomplete":')
            job = snapshot(self.cfg)['jobs'][0]
            self.assertEqual(job['stage'], 'checks')
            self.assertEqual(job['waiting'], ['running CI unit'])
        finally:
            store.db.close()

    def test_heartbeat_distinguishes_live_stopped_and_stale(self):
        store = Store(self.cfg['state_dir'])
        try:
            with store.heartbeat():
                self.assertEqual(snapshot(self.cfg)['worker']['status'], 'online')
            self.assertEqual(snapshot(self.cfg)['worker']['status'], 'offline')
            (store.root / 'worker.json').write_text(json.dumps({'running': True, 'time': time.time()-60}))
            self.assertEqual(snapshot(self.cfg)['worker']['status'], 'offline')
        finally:
            store.db.close()

    def test_unwritable_heartbeat_never_stops_the_worker(self):
        # A monitoring file that cannot be written must not abort `ado run`.
        store = Store(self.cfg['state_dir'])
        try:
            (store.root / 'worker.json').mkdir()
            with store.heartbeat():
                self.assertEqual(snapshot(self.cfg)['worker']['status'], 'unknown')
                store.event(None, 'STEP', stage='spec')
                row = store.add('owner/project', 1)
                store.save(row, 'BUILDING', {})
        finally:
            store.db.close()

    def test_in_flight_minimax_review_shows_the_review_stage(self):
        store = Store(self.cfg['state_dir'])
        try:
            row = store.add('owner/project', 1)
            store.save(row, 'REVIEWING', {'resume': 'REVIEW', 'pr': 7})
            job = snapshot(self.cfg)['jobs'][0]
            self.assertEqual(job['stage'], 'review')
            self.assertEqual(job['state'], 'REVIEWING')
        finally:
            store.db.close()

    def test_http_is_local_read_only_and_rejects_unexpected_host(self):
        server = create_server(self.cfg, 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            with urllib.request.urlopen(url + '/api/status') as response:
                self.assertEqual(json.load(response)['jobs'], [])
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
            with urllib.request.urlopen(url + '/') as response:
                self.assertIn(b'Flow monitor', response.read())
                self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
            for request, status in [(urllib.request.Request(url+'/api/status', method='POST'), 501),
                                    (urllib.request.Request(url+'/api/status', headers={'Host': 'evil.example'}), 403),
                                    (urllib.request.Request(url+'/../state.sqlite'), 404)]:
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request)
                self.assertEqual(raised.exception.code, status)
            self.assertFalse(Path(self.cfg['state_dir']).exists())
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
