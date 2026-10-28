# Plan

Status legend: ✅ done · 🔄 in progress · ⏳ planned

Evidence for every ✅ is in [VERIFICATION.md](VERIFICATION.md).

## Phase 1 — Foundation and domain contract ✅
* Compose stack: PostgreSQL, Redis, one-shot migration job, two API processes, and
  nginx with shared round-robin.
* Schema and Alembic migration with FK, unique, partial-unique and CHECK constraints.
  Settings validation with readable startup errors.
* Server-side sessions (HttpOnly cookie plus CSRF token), Argon2 hashing, role and
  object-level authorization, consistent JSON errors, request ids, redacted JSON logs.

Acceptance: a fresh DB migrates; the seed creates all roles, eight food banks and several weeks of history;
cross-organization reads and writes are rejected; registration cannot grant privileges;
logs and responses contain no secrets. **Met.**

## Phase 2 — One complete workflow ✅
Inventory, rounds, reservations, participants, requests, close, preview, finalize,
results, and UI screens for each.

Acceptance: the browser test completes the workflow; invalid transitions and late
requests are rejected; previews equal stored results; repeated finalization has one
effect. **Met.**

## Phase 3 — Allocation correctness and concurrency ✅
Need-weighted and priority strategies, explanations, hand-computed and property tests,
idempotency keys, and PostgreSQL race tests with lock-wait barriers.

Acceptance: race tests for reservations, competing requests, edit conflicts,
request-vs-close, competing finalization and retries, all asserting persisted state.
**Met.**

## Phase 4 — Reliable live updates ✅
Redis pub/sub fan-out, authorized WebSockets, version-based invalidation, periodic
resync, slow-client coalescing, session re-validation.

Acceptance: clients on different API processes see committed changes; no private data
crosses organizations; Redis outage and reconnect converge within the documented bound.
**Met.**

## Phase 5 — Operation and delivery ✅
CI workflow, seed/reset/prune commands, health and readiness, a performance runner with
raw results, documentation, and a phone-width check.

## Phase 6 — Messaging and planning workspace ✅
Direct messages, groups, a network channel and regional channels with live delivery,
unread state and moderation; a planning workspace with preferences, file uploads and
notes, a document parser and a deterministic planner producing three explainable sample
plans that members can apply as requests. The UI moved to a sidebar layout with a
consistent design system.

Acceptance: chat access rules hold for every role and region; messages arrive live across
API processes and converge through the version endpoint; uploads are bounded and safe to
download; plans are deterministic, respect preferences and capacity, and applying one
goes through the normal request rules. **Met.**

## Next actions (not started)
* Run the CI workflow on GitHub once the repository is pushed.
* Add idle session timeout if pilot users leave shared terminals signed in.
* Set up scheduled backups and a restore drill for any shared deployment
  ([OPERATIONS.md](OPERATIONS.md#backup-and-restore)).
* Decide a message retention period and add it to `prune`.
* Decide whether planning may send data to an external language model (DECISIONS D19)
  before adding one.
