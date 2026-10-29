import os
import re

import httpx
from dotenv import load_dotenv

load_dotenv()

API = "https://api.github.com"

# Accepts "owner/repo#12" or "https://github.com/owner/repo/pull/12"
PR_REF = re.compile(r"(?:https?://github\.com/)?([\w.-]+)/([\w.-]+)(?:#|/pull/)(\d+)")

# Generated or binary files: not worth reviewing, and they can be huge.
SKIP_SUFFIXES = (".lock", "package-lock.json", ".min.js", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".pdf")

# Roughly 5k tokens per reviewer call, which keeps one review under Groq's free-tier limit.
MAX_DIFF_CHARS = 20_000


# Turns "owner/repo#12" or a PR URL into ("owner", "repo", 12).
def parse_pr_ref(ref: str) -> tuple[str, str, int]:
    m = PR_REF.fullmatch(ref.strip().rstrip("/"))
    if not m:
        raise ValueError(f"Expected owner/repo#123 or a GitHub PR URL, got: {ref}")
    return m.group(1), m.group(2), int(m.group(3))


def _headers(accept: str = "application/vnd.github+json") -> dict:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is missing from .env")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
    }


# Asks GitHub for the PR as a plain unified diff (same format as your eval cases).
def get_pr_diff(owner: str, repo: str, number: int) -> str:
    resp = httpx.get(
        f"{API}/repos/{owner}/{repo}/pulls/{number}",
        # Allows us to extract a raw diff file, not a JSON. 
        headers=_headers("application/vnd.github.diff"),
        timeout=30,
        # It follows redirects (follow_redirects=True), for example when a repo has been renamed.
        follow_redirects=True,
    )
    resp.raise_for_status()
    return resp.text


# Drops generated/binary files from a GitHub diff e.g, lock files, minified JS and images. Returns (kept_diff, skipped_paths).  
# filter_diff splits the diff into files at each diff --git line and drops generated or binary files:
def filter_diff(diff: str) -> tuple[str, list[str]]:
    kept, skipped = [], []
    for chunk in re.split(r"(?m)^(?=diff --git )", diff):
        if not chunk.strip():
            continue
        first_line = chunk.splitlines()[0]
        path = first_line.rsplit(" b/", 1)[-1]
        if path.endswith(SKIP_SUFFIXES):
            skipped.append(path)
        else:
            kept.append(chunk)
    return "".join(kept), skipped


# ---------- Posting reviews ----------

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

# Hidden tag in every review body, so later we can recognise (and skip or update) our own reviews.
BOT_MARKER = "<!-- pr-reviewer -->"


# GitHub only accepts inline comments on lines that appear in the diff.
# Returns {path: {line numbers in the new file that are added or context lines}}.
def commentable_lines(diff: str) -> dict[str, set[int]]:
    lines: dict[str, set[int]] = {}
    path, new_line = None, None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path, new_line = None, None
        elif line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None   # "+++ /dev/null" = deleted file
        elif m := HUNK.match(line):
            new_line = int(m.group(1))
        elif path is None or new_line is None or line.startswith(("--- ", "\\")):
            continue
        elif line.startswith("-"):
            continue                                                 # removed lines don't exist in the new file
        else:
            lines.setdefault(path, set()).add(new_line)              # added (+) or context ( ) line
            new_line += 1
    return lines


def _format_finding(f) -> str:
    return f"**{f.severity.upper()}** · {f.reviewer}\n\n{f.message}"


# Builds the request body for GitHub's "create a review" endpoint.
# Findings on lines GitHub can attach to become inline comments; the rest go in the summary.
# Findings on a commentable line become inline comments, which appear next to the code. Others are listed in the summary instead of being lost
def build_review(findings, diff: str, footer: str = "") -> dict:
    allowed = commentable_lines(diff)
    inline, leftover = [], []
    for f in findings:
        if f.line in allowed.get(f.file, set()):
            inline.append({"path": f.file, "line": f.line, "side": "RIGHT", "body": _format_finding(f)})
        else:
            leftover.append(f)

    if findings:
        body = f"🤖 **Automated review:** {len(findings)} issue(s) found."
    else:
        body = "🤖 **Automated review:** no issues found."
    if leftover:
        body += "\n\nCould not attach these to a changed line:\n" + "\n".join(
            f"- `{f.file}:{f.line}` **{f.severity.upper()}** · {f.reviewer}: {f.message}" for f in leftover)
    if footer:
        body += f"\n\n<sub>{footer}</sub>"
    body += f"\n{BOT_MARKER}"

    # "COMMENT" only. The bot never approves or blocks a PR; a human decides.
    return {"event": "COMMENT", "body": body, "comments": inline}


# Posts the review to the PR and returns its URL.
def post_review(owner: str, repo: str, number: int, review: dict) -> str:
    resp = httpx.post(
        f"{API}/repos/{owner}/{repo}/pulls/{number}/reviews",
        headers=_headers(),
        json=review,
        timeout=30,
    )
    if resp.status_code == 422:
        # Usually an inline comment on a line GitHub won't accept. Don't lose the review: post it as summary only.
        fallback = {**review, "comments": []}
        fallback["body"] += "\n\n" + "\n".join(f"- `{c['path']}:{c['line']}` {c['body']}" for c in review["comments"])
        resp = httpx.post(f"{API}/repos/{owner}/{repo}/pulls/{number}/reviews",
                          headers=_headers(), json=fallback, timeout=30)
    resp.raise_for_status()
    return resp.json()["html_url"]