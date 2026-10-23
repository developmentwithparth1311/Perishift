from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer,
    SmallInteger, String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.contracts.enums import (
    AssignmentStatus, EventType, ItemClass, LeaseStatus,
    LotState, OfferStatus, QuantityUnit, ReasonCode, ScenarioMode,
    SimulatedRiderMode,
)
from app.models.base import Base


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Scenario(Base):
    __tablename__ = "scenarios"
    id: Mapped[uuid.UUID] = uuid_pk()
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default=ScenarioMode.LIVE.value)
    current_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __table_args__ = (
        CheckConstraint("mode IN ('LIVE','SIMULATED')", name="mode"),
        CheckConstraint("generation >= 0 AND version >= 0", name="nonnegative_versions"),
        Index("uq_scenarios_one_active_simulation", "mode", unique=True,
              postgresql_where=text("active AND mode = 'SIMULATED'")),
    )


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = uuid_pk()
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __table_args__ = (
        CheckConstraint("role IN ('DONOR','RIDER','SHELTER','COORDINATOR','JUDGE')", name="role"),
        UniqueConstraint("scenario_id", "external_id", name="scenario_external_identity"),
    )


class Donor(Base):
    __tablename__ = "donors"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    latitude: Mapped[float] = mapped_column(nullable=False)
    longitude: Mapped[float] = mapped_column(nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180", name="valid_location"),
    )


class Rider(Base):
    __tablename__ = "riders"
    id: Mapped[uuid.UUID] = uuid_pk()
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    latitude: Mapped[float | None] = mapped_column()
    longitude: Mapped[float | None] = mapped_column()
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    simulated_mode: Mapped[str | None] = mapped_column(String(16))
    handling_capabilities: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    handling_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handling_source: Mapped[str | None] = mapped_column(String(80))
    heartbeat_period_seconds: Mapped[int | None] = mapped_column(Integer)
    accept_delay_seconds: Mapped[int | None] = mapped_column(Integer)
    pickup_delay_seconds: Mapped[int | None] = mapped_column(Integer)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    __table_args__ = (
        CheckConstraint("capacity > 0", name="positive_capacity"),
        CheckConstraint("(latitude IS NULL AND longitude IS NULL) OR (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)", name="valid_location"),
        CheckConstraint("simulated_mode IS NULL OR simulated_mode IN ('AUTO','MANUAL','SILENT','DELAYED')", name="simulated_mode"),
        CheckConstraint("handling_source IS NULL OR length(trim(handling_source)) > 0", name="handling_source"), 
        Index("ix_riders_scenario_available", "scenario_id", "available"),
    )


class Shelter(Base):
    __tablename__ = "shelters"
    id: Mapped[uuid.UUID] = uuid_pk()
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    latitude: Mapped[float] = mapped_column(nullable=False)
    longitude: Mapped[float] = mapped_column(nullable=False)
    intake_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    capabilities: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    capability_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capability_source: Mapped[str | None] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180", name="valid_location"),
        Index("ix_shelters_scenario_intake", "scenario_id", "intake_open"),
    )


class Demand(Base):
    __tablename__ = "demands"
    id: Mapped[uuid.UUID] = uuid_pk()
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"))
    shelter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shelters.id", ondelete="RESTRICT"))
    item_class: Mapped[str] = mapped_column(String(32), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    required_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    received_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    transit_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    intake_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    service_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    service_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    __table_args__ = (
        CheckConstraint("item_class IN ('HOT_COOKED_MEAL','RAW_RATION_KIT','POWDER_FORMULA','COLD_CHAIN_MEDICINE')", name="item_class"),
        CheckConstraint("(item_class = 'HOT_COOKED_MEAL' AND unit = 'MEAL') OR (item_class = 'RAW_RATION_KIT' AND unit = 'KIT') OR (item_class IN ('POWDER_FORMULA','COLD_CHAIN_MEDICINE') AND unit = 'PACKAGE')", name="class_unit"),
        CheckConstraint("required_qty >= 0 AND received_qty >= 0 AND reserved_qty >= 0 AND transit_qty >= 0", name="nonnegative_ledger"),
        CheckConstraint("service_window_start IS NULL OR service_window_end IS NULL OR service_window_start < service_window_end", name="valid_service_window"),
        Index("ix_demands_eligible", "scenario_id", "item_class", "intake_open", "shelter_id"),
        Index("uq_demands_window", "scenario_id", "shelter_id", "item_class", "service_window_start", "service_window_end", unique=True,
              postgresql_nulls_not_distinct=True),
    )


class Batch(Base):
    __tablename__ = "batches"
    id: Mapped[uuid.UUID] = uuid_pk()
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"))
    donor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("donors.id", ondelete="RESTRICT"))
    item_class: Mapped[str] = mapped_column(String(32), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    total_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    ready_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    safe_by: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deliver_by: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    safety_evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="AVAILABLE")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __table_args__ = (
        CheckConstraint("item_class IN ('HOT_COOKED_MEAL','RAW_RATION_KIT','POWDER_FORMULA','COLD_CHAIN_MEDICINE')", name="item_class"),
        CheckConstraint("(item_class = 'HOT_COOKED_MEAL' AND unit = 'MEAL') OR (item_class = 'RAW_RATION_KIT' AND unit = 'KIT') OR (item_class IN ('POWDER_FORMULA','COLD_CHAIN_MEDICINE') AND unit = 'PACKAGE')", name="class_unit"),
        CheckConstraint("total_qty > 0 AND ready_at < deliver_by AND deliver_by <= safe_by", name="valid_quantity_window"),
        Index("ix_batches_scenario_ready", "scenario_id", "status", "ready_at"),
    )


class BatchInventory(Base):
    __tablename__ = "batch_inventory"
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("batches.id", ondelete="CASCADE"), primary_key=True)
    available: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    carried: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    disposed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    __table_args__ = (
        CheckConstraint("available >= 0 AND reserved >= 0 AND carried >= 0 AND delivered >= 0 AND disposed >= 0", name="nonnegative_buckets"),
    )


class Lot(Base):
    __tablename__ = "lots"
    id: Mapped[uuid.UUID] = uuid_pk()
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"))
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("batches.id", ondelete="RESTRICT"))
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default=LotState.OPEN.value)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __table_args__ = (
        CheckConstraint("qty > 0", name="positive_qty"),
        CheckConstraint("state IN ('OPEN','OFFERED','LEASED','IN_TRANSIT','DELIVERED','EXPIRED','DISPOSED')", name="state"),
        Index("ix_lots_scenario_state", "scenario_id", "state"),
    )


class Offer(Base):
    __tablename__ = "offers"
    id: Mapped[uuid.UUID] = uuid_pk()
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"))
    lot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("lots.id", ondelete="RESTRICT"))
    rider_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("riders.id", ondelete="RESTRICT"))
    demand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("demands.id", ondelete="RESTRICT"))
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=OfferStatus.PUBLISHED.value)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    __table_args__ = (
        CheckConstraint("qty > 0", name="positive_qty"),
        CheckConstraint("status IN ('PUBLISHED','ACCEPTED','DECLINED','EXPIRED','CANCELLED')", name="status"),
        Index("ix_offers_rider_eligible", "scenario_id", "rider_id", "status", "expires_at"),
    )


class Assignment(Base):
    __tablename__ = "assignments"
    id: Mapped[uuid.UUID] = uuid_pk()
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"))
    lot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("lots.id", ondelete="RESTRICT"))
    rider_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("riders.id", ondelete="RESTRICT"))
    demand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("demands.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=AssignmentStatus.RESERVED.value)
    reserved_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    picked_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    carried_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    risk_flags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    __table_args__ = (
        CheckConstraint("status IN ('RESERVED','IN_TRANSIT','DELIVERED','CANCELLED','DISPOSED')", name="status"),
        CheckConstraint("reserved_qty > 0 AND picked_qty >= 0 AND accepted_qty >= 0 AND rejected_qty >= 0 AND carried_qty >= 0", name="nonnegative_quantities"),
        CheckConstraint("accepted_qty + rejected_qty <= picked_qty", name="delivery_within_pickup"),
        Index("uq_assignments_active_lot", "lot_id", unique=True, postgresql_where=text("status IN ('RESERVED','IN_TRANSIT')")),
        Index("uq_assignments_active_rider", "scenario_id", "rider_id", unique=True, postgresql_where=text("status IN ('RESERVED','IN_TRANSIT')")),
    )


class Lease(Base):
    __tablename__ = "leases"
    id: Mapped[uuid.UUID] = uuid_pk()
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"))
    lot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("lots.id", ondelete="RESTRICT"))
    rider_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("riders.id", ondelete="RESTRICT"))
    assignment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assignments.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=LeaseStatus.ACTIVE.value)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_progress_by: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    physical_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    backup_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    renewals: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','PICKED_UP','RELEASED','EXPIRED','REVOKED')", name="status"),
        CheckConstraint("issued_at < expires_at AND expires_at <= physical_cutoff_at AND backup_cutoff_at >= issued_at", name="valid_deadlines"),
        Index("uq_leases_active_lot", "lot_id", unique=True, postgresql_where=text("status = 'ACTIVE'")),
        Index("uq_leases_active_rider", "scenario_id", "rider_id", unique=True, postgresql_where=text("status = 'ACTIVE'")),
    )


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="RESTRICT"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint(
            "aggregate_type IN ('batch','demand','lot','rider','shelter','scenario')",
            name="aggregate_type",
        ),
        CheckConstraint(
            "event_type IN (" + ",".join(f"'{value.value}'" for value in EventType) + ")",
            name="event_type",
        ),
        CheckConstraint(
            "reason_code IS NULL OR reason_code IN ("
            + ",".join(f"'{value.value}'" for value in ReasonCode)
            + ")",
            name="reason_code",
        ),
        Index("ix_events_scenario_timeline", "scenario_id", "id"),
        Index("ix_events_aggregate_timeline", "aggregate_type", "aggregate_id", "id"),
    )


class ScheduledAction(Base):
    __tablename__ = "scheduled_actions"
    id: Mapped[uuid.UUID] = uuid_pk()
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"))
    action_type: Mapped[str] = mapped_column(String(48), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(180), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','RUNNING','DONE','CANCELLED','FAILED')", name="status"),
        Index("uq_scheduled_actions_dedupe", "scenario_id", "dedupe_key", unique=True, postgresql_nulls_not_distinct=True),
        Index("ix_scheduled_actions_due", "due_at", "id", postgresql_where=text("status = 'PENDING'")),
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    id: Mapped[uuid.UUID] = uuid_pk()
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"))
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    endpoint: Mapped[str] = mapped_column(String(180), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column(SmallInteger)
    response_body: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __table_args__ = (
        Index("uq_idempotency_scope_key", "scenario_id", "actor_id", "endpoint", "idempotency_key", unique=True, postgresql_nulls_not_distinct=True),
        Index("ix_idempotency_created", "created_at"),
    )
