import os
import re
import tomllib
from pathlib import Path
from .policy import Blocked


def load(path):
    with open(path, "rb") as file:
        config = tomllib.load(file)
    if "github_login" in config and (not isinstance(config["github_login"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", config["github_login"])):
        raise Blocked("Invalid github_login")
    config.setdefault("dry_run", True)
    config.setdefault("auto_merge", False)
    config.setdefault("state_dir", "~/.local/state/ado")
    config.setdefault("poll_seconds", 60)
    config.setdefault("review_timeout_seconds", 86400)
    config.setdefault("max_repairs", 3)
    for key in ("dry_run", "auto_merge"):
        if type(config[key]) is not bool:
            raise Blocked(key + " must be boolean")
    for key, lower, upper in (("max_repairs", 0, 3), ("poll_seconds", 30, 3600),
                              ("review_timeout_seconds", 60, 604800)):
        if type(config[key]) is not int or not lower <= config[key] <= upper:
            raise Blocked("Invalid " + key)
    config["state_dir"] = str(Path(config["state_dir"]).expanduser().resolve())
    repos = config.get("repositories", [])
    seen = set()
    for repo in repos:
        name = repo.get("name", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name) or name in seen:
            raise Blocked("Invalid or duplicate repository")
        seen.add(name)
        if not repo.get("allowed_paths") or not repo.get("trusted_authors"):
            raise Blocked("Each repository requires allowed_paths and trusted_authors")
        repo.setdefault("issue_label", "ado:ready")
        repo.setdefault("required_checks", [{"name": "unit", "app": "github-actions"}])
        repo.setdefault("coderabbit_login", "coderabbitai[bot]")
        # Do not mutate legacy configs merely to apply the default: active jobs
        # persist a hash of repo config and must not be invalidated by an upgrade.
        if repo.get("review_provider", "coderabbit") not in ("coderabbit", "minimax"):
            raise Blocked("review_provider must be coderabbit or minimax")
        if not repo["required_checks"]:
            raise Blocked("At least one CI check is required")
        for check in repo["required_checks"]:
            if set(check) != {"name", "app"} or check["app"] != "github-actions":
                raise Blocked("MVP requires named GitHub Actions checks")
        repo.setdefault("context_paths", ["README.md"])
        if type(repo.get("auto_merge", False)) is not bool:
            raise Blocked("repository auto_merge must be boolean")
    # No API-key/provider fallback is implemented. Existing subscription login only.
    return config
