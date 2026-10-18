"""Read the macOS user's login Keychain path (not the potentially different default)."""
import shlex
import subprocess
from pathlib import Path


def login_keychain():
    result = subprocess.run(
        ["/usr/bin/security", "login-keychain", "-d", "user"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode:
        raise RuntimeError("Unable to locate the login Keychain")
    try:
        paths = shlex.split(result.stdout)
    except ValueError:
        paths = []
    if len(paths) != 1 or not Path(paths[0]).is_absolute() or any(ord(c) < 32 for c in paths[0]):
        raise RuntimeError("Invalid login Keychain path")
    return paths[0]
