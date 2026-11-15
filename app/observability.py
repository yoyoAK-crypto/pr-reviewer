# Observability: what the deployed reviewer is doing, without opening a debugger.
#
# Every review (posted, skipped or failed) produces ONE structured log line, a JSON object
# that log tools (Render's log search, Datadog, etc.) can filter on, e.g. status=error.
# The same numbers are summed in memory and served at /stats.
#
# Deliberately left out: the diff, code, and finding messages. Logs are often kept longer and
# shared more widely than the code itself, so they hold counts, never content.
#
# Limitation: the totals live in memory, so they reset when the service restarts (on Render's
# free tier, after every sleep). Durable history would need a database, e.g. a Supabase table.

import json
import logging
import threading
import time
from collections import Counter, deque

logger = logging.getLogger("pr-reviewer.metrics")

_lock = threading.Lock()          # reviews run in background threads; counters must not race
_started = time.time()
_by_status: Counter = Counter()   # posted / dry_run / skipped_... / error
_totals: Counter = Counter()      # tokens, findings, secrets_redacted, injections_flagged
_seconds: list[float] = []        # durations of reviews that actually ran the model
_recent: deque = deque(maxlen=20) # last 20 events, newest last


def record_review(event: dict) -> None:
    event = {"event": "review", "ts": round(time.time(), 3), **event}
    logger.info(json.dumps(event, sort_keys=True))
    with _lock:
        _by_status[event.get("status", "unknown")] += 1
        for key in ("tokens", "findings", "secrets_redacted", "injections_flagged"):
            _totals[key] += int(event.get(key) or 0)
        if "tokens" in event:          # only reviews that reached the model
            _seconds.append(float(event.get("seconds", 0)))
        _recent.append(event)


def snapshot() -> dict:
    with _lock:
        ran = len(_seconds)
        return {
            "uptime_seconds": round(time.time() - _started),
            "reviews_by_status": dict(_by_status),
            "reviews_run": ran,
            "tokens_total": _totals["tokens"],
            "tokens_per_review": round(_totals["tokens"] / ran) if ran else None,
            "seconds_per_review": round(sum(_seconds) / ran, 2) if ran else None,
            "findings_total": _totals["findings"],
            "secrets_redacted_total": _totals["secrets_redacted"],
            "injections_flagged_total": _totals["injections_flagged"],
            "recent": list(_recent),
        }


# Only for tests: start from zero.
def reset() -> None:
    global _started
    with _lock:
        _by_status.clear(); _totals.clear(); _seconds.clear(); _recent.clear()
        _started = time.time()
