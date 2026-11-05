import math
import re
from dataclasses import dataclass

# Secrets with a recognisable shape. Each pattern only matches the secret itself,
# so the rest of the line (and every line number) stays exactly as it was.
PATTERNS = {
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b"),
    "stripe_key": re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "groq_key": re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"),
    "openai_key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "langsmith_key": re.compile(r"\blsv2_[A-Za-z0-9_]{20,}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
}

# Secrets with no fixed shape, caught by context: a name like "password" or "api_key"
# assigned a quoted string literal. Only the value inside the quotes is replaced.
ASSIGNMENT = re.compile(
    r"""(?ix)
    \b[\w.]*(?:password|passwd|secret|token|api_?key|access_?key|private_?key|client_?secret)[\w.]*
    \s*[:=]\s*
    (?P<q>["'])(?P<value>[^"'\s]{8,})(?P=q)
    """)

# Values that are obviously not real secrets, even when assigned to a "password" variable.
PLACEHOLDERS = re.compile(r"(?i)^(?:changeme|password|example|dummy|test|xxx+|\*+|<.*>|\$\{.*\}|your[_-].*)$")


@dataclass
class Redaction:
    kind: str           # e.g. "stripe_key"
    line_index: int     # 0-based index of the line in the text
    is_added: bool      # True when the line is an added (+) line of a diff


# Shannon entropy in bits per character: random keys score ~4-6, ordinary words ~2-3.
def _entropy(s: str) -> float:
    counts = {c: s.count(c) for c in set(s)}
    return -sum(n / len(s) * math.log2(n / len(s)) for n in counts.values())


def _redact_line(line: str) -> tuple[str, list[str]]:
    kinds = []
    for kind, pattern in PATTERNS.items():
        if pattern.search(line):
            line = pattern.sub(f"[REDACTED:{kind}]", line)
            kinds.append(kind)

    def generic(m: re.Match) -> str:
        value = m.group("value")
        if "[REDACTED:" in value or PLACEHOLDERS.match(value) or _entropy(value) < 3.0:
            return m.group(0)          # a placeholder or low-randomness value: leave it
        kinds.append("credential")
        return m.group(0).replace(value, "[REDACTED:credential]")

    return ASSIGNMENT.sub(generic, line), kinds


# Replaces secrets in any text (a diff or retrieved code) before it is sent to the model.
# Works line by line and never adds or removes lines, so diff line numbers stay valid.
# A private key spans many lines: everything from BEGIN to END is blanked out.
def redact(text: str) -> tuple[str, list[Redaction]]:
    out, found, in_key = [], [], False
    for i, line in enumerate(text.splitlines()):
        prefix = line[:1] if line[:1] in "+- " and not line.startswith(("+++", "---")) else ""
        if in_key:
            if "-----END" in line and "PRIVATE KEY-----" in line:
                in_key = False
            out.append(prefix + "[REDACTED:private_key]")
            continue
        new, kinds = _redact_line(line)
        if "private_key" in kinds and "-----END" not in line:
            in_key = True
        out.append(new)
        found += [Redaction(k, i, prefix == "+") for k in kinds]
    return "\n".join(out) + ("\n" if text.endswith("\n") else ""), found


MARKER = re.compile(r"\[REDACTED:(\w+)\]")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


# Finds redaction markers on ADDED lines of a (redacted) diff, with each one's file and line.
# Works whether the diff was redacted here or earlier (e.g. in service.py before tracing),
# because it looks for the markers rather than the secrets.
def secrets_added(diff: str) -> list[tuple[str, int, str]]:
    hits, path, new_line = [], None, None
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            path, new_line = None, None
        elif line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif m := HUNK.match(line):
            new_line = int(m.group(1))
        elif path is None or new_line is None or line.startswith(("--- ", "\\")):
            continue
        elif line.startswith("-"):
            continue
        else:
            if line.startswith("+"):
                for kind in dict.fromkeys(MARKER.findall(line)):   # each kind once per line
                    hits.append((path, new_line, kind))
            new_line += 1
    return hits
