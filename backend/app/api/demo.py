"""Scenario time controls and deterministic chaos actions for local demos."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.core.auth import current_actor
from app.core.errors import ServiceError
from app.contracts.schemas import UTCDateTime
from app.demo.scenarios import (
    DemoScenarioError,
    add_shelter_walkin,
    advance_scenario,
    close_shelter,
    connect_demo_session,
    remove_shelter_capability,
    reset_scenario,
    silence_rider,
)
from app.models import User


router = APIRouter(prefix="/api/v1/demo/scenarios", tags=["demo"])


class DemoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdvanceScenarioRequest(DemoRequest):
    seconds: StrictInt = Field(gt=0, le=86_400)


class ShelterVersionRequest(DemoRequest):
    expected_shelter_version: StrictInt = Field(ge=0)


class CapabilityLossRequest(ShelterVersionRequest):
    verified_at: UTCDateTime


class WalkinRequest(DemoRequest):
    demand_id: UUID
    qty: StrictInt = Field(gt=0)
    expected_demand_version: StrictInt = Field(ge=0)


def _call(action):
    try:
        return action()
    except DemoScenarioError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except ServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code.value, "message": exc.message},
        ) from exc


@router.post("/connect")
def post_connect_demo(actor: User = Depends(current_actor)) -> dict[str, object]:
    return _call(lambda: connect_demo_session(actor.id))


@router.post("/{scenario_id}/advance")
def post_advance_scenario(
    scenario_id: UUID,
    body: AdvanceScenarioRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=180),
    actor: User = Depends(current_actor),
) -> dict[str, object]:
    return _call(
        lambda: advance_scenario(
            actor.id,
            scenario_id,
            seconds=body.seconds,
            idempotency_key=idempotency_key,
        )
    )


@router.post("/{scenario_id}/reset", status_code=status.HTTP_201_CREATED)
def post_reset_scenario(
    scenario_id: UUID,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=180),
    actor: User = Depends(current_actor),
) -> dict[str, object]:
    return _call(
        lambda: reset_scenario(
            actor.id, scenario_id, idempotency_key=idempotency_key
        )
    )


@router.post("/{scenario_id}/riders/{rider_id}/silent")
def post_rider_silent(
    scenario_id: UUID,
    rider_id: UUID,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=180),
    actor: User = Depends(current_actor),
) -> dict[str, object]:
    return _call(
        lambda: silence_rider(
            actor.id, scenario_id, rider_id, idempotency_key=idempotency_key
        )
    )


@router.post("/{scenario_id}/shelters/{shelter_id}/close")
def post_shelter_close(
    scenario_id: UUID,
    shelter_id: UUID,
    body: ShelterVersionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=180),
    actor: User = Depends(current_actor),
) -> dict[str, object]:
    return _call(
        lambda: close_shelter(
            actor.id,
            scenario_id,
            shelter_id,
            expected_shelter_version=body.expected_shelter_version,
            idempotency_key=idempotency_key,
        )
    )


@router.post("/{scenario_id}/shelters/{shelter_id}/walk-in")
def post_shelter_walkin(
    scenario_id: UUID,
    shelter_id: UUID,
    body: WalkinRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=180),
    actor: User = Depends(current_actor),
) -> dict[str, object]:
    return _call(
        lambda: add_shelter_walkin(
            actor.id,
            scenario_id,
            shelter_id,
            body.demand_id,
            qty=body.qty,
            expected_demand_version=body.expected_demand_version,
            idempotency_key=idempotency_key,
        )
    )


@router.post("/{scenario_id}/shelters/{shelter_id}/capabilities/{capability}/lose")
def post_capability_loss(
    scenario_id: UUID,
    shelter_id: UUID,
    capability: Literal[
        "clean_water",
        "cooking_fuel",
        "cooking_facility",
        "refrigeration",
        "trained_receiver",
        "authorized_recipient",
    ],
    body: CapabilityLossRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=180),
    actor: User = Depends(current_actor),
) -> dict[str, object]:
    return _call(
        lambda: remove_shelter_capability(
            actor.id,
            scenario_id,
            shelter_id,
            capability,
            expected_shelter_version=body.expected_shelter_version,
            verified_at=body.verified_at,
            idempotency_key=idempotency_key,
        )
    )
