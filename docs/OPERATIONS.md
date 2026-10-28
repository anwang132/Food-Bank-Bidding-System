# Operations

## Configuration

All settings are environment variables prefixed `FB_`
([backend/app/config.py](../backend/app/config.py)). Invalid configuration stops the
process at startup with one line per problem, for example
`FB_ALLOWED_ORIGINS: Field required`.

| Variable | Required | Notes |
|---|---|---|
| `FB_DATABASE_URL` | yes | `postgresql+psycopg://…` |
| `FB_REDIS_URL` | yes | `redis://…` |
| `FB_ALLOWED_ORIGINS` | yes | Comma-separated exact origins for unsafe requests and WebSockets; wildcards rejected |
| `FB_ENVIRONMENT` | | `development` / `test` / `production`. Production requires `FB_COOKIE_SECURE=true` and https origins, disables `/api/docs`, and refuses `reset-demo` |
| `FB_COOKIE_SECURE` | | default `true`; compose sets `false` for plain-HTTP localhost |
| `FB_SESSION_TTL_MINUTES` | | default 480 (absolute session lifetime) |
| `FB_RESYNC_INTERVAL_SECONDS` | | default 15; the client's periodic version check, which sets the recovery bound |
| `FB_WS_REVALIDATE_SECONDS` | | default 30; WebSocket session re-check interval |
| `FB_INSTANCE_ID` | | Shown in logs, `X-Instance-Id` headers and WebSocket `hello` |
| `FB_ALLOW_REGISTRATION` | | default `true`; self-registration creates viewers only |
| `FB_IP_RATE_LIMIT_PER_MINUTE` | | default 30; login/registration attempts per client IP per minute, per API process |
| `FB_LOGIN_MAX_FAILURES` | | default 5 failed logins per email per 15 minutes (global, in PostgreSQL) |
| `FB_MAX_UPLOAD_BYTES` | | default 5 MB; largest planning upload. Keep nginx's `client_max_body_size` for `/api/planning/documents` slightly above it |
| `FB_CHAT_MESSAGES_PER_MINUTE` | | default 30; messages one user can post per minute |

## Health

* `GET /healthz`: liveness. The process is up.
* `GET /readyz`: readiness. **503 only** when PostgreSQL is unreachable or the schema
  is not at the migration head. Redis problems return 200 with `"redis": "degraded"`.
  Live updates then fall back to same-process delivery plus periodic client resync, and
  no write depends on Redis. The body includes subscriber state, reconnect count, publish
  failures and connection count, for diagnosing notification problems.

## Logs

JSON lines on stdout. Each line has `ts`, `level`, `logger`, `msg`, `instance`,
`request_id` and `user_id`. Each HTTP request produces one `app.access` line with
method, path, status and duration; query strings and bodies are never logged.
Keys containing password, token, secret, cookie, authorization, csrf or session are
redacted recursively. Notification failures log `event publish failed; delivering
locally only` with the event type and round id. Responses carry `X-Request-ID`, taken
from nginx's `$request_id` or generated, and 5xx error messages show the first 8
characters of it as a reference.

## Migrations

Alembic, in [backend/alembic/versions/](../backend/alembic/versions/). The compose
`migrate` service runs `alembic upgrade head` before either API starts, and APIs report
not-ready until the schema is at head. CI also runs `alembic check` to fail on
model/migration drift. Write migrations to be backward compatible with the previous
release (add, backfill, then switch, then drop) so a rolling restart of the two API
processes is safe.

## Backup and restore

PostgreSQL holds all authoritative state. Redis holds nothing durable; it runs with
persistence disabled and can be restarted at any time.

```bash
# backup (consistent snapshot while running)
docker compose exec -T db pg_dump -U foodbank -Fc foodbank > foodbank-$(date +%F).dump
# restore into an empty database
docker compose exec -T db pg_restore -U foodbank -d foodbank --clean --if-exists < foodbank-YYYY-MM-DD.dump
```

After a restore, connected browsers converge on the restored state within the
resync interval. Production should add scheduled dumps or WAL archiving, and should test
restores regularly. Neither is provisioned here.

## Maintenance

```bash
docker compose exec api-1 python -m app.cli prune --days 7
```

This deletes expired sessions, revoked sessions older than the retention period, login
failures older than a day, and idempotency keys older than the retention period. Clients
can therefore replay a lost response for up to 7 days. Run it daily, for example from cron.

## Demo data

* `make seed` (`python -m app.cli seed`) refuses to run if any user exists.
* `make reset-demo` runs `python -m app.cli reset-demo --confirm foodbank`. It truncates
  every table and reseeds. It is refused when `FB_ENVIRONMENT=production` and when
  `--confirm` does not match the connected database's name.
* `create-admin --email …` with `FB_ADMIN_PASSWORD` set bootstraps a real deployment
  without demo data.

## Scaling notes

Request writes to one round are serialized by the round row lock (DECISIONS D3). The
measured cost is in [VERIFICATION.md](VERIFICATION.md#performance). Different rounds do not
contend. API processes are stateless apart from their WebSocket connections, so more
processes can be added behind nginx. Each one holds one Redis subscription.
