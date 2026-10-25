import sys
from pathlib import Path

# Lets `python scripts/review_local.py` find the app package, which lives one folder up.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.graph.build import build_graph

if __name__ == "__main__":
    # Windows defaults to cp1252 when output is piped, which crashes on characters like '‑' in model messages.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    diff = Path(sys.argv[1]).read_text(encoding="utf-8")

    # The graph now does everything: planner picks reviewers, they run in parallel,
    # and the synthesizer filters, dedupes, ranks and caps the findings.
    graph = build_graph()

    # START state. findings and tokens_used start empty because nodes add to them.
    result = graph.invoke({"pr_id": "local", "diff": diff, "findings": [], "tokens_used": 0})

    # We then print the final review of that state when we reach END
    print(result["final_review"])

    # Debug info: compare raw vs final to see how much the synthesizer filtered out.
    print(f"\nReviewers run: {result['plan']}")
    print(f"Raw findings: {len(result['findings'])}, tokens used: {result['tokens_used']}")