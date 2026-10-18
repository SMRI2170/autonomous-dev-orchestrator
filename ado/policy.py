"""Deterministic boundaries. Model text is never used as a command or authority."""
import fnmatch
import json
import re
from pathlib import PurePosixPath


class Blocked(RuntimeError):
    pass


SECRET = re.compile(
    r"(?i)(?:gh[pousr]_[a-z0-9_]{20,}|github_pat_[a-z0-9_]{20,}|"
    r"sk-[a-z0-9_-]{16,}|AKIA[A-Z0-9]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?:password|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*['\"]?[^\s'\"]{8,}|"
    r"bearer\s+[a-z0-9._~-]{12,})"
)
PROTECTED = re.compile(
    r"(?i)(?:^|/)(?:\.[^/]+|agents\.md|claude\.md|gemini\.md|"
    r".*(?:secret|credential|password|permission|entitlement|auth|security|policy|"
    r"keychain|token|migration|billing|payment|deploy|dockerfile|makefile|"
    r"package|lock|pubspec|requirements|pyproject|setup|gradle|podfile|gemfile).*)(?:/|$)|"
    r"(?i:\.(?:pem|key|p12|pfx|keystore|mobileprovision|sh|bash|zsh|ps1|bat|exe|plist)$)"
)
DANGEROUS = re.compile(
    r"(?i)(?:\brm\s+(?:-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\b|\bsudo\b|"
    r"\bchmod\b|\bchown\b|\bcurl\b[^\n]*\|\s*(?:ba)?sh\b|"
    r"\beval\s*\(|\bexec\s*\(|\bos\.system\s*\(|\bsubprocess\.|"
    r"\bchild_process\b|\bProcess\.(?:run|start)\b)"
)


def redact(value):
    return SECRET.sub("[REDACTED]", str(value))


def matches(path, patterns):
    # pathlib-like **/ also matches zero directories, unlike fnmatch alone.
    return any(fnmatch.fnmatchcase(path, p) or
               ("**/" in p and matches(path, [p.replace("**/", "", 1)])) for p in patterns)


def safe_path(path):
    if not isinstance(path, str) or not path or not re.fullmatch(r"[A-Za-z0-9_./-]+", path):
        raise Blocked("Unsupported file path")
    if str(PurePosixPath(path)) != path or path.startswith("/") or ".." in path.split("/"):
        raise Blocked("Path traversal")
    if PROTECTED.search(path):
        raise Blocked("Protected file: " + path)
    return path


def inspect_text(text):
    if not isinstance(text, str) or "\x00" in text:
        raise Blocked("Only UTF-8 text files are supported")
    if SECRET.search(text):
        raise Blocked("Potential secret detected")
    if DANGEROUS.search(text):
        raise Blocked("Dangerous command or execution primitive detected")


def validate_changes(changes, config, snapshot):
    if not isinstance(changes, dict) or not changes or len(changes) > config.get("max_files", 5):
        raise Blocked("Empty/oversized change set")
    size = 0
    for path, content in changes.items():
        safe_path(path)
        if not matches(path, config["allowed_paths"]):
            raise Blocked("File outside configured allowlist: " + path)
        inspect_text(content)
        size += len(content.encode())
        if snapshot.get(path) == content:
            raise Blocked("Unchanged file returned: " + path)
    if size > config.get("max_change_bytes", 50000):
        raise Blocked("Change byte budget exceeded")


def low_risk(changes, config):
    # Hard floor: executable/code changes never auto-merge in this MVP.
    return all(p.endswith(".md") and p.startswith("docs/") and
               matches(p, config.get("auto_merge_paths", [])) for p in changes)


def parse_changes(output):
    try:
        def unique(pairs):
            result = {}
            for key, val in pairs:
                if key in result:
                    raise ValueError("Duplicate key")
                result[key] = val
            return result
        data = json.loads(output, object_pairs_hook=unique)
        if set(data) != {"files"} or not isinstance(data["files"], dict):
            raise ValueError("Expected only files object")
        return data["files"]
    except (ValueError, TypeError) as exc:
        raise Blocked("Agent must return a single strict JSON object with files") from exc


def dependencies(body):
    lines = re.findall(r"(?im)^Depends-On:\s*(.*?)\s*$", body)
    result = []
    for line in lines:
        if not re.fullmatch(r"#\d+(?:\s*,\s*#\d+)*", line):
            raise Blocked("Depends-On requires local issue numbers, e.g. #12, #13")
        result.extend(int(x) for x in re.findall(r"#(\d+)", line))
    return sorted(set(result))
