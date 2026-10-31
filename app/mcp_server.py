import logging
import sys
from pathlib import Path

# Claude Desktop starts this file from its own folder, not yours, so make the
# project importable and load .env from the project root explicitly.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import groq
import httpx
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from app.service import graph, review_pull_request as run_pr_review
from app.tools.github import MAX_DIFF_CHARS, get_pr, parse_pr_ref

# MCP over stdio uses stdout for its messages. Anything else printed there corrupts
# the connection, so all logging goes to stderr.
logging.basicConfig(stream=sys.stderr, level=logging.INFO)

mcp = MCPServer(
    "pr-reviewer",
    instructions=(
        "Multi-agent code reviewer (security, style, tests). "
        "Use review_diff for local changes, e.g. the output of `git diff`. "
        "Use review_pull_request for a GitHub PR. Only set post=true when the user asks to publish. "
        "Present `findings` as the review. `filtered_out` was removed as noise and never gets posted: "
        "mention it only as background, not as issues to fix."
    ),
)


# Turns graph output into plain JSON the assistant can read.
def _summarize(result: dict) -> dict:
    return {
        "findings": [f.model_dump() for f in result["final_findings"]],
        # What the reviewers said but the synthesizer removed, so the assistant can see every reviewer's output.
        "filtered_out": [f.model_dump() for f in result["findings"] if f not in result["final_findings"]],
        "filter_rules": "Removed: low severity; confidence under 0.6 unless high severity; "
                        "a second finding on the same file and line; anything past the top 7.",
        "reviewers_run": result["plan"],
        "raw_findings": len(result["findings"]),
        "tokens_used": result["tokens_used"],
    }


# Without this, the assistant only sees "Error executing tool" and has to guess the cause.
# Returns a message saying what actually failed. The token is never part of these messages.
def _describe_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        hints = {401: "GITHUB_TOKEN is invalid or expired",
                 403: "GITHUB_TOKEN lacks permission for this action, or GitHub's rate limit was hit",
                 404: "the PR doesn't exist, or GITHUB_TOKEN can't access this repo"}
        return f"GitHub returned {code}: {hints.get(code, 'request failed')}."
    if isinstance(e, httpx.TransportError):
        return f"Could not reach GitHub ({type(e).__name__}). Check the internet connection and retry."
    if isinstance(e, groq.APIConnectionError):
        return f"Could not reach Groq ({type(e).__name__}). Check the internet connection and retry."
    if isinstance(e, groq.RateLimitError):
        return "Groq's rate limit was hit. Wait a minute and retry."
    if isinstance(e, groq.APIStatusError):
        return f"Groq returned {e.status_code}. Check GROQ_API_KEY if this is 401."
    if isinstance(e, (ValueError, RuntimeError)):
        return str(e)  # our own messages: bad PR reference, missing token, model ran out of tokens
    return f"Unexpected {type(e).__name__}. See the server log for details."


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False)) # The @ marks the function as a tool. 
def review_diff(diff: str) -> dict:
    """Review a unified diff (e.g. output of `git diff`) for security bugs, correctness
    and style problems, and missing tests. Returns findings with file, line, severity,
    message and confidence. Nothing is posted anywhere.""" # Description of what the tool does, so it's effectively written for the AI.
    if not diff.strip():
        return {"error": "empty diff"}
    if len(diff) > MAX_DIFF_CHARS:
        return {"error": f"diff is {len(diff):,} characters; limit is {MAX_DIFF_CHARS:,}. Review fewer files."}
    try:
        result = graph.invoke({"pr_id": "mcp-local", "diff": diff, "findings": [], "tokens_used": 0})
    except Exception as e:
        logging.exception("review_diff failed")
        return {"error": _describe_error(e)}
    return _summarize(result)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True))
def review_pull_request(pr: str, post: bool = False) -> dict:
    """Review a GitHub pull request, given as owner/repo#123 or a PR URL.
    By default this is a dry run that only returns the findings. Set post=true to
    publish the review as comments on the PR; only do this if the user asked.""" # Description of what the tool does, so it's effectively written for the AI.
    try:
        owner, repo, number = parse_pr_ref(pr)
        head_sha = get_pr(owner, repo, number)["head"]["sha"]
        out = run_pr_review(owner, repo, number, head_sha, post=post)
    except Exception as e:
        logging.exception("review_pull_request failed for %s", pr)
        return {"error": _describe_error(e)}
    if "result" not in out:
        return {"status": out["status"]}
    return {"status": out["status"], "commit": head_sha[:7], "url": out.get("url"), **_summarize(out["result"])}


if __name__ == "__main__":
    mcp.run()  # stdio: the client starts this process and talks to it over stdin/stdout