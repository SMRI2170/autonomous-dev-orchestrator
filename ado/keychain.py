"""Locate the current user's login Keychain without trusting an arbitrary default."""
import shlex
import subprocess
from pathlib import Path


def _paths(output):
    try:
        paths = shlex.split(output)
    except ValueError:
        return []
    if any(not Path(p).is_absolute() or any(ord(c) < 32 for c in p) for p in paths):
        return []
    return paths


def login_keychain():
    result = subprocess.run(
        ["/usr/bin/security", "login-keychain", "-d", "user"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode == 0:
        paths = _paths(result.stdout)
        if len(paths) != 1:
            raise RuntimeError("Invalid login Keychain path")
        return paths[0]

    # Some macOS configurations report exit 1 for login-keychain even when
    # the standard login.keychain-db exists and is the configured default.
    # Never silently choose an arbitrary default or another user's Keychain.
    expected = Path.home() / "Library" / "Keychains" / "login.keychain-db"
    if not expected.is_file():
        raise RuntimeError("Unable to locate the login Keychain")
    default = subprocess.run(
        ["/usr/bin/security", "default-keychain", "-d", "user"],
        capture_output=True, text=True, timeout=15,
    )
    search = subprocess.run(
        ["/usr/bin/security", "list-keychains", "-d", "user"],
        capture_output=True, text=True, timeout=15,
    )
    if default.returncode or search.returncode or \
            _paths(default.stdout) != [str(expected)] or str(expected) not in _paths(search.stdout):
        raise RuntimeError("Login Keychain fallback could not be safely validated")
    return str(expected)
