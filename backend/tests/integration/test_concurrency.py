"""Race tests on real PostgreSQL with independent connections and lock-wait barriers.
Each test asserts the persisted final state, not just the error raised."""

import asyncio

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.errors import Conflict, InvalidState, ValidationFailed
from app.models import (
    AllocationRequest,
    AllocationResult,
    InventoryLot,
    Round,
    RoundLot,
    RoundSnapshot,
)
from app.services import allocation, idempotency, inventory, rounds
from app.services import requests as req_service
from app.services.common import Changes
from tests.conftest import make_lot, make_round
from tests.integration.helpers import assert_invariants, backend_pid, wait_until_blocked


async def test_competing_reservations_of_one_lot(maker, world):
    lot = await make_lot(maker, qty=100)
    r1 = await make_round(maker, world, lots=[], open_round=False)
    r2 = await make_round(maker, world, lots=[], open_round=False)
    async with maker() as a, maker() as b:
        await rounds.reserve_lot(a, world.admin_actor, Changes(), r1, lot_id=lot.id, quantity=60)
        pid_b = await backend_pid(b)
        task = asyncio.create_task(
            rounds.reserve_lot(b, world.admin_actor, Changes(), r2, lot_id=lot.id, quantity=30)
        )
        await wait_until_blocked(maker, pid_b)
        await a.commit()
        with pytest.raises(Conflict) as err:
            await task
        assert err.value.code == "LOT_ALREADY_RESERVED"
        await b.rollback()
    async with maker() as s:
        rows = (await s.execute(select(RoundLot.round_id, RoundLot.quantity))).all()
        assert rows == [(r1, 60)]
        assert (await s.get(InventoryLot, lot.id)).quantity_reserved == 60
        await assert_invariants(s)


async def test_partial_unique_index_backs_up_reservation_check(maker, world):
    lot = await make_lot(maker, qty=100)
    r1 = await make_round(maker, world, lots=[(lot.id, 10)], open_round=False)
    r2 = await make_round(maker, world, lots=[], open_round=False)
    async with maker() as s:
        s.add(RoundLot(round_id=r2, lot_id=lot.id, quantity=5, status="reserved"))
        with pytest.raises(IntegrityError):
            await s.flush()
    assert r1


async def test_stock_reduction_racing_reservation(maker, world):
    lot = await make_lot(maker, qty=100)
    rnd = await make_round(maker, world, lots=[], open_round=False)
    async with maker() as a, maker() as b:
        await rounds.reserve_lot(a, world.admin_actor, Changes(), rnd, lot_id=lot.id, quantity=80)
        pid_b = await backend_pid(b)
        task = asyncio.create_task(
            inventory.update_lot(
                b,
                world.admin_actor,
                Changes(),
                lot.id,
                version=1,
                reason="spoilage",
                quantity_on_hand=50,
            )
        )
        await wait_until_blocked(maker, pid_b)
        await a.commit()
        with pytest.raises(Conflict):  # version moved on: the reservation bumped it
            await task
        await b.rollback()
    async with maker() as s:  # retry with the fresh version still cannot go below reserved
        with pytest.raises(InvalidState) as err:
            await inventory.update_lot(
                s,
                world.admin_actor,
                Changes(),
                lot.id,
                version=2,
                reason="spoilage",
                quantity_on_hand=50,
            )
        assert err.value.code == "BELOW_RESERVED"
        await s.rollback()
        fresh = await s.get(InventoryLot, lot.id)
        assert (fresh.quantity_on_hand, fresh.quantity_reserved) == (100, 80)
        # The CHECK constraint holds even for a write that bypasses the service.
        with pytest.raises(IntegrityError):
            await s.execute(
                text(f"UPDATE inventory_lots SET quantity_on_hand = 10 WHERE id = {lot.id}")
            )
        await s.rollback()


async def test_competing_requests_from_same_organization(maker, world):
    lot = await make_lot(maker)
    rnd = await make_round(maker, world, lots=[(lot.id, 50)])
    async with maker() as a, maker() as b:
        await req_service.create_request(
            a, world.actor_a, Changes(), rnd, lot_id=lot.id, quantity=10
        )
        pid_b = await backend_pid(b)
        task = asyncio.create_task(
            req_service.create_request(
                b, world.actor_a2, Changes(), rnd, lot_id=lot.id, quantity=20
            )
        )
        await wait_until_blocked(maker, pid_b)
        await a.commit()
        with pytest.raises(Conflict) as err:
            await task
        assert err.value.code == "REQUEST_EXISTS"
        await b.rollback()
    async with maker() as s:
        active = (
            await s.scalars(
                select(AllocationRequest.quantity).where(AllocationRequest.status == "active")
            )
        ).all()
        assert active == [10]


async def test_concurrent_edits_do_not_overwrite(maker, world):
    lot = await make_lot(maker)
    rnd = await make_round(maker, world, lots=[(lot.id, 50)])
    async with maker() as s:
        req = await req_service.create_request(
            s, world.actor_a, Changes(), rnd, lot_id=lot.id, quantity=5
        )
        await s.commit()
    async with maker() as a, maker() as b:
        await req_service.update_request(
            a, world.actor_a, Changes(), req.id, quantity=10, version=1
        )
        pid_b = await backend_pid(b)
        task = asyncio.create_task(
            req_service.update_request(b, world.actor_a2, Changes(), req.id, quantity=20, version=1)
        )
        await wait_until_blocked(maker, pid_b)
        await a.commit()
        with pytest.raises(Conflict) as err:
            await task
        assert err.value.code == "VERSION_CONFLICT"
        assert err.value.details["current_quantity"] == 10
        await b.rollback()
    async with maker() as s:
        fresh = await s.get(AllocationRequest, req.id)
        assert (fresh.quantity, fresh.version, fresh.accepted_seq) == (10, 2, req.accepted_seq)


async def test_edit_keeps_order_and_resubmission_gets_new_position(maker, world):
    lot = await make_lot(maker)
    rnd = await make_round(maker, world, lots=[(lot.id, 50)])
    async with maker() as s:
        a = await req_service.create_request(
            s, world.actor_a, Changes(), rnd, lot_id=lot.id, quantity=5
        )
        b = await req_service.create_request(
            s, world.actor_b, Changes(), rnd, lot_id=lot.id, quantity=5
        )
        await req_service.update_request(s, world.actor_a, Changes(), a.id, quantity=9, version=1)
        await s.commit()
        assert a.accepted_seq < b.accepted_seq
        await req_service.withdraw_request(s, world.actor_a, Changes(), a.id, version=2)
        again = await req_service.create_request(
            s, world.actor_a, Changes(), rnd, lot_id=lot.id, quantity=9
        )
        await s.commit()
        assert again.accepted_seq > b.accepted_seq


@pytest.mark.parametrize("request_first", [True, False])
async def test_request_racing_close(maker, world, request_first):
    """Whichever transaction takes the round lock first wins; the other observes it."""
    lot = await make_lot(maker)
    rnd = await make_round(maker, world, lots=[(lot.id, 50)])
    async with maker() as first, maker() as second:
        pid = await backend_pid(second)
        if request_first:
            await req_service.create_request(
                first, world.actor_a, Changes(), rnd, lot_id=lot.id, quantity=7
            )
            task = asyncio.create_task(
                rounds.close_round(second, world.admin_actor, Changes(), rnd, version=None)
            )
        else:
            await rounds.close_round(first, world.admin_actor, Changes(), rnd, version=None)
            task = asyncio.create_task(
                req_service.create_request(
                    second, world.actor_a, Changes(), rnd, lot_id=lot.id, quantity=7
                )
            )
        await wait_until_blocked(maker, pid)
        await first.commit()
        if request_first:
            await task
            await second.commit()
        else:
            with pytest.raises(InvalidState):
                await task
            await second.rollback()
    async with maker() as s:
        snap = await s.get(RoundSnapshot, rnd)
        frozen = [r["quantity"] for r in snap.input["lots"][0]["requests"]]
        stored = (await s.scalars(select(AllocationRequest.quantity))).all()
        assert (await s.get(Round, rnd)).status == "closed"
        if request_first:
            assert frozen == [7] and stored == [7]
        else:
            assert frozen == [] and stored == []


async def _closed_round(maker, world, strategy="fcfs"):
    lot = await make_lot(maker, qty=100)
    rnd = await make_round(maker, world, lots=[(lot.id, 60)], strategy=strategy)
    async with maker() as s:
        await req_service.create_request(
            s, world.actor_a, Changes(), rnd, lot_id=lot.id, quantity=40
        )
        await req_service.create_request(
            s, world.actor_b, Changes(), rnd, lot_id=lot.id, quantity=40
        )
        await rounds.close_round(s, world.admin_actor, Changes(), rnd, version=None)
        await s.commit()
    return rnd, lot


async def test_simultaneous_finalization_has_one_effect(maker, world):
    rnd, lot = await _closed_round(maker, world)
    async with maker() as a, maker() as b:
        await allocation.finalize(a, world.admin_actor, Changes(), rnd)
        pid_b = await backend_pid(b)
        task = asyncio.create_task(allocation.finalize(b, world.admin_actor, Changes(), rnd))
        await wait_until_blocked(maker, pid_b)
        await a.commit()
        with pytest.raises(InvalidState) as err:
            await task
        assert err.value.code == "ALREADY_FINALIZED"
        await b.rollback()
    async with maker() as s:
        assert await s.scalar(select(func.count()).select_from(AllocationResult)) == 2
        fresh = await s.get(InventoryLot, lot.id)
        assert (fresh.quantity_on_hand, fresh.quantity_reserved) == (40, 0)  # 100 - 60 allocated
        await assert_invariants(s)


async def test_finalize_matches_preview_and_conserves_inventory(maker, world):
    rnd, lot = await _closed_round(maker, world, strategy="need_weighted")
    async with maker() as s:
        preview = await allocation.preview(s, rnd, None)
        await s.rollback()
        final = await allocation.finalize(s, world.admin_actor, Changes(), rnd)
        await s.commit()
        assert preview.result_hash == final.result_hash
        assert [ln.allocated for ln in final.lots[0].lines] == [
            40,
            20,
        ]  # 3:1 of 60 → 45/15; A capped at 40, B gets 20
        stored = (
            await s.scalars(
                select(AllocationResult.allocated_quantity).order_by(AllocationResult.request_id)
            )
        ).all()
        assert stored == [40, 20]
        fresh = await s.get(InventoryLot, lot.id)
        assert fresh.quantity_on_hand + sum(stored) == 100
        await assert_invariants(s)


async def test_cancel_releases_reservations(maker, world):
    lot = await make_lot(maker, qty=100)
    rnd = await make_round(maker, world, lots=[(lot.id, 70)])
    async with maker() as s:
        await rounds.cancel_round(s, world.admin_actor, Changes(), rnd, reason="donor recall")
        await s.commit()
        fresh = await s.get(InventoryLot, lot.id)
        assert (fresh.quantity_on_hand, fresh.quantity_reserved) == (100, 0)
        await assert_invariants(s)
        with pytest.raises(InvalidState):
            await allocation.finalize(s, world.admin_actor, Changes(), rnd)


# ---------------------------------------------------------------- idempotency


async def _create_with_key(session, world, rnd, lot_id, key, quantity):
    slot = await idempotency.begin(
        session,
        actor_user_id=world.member_a.id,
        operation="request.create",
        key=key,
        payload={"round_id": rnd, "lot_id": lot_id, "quantity": quantity},
    )
    if isinstance(slot, idempotency.Replay):
        return slot
    req = await req_service.create_request(
        session, world.actor_a, Changes(), rnd, lot_id=lot_id, quantity=quantity
    )
    await idempotency.complete(session, slot, 201, {"id": req.id})
    return req


async def test_concurrent_identical_create_requests_replay(maker, world):
    lot = await make_lot(maker)
    rnd = await make_round(maker, world, lots=[(lot.id, 50)])
    async with maker() as a, maker() as b:
        created = await _create_with_key(a, world, rnd, lot.id, "key-0001", 12)
        pid_b = await backend_pid(b)
        task = asyncio.create_task(_create_with_key(b, world, rnd, lot.id, "key-0001", 12))
        await wait_until_blocked(maker, pid_b)  # blocked on the unique index
        await a.commit()
        replay = await task
        await b.rollback()
    assert isinstance(replay, idempotency.Replay)
    assert replay.response == {"id": created.id}
    async with maker() as s:
        assert await s.scalar(select(func.count()).select_from(AllocationRequest)) == 1
        with pytest.raises(ValidationFailed) as err:
            await _create_with_key(s, world, rnd, lot.id, "key-0001", 13)
        assert err.value.code == "IDEMPOTENCY_KEY_REUSED"


async def test_idempotent_create_after_first_attempt_rolled_back(maker, world):
    lot = await make_lot(maker)
    rnd = await make_round(maker, world, lots=[(lot.id, 50)])
    async with maker() as a, maker() as b:
        await _create_with_key(a, world, rnd, lot.id, "key-0002", 5)
        pid_b = await backend_pid(b)
        task = asyncio.create_task(_create_with_key(b, world, rnd, lot.id, "key-0002", 5))
        await wait_until_blocked(maker, pid_b)
        await a.rollback()  # e.g. the first process crashed before commit
        result = await task
        await b.commit()
    assert isinstance(result, AllocationRequest)
    async with maker() as s:
        assert await s.scalar(select(func.count()).select_from(AllocationRequest)) == 1
