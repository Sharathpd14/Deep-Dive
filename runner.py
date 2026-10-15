import threading
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from config import settings
from db import store
from graph import run_pipeline

_lock = threading.Lock()


class Busy(Exception):
    """Raised when a limit is hit. The API turns it into HTTP 429."""


def start(question: str) -> str:
    """Check the limits and create a queued run, atomically."""
    with _lock:
        if store.count_active_runs() >= settings.max_concurrent_runs:
            raise Busy("The server is busy with other runs. Please try again in a minute.")
        since = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="seconds")
        if store.count_runs_since(since) >= settings.max_runs_per_hour:
            raise Busy("Hourly run limit reached. Please try again later.")
        return store.create_run(question)


def execute(run_id: str) -> None:
    """Runs in a background thread. Failures are already saved on the run and in the trail."""
    with suppress(Exception):
        run_pipeline(run_id=run_id)