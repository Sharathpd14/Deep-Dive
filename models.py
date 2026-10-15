from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


SubQStatus = Literal["pending", "researched", "verified", "thin", "insufficient"]
VerdictLabel = Literal["pending", "supported", "partial", "unsupported", "contradicted"]
RunStatus = Literal["queued", "running", "done", "failed"]


class SubQuestion(BaseModel):
    id: str                                   # "Q1", "Q2", ...
    run_id: str
    text: str
    queries: list[str] = Field(default_factory=list)
    status: SubQStatus = "pending"


class Chunk(BaseModel):
    id: str                                   # "E1", "E2", ... unique within a run
    run_id: str
    sub_q_id: str | None = None
    url: str
    title: str = ""
    domain: str = ""
    text: str
    tier: float = 0.5                         # source quality, 0.0 to 1.0
    fetched_at: str = Field(default_factory=utcnow)


class Claim(BaseModel):
    id: str                                   # "C1", "C2", ...
    run_id: str
    sub_q_id: str
    text: str
    evidence_ids: list[str]
    verdict: VerdictLabel = "pending"
    quote: str = ""                           # exact supporting text from the source
    reason: str = ""
    round: int = 0                            # which research loop produced it


class Verdict(BaseModel):
    """Strict shape of the verifier LLM's JSON output."""
    verdict: Literal["supported", "partial", "unsupported", "contradicted"]
    quote: str = ""
    reason: str = ""


class TrailEvent(BaseModel):
    id: int | None = None
    run_id: str
    ts: str = Field(default_factory=utcnow)
    agent: str                                # planner, researcher, verifier, ...
    action: str                               # e.g. "search", "claim_rejected"
    detail: dict = Field(default_factory=dict)
    reason: str = ""