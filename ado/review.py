"""SHA-bound review evidence. Free-text praise never grants approval."""
from .policy import Blocked, redact


def judge(pr, evidence, config):
    sha = pr["head"]["sha"]
    checks = evidence["checks"]
    feedback, waiting = [], []
    for required in config["required_checks"]:
        relevant = [c for c in checks if c["name"] == required["name"] and
                    c.get("app", {}).get("slug") == required["app"] and c.get("head_sha") == sha]
        if not relevant:
            waiting.append("missing CI " + required["name"])
            continue
        check = max(relevant, key=lambda c: c["id"])
        if check.get("status") != "completed":
            waiting.append("running CI " + required["name"])
        elif check.get("conclusion") != "success":
            feedback.append({"kind": "ci", "name": required["name"],
                             "conclusion": check.get("conclusion"),
                             "summary": redact((check.get("output") or {}).get("summary") or "")[:8000]})
    latest, verdicts = {}, {}
    for review in sorted(evidence["reviews"], key=lambda r: r["id"]):
        latest[review["user"]["login"]] = review
        if review.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            verdicts[review["user"]["login"]] = review
    bot = latest.get(config["coderabbit_login"])
    if not bot or bot.get("commit_id") != sha or bot.get("user", {}).get("type") != "Bot":
        waiting.append("missing CodeRabbit review at current SHA")
    elif bot["state"] == "CHANGES_REQUESTED":
        feedback.append({"kind": "review", "body": redact(bot.get("body") or "")[:8000]})
    elif bot["state"] != "APPROVED":
        waiting.append("CodeRabbit has not APPROVED current SHA")
    for review in verdicts.values():
        if review.get("state") == "CHANGES_REQUESTED":
            # Outstanding changes-requested remains a veto even from an earlier commit.
            if review.get("commit_id") != sha:
                waiting.append("stale changes-requested review requires dismissal/re-review")
            elif review is not bot:
                feedback.append({"kind": "review", "body": redact(review.get("body") or "")[:8000]})
    for thread in evidence["threads"]:
        if thread["isResolved"]:
            continue
        comments = thread["comments"]["nodes"]
        if not thread["isOutdated"] and comments and comments[0].get("commit", {}).get("oid") == sha:
            feedback.append({"kind": "thread", "body": redact(comments[0]["body"])[:8000]})
        else:
            waiting.append("unresolved old review thread")
    # Never repair while a required check is still running/missing: avoid burning retries.
    if any(x.startswith(("missing CI", "running CI")) for x in waiting):
        return "WAIT", waiting
    if feedback:
        return "REPAIR", feedback
    if waiting:
        return "WAIT", waiting
    if pr.get("draft") or pr.get("mergeable") is not True or pr.get("mergeable_state") != "clean":
        return "WAIT", ["PR is draft, conflicted, behind or mergeability is unknown"]
    return "PASS", []
