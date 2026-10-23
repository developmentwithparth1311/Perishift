from uuid import UUID

from sqlalchemy.orm import Session

from app.core.db import LockOrder, LockRank, lock_rows
from app.domain.inventory import DemandLedger
from app.models import Demand


def lock_demand(session: Session, demand_id: UUID, lock_order: LockOrder) -> Demand:
    rows = lock_rows(session, Demand, LockRank.DEMAND, [demand_id], lock_order)
    if not rows:
        raise LookupError("demand not found")
    return rows[0]


def demand_ledger(demand: Demand) -> DemandLedger:
    return DemandLedger(
        required=demand.required_qty,
        received=demand.received_qty,
        reserved=demand.reserved_qty,
        transit=demand.transit_qty,
        intake_open=demand.intake_open,
    )


def save_demand_ledger(demand: Demand, ledger: DemandLedger) -> None:
    demand.received_qty = ledger.received
    demand.reserved_qty = ledger.reserved
    demand.transit_qty = ledger.transit
    demand.intake_open = ledger.intake_open
    demand.version += 1
