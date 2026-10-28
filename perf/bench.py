"""Bounded, reproducible performance runner.

    uv run --project backend python perf/bench.py alloc          # pure allocation, no services
    uv run --project backend python perf/bench.py http           # needs the compose stack + seed

Every sample is timed with one clock (time.perf_counter_ns) in this process. HTTP
samples end after the full response body has been read and its status checked.
Raw samples and environment metadata are written to perf/results/<name>-<time>.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import random
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.allocation import CURRENT_VERSION, LotInput, RequestInput, run  # noqa: E402

RESULTS = ROOT / "perf" / "results"
ORIGIN = "http://localhost:8080"
PASSWORD = os.environ.get("PERF_PASSWORD", "foodbank-demo")
MEMBERS = ["northside", "eastside", "riverside", "westgate"]


# A percentile is reported only when the sample can support it (p99 needs >= 100).
_MIN_SAMPLES = {50: 1, 95: 20, 99: 100}


def pct(samples_ms: list[float], p: int) -> float | None:
    """Nearest-rank percentile, or None when the sample is too small."""
    if len(samples_ms) < _MIN_SAMPLES[p]:
        return None
    ordered = sorted(samples_ms)
    return round(ordered[math.ceil(p / 100 * len(ordered)) - 1], 4)


def summarize(samples_ms: list[float]) -> dict[str, Any]:
    return {
        "n": len(samples_ms),
        "mean_ms": round(statistics.fmean(samples_ms), 4) if samples_ms else None,
        "p50_ms": pct(samples_ms, 50),
        "p95_ms": pct(samples_ms, 95),
        "p99_ms": pct(samples_ms, 99),
        "max_ms": round(max(samples_ms), 4) if samples_ms else None,
    }


def environment() -> dict[str, Any]:
    info = time.get_clock_info("perf_counter")
    # Timer overhead: cost of two back-to-back reads, the floor of any sample.
    overhead = []
    for _ in range(10_000):
        a = time.perf_counter_ns()
        overhead.append(time.perf_counter_ns() - a)
    git = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "git_commit": git or None,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "clock": {
            "name": "perf_counter_ns",
            "resolution_s": info.resolution,
            "monotonic": info.monotonic,
            "read_overhead_ns_median": statistics.median(overhead),
        },
    }


def write(name: str, payload: dict[str, Any]) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{name}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


# ---------------------------------------------------------------- allocation


def make_lot(n: int, seed: int) -> LotInput:
    rng = random.Random(seed)
    reqs = tuple(
        RequestInput(
            request_id=i + 1,
            organization_id=i + 1,
            quantity=rng.randint(1, 200),
            accepted_seq=i + 1,
            eligible=rng.random() > 0.05,
            weight=rng.randint(1, 20),
            priority=rng.randint(1, 10),
        )
        for i in range(n)
    )
    demand = sum(r.quantity for r in reqs)
    return LotInput(lot_id=1, unit="cases", supply=demand // 2, requests=reqs)


def bench_alloc(args: argparse.Namespace) -> None:
    sizes = [10, 100, 1000, 5000]
    results = []
    for strategy in sorted(CURRENT_VERSION):
        for n in sizes:
            lot = make_lot(n, seed=n)
            iterations = max(20, min(2000, 200_000 // n))
            for _ in range(max(5, iterations // 10)):  # warmup
                run(strategy, 1, [lot])
            samples = []
            for _ in range(iterations):
                t0 = time.perf_counter_ns()
                run(strategy, 1, [lot])
                samples.append((time.perf_counter_ns() - t0) / 1e6)
            row = {
                "strategy": strategy,
                "requests": n,
                "iterations": iterations,
                **summarize(samples),
            }
            results.append(row)
            print(f"{strategy:14} n={n:5}  p50={row['p50_ms']:.3f} ms  p95={row['p95_ms']} ms")
    path = write(
        "alloc",
        {
            "kind": "allocation-microbenchmark",
            "env": environment(),
            "workload": "one lot, supply = half of demand, 5% ineligible, "
            "weights 1-20, tiers 1-10 (seeded RNG)",
            "results": results,
        },
    )
    print(f"raw results: {path.relative_to(ROOT)}")


# ---------------------------------------------------------------- HTTP / database


async def login(client: Any, email: str) -> str:
    r = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    r.raise_for_status()
    return str(r.json()["csrf_token"])


async def timed(samples: list[float], errors: dict[str, int], coro: Any, ok: set[int]) -> Any:
    t0 = time.perf_counter_ns()
    try:
        r = await coro
        elapsed = (time.perf_counter_ns() - t0) / 1e6
        if r.status_code in ok:
            samples.append(elapsed)
        else:
            key = f"HTTP {r.status_code}"
            errors[key] = errors.get(key, 0) + 1
        return r
    except Exception as exc:  # timeouts and connection errors are counted, not raised
        errors[type(exc).__name__] = errors.get(type(exc).__name__, 0) + 1
        return None


async def bench_http(args: argparse.Namespace) -> None:
    import httpx

    base = args.base_url
    limits = httpx.Limits(max_connections=args.concurrency * 2)
    timeout = httpx.Timeout(10.0)
    admin = httpx.AsyncClient(base_url=base, headers={"Origin": ORIGIN}, timeout=timeout)
    csrf = await login(admin, "admin@example.org")
    h = {"X-CSRF-Token": csrf}

    # Workload setup: one open round with `lots` lots of ample stock.
    stamp = datetime.now(UTC).strftime("%m%d%H%M%S")
    rnd = (
        await admin.post(
            "/api/admin/rounds",
            headers=h,
            json={
                "name": f"Perf {stamp}",
                "strategy": "need_weighted",
                "request_deadline": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
            },
        )
    ).json()
    lot_ids = []
    for i in range(args.lots):
        lot = (
            await admin.post(
                "/api/admin/lots",
                headers=h,
                json={"name": f"Perf lot {stamp}-{i}", "unit": "cases", "quantity_on_hand": 1000},
            )
        ).json()
        await admin.post(
            f"/api/admin/rounds/{rnd['id']}/lots",
            headers=h,
            json={"lot_id": lot["id"], "quantity": 500},
        )
        lot_ids.append(lot["id"])
    (await admin.post(f"/api/admin/rounds/{rnd['id']}/open", headers=h, json={})).raise_for_status()

    members = []
    for m in MEMBERS:
        c = httpx.AsyncClient(
            base_url=base, headers={"Origin": ORIGIN}, timeout=timeout, limits=limits
        )
        members.append((c, await login(c, f"{m}@example.org")))

    phases: dict[str, Any] = {}

    # Phase 1: contended creates — every organization requests every lot at once.
    samples: list[float] = []
    errors: dict[str, int] = {}
    sem = asyncio.Semaphore(args.concurrency)

    async def create(c: Any, token: str, lot_id: int) -> None:
        async with sem:
            await timed(
                samples,
                errors,
                c.post(
                    f"/api/rounds/{rnd['id']}/requests",
                    json={"lot_id": lot_id, "quantity": 10},
                    headers={
                        "X-CSRF-Token": token,
                        "Idempotency-Key": f"perf-{stamp}-{id(c)}-{lot_id}",
                    },
                ),
                {201},
            )

    t0 = time.perf_counter_ns()
    await asyncio.gather(*(create(c, t, lot) for c, t in members for lot in lot_ids))
    wall = (time.perf_counter_ns() - t0) / 1e9
    phases["create_requests"] = {
        "operations": len(members) * len(lot_ids),
        "concurrency": args.concurrency,
        "loop": "closed (each worker issues its next request when the previous completes)",
        "wall_s": round(wall, 3),
        "achieved_per_s": round(len(samples) / wall, 1),
        "errors": errors,
        **summarize(samples),
    }

    # Phase 2: edits and reads for a fixed duration after warmup.
    detail_url = f"/api/rounds/{rnd['id']}"
    own: dict[int, list[dict[str, Any]]] = {}
    for i, (c, _) in enumerate(members):
        own[i] = [
            r for r in (await c.get(detail_url)).json()["requests"] if r["status"] == "active"
        ]

    async def mixed(
        duration: float, record: bool
    ) -> tuple[list[float], list[float], dict[str, int], float]:
        reads: list[float] = []
        writes: list[float] = []
        errs: dict[str, int] = {}
        stop = time.perf_counter() + duration

        async def worker(w: int) -> None:
            i = w % len(members)
            c, token = members[i]
            rng = random.Random(w)
            while time.perf_counter() < stop:
                if rng.random() < args.write_ratio and own[i]:
                    req = rng.choice(own[i])
                    r = await timed(
                        writes if record else [],
                        errs,
                        c.patch(
                            f"/api/requests/{req['id']}",
                            headers={"X-CSRF-Token": token},
                            json={"quantity": rng.randint(1, 50), "version": req["version"]},
                        ),
                        {200},
                    )
                    if r is not None and r.status_code == 200:
                        req["version"] = r.json()["version"]
                else:
                    await timed(reads if record else [], errs, c.get(detail_url), {200})

        started = time.perf_counter_ns()
        await asyncio.gather(*(worker(w) for w in range(args.concurrency)))
        return reads, writes, errs, (time.perf_counter_ns() - started) / 1e9

    await mixed(args.warmup, record=False)
    reads, writes, errs, wall = await mixed(args.duration, record=True)
    phases["mixed_read_edit"] = {
        "concurrency": args.concurrency,
        "duration_s": args.duration,
        "warmup_s": args.warmup,
        "write_ratio": args.write_ratio,
        "loop": "closed",
        "note": "HTTP 409 here is the expected VERSION_CONFLICT: several workers per "
        "organization edit the same requests concurrently with optimistic versions.",
        "wall_s": round(wall, 3),
        "achieved_per_s": round((len(reads) + len(writes)) / wall, 1),
        "errors": errs,
        "reads": summarize(reads),
        "edits": summarize(writes),
    }

    # Phase 3: close + finalize the round (single operations, reported individually).
    s: list[float] = []
    e: dict[str, int] = {}
    await timed(s, e, admin.post(f"/api/admin/rounds/{rnd['id']}/close", headers=h, json={}), {200})
    await timed(
        s,
        e,
        admin.post(
            f"/api/admin/rounds/{rnd['id']}/finalize",
            headers={**h, "Idempotency-Key": f"perf-fin-{stamp}"},
        ),
        {200},
    )
    phases["close_finalize_ms"] = {
        "close": s[0] if s else None,
        "finalize": s[1] if len(s) > 1 else None,
        "errors": e,
        "requests_in_round": len(members) * len(lot_ids),
    }

    for c, _ in members:
        await c.aclose()
    await admin.aclose()
    path = write(
        "http",
        {
            "kind": "http-db",
            "env": environment(),
            "base_url": base,
            "workload": {"lots": args.lots, "organizations": len(members)},
            "phases": phases,
        },
    )
    print(json.dumps(phases, indent=2))
    print(f"raw results: {path.relative_to(ROOT)}")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("alloc")
    h = sub.add_parser("http")
    h.add_argument("--base-url", default="http://localhost:8080")
    h.add_argument("--lots", type=int, default=25)
    h.add_argument("--concurrency", type=int, default=16)
    h.add_argument("--duration", type=float, default=20.0)
    h.add_argument("--warmup", type=float, default=5.0)
    h.add_argument("--write-ratio", type=float, default=0.2)
    args = p.parse_args()
    if args.cmd == "alloc":
        bench_alloc(args)
    else:
        asyncio.run(bench_http(args))


if __name__ == "__main__":
    main()
