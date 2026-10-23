from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from app.contracts.enums import ItemClass
from app.contracts.schemas import DemandSnapshot, UTCDateTime
from app.core.auth import current_actor
from app.models import User
from app.services.resources_shelter import (
    ShelterResourceError,
    create_demand,
    list_my_demands,
    record_walkin,
    set_intake,
    update_capabilities,
    update_demand,
)


router = APIRouter(tags=["shelters"])


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateDemandRequest(RequestModel):
    item_class: ItemClass
    required_qty: StrictInt = Field(ge=0)
    service_window_start: UTCDateTime | None = None
    service_window_end: UTCDateTime | None = None

    @model_validator(mode="after")
    def validate_window(self) -> "CreateDemandRequest":
        if (self.service_window_start is None) != (self.service_window_end is None):
            raise ValueError("Both service window bounds must be supplied together")
        if self.service_window_start and self.service_window_end and self.service_window_start >= self.service_window_end:
            raise ValueError("service_window_end must be later than its start")
        return self


class UpdateDemandRequest(RequestModel):
    required_qty: StrictInt | None = Field(default=None, ge=0)
    intake_open: bool | None = None
    expected_version: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def has_change(self) -> "UpdateDemandRequest":
        if self.required_qty is None and self.intake_open is None:
            raise ValueError("At least one demand field must be updated")
        return self


class WalkinEvent(RequestModel):
    type: Literal["WALKIN_RECEIVED"]
    demand_id: UUID
    qty: StrictInt = Field(gt=0)
    expected_version: StrictInt = Field(ge=0)


class IntakeEvent(RequestModel):
    type: Literal["CLOSE_INTAKE", "REOPEN_INTAKE"]
    expected_version: StrictInt = Field(ge=0)


class CapabilityEvent(RequestModel):
    type: Literal["CAPABILITY_CHANGED"]
    capabilities: dict[str, StrictBool]
    verified_at: UTCDateTime
    source: str = Field(min_length=1, max_length=80)
    expected_version: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def nonempty_capabilities(self) -> "CapabilityEvent":
        if not self.capabilities or any(not key.strip() for key in self.capabilities):
            raise ValueError("At least one named capability is required")
        if not self.source.strip():
            raise ValueError("source must contain non-whitespace text")
        return self


class ShelterIntakeResponse(RequestModel):
    shelter_id: UUID
    intake_open: bool
    version: int = Field(ge=0)


class ShelterCapabilitiesResponse(RequestModel):
    shelter_id: UUID
    capabilities: dict[str, bool]
    verified_at: UTCDateTime
    source: str
    version: int = Field(ge=0)


def _call_service(action):
    try:
        return action()
    except ShelterResourceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}) from exc


@router.get("/demands", response_model=list[DemandSnapshot])
def get_my_demands(actor: User = Depends(current_actor)) -> list[dict[str, object]]:
    return _call_service(lambda: list_my_demands(actor.id))


@router.post("/demands", response_model=DemandSnapshot, status_code=201)
def post_demand(
    body: CreateDemandRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=180),
    actor: User = Depends(current_actor),
) -> dict[str, object]:
    return _call_service(
        lambda: create_demand(
            actor.id,
            item_class=body.item_class,
            required_qty=body.required_qty,
            service_window_start=body.service_window_start,
            service_window_end=body.service_window_end,
            idempotency_key=idempotency_key,
        )
    )


@router.patch("/demands/{demand_id}", response_model=DemandSnapshot)
def patch_demand(
    demand_id: UUID,
    body: UpdateDemandRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=180),
    actor: User = Depends(current_actor),
) -> dict[str, object]:
    return _call_service(
        lambda: update_demand(
            actor.id,
            demand_id,
            required_qty=body.required_qty,
            intake_open=body.intake_open,
            expected_version=body.expected_version,
            idempotency_key=idempotency_key,
        )
    )


@router.post(
    "/shelters/{shelter_id}/events",
    response_model=DemandSnapshot | ShelterIntakeResponse | ShelterCapabilitiesResponse,
)
def post_shelter_event(
    shelter_id: UUID,
    body: WalkinEvent | IntakeEvent | CapabilityEvent,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=180),
    actor: User = Depends(current_actor),
) -> dict[str, object]:
    if isinstance(body, WalkinEvent):
        return _call_service(
            lambda: record_walkin(
                actor.id,
                shelter_id,
                body.demand_id,
                qty=body.qty,
                expected_version=body.expected_version,
                idempotency_key=idempotency_key,
            )
        )
    if isinstance(body, IntakeEvent):
        response = _call_service(
            lambda: set_intake(
                actor.id,
                shelter_id,
                open_for_intake=body.type == "REOPEN_INTAKE",
                expected_version=body.expected_version,
                idempotency_key=idempotency_key,
            )
        )
        return ShelterIntakeResponse.model_validate(response).model_dump(mode="json")
    response = _call_service(
        lambda: update_capabilities(
            actor.id,
            shelter_id,
            capabilities=body.capabilities,
            verified_at=body.verified_at,
            source=body.source,
            expected_version=body.expected_version,
            idempotency_key=idempotency_key,
        )
    )
    return ShelterCapabilitiesResponse.model_validate(response).model_dump(mode="json")
