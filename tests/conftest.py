# Settings for every test, applied before any app code is imported.
# Values set here win over .env, because load_dotenv() never overrides existing variables.
import os

os.environ["GROQ_API_KEY"] = "test-key"          # the Groq client needs a key to exist; it's never called
os.environ["GITHUB_TOKEN"] = "test-token"
os.environ["LANGSMITH_TRACING"] = "false"        # no traces sent from tests
os.environ["RETRIEVAL_MODE"] = "off"             # no repo downloads; retrieval has its own tests
os.environ["INJECTION_DEFENSE"] = "on"
os.environ.pop("STATS_TOKEN", None)

import json
from types import SimpleNamespace

import pytest


# A stand-in for the Groq client: records every prompt and answers with canned findings.
# Lets the real graph run end to end with no network and no tokens.
class FakeGroq:
    def __init__(self, findings_by_reviewer=None):
        self.calls = []
        self.findings_by_reviewer = findings_by_reviewer or {}
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        system, user = kwargs["messages"][0]["content"], kwargs["messages"][1]["content"]
        self.calls.append({"system": system, "user": user})
        first_line = system.splitlines()[0].lower()   # "You are a security code reviewer..." etc.
        reviewer = "security" if "security" in first_line else "style" if "style" in first_line else "tests"
        content = json.dumps({"findings": self.findings_by_reviewer.get(reviewer, [])})
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=content))],
                               usage=SimpleNamespace(total_tokens=1000))


@pytest.fixture
def fake_groq(monkeypatch):
    import app.graph.nodes as nodes
    fake = FakeGroq()
    monkeypatch.setattr(nodes, "client", fake)
    return fake


@pytest.fixture(autouse=True)
def fresh_stats():
    from app import observability
    observability.reset()
    yield
