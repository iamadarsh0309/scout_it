from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config.settings import settings

_engine = create_engine(
    settings.database_url,
    future=True,
    pool_pre_ping=True,  # validates before use; avoids stale-connection errors against a
    # remote/pooled DB (e.g. Supabase's Supavisor) that can silently drop idle backends
    pool_recycle=1800,  # recycle connections older than 30 min, ahead of the pooler's own limits
    connect_args={"connect_timeout": 10},  # fail fast instead of hanging on network issues
    # If this ever points at Supabase's transaction pooler (port 6543) instead of the
    # session pooler, also add connect_args={"prepare_threshold": None} -- psycopg3's
    # server-side prepared statements aren't safe across transaction-mode pooling.
)
SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


def get_engine():
    return _engine


def get_session() -> Session:
    return SessionLocal()
