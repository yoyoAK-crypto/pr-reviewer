import sys, json
from pathlib import Path

# Shows misses and false alarms from the newest results file (or one passed as an argument).
EVALS = Path(__file__).resolve().parent
results = EVALS / "results"
SET_DIRS = {"easy": "cases", "hard": "cases_hard", "context": "cases_context", "injection": "cases_injection"}
path = Path(sys.argv[1]) if len(sys.argv) > 1 else max(results.glob("*.json"), key=lambda p: p.stat().st_mtime)
data = json.loads(path.read_text(encoding="utf-8"))
print(f"{path.name}  ({data['note']})\n")

# A case is clean when its labels list no bugs. (Checking the name broke on the context set,
# whose clean cases are called "ctx_clean_..." rather than "clean_...".)
labels_file = EVALS / SET_DIRS.get(data.get("set", "hard"), "cases_hard") / "labels.json"
labels = json.loads(labels_file.read_text(encoding="utf-8")) if labels_file.exists() else {}

for case in data["cases"]:
    if "error" in case:
        continue
    final = case["final"]
    is_clean = (not labels[case["case"]]["bugs"]) if case["case"] in labels else "clean_" in case["case"]
    missed = not is_clean and not any(f["hit"] == "strict" for f in final)
    if not (missed or (is_clean and final)):
        continue

    print(f"== {case['case']}  {'MISSED' if missed else 'FALSE ALARM'}")
    # For misses, show raw findings too, to tell "never found" apart from "synthesizer dropped it".
    for stage in (["raw", "final"] if missed else ["final"]):
        for f in case[stage]:
            print(f"  [{stage}] {f['reviewer']:8} {f['severity']:6} line {f['line']:<4} {f['hit']:13} {f['message']}")
    print()