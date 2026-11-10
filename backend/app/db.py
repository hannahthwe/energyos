"""SQLite engine and session factory.

One SQLite file holds every simulated system, each in its own prefixed tables
with its own native schema:

    crm_*      HelioHEMS CRM
    hems_*     HelioHEMS energy backend
    chg_*      third-party charger platform, as exposed through the authorised integration
    kb_*       approved knowledge base
    audit_*    configuration audit log
    cc_*       ConnectedCare canonical model + support workflow
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DEFAULT_DB = Path(__file__).resolve().parent.parent / "connectedcare.db"


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None):
    url = url or os.environ.get("CONNECTEDCARE_DB_URL") or f"sqlite:///{DEFAULT_DB}"
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):  # pragma: no cover - sqlite pragma
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def configure(url: str) -> None:
    """Point the app at another database (used by tests)."""
    global engine
    engine = make_engine(url)
    SessionLocal.configure(bind=engine)


@contextmanager
def session_scope() -> Session:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
