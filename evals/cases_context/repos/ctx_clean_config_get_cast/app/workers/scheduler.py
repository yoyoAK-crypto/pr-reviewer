from app.api.auth import create_token
from app.workers.jobs import JobStats, run_with_retry

stats = JobStats()


def run_nightly(tasks) -> dict:
    for task in tasks:
        run_with_retry(task, stats)
    return {"processed": stats.processed, "failed": stats.failed}


def mark_external_result(ok: bool) -> None:
    stats.record(ok=ok)


def service_token() -> str:
    return create_token(0)
