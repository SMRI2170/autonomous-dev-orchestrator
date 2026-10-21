import sys
import time
import unittest
from ado.policy import Blocked
from ado.process import run


def script(body):
    return [sys.executable, "-c", body]


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
        # A child that inherits the pipes would keep the call open; the runner stops it.
        command = script("import subprocess, sys, time\n"
                         "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                         "time.sleep(30)\n")
        with self.assertRaises(Blocked):
            run(command, 0.5, label="probe")

    def test_payload_is_written_to_stdin(self):
        stdout, _ = run(script("import sys; sys.stdout.write(sys.stdin.read().upper())"),
                        10, payload=b"hello", label="probe")
        self.assertEqual(stdout, b"HELLO")