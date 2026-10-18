#!/usr/bin/env python3
"""launchd entry point: read token from login Keychain, never persist it in plist."""
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
if len(sys.argv) != 2 or not Path(sys.argv[1]).is_absolute():
    raise SystemExit("An absolute config path is required")
sys.path.insert(0, str(root))
from ado.config import load
cfg = load(sys.argv[1])
env = {k: v for k, v in os.environ.items() if k in ("HOME", "PATH", "LANG")}
if not cfg["dry_run"]:
    result = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", "ado.github-token", "-a", "ado", "-w"],
                            capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise SystemExit("ADO token unavailable in login Keychain")
    env["GH_TOKEN"] = result.stdout.strip()
os.chdir(root)
os.execve(sys.executable, [sys.executable, "-m", "ado", "--config", sys.argv[1], "run"], env)
