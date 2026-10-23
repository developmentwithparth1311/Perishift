"""HTTP endpoints for the I0 dispatch, lease and custody vertical slice."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.contracts.enums import AssignmentStatus, LeaseStatus, LotState, OfferStatus
from app.contracts.schemas import AcceptLotRequest, DeliveryConfirmationRequest, PickupRequest, UTCDateTime
from app.core.auth import current_actor
from app.core.errors import ServiceError
from app.models import User
from app.services.commitment import accept_lot, publish_offer
from app.services.custody import confirm_delivery, confirm_pickup, get_active_dispatch, get_dispatch_map


router = APIRouter(prefix="/api/v1", tags=["lots"])


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublishOfferRequest(RequestModel):
    batch_id: UUID
    demand_id: UUID
    rider_id: UUID
    quantity: StrictInt = Field(gt=0)
    expires_at: UTCDateTime
    expected_batch_version: StrictInt = Field(ge=0)
    expected_demand_version: StrictInt = Field(ge=0)
    expected_rider_version: StrictInt = Field(ge=0)


class PublishOfferResponse(RequestModel):
    offer_id: UUID
    lot_id: UUID
    rider_id: UUID
    demand_id: UUID
    qty: int = Field(gt=0)
    status: OfferStatus
    expires_at: UTCDateTime
    version: int = Field(ge=0)
    lot_version: int = Field(ge=0)


class AcceptCommandResponse(RequestModel):
    lot_id: UUID
    state: LotState
    version: int = Field(ge=0)
    offer_id: UUID
    assignment_id: UUID
    lease_id: UUID
    lease_status: LeaseStatus
    lease_expires_at: UTCDateTime
    first_progress_by: UTCDateTime
    reserved_qty: int = Field(gt=0)


class CustodyCommandResponse(RequestModel):
    lot_id: UUID
    lot_state: LotState
    lot_version: int = Field(ge=0)
    assignment_id: UUID
    assignment_status: AssignmentStatus
    assignment_version: int = Field(ge=0)
    picked_qty: int = Field(ge=0)
    accepted_qty: int = Field(ge=0)
    rejected_qty: int = Field(ge=0)
    carried_qty: int = Field(ge=0)
    risk_flags: list[str]
    rider_id: UUID
    rider_available: bool
    inventory: dict[str, int]
    demand: dict[str, int]


class ActiveDispatchResponse(RequestModel):
    lot_id: UUID
    batch_id: UUID
    offer_id: UUID
    rider_id: UUID
    demand_id: UUID
    qty: int = Field(gt=0)
    state: LotState
    lot_version: int = Field(ge=0)
    rider_version: int = Field(ge=0)
    picked_qty: int = Field(ge=0)
    accepted_qty: int = Field(ge=0)
    rejected_qty: int = Field(ge=0)
    carried_qty: int = Field(ge=0)
    lease_expires_at: UTCDateTime | None


class MapLocationResponse(RequestModel):
    name: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class MapRiderResponse(MapLocationResponse):
    location_known: bool


class DispatchMapResponse(RequestModel):
    lot_id: UUID | None
    state: LotState | None
    batch_id: UUID
    demand_id: UUID
    simulated: bool
    pickup: MapLocationResponse
    destination: MapLocationResponse
    rider: MapRiderResponse


def _service(action):
    try:
        return action()
    except ServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code.value, "message": exc.message},
        ) from exc


@router.get("/dispatch/active", response_model=ActiveDispatchResponse | None)
def get_dispatch_active(rider_id: UUID, actor: User = Depends(current_actor)) -> dict[str, object] | None:
    return _service(lambda: get_active_dispatch(actor_id=actor.id, rider_id=rider_id))


@router.get("/dispatch/map", response_model=DispatchMapResponse)
def get_dispatch_route_map(
    rider_id: UUID, batch_id: UUID | None = None, demand_id: UUID | None = None,
    lot_id: UUID | None = None, actor: User = Depends(current_actor),
) -> dict[str, object]:
    return _service(lambda: get_dispatch_map(
        actor_id=actor.id, rider_id=rider_id, batch_id=batch_id, demand_id=demand_id, lot_id=lot_id,
    ))


@router.post("/dispatch/commit", response_model=PublishOfferResponse, status_code=status.HTTP_201_CREATED)
def post_dispatch_commit(
    body: PublishOfferRequest,
    actor: User = Depends(current_actor),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=180),
) -> dict[str, object]:
    if actor.role not in {"COORDINATOR", "JUDGE", "DONOR"}:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN_ROLE", "message": "Role cannot publish offers"})
    return _service(lambda: publish_offer(
        actor_id=actor.id,
        batch_id=body.batch_id,
        demand_id=body.demand_id,
        rider_id=body.rider_id,
        quantity=body.quantity,
        expires_at=body.expires_at,
        expected_batch_version=body.expected_batch_version,
        expected_demand_version=body.expected_demand_version,
        expected_rider_version=body.expected_rider_version,
        idempotency_key=idempotency_key,
    ))


@router.post("/lots/{lot_id}/accept", response_model=AcceptCommandResponse)
def post_lot_accept(
    lot_id: UUID,
    body: AcceptLotRequest,
    actor: User = Depends(current_actor),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=180),
) -> dict[str, object]:
    if actor.role != "RIDER":
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN_ROLE", "message": "RIDER role is required"})
    return _service(lambda: accept_lot(
        actor_id=actor.id,
        lot_id=lot_id,
        rider_id=body.rider_id,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
    ))


@router.post("/lots/{lot_id}/pickup", response_model=CustodyCommandResponse)
def post_lot_pickup(
    lot_id: UUID,
    body: PickupRequest,
    actor: User = Depends(current_actor),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=180),
) -> dict[str, object]:
    if actor.role != "RIDER":
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN_ROLE", "message": "RIDER role is required"})
    return _service(lambda: confirm_pickup(
        actor_id=actor.id,
        lot_id=lot_id,
        picked_quantity=body.picked_qty,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
    ))


@router.post("/lots/{lot_id}/deliver", response_model=CustodyCommandResponse)
def post_lot_delivery(
    lot_id: UUID,
    body: DeliveryConfirmationRequest,
    actor: User = Depends(current_actor),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=180),
) -> dict[str, object]:
    if actor.role not in {"RIDER", "SHELTER"}:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN_ROLE", "message": "RIDER or destination SHELTER role is required"})
    return _service(lambda: confirm_delivery(
        actor_id=actor.id,
        lot_id=lot_id,
        accepted_quantity=body.accepted_qty,
        rejected_quantity=body.rejected_qty,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
    ))
