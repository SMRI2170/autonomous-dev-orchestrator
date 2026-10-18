#!/usr/bin/env python3
"""Render a plist only; installing/starting the service is a separate operator step."""
import argparse
import plistlib
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--config", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
config = Path(args.config).expanduser().resolve(strict=True)
python = root / ".venv/bin/python"
if not python.exists():
    raise SystemExit("Create the project .venv first")
logs = root / ".state/launchd"
logs.mkdir(parents=True, exist_ok=True, mode=0o700)
plist = {
    "Label": "local.ado.worker",
    "ProgramArguments": [str(python), str(root / "scripts/launch.py"), str(config)],
    "WorkingDirectory": str(root), "RunAtLoad": True,
    "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 60,
    "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "LANG": "en_US.UTF-8"},
    "StandardOutPath": str(logs / "stdout.log"), "StandardErrorPath": str(logs / "stderr.log"),
    "Umask": 0o077,
}
with open(args.output, "wb") as file:
    plistlib.dump(plist, file)
print(Path(args.output).resolve())
