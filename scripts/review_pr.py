import argparse
import sys
from pathlib import Path

# Lets `python scripts/review_pr.py` find the app package, which lives one folder up.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service import review_pull_request
from app.tools.github import get_pr, parse_pr_ref

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser()
    parser.add_argument("pr", help="owner/repo#123 or a GitHub PR URL")
    parser.add_argument("--post", action="store_true", help="post the review to GitHub (default: just print it)")
    args = parser.parse_args()

    owner, repo, number = parse_pr_ref(args.pr)
    head_sha = get_pr(owner, repo, number)["head"]["sha"]

    out = review_pull_request(owner, repo, number, head_sha, post=args.post)
    if "result" not in out:
        sys.exit(f"Not reviewed: {out['status']}")

    result = out["result"]
    print(f"\nReview for {owner}/{repo}#{number} @ {head_sha[:7]}\n")
    print(result["final_review"])
    print(f"\nReviewers run: {result['plan']}")
    print(f"Raw findings: {len(result['findings'])}, tokens used: {result['tokens_used']}")

    if args.post:
        print(f"\nPosted: {out['url']}")
    else:
        print(f"\nDry run: {len(out['review']['comments'])} inline comment(s) ready. Add --post to publish.")