import sys, json
from pathlib import Path

# Shows misses and false alarms from the newest results file (or one passed as an argument).
results = Path(__file__).resolve().parent / "results"
path = Path(sys.argv[1]) if len(sys.argv) > 1 else max(results.glob("*.json"), key=lambda p: p.stat().st_mtime)
data = json.loads(path.read_text(encoding="utf-8"))
print(f"{path.name}  ({data['note']})\n")

for case in data["cases"]:
    if "error" in case:
        continue
    final = case["final"]
    is_clean = case["case"].startswith("clean_")
    missed = not is_clean and not any(f["hit"] == "strict" for f in final)
    if not (missed or (is_clean and final)):
        continue

    print(f"== {case['case']}  {'MISSED' if missed else 'FALSE ALARM'}")
    # For misses, show raw findings too, to tell "never found" apart from "synthesizer dropped it".
    for stage in (["raw", "final"] if missed else ["final"]):
        for f in case[stage]:
            print(f"  [{stage}] {f['reviewer']:8} {f['severity']:6} line {f['line']:<4} {f['hit']:13} {f['message']}")
    print()