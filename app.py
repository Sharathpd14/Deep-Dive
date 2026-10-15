from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path

import markdown
import nh3
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field

import runner
from coverage import verified_stats
from db import store
from db.connection import init_db

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=BASE / "templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    stale = store.fail_stale_runs()
    if stale:
        print(f"Marked {stale} interrupted run(s) as failed")
    yield


app = FastAPI(title="DeepDive", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


class RunRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    question: str = Field(min_length=10, max_length=500)


def _run_or_404(run_id: str) -> dict:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


def render_markdown(text: str) -> str:
    """Report text comes from web pages: sanitize before it reaches a browser."""
    return nh3.clean(markdown.markdown(text, extensions=["extra", "sane_lists"]))


# ---------- API ----------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/runs", status_code=202)
def create_run(req: RunRequest, background: BackgroundTasks):
    try:
        run_id = runner.start(req.question)
    except runner.Busy as e:
        raise HTTPException(429, str(e))
    background.add_task(runner.execute, run_id)
    return {"run_id": run_id, "status_url": f"/runs/{run_id}", "view_url": f"/runs/{run_id}/view"}


@app.get("/runs/{run_id}")
def get_run(run_id: str, after: int = 0):
    run = _run_or_404(run_id)
    stats = verified_stats(run_id)
    subqs = [
        {"id": s.id, "text": s.text, "status": s.status,
         "verified_claims": stats.get(s.id, {}).get("claims", 0),
         "domains": stats.get(s.id, {}).get("domains", 0)}
        for s in store.list_sub_questions(run_id)
    ]
    counts = Counter(c.verdict for c in store.list_claims(run_id))
    events = store.list_events(run_id, after_id=after)
    return {
        "id": run["id"], "question": run["question"], "status": run["status"],
        "loop_count": run["loop_count"], "error": run["error"],
        "created_at": run["created_at"], "finished_at": run["finished_at"],
        "sub_questions": subqs, "counts": dict(counts),
        "events": [e.model_dump() for e in events],
        "last_event_id": events[-1].id if events else after,
    }


@app.get("/runs/{run_id}/claims")
def get_claims(run_id: str):
    _run_or_404(run_id)
    evidence = {c.id: c for c in store.list_evidence(run_id)}
    return [
        {"id": c.id, "sub_q": c.sub_q_id, "text": c.text, "verdict": c.verdict,
         "quote": c.quote, "reason": c.reason, "round": c.round,
         "sources": [{"url": evidence[e].url, "domain": evidence[e].domain}
                     for e in c.evidence_ids if e in evidence]}
        for c in store.list_claims(run_id)
    ]


@app.get("/runs/{run_id}/report")
def get_report(run_id: str):
    run = _run_or_404(run_id)
    if not run["report_md"]:
        raise HTTPException(409, "Report not ready")
    return PlainTextResponse(run["report_md"], media_type="text/markdown; charset=utf-8")


# ---------- pages ----------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"runs": store.list_runs(8)})


@app.get("/runs/{run_id}/view", response_class=HTMLResponse)
def run_page(request: Request, run_id: str):
    _run_or_404(run_id)
    return templates.TemplateResponse(request, "run.html", {"run_id": run_id})


@app.get("/runs/{run_id}/report/view", response_class=HTMLResponse)
def report_page(request: Request, run_id: str):
    run = _run_or_404(run_id)
    if not run["report_md"]:
        raise HTTPException(409, "Report not ready")
    return templates.TemplateResponse(
        request, "report.html", {"run_id": run_id, "body": render_markdown(run["report_md"])}
    )