from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.contracts.enums import (
    ITEM_CLASS_UNIT,
    AggregateType,
    ApiErrorCode,
    AssignmentStatus,
    EventType,
    ItemClass,
    LeaseStatus,
    LotState,
    OfferStatus,
    OperationalFlag,
    QuantityUnit,
    ReasonCode,
    ScenarioMode,
    SimulatedRiderMode,
)


def normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value.astimezone(timezone.utc)


UTCDateTime = Annotated[datetime, AfterValidator(normalize_utc)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class CreateBatchRequest(ContractModel):
    scenario_id: UUID | None = None
    donor_id: UUID
    item_class: ItemClass
    unit: QuantityUnit
    qty: int = Field(gt=0)
    ready_at: UTCDateTime
    declared_safe_by: UTCDateTime
    serving_buffer_seconds: int = Field(default=600, ge=0)

    @model_validator(mode="after")
    def validate_window_and_unit(self) -> "CreateBatchRequest":
        if self.ready_at >= self.declared_safe_by:
            raise ValueError("declared_safe_by must be later than ready_at")
        if self.unit != ITEM_CLASS_UNIT[self.item_class]:
            raise ValueError("unit does not match the canonical unit for item_class")
        return self


class BatchBalance(ContractModel):
    batch_id: UUID
    item_class: ItemClass
    unit: QuantityUnit
    total_qty: int = Field(gt=0)
    available_qty: int = Field(ge=0)
    reserved_qty: int = Field(ge=0)
    carried_qty: int = Field(ge=0)
    delivered_qty: int = Field(ge=0)
    disposed_qty: int = Field(ge=0)
    safe_by: UTCDateTime
    deliver_by: UTCDateTime
    version: int = Field(ge=0)

    @model_validator(mode="after")
    def conserve_batch_quantity(self) -> "BatchBalance":
        accounted_qty = (
            self.available_qty
            + self.reserved_qty
            + self.carried_qty
            + self.delivered_qty
            + self.disposed_qty
        )
        if accounted_qty != self.total_qty:
            raise ValueError("the five inventory buckets must sum to total_qty")
        if self.deliver_by > self.safe_by:
            raise ValueError("deliver_by cannot be later than safe_by")
        return self


class DemandSnapshot(ContractModel):
    demand_id: UUID
    shelter_id: UUID
    item_class: ItemClass
    unit: QuantityUnit
    required_qty: int = Field(ge=0)
    received_qty: int = Field(ge=0)
    reserved_qty: int = Field(ge=0)
    transit_qty: int = Field(ge=0)
    uncommitted_need: int = Field(ge=0)
    intake_open: bool
    version: int = Field(ge=0)
    service_window_start: UTCDateTime | None = None
    service_window_end: UTCDateTime | None = None

    @model_validator(mode="after")
    def validate_demand_snapshot(self) -> "DemandSnapshot":
        expected_need = max(
            0,
            self.required_qty
            - self.received_qty
            - self.reserved_qty
            - self.transit_qty,
        )
        if self.uncommitted_need != expected_need:
            raise ValueError("uncommitted_need does not match the demand ledger")
        if (self.service_window_start is None) != (self.service_window_end is None):
            raise ValueError("both service window bounds must be supplied together")
        if (
            self.service_window_start is not None
            and self.service_window_end is not None
            and self.service_window_start >= self.service_window_end
        ):
            raise ValueError("service_window_end must be later than its start")
        if self.unit != ITEM_CLASS_UNIT[self.item_class]:
            raise ValueError("unit does not match the canonical unit for item_class")
        return self


class EventEnvelope(ContractModel):
    id: int = Field(gt=0)
    scenario_id: UUID | None = None
    type: EventType
    aggregate_type: AggregateType
    aggregate_id: UUID
    occurred_at: UTCDateTime
    reason_code: ReasonCode | None = None
    payload: dict[str, object] = Field(default_factory=dict)


class ErrorDetail(ContractModel):
    code: ApiErrorCode
    message: str
    details: dict[str, object] | None = None
    retryable: bool | None = None


class ErrorEnvelope(ContractModel):
    error: ErrorDetail
    request_id: str


class AcceptLotRequest(ContractModel):
    rider_id: UUID
    expected_version: int = Field(ge=0)


class AcceptLotResponse(ContractModel):
    lot_id: UUID
    state: LotState
    version: int = Field(ge=0)
    lease_id: UUID
    lease_status: LeaseStatus
    lease_expires_at: UTCDateTime
    first_progress_by: UTCDateTime


class PickupRequest(ContractModel):
    picked_qty: int = Field(gt=0)
    expected_version: int = Field(ge=0)


class DeliveryConfirmationRequest(ContractModel):
    accepted_qty: int = Field(ge=0)
    rejected_qty: int = Field(ge=0)
    expected_version: int = Field(ge=0)


class OfferSnapshot(ContractModel):
    offer_id: UUID
    lot_id: UUID
    rider_id: UUID
    demand_id: UUID
    qty: int = Field(gt=0)
    status: OfferStatus
    expires_at: UTCDateTime
    version: int = Field(ge=0)


class AssignmentSnapshot(ContractModel):
    assignment_id: UUID
    lot_id: UUID
    rider_id: UUID
    demand_id: UUID
    status: AssignmentStatus
    risk_flags: set[OperationalFlag] = Field(default_factory=set)
    picked_qty: int = Field(ge=0)
    accepted_qty: int = Field(ge=0)
    rejected_qty: int = Field(ge=0)
    carried_qty: int = Field(ge=0)
    version: int = Field(ge=0)


class ScenarioSnapshot(ContractModel):
    scenario_id: UUID
    mode: ScenarioMode
    now: UTCDateTime
    version: int = Field(ge=0)


class SimulatedRiderSnapshot(ContractModel):
    rider_id: UUID
    scenario_id: UUID
    mode: SimulatedRiderMode
    heartbeat_period_seconds: int = Field(gt=0)
    accept_delay_seconds: int = Field(ge=0)
    pickup_delay_seconds: int = Field(ge=0)
    generation: int = Field(ge=0)
