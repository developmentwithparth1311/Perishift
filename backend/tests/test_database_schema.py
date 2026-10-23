import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from app.core.db import LockOrder, LockRank
from app.models import Base


BACKEND = Path(__file__).resolve().parents[1]


def test_schema_contains_authoritative_tables_and_constraints() -> None:
    assert {
        "scenarios", "users", "donors", "riders", "shelters", "demands",
        "batches", "batch_inventory", "lots", "offers", "assignments",
        "leases", "events", "scheduled_actions", "idempotency_records",
    } == set(Base.metadata.tables)

    inventory = Base.metadata.tables["batch_inventory"]
    assert "ck_batch_inventory_nonnegative_buckets" in {c.name for c in inventory.constraints}
    assignments = Base.metadata.tables["assignments"]
    assert "ck_assignments_delivery_within_pickup" in {c.name for c in assignments.constraints}
    assert "uq_assignments_active_rider" in {i.name for i in assignments.indexes}


def test_scenario_scoped_dedupe_indexes_cover_live_and_simulated_rows() -> None:
    expected = {
        "uq_scheduled_actions_dedupe",
        "uq_idempotency_scope_key",
        "uq_demands_window",
    }
    indexes = {
        index.name: str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        for table in Base.metadata.tables.values()
        for index in table.indexes
    }
    for name in expected:
        assert "NULLS NOT DISTINCT" in indexes[name]


def test_migration_renders_postgresql_schema_and_deferred_safety_triggers() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    sql = result.stdout
    assert "CREATE TABLE batch_inventory" in sql
    assert "uq_assignments_active_rider" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert "events_append_only" in sql
    assert "NULLS NOT DISTINCT" in sql


def test_global_lock_order_is_sorted_and_rejects_inversions() -> None:
    order = LockOrder()
    assert order.acquire(LockRank.BATCH, ["b", "a"]) == ("a", "b")
    order.acquire(LockRank.BATCH_INVENTORY, ["a"])
    with pytest.raises(ValueError, match="lock order violation"):
        order.acquire(LockRank.BATCH, ["c"])

    same_rank = LockOrder()
    same_rank.acquire(LockRank.DEMAND, ["demand-1"])
    with pytest.raises(ValueError, match="sorted id"):
        same_rank.acquire(LockRank.DEMAND, ["demand-0"])
