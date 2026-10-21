import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from ado.policy import Blocked
from ado.process import run


def script(body):
    return [sys.executable, "-c", body]


def alive(pid):
    """True while the process still exists and is not an unreaped zombie.

    A killed descendant is reparented to init, which may take a moment to reap it;
    a zombie holds a pid but is not running, so it does not count as surviving.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    state = subprocess.run(["ps", "-o", "state=", "-p", str(pid)],
                           capture_output=True, text=True).stdout.strip()
    return not state.startswith("Z")


class ProcessTests(unittest.TestCase):
    def test_failure_message_never_contains_program_output(self):
        with self.assertRaises(Blocked) as caught:
            run(script("import sys; sys.stderr.write('SECRET_PRIVATE_RESPONSE'); sys.exit(1)"),
                10, label="probe")
        self.assertEqual(str(caught.exception), "probe failed")
        self.assertNotIn("SECRET_PRIVATE_RESPONSE", str(caught.exception))

    def test_pulse_runs_throughout_the_call(self):
        beats = []
        run(script("import time; time.sleep(0.4)"), 10, label="probe", pulse=lambda: beats.append(1))
        self.assertGreaterEqual(len(beats), 2)

    def test_budget_is_combined_across_streams(self):
        # Neither stream reaches 2,000,000 bytes, but together they do.
        writer = ("import sys\n"
                  "sys.stdout.buffer.write(b'x'*1100000); sys.stdout.buffer.flush()\n"
                  "sys.stderr.buffer.write(b'y'*1100000)\n")
        with self.assertRaises(Blocked) as caught:
            run(script(writer), 10, limit=2_000_000, label="probe")
        self.assertIn("output budget", str(caught.exception))

    def test_deadline_is_enforced_and_the_process_is_reaped(self):
        started = time.monotonic()
        with self.assertRaises(Blocked) as caught:
            run(script("import time; time.sleep(30)"), 0.5, label="probe")
        self.assertIn("execution deadline exceeded", str(caught.exception))
        self.assertLess(time.monotonic() - started, 10)

    def test_descendants_do_not_outlive_the_call(self):
        # The CLI can exit while a child it spawned still holds the pipes open; the
        # group kill is the only thing that ends that child. Assert on the pid rather
        # than on the call returning -- with a parent that outlives its child, checking
        # the return value would pass even with no killpg at all.
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / "pid"
            command = script("import subprocess, sys\n"
                             f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
                             f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n")
            run(command, 10, label="probe")
            child = int(pidfile.read_text())
            for _ in range(60):
                if not alive(child):
                    return
                time.sleep(0.05)
            self.fail(f"Descendant {child} survived the process-group kill")

    def test_payload_is_written_to_stdin(self):
        stdout, _ = run(script("import sys; sys.stdout.write(sys.stdin.read().upper())"),
                        10, payload=b"hello", label="probe")
        self.assertEqual(stdout, b"HELLO")