"""Exercise custody against PostgreSQL in schemas rolled back after each test."""

import os
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema

from app.demo import scenarios, seed
from app.core.auth import current_actor
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import ServiceError
from app.models import Base, BatchInventory, Demand, Donor, Rider, Scenario, Shelter, User
from app.services import commitment, custody, resources, resources_shelter


@pytest.fixture
def custody_database(monkeypatch):
    database_url = os.environ.get("PERISHIFT_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PERISHIFT_TEST_DATABASE_URL is required for PostgreSQL integration tests")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        transaction = connection.begin()
        schema_name = "custody_test_" + uuid4().hex
        connection.execute(CreateSchema(schema_name))
        connection = connection.execution_options(schema_translate_map={None: schema_name})
        Base.metadata.create_all(connection)
        with Session(bind=connection, autoflush=False, expire_on_commit=False) as session:
            # Keep all service transactions inside the test's rollback boundary.
            for module in (seed, commitment, custody, scenarios, resources, resources_shelter):
                monkeypatch.setattr(module, "run_transaction", lambda operation: operation(session))
            try:
                yield session, seed.reset_demo_scenario()
            finally:
                if transaction.is_active:
                    transaction.rollback()
    engine.dispose()


@pytest.mark.parametrize("picked_quantity", [20, 15])
def test_pickup_and_delivery_preserve_stock_and_demand(custody_database, picked_quantity):
    session, scenario = custody_database
    assert custody.get_active_dispatch(actor_id=scenario.coordinator_actor_id, rider_id=scenario.rider_id) is None
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
        idempotency_key="test-publish",
    )
    offered = custody.get_active_dispatch(actor_id=scenario.rider_actor_id, rider_id=scenario.rider_id)
    assert offered["state"] == "OFFERED"
    assert offered["lot_id"] == offer["lot_id"]
    accepted = commitment.accept_lot(
        actor_id=scenario.rider_actor_id,
        lot_id=offer["lot_id"],
        rider_id=scenario.rider_id,
        expected_version=offer["lot_version"],
        idempotency_key="test-accept",
    )
    resumed = custody.get_active_dispatch(actor_id=scenario.coordinator_actor_id, rider_id=scenario.rider_id)
    assert resumed["state"] == "LEASED"
    assert resumed["lot_version"] == accepted["version"]
    assert resumed["lease_expires_at"] is not None
    pickup_arguments = {
        "actor_id": scenario.rider_actor_id,
        "lot_id": offer["lot_id"],
        "picked_quantity": picked_quantity,
        "expected_version": accepted["version"],
        "idempotency_key": "test-pickup",
    }
    picked = custody.confirm_pickup(**pickup_arguments)
    replayed = custody.confirm_pickup(**pickup_arguments)

    assert picked["lot_state"] == "IN_TRANSIT"
    assert picked["carried_qty"] == picked_quantity
    assert picked["inventory"]["available"] == 20 - picked_quantity
    assert picked["inventory"]["reserved"] == 0
    assert picked["demand"]["reserved"] == 0
    assert picked["demand"]["transit"] == picked_quantity
    assert replayed["lot_version"] == picked["lot_version"]
    resumed = custody.get_active_dispatch(actor_id=scenario.rider_actor_id, rider_id=scenario.rider_id)
    assert resumed["state"] == "IN_TRANSIT"
    assert resumed["carried_qty"] == picked_quantity
    assert resumed["lot_version"] == picked["lot_version"]

    delivered = custody.confirm_delivery(
        actor_id=scenario.rider_actor_id,
        lot_id=offer["lot_id"],
        accepted_quantity=picked_quantity,
        rejected_quantity=0,
        expected_version=picked["lot_version"],
        idempotency_key="test-deliver",
    )
    assert delivered["lot_state"] == "DELIVERED"
    assert delivered["carried_qty"] == 0
    assert delivered["rider_available"] is True

    inventory = session.get(BatchInventory, scenario.batch_id)
    demand = session.scalar(select(Demand).where(Demand.id == scenario.demand_id))
    assert inventory.available == 20 - picked_quantity
    assert inventory.reserved == inventory.carried == inventory.disposed == 0
    assert inventory.delivered == picked_quantity
    assert sum((inventory.available, inventory.reserved, inventory.carried, inventory.delivered, inventory.disposed)) == 20
    assert demand.reserved_qty == demand.transit_qty == 0
    assert demand.received_qty == picked_quantity
    assert custody.get_active_dispatch(actor_id=scenario.coordinator_actor_id, rider_id=scenario.rider_id) is None


def test_resuming_dispatch_rejects_other_roles_riders_and_scenarios(custody_database):
    session, scenario = custody_database
    with pytest.raises(ServiceError) as forbidden_role:
        custody.get_active_dispatch(actor_id=scenario.donor_actor_id, rider_id=scenario.rider_id)
    assert forbidden_role.value.status_code == 403

    other_rider_actor = User(id=uuid4(), scenario_id=scenario.scenario_id, role="RIDER", display_name="Other rider", active=True)
    other_scenario_actor = User(id=uuid4(), scenario_id=None, role="COORDINATOR", display_name="Other scenario", active=True)
    session.add_all([other_rider_actor, other_scenario_actor])
    session.flush()
    with pytest.raises(ServiceError) as forbidden_rider:
        custody.get_active_dispatch(actor_id=other_rider_actor.id, rider_id=scenario.rider_id)
    assert forbidden_rider.value.status_code == 403
    with pytest.raises(ServiceError) as other_scenario:
        custody.get_active_dispatch(actor_id=other_scenario_actor.id, rider_id=scenario.rider_id)
    assert other_scenario.value.status_code == 404


def test_dispatch_map_returns_recorded_locations_and_restricts_access(custody_database):
    session, scenario = custody_database
    arguments = dict(rider_id=scenario.rider_id, batch_id=scenario.batch_id, demand_id=scenario.demand_id)
    route = custody.get_dispatch_map(actor_id=scenario.coordinator_actor_id, **arguments)
    donor = session.get(Donor, scenario.donor_id)
    demand = session.get(Demand, scenario.demand_id)
    shelter = session.get(Shelter, demand.shelter_id)
    assert route["simulated"] is True
    assert route["lot_id"] is route["state"] is None
    assert route["pickup"]["latitude"] == donor.latitude
    assert route["pickup"]["longitude"] == donor.longitude
    assert route["destination"] == {"name": shelter.name, "latitude": shelter.latitude, "longitude": shelter.longitude}
    assert custody.get_dispatch_map(actor_id=scenario.rider_actor_id, **arguments) == route
    with pytest.raises(ServiceError) as forbidden:
        custody.get_dispatch_map(actor_id=scenario.donor_actor_id, **arguments)
    assert forbidden.value.status_code == 403
    outsider = User(id=uuid4(), role="COORDINATOR", display_name="Other scenario", active=True)
    other_rider = User(id=uuid4(), scenario_id=scenario.scenario_id, role="RIDER", display_name="Other rider", active=True)
    session.add_all([outsider, other_rider])
    session.flush()
    for actor_id, status in ((outsider.id, 404), (other_rider.id, 403), (uuid4(), 401)):
        with pytest.raises(ServiceError) as denied:
            custody.get_dispatch_map(actor_id=actor_id, **arguments)
        assert denied.value.status_code == status
    for changed in ({"batch_id": uuid4()}, {"demand_id": uuid4()}, {"lot_id": uuid4()}, {"rider_id": uuid4()}):
        with pytest.raises(ServiceError) as missing:
            custody.get_dispatch_map(actor_id=scenario.coordinator_actor_id, **(arguments | changed))
        assert missing.value.status_code == 404
    rider = session.get(Rider, scenario.rider_id)
    rider.latitude = rider.longitude = None
    session.flush()
    fallback = custody.get_dispatch_map(actor_id=scenario.coordinator_actor_id, **arguments)
    assert fallback["rider"]["location_known"] is False
    assert fallback["rider"]["latitude"] == donor.latitude
    assert fallback["rider"]["longitude"] == donor.longitude
    session.get(Scenario, scenario.scenario_id).active = False
    session.flush()
    with pytest.raises(ServiceError) as retired:
        custody.get_dispatch_map(actor_id=scenario.coordinator_actor_id, **arguments)
    assert retired.value.status_code == 404


def test_nonexpiring_credentials_require_their_matching_simulated_scenario(custody_database, monkeypatch):
    session, scenario = custody_database
    monkeypatch.setattr(settings, "demo_mode", True)
    tokens, expires = seed.issue_demo_bearer_tokens(scenario)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=tokens["rider"])
    assert expires is None
    assert current_actor(credentials=credentials, session=session).id == scenario.rider_actor_id

    scenario_row = session.get(Scenario, scenario.scenario_id)
    scenario_row.mode = "LIVE"
    with pytest.raises(HTTPException) as live_rejected:
        current_actor(credentials=credentials, session=session)
    assert live_rejected.value.status_code == 401

    scenario_row.mode = "SIMULATED"
    scenario_row.active = False
    # Retired actors may replay a completed reset; new commands still require an active scenario.
    assert current_actor(credentials=credentials, session=session).id == scenario.rider_actor_id
    with pytest.raises(scenarios.DemoScenarioError):
        scenarios._authorized_scenario(session, scenario.coordinator_actor_id, scenario.scenario_id)

    scenario_row.active = True
    actor = session.get(User, scenario.rider_actor_id)
    actor.scenario_id = None
    with pytest.raises(HTTPException) as wrong_scenario_rejected:
        current_actor(credentials=credentials, session=session)
    assert wrong_scenario_rejected.value.status_code == 401

    actor.scenario_id = scenario.scenario_id
    timed_tokens, _ = seed.issue_demo_bearer_tokens(scenario, lifetime=timedelta(minutes=15))
    monkeypatch.setattr(settings, "demo_mode", False)
    normal_credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=timed_tokens["rider"])
    assert current_actor(credentials=normal_credentials, session=session).id == scenario.rider_actor_id


def test_reset_returns_nonexpiring_credentials_and_replays_without_another_reset(custody_database, monkeypatch):
    session, scenario = custody_database
    monkeypatch.setattr(settings, "demo_mode", True)
    output = scenarios.reset_scenario(scenario.coordinator_actor_id, scenario.scenario_id, idempotency_key="test-reset")
    session.flush()
    assert output["token_expires_at"] is None
    assert output["scenario"]["scenario_id"] != str(scenario.scenario_id)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=output["bearer_tokens"]["coordinator"])
    assert str(current_actor(credentials=credentials, session=session).scenario_id) == output["scenario"]["scenario_id"]
    assert scenarios.reset_scenario(scenario.coordinator_actor_id, scenario.scenario_id, idempotency_key="test-reset") == output
    with pytest.raises(ServiceError) as already_reset:
        seed.reset_demo_scenario(session=session, replacing_scenario_id=scenario.scenario_id)
    assert already_reset.value.status_code == 409
    assert session.get(Scenario, UUID(output["scenario"]["scenario_id"])).active is True


def test_three_complete_http_deliveries_each_reset_to_fresh_supply_and_demand(custody_database, monkeypatch):
    from app.api.batches import router as batches_router
    from app.api.demo import router as demo_router
    from app.api.lots import router as lots_router
    from app.api.shelters import router as shelters_router

    session, scenario = custody_database
    monkeypatch.setattr(settings, "demo_mode", True)
    app = FastAPI()
    for router in (batches_router, demo_router, lots_router, shelters_router):
        app.include_router(router)
    app.dependency_overrides[get_db] = lambda: session
    tokens, _ = seed.issue_demo_bearer_tokens(scenario)
    seed_data = scenario.to_dict()
    bootstrap_headers = {"Authorization": f"Bearer {tokens['coordinator']}"}

    with TestClient(app) as client:
        for round_number in range(3):
            connected = client.post("/api/v1/demo/scenarios/connect", headers=bootstrap_headers)
            assert connected.status_code == 200, connected.text
            assert connected.json()["scenario"]["scenario_id"] == seed_data["scenario_id"]
            assert connected.json()["token_expires_at"] is None
            tokens = connected.json()["bearer_tokens"]
            coordinator_headers = {"Authorization": f"Bearer {tokens['coordinator']}", "Idempotency-Key": f"publish-{round_number}"}
            rider_headers = {"Authorization": f"Bearer {tokens['rider']}", "Idempotency-Key": f"accept-{round_number}"}
            shelter_headers = {"Authorization": f"Bearer {tokens['shelter']}"}
            preview_params = {key: seed_data[key] for key in ("batch_id", "demand_id", "rider_id")}
            preview = client.get("/api/v1/dispatch/map", params=preview_params, headers=coordinator_headers)
            assert preview.status_code == 200, preview.text
            assert preview.json()["simulated"] is True
            assert preview.json()["state"] is None
            assert client.get("/api/v1/dispatch/map", params=preview_params, headers=shelter_headers).status_code == 403
            offer_response = client.post("/api/v1/dispatch/commit", headers=coordinator_headers, json={
                "batch_id": seed_data["batch_id"], "demand_id": seed_data["demand_id"], "rider_id": seed_data["rider_id"],
                "quantity": 20, "expected_batch_version": 0, "expected_demand_version": 0, "expected_rider_version": 0,
                "expires_at": (datetime.fromisoformat(seed_data["scenario_time"]) + timedelta(minutes=3)).isoformat(),
            })
            assert offer_response.status_code == 201, offer_response.text
            offer = offer_response.json()
            lot_url = f"/api/v1/lots/{offer['lot_id']}"
            accepted = client.post(f"{lot_url}/accept", headers=rider_headers, json={"rider_id": seed_data["rider_id"], "expected_version": offer["lot_version"]})
            assert accepted.status_code == 200, accepted.text
            reconnected = client.post("/api/v1/demo/scenarios/connect", headers=bootstrap_headers)
            assert reconnected.status_code == 200, reconnected.text
            assert reconnected.json()["active_dispatch"]["state"] == "LEASED"
            assert reconnected.json()["active_dispatch"]["lot_id"] == offer["lot_id"]
            rider_headers["Idempotency-Key"] = f"pickup-{round_number}"
            picked = client.post(f"{lot_url}/pickup", headers=rider_headers, json={"picked_qty": 20, "expected_version": accepted.json()["version"]})
            assert picked.status_code == 200, picked.text
            # A committed route follows its lot even when unrelated selectors change.
            map_params = {"rider_id": seed_data["rider_id"], "lot_id": offer["lot_id"], "batch_id": str(uuid4()), "demand_id": str(uuid4())}
            in_transit_map = client.get("/api/v1/dispatch/map", params=map_params, headers=rider_headers)
            assert in_transit_map.status_code == 200, in_transit_map.text
            assert in_transit_map.json()["state"] == "IN_TRANSIT"
            assert in_transit_map.json()["batch_id"] == seed_data["batch_id"]
            assert in_transit_map.json()["destination"] == preview.json()["destination"]
            rider_headers["Idempotency-Key"] = f"deliver-{round_number}"
            delivered = client.post(f"{lot_url}/deliver", headers=rider_headers, json={"accepted_qty": 20, "rejected_qty": 0, "expected_version": picked.json()["lot_version"]})
            assert delivered.status_code == 200, delivered.text
            assert delivered.json()["lot_state"] == "DELIVERED"
            assert client.get("/api/v1/dispatch/map", params=map_params, headers=coordinator_headers).json()["state"] == "DELIVERED"
            assert client.get("/demands", headers=shelter_headers).json()[0]["uncommitted_need"] == 0
            completed = client.post("/api/v1/demo/scenarios/connect", headers=bootstrap_headers)
            assert completed.status_code == 200, completed.text
            assert completed.json()["active_dispatch"] is None
            assert completed.json()["scenario"]["batch_balance"]["delivered_qty"] == 20

            old_demand_id = seed_data["demand_id"]
            reset_url = f"/api/v1/demo/scenarios/{seed_data['scenario_id']}/reset"
            coordinator_headers["Idempotency-Key"] = f"reset-{round_number}"
            reset_response = client.post(reset_url, headers=coordinator_headers)
            assert reset_response.status_code == 201, reset_response.text
            reset = reset_response.json()
            assert reset["token_expires_at"] is None
            replay = client.post(reset_url, headers=coordinator_headers)
            assert replay.status_code == 201 and replay.json() == reset
            assert session.get(Demand, UUID(old_demand_id)).received_qty == 20

            seed_data = reset["scenario"]
            tokens = reset["bearer_tokens"]
            batches = client.get(f"/api/v1/batches?scenario_id={seed_data['scenario_id']}", headers={"Authorization": f"Bearer {tokens['coordinator']}"})
            demands = client.get("/demands", headers={"Authorization": f"Bearer {tokens['shelter']}"})
            assert batches.status_code == demands.status_code == 200
            assert len(batches.json()) == len(demands.json()) == 1
            assert batches.json()[0]["available_qty"] == 20
            assert demands.json()[0]["uncommitted_need"] == 20


def test_demo_connect_requires_a_simulated_coordinator_and_demo_mode(custody_database, monkeypatch):
    session, scenario = custody_database
    monkeypatch.setattr(settings, "demo_mode", True)
    with pytest.raises(scenarios.DemoScenarioError) as donor:
        scenarios.connect_demo_session(scenario.donor_actor_id)
    assert donor.value.status_code == 403
    monkeypatch.setattr(settings, "demo_mode", False)
    with pytest.raises(scenarios.DemoScenarioError) as disabled:
        scenarios.connect_demo_session(scenario.coordinator_actor_id)
    assert disabled.value.status_code == 404
    monkeypatch.setattr(settings, "demo_mode", True)
    session.get(Scenario, scenario.scenario_id).mode = "LIVE"
    with pytest.raises(scenarios.DemoScenarioError) as live:
        scenarios.connect_demo_session(scenario.coordinator_actor_id)
    assert live.value.status_code == 404


def test_demo_connect_creates_one_run_when_only_retired_runs_exist(custody_database, monkeypatch):
    session, scenario = custody_database
    monkeypatch.setattr(settings, "demo_mode", True)
    session.get(Scenario, scenario.scenario_id).active = False
    session.flush()
    connected = scenarios.connect_demo_session(scenario.coordinator_actor_id)
    replay = scenarios.connect_demo_session(scenario.coordinator_actor_id)
    assert connected["scenario"]["scenario_id"] != str(scenario.scenario_id)
    assert connected["scenario"]["scenario_id"] == replay["scenario"]["scenario_id"]
    assert connected["scenario"]["batch_balance"]["available_qty"] == 20
