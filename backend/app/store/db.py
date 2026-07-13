"""SQLite 存储层：引擎、会话与建表。"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.store.models import Base

_engine = None
_SessionLocal: sessionmaker | None = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{settings.db_path}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(_engine)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def db_session() -> Session:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖。"""
    session = db_session()
    try:
        yield session
    finally:
        session.close()
