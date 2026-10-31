# In main.py, you create the secret, not GitHub. You generate it and give the same value to GitHub and to your .env. Then:
# GitHub mixes the message with the secret to produce a signature and sends it along.
# Your server does the same calculation with its copy of the secret.
# If the two signatures match, the message is genuine. If not, it’s rejected with 401

import hashlib
import hmac
import json
import logging
import os
import fastapi

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from app.service import review_pull_request

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("pr-reviewer")

WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")

# Only these PR events need a review: new PR, new commits pushed, PR reopened, draft marked ready.
REVIEW_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}

app = FastAPI(title="PR Reviewer")


# GitHub signs every webhook with your secret. Recomputing the signature proves the request
# really came from GitHub; without this, anyone who finds your URL could make you spend tokens.
def verify_signature(body: bytes, signature: str | None) -> None:
    if not WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="GITHUB_WEBHOOK_SECRET is not set")
    if not signature or not signature.startswith("sha256="):
        raise HTTPException(status_code=401, detail="missing signature")
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    # compare_digest takes the same time whether the strings match early or late (no timing attack).
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="bad signature")


# Runs after the response is sent. Errors are logged, not raised, since nobody is waiting on them.
def run_review(owner: str, repo: str, number: int, head_sha: str) -> None:
    try:
        out = review_pull_request(owner, repo, number, head_sha, post=True)
        logger.info("%s/%s#%d @ %s: %s", owner, repo, number, head_sha[:7], out["status"])
    except Exception:
        logger.exception("review failed for %s/%s#%d", owner, repo, number)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/webhook", status_code=202)
async def webhook(
    request: Request,
    background: BackgroundTasks,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str | None = Header(default=None),
) -> dict:
    # Read the raw bytes first: the signature is computed over the exact body GitHub sent.
    body = await request.body()
    verify_signature(body, x_hub_signature_256)

    # GitHub sends a "ping" when you first create the webhook, to check the URL works.
    if x_github_event == "ping":
        return {"msg": "pong"}
    if x_github_event != "pull_request":
        return {"msg": f"ignored event {x_github_event}"}

    payload = json.loads(body)
    action = payload.get("action")
    pr = payload["pull_request"]
    if action not in REVIEW_ACTIONS:
        return {"msg": f"ignored action {action}"}
    if pr.get("draft"):
        return {"msg": "ignored draft PR"}

    owner, repo = payload["repository"]["full_name"].split("/")
    number, head_sha = pr["number"], pr["head"]["sha"]

    # GitHub gives up if we take more than 10 seconds to answer, and a review takes longer.
    # So answer "accepted" now and do the review in the background.
    background.add_task(run_review, owner, repo, number, head_sha)
    return {"msg": "review queued", "pr": number, "sha": head_sha[:7]}