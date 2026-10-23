import sys, json
from groq import Groq
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError
from typing import Literal

load_dotenv()
client = Groq()

# This defines what one review comment must look like. Pydantic checks every finding the model returns.
class Finding(BaseModel):
    file: str
    line: int
    severity: Literal["low", "medium", "high"]
    message: str
    confidence: float = Field(ge=0, le=1)

SYSTEM = """You are a security code reviewer. Review ONLY the added lines in the diff.
Return a JSON object {"findings": [...]} where each finding has keys: file, line, severity, message, confidence (0 to 1).
Return {"findings": []} if there are no real issues. Never claim a bug you cannot point to in the diff."""

# The function takes one input called 'diff' which should be a string
# '-> list[Finding]' States that the function should return a list of Finding objects.
# Finding is the Pydantic class defined at the top of the file, so each item has file, line, severity, message and confidence.

def review(diff: str) -> list[Finding]:
    resp = client.chat.completions.create(
        model="openai/gpt-oss-120b",  # check console.groq.com/docs/models for current IDs
        max_completion_tokens=4000,  # reasoning tokens count toward this limit
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": diff},
        ],
        response_format={"type": "json_object"},
    )

    # gpt-oss is a reasoning model: if it uses the whole token budget thinking,
    # it stops before writing the answer and content comes back empty.
    # finish_reason == "length" is how Groq reports that the reply hit the limit. 
    choice = resp.choices[0]
    if choice.finish_reason == "length" or not choice.message.content:
        raise RuntimeError("Model ran out of tokens before answering")

    text = choice.message.content
    # json.loads reads a string written in JSON format and builds the matching Python objects from it.
    # After parsing, you get a real dictionary you can index into: e.g, data["findings"] gives you... [{'file': 'app/users.py', 'line': 3, 'confidence': 1.0}]
    data = json.loads(text)

    # Validate each finding on its own, so one malformed finding doesn't throw away the valid ones.
    # .get("findings", []) returns an empty list instead of crashing if the key is missing.
    findings = []
    for f in data.get("findings", []):
        try:
            findings.append(Finding(**f))
            # If one filed doesn't match oydantic validation, don't crash all findings, rather send a message to terminal that finding was skipped. 
        except ValidationError as e:
            # Printed to stderr so it shows in the terminal but stays separate from the real output.
            print(f"Skipped invalid finding: {e}", file=sys.stderr)
    return findings

if __name__ == "__main__":
    # Windows defaults to cp1252 when output is piped, which crashes on characters like '‑' in model messages.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    diff = open(sys.argv[1], encoding="utf-8").read()
    for f in review(diff):
        print(f"[{f.severity}] {f.file}:{f.line} ({f.confidence}) {f.message}")