# Runs the pipeline in one function: duplicate check → fetch diff → reviewers → build review → post. Both the script and the server call it.

import logging

from app.graph.build import build_graph
from app.tools.github import (MAX_DIFF_CHARS, already_reviewed, build_review, filter_diff,
                              get_pr_diff, post_review)

logger = logging.getLogger(__name__)

# Build the graph once and reuse it for every review.
graph = build_graph()


# The whole pipeline for one PR: fetch diff -> run graph -> build review -> (optionally) post.
# Used by both the command-line script and the webhook server, so they always behave the same.
# Returns a short status string describing what happened.
def review_pull_request(owner: str, repo: str, number: int, head_sha: str, post: bool = True) -> dict:
    pr_id = f"{owner}/{repo}#{number}"

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

    result = graph.invoke({"pr_id": pr_id, "diff": diff, "findings": [], "tokens_used": 0})
    footer = f"Reviewers: {', '.join(result['plan'])} · {result['tokens_used']:,} tokens · commit {head_sha[:7]}"
    review = build_review(result["final_findings"], diff, head_sha, footer)

    out = {"status": "dry_run", "result": result, "review": review}
    if post:
        out["status"] = "posted"
        out["url"] = post_review(owner, repo, number, review)
        logger.info("%s: posted %d inline comment(s) -> %s", pr_id, len(review["comments"]), out["url"])
    return out