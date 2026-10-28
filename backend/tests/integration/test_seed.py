from sqlalchemy import func, select

from app.models import (
    AllocationResult,
    AuditEntry,
    ChatChannel,
    ChatMessage,
    Organization,
    PlanningDocument,
    PlanningProfile,
    Round,
)
from app.seed import seed
from tests.integration.helpers import assert_invariants


async def test_seed_builds_consistent_demo_showing_strategy_differences(maker):
    async with maker() as s:
        await seed(s)
    async with maker() as s:
        await assert_invariants(s)
        statuses = sorted((await s.scalars(select(Round.status))).all())
        assert statuses == ["allocated"] * 6 + ["cancelled", "closed", "draft", "open", "open"]

        # Backdated history keeps each round's timeline in order.
        out_of_order = await s.scalar(
            select(func.count())
            .select_from(Round)
            .where(
                Round.status == "allocated",
                ~((Round.opened_at < Round.closed_at) & (Round.closed_at <= Round.allocated_at)),
            )
        )
        assert out_of_order == 0
        span = await s.scalar(
            select(func.max(AuditEntry.created_at) - func.min(AuditEntry.created_at))
        )
        assert span.days >= 40

        kinds = sorted((await s.scalars(select(ChatChannel.kind))).all())
        assert kinds == ["direct"] * 3 + ["global", "group"] + ["local"] * 3
        out_of_order = await s.scalar(
            select(func.count())
            .select_from(ChatMessage)
            .join(
                later := ChatMessage.__table__.alias("later"),
                (later.c.channel_id == ChatMessage.channel_id) & (later.c.id > ChatMessage.id),
            )
            .where(later.c.created_at < ChatMessage.created_at)
        )
        assert out_of_order == 0  # message ids follow time within each conversation
        assert await s.scalar(select(func.count()).select_from(PlanningProfile)) == 4
        docs = (await s.scalars(select(PlanningDocument))).all()
        assert sum(len(d.analysis["needs"]) for d in docs) == 8

        names = {o.id: o.name.split()[0] for o in await s.scalars(select(Organization))}
        outcomes = {}
        for rnd in await s.scalars(select(Round).where(Round.name.like("Comparison:%"))):
            rows = await s.scalars(
                select(AllocationResult).where(AllocationResult.round_id == rnd.id)
            )
            outcomes[rnd.strategy] = {names[r.organization_id]: r.allocated_quantity for r in rows}
    # 60 cases; Eastside asked first, then Riverside, then Northside (40 each).
    # Weights E2/R1/N3; tiers N1, E2, R3.
    assert outcomes["fcfs"] == {"Eastside": 40, "Riverside": 20, "Northside": 0}
    assert outcomes["need_weighted"] == {"Eastside": 20, "Riverside": 10, "Northside": 30}
    assert outcomes["priority"] == {"Northside": 40, "Eastside": 20, "Riverside": 0}
