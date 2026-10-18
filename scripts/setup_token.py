#!/usr/bin/env python3
"""Prompt locally and store a repository-scoped PAT in macOS login Keychain."""
import argparse
import getpass
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ado.config import load

SERVICE = "ado.github-token"
ACCOUNT = "ado"


def request(token, path):
    req = urllib.request.Request("https://api.github.com/" + path, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "User-Agent": "ado-local-setup"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)
    except (urllib.error.URLError, ValueError):
        raise RuntimeError("GitHub validation failed; token was not stored") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config.toml')
    args = parser.parse_args(argv)
    config = load(args.config)
    targets = [repo['name'] for repo in config['repositories']]
    if not targets:
        raise SystemExit("Configure at least one repository before storing a token")
    if sys.platform != "darwin":
        raise SystemExit("macOS login Keychain is required")
    exists = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", SERVICE, "-a", ACCOUNT],
                            capture_output=True, timeout=15)
    if exists.returncode == 0:
        raise SystemExit("An ADO token already exists. It was not overwritten.")
    if exists.returncode != 44:
        raise SystemExit("Cannot check Keychain availability; no token was requested")
    print("Use a fine-grained token limited to: " + ", ".join(targets))
    print("Paste it here only; input is hidden. Do not paste it into chat.")
    token = getpass.getpass("GitHub token: ").strip()
    if not re.fullmatch(r"github_pat_[A-Za-z0-9_]{20,}", token):
        raise SystemExit("Expected a fine-grained GitHub token; nothing was stored")
    user = request(token, "user")
    expected = config.get('github_login')
    if not user.get('login') or (expected and user['login'].casefold() != expected.casefold()):
        raise SystemExit("GitHub account mismatch; token was not stored")
    for target in targets:
        repo = request(token, "repos/" + target)
        if repo.get('full_name', '').casefold() != target.casefold() or repo.get("private") is not False or not repo.get("permissions", {}).get("push"):
            raise SystemExit("Configured repository write access mismatch; token was not stored")
    # security's interactive parser receives the credential via stdin, never argv.
    # Validation above permits only ASCII alphanumerics and underscore in the value.
    commands = f'add-generic-password -a "{ACCOUNT}" -s "{SERVICE}" -w "{token}"\n'
    added, verified = False, False
    try:
        result = subprocess.run(["/usr/bin/security", "-i"], input=commands, text=True,
                                capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError("Keychain did not confirm creation; no existing item was overwritten")
        added = True
        readback = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w"],
                                  capture_output=True, text=True, timeout=15)
        if readback.returncode or readback.stdout.strip() != token:
            raise RuntimeError("Keychain readback verification failed")
        verified = True
    finally:
        if added and not verified:
            rollback = subprocess.run(["/usr/bin/security", "delete-generic-password", "-s", SERVICE, "-a", ACCOUNT],
                                      capture_output=True, timeout=15)
            if rollback.returncode:
                raise RuntimeError("Unverified new Keychain item requires manual removal")
    print("ADO token saved to login Keychain for " + user['login'] + ".")
    print("Repository metadata/push access checked. Issues/PR write permissions need UI confirmation and a controlled pilot.")
    print("dry-run and auto-merge settings were not changed.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.SubprocessError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    except KeyboardInterrupt:
        raise SystemExit("Token setup cancelled") from None
