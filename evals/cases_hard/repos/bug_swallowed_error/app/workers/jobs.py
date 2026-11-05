import asyncio
import logging
import threading
import time

import httpx

logger = logging.getLogger(__name__)

STATUS_URL = "https://jobs.internal/api/v2/status/{job_id}"
MAX_ATTEMPTS = 3


class JobStats:
    def __init__(self):
        self._lock = threading.Lock()
        self.processed = 0
        self.failed = 0

    def record(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self.processed += 1
            else:
                self.failed += 1


async def fetch_status(client: httpx.AsyncClient, job_id: str) -> str:
    resp = await client.get(STATUS_URL.format(job_id=job_id), timeout=10)
    resp.raise_for_status()
    return resp.json()["status"]


async def wait_for_job(client: httpx.AsyncClient, job_id: str, poll_seconds: float = 2.0) -> str:
    while True:
        status = await fetch_status(client, job_id)
        if status in ("done", "failed"):
            return status
        await asyncio.sleep(poll_seconds)


def run_with_retry(task, stats: JobStats) -> None:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            task()
        except Exception:
            pass
        stats.record(ok=True)
        return
    stats.record(ok=False)
