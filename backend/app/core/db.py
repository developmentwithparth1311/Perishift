import random
import time
from collections.abc import Callable, Sequence
from enum import IntEnum
from pathlib import Path
from typing import TypeVar

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False, autoflush=False)

T = TypeVar("T")


class LockRank(IntEnum):
    """Global row-lock order; acquire a later rank only after all earlier ranks."""

    IDEMPOTENCY = 0
    SCENARIO = 1
    ACTOR = 2
    DONOR = 3
    BATCH = 4
    BATCH_INVENTORY = 5
    LOT = 6
    SHELTER = 7
    DEMAND = 8
    RIDER = 9
    OFFER = 10
    ASSIGNMENT = 11
    LEASE = 12
    EVENT_AND_SCHEDULE = 13


class LockOrder:
    """Tracks lock acquisition order for a single service transaction."""

    def __init__(self) -> None:
        self._last_rank = -1
        self._last_ids: tuple[str, ...] = ()

    def acquire(self, rank: LockRank, ids: Sequence[object]) -> tuple[object, ...]:
        ordered_values = tuple(sorted(ids, key=str))
        ordered = tuple(str(value) for value in ordered_values)
        if int(rank) < self._last_rank:
            raise ValueError(f"lock order violation: {rank.name} acquired after a later rank")
        if int(rank) == self._last_rank and ordered and self._last_ids and ordered[0] < self._last_ids[-1]:
            raise ValueError(f"lock order violation: {rank.name} rows must be acquired by sorted id")
        self._last_rank = int(rank)
        self._last_ids = ordered
        return ordered_values


def lock_rows(
    session: Session,
    model: type[T],
    rank: LockRank,
    ids: Sequence[object],
    lock_order: LockOrder,
) -> list[T]:
    """Lock only the requested rows, in stable UUID order, for this transaction."""
    keys = tuple(lock_order.acquire(rank, ids))
    if not keys:
        return []
    primary_key = inspect(model).primary_key
    if len(primary_key) != 1:
        raise ValueError("lock_rows supports one-column primary keys only")
    key_column = primary_key[0]
    statement = select(model).where(key_column.in_(keys)).order_by(key_column).with_for_update()
    return list(session.scalars(statement))


def run_transaction(operation: Callable[[Session], T], *, attempts: int = 4) -> T:
    """Run a whole transaction with bounded retries for PostgreSQL deadlocks/serialization."""
    if attempts < 1:
        raise ValueError("attempts must be at least one")
    for attempt in range(attempts):
        try:
            with SessionLocal.begin() as session:
                return operation(session)
        except DBAPIError as exc:
            sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
            if sqlstate not in {"40001", "40P01"} or attempt + 1 >= attempts:
                raise
            time.sleep(random.uniform(0.005, 0.025) * (2**attempt))
    raise AssertionError("unreachable")


def get_db():
    """FastAPI dependency that closes sessions and leaves transaction ownership to services."""
    with SessionLocal() as session:
        yield session


def database_is_ready() -> bool:
    """Return whether PostgreSQL is reachable and migrated to the current head."""
    try:
        config_path = Path(__file__).resolve().parents[2] / "alembic.ini"
        config = Config(str(config_path))
        config.set_main_option("script_location", str(config_path.parent / "alembic"))
        expected_revision = ScriptDirectory.from_config(config).get_current_head()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            current_revision = MigrationContext.configure(connection).get_current_revision()
        if current_revision != expected_revision:
            return False
    except (SQLAlchemyError, OSError, ValueError):
        return False
    return True
