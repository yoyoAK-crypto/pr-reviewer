import sys, json, re
from pathlib import Path

from groq import Groq
from dotenv import load_dotenv
from pydantic import ValidationError
from langgraph.types import Send

from app.graph.state import ReviewState, Finding

load_dotenv()
client = Groq(max_retries=6)  # waits and retries on 429 instead of failing

# Folder holding security.txt, style.txt and tests.txt
PROMPTS = Path(__file__).parent.parent / "prompts"

# The exact JSON shape every reviewer must return.
# With strict mode, the model is forced to include every key with the right type,
# so "Field required" errors from missing keys should disappear.
FINDINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "message": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["file", "line", "severity", "message", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


# Prefixes every added and context line with its real line number in the new file,
# so the model reads line numbers instead of counting them from the @@ header.
def number_diff(diff: str) -> str:
    out, new_line = [], None
    for line in diff.splitlines():
        m = HUNK.match(line)
        if m:
            new_line = int(m.group(1))
            out.append(line)
            continue
        # file headers and "\ No newline" markers stay as they are
        if new_line is None or line.startswith(("--- a/", "--- /dev/null", "+++ b/", "\\")):
            out.append(line)
            continue
        if line.startswith("-"):
            out.append(f"{'':>5} {line}")          # removed lines have no new line number
        else:
            out.append(f"{new_line:>5} {line}")    # added or unchanged line
            new_line += 1
    return "\n".join(out)


# ---------- One reviewer call ----------

# Runs one reviewer (security, style or tests) on a diff.
# Returns the valid findings and how many tokens the call used.
def run_reviewer(name: str, diff: str) -> tuple[list[Finding], int]:
    system = (PROMPTS / f"{name}.txt").read_text(encoding="utf-8")

    resp = client.chat.completions.create(
        model="openai/gpt-oss-120b",  # check console.groq.com/docs/models for current IDs
        max_completion_tokens=4000,  # reasoning tokens count toward this limit
        temperature=0,  # same input gives (nearly) the same output, so eval scores are comparable
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": number_diff(diff)},
        ],
        response_format={"type": "json_object"},
    )

    # gpt-oss is a reasoning model: if it uses the whole token budget thinking,
    # it stops before writing the answer and content comes back empty.
    # finish_reason == "length" means it hit max_completion_tokens.
    choice = resp.choices[0]
    if choice.finish_reason == "length" or not choice.message.content:
        raise RuntimeError(f"{name} reviewer ran out of tokens before answering")

    # json.loads turns the JSON text into a Python dictionary.
    # Each finding in that dictionary becomes a Finding object, which is where Pydantic checks it.
    data = json.loads(choice.message.content)

    # Validate each finding on its own, so one malformed finding doesn't throw away the valid ones.
    # reviewer=name is set by our code, so the model can't mislabel it.
    # {**f, "reviewer": name} overwrites any "reviewer" key the model sent, instead of passing it twice.
    findings = []
    for f in data.get("findings", []):
        try:
            findings.append(Finding(**{**f, "reviewer": name}))
        except ValidationError as e:
            print(f"Skipped invalid {name} finding: {e}", file=sys.stderr)

    return findings, resp.usage.total_tokens



# Decides which reviewers to run, based on simple rules (no LLM yet).
def planner(state: ReviewState) -> dict:
    # Lines like "+++ b/app/auth.py" name the changed files; [6:] cuts off "+++ b/".
    files = [line[6:] for line in state["diff"].splitlines() if line.startswith("+++ b/")]

    # Docs or config only: style is the only reviewer worth running.
    # .txt is not in the list because prompt files are .txt and change how the reviewers behave.
    # `files and` stops an empty list from counting as docs-only, since all([]) is True.
    docs_only = bool(files) and all(f.endswith((".md", ".json", ".yml", ".yaml", ".toml")) for f in files)
    plan = ["style"] if docs_only else ["security", "style", "tests"]

    return {"changed_files": files, "plan": plan}


# Called by LangGraph after the planner. Returns one Send per reviewer,
# which tells LangGraph to run the "reviewer" node once for each, in parallel.
def fan_out(state: ReviewState) -> list[Send]:
    return [Send("reviewer", {"name": name, "diff": state["diff"]}) for name in state["plan"]]


# One reviewer run. Receives the small dict from Send, not the full state.
# Returns findings and tokens; operator.add in ReviewState merges them across parallel runs.
def reviewer(payload: dict) -> dict:
    findings, tokens = run_reviewer(payload["name"], payload["diff"])
    return {"findings": findings, "tokens_used": tokens}


# Cleans up all findings into one review (plain rules, no LLM yet).
def synthesizer(state: ReviewState) -> dict:
    # Drop weak findings, unless they're high severity.
    # Low-severity comments are nitpicks; keep them out of the main review.
    # (Confidence alone can't filter them, the model rates its nits 0.9+.)
    kept = [f for f in state["findings"]
            if f.severity != "low" and (f.confidence >= 0.6 or f.severity == "high")]

    # Merge duplicates: same file and same line, keep the most confident.
    # Only exact lines count, so two different bugs a few lines apart are both kept.
    kept.sort(key=lambda f: f.confidence, reverse=True)
    unique = []
    for f in kept:
        if not any(u.file == f.file and u.line == f.line for u in unique):
            unique.append(f)

    # Rank by severity, then confidence, and cap at 7 comments.
    order = {"high": 0, "medium": 1, "low": 2}
    unique.sort(key=lambda f: (order[f.severity], -f.confidence))
    top = unique[:7]

    lines = [f"[{f.severity}] {f.file}:{f.line} ({f.reviewer}) {f.message}" for f in top]
    return {"final_review": "\n".join(lines) or "No issues found.", "final_findings": top}