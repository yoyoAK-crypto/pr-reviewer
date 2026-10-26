import sys, json, time
from pathlib import Path
from datetime import datetime

# Lets `python evals/run_evals.py` find the app package, which lives one folder up.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.graph.build import build_graph

CASES = ROOT / "evals" / "cases"
RESULTS = ROOT / "evals" / "results"

# Models often get line numbers slightly wrong, so a finding counts as a hit
# if it lands within this many lines of the labeled bug.
LINE_TOLERANCE = 2


# Does this finding point at this labeled bug? Same file, line inside the bug's range (+/- tolerance).
def matches(finding, bug) -> bool:
    lo, hi = bug["lines"]
    return finding.file == bug["file"] and lo - LINE_TOLERANCE <= finding.line <= hi + LINE_TOLERANCE


# For one case: which bugs were caught, and how many findings pointed at a real bug.
def score(findings, bugs):
    caught = sum(any(matches(f, b) for f in findings) for b in bugs)
    true_pos = sum(any(matches(f, b) for b in bugs) for f in findings)
    return caught, true_pos


def pct(part, whole):
    return round(100 * part / whole, 1) if whole else None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    # Optional note for this run, e.g. python evals/run_evals.py "v1 baseline"
    note = sys.argv[1] if len(sys.argv) > 1 else ""

    labels = json.loads((CASES / "labels.json").read_text(encoding="utf-8"))
    graph = build_graph()

    # We score twice: raw (all reviewer findings) and final (after the synthesizer).
    # Comparing them shows what the synthesizer gains in precision and loses in recall.
    totals = {stage: {"bugs": 0, "caught": 0, "findings": 0, "true_pos": 0, "clean_findings": 0}
              for stage in ("raw", "final")}
    clean_cases = clean_passed = 0
    tokens = seconds = 0
    rows = []

    for name, label in labels.items():
        diff = (CASES / name).read_text(encoding="utf-8")
        bugs = label["bugs"]

        start = time.perf_counter()
        try:
            result = graph.invoke({"pr_id": name, "diff": diff, "findings": [], "tokens_used": 0})
        except Exception as e:
            # One failing case shouldn't kill the whole run; record it and move on.
            print(f"{name:32} ERROR {e}", file=sys.stderr)
            rows.append({"case": name, "error": str(e)})

            # If the first two cases both fail, it's almost certainly the network or API key, not the model.
            if len(rows) == 2 and all("error" in r for r in rows):
                sys.exit("First two cases failed, check your internet connection and API keys.")

            continue
        elapsed = time.perf_counter() - start
        tokens += result["tokens_used"]
        seconds += elapsed

        row = {"case": name, "plan": result["plan"], "seconds": round(elapsed, 1), "tokens": result["tokens_used"]}
        for stage, findings in (("raw", result["findings"]), ("final", result["final_findings"])):
            caught, true_pos = score(findings, bugs)
            t = totals[stage]
            t["bugs"] += len(bugs)
            t["caught"] += caught
            t["findings"] += len(findings)
            t["true_pos"] += true_pos
            if not bugs:
                t["clean_findings"] += len(findings)
            row[stage] = {"caught": caught, "findings": [f.model_dump() for f in findings]}

        # A clean case passes only if the final review says nothing.
        if not bugs:
            clean_cases += 1
            clean_passed += len(result["final_findings"]) == 0

        rows.append(row)
        status = f"caught {row['final']['caught']}/{len(bugs)}" if bugs else "clean"
        print(f"{name:32} {status:12} final findings: {len(result['final_findings']):2}  {elapsed:5.1f}s")

        time.sleep(20)

    # Recall: share of labeled bugs that got caught.
    # Precision: share of comments that point at a labeled bug. Note: a valid extra comment on a
    # bug case still counts against precision, so read the per-case findings before trusting it.
    metrics = {}
    for stage, t in totals.items():
        metrics[stage] = {
            "recall_%": pct(t["caught"], t["bugs"]),
            "precision_%": pct(t["true_pos"], t["findings"]),
            "findings_per_clean_case": round(t["clean_findings"] / clean_cases, 2) if clean_cases else None,
        }
    ok = [r for r in rows if "error" not in r]
    metrics["clean_pass_rate_%"] = pct(clean_passed, clean_cases)
    metrics["total_tokens"] = tokens
    metrics["avg_seconds_per_pr"] = round(seconds / len(ok), 1) if ok else None
    metrics["errors"] = len(rows) - len(ok)

    print("\n" + json.dumps(metrics, indent=2))

    # Save every run so you can compare prompt versions over time.
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps({"note": note, "metrics": metrics, "cases": rows}, indent=2), encoding="utf-8")
    print(f"\nSaved {out.relative_to(ROOT)}")