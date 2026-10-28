# Context

The implemented domain contract, roles, architecture and assumptions. Decisions and
their reasons are in [DECISIONS.md](DECISIONS.md). Algorithm details are in
[ALLOCATION.md](ALLOCATION.md).

## Starting point

The repository began with only a placeholder README. There was no existing code,
schema, or product contract to preserve, so everything below was built from scratch.

## Domain contract (implemented)

| Concept | Meaning |
|---|---|
| Organization | A food bank. Has default need weight (1–1000) and default priority tier (1–10). Can be deactivated. |
| User | `admin`, `member` (belongs to exactly one organization), or `viewer`. Enforced by a DB check constraint. |
| Lot | Donated inventory with one unit (e.g. "cases"), `quantity_on_hand`, `quantity_reserved` (`0 ≤ reserved ≤ on_hand`), optional best-before date. |
| Round | `draft → open → closed → allocated`; `cancelled` is reachable from draft/open/closed. Has one strategy and a request deadline. |
| Round lot | A reservation of a lot quantity for one round. A lot can be reserved by at most one active round (partial unique index). |
| Participant | Per-round, per-organization eligibility, weight and priority. Created from organization defaults when the round is created; editable until close. |
| Request ("bid") | A positive integer quantity of one round lot requested by one organization. At most one *active* request per (round, lot, organization). |
| Snapshot | Frozen JSON allocation input written at close: supply per lot, active requests with accepted order, eligibility/weight/priority, strategy and strategy version, plus a SHA-256 input hash. |
| Allocation result | One persisted row per request at finalization, with allocated quantity, explanation text and structured details. |
| Region | Optional label on an organization. Organizations in a region share one local chat channel. |
| Channel | A conversation: `global` (every admin and member), `local` (one region), `group` (named, explicit members) or `direct` (exactly two people, unique per pair). |
| Message | Up to 4000 characters, posted by a member or admin, or a system line ("Morgan added Dana"). Soft-deleted by its sender or an admin. |
| Planning profile | One organization's stated preferences: households served, ranked priority categories, avoided categories, storage capacities, largest share of a lot, dietary notes. Version-checked. |
| Planning document | An uploaded file (stored bytes plus what the parser extracted) or a typed note. Feeds the planner. |
| Sample plan | A draft set of requests for the open rounds, computed on demand and never stored. Three variants: Balanced, Priorities first, Lean. |

Rules:

* A "bid" is a quantity request, never money. The UI says "request".
* Requests may be submitted, edited and withdrawn only while the round is `open`
  and before its deadline (server clock). Edits keep accepted order; withdraw +
  resubmit gets a new position. Edits require the current `version` (optimistic
  concurrency); stale versions get `409`.
* Accepted order is a per-round counter assigned while holding the round row lock,
  so for any two requests in the same round, order equals commit order.
* Closing locks the round, so every request either committed before the close
  (and is in the snapshot) or observes `closed` and is rejected.
* Preview and finalization both run the registered strategy implementation for the
  snapshot's `(strategy, strategy_version)` over the snapshot JSON.
* Finalization: consumes allocated quantity from `on_hand`, releases the whole
  reservation from `reserved`, marks round lots `consumed`. `on_hand_before =
  on_hand_after + allocated`.
* Cancellation (before allocation) releases reservations; nothing is consumed.
* Partial fulfilment allowed; `0 ≤ allocated ≤ requested`; Σ allocated per lot ≤
  reserved; ineligible organizations receive 0 with an explanation.

## Roles and visibility

| Capability | admin | member | viewer |
|---|---|---|---|
| Public round summaries (non-draft rounds, lots, aggregate outcomes) | ✓ | ✓ | ✓ |
| Round detail with lots and own organization's requests/eligibility | all orgs | own org | – |
| Submit/edit/withdraw requests | – | own org | – |
| Results with explanations | all | own org | aggregates only |
| Inventory, rounds, participants, organizations, users | manage | – | – |
| Audit log | read | – | – |
| Global channel; direct messages and groups they belong to | ✓ | ✓ | – |
| Local channels | all regions (moderation) | own region | – |
| Delete messages | any (audited) | own | – |
| Planning workspace (preferences, documents, plans) | any organization | own org | – |
| Apply a plan as requests | – | own org | – |

Authorization is enforced server-side per request (HTTP) and per connection with
periodic re-validation (WebSocket). Self-registration creates viewers only; roles
can only be assigned by an admin, and an admin cannot change their own role or
deactivate themselves.

## Architecture

Modular monolith:

```
browser ──► nginx (static SPA + /api proxy, round-robin) ──► api-1 ─┐
                                                          └► api-2 ─┤──► PostgreSQL (authoritative)
                                                                    └──► Redis pub/sub (notifications only)
```

* `backend/app/allocation/` — pure, deterministic strategy functions (no I/O).
* `backend/app/services/` — domain services; own transactions' business rules and lock order; return events.
* `backend/app/api/` — thin FastAPI routers: auth, validation, commit, publish.
* `backend/app/realtime/` — Redis publisher/subscriber, per-process WebSocket hub.
* `frontend/` — React + TypeScript (Vite), TanStack Query for server state; a
  realtime provider turns WebSocket hints and periodic version checks into cache
  invalidations.

## Scope

In scope: inventory, rounds, requests, three allocation strategies, results with
explanations, audit, authorized live updates, local operation. Out of scope (initial): fulfilment
logistics, reversals of allocations, payments, donor CRM, mobile apps, public cloud
deployment, durable event streams.

## Assumptions

* Weights are integers 1–1000; priority tiers are integers 1–10 where **1 is the
  highest priority** (served first).
* Deadline blocks member changes after it passes but does not auto-close the round;
  closing is an explicit admin action.
* A round's strategy may change while draft/open (audited); it is frozen at close.
* Member explanations may contain aggregates (e.g. "earlier requests used 50 of 60
  cases") but never another organization's identity or individual values.
