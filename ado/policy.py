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


def parses(path, content):
    """A changed Python file must at least parse.

    The model returns the complete file as a JSON string, and one stray escape turns
    every line continuation into the six characters of an escape sequence: the file
    that reaches the pull request does not parse, and the only signal is a red build.
    The check belongs here, where the model cannot remove it, and it gives the model
    a precise reason to correct itself.
    """
    if not path.endswith(".py"):
        return
    try:
        compile(content, path, "exec")
    except SyntaxError as exc:
        raise Blocked("Changed file does not compile: %s line %s: %s"
                      % (path, exc.lineno, exc.msg)) from exc
    except ValueError as exc:
        raise Blocked("Changed file cannot be compiled: " + path) from exc


class Changes(dict):
    """The resolved changes of one answer, with the bytes the model wrote for each path.

    A patch writes only the changed parts, so its cost is the patch payload. A full
    replacement writes the whole file, so its cost is the file. The mapping interface
    is the one the engine has always used; the costs only feed the change budget.
    """

    def __init__(self, mapping, costs=None):
        super().__init__(mapping)
        self.costs = dict(costs or {})

    def cost_of(self, path, content):
        return self.costs.get(path, len(content.encode()))


def cost_of(changes, path, content):
    """The bytes the model wrote for one path of one answer.

    An answer that carries its costs says so; any other mapping is measured by
    the content it holds, which is what a full replacement costs.
    """
    if isinstance(changes, Changes):
        return changes.cost_of(path, content)
    return len(content.encode())


def accumulate_costs(previous, changes):
    """The per-path byte total of a job after one more answer.

    A path can be written more than once in a job: a repair round rewrites what
    an earlier round wrote. The budget must count both, or a job could spend
    twice its budget on one file by splitting the work across rounds.
    """
    total = dict(previous or {})
    for path, content in changes.items():
        total[path] = total.get(path, 0) + cost_of(changes, path, content)
    return total


def validate_changes(changes, config, snapshot):
    if not isinstance(changes, dict) or not changes or len(changes) > config.get("max_files", 5):
        raise Blocked("Empty/oversized change set")
    size = 0
    for path, content in changes.items():
        safe_path(path)
        if not matches(path, config["allowed_paths"]):
            raise Blocked("File outside configured allowlist: " + path)
        inspect_text(content, snapshot.get(path), documentation=path.endswith('.md'))
        parses(path, content)
        # The budget measures what the model wrote. A patch writes only the changed
        # parts, so its cost is the payload; a full replacement writes the whole file,
        # so its cost is the file. Both are the bytes the model authored.
        size += cost_of(changes, path, content)
        if snapshot.get(path) == content:
            raise Blocked("Unchanged file returned: " + path)
    if size > config.get("max_change_bytes", 50000):
        raise Blocked("Change byte budget exceeded")
    context_limits(changes, config, snapshot)


def context_limits(changes, config, snapshot):
    """Check the resolved files against the limits the context will be read under.

    A file that this job publishes is read back into the context of the next
    round, and the reading applies limits: one per file, and one for the whole
    context. A change that pushes a file or the context past those limits would
    leave the job unable to continue after it published, so the limits are
    checked here, where the model can still correct the change.
    """
    per_file = config.get("max_context_file_bytes", 50000)
    total_budget = config.get("max_context_bytes", 300000)
    total = 0
    for path, content in changes.items():
        written = len(content.encode())
        if written > per_file:
            raise Blocked("Resolved file exceeds the per-file context limit: %s (%d bytes > %d)"
                          % (path, written, per_file))
        total += written
    for path, content in snapshot.items():
        if path not in changes:
            total += len(content.encode())
    if total > total_budget:
        raise Blocked("Context budget exceeded: %d bytes of context, budget %d; "
                      "narrow allowed_paths/context_paths or raise max_context_bytes"
                      % (total, total_budget))


def low_risk(changes, config):
    # Hard floor: executable/code changes never auto-merge in this MVP.
    return all(p.endswith(".md") and p.startswith("docs/") and
               matches(p, config.get("auto_merge_paths", [])) for p in changes)


def parse_changes(output, snapshot=None):
    """Read the agent's answer.

    These shapes are accepted:

    {"files": {"path": "complete content"}} -- the whole file is written.
    {"patches": {"path": [operations]}} -- only the changed parts are written.
    {"files": {...}, "patches": {...}} -- both at once, for an issue that creates
    a file and changes another. The two sets of paths must not overlap: a file
    is either written whole or patched, never both.

    The returned mapping is the resolved content per path. A patch is resolved
    against the files that exist now, so the result is the file as it will be
    committed, and the bytes the model wrote for each path travel with it.
    """
    try:
        def unique(pairs):
            result = {}
            for key, val in pairs:
                if key in result:
                    raise ValueError("Duplicate key")
                result[key] = val
            return result
        data = json.loads(output, object_pairs_hook=unique)
        keys = set(data)
        if not keys or not keys <= {"files", "patches"}:
            raise ValueError("Expected only files object or patches object")
        files = data.get("files", {})
        patches = data.get("patches", {})
        if not isinstance(files, dict) or not isinstance(patches, dict):
            raise ValueError("files and patches must be objects")
        for content in files.values():
            if not isinstance(content, str):
                raise ValueError("File content must be a string")
        overlap = set(files) & set(patches)
        if overlap:
            # A file that is written whole and patched at the same time has no
            # defined result, so the answer is refused rather than guessed.
            raise UnusableOutput("A path cannot be both written and patched: "
                                  + ", ".join(sorted(overlap)))
        resolved, costs = {}, {}
        if patches:
            if snapshot is None:
                raise ValueError("Patches need the current files")
            patched = resolve_patches(patches, snapshot)
            resolved.update(patched)
            costs.update(patched.costs)
        for path, content in files.items():
            resolved[path] = content
            costs[path] = len(content.encode())
        return Changes(resolved, costs)
    except UnusableOutput:
        raise
    except (ValueError, TypeError) as exc:
        raise Blocked("Agent must return a single strict JSON object with files or patches") from exc


def resolve_patches(patches, snapshot):
    """Apply patch operations to the current files.

    A patch that names a file the snapshot does not hold, or an anchor that is
    missing or ambiguous, is rejected instead of guessed. A wrong guess would
    rewrite the wrong place, which is the failure this contract removes.
    """
    changes, costs = {}, {}
    for path, operations in patches.items():
        if not isinstance(operations, list) or not operations:
            raise UnusableOutput("Patch operations must be a non-empty list: " + path)
        if path not in snapshot:
            raise UnusableOutput("Patch target is not among the current files: " + path)
        content, authored = apply_patch(path, snapshot[path], operations)
        changes[path] = content
        costs[path] = authored
    return Changes(changes, costs)


def apply_patch(path, content, operations):
    """Apply the operations of one patch to the current content of one file.

    The operations run in order, each one against the result of the previous
    one. The returned pair is the new content and the number of bytes the
    model wrote, which is what the change budget is measured in.
    """
    authored = 0
    for operation in operations:
        if not isinstance(operation, dict):
            raise UnusableOutput("Patch operation must be an object: " + path)
        op = operation.get("op")
        if op == "replace":
            search, replace = operation.get("search"), operation.get("replace")
            if not isinstance(search, str) or not isinstance(replace, str):
                raise UnusableOutput("replace needs search and replace text: " + path)
            content = replace_once(path, content, search, replace)
            authored += len(replace.encode())
        elif op == "insert_after":
            search, text = operation.get("search"), operation.get("text")
            if not isinstance(search, str) or not isinstance(text, str):
                raise UnusableOutput("insert_after needs search and text: " + path)
            content = replace_once(path, content, search, search + text)
            authored += len(text.encode())
        elif op == "prepend":
            text = operation.get("text")
            if not isinstance(text, str):
                raise UnusableOutput("prepend needs text: " + path)
            content = text + content
            authored += len(text.encode())
        elif op == "append":
            text = operation.get("text")
            if not isinstance(text, str):
                raise UnusableOutput("append needs text: " + path)
            content = content + text
            authored += len(text.encode())
        else:
            raise UnusableOutput("Unknown patch operation %r: %s" % (op, path))
    return content, authored


def count_occurrences(content, search):
    """How many places the anchor could match, counting overlapping matches.

    str.count does not count overlapping occurrences: "aa" occurs twice in "aaa",
    but count reports one. An anchor that could match in two places is ambiguous
    even when the matches overlap, so every start position has to be counted.
    """
    total = 0
    index = content.find(search)
    while index != -1:
        total += 1
        index = content.find(search, index + 1)
    return total


def replace_once(path, content, search, replace):
    """Replace one exact occurrence, and only if there is exactly one."""
    if not search:
        raise UnusableOutput("Patch anchor is empty: " + path)
    count = count_occurrences(content, search)
    if count != 1:
        raise UnusableOutput("Patch anchor is not unique in %s: %d matches" % (path, count))
    return content.replace(search, replace, 1)


def dependencies(body):
    lines = re.findall(r"(?im)^Depends-On:\s*(.*?)\s*$", body)
    result = []
    for line in lines:
        if not re.fullmatch(r"#\d+(?:\s*,\s*#\d+)*", line):
            raise Blocked("Depends-On requires local issue numbers, e.g. #12, #13")
        result.extend(int(x) for x in re.findall(r"#(\d+)", line))
    return sorted(set(result))
