from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class BatchBuckets:
    total: int
    available: int
    reserved: int = 0
    carried: int = 0
    delivered: int = 0
    disposed: int = 0

    def __post_init__(self) -> None:
        values = (self.total, self.available, self.reserved, self.carried, self.delivered, self.disposed)
        if any(not _is_integer(value) or value < 0 for value in values) or self.total == 0:
            raise ValueError("batch quantities must be nonnegative integers and total must be positive")
        if self.accounted != self.total:
            raise ValueError("the five inventory buckets must equal the batch total")

    @property
    def accounted(self) -> int:
        return self.available + self.reserved + self.carried + self.delivered + self.disposed

    def reserve(self, qty: int) -> "BatchBuckets":
        _positive(qty)
        if qty > self.available:
            raise ValueError("reservation exceeds available inventory")
        return replace(self, available=self.available - qty, reserved=self.reserved + qty)

    def cancel_reservation(self, qty: int) -> "BatchBuckets":
        _positive(qty)
        if qty > self.reserved:
            raise ValueError("reservation cancellation exceeds reserved inventory")
        return replace(self, available=self.available + qty, reserved=self.reserved - qty)

    def pickup(self, reserved_qty: int, picked_qty: int) -> "BatchBuckets":
        _positive(reserved_qty)
        if picked_qty < 0 or picked_qty > reserved_qty:
            raise ValueError("picked quantity must be between zero and the reservation")
        if reserved_qty > self.reserved:
            raise ValueError("pickup exceeds reserved inventory")
        # The uncollected shortfall returns to available stock immediately.
        return replace(
            self,
            available=self.available + reserved_qty - picked_qty,
            reserved=self.reserved - reserved_qty,
            carried=self.carried + picked_qty,
        )

    def deliver(self, accepted_qty: int) -> "BatchBuckets":
        if accepted_qty <= 0 or accepted_qty > self.carried:
            raise ValueError("accepted quantity must be positive and no greater than carried cargo")
        # Rejected and unresolved cargo remains in carried custody until return or disposal.
        return replace(self, carried=self.carried - accepted_qty, delivered=self.delivered + accepted_qty)

    def return_carried(self, qty: int) -> "BatchBuckets":
        _positive(qty)
        if qty > self.carried:
            raise ValueError("returned quantity exceeds physically carried cargo")
        return replace(self, available=self.available + qty, carried=self.carried - qty)

    def dispose(self, qty: int, *, from_bucket: str) -> "BatchBuckets":
        _positive(qty)
        if from_bucket not in {"available", "reserved", "carried"}:
            raise ValueError("only available, reserved, or carried stock can be disposed")
        source_qty = getattr(self, from_bucket)
        if qty > source_qty:
            raise ValueError("disposal exceeds the source inventory bucket")
        return replace(self, **{from_bucket: source_qty - qty, "disposed": self.disposed + qty})


@dataclass(frozen=True, slots=True)
class DemandLedger:
    required: int
    received: int = 0
    reserved: int = 0
    transit: int = 0
    intake_open: bool = True

    def __post_init__(self) -> None:
        values = (self.required, self.received, self.reserved, self.transit)
        if any(not _is_integer(value) or value < 0 for value in values):
            raise ValueError("demand ledger quantities must be nonnegative integers")

    @property
    def uncommitted_need(self) -> int:
        return max(0, self.required - self.received - self.reserved - self.transit)

    def reserve(self, qty: int) -> "DemandLedger":
        _positive(qty)
        if not self.intake_open:
            raise ValueError("demand intake is closed")
        if qty > self.uncommitted_need:
            raise ValueError("reservation exceeds uncommitted demand")
        return replace(self, reserved=self.reserved + qty)

    def cancel_reservation(self, qty: int) -> "DemandLedger":
        _positive(qty)
        if qty > self.reserved:
            raise ValueError("reservation cancellation exceeds reserved demand")
        return replace(self, reserved=self.reserved - qty)

    def depart(self, qty: int) -> "DemandLedger":
        _positive(qty)
        if qty > self.reserved:
            raise ValueError("departing quantity exceeds reserved demand")
        return replace(self, reserved=self.reserved - qty, transit=self.transit + qty)

    def receive(self, qty: int) -> "DemandLedger":
        _positive(qty)
        if qty > self.transit:
            raise ValueError("receipt exceeds in-transit demand commitment")
        return replace(self, transit=self.transit - qty, received=self.received + qty)

    def release_transit(self, qty: int) -> "DemandLedger":
        _positive(qty)
        if qty > self.transit:
            raise ValueError("released quantity exceeds in-transit demand commitment")
        return replace(self, transit=self.transit - qty)

    def record_walk_in(self, qty: int) -> "DemandLedger":
        _positive(qty)
        # Walk-ins may exceed the requested amount; need remains clamped to zero.
        return replace(self, received=self.received + qty)

    def close_intake(self) -> "DemandLedger":
        return replace(self, intake_open=False)


def _positive(qty: int) -> None:
    if not _is_integer(qty) or qty <= 0:
        raise ValueError("quantity must be a positive integer")


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
