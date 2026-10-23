"""Recovery command endpoints; mounted by the application integrator."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.core.auth import current_actor
from app.core.errors import ServiceError
from app.models import User
from app.services.recovery import reroute_in_transit


router = APIRouter(prefix="/api/v1", tags=["recovery"])


class RerouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_lot_version: StrictInt = Field(ge=0)
    expected_assignment_version: StrictInt = Field(ge=0)
    expected_current_demand_version: StrictInt = Field(ge=0)


class PartialOption(BaseModel):
    demand_id: UUID
    label: str
    available_quantity: int = Field(ge=0)
    carried_quantity: int = Field(gt=0)
    reason_codes: list[str]
    explanation: str


class RerouteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    lot_id: UUID
    lot_state: str
    lot_version: int = Field(ge=0)
    assignment_id: UUID
    assignment_status: str
    assignment_version: int = Field(ge=0)
    from_demand_id: UUID
    to_demand_id: UUID | None
    carried_qty: int = Field(ge=0)
    rerouted: bool
    reason_code: str | None
    risk_flags: list[str]
    rider_id: UUID
    rider_available: bool
    partial_options: list[PartialOption]
    explanation: str


@router.post("/lots/{lot_id}/reroute", response_model=RerouteResponse)
def post_lot_reroute(
    lot_id: UUID,
    body: RerouteRequest,
    actor: User = Depends(current_actor),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=180),
) -> dict[str, object]:
    if actor.role not in {"COORDINATOR", "JUDGE", "RIDER"}:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN_ROLE", "message": "Recovery role is required"})
    try:
        return reroute_in_transit(
            actor_id=actor.id,
            lot_id=lot_id,
            expected_lot_version=body.expected_lot_version,
            expected_assignment_version=body.expected_assignment_version,
            expected_current_demand_version=body.expected_current_demand_version,
            idempotency_key=idempotency_key,
        )
    except ServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code.value, "message": exc.message},
        ) from exc
