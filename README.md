# pr-reviewer

An AI code reviewer for GitHub pull requests. Three reviewers (security, style, tests) read a PR's diff in parallel, and a rule-based synthesizer turns their findings into one short review.

## Architecture

There are three ways in. All of them run the same pipeline, so a review behaves the same however it was started.

```mermaid
flowchart TD
    subgraph entry["Three ways in"]
        S["Script<br/>scripts/review_pr.py"]
        W["Webhook<br/>app/main.py"]
        M["MCP tools<br/>app/mcp_server.py"]
    end

    GH(["GitHub PR event"]) -->|"signed message"| W
    A(["AI assistant"]) -->|"tool call"| M
    U(["You, in a terminal"]) --> S

    S --> SVC
    W --> SVC
    M --> SVC

    subgraph pipeline["Shared pipeline: app/service.py"]
        SVC["Skip if this commit was<br/>already reviewed"] --> D["Fetch and filter the diff"]
        D --> P["Planner<br/>docs-only PRs get style only"]
        P --> R1["Security reviewer"]
        P --> R2["Style reviewer"]
        P --> R3["Tests reviewer"]
        R1 --> Y["Synthesizer<br/>filter, dedupe, rank, cap at 7"]
        R2 --> Y
        R3 --> Y
        Y --> B["Build the review"]
    end

    B -->|"optional"| POST(["Comments on the PR"])
```

The script and MCP tools only post when asked (`--post`, `post=true`). The webhook always posts. The MCP `review_diff` tool takes diff text directly, so it starts at the planner and never posts.

The bot only ever comments. It never approves or blocks a PR.

## Setup

Requires Python 3.10+.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env
```

Fill in `.env`. Edit it in a text editor, since PowerShell's `>` can save it in an encoding the app can't read.

- `GROQ_API_KEY`: from the [Groq console](https://console.groq.com/keys).
- `GITHUB_TOKEN`: a fine-grained token with **Contents: Read-only** and **Pull requests: Read and write**.
- `GITHUB_WEBHOOK_SECRET`: a long random string, only needed for the webhook.

## How to run it

### Script

```powershell
python scripts/review_pr.py "owner/repo#123"          # prints the review, posts nothing
python scripts/review_pr.py "owner/repo#123" --post   # posts it to the PR
python scripts/review_local.py path\to\change.diff    # reviews a diff file, no GitHub needed
```

### Webhook

Reviews every PR automatically when it's opened or updated. For local development, use two terminals and keep both open:

```powershell
python -m uvicorn app.main:app --port 8000
npx smee-client --url https://smee.io/YOUR-ID --target http://127.0.0.1:8000/webhook
```

Get `YOUR-ID` from [smee.io/new](https://smee.io/new). Then in the repo, go to **Settings → Webhooks → Add webhook**:

- **Payload URL:** your smee URL
- **Content type:** `application/json`
- **Secret:** the same value as `GITHUB_WEBHOOK_SECRET`
- **Events:** only **Pull requests**

### MCP

Lets an AI assistant call the reviewer as a tool. Add this to your client's config, for example `claude_desktop_config.json`, using absolute paths:

```json
{
  "mcpServers": {
    "pr-reviewer": {
      "command": "C:\\path\\to\\pr-reviewer\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\pr-reviewer\\app\\mcp_server.py"]
    }
  }
}
```

Restart the client, then ask it something like "Use pr-reviewer to review owner/repo#123. Don't post anything."

- `review_diff`: reviews diff text and posts nothing.
- `review_pull_request`: reviews a PR. It's a dry run unless `post=true`.

## Tests and evals

```powershell
python -m pytest                                   # webhook tests: no network, no tokens
python evals/run_evals.py "my note"                # hard set, tune split
python evals/run_evals.py "my note" --split holdout  # run only to confirm a final version
```

The tests send signed fake GitHub messages to the real webhook code. They check that signatures are enforced, irrelevant events are ignored, and retries don't post twice.

## Evaluation

31 hand-built cases (21 planted bugs: 10 security, 11 correctness; 10 clean PRs, several designed to look dangerous but be safe), split into 22 tune / 9 holdout. A catch counts only if the comment is at the right line (±2) and names the right issue.

| Version | Change | Recall | Precision | Clean pass | Tokens/PR |
|---|---|---|---|---|---|
| v3 (holdout) | 3 reviewers + rule-based synthesizer | 66.7% | 31.2% | 0% | 2.6k |
| v4 (tune) | Line-numbered diffs | 93.3% | 55.2% | 42.9% | 3.2k |
| v5 (tune) | Severity rubric, diff-only scope, low-severity filter | 100% | 65 to 71% | 57 to 71% | 3.4k |
| v5 (holdout) | Final check | 100% | 60.0% | 66.7% | 3.0k |

What mattered:
- **Most "misses" were line-number errors, not reasoning errors.** The model found the bugs but counted lines wrong from the @@ header. Prefixing each line with its real number fixed it.
- **Model confidence was useless for filtering.** Nitpicks were rated 0.85 to 0.95 confidence. An explicit severity rubric worked instead.
- **Runs vary.** With temperature 0, the clean pass rate still moved 57 to 71% between identical runs, so numbers are reported as ranges.
- **Eval labels had bugs too.** Two "misses" were label errors, and one "false alarm" was a valid comment.

Note: the holdout split was viewed once (v3) before the final fixes; those fixes were general (line numbering, severity rules), not case-specific.

These numbers are from v5. Later prompt and output-schema changes have not been re-evaluated yet.
