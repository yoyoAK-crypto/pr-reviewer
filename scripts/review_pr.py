import argparse
import sys
from pathlib import Path

# Lets `python scripts/review_pr.py` find the app package, which lives one folder up.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.graph.build import build_graph
from app.tools.github import (MAX_DIFF_CHARS, build_review, filter_diff, get_pr_diff,
                              parse_pr_ref, post_review)

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser()
    parser.add_argument("pr", help="owner/repo#123 or a GitHub PR URL")
    parser.add_argument("--post", action="store_true", help="post the review to GitHub (default: just print it)")
    args = parser.parse_args()

    owner, repo, number = parse_pr_ref(args.pr)
    diff, skipped = filter_diff(get_pr_diff(owner, repo, number))

    if skipped:
        print(f"Skipped {len(skipped)} generated/binary files: {', '.join(skipped)}")
    if not diff.strip():
        sys.exit("Nothing to review after filtering.")
    # Big PRs would blow past the token limit. Splitting them up is a week 4 job.
    if len(diff) > MAX_DIFF_CHARS:
        sys.exit(f"Diff is {len(diff):,} characters, over the {MAX_DIFF_CHARS:,} limit. Try a smaller PR.")

    pr_id = f"{owner}/{repo}#{number}"
    result = build_graph().invoke({"pr_id": pr_id, "diff": diff, "findings": [], "tokens_used": 0})

    print(f"\nReview for {pr_id}\n")
    print(result["final_review"])
    print(f"\nReviewers run: {result['plan']}")
    print(f"Raw findings: {len(result['findings'])}, tokens used: {result['tokens_used']}")

    footer = f"Reviewers: {', '.join(result['plan'])} · {result['tokens_used']:,} tokens"
    review = build_review(result["final_findings"], diff, footer)

    if args.post:
        url = post_review(owner, repo, number, review)
        print(f"\nPosted: {url}")
    else:
        print(f"\nDry run: {len(review['comments'])} inline comment(s) ready. Add --post to publish.")