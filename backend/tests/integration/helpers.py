"""Concurrency helpers. A "barrier" here is deterministic: transaction A holds its
locks uncommitted while B is started; we then wait until PostgreSQL reports B's
backend as waiting on a lock before letting A commit. No timing sleeps decide the
interleaving."""

from __future__ import annotations

import asyncio

from sqlalchemy import text


async def backend_pid(session) -> int:
    return await session.scalar(text("SELECT pg_backend_pid()"))


async def wait_until_blocked(maker, pid: int, timeout: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with maker() as s:
        while True:
            state = await s.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            )
            await s.rollback()  # pg_stat_activity is snapshotted per transaction
            if state == "Lock":
                return
            if loop.time() > deadline:
                raise AssertionError(f"backend {pid} never blocked on a lock (state={state})")
            await asyncio.sleep(0.005)


async def assert_invariants(session) -> None:
    """Database-wide inventory and allocation invariants."""
    bad = (
        await session.execute(
            text("""
            SELECT l.id, l.quantity_reserved, COALESCE(SUM(rl.quantity), 0) AS active
            FROM inventory_lots l
            LEFT JOIN round_lots rl ON rl.lot_id = l.id AND rl.status = 'reserved'
            GROUP BY l.id
            HAVING l.quantity_reserved <> COALESCE(SUM(rl.quantity), 0)
                OR l.quantity_reserved > l.quantity_on_hand
            """)
        )
    ).all()
    assert bad == [], f"reserved quantity mismatch: {bad}"
    bad = (
        await session.execute(
            text("""
            SELECT rl.round_id, rl.lot_id, rl.allocated_quantity, rl.quantity,
                   COALESCE(SUM(r.allocated_quantity), 0)
            FROM round_lots rl
            LEFT JOIN allocation_results r ON r.round_id = rl.round_id AND r.lot_id = rl.lot_id
            WHERE rl.status = 'consumed'
            GROUP BY rl.round_id, rl.lot_id
            HAVING rl.allocated_quantity <> COALESCE(SUM(r.allocated_quantity), 0)
                OR rl.allocated_quantity > rl.quantity
            """)
        )
    ).all()
    assert bad == [], f"allocation totals mismatch: {bad}"
    bad = (
        await session.execute(
            text("""
            SELECT r.request_id FROM allocation_results r
            JOIN round_snapshots s ON s.round_id = r.round_id
            CROSS JOIN LATERAL jsonb_array_elements(s.input->'lots') lot
            CROSS JOIN LATERAL jsonb_array_elements(lot->'requests') req
            WHERE (req->>'request_id')::bigint = r.request_id
              AND (req->>'eligible')::boolean = false AND r.allocated_quantity > 0
            """)
        )
    ).all()
    assert bad == [], f"allocated to ineligible organizations: {bad}"
    await session.rollback()
