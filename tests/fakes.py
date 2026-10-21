import copy
import hashlib
from ado.policy import Blocked


def issue(number=1):
    return {"number": number, "title": "Add glossary", "body": "## Acceptance criteria\nExplain PR in docs/glossary.md",
            "state": "open", "user": {"login": "owner"}, "labels": [{"name": "ado:ready"}, {"name": "ado:low-risk"}]}


def repo_config():
    return {"name": "owner/project", "trusted_authors": ["owner"], "issue_label": "ado:ready",
            "allowed_paths": ["docs/**/*.md", "src/**/*.py"], "context_paths": ["README.md"],
            "required_checks": [{"name": "unit", "app": "github-actions"}],
            "coderabbit_login": "coderabbitai[bot]", "auto_merge": False,
            "auto_merge_paths": ["docs/**/*.md"]}


def config(root):
    return {"state_dir": str(root), "dry_run": False, "auto_merge": False, "max_repairs": 3,
            "review_timeout_seconds": 3600, "poll_seconds": 60, "repositories": [repo_config()]}


class Agent:
    def __init__(self):
        self.calls = 0
        self.inputs = []
        self.path = "docs/glossary.md"

    def generate(self, issue, files, config, feedback):
        self.calls += 1
        self.inputs.append(copy.deepcopy(feedback))
        return {self.path: f"# Glossary\nPR: pull request. Revision {self.calls}.\n"}


class GitHub:
    def __init__(self):
        self.items = {1: issue()}
        self.files = {}
        self.base = "b" * 40
        self.head = None
        self.pr_number = None
        self.merged = False
        self.closed_pr = False
        self.published = 0
        self.pr_creates = 0
        self.merge_calls = 0
        self.close_calls = 0
        self.fail_publish_once = False
        self.fail_after_pr_once = False
        self.fail_close_once = False
        self.review = "PASS"
        self.bot_sha = None
        self.protected = True
        self.ci = "success"
        self.mergeable_state = "clean"
        self.mergeable = True
        self.extra_files = []

    def metadata(self, repo):
        return {"default_branch": "main", "private": False}

    def issues(self, repo):
        return copy.deepcopy(list(self.items.values()))

    def issue(self, repo, number):
        return copy.deepcopy(self.items[number])

    def branch_head(self, repo, branch):
        return self.base

    def snapshot(self, repo, sha, config):
        return dict(self.files), {k: "100644" for k in self.files}

    def make_commit(self, repo, parent, changes, date, issue):
        self.files.update(changes)
        return hashlib.sha1((parent + date + repr(changes)).encode()).hexdigest()

    def publish_ref(self, repo, branch, sha, previous=None):
        self.head = sha
        self.published += 1
        if self.fail_publish_once:
            self.fail_publish_once = False
            raise Blocked("Simulated lost response after ref write")

    def ensure_pr(self, repo, branch, base, issue):
        if self.pr_number is None:
            self.pr_number = 10
            self.pr_creates += 1
        if self.fail_after_pr_once:
            self.fail_after_pr_once = False
            raise Blocked("Simulated lost response after PR create")
        return self.pr_number

    def pr(self, repo, number):
        return {"number": number, "head": {"sha": self.head, "ref": "ado/issue-1", "repo": {"full_name": repo}},
                "base": {"sha": self.base, "ref": "main", "repo": {"full_name": repo}},
                "merged": self.merged, "state": "closed" if self.closed_pr or self.merged else "open",
                "draft": False, "mergeable": self.mergeable, "mergeable_state": self.mergeable_state}

    def evidence(self, repo, pr):
        bot = {"id": 1, "user": {"login": "coderabbitai[bot]", "type": "Bot"},
               "commit_id": self.bot_sha or self.head, "body": "Please fix the glossary",
               "state": "APPROVED" if self.review == "PASS" else "CHANGES_REQUESTED"}
        return {"checks": [{"id": 1, "name": "unit", "head_sha": self.head, "app": {"slug": "github-actions", "id": 15368},
                            "status": "completed", "conclusion": self.ci}],
                "reviews": [] if self.review == "MISSING" else [bot], "threads": []}

    def changed_files(self, repo, number):
        result = []
        for p, v in self.files.items():
            raw = v.encode()
            result.append({"filename": p, "status": "added", "sha": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()})
        return result + self.extra_files

    def assert_protection(self, *args):
        if not self.protected:
            raise Blocked("Protection missing")

    def merge(self, repo, number, sha):
        assert sha == self.head
        self.merge_calls += 1
        self.merged = True

    def close_issue(self, repo, number):
        if self.fail_close_once:
            self.fail_close_once = False
            raise Blocked("close timeout")
        self.close_calls += 1
        self.items[number]["state"] = "closed"
