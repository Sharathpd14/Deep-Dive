from db import store


def log(run_id: str, agent: str, action: str, reason: str = "", **detail) -> None:
    store.log_event(run_id, agent, action, reason=reason, detail=detail)