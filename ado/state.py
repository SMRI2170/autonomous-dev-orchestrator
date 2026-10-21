import fcntl
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from .policy import Blocked, redact


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.db = sqlite3.connect(self.root / "state.sqlite")
        os.chmod(self.root / "state.sqlite", 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
          id INTEGER PRIMARY KEY, repo TEXT NOT NULL, issue INTEGER NOT NULL,
          state TEXT NOT NULL, data TEXT NOT NULL, updated REAL NOT NULL,
          UNIQUE(repo,issue))""")
        self.db.commit()

    @contextmanager
    def lock(self):
        with open(self.root / "worker.lock", "a") as file:
            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Blocked("Another worker owns the state directory") from exc
            try:
                yield
            finally:
                fcntl.flock(file, fcntl.LOCK_UN)

    def event(self, job, event, **fields):
        record = {"time": time.time(), "job": job, "event": event, **fields}
        # Never log prompts, model output, HTTP bodies or environment variables.
        line = redact(json.dumps(record, ensure_ascii=False))
        fd = os.open(self.root / "events.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as file:
            file.write(line + "\n")

    @contextmanager
    def heartbeat(self):
        """Publish liveness without sharing the SQLite connection across threads."""
        stopped = threading.Event()
        target = self.root / "worker.json"
        temporary = self.root / f".worker-{os.getpid()}.json"

        def write(running):
            temporary.write_text(json.dumps({"pid": os.getpid(), "time": time.time(), "running": running}))
            temporary.chmod(0o600)
            os.replace(temporary, target)

        def pulse():
            while not stopped.wait(5):
                try:
                    write(True)
                except OSError:
                    # Monitoring must not interrupt an in-flight job.
                    pass

        write(True)
        thread = threading.Thread(target=pulse, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            thread.join()
            try:
                if json.loads(target.read_text()).get("pid") == os.getpid():
                    write(False)
            except (OSError, ValueError):
                pass

    def add(self, repo, issue):
        self.db.execute("INSERT OR IGNORE INTO jobs(repo,issue,state,data,updated) VALUES(?,?,'QUEUED','{}',?)",
                        (repo, issue, time.time()))
        self.db.commit()
        return self.get(repo, issue)

    def get(self, repo, issue):
        return self.db.execute("SELECT * FROM jobs WHERE repo=? AND issue=?", (repo, issue)).fetchone()

    def rows(self):
        return self.db.execute("SELECT * FROM jobs ORDER BY id").fetchall()

    def save(self, row, state, data):
        self.db.execute("UPDATE jobs SET state=?,data=?,updated=? WHERE id=?",
                        (state, json.dumps(data), time.time(), row["id"]))
        self.db.commit()
        self.event(row["id"], state, repo=row["repo"], issue=row["issue"],
                   repairs=data.get("repairs", 0), sha=data.get("head"), reason=data.get("reason"))

    def retry(self, job_id):
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row or row["state"] != "BLOCKED":
            raise Blocked("Only an explicitly selected BLOCKED job can be retried")
        data = json.loads(row["data"])
        if data.get("repairs", 0) >= 3:
            raise Blocked("Repair budget exhausted; create a new, reviewed issue")
        phase = data.get("resume", "QUEUED")
        # Never replay an agent call after interruption; a human must inspect it.
        if phase == "BUILDING":
            raise Blocked("Interrupted model call: inspect artifacts and create a new issue")
        data.pop("reason", None)
        self.save(row, phase, data)
