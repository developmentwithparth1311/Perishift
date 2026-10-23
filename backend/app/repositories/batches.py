from uuid import UUID

from sqlalchemy.orm import Session

from app.core.db import LockOrder, LockRank, lock_rows
from app.domain.inventory import BatchBuckets
from app.models import Batch, BatchInventory


def lock_batch_inventory(
    session: Session, batch_id: UUID, lock_order: LockOrder
) -> tuple[Batch, BatchInventory]:
    batches = lock_rows(session, Batch, LockRank.BATCH, [batch_id], lock_order)
    if not batches:
        raise LookupError("batch not found")
    inventories = lock_rows(session, BatchInventory, LockRank.BATCH_INVENTORY, [batch_id], lock_order)
    if not inventories:
        raise RuntimeError("batch inventory row is missing")
    return batches[0], inventories[0]


def inventory_buckets(batch: Batch, inventory: BatchInventory) -> BatchBuckets:
    return BatchBuckets(
        total=batch.total_qty,
        available=inventory.available,
        reserved=inventory.reserved,
        carried=inventory.carried,
        delivered=inventory.delivered,
        disposed=inventory.disposed,
    )


def save_inventory(inventory: BatchInventory, buckets: BatchBuckets) -> None:
    inventory.available = buckets.available
    inventory.reserved = buckets.reserved
    inventory.carried = buckets.carried
    inventory.delivered = buckets.delivered
    inventory.disposed = buckets.disposed
    inventory.version += 1
