import argparse, sys, json, time, os
from pathlib import Path
from datetime import datetime

# Lets `python evals/run_evals.py` find the app package, which lives one folder up.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.graph.build import build_graph

SETS = {"easy": ROOT / "evals" / "cases", "hard": ROOT / "evals" / "cases_hard"}
RESULTS = ROOT / "evals" / "results"

# Models often get line numbers slightly wrong, so a finding counts as "at the bug"
# if it lands within this many lines of the labeled range.
LINE_TOLERANCE = 2

# Groq free tier tokens-per-minute limit. After each case we pause long enough
# to stay under it. Raise it in .env (GROQ_TPM=...) if you upgrade your plan.
TPM = int(os.getenv("GROQ_TPM", "8000"))


# Is the finding at the bug's location? Same file, line inside the range (+/- tolerance).
def at_location(finding, bug) -> bool:
    lo, hi = bug["lines"]
    return finding.file == bug["file"] and lo - LINE_TOLERANCE <= finding.line <= hi + LINE_TOLERANCE


# Is the finding about the bug? Its message must mention one of the bug's keywords.
# Cases without keywords (the easy set) only check location.
def on_topic(finding, bug) -> bool:
    keywords = bug.get("keywords")
    return not keywords or any(k.lower() in finding.message.lower() for k in keywords)


# "loose" = right place. "strict" = right place AND talking about the right problem.
def matches(finding, bug, strict: bool) -> bool:
    return at_location(finding, bug) and (not strict or on_topic(finding, bug))


def pct(part, whole):
    return round(100 * part / whole, 1) if whole else None


def new_totals():
    return {"bugs": 0, "findings": 0, "clean_findings": 0,
            "caught_loose": 0, "caught_strict": 0, "tp_loose": 0, "tp_strict": 0}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser()
    parser.add_argument("note", nargs="?", default="", help='label for this run, e.g. "v1 baseline"')
    parser.add_argument("--set", choices=SETS, default="hard", help="which eval set to run")
    parser.add_argument("--split", choices=["tune", "holdout", "all"], default="tune",
                        help="tune while iterating; run holdout only to confirm a final version")
    args = parser.parse_args()

    cases_dir = SETS[args.set]
    labels = json.loads((cases_dir / "labels.json").read_text(encoding="utf-8"))
    # The easy set has no split field, so it always runs in full.
    labels = {k: v for k, v in labels.items() if args.split == "all" or v.get("split", args.split) == args.split}

    graph = build_graph()
    totals = {"raw": new_totals(), "final": new_totals()}
    by_category = {}  # category -> [caught_strict, total], final stage only
    clean_cases = clean_passed = 0
    tokens = seconds = 0
    rows = []

    print(f"Running {len(labels)} cases from '{args.set}' ({args.split} split)\n")

    for name, label in labels.items():
        diff = (cases_dir / name).read_text(encoding="utf-8")
        bugs = label["bugs"]

        start = time.perf_counter()
        try:
            result = graph.invoke({"pr_id": name, "diff": diff, "findings": [], "tokens_used": 0})
        except Exception as e:
            # One failing case shouldn't kill the whole run; record it and move on.
            print(f"{name:36} ERROR {e}", file=sys.stderr)
            rows.append({"case": name, "error": str(e)})

            # If the first two cases both fail, it's almost certainly the network or API key, not the model.
            if len(rows) == 2 and all("error" in r for r in rows):
                sys.exit("First two cases failed, check your internet connection and API keys.")

            time.sleep(30)  # likely a rate limit, give it time to reset
            continue
        elapsed = time.perf_counter() - start
        tokens += result["tokens_used"]
        seconds += elapsed

        row = {"case": name, "plan": result["plan"], "seconds": round(elapsed, 1), "tokens": result["tokens_used"]}
        for stage, findings in (("raw", result["findings"]), ("final", result["final_findings"])):
            t = totals[stage]
            t["bugs"] += len(bugs)
            t["findings"] += len(findings)
            if not bugs:
                t["clean_findings"] += len(findings)
            for mode, strict in (("loose", False), ("strict", True)):
                t[f"caught_{mode}"] += sum(any(matches(f, b, strict) for f in findings) for b in bugs)
                t[f"tp_{mode}"] += sum(any(matches(f, b, strict) for b in bugs) for f in findings)

            # Label each finding so you can see in the results file why it did or didn't count.
            annotated = []
            for f in findings:
                hit = ("strict" if any(matches(f, b, True) for b in bugs)
                       else "location_only" if any(matches(f, b, False) for b in bugs)
                       else "miss")
                annotated.append({**f.model_dump(), "hit": hit})
            row[stage] = annotated

        for b in bugs:
            cat = b.get("category", "uncategorized")
            caught = any(matches(f, b, True) for f in result["final_findings"])
            by_category.setdefault(cat, [0, 0])
            by_category[cat][0] += caught
            by_category[cat][1] += 1

        # A clean case passes only if the final review says nothing.
        n_final = len(result["final_findings"])
        if not bugs:
            clean_cases += 1
            clean_passed += n_final == 0
            status = "clean" if n_final == 0 else f"FALSE ALARM x{n_final}"
        else:
            strict = sum(any(matches(f, b, True) for f in result["final_findings"]) for b in bugs)
            loose = sum(any(matches(f, b, False) for f in result["final_findings"]) for b in bugs)
            status = f"caught {strict}/{len(bugs)}" + (" (right line, wrong issue)" if loose > strict else "")

        rows.append(row)
        print(f"{name:36} {status:38} final: {n_final}  {elapsed:5.1f}s  {result['tokens_used']:6} tok")

        # Pause so tokens used per minute stays under the Groq limit.
        time.sleep(max(0, result["tokens_used"] / TPM * 60 - elapsed))

    # recall:    share of labeled bugs that got caught
    # precision: share of comments that point at a labeled bug. A valid extra comment on a bug
    #            case still counts against it, so check the results file before trusting it.
    # strict versions require the message to describe the right problem, not just the right line.
    metrics = {}
    for stage, t in totals.items():
        metrics[stage] = {
            "recall_strict_%": pct(t["caught_strict"], t["bugs"]),
            "recall_loose_%": pct(t["caught_loose"], t["bugs"]),
            "precision_strict_%": pct(t["tp_strict"], t["findings"]),
            "precision_loose_%": pct(t["tp_loose"], t["findings"]),
            "findings_per_clean_case": round(t["clean_findings"] / clean_cases, 2) if clean_cases else None,
        }
    ok = [r for r in rows if "error" not in r]
    metrics["final_recall_strict_by_category_%"] = {c: pct(hit, n) for c, (hit, n) in sorted(by_category.items())}
    metrics["clean_pass_rate_%"] = pct(clean_passed, clean_cases)
    metrics["total_tokens"] = tokens
    metrics["avg_tokens_per_pr"] = round(tokens / len(ok)) if ok else None
    metrics["avg_seconds_per_pr"] = round(seconds / len(ok), 1) if ok else None
    metrics["errors"] = len(rows) - len(ok)

    print("\n" + json.dumps(metrics, indent=2))

    # Save every run so you can compare prompt versions over time.
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{datetime.now():%Y%m%d_%H%M%S}_{args.set}_{args.split}.json"
    out.write_text(json.dumps({"note": args.note, "set": args.set, "split": args.split,
                               "metrics": metrics, "cases": rows}, indent=2), encoding="utf-8")
    print(f"\nSaved {out.relative_to(ROOT)}")