# /stats and the structured log line written for every review.

import json
import logging

import pytest
from fastapi.testclient import TestClient

import app.main as main
import app.service as service
from app.graph.state import Finding

# A fake Stripe key, assembled at runtime: written out in full, it looks real enough that
# GitHub push protection (rightly) blocks the commit.
FAKE_STRIPE_KEY = "sk_" + "live_" + "51HxQ2bKq8vT3mZpL9wR4nY7c"

DIFF = f'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,2 @@\n x = 1\n+key = "{FAKE_STRIPE_KEY}"\n'


class FakeGraph:
    def __init__(self):
        self.config = None

    def invoke(self, state, config=None):
        self.config = config
        f = Finding(reviewer="guardrail", file="a.py", line=2, severity="high", confidence=1.0,
                    message="Possible prompt injection: example.")
        return {"plan": ["security"], "findings": [f], "final_findings": [f], "tokens_used": 1234}


@pytest.fixture
def pipeline(monkeypatch):
    graph = FakeGraph()
    monkeypatch.setattr(service, "graph", graph)
    monkeypatch.setattr(service, "get_pr_diff", lambda *a: DIFF)
    monkeypatch.setattr(service, "already_reviewed", lambda *a: False)
    monkeypatch.setattr(service, "post_review", lambda *a: "https://github.com/fake")
    return graph


def test_stats_count_a_review(pipeline):
    service.review_pull_request("o", "r", 1, "a" * 40, post=True)
    stats = TestClient(main.app).get("/stats").json()
    assert stats["reviews_by_status"] == {"posted": 1}
    assert stats["tokens_total"] == 1234 and stats["secrets_redacted_total"] == 1
    assert stats["injections_flagged_total"] == 1 and stats["recent"][0]["pr"] == "o/r#1"


def test_skips_and_errors_are_counted_too(pipeline, monkeypatch):
    monkeypatch.setattr(service, "already_reviewed", lambda *a: True)
    service.review_pull_request("o", "r", 1, "a" * 40)
    monkeypatch.setattr(service, "already_reviewed", lambda *a: False)
    monkeypatch.setattr(service, "post_review", lambda *a: (_ for _ in ()).throw(RuntimeError("github down")))
    with pytest.raises(RuntimeError):
        service.review_pull_request("o", "r", 2, "b" * 40)
    stats = TestClient(main.app).get("/stats").json()
    assert stats["reviews_by_status"] == {"skipped_already_reviewed": 1, "error": 1}
    assert stats["recent"][-1]["error"] == "RuntimeError"


def test_langsmith_run_is_labelled_with_repo_and_commit(pipeline):
    service.review_pull_request("o", "r", 7, "c" * 40, post=False)
    assert pipeline.config["metadata"] == {"repo": "o/r", "pr": 7, "sha": "c" * 40}
    assert "dry-run" in pipeline.config["tags"]


def test_log_line_is_json_and_contains_no_code_or_secrets(pipeline, caplog):
    with caplog.at_level(logging.INFO, logger="pr-reviewer.metrics"):
        service.review_pull_request("o", "r", 1, "a" * 40)
    line = next(r.getMessage() for r in caplog.records if r.name == "pr-reviewer.metrics")
    event = json.loads(line)
    assert event["status"] == "posted" and event["tokens"] == 1234
    assert "sk_live" not in line and "key =" not in line


def test_stats_can_be_locked_with_a_token(pipeline, monkeypatch):
    monkeypatch.setattr(main, "STATS_TOKEN", "s3cret")
    client = TestClient(main.app)
    assert client.get("/stats").status_code == 401
    assert client.get("/stats", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/stats", headers={"Authorization": "Bearer s3cret"}).status_code == 200
