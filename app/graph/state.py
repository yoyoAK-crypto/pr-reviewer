import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field


# One review comment. Every reviewer returns a list of these.
# Model output is untrusted and gets checked by "Finding"
class Finding(BaseModel):
    reviewer: str                                  # "security", "style" or "tests", set by our code, not the model
    file: str
    line: int
    severity: Literal["low", "medium", "high"]
    message: str
    confidence: float = Field(ge=0, le=1)
                    # what the synthesizer kept, used by evals


# Everything the LangGraph workflow reads and writes as a PR moves through it.
# Each node receives this dict and returns only the keys it changed.
# ReviewState only organises data that has already been checked.
class ReviewState(TypedDict):
    pr_id: str                                     # "local" for now, will identify the GitHubs' PR number later
    diff: str                                      # contains the lines that were added (+), the lines that were removed (-) and a few unchanged lines around them for context.
    changed_files: list[str]                       # parsed from the diff, used by the planner.
    plan: list[str]                                # reviewers the planner chose to run. Needs system prompt

    # Annotated[..., operator.add] tells LangGraph to merging lists instead of overwriting.
    # Three reviewers running in parallel each return {"findings": [...]},
    # and LangGraph adds them together instead of keeping only the last one.
    # Each reviewer calls the model with its own prompt, like SYSTEM
    findings: Annotated[list[Finding], operator.add]

    final_review: str                              # The model writes a summary from all the findings, using a synthesizer prompt
    tokens_used: Annotated[int, operator.add] 
    final_findings: list[Finding]                  # what the synthesizer kept, used by evals     # summed across all nodes, for cost tracking