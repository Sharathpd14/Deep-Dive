import hashlib
import json
import re
import uuid
from urllib.parse import urlparse

from db.connection import get_conn
from models import Chunk, Claim, SubQuestion, TrailEvent, utcnow

# ---------- helpers ----------

def _next_id(conn, table: str, run_id: str, prefix: str) -> str:
    n = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    return f"{prefix}{n + 1}"


def _content_hash(text: str) -> str:
    norm = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.sha1(norm.encode()).hexdigest()


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host.removeprefix("www.")


# ---------- runs ----------

def create_run(question: str) -> str:
    run_id = uuid.uuid4().hex[:12]
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO runs (id, question, status, created_at) VALUES (?, ?, 'queued', ?)",
            (run_id, question, utcnow()),
        )
    return run_id


def update_run(run_id: str, **fields) -> None:
    allowed = {"status", "loop_count", "report_md", "error", "finished_at"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"Cannot update run fields: {bad}")
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE runs SET {cols} WHERE id = ?", (*fields.values(), run_id))


def get_run(run_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


# ---------- sub-questions ----------

def add_sub_question(run_id: str, text: str, queries: list[str]) -> SubQuestion:
    with get_conn() as conn:
        sq_id = _next_id(conn, "sub_questions", run_id, "Q")
        conn.execute(
            "INSERT INTO sub_questions (run_id, id, text, queries_json) VALUES (?, ?, ?, ?)",
            (run_id, sq_id, text, json.dumps(queries)),
        )
    return SubQuestion(id=sq_id, run_id=run_id, text=text, queries=queries)


def set_sub_question_status(run_id: str, sq_id: str, status: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE sub_questions SET status = ? WHERE run_id = ? AND id = ?",
            (status, run_id, sq_id),
        )


def set_sub_question_queries(run_id: str, sq_id: str, queries: list[str]) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE sub_questions SET queries_json = ? WHERE run_id = ? AND id = ?",
            (json.dumps(queries), run_id, sq_id),
        )


def list_sub_questions(run_id: str) -> list[SubQuestion]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sub_questions WHERE run_id = ? ORDER BY rowid", (run_id,)
        ).fetchall()
    return [
        SubQuestion(
            id=r["id"], run_id=r["run_id"], text=r["text"],
            queries=json.loads(r["queries_json"]), status=r["status"],
        )
        for r in rows
    ]


# ---------- evidence ----------

def add_evidence(
    run_id: str, sub_q_id: str | None, url: str, title: str, text: str, tier: float
) -> str | None:
    """Store a chunk. Returns its ID, or None if identical text already exists."""
    h = _content_hash(text)
    with get_conn() as conn:
        dup = conn.execute(
            "SELECT 1 FROM evidence WHERE run_id = ? AND content_hash = ?", (run_id, h)
        ).fetchone()
        if dup:
            return None
        ev_id = _next_id(conn, "evidence", run_id, "E")
        conn.execute(
            """INSERT INTO evidence
               (run_id, id, sub_q_id, url, title, domain, text, tier, content_hash, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, ev_id, sub_q_id, url, title, _domain(url), text, tier, h, utcnow()),
        )
    return ev_id


def _chunk(r) -> Chunk:
    return Chunk(
        id=r["id"], run_id=r["run_id"], sub_q_id=r["sub_q_id"], url=r["url"],
        title=r["title"], domain=r["domain"], text=r["text"], tier=r["tier"],
        fetched_at=r["fetched_at"],
    )


def get_chunks(run_id: str, ids: list[str]) -> list[Chunk]:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM evidence WHERE run_id = ? AND id IN ({marks})", (run_id, *ids)
        ).fetchall()
    return [_chunk(r) for r in rows]


def list_evidence(run_id: str, sub_q_id: str | None = None) -> list[Chunk]:
    q, args = "SELECT * FROM evidence WHERE run_id = ?", [run_id]
    if sub_q_id:
        q += " AND sub_q_id = ?"
        args.append(sub_q_id)
    with get_conn() as conn:
        rows = conn.execute(q + " ORDER BY rowid", args).fetchall()
    return [_chunk(r) for r in rows]


def evidence_urls(run_id: str) -> set[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT url FROM evidence WHERE run_id = ?", (run_id,)
        ).fetchall()
    return {r["url"] for r in rows}


# ---------- claims ----------

def save_claim(
    run_id: str, sub_q_id: str, text: str, evidence_ids: list[str], round_: int = 0
) -> Claim:
    with get_conn() as conn:
        cid = _next_id(conn, "claims", run_id, "C")
        conn.execute(
            """INSERT INTO claims (run_id, id, sub_q_id, text, evidence_ids_json, round)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, cid, sub_q_id, text, json.dumps(evidence_ids), round_),
        )
    return Claim(id=cid, run_id=run_id, sub_q_id=sub_q_id, text=text,
                 evidence_ids=evidence_ids, round=round_)


def set_claim_verdict(run_id: str, claim_id: str, verdict: str, quote: str, reason: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE claims SET verdict = ?, quote = ?, reason = ? WHERE run_id = ? AND id = ?",
            (verdict, quote, reason, run_id, claim_id),
        )


def list_claims(
    run_id: str, sub_q_id: str | None = None, verdict: str | None = None
) -> list[Claim]:
    q, args = "SELECT * FROM claims WHERE run_id = ?", [run_id]
    if sub_q_id:
        q += " AND sub_q_id = ?"
        args.append(sub_q_id)
    if verdict:
        q += " AND verdict = ?"
        args.append(verdict)
    with get_conn() as conn:
        rows = conn.execute(q + " ORDER BY rowid", args).fetchall()
    return [
        Claim(
            id=r["id"], run_id=r["run_id"], sub_q_id=r["sub_q_id"], text=r["text"],
            evidence_ids=json.loads(r["evidence_ids_json"]), verdict=r["verdict"],
            quote=r["quote"], reason=r["reason"], round=r["round"],
        )
        for r in rows
    ]


# ---------- trail ----------

def log_event(
    run_id: str, agent: str, action: str, reason: str = "", detail: dict | None = None
) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO trail_events (run_id, ts, agent, action, detail_json, reason)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, utcnow(), agent, action, json.dumps(detail or {}), reason),
        )


def list_events(run_id: str, after_id: int = 0) -> list[TrailEvent]:
    """after_id lets the UI poll for only the new events."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trail_events WHERE run_id = ? AND id > ? ORDER BY id",
            (run_id, after_id),
        ).fetchall()
    return [
        TrailEvent(
            id=r["id"], run_id=r["run_id"], ts=r["ts"], agent=r["agent"],
            action=r["action"], detail=json.loads(r["detail_json"]), reason=r["reason"],
        )
        for r in rows
    ]
    
def latest_run_id() -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM runs ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
    return row["id"] if row else None


# ---------- run management (used by the API) ----------

def count_active_runs() -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM runs WHERE status IN ('queued', 'running')"
        ).fetchone()[0]


def count_runs_since(since_iso: str) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM runs WHERE created_at >= ?", (since_iso,)
        ).fetchone()[0]


def fail_stale_runs() -> int:
    """At startup, nothing can still be running: mark leftovers as failed."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE runs SET status = 'failed', finished_at = ?, "
            "error = 'server restarted while the run was in progress' "
            "WHERE status IN ('queued', 'running')",
            (utcnow(),),
        )
        return cur.rowcount


def list_runs(limit: int = 8) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, question, status, created_at FROM runs "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]