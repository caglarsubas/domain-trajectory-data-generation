from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

SessionLocal = sessionmaker(autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    if url.startswith("sqlite"):
        return create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(url, pool_pre_ping=True)


def init_db(url: str) -> None:
    engine = make_engine(url)
    SessionLocal.configure(bind=engine)
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _add_missing_columns(engine)


# Columns added after a table first shipped. create_all makes new tables but never alters old ones.
ADDED_COLUMNS = {"runs": {"generation": "JSON"}}


def _add_missing_columns(engine) -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, columns in ADDED_COLUMNS.items():
            if not inspector.has_table(table):
                continue
            present = {column["name"] for column in inspector.get_columns(table)}
            for name, kind in columns.items():
                if name not in present:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {kind}"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
