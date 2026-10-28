# Food Bank Allocation System

Distributes donated inventory among food bank organizations. Administrators put stock
into time-boxed **rounds**. Member organizations submit **requests**: integer quantities,
not money. When a round closes, its input is frozen and allocated with one of three
explainable strategies: first come first served, need-weighted share, or priority tiers.
Every result carries a plain-language explanation, every consequential change is
audited, and open browsers update live.

Alongside allocation, the workspace has:

* **Messages**: direct messages, group chats, a network-wide channel for every food bank,
  and a local channel per region. Messages arrive live; unread counts follow you across
  tabs.
* **Planning assistant** (simulated): each food bank keeps preferences, uploads needs
  spreadsheets and notes, and gets three draft request plans for the open rounds. Every
  suggested quantity lists its reasons, and a plan is applied only when a member chooses
  to. Plans come from a deterministic local planner, so no data leaves the deployment.

## Prerequisites

* Docker with Compose v2 (Docker Desktop 4.x or Docker Engine 24+)
* For running tests locally: [uv](https://docs.astral.sh/uv/) ≥ 0.5 (Python 3.13 is
  fetched automatically), Node.js ≥ 22, and `make`

Ports used, all bound to `127.0.0.1`: 8080 (app), 8001/8002 (API processes), 55432
(PostgreSQL), 56379 (Redis).

## Quick start

```bash
make up      # build and start: PostgreSQL, Redis, migrations, two API processes, nginx
make seed    # synthetic demo data (refuses to run on a non-empty database)
open http://localhost:8080
```

Without `make`: `docker compose up -d --build`, then
`docker compose exec api-1 python -m app.cli seed`.

All demo accounts use the password `foodbank-demo`. The seed models a network of eight
fictional food banks with about seven weeks of round history:

| Account | Who | Role |
|---|---|---|
| `admin@example.org` | Avery Thompson | Administrator |
| `priya.raman@example.org` | Priya Raman | Administrator |
| `northside@example.org` | Dana Whitfield, Northside Community Pantry | Member |
| `eastside@example.org` | Jordan Okafor, Eastside Food Shelf | Member |
| `harborpoint@example.org`, `lakeside@example.org`, `maplestreet@example.org`, `riverside@example.org`, `southview@example.org`, `westgate@example.org` | One coordinator per remaining food bank | Member |
| `viewer@example.org` | Casey Morgan | Viewer (public summary only) |

The food banks are spread over three regions (North Valley, Bayfront, South County), each
with its own local channel, and the seed includes several weeks of conversations, a group
chat, planning preferences for four food banks, and Northside's needs spreadsheet,
storage notes and intake survey.

`make reset-demo` wipes and reseeds the local demo database. `make down` stops the stack
and keeps the data; `docker compose down -v` also deletes it.

## Demo walkthrough

1. Sign in as `admin@example.org`. **Overview** shows open rounds, a closed round waiting
   for finalization, produce nearing its best-before date, and recent activity.
2. Open **Week 44 produce and grains** (closed, need-weighted). Under **Preview allocation**,
   switch strategies to compare them (what-if only), then **Finalize**. Westgate is marked
   ineligible in this round, and the reason is in the **Audit log**.
3. Sign in as `northside@example.org` in another browser. Open **Week 45 family staples**
   (open, priority tiers), then request infant formula, edit the quantity, or withdraw it.
   The admin's page updates without reloading, and other members never see Northside's
   requests.
4. **Comparison: beans by …** are three finalized rounds with identical demand (60 cases,
   three requests of 40) under each strategy. Every result explains how it was decided.
5. Create your own lot (**Inventory**) and round (**Rounds → New round**), reserve the lot,
   and open the round.
6. As Northside, open **Messages**: unread conversations are marked, and a reply from the
   admin's browser appears without reloading. Start a direct message or a group with
   **New message** / **New group**.
7. Open **Planning**. **Sample plans** compares Balanced, Priorities first and Lean drafts
   for both open rounds; **Apply to …** submits a round's suggestions as real requests.
   Under **Documents & notes**, upload a CSV or Excel sheet with item and quantity columns
   (or write a note such as `- Rice: 40 bags`) and the plans change. Under **Preferences**,
   **Suggest from history** proposes preferences inferred from past requests.

## Architecture

```
 Browser (React + TypeScript)
   │  HTTP: cookie session + CSRF header     WebSocket: change hints (ids + versions)
   ▼
 nginx :8080 ── static SPA; /api and /api/ws load-balanced round-robin
   ├──► api-1 (FastAPI) ─┐   routers → domain services → pure allocation strategies
   └──► api-2 (FastAPI) ─┤   + per-process WebSocket hub
                         │
          ┌──────────────┴───────────────┐
          ▼                              ▼
   PostgreSQL (authoritative)       Redis pub/sub
   row locks, constraints,          post-commit change hints
   snapshots, results, audit,       between API processes
   idempotency keys                 (nothing durable)
```

* **Modular monolith.** Pure, versioned strategies are in
  [backend/app/allocation](backend/app/allocation). Domain services that own transactions
  and the lock order are in [backend/app/services](backend/app/services). Thin routers are
  in [backend/app/api](backend/app/api).
* **Correctness lives in PostgreSQL.** Every round mutation takes the round row lock.
  Accepted order is assigned under that lock. Closing writes a frozen snapshot. Preview and
  finalize run the same code on that snapshot. Constraints back every invariant.
* **Realtime is best-effort and self-healing.** Events are published to Redis only after
  commit and carry no private data. Clients refetch over HTTP. A periodic version check
  bounds staleness to about 15 s even if Redis is down. Chat uses the same path: an event
  names a conversation and the users allowed to read it, nothing more.
* **Planning is pure and explainable.** The document parser
  ([backend/app/planning/parsing.py](backend/app/planning/parsing.py)) and the planner
  ([backend/app/planning/planner.py](backend/app/planning/planner.py)) are deterministic
  functions with their own unit tests. Uploads are stored in PostgreSQL, size-limited and
  always served as downloads.

## Core workflow

`draft → open → closed → allocated` (cancellation is possible before allocation).

1. The admin adds inventory, creates a round, reserves lots (stock is reserved atomically,
   one active round per lot), adjusts eligibility, weights and tiers, and opens the round.
2. Members submit, edit (version-checked) and withdraw requests until the deadline.
3. The admin closes the round, which freezes requests, supply, eligibility, weights,
   tiers and strategy. They then preview and finalize; finalization is idempotent.
4. Allocated quantity is consumed from stock, and the unallocated remainder is released.
   Members see their results with explanations; viewers see aggregates only.

## Tests

```bash
make install       # local toolchains (uv sync, npm ci, Playwright Chromium)
make check         # ruff format/lint, mypy --strict, eslint, prettier, tsc
make test          # backend: unit + property + PostgreSQL integration + API + realtime; frontend: vitest
make e2e           # browser journeys: allocation, authorization, messaging, planning, phone width (needs `make up && make seed`)
make test-stack    # two-process delivery + Redis outage against the running stack
make e2e-realtime  # browser convergence across processes and during a Redis outage
make perf          # benchmarks; raw output in perf/results/
```

`make test-stack` and `make e2e-realtime` stop and restart the compose Redis container.

## Documentation

* [docs/CONTEXT.md](docs/CONTEXT.md): domain contract, roles, architecture, assumptions
* [docs/ALLOCATION.md](docs/ALLOCATION.md): strategies, rounding, ties, edge cases, fairness tradeoffs
* [docs/DECISIONS.md](docs/DECISIONS.md): consequential decisions and alternatives
* [docs/OPERATIONS.md](docs/OPERATIONS.md): configuration, health, logs, migrations, backup/restore
* [docs/VERIFICATION.md](docs/VERIFICATION.md): what was tested, results, performance, known gaps
* [docs/PLAN.md](docs/PLAN.md): delivery phases and status
