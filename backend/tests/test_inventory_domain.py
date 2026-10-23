import pytest

from app.domain.inventory import BatchBuckets, DemandLedger


def test_batch_inventory_conservation_through_partial_pickup_and_delivery() -> None:
    stock = BatchBuckets(total=180, available=180)
    stock = stock.reserve(40)
    assert stock == BatchBuckets(total=180, available=140, reserved=40)

    stock = stock.pickup(reserved_qty=40, picked_qty=35)
    assert stock == BatchBuckets(total=180, available=145, carried=35)

    stock = stock.deliver(accepted_qty=30)
    assert stock == BatchBuckets(total=180, available=145, carried=5, delivered=30)
    stock = stock.return_carried(5)
    assert stock == BatchBuckets(total=180, available=150, delivered=30)
    assert stock.accounted == 180


def test_reservation_and_disposal_reject_invalid_quantities() -> None:
    stock = BatchBuckets(total=10, available=10).reserve(7)
    with pytest.raises(ValueError, match="available inventory"):
        stock.reserve(4)
    with pytest.raises(ValueError, match="reserved inventory"):
        stock.pickup(reserved_qty=8, picked_qty=1)
    with pytest.raises(ValueError, match="positive integer"):
        stock.reserve(True)

    disposed = stock.dispose(2, from_bucket="reserved")
    assert disposed == BatchBuckets(total=10, available=3, reserved=5, disposed=2)


def test_demand_ledger_reserve_dispatch_receive_and_overage() -> None:
    demand = DemandLedger(required=50)
    demand = demand.reserve(40)
    assert demand.uncommitted_need == 10
    demand = demand.depart(35)
    demand = demand.cancel_reservation(5)
    assert demand == DemandLedger(required=50, reserved=0, transit=35)

    demand = demand.receive(30)
    demand = demand.release_transit(5)
    assert demand == DemandLedger(required=50, received=30, transit=0)
    assert demand.uncommitted_need == 20

    walk_in = demand.record_walk_in(25)
    assert walk_in.received == 55
    assert walk_in.uncommitted_need == 0


def test_closed_demand_and_overcommit_are_rejected() -> None:
    with pytest.raises(ValueError, match="uncommitted demand"):
        DemandLedger(required=10).reserve(11)
    with pytest.raises(ValueError, match="intake is closed"):
        DemandLedger(required=10).close_intake().reserve(1)
    with pytest.raises(ValueError, match="in-transit"):
        DemandLedger(required=10).receive(1)
