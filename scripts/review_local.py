import sys
from pathlib import Path

# Lets `python scripts/review_local.py` find the app package, which lives one folder up.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.graph.nodes import run_reviewer

REVIEWERS = ["security", "style", "tests"]

if __name__ == "__main__":
    # Windows defaults to cp1252 when output is piped, which crashes on characters like '‑' in model messages.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    diff = Path(sys.argv[1]).read_text(encoding="utf-8")

    # Run each reviewer on the same diff and collect everything into one list.
    findings = [f for name in REVIEWERS for f in run_reviewer(name, diff)]

    # Sort by file and line so findings about the same code appear next to each other.
    for f in sorted(findings, key=lambda f: (f.file, f.line)):
        print(f"[{f.reviewer}][{f.severity}] {f.file}:{f.line} ({f.confidence}) {f.message}")
