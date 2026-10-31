import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.environ.get("ALASALAMA_DB", str(ROOT / "data" / "alasalama.db"))

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(_engine, "connect")
        def _pragmas(conn, _):
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return _engine


def init_db():
    from . import models  # noqa: F401  (register tables)

    SQLModel.metadata.create_all(get_engine())


def reset_db():
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None
    for suffix in ("", "-wal", "-shm"):
        Path(DB_PATH + suffix).unlink(missing_ok=True)
    init_db()


@contextmanager
def session():
    with Session(get_engine()) as s:
        yield s
