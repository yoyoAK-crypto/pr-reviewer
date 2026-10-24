import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq
from pydantic import ValidationError

from app.graph.state import Finding

load_dotenv()
client = Groq()

# Prompts live in app/prompts/, one file per reviewer.
PROMPTS = Path(__file__).parent.parent / "prompts"

# The function takes the reviewer's name (which picks the prompt file) and the diff, both strings
# '-> list[Finding]' States that the function should return a list of Finding objects.
# Finding is the Pydantic class defined in state.py, so each item has reviewer, file, line, severity, message and confidence.

def run_reviewer(name: str, diff: str) -> list[Finding]:
    system = (PROMPTS / f"{name}.txt").read_text(encoding="utf-8")
    resp = client.chat.completions.create(
        model="openai/gpt-oss-120b",  # check console.groq.com/docs/models for current IDs
        max_completion_tokens=4000,  # reasoning tokens count toward this limit
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": diff},
        ],
        response_format={"type": "json_object"},
    )

    # gpt-oss is a reasoning model: if it uses the whole token budget thinking,
    # it stops before writing the answer and content comes back empty.
    # finish_reason == "length" is how Groq reports that the reply hit the limit.
    choice = resp.choices[0]
    if choice.finish_reason == "length" or not choice.message.content:
        raise RuntimeError(f"{name} reviewer ran out of tokens before answering")

    # json.loads reads a string written in JSON format and builds the matching Python objects from it.
    # After parsing, you get a real dictionary you can index into: e.g, data["findings"] gives you... [{'file': 'app/users.py', 'line': 3, 'confidence': 1.0}]
    data = json.loads(choice.message.content)

    # Validate each finding on its own, so one malformed finding doesn't throw away the valid ones.
    # .get("findings", []) returns an empty list instead of crashing if the key is missing.
    findings = []
    for f in data.get("findings", []):
        try:
            # reviewer is filled in here from the name we passed, not trusted from the model's reply.
            findings.append(Finding(**{**f, "reviewer": name}))
        # If one field doesn't match Pydantic validation, don't crash all findings, rather send a message to terminal that finding was skipped.
        except ValidationError as e:
            # Printed to stderr so it shows in the terminal but stays separate from the real output.
            print(f"Skipped invalid {name} finding: {e}", file=sys.stderr)
    return findings
