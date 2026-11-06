"""Prints one table comparing eval runs, e.g. the same set with different retrieval modes.

    python evals/compare.py                    # newest 8 result files
    python evals/compare.py context            # only runs on the context set
"""
import json
import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parent
results = EVALS / "results"
SET_DIRS = {"easy": "cases", "hard": "cases_hard", "context": "cases_context", "injection": "cases_injection"}
LINE_TOLERANCE = 2


# "Named the caller": a final comment at the bug's location that names the function that breaks.
# This separates evidence from guessing. Without retrieval, a reviewer can say "this may break
# callers" from the diff alone, and that already counts as a strict catch. Only a reviewer that
# SAW the broken caller can name it, e.g. "breaks invoice_summary, which reads user.email".
def named_caller_recall(run: dict) -> str:
    labels_file = EVALS / SET_DIRS.get(run.get("set"), "") / "labels.json"
    if not labels_file.exists():
        return "-"
    labels = json.loads(labels_file.read_text(encoding="utf-8"))
    named = total = 0
    for case in run["cases"]:
        if "error" in case:
            continue
        for bug in labels.get(case["case"], {}).get("bugs", []):
            if not bug.get("affected"):
                continue
            total += 1
            caller = bug["affected"].split("::")[1].split(".")[-1].lower()   # "invoice_summary"
            lo, hi = bug["lines"]
            named += any(f["file"] == bug["file"] and lo - LINE_TOLERANCE <= f["line"] <= hi + LINE_TOLERANCE
                         and caller in f["message"].lower() for f in case["final"])
    return f"{round(100 * named / total, 1)}%" if total else "-"


want = sys.argv[1] if len(sys.argv) > 1 else None
files = sorted(results.glob("*.json"), key=lambda p: p.stat().st_mtime)
runs = [json.loads(p.read_text(encoding="utf-8")) for p in files]
runs = [r for r in runs if want is None or r.get("set") == want][-8:]

cols = ["set", "retrieval", "defense", "recall", "named caller", "caller found", "inj flagged", "precision", "clean pass", "tokens/PR", "note"]
print(" | ".join(cols))
print(" | ".join("---" for _ in cols))
for r in runs:
    m, f = r["metrics"], r["metrics"]["final"]
    print(" | ".join(str(x) for x in [
        r.get("set"), m.get("retrieval", "off"), m.get("injection_defense", "off"),
        f"{f['recall_strict_%']}%", named_caller_recall(r),
        f"{m['retrieval_found_broken_caller_%']}%" if "retrieval_found_broken_caller_%" in m else "-",
        f"{m['injection_flagged_%']}%" if "injection_flagged_%" in m else "-",
        f"{f['precision_strict_%']}%", f"{m['clean_pass_rate_%']}%",
        m.get("avg_tokens_per_pr"), r.get("note", "")]))
