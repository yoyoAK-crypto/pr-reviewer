# Sends fake GitHub messages to the real /webhook code. Only the outside services are faked
# (GitHub and the model), so these tests make no network calls and cost no tokens.

import hashlib
import hmac
import json
import os

# nodes.py builds a Groq client on import, which needs a key to exist. It is never used here.
os.environ.setdefault("GROQ_API_KEY", "test-key")

import pytest
from fastapi.testclient import TestClient

import app.main as main
import app.service as service
from app.graph.state import Finding
from app.tools.github import BOT_MARKER

SECRET = "test-secret"

DIFF = """diff --git a/app/a.py b/app/a.py
--- a/app/a.py
+++ b/app/a.py
@@ -1,2 +1,3 @@
 x = 1
+y = eval(input())
 z = 3
"""


# Stands in for GitHub and the graph, and records what the server did with them.
class FakeServices:
    def __init__(self):
        self.reviews = []
        self.graph_calls = 0

    def invoke(self, state):
        self.graph_calls += 1
        finding = Finding(reviewer="security", file="app/a.py", line=2, severity="high",
                          message="eval on user input", confidence=0.9)
        return {"plan": ["security"], "findings": [finding], "final_findings": [finding], "tokens_used": 100}

    def already_reviewed(self, owner, repo, number, head_sha):
        return any(BOT_MARKER.format(sha=head_sha) in r["body"] for r in self.reviews)

    def post_review(self, owner, repo, number, review):
        self.reviews.append(review)
        return "https://github.com/fake/review"


@pytest.fixture
def fake(monkeypatch):
    services = FakeServices()
    monkeypatch.setattr(main, "WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(service, "graph", services)
    monkeypatch.setattr(service, "get_pr_diff", lambda owner, repo, number: DIFF)
    monkeypatch.setattr(service, "already_reviewed", services.already_reviewed)
    monkeypatch.setattr(service, "post_review", services.post_review)
    return services


@pytest.fixture
def client():
    return TestClient(main.app)


def pr_event(action="opened", sha="a" * 40, draft=False) -> bytes:
    return json.dumps({
        "action": action,
        "repository": {"full_name": "owner/repo"},
        "pull_request": {"number": 1, "draft": draft, "head": {"sha": sha}},
    }).encode()


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# Sends a correctly signed message, the way GitHub would.
def send(client, body: bytes, event: str = "pull_request"):
    return client.post("/webhook", content=body,
                       headers={"X-GitHub-Event": event, "X-Hub-Signature-256": sign(body)})


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


# ---------- Signatures ----------

def test_missing_signature_is_rejected(client, fake):
    resp = client.post("/webhook", content=pr_event(), headers={"X-GitHub-Event": "pull_request"})
    assert resp.status_code == 401
    assert fake.graph_calls == 0


def test_signature_from_wrong_secret_is_rejected(client, fake):
    body = pr_event()
    resp = client.post("/webhook", content=body,
                       headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sign(body, "wrong")})
    assert resp.status_code == 401
    assert fake.graph_calls == 0


def test_body_changed_after_signing_is_rejected(client, fake):
    resp = client.post("/webhook", content=pr_event(sha="b" * 40),
                       headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sign(pr_event())})
    assert resp.status_code == 401
    assert fake.graph_calls == 0


# With no secret configured, nothing could be verified, so the server must refuse rather than trust everyone.
def test_server_without_a_secret_refuses_everything(client, fake, monkeypatch):
    monkeypatch.setattr(main, "WEBHOOK_SECRET", "")
    assert send(client, pr_event()).status_code == 500
    assert fake.graph_calls == 0


# ---------- Irrelevant events ----------

def test_ping_gets_pong(client, fake):
    resp = send(client, b"{}", event="ping")
    assert resp.status_code == 202
    assert resp.json() == {"msg": "pong"}
    assert fake.graph_calls == 0


def test_other_event_types_are_ignored(client, fake):
    assert "ignored" in send(client, b"{}", event="push").json()["msg"]
    assert fake.graph_calls == 0


@pytest.mark.parametrize("action", ["closed", "edited", "labeled", "assigned"])
def test_other_pr_actions_are_ignored(client, fake, action):
    assert "ignored" in send(client, pr_event(action=action)).json()["msg"]
    assert fake.graph_calls == 0


def test_draft_prs_are_ignored(client, fake):
    assert "ignored" in send(client, pr_event(draft=True)).json()["msg"]
    assert fake.graph_calls == 0


# ---------- Reviews ----------

@pytest.mark.parametrize("action", ["opened", "synchronize", "reopened", "ready_for_review"])
def test_review_actions_post_one_review(client, fake, action):
    resp = send(client, pr_event(action=action))
    assert resp.status_code == 202
    assert len(fake.reviews) == 1


def test_review_is_tied_to_the_commit_and_comments_on_the_line(client, fake):
    sha = "c" * 40
    send(client, pr_event(sha=sha))
    review = fake.reviews[0]
    assert review["commit_id"] == sha
    assert review["event"] == "COMMENT"  # the bot never approves or blocks a PR
    assert [(c["path"], c["line"]) for c in review["comments"]] == [("app/a.py", 2)]


# GitHub can deliver the same message twice. The second one must not post or spend tokens.
def test_retry_of_the_same_commit_does_not_post_twice(client, fake):
    body = pr_event()
    assert send(client, body).status_code == 202
    assert send(client, body).status_code == 202
    assert len(fake.reviews) == 1
    assert fake.graph_calls == 1


def test_new_commit_gets_its_own_review(client, fake):
    send(client, pr_event(action="opened", sha="a" * 40))
    send(client, pr_event(action="synchronize", sha="b" * 40))
    assert [r["commit_id"] for r in fake.reviews] == ["a" * 40, "b" * 40]


# The review runs after GitHub has its answer, so a failure is logged, not raised.
def test_failed_review_still_answers_202(client, fake, monkeypatch):
    def fail(*args):
        raise RuntimeError("github is down")
    monkeypatch.setattr(service, "post_review", fail)
    assert send(client, pr_event()).status_code == 202
    assert fake.reviews == []
