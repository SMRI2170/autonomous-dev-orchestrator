"""Deterministic boundaries. Model text is never used as a command or authority."""
import fnmatch
import json
import re
from collections import Counter
from pathlib import PurePosixPath


class Blocked(RuntimeError):
    pass


class UnusableOutput(Blocked):
    """The model call finished and was billed; only its output was rejected.

    Distinct from an in-flight failure. An interrupted call must never be replayed,
    but a completed one is already spent either way, so a bounded repair attempt
    recovers the job instead of discarding the work it has already published.
    """


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
    r"package|lock|pubspec|requirements|pyproject|setup\.(?:py|cfg)|gradle|podfile|gemfile).*)(?:/|$)|"
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


def inspect_source(text):
    """Check text read back from the repository before it becomes model context.

    Credentials are refused: they would otherwise ride into the prompt and could be
    echoed back into an output change. A committed file that merely *names* a
    dangerous primitive is ordinary documentation -- a security note, a migration log,
    our own verification history -- and refusing it made the orchestrator unable to
    read its own repository. inspect_text keeps the stricter rule for model output.
    """
    if not isinstance(text, str) or "\x00" in text:
        raise Blocked("Only UTF-8 text files are supported")
    if SECRET.search(text):
        raise Blocked("Potential secret detected")


def dangerous_counts(text):
    """How often each dangerous primitive appears in the text."""
    return Counter(DANGEROUS.findall(text))


def inspect_text(text, previous=None, documentation=False):
    """Check text the model proposed, which may become executable project content.

    Documentation may repeat what the repository already says: this repository's own
    verification record names a dangerous primitive, because it documents an incident,
    and the model must still be able to correct that file. So for documentation the
    check asks what the change *adds* -- every primitive the new content has must
    already be in the old content, as often. A reworded sentence is fine, a second
    mention is not, and a different primitive is not either.

    Code keeps the stricter rule: a file that contains a primitive at all is refused,
    so a change can never trade one primitive for another. A new file is checked whole,
    because everything in it is new. A credential is refused whatever the file already
    contained.
    """
    inspect_source(text)
    if previous is None or not documentation:
        if DANGEROUS.search(text):
            raise Blocked("Dangerous command or execution primitive detected")
        return
    if dangerous_counts(text) - dangerous_counts(previous):
        raise Blocked("Dangerous command or execution primitive detected")


def validate_changes(changes, config, snapshot):
    if not isinstance(changes, dict) or not changes or len(changes) > config.get("max_files", 5):
        raise Blocked("Empty/oversized change set")
    size = 0
    for path, content in changes.items():
        safe_path(path)
        if not matches(path, config["allowed_paths"]):
            raise Blocked("File outside configured allowlist: " + path)
        inspect_text(content, snapshot.get(path), documentation=path.endswith('.md'))
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
