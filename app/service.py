# Runs the pipeline in one function: duplicate check → fetch diff → reviewers → build review → post. Both the script and the server call it.

import logging
import time

from app.graph.build import build_graph
from app.tools.github import (MAX_DIFF_CHARS, already_reviewed, build_review, download_repo,
                              filter_diff, get_pr_diff, post_review)
from app.tools.retrieval import MODE as RETRIEVAL_MODE
from app.guardrails.redact import redact
from app.observability import record_review

logger = logging.getLogger(__name__)

# Build the graph once and reuse it for every review.
graph = build_graph()


# The whole pipeline for one PR: fetch diff -> run graph -> build review -> (optionally) post.
# Used by both the command-line script and the webhook server, so they always behave the same.
# Returns a short status string describing what happened.
# Every call, whatever happens (posted, skipped, or an error), is recorded once for /stats and the logs.
def review_pull_request(owner: str, repo: str, number: int, head_sha: str, post: bool = True) -> dict:
    pr_id = f"{owner}/{repo}#{number}"
    event = {"pr": pr_id, "sha": head_sha[:7], "retrieval": RETRIEVAL_MODE, "post": post}
    start = time.perf_counter()
    try:
        out = _review(owner, repo, number, head_sha, post, pr_id, event)
        event["status"] = out["status"]
        return out
    except Exception as e:
        event["status"] = "error"
        event["error"] = type(e).__name__     # the type only: messages can contain request details
        raise
    finally:
        event["seconds"] = round(time.perf_counter() - start, 2)
        record_review(event)


def _review(owner: str, repo: str, number: int, head_sha: str, post: bool, pr_id: str, event: dict) -> dict:

    if post and already_reviewed(owner, repo, number, head_sha):
        logger.info("%s @ %s already reviewed, skipping", pr_id, head_sha[:7])
        return {"status": "skipped_already_reviewed"}

    diff, skipped = filter_diff(get_pr_diff(owner, repo, number))
    if skipped:
        logger.info("%s: skipped generated/binary files %s", pr_id, skipped)
    if not diff.strip():
        return {"status": "skipped_empty_diff"}
    # Big PRs would blow past the token limit. Splitting them up is a week 4 job.
    if len(diff) > MAX_DIFF_CHARS:
        logger.warning("%s: diff is %d chars, over the limit", pr_id, len(diff))
        return {"status": "skipped_too_large", "chars": len(diff)}

    # Retrieval needs the repo's code at this commit. If the download fails, review without it.
    repo_root = None
    if RETRIEVAL_MODE != "off":
        try:
            repo_root = str(download_repo(owner, repo, head_sha))
        except Exception:
            logger.exception("%s: could not download repo, reviewing without context", pr_id)

    # Redact before the graph too: the graph's input is what LangSmith records in traces.
    # (The guard node redacts again, harmlessly, and turns the markers into findings.)
    safe_diff, secrets = redact(diff)
    if secrets:
        logger.warning("%s: redacted %d secret(s): %s", pr_id, len(secrets), sorted({s.kind for s in secrets}))

    # run_name, tags and metadata label this run in LangSmith, so the dashboard can be filtered
    # by repo, commit or retrieval mode instead of scrolling through anonymous traces.
    result = graph.invoke(
        {"pr_id": pr_id, "diff": safe_diff, "repo_root": repo_root, "findings": [], "tokens_used": 0},
        config={"run_name": f"review {pr_id}",
                "tags": [f"retrieval:{RETRIEVAL_MODE}", "post" if post else "dry-run"],
                "metadata": {"repo": f"{owner}/{repo}", "pr": number, "sha": head_sha}},
    )
    final = result["final_findings"]
    event.update(
        tokens=result["tokens_used"],
        findings=len(final),
        secrets_redacted=len(secrets),
        injections_flagged=sum(f.reviewer == "guardrail" and "prompt injection" in f.message.lower() for f in final),
        context_chunks=len(result.get("context_ids") or []),
    )
    footer = f"Reviewers: {', '.join(result['plan'])} · {result['tokens_used']:,} tokens · commit {head_sha[:7]}"
    review = build_review(result["final_findings"], diff, head_sha, footer)

    event["inline_comments"] = len(review["comments"])
    out = {"status": "dry_run", "result": result, "review": review}
    if post:
        out["status"] = "posted"
        out["url"] = post_review(owner, repo, number, review)
        logger.info("%s: posted %d inline comment(s) -> %s", pr_id, len(review["comments"]), out["url"])
    return out