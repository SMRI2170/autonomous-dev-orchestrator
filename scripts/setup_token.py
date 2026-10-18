#!/usr/bin/env python3
"""Prompt locally and store a repository-scoped PAT in macOS login Keychain."""
import getpass
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request

SERVICE = "ado.github-token"
ACCOUNT = "ado"
REPOSITORY = "SMRI2170/autonomous-dev-orchestrator"


def request(token, path):
    req = urllib.request.Request("https://api.github.com/" + path, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "User-Agent": "ado-local-setup"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)
    except (urllib.error.URLError, ValueError):
        raise RuntimeError("GitHub validation failed; token was not stored") from None


def main():
    if sys.platform != "darwin":
        raise SystemExit("macOS login Keychain is required")
    exists = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", SERVICE, "-a", ACCOUNT],
                            capture_output=True, timeout=15)
    if exists.returncode == 0:
        raise SystemExit("An ADO token already exists. It was not overwritten.")
    if exists.returncode != 44:
        raise SystemExit("Cannot check Keychain availability; no token was requested")
    print("Use a fine-grained token limited to SMRI2170/autonomous-dev-orchestrator.")
    print("Paste it here only; input is hidden. Do not paste it into chat.")
    token = getpass.getpass("GitHub token: ").strip()
    if not re.fullmatch(r"github_pat_[A-Za-z0-9_]{20,}", token):
        raise SystemExit("Expected a fine-grained GitHub token; nothing was stored")
    user = request(token, "user")
    repo = request(token, "repos/" + REPOSITORY)
    if user.get("login") != "SMRI2170" or repo.get("private") is not False or not repo.get("permissions", {}).get("push"):
        raise SystemExit("Account/repository write access mismatch; token was not stored")
    # security's interactive parser receives the credential via stdin, never argv.
    # Validation above permits only ASCII alphanumerics and underscore in the value.
    commands = f'add-generic-password -a "{ACCOUNT}" -s "{SERVICE}" -w "{token}"\n'
    result = subprocess.run(["/usr/bin/security", "-i"], input=commands, text=True,
                            capture_output=True, timeout=30)
    readback = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w"],
                              capture_output=True, text=True, timeout=15)
    if result.returncode or readback.returncode or readback.stdout.strip() != token:
        raise SystemExit("Could not verify Keychain storage; no credential was printed")
    print("ADO token saved to login Keychain. dry-run and auto-merge settings were not changed.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc)) from None
