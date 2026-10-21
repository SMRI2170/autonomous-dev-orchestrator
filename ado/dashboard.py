"""Local, read-only projection of job state. No GitHub or model calls."""
import json
import re
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .policy import redact

ASSETS = Path(__file__).with_name("web")
DEMO_TIME = time.time()
STAGES = [
    ("spec", "仕様を確認", "Issueの受入条件と依存関係を確認します"),
    ("context", "資料を取得", "許可されたファイルをGitHubから読み取ります"),
    ("implement", "MiniMaxで実装", "Issueに沿ったファイル変更を提案します"),
    ("validate", "変更を検査", "パス・機密情報・変更量を検査します"),
    ("publish", "PRを作成", "検査済みの変更を専用ブランチで公開します"),
    ("checks", "CIを確認", "現在のコミットのテスト結果を確認します"),
    ("review", "レビュー", "承認と未解決の指摘を確認します"),
    ("handoff", "人間の確認", "PRの内容を確認し、マージを待ちます"),
    ("done", "完了", "マージを確認してIssueを完了します"),
]
STAGE_IDS = {x[0] for x in STAGES} | {"merge"}
STATE_STAGE = {"QUEUED": "spec", "PLANNED": "spec", "BUILDING": "implement", "PUBLISHING": "publish",
               "REVIEWING": "review", "REVIEW": "review", "AWAITING_HUMAN": "handoff", "DONE": "done"}


def clean(value, limit=500):
    return redact(str(value or ""))[:limit]


def read_events(root):
    """Bound work on a long-running JSONL log; ignore an incomplete last line."""
    try:
        with (root / "events.jsonl").open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 524288))
            lines = stream.read(524288).splitlines()
        result = []
        for line in lines[-1000:]:
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    result.append(value)
            except (ValueError, UnicodeError):
                continue
        return result
    except FileNotFoundError:
        return []


def public_event(event):
    result = {k: event[k] for k in ("time", "job", "repairs") if isinstance(event.get(k), (int, float))}
    result["event"] = clean(event.get("event"), 40)
    if event.get("stage") in STAGE_IDS:
        result["stage"] = event["stage"]
    if isinstance(event.get("reasons"), list):
        result["reasons"] = [clean(x) for x in event["reasons"][:5] if isinstance(x, str)]
    if event.get("reason"):
        result["reason"] = clean(event["reason"])
    return result


def snapshot(config):
    root = Path(config["state_dir"])
    now = time.time()
    events = read_events(root)
    db_path = root / "state.sqlite"
    jobs = []
    if db_path.exists():
        # mode=ro prevents accidental creation/writes. Do not instantiate Store here.
        connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute("SELECT * FROM jobs ORDER BY updated DESC LIMIT 200").fetchall()
        finally:
            connection.close()
        for row in rows:
            data = json.loads(row["data"])
            history = [public_event(e) for e in events if e.get("job") == row["id"]][-60:]
            latest = history[-1] if history else {}
            stage = STATE_STAGE.get(row["state"], STATE_STAGE.get(data.get("resume"), "spec"))
            if row["state"] in ("QUEUED", "BUILDING", "REVIEWING", "REVIEW", "BLOCKED"):
                stage = next((e["stage"] for e in reversed(history) if "stage" in e), stage)
            if data.get("resume") == "BUILDING" and row["state"] == "BLOCKED" and stage == "spec":
                stage = "implement"
            repo = clean(row["repo"], 160)
            url = "https://github.com/" + repo if re.fullmatch(r"[\w.-]+/[\w.-]+", repo) else None
            paths = data.get("all_changes", data.get("changes", {}))
            jobs.append({"id": row["id"], "repo": repo, "issue": row["issue"], "state": row["state"],
                         "title": clean(data.get("issue_title") or f"Issue #{row['issue']}", 180),
                         "stage": stage, "updated": row["updated"], "repairs": data.get("repairs", 0),
                         "calls": data.get("calls", 0), "pr": data.get("pr"),
                         "head": clean(data.get("head"), 40), "reason": clean(data.get("reason")),
                         "waiting": latest.get("reasons", []), "files": [clean(p, 180) for p in paths][:20],
                         "events": history, "issue_url": f"{url}/issues/{row['issue']}" if url else None,
                         "pr_url": f"{url}/pull/{data['pr']}" if url and isinstance(data.get("pr"), int) else None})
    latest_scan = next((e for e in reversed(events) if e.get("event") == "DRY_RUN"), None)
    planned = []
    if latest_scan:
        for item in latest_scan.get("planned", [])[:200]:
            planned.append({"repo": clean(item.get("repo"), 160), "issue": item.get("issue"),
                            "title": clean(item.get("title") or f"Issue #{item.get('issue')}", 180),
                            "result": clean(item.get("result")), "time": latest_scan.get("time")})
    worker = {"status": "unknown", "last_seen": None}
    try:
        pulse = json.loads((root / "worker.json").read_text())
        stamp = float(pulse["time"])
        worker = {"status": "online" if pulse.get("running") and -5 <= now-stamp <= 15 else "offline",
                  "last_seen": stamp}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {"demo": False, "time": now, "dry_run": config["dry_run"], "auto_merge": config["auto_merge"],
            "worker": worker, "repositories": [r["name"] for r in config["repositories"]],
            "stages": [{"id": s, "label": label, "description": desc} for s, label, desc in STAGES],
            "jobs": jobs, "planned": planned, "last_scan": latest_scan.get("time") if latest_scan else None}


def demo_snapshot(config):
    """Explicit demo data, generated in memory; never touches the live state."""
    data = {"demo": True, "time": time.time(), "dry_run": False, "auto_merge": False,
            "worker": {"status": "online", "last_seen": time.time()}, "repositories": ["example/ado-demo"],
            "stages": [{"id": s, "label": label, "description": desc} for s, label, desc in STAGES],
            "jobs": [], "planned": [], "last_scan": None}
    for number, state, stage, title in [(12, "REVIEW", "review", "日本語の開発用語集を追加する"),
                                        (11, "BUILDING", "implement", "READMEの導入手順を分かりやすくする"),
                                        (10, "BLOCKED", "validate", "チュートリアルのサンプルを更新する"),
                                        (9, "DONE", "done", "PRの作成手順を追加する")]:
        stamp = DEMO_TIME - (12-number)*180
        history = [{"time": stamp-180+i*20, "job": number, "event": "STEP", "stage": sid}
                   for i, sid in enumerate([s[0] for s in STAGES]) if i <= [s[0] for s in STAGES].index(stage)]
        data["jobs"].append({"id": number, "repo": "example/ado-demo", "issue": number, "state": state,
                             "title": title, "stage": stage, "updated": stamp, "repairs": 1 if number == 12 else 0,
                             "calls": 2 if number == 12 else 1, "pr": 24 if number == 12 else None,
                             "head": "a1b2c3d4", "reason": "Protected file: .github/workflows/ci.yml" if number == 10 else "",
                             "waiting": ["missing CodeRabbit review at current SHA"] if number == 12 else [],
                             "files": ["docs/glossary.md"] if number != 11 else [], "events": history,
                             "issue_url": None, "pr_url": None})
    return data


def create_server(config, port=8765):
    if not 0 <= port <= 65535:
        raise ValueError("Invalid dashboard port")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            host = self.headers.get("Host", "")
            if host not in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"):
                self.send_error(403)
                return
            path = urlsplit(self.path).path
            try:
                if path in ("/api/status", "/api/demo"):
                    body = json.dumps(demo_snapshot(config) if path == "/api/demo" else snapshot(config), ensure_ascii=False).encode()
                    content_type = "application/json; charset=utf-8"
                elif path in ("/", "/app.js", "/style.css"):
                    filename = "index.html" if path == "/" else path[1:]
                    body = (ASSETS / filename).read_bytes()
                    content_type = {"index.html": "text/html", "app.js": "text/javascript", "style.css": "text/css"}[filename] + "; charset=utf-8"
                else:
                    self.send_error(404)
                    return
            except (OSError, ValueError, sqlite3.Error):
                self.send_error(503, "State is temporarily unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(config, port=8765):
    server = create_server(config, port)
    print(f"ADO dashboard: http://127.0.0.1:{server.server_port} (read-only)", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
