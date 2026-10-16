import json
import sys
from functools import lru_cache
from pathlib import Path

from db import store

DEMO_FILE = Path(__file__).parent / "demo_data" / "demo_run.json"


def export(run_id: str, path: Path | None = None) -> Path:
    path = path or DEMO_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store.export_run(run_id), indent=1), encoding="utf-8")
    return path


def ensure_demo(path: Path | None = None) -> str | None:
    """Load the bundled example run into the database (a no-op if it is already there)."""
    path = path or DEMO_FILE
    if not path.exists():
        return None
    return store.import_run(json.loads(path.read_text(encoding="utf-8")))


@lru_cache
def _demo_id(path: Path) -> str:
    return json.loads(path.read_text(encoding="utf-8"))["run"]["id"]


def demo_run(path: Path | None = None) -> dict | None:
    path = path or DEMO_FILE
    if not path.exists():
        return None
    run = store.get_run(_demo_id(path))
    return {"id": run["id"], "question": run["question"]} if run else None


if __name__ == "__main__":
    # uv run python -m seed e344146501b8
    print(f"Wrote {export(sys.argv[1])}")