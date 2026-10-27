# pr-reviewer

## Evaluation

31 hand-built cases (21 planted bugs: 10 security, 11 correctness; 10 clean PRs, several designed to look dangerous but be safe), split into 22 tune / 9 holdout. A catch counts only if the comment is at the right line (±2) and names the right issue.

| Version | Change | Recall | Precision | Clean pass | Tokens/PR |
|---|---|---|---|---|---|
| v3 (holdout) | 3 reviewers + rule-based synthesizer | 66.7% | 31.2% | 0% | 2.6k |
| v4 (tune) | Line-numbered diffs | 93.3% | 55.2% | 42.9% | 3.2k |
| v5 (tune) | Severity rubric, diff-only scope, low-severity filter | 100% | 65 to 71% | 57 to 71% | 3.4k |
| v5 (holdout) | Final check | 100% | 60.0% | 66.7% | 3.0k |

What mattered:
- **Most "misses" were line-number errors, not reasoning errors.** The model found the bugs but counted lines wrong from the @@ header. Prefixing each line with its real number fixed it.
- **Model confidence was useless for filtering.** Nitpicks were rated 0.85 to 0.95 confidence. An explicit severity rubric worked instead.
- **Runs vary.** With temperature 0, the clean pass rate still moved 57 to 71% between identical runs, so numbers are reported as ranges.
- **Eval labels had bugs too.** Two "misses" were label errors, and one "false alarm" was a valid comment.

Note: the holdout split was viewed once (v3) before the final fixes; those fixes were general (line numbering, severity rules), not case-specific.
