import os

import pytest

os.environ["ALASALAMA_NO_LLM"] = "1"


@pytest.fixture(autouse=True)
def _llm_mode(request, monkeypatch):
    """Offline by default; integration tests use the real API (needs ANTHROPIC_API_KEY)."""
    for var in ("TELEGRAM_BOT_TOKEN", "ALASALAMA_TELEGRAM_MAP", "ALASALAMA_ONLY_PATIENTS", "ALASALAMA_TRACE",
                "ALASALAMA_TELEGRAM_OFFSET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ALASALAMA_CHANNEL", "webchat")
    if request.node.get_closest_marker("integration"):
        monkeypatch.delenv("ALASALAMA_NO_LLM", raising=False)


@pytest.fixture
def db(tmp_path, monkeypatch):
    from alasalama import db as dbm

    monkeypatch.setattr(dbm, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(dbm, "_engine", None)
    from alasalama.seed import load_seed

    load_seed()
    yield dbm
    dbm._engine.dispose()
    monkeypatch.setattr(dbm, "_engine", None)
