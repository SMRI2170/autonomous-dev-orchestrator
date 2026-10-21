import argparse
import json
import os
import sys
import time
from .agent import MiniMax
from .config import load
from .engine import Engine
from .github import GitHub
from .policy import Blocked, redact
from .state import Store


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Single-job local GitHub development orchestrator")
    parser.add_argument("--config", default="config.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--once", action="store_true", help="One state transition / read-only dry-run scan")
    sub.add_parser("status")
    retry = sub.add_parser("retry")
    retry.add_argument("job", type=int)
    sub.add_parser("doctor")
    dashboard = sub.add_parser("dashboard", help="Read-only local flow monitor")
    dashboard.add_argument("--port", type=int, default=8765)
    try:
        args = parser.parse_args()
        cfg = load(args.config)
        if args.command == "dashboard":
            from .dashboard import serve
            serve(cfg, args.port)
            return 0
        store = Store(cfg["state_dir"])
        if args.command == "status":
            print(json.dumps([{"job": r["id"], "repo": r["repo"], "issue": r["issue"], "state": r["state"],
                               "repairs": json.loads(r["data"]).get("repairs", 0),
                               "reason": json.loads(r["data"]).get("reason")}
                              for r in store.rows()], ensure_ascii=False, indent=2))
        elif args.command == "retry":
            with store.lock():
                store.retry(args.job)
        elif args.command == "doctor":
            import platform
            import shutil
            print(json.dumps({"python": sys.version.split()[0], "macos": platform.system() == "Darwin",
                              "gh_available": bool(shutil.which("gh")), "dry_run": cfg["dry_run"],
                              "auto_merge": cfg["auto_merge"], "repository_count": len(cfg["repositories"]),
                              "explicit_github_token": bool(os.environ.get("GH_TOKEN")),
                              "subscription_confirmed": cfg.get("minimax", {}).get("subscription_confirmed", False),
                              "minimax_runtime": MiniMax(cfg.get("minimax", {})).runtime_status(),
                              "projects_write_supported": False, "codex_automatic_supported": False}, indent=2))
        else:
            engine = Engine(cfg, store, GitHub(cfg["dry_run"]), MiniMax(cfg.get("minimax", {})))
            with store.heartbeat():
                while True:
                    print(redact(json.dumps(engine.tick(), ensure_ascii=False)), flush=True)
                    if args.once:
                        break
                    time.sleep(cfg["poll_seconds"])
    except (Blocked, OSError, ValueError) as exc:
        print(redact(str(exc)), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0
