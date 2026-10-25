"""PostgreSQL acceptance tests for the inventory reconciliation report."""

import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema

from app.demo import seed
from app.domain.reconciliation import reconcile_inventory
from app.models import Base, BatchInventory, Demand
from app.services import commitment, custody, resources_shelter


@pytest.fixture
def reconciliation_database(monkeypatch):
    database_url = os.environ.get("PERISHIFT_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PERISHIFT_TEST_DATABASE_URL is required for PostgreSQL integration tests")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        transaction = connection.begin()
        schema_name = "reconcile_test_" + uuid4().hex
        connection.execute(CreateSchema(schema_name))
        connection = connection.execution_options(schema_translate_map={None: schema_name})
        Base.metadata.create_all(connection)
        with Session(bind=connection, autoflush=False, expire_on_commit=False) as session:
            for module in (seed, commitment, custody, resources_shelter):
                monkeypatch.setattr(module, "run_transaction", lambda operation: operation(session))
            try:
                yield session, seed.reset_demo_scenario()
            finally:
                if transaction.is_active:
                    transaction.rollback()
    engine.dispose()


def test_seeded_ledgers_reconcile_cleanly(reconciliation_database):
    session, scenario = reconciliation_database

    report = reconcile_inventory(session, scenario_id=scenario.scenario_id)

    assert report["clean"] is True
    assert report["checked_batches"] == 1
    assert report["checked_demands"] == 1
    assert report["discrepancies"] == []


def test_partial_pickup_and_rejected_drop_keep_custody_and_commitments_distinct(reconciliation_database):
    session, scenario = reconciliation_database
    offer = commitment.publish_offer(
        actor_id=scenario.coordinator_actor_id,
        batch_id=scenario.batch_id,
        demand_id=scenario.demand_id,
        rider_id=scenario.rider_id,
        quantity=20,
        expires_at=scenario.scenario_time + timedelta(minutes=3),
        expected_batch_version=0,
        expected_demand_version=0,
        expected_rider_version=0,
        idempotency_key="reconcile-publish",
    )
    accepted = commitment.accept_lot(
        actor_id=scenario.rider_actor_id,
        lot_id=offer["lot_id"],
        rider_id=scenario.rider_id,
        expected_version=offer["lot_version"],
        idempotency_key="reconcile-accept",
    )
    picked = custody.confirm_pickup(
        actor_id=scenario.rider_actor_id,
        lot_id=offer["lot_id"],
        picked_quantity=15,
        expected_version=accepted["version"],
        idempotency_key="reconcile-pickup",
    )
    custody.confirm_delivery(
        actor_id=scenario.rider_actor_id,
        lot_id=offer["lot_id"],
        accepted_quantity=5,
        rejected_quantity=2,
        expected_version=picked["lot_version"],
        idempotency_key="reconcile-partial-delivery",
    )
    session.flush()

    report = reconcile_inventory(session, scenario_id=scenario.scenario_id)

    assert report["clean"] is True
    assert report["discrepancies"] == []
    inventory = session.get(BatchInventory, scenario.batch_id)
    demand = session.scalar(select(Demand).where(Demand.id == scenario.demand_id))
    assert inventory.carried == 10  # Includes rejected cargo still physically held.
    assert demand.transit_qty == 8  # Rejected units are no longer committed to this demand.

    inventory.carried += 1
    demand.transit_qty += 1
    session.flush()
    drifted = reconcile_inventory(session, scenario_id=scenario.scenario_id)
    drift_codes = {item["code"] for item in drifted["discrepancies"]}
    assert "BATCH_CARRIED_CUSTODY_MISMATCH" in drift_codes
    assert "DEMAND_TRANSIT_COMMITMENT_MISMATCH" in drift_codes


def test_corrupt_batch_and_demand_ledgers_are_reported(reconciliation_database):
    session, scenario = reconciliation_database
    offer = commitment.publish_offer(
        actor_id=scenario.coordinator_actor_id,
        batch_id=scenario.batch_id,
        demand_id=scenario.demand_id,
        rider_id=scenario.rider_id,
        quantity=20,
        expires_at=scenario.scenario_time + timedelta(minutes=3),
        expected_batch_version=0,
        expected_demand_version=0,
        expected_rider_version=0,
        idempotency_key="reconcile-corrupt-publish",
    )
    commitment.accept_lot(
        actor_id=scenario.rider_actor_id,
        lot_id=offer["lot_id"],
        rider_id=scenario.rider_id,
        expected_version=offer["lot_version"],
        idempotency_key="reconcile-corrupt-accept",
    )
    inventory = session.get(BatchInventory, scenario.batch_id)
    demand = session.scalar(select(Demand).where(Demand.id == scenario.demand_id))
    inventory.delivered += 1
    inventory.reserved += 1
    demand.reserved_qty += 3
    session.flush()

    report = reconcile_inventory(session, scenario_id=scenario.scenario_id)
    codes = {item["code"] for item in report["discrepancies"]}

    assert report["clean"] is False
    assert "BATCH_CONSERVATION_MISMATCH" in codes
    assert "BATCH_RESERVED_CUSTODY_MISMATCH" in codes
    assert "DEMAND_RESERVED_COMMITMENT_MISMATCH" in codes


def test_walkin_overcommitment_is_a_grounded_warning_not_ledger_drift(reconciliation_database):
    session, scenario = reconciliation_database
    demand = session.scalar(select(Demand).where(Demand.id == scenario.demand_id))
    resources_shelter.record_walkin(
        scenario.shelter_actor_id,
        scenario.shelter_id,
        scenario.demand_id,
        qty=25,
        expected_version=demand.version,
        idempotency_key="reconcile-walkin-overcommit",
    )
    session.flush()

    report = reconcile_inventory(session, scenario_id=scenario.scenario_id)

    assert report["clean"] is True
    assert report["discrepancies"] == []
    assert report["warnings"] == [{
        "code": "EXTERNAL_WALKIN_OVERCOMMITMENT",
        "entity_type": "demand",
        "entity_id": str(scenario.demand_id),
        "overcommitment_qty": 5,
        "walkin_received_qty": 25,
        "message": "Recorded walk-in receipts explain this portion of demand overcommitment.",
    }]
