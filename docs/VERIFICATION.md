# Verification

All results below were produced on 2026-10-27 against a database created from an empty
Docker volume (`docker compose down -v && make up && make seed`).

**Environment:** Apple M4 (10 cores, 16 GB), macOS 15.6.1, Docker Desktop VM with 10 CPUs
and 8 GB. Containers: PostgreSQL 17.11, Redis 7.4.12, nginx 1.27.5, Python 3.13.18. The
test runner used Python 3.13.14 and Node 25.8.1 with Playwright 1.64 and Chromium.

## Commands and outcomes

| Command | What it covers | Result |
|---|---|---|
| `make up && make seed` (fresh volume) | Migrations from empty, two API processes healthy, nginx, seed via services | ✅ ready in ~26 s |
| `make check` | ruff format + lint, mypy `--strict`, eslint, prettier, `tsc` | ✅ clean |
| `alembic check` | ORM models match migrations | ✅ no drift |
| `make test-backend` | 116 tests: 41 unit/property (allocation, planner, file parser), 14 PostgreSQL integration, 61 API (incl. 10 realtime, 8 messaging, 5 planning) | ✅ 116 passed |
| `make test-frontend` | Vitest: version-gap logic, stale-round detection, backoff | ✅ 5 passed |
| `make e2e` | Browser: allocation journey, 4 authorization checks, live direct messages both ways, group creation, upload → plan → apply as requests, suggested preferences, two phone-width layouts | ✅ 11 passed |
| `make test-stack` | Two containers behind nginx: cross-process delivery both ways; Redis stopped and restarted | ✅ 2 passed |
| `make e2e-realtime` | Browser pages pinned to api-1 and api-2; convergence during a Redis outage; live delivery resumes after it | ✅ 1 passed |
| `make perf` | Benchmarks (see [Performance](#performance)) | ✅ results in `perf/results/` |

CI ([.github/workflows/ci.yml](../.github/workflows/ci.yml)) runs the same steps: lint and
types, a fresh-database migration plus `alembic check`, backend tests against PostgreSQL and
Redis service containers, the frontend build, then the compose stack with the browser smoke
test and the stack/realtime tests. The workflow has not been run on GitHub from this
environment, because nothing has been pushed.

## Critical invariants and where they are proven

Race tests ([test_concurrency.py](../backend/tests/integration/test_concurrency.py)) use two
independent PostgreSQL connections. Transaction A holds its locks uncommitted, and B is
started. The test waits until `pg_stat_activity` shows B **waiting on a lock**, then commits
or rolls back A. No sleeps decide the interleaving. Every test asserts persisted state.

| Invariant | Test(s) |
|---|---|
| A lot is reserved by at most one active round; competing reservations → one wins | `test_competing_reservations_of_one_lot`, `test_partial_unique_index_backs_up_reservation_check` |
| On-hand stock never drops below reserved (service check and CHECK constraint) | `test_stock_reduction_racing_reservation`, `test_lot_reduction_below_reserved_rejected` |
| One active request per org per lot under concurrent creates | `test_competing_requests_from_same_organization` |
| Concurrent edits never silently overwrite (version check) | `test_concurrent_edits_do_not_overwrite`, API `test_full_workflow` |
| Edit keeps accepted order; withdraw + resubmit goes to the back | `test_edit_keeps_order_and_resubmission_gets_new_position` |
| A request racing close is either in the snapshot or rejected, never lost | `test_request_racing_close[True/False]` |
| Finalization has one effect under concurrent calls | `test_simultaneous_finalization_has_one_effect` |
| Identical concurrent creates → one request, the other replays the stored response | `test_concurrent_identical_create_requests_replay` |
| Retry after a rolled-back attempt performs the operation once | `test_idempotent_create_after_first_attempt_rolled_back` |
| Same key with a different payload is rejected; lost finalize response is replayable | API `test_full_workflow` |
| Preview equals finalized result; inventory is conserved (`on_hand_before = on_hand_after + allocated`) | `test_finalize_matches_preview_and_conserves_inventory` |
| Cancel releases reservations | `test_cancel_releases_reservations` |
| Database-wide: reserved = Σ active reservations; consumed totals = Σ results; no allocation to ineligible orgs | `assert_invariants` ([helpers.py](../backend/tests/integration/helpers.py)), run after the inventory tests and the seed |
| Allocation bounds, conservation, determinism, oracle agreement | [test_allocation_properties.py](../backend/tests/unit/test_allocation_properties.py) |

**Protocol coverage.** Every round-scoped write path (request create/edit/withdraw,
participant change, reserve/change/release lot, open, close, cancel, finalize) begins with
`lock_round`, and inventory is locked after the round in id order (DECISIONS D3). Inventory
adjustments lock only the lot. That is safe because every path that holds a lot lock either
already holds the round lock or never requests one.

## Security and authorization

[test_auth.py](../backend/tests/api/test_auth.py) and
[test_workflow.py](../backend/tests/api/test_workflow.py) cover the following:

* HttpOnly/SameSite cookie, no password hash or token in any response, and identical
  errors for an unknown user and a wrong password.
* Login throttling (429 after 5 failures, across processes), logout revocation, and
  deactivation revoking sessions.
* CSRF token required on unsafe methods; foreign `Origin` rejected.
* Self-registration creates viewers only, and a `role` field is rejected (422).
* Admins cannot change their own role or deactivate themselves.
* 18 admin endpoint × role combinations return 403 for members and viewers. Requests are
  always for the caller's organization (an `organization_id` field is rejected).
* Another organization's request returns 404. Draft rounds are hidden from members, and
  viewers see aggregates only.
* Captured log output contains no passwords, session tokens or CSRF values.

WebSockets ([test_realtime.py](../backend/tests/api/test_realtime.py)) are covered for bad
origin and missing session (handshake rejected), logout (close 4401), membership change
(close 4409, then resubscribe), session expiry (close 4401), and org isolation. Isolation
is checked with an ordering marker: org B's first message is a later public event,
proving org A's private event was never routed to B.

## Messaging and planning

[test_chat.py](../backend/tests/api/test_chat.py),
[test_planning.py](../backend/tests/api/test_planning.py) and
[test_planning.py (unit)](../backend/tests/unit/test_planning.py) cover the following:

* Viewers get 403 on every messaging and planning endpoint and have a `null` chat
  fingerprint. They cannot be messaged or added to groups.
* Members see the global channel and their own region's channel only; another region's
  channel, and other people's direct messages and groups, answer 404, including to admins
  for direct messages. Admins see every region's channel.
* One direct channel per pair, from either side. A retry with the same `client_id` posts
  once (200 + `Idempotent-Replayed`); reusing it in another conversation is rejected.
* Unread counts, pagination with `before`, read positions that never move backwards, and a
  fingerprint that changes when anything visible changes.
* Senders delete their own messages; admin moderation is audited without the message body;
  message length and the per-user rate limit (429 with `Retry-After`) are enforced.
* Hub routing: a chat event reaches only the named recipients' sockets, never a viewer's.
* Planning is scoped: members to their own organization (403 otherwise), admins to any
  organization they name. Preferences are version-checked (409 on a stale save), and
  duplicates and contradictions are cleaned. Every change is audited.
* Uploads: CSV (comma, semicolon, tab), Excel across sheets, Markdown lines and tables,
  JSON, text in Windows-1252, binary files stored without analysis. Over-limit bodies get
  413 both with `Content-Length` and when streamed chunked; empty files 422; a
  path-traversal filename is reduced to its base name. A zip bomb disguised as `.xlsx` is
  refused before parsing.
* Downloads are attachments with `Content-Security-Policy: sandbox` and `nosniff`; another
  organization cannot read, download or delete a document.
* Planner: deterministic output; avoided categories excluded; the share cap, storage
  trimming (lowest priority first) and perishability notes; existing requests anchoring
  lines without a stated need; suggestions that keep stated storage capacities.

## Realtime recovery bound

Contract: a connected client converges with any committed change within the **resync
interval (15 s) plus one fetch**, even if every notification is lost. The tests use 20 s
as the bound.

* In-process: a TCP proxy between one API instance and Redis is cut, writes made on the
  other instance commit, and when the proxy is restored the subscriber reconnects and
  sends `resync` to its open sockets (`test_redis_outage_and_subscriber_recovery`).
* Stack: `docker compose stop redis`. Both `/readyz` stay 200 with `redis: degraded`,
  writes commit, and after `start redis` the socket on api-2 receives `resync` within the
  bound.
* Browser: with Redis stopped, a page connected to api-2 shows a change written on api-1
  without a reload, within the bound. The run took ~15 s, which is the polling
  interval. After Redis returns, delivery is live again.
* Publish failure: with Redis unreachable, a write still returns 201, and a socket on the
  same process receives the event through local fallback (`test_publish_failure_does_not_fail_write`).
* Slow consumer: a full queue collapses into a single `resync` (`test_slow_consumer_queue_collapses_to_resync`).

## Performance

Reproduce with `make perf`. Raw samples and metadata are in [perf/results/](../perf/results/).

**Method.** Each benchmark uses one observer clock. Python uses `time.perf_counter_ns`
(resolution 42 ns, median back-to-back read overhead 41 ns). The browser probe uses Node
`performance.now()` in the Playwright runner, with a median probe overhead of 0.9 ms.
HTTP samples end after the full response body is read and the status is checked; non-2xx
responses are counted separately and excluded from latency. All loops are closed-loop, so
offered load equals what the workers can issue. These are single-machine results with
client, proxy, both APIs, PostgreSQL and Redis sharing one laptop. They show relative cost
and regressions, not production capacity.

### Allocation (pure functions, one lot, supply = half of demand)

| Requests per lot | FCFS p50 / p95 | Need-weighted p50 / p95 | Priority p50 / p95 |
|---|---|---|---|
| 10 | 0.011 / 0.011 ms | 0.054 / 0.056 ms | 0.013 / 0.013 ms |
| 100 | 0.10 / 0.10 ms | 0.66 / 0.67 ms | 0.13 / 0.13 ms |
| 1,000 | 1.0 / 1.0 ms | 7.2 / 7.3 ms | 1.3 / 1.3 ms |
| 5,000 | 5.4 / 5.5 ms | 37 / 43 ms | 6.9 / 8.6 ms |

The cost scales roughly linearly. Need-weighted is about 7× slower because of exact
`Fraction` arithmetic and repeated passes. It is still far below the cost of the
finalize transaction for realistic rounds of tens of organizations.

### HTTP + database (through nginx, 2 API processes, 16 concurrent workers)

| Phase | Ops | Achieved | p50 | p95 | p99 | Errors |
|---|---|---|---|---|---|---|
| Contended creates (4 orgs × 25 lots, all at once) | 100 | 235/s | 62 ms | 101 ms | 112 ms | 0 |
| Mixed 20 s after 5 s warmup: reads (round detail) | 10,188 | | 8.4 ms | 14.6 ms | 20.3 ms | 0 |
| Mixed: request edits (same round) | 2,509 | 634/s total | 90 ms | 147 ms | 185 ms | 250 × 409* |
| Close (snapshot of 100 requests) / finalize | 1 / 1 | | 15 ms / 23 ms | | | 0 |

\* The 409s are expected `VERSION_CONFLICT` responses: four workers per organization edit
the same requests with optimistic versions. This exercises conflict detection; none of them
are lost updates.

Writes to one round are serialized by the round lock, so edit latency is mostly queueing
behind other writers (16 workers ≈ 125 writes/s per round). This is the intended tradeoff
(DECISIONS D3).

### Browser-visible updates (write on one process → text visible on a page connected to the other)

50 samples after 5 warmup: **p50 32 ms, p95 44 ms, max 51 ms**. Each sample includes the
HTTP write, commit, Redis publish, WebSocket delivery, refetch, React render and the
Playwright polling overhead.

### Targets and rationale

Expected load: tens of organizations, each submitting a handful of requests per round,
mostly near the deadline. Peak is perhaps a few writes per second per round. The targets
are set from the measured baseline with headroom for slower production hardware:

| Target | Basis |
|---|---|
| Request write p95 < 250 ms at 16 concurrent writers on one round | measured 147 ms; round-lock serialization is the dominant cost |
| Round read p95 < 50 ms | measured 15 ms |
| Finalize < 1 s for 1,000 requests across lots | allocation at 1,000 requests ≤ 8 ms; finalize at 100 requests is 23 ms |
| Browser-visible update p95 < 1 s when Redis is healthy; < 20 s during a Redis outage | measured 44 ms; the outage bound is the resync interval |

## Known gaps and limitations

* The CI workflow is written but has not run on GitHub.
* Re-running the browser suites in a tight loop (more than ~60 sign-ins per minute from one
  address) trips the login rate limit by design. The test helper then fails with the
  server's "Too many attempts" message. Space runs out or raise
  `FB_IP_RATE_LIMIT_PER_MINUTE` locally.
* The per-IP limiter is per process (the per-email login throttle is global in PostgreSQL),
  and it trusts `X-Real-IP` from nginx. API ports are bound to localhost for that reason.
* Sessions have a fixed 8-hour lifetime with no idle timeout or refresh.
* Self-registration reveals whether an email already exists (409).
* Allocation results are final; corrections need a new round. Fulfillment, pickup
  logistics and reversals are out of scope.
* Benchmarks ran on one laptop with every component co-located. Re-measure on target
  hardware before relying on absolute numbers.
* Uploaded files live in PostgreSQL (DECISIONS D18). That is right for a few files per
  food bank but would need object storage at much larger volumes. Files are not virus
  scanned; they are only ever served back as downloads.
* Messages are not end-to-end encrypted, and administrators can read every region's
  channel by design. There is no message editing, no attachments in chat, and no
  retention policy yet. `prune` does not delete messages.
* The planning assistant is a deterministic planner, not a language model (DECISIONS D19).
  Its sizing heuristic (about one unit per 25 households) is a documented default, not a
  measured figure for any real food bank.
* The browser layout was checked at 1440 px, 1280 px and phone widths (Pixel 7 and
  iPhone 13; automated overflow checks plus screenshot review).
  No screen-reader session was run; semantics (labels, roles, live regions, focus styles)
  were implemented and reviewed in code only.
