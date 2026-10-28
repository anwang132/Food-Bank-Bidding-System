import io
import logging

from app.logs import JsonFormatter, redact
from tests.conftest import PASSWORD


async def test_login_sets_httponly_cookie_and_hides_secrets(world, client_factory):
    c = await client_factory()
    r = await c.login("a@test.local")
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "fb_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    body = r.text
    assert "password" not in body and "token_hash" not in body
    me = r.json()
    assert me["user"]["role"] == "member"
    assert me["organization"]["name"] == "Alpha Pantry"
    assert me["organization"]["default_weight"] is None  # need info not exposed to members


async def test_wrong_password_and_unknown_user_look_identical(world, client_factory):
    c = await client_factory()
    r1 = await c.login("a@test.local", "nope-nope-nope")
    r2 = await c.login("nobody@test.local", "nope-nope-nope")
    assert r1.status_code == r2.status_code == 401
    assert r1.json()["error"]["code"] == r2.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_login_throttled_after_repeated_failures(world, client_factory):
    c = await client_factory()
    for _ in range(5):
        assert (await c.login("a@test.local", "bad-password-x")).status_code == 401
    r = await c.login("a@test.local")  # even the right password is refused for now
    assert r.status_code == 429
    assert "Retry-After" in r.headers


async def test_logout_revokes_session(world, client_factory):
    c = await client_factory("a@test.local")
    assert (await c.get("/api/auth/me")).status_code == 200
    assert (await c.post("/api/auth/logout")).status_code == 204
    assert (await c.get("/api/auth/me")).status_code == 401


async def test_unsafe_requests_require_csrf_token_and_allowed_origin(world, client_factory):
    c = await client_factory("admin@test.local")
    body = {"name": "Gamma", "default_weight": 1, "default_priority": 5}
    r = await c.http.post("/api/admin/organizations", json=body)  # no CSRF header
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_FAILED"
    r = await c.http.post(
        "/api/admin/organizations",
        json=body,
        headers={"X-CSRF-Token": c.csrf, "Origin": "https://evil.example"},
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"
    assert (await c.post("/api/admin/organizations", json=body)).status_code == 201


async def test_registration_creates_viewer_and_rejects_role(world, client_factory):
    c = await client_factory()
    r = await c.http.post(
        "/api/auth/register",
        json={
            "email": "new@example.org",
            "display_name": "New Person",
            "password": "long-enough-pw",
            "role": "admin",
        },
    )
    assert r.status_code == 422
    r = await c.http.post(
        "/api/auth/register",
        json={
            "email": "new@example.org",
            "display_name": "New Person",
            "password": "long-enough-pw",
        },
    )
    assert r.status_code == 201 and r.json()["role"] == "viewer"
    assert (await c.login("new@example.org", "long-enough-pw")).status_code == 200
    assert (await c.get("/api/admin/users")).status_code == 403


async def test_admin_cannot_change_own_role(world, client_factory):
    c = await client_factory("admin@test.local")
    r = await c.patch(f"/api/admin/users/{world.admin.id}", json={"version": 1, "role": "viewer"})
    assert r.status_code == 403


async def test_member_moved_to_other_org_sees_new_org_immediately(world, client_factory):
    admin = await client_factory("admin@test.local")
    member = await client_factory("a@test.local")
    r = await admin.patch(
        f"/api/admin/users/{world.member_a.id}",
        json={"version": 1, "organization_id": world.org_b.id, "reason": "staff transfer"},
    )
    assert r.status_code == 200
    me = (await member.get("/api/auth/me")).json()
    assert me["organization"]["id"] == world.org_b.id


async def test_deactivation_revokes_sessions(world, client_factory):
    admin = await client_factory("admin@test.local")
    member = await client_factory("a@test.local")
    r = await admin.patch(
        f"/api/admin/users/{world.member_a.id}",
        json={"version": 1, "is_active": False, "reason": "left org"},
    )
    assert r.status_code == 200
    assert (await member.get("/api/auth/me")).status_code == 401


async def test_logs_never_contain_passwords_or_tokens(world, client_factory):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.DEBUG)
    try:
        c = await client_factory()
        r = await c.login("a@test.local")
        token = r.cookies.get("fb_session")
        await c.login("a@test.local", "wrong-password-123")
        logging.getLogger("app").info("probe", extra={"password": "x", "csrf_token": "y"})
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    out = stream.getvalue()
    assert '"path": "/api/auth/login"' in out
    assert PASSWORD not in out and "wrong-password-123" not in out
    assert token and token not in out
    assert '"password": "[REDACTED]"' in out and '"csrf_token": "[REDACTED]"' in out


def test_redact_nested():
    assert redact({"a": {"Password": "p", "ok": 1}, "list": [{"token": "t"}]}) == {
        "a": {"Password": "[REDACTED]", "ok": 1},
        "list": [{"token": "[REDACTED]"}],
    }
