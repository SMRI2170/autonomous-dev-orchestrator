"""Bounded subprocess execution: one combined output budget, a deadline, an optional liveness pulse.

Shared by the MiniMax adapter and the GitHub transport so both bound output and publish
liveness the same way. No background thread is used anywhere: the MiniMax adapter also applies
preexec_fn, which CPython documents as unsafe while another thread may hold a lock at fork.
"""
import os
import selectors
import signal
import subprocess
import time
from .policy import Blocked


def drain(proc, payload, deadline, limit, label, pulse=None):
    """Write payload to stdin while reading both pipes under one combined byte budget.

    Enforcing the cap on read is what bounds a program that writes without stopping; the
    caller's timeout only bounds how long it may run. `pulse` is called on every pass so the
    caller can publish liveness without running a background thread. Termination is the
    caller's job -- see run(), which always performs it.
    """
    os.set_blocking(proc.stdin.fileno(), False)
    offset, total = 0, 0
    streams = {"stdout": [], "stderr": []}
    with selectors.DefaultSelector() as selector:
        for name in ("stdout", "stderr"):
            selector.register(getattr(proc, name), selectors.EVENT_READ, name)
        selector.register(proc.stdin, selectors.EVENT_WRITE, "stdin")
        # Descendants can inherit the pipes and keep them open after the program
        # exits. Drain briefly, then stop.
        exited_at = None
        while selector.get_map():
            now = time.monotonic()
            remaining = deadline - now
            if pulse is not None:
                pulse()
            if remaining <= 0:
                raise Blocked(f"{label} execution deadline exceeded")
            if proc.poll() is not None:
                exited_at = exited_at or now
                if now - exited_at >= 1.0:
                    break
            for key, _ in selector.select(timeout=min(0.25, remaining)):
                stream, event = key.fileobj, key.data
                if event == "stdin":
                    try:
                        written = os.write(stream.fileno(), payload[offset:offset + 8192])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        written = 0
                        offset = len(payload)
                    offset += written
                    if offset >= len(payload):
                        selector.unregister(stream)
                        stream.close()
                else:
                    chunk = os.read(stream.fileno(), min(65536, limit + 1 - total))
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    total += len(chunk)
                    if total > limit:
                        raise Blocked(f"{label} output budget exceeded")
                    streams[event].append(chunk)
    return b"".join(streams["stdout"]), b"".join(streams["stderr"])


def run(command, timeout, payload=b"", limit=2_000_000, label="Command", failure=None,
        cwd=None, env=None, preexec_fn=None, pulse=None):
    """Run one command to completion under a deadline and a combined output budget.

    `pulse` is called throughout the call, so a caller that must publish liveness does not
    need a thread. The process group is always killed and reaped here, including when the
    deadline or the budget is exceeded, because a caller cannot be relied on to do it after
    an exception. A non-zero exit raises `failure`, or a generic message built from `label`.
    """
    proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True, preexec_fn=preexec_fn)
    try:
        deadline = time.monotonic() + timeout
        stdout, stderr = drain(proc, payload, deadline, limit=limit, label=label, pulse=pulse)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Blocked(f"{label} execution deadline exceeded")
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise Blocked(f"{label} execution deadline exceeded") from exc
        if proc.returncode:
            raise Blocked(failure or f"{label} failed")
        return stdout, stderr
    finally:
        # Also terminate surviving descendants after a normal exit.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if not stream.closed:
                stream.close()