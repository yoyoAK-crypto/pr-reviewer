from app.workers.jobs import JobStats


def test_snapshot_reflects_recorded_results():
    stats = JobStats()
    stats.record(ok=True)
    stats.record(ok=False)
    assert stats.snapshot() == {"processed": 1, "failed": 1}
