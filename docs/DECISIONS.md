# Decisions

Consequential choices only. Each entry: decision, alternatives, reason.

## D1. Modular monolith, async SQLAlchemy + psycopg 3
FastAPI app with pure allocation module, domain services, thin routers. Async
SQLAlchemy 2 over psycopg 3 so HTTP, WebSockets and Redis share one event loop.
*Alternatives:* sync SQLAlchemy in a threadpool (mixes models with async WS code).

## D2. Server-side opaque sessions instead of JWT
Login issues a 256-bit random token (`secrets.token_urlsafe`) in an `HttpOnly`,
`SameSite=Lax` cookie (`Secure` outside development); only its SHA-256 hash is
stored. Sessions have an absolute 8-hour lifetime and no refresh tokens: users
re-login after a working day, and server-side state gives immediate logout,
deactivation and membership changes without a token denylist. Unsafe methods
require an `X-CSRF-Token` header equal to the session's CSRF token (synchronizer
pattern) and an allowed `Origin` when present. Authorization reads the current user
row on every request, so role/org changes apply on the next HTTP call.
*Alternatives:* JWT access + refresh tokens (revocation needs a denylist anyway).

## D3. Round row lock serializes all round mutations
Every write that touches a round (request create/edit/withdraw, participant changes,
reservations, open/close/cancel/finalize) begins with `SELECT … FROM rounds … FOR
UPDATE`. Accepted order is `rounds.next_request_seq`, incremented under that lock,
so order equals commit order within a round, and request-vs-close is decided by
lock acquisition order. Cost: request writes in one round are serialized (a few ms
each) — acceptable for food-bank scale (tens of organizations).
*Alternatives:* `SERIALIZABLE` with retries (harder to explain, retry loops in every
path); a sequence (`nextval` order ≠ commit order).

**Lock order (all paths):** idempotency-key row → `rounds` → `inventory_lots`
(ascending id) → `round_lots`/`round_participants`/`allocation_requests`.
Inventory adjustments without a round lock only the lot. No path locks a lot and
then a round, so the order is acyclic.

## D4. Explicit snapshot at close
Close writes `round_snapshots.input` (canonical JSON) and its SHA-256 hash. Preview
and finalize read only the snapshot, so later edits to organizations, defaults or
code cannot change frozen input. Services also reject mutations of closed rounds.

## D5. Strategy versioning
Strategies are registered by `(name, version)`. The snapshot stores both; persisted
results are never recomputed. A behaviour change must add a new version and keep the
old implementation registered while unfinalized snapshots reference it.

## D6. Idempotency keys for create-request and finalize
`Idempotency-Key` header (required). Row `(actor_user_id, operation, key)` is inserted
**first in the same transaction** as the effect. A concurrent duplicate blocks on the
unique index until the first commits, then replays the stored response; if the first
rolled back, the duplicate proceeds. Same key + different payload hash → `422
IDEMPOTENCY_KEY_REUSED`. Only successful responses are stored (failures roll back and
can be retried). Lost responses are recovered by retrying with the same key.
Finalize is additionally state-guarded: a different key after allocation gets `409`.
Edits/withdrawals use `version` checks instead.

## D7. Notifications are invalidation hints; the DB is authoritative
Events carry only `{type, round_id, version}` (no request data). Published to Redis
**after commit**; failure is logged/counted and never changes the HTTP outcome. If
publish fails, the event is delivered to sockets on the same process as a fallback.
Three version counters prevent cross-audience leakage:
`rounds.public_version` (status, lots, deadline, results), `round_participants.version`
(one organization's requests/eligibility) and `rounds.version` (everything, admins).
*Alternatives:* outbox/durable stream — unnecessary because clients recover by
refetching versioned state.

## D8. Recovery protocol and bound
Client: connect WS → on `hello` invalidate all cached queries (snapshot after
subscribe) → apply events whose version exceeds the cached version → every 15 s
fetch `/api/sync/versions` and refetch stale rounds → reconnect with capped
exponential backoff. Server: on Redis subscriber reconnect, send `resync` to every
local socket; slow sockets have a 100-message queue that collapses into a single
`resync` on overflow. **Bound:** a connected client converges within 15 s + one fetch
of any commit even if every notification is lost (documented as ≤ 20 s and tested).

## D9. Redis outage = degraded, not unready
`/readyz` fails only when Postgres is unreachable or migrations are not at head.
Redis status is reported as `degraded`. Writes and reads never depend on Redis;
live updates degrade to same-process delivery plus periodic resync.

## D10. Need-weighted = weighted water-filling with exact fractions
See [ALLOCATION.md](ALLOCATION.md). Python `fractions.Fraction`; largest remainder with
ties by earlier accepted order, then request id.

## D11. Priority direction: tier 1 is highest
Smaller number = served earlier. Matches "Tier 1" language in UI.

## D12. Login throttling in Postgres
Failed logins are recorded per normalized email; ≥5 failures in 15 minutes returns
`429` across all processes. A small in-process per-IP limiter bounds login and
registration attempts per process. No IP addresses are persisted.

## D13. Single organization per member
`users.organization_id` with a check constraint (`member ⇔ organization set`) instead
of a many-to-many membership table. Simpler authorization; moving a member is an
audited admin action.

## D14. Round settings edits are checked against `public_version`
Admin edits to name, deadline, strategy or description send the `public_version` they
were based on. Checking `rounds.version` instead would make every member request
invalidate an open settings form. Status transitions (open/close) carry no version: they
are deliberate actions on the current state and are idempotent by state.

## D15. Inventory descriptions are immutable while reserved
While a lot is reserved by a round, only its on-hand quantity can change. Renaming a lot
or changing its unit mid-round would alter what members requested, and it would need the
round lock, which inventory updates deliberately never take (lock order, D3).

## D16. Benchmark targets come from measured baselines
Targets in [VERIFICATION.md](VERIFICATION.md#targets-and-rationale) are set from measured
p95 values with headroom for slower hardware, and checked against the expected workload
(tens of organizations, a few writes per second per round at peak). They are not
service-level objectives for an unknown deployment.

## D17. Chat channels: implicit network membership, explicit groups
`global` and `local` channels have no member rows: who can read them follows from role,
active status and the organization's region, so moving an organization to another region
or deactivating a user takes effect immediately with no membership sync to drift.
`group` and `direct` channels list members in `chat_members`; a pair of people has
exactly one direct channel (unique `direct_key`). Network channels are created on first
use with `INSERT … ON CONFLICT DO NOTHING` against partial unique indexes, so concurrent
first visits converge. Admins can read every local channel to moderate, but not other
people's groups or direct messages. A channel the reader cannot access answers 404, not
403, so ids do not reveal which conversations exist.

Realtime reuses D7: an event carries the channel id and the user ids allowed to read it,
and each API process forwards it only to those users' sockets. Recovery reuses D8: the
version endpoint returns a per-user chat fingerprint (channel count, summed channel
versions, summed read positions); a change makes the client refetch.

Posting is idempotent per `(sender, client_id)`: the browser generates the id, and a
retry, including a concurrent one that waits on the unique index, returns the original
message. A per-user limit (`FB_CHAT_MESSAGES_PER_MINUTE`) slows floods. Moderation
deletes are audited without copying the message body into the audit log.

Alternatives considered: a third-party chat service (it would put conversation data
outside the deployment and need its own auth bridge), and per-user Redis topics (more
publishes per message, with no gain at this scale).

## D18. Uploads: raw body, stored in PostgreSQL, parsed defensively
Files are sent as the raw request body (`POST /api/planning/documents?filename=…`) rather
than multipart. That avoids a multipart parser and lets the server count bytes while
streaming. The limit (`FB_MAX_UPLOAD_BYTES`, default 5 MB) is enforced three times: by
nginx (`client_max_body_size` on that one location only), by the middleware on
`Content-Length`, and while reading the stream for chunked bodies. Bytes are kept in
PostgreSQL (`planning_documents.content`, deferred so listings never load it). Both API
processes then see the same files, backups already cover them, and no shared volume is
needed. For a few files per food bank this is the simplest correct option; object
storage would be the next step at much larger volumes.

Parsing treats every file as hostile. Workbooks are rejected before openpyxl opens them
if their decompressed size or entry count is too large (zip-bomb guard), and openpyxl
uses defusedxml. Text with NUL bytes is not parsed. Rows, columns, cells, needs and
stored text are all capped, and parsing runs in a worker thread, off the event loop.
Downloads are always `Content-Disposition: attachment` with `Content-Security-Policy:
sandbox` and `nosniff`, so an uploaded HTML or SVG file can never run in the app's
origin. Filenames are reduced to a base name without control characters.

## D19. The planning assistant is a deterministic, explainable planner
The planning section is presented as an assistant, but plans come from a pure function
of the organization's preferences, its documents' extracted needs, its request history
and the open rounds it is eligible for. It does not call a language model. That makes
plans reproducible and testable, keeps every input inside the deployment, and lets each
quantity carry the exact reasons behind it, which matters because allocation itself is
explainable (D10). The UI labels the feature "Simulated AI" so nobody mistakes it for a
model.

Plans are computed on request, never stored, and never submitted on their own. "Apply"
uses the ordinary request endpoints (idempotency keys for creates, versions for edits),
so a plan cannot bypass deadlines, eligibility or concurrency rules. The planner never
suggests more than the stated share of a lot or the stated storage capacity. Where no
document lists a quantity, an existing request stands in for the need, so defaults never
quietly shrink what a food bank already asked for. "Suggest from history" proposes
preferences from past requests, but never lowers a storage capacity the food bank
stated (storage is a physical fact, and history only gives a lower bound).

A language model could later be added behind the same planner interface to phrase
summaries or read free-form notes. It would need an explicit data-sharing decision
first, so it was left out.
