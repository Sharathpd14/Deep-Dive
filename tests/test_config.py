import pytest
from pydantic import ValidationError

import config


def test_blank_or_placeholder_key_is_rejected(monkeypatch):
    for bad in ("", "   ", "rsk_replace_me"):
        monkeypatch.setenv("MESH_API_KEY", bad)
        with pytest.raises(ValidationError):
            config.Settings(_env_file=None)


def test_startup_stops_with_a_clear_message(monkeypatch):
    monkeypatch.setenv("MESH_API_KEY", "")
    with pytest.raises(SystemExit) as exc:
        config.load_settings()
    assert "MESH_API_KEY" in str(exc.value)