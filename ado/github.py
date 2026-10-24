"""GitHub REST/GraphQL transport; no target checkout or target command execution."""
import base64
import json
import os
from .policy import Blocked, inspect_source, matches, safe_path
from .process import run

TIMEOUT_SECONDS = 45
# Generous next to the MiniMax budget: paginated evidence is legitimately large, and the
# point here is to bound memory rather than to second-guess GitHub's page sizes.
OUTPUT_LIMIT = 8_000_000


class GitHub:
    def __init__(self, dry_run=True, pulse=None):
        self.dry_run = dry_run
        # Optional liveness callback, called while a request is in flight. Without it a
        # slow request outlives the dashboard's online window and shows as offline.
        self.pulse = pulse
        if not dry_run and not os.environ.get("GH_TOKEN"):
            raise Blocked("Live mode requires an explicit repository-scoped GH_TOKEN")

    def api(self, endpoint, method="GET", data=None):
        if self.dry_run and method != "GET":
            raise Blocked("dry-run forbids GitHub mutations")
        env = {k: os.environ[k] for k in ("PATH", "HOME", "GH_TOKEN") if k in os.environ}
        env.update(GH_HOST="github.com", GH_PROMPT_DISABLED="1", GH_PAGER="cat")
        args = ["gh", "api", "--hostname", "github.com", "-X", method,
                "-H", "Accept: application/vnd.github+json", endpoint]
        if data is not None:
            args += ["--input", "-"]
        payload = json.dumps(data).encode("utf-8") if data is not None else b""
        try:
            stdout, _ = run(args, TIMEOUT_SECONDS, payload=payload, limit=OUTPUT_LIMIT,
                            label="GitHub request",
                            # Deliberately omit CLI stderr / remote response (may contain secrets).
                            failure="GitHub request failed: " + method + " " + endpoint.split("?")[0],
                            env=env, pulse=self.pulse)
            return json.loads(stdout.decode("utf-8")) if stdout.strip() else None
        except ValueError as exc:
            raise Blocked("GitHub request returned unparsable JSON: "
                          + method + " " + endpoint.split("?")[0]) from exc

    def pages(self, endpoint, key=None):
        output = []
        for page in range(1, 21):
            sep = "&" if "?" in endpoint else "?"
            data = self.api(f"{endpoint}{sep}per_page=100&page={page}")
            items = data[key] if key else data
            output.extend(items)
            if len(items) < 100:
                if key and data.get("total_count", len(output)) > len(output):
                    raise Blocked("API returned incomplete evidence despite pagination")
                return output
        raise Blocked("Pagination limit exceeded; refusing incomplete evidence")

    def metadata(self, repo):
        data = self.api(f"repos/{repo}")
        if data["private"] or data.get("archived") or data.get("disabled"):
            raise Blocked("MVP operates only on active PUBLIC repositories (free hosted CI)")
        return data

    def issues(self, repo):
        # Label is filtered locally to keep endpoints fixed and avoid query injection.
        return [x for x in self.pages(f"repos/{repo}/issues?state=open&sort=created&direction=asc")
                if "pull_request" not in x]

    def issue(self, repo, number):
        return self.api(f"repos/{repo}/issues/{int(number)}")

    def branch_head(self, repo, branch):
        from urllib.parse import quote
        data = self.api(f"repos/{repo}/git/ref/heads/{quote(branch, safe='')}")
        return data["object"]["sha"]

    def snapshot(self, repo, sha, config):
        tree = self.api(f"repos/{repo}/git/trees/{sha}?recursive=1")
        if tree.get("truncated"):
            raise Blocked("Repository tree is truncated")
        files, modes, total = {}, {}, 0
        for item in tree["tree"]:
            path = item["path"]
            modes[path] = item["mode"]
            wanted = matches(path, config["allowed_paths"]) or path in config["context_paths"]
            if not wanted or item["type"] != "blob":
                continue
            try:
                safe_path(path)
            except Blocked:
                continue
            if item["mode"] != "100644":
                continue
            if item.get("size", 0) > 50000:
                raise Blocked("Context file too large: " + path)
            blob = self.api(f"repos/{repo}/git/blobs/{item['sha']}")
            try:
                content = base64.b64decode(blob["content"], validate=False).decode("utf-8")
            except (ValueError, UnicodeError) as exc:
                raise Blocked("Non-UTF8 context") from exc
            inspect_source(content)
            total += len(content.encode())
            if total > config.get("max_context_bytes", 150000):
                raise Blocked("Context budget exceeded")
            files[path] = content
        return files, modes

    def make_commit(self, repo, base, changes, date, issue):
        commit = self.api(f"repos/{repo}/git/commits/{base}")
        entries = []
        for path, content in sorted(changes.items()):
            blob = self.api(f"repos/{repo}/git/blobs", "POST", {"content": content, "encoding": "utf-8"})
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = self.api(f"repos/{repo}/git/trees", "POST", {"base_tree": commit["tree"]["sha"], "tree": entries})
        identity = {"name": "ADO local worker", "email": "ado@users.noreply.github.com", "date": date}
        result = self.api(f"repos/{repo}/git/commits", "POST", {
            "message": f"Implement issue #{issue}", "tree": tree["sha"], "parents": [base],
            "author": identity, "committer": identity})
        return result["sha"]

    def publish_ref(self, repo, branch, sha, previous=None):
        # Enumerate exact matching refs. Missing ref is distinct from transport failure.
        from urllib.parse import quote
        refs = self.api(f"repos/{repo}/git/matching-refs/heads/{quote(branch, safe='')}")
        exact = [r for r in refs if r["ref"] == "refs/heads/" + branch]
        if exact and exact[0]["object"]["sha"] == sha:
            return
        if exact:
            if previous is None or exact[0]["object"]["sha"] != previous:
                raise Blocked("Branch ownership/head conflict")
            self.api(f"repos/{repo}/git/refs/heads/{quote(branch, safe='')}", "PATCH", {"sha": sha, "force": False})
        else:
            if previous is not None:
                raise Blocked("Previously published branch disappeared")
            self.api(f"repos/{repo}/git/refs", "POST", {"ref": "refs/heads/" + branch, "sha": sha})

    def ensure_pr(self, repo, branch, base, issue):
        prs = self.pages(f"repos/{repo}/pulls?state=all")
        same = [p for p in prs if p["head"]["ref"] == branch and
                p["head"]["repo"] and p["head"]["repo"]["full_name"] == repo]
        if same:
            if len(same) != 1 or same[0]["state"] != "open" or same[0]["base"]["ref"] != base:
                raise Blocked("Existing PR conflicts with this job")
            return same[0]["number"]
        # Do not trust or interpolate issue/agent titles, bodies or closing keywords.
        result = self.api(f"repos/{repo}/pulls", "POST", {
            "title": f"ADO: implement issue #{issue}", "head": branch, "base": base,
            "body": f"Local ADO job for #{issue}.\n\nGenerated changes require CI and review at the current head SHA.\n"
                    "Auto-merge is disabled by default. Issue closure is controlled by the worker after merge."})
        return result["number"]

    def pr(self, repo, number):
        return self.api(f"repos/{repo}/pulls/{number}")

    def evidence(self, repo, pr):
        sha = pr["head"]["sha"]
        checks = self.pages(f"repos/{repo}/commits/{sha}/check-runs", "check_runs")
        reviews = self.pages(f"repos/{repo}/pulls/{pr['number']}/reviews")
        # GraphQL unresolved review threads, all pages, including replies/outdated info.
        owner, name = repo.split("/")
        query = """query($owner:String!,$name:String!,$number:Int!,$cursor:String){
          repository(owner:$owner,name:$name){pullRequest(number:$number){
            reviewThreads(first:100,after:$cursor){pageInfo{hasNextPage endCursor}
              nodes{id isResolved isOutdated comments(first:1){nodes{body commit{oid}}}}}
          }}}"""
        threads, cursor = [], None
        for _ in range(20):
            response = self.api("graphql", "POST", {"query": query, "variables": {
                "owner": owner, "name": name, "number": pr["number"], "cursor": cursor}})
            if response.get("errors"):
                raise Blocked("Incomplete review-thread evidence")
            page = response["data"]["repository"]["pullRequest"]["reviewThreads"]
            threads.extend(page["nodes"])
            if not page["pageInfo"]["hasNextPage"]:
                return {"checks": checks, "reviews": reviews, "threads": threads}
            cursor = page["pageInfo"]["endCursor"]
        raise Blocked("Review thread pagination exceeded")

    def changed_files(self, repo, number):
        return self.pages(f"repos/{repo}/pulls/{number}/files")

    def merge(self, repo, number, sha):
        response = self.api(f"repos/{repo}/pulls/{number}/merge", "PUT", {"sha": sha, "merge_method": "squash"})
        if not response.get("merged"):
            raise Blocked("GitHub refused SHA-conditional merge")

    def close_issue(self, repo, number):
        self.api(f"repos/{repo}/issues/{number}", "PATCH", {"state": "closed", "state_reason": "completed"})

    def assert_protection(self, repo, branch, config, evidence):
        from urllib.parse import quote
        protection = self.api(f"repos/{repo}/branches/{quote(branch, safe='')}/protection")
        status = protection.get("required_status_checks") or {}
        review = protection.get("required_pull_request_reviews") or {}
        if not status.get("strict") or not protection.get("enforce_admins", {}).get("enabled") or \
           not review.get("dismiss_stale_reviews") or review.get("required_approving_review_count", 0) < 1 or \
           protection.get("allow_force_pushes", {}).get("enabled") or \
           protection.get("allow_deletions", {}).get("enabled"):
            raise Blocked("Auto-merge requires strict CI, stale-review dismissal and admin-enforced branch protection")
        bound = {(c["context"], c.get("app_id")) for c in status.get("checks", [])}
        for required in config["required_checks"]:
            current = [c for c in evidence["checks"] if c["name"] == required["name"] and
                       c.get("app", {}).get("slug") == required["app"]]
            if not current or (required["name"], current[0]["app"]["id"]) not in bound:
                raise Blocked("Branch protection must require CI bound to the GitHub Actions app")
