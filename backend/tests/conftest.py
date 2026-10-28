"""Test fixtures backed by real PostgreSQL and Redis.

The suite creates (and recreates) a dedicated database, ``foodbank_test`` by default,
on the server named by FB_TEST_DATABASE_URL's host. It never touches the demo DB.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.main import create_app
from app.models import InventoryLot, Organization, User
from app.security import hash_password
from app.services import rounds as round_service
from app.services.common import Actor, Changes

BACKEND = Path(__file__).resolve().parent.parent
TEST_DB_URL = os.environ.get(
    "FB_TEST_DATABASE_URL", "postgresql+psycopg://foodbank:foodbank@localhost:55432/foodbank_test"
)
TEST_REDIS_URL = os.environ.get("FB_TEST_REDIS_URL", "redis://localhost:56379/1")
ORIGIN = "http://testserver"
PASSWORD = "correct-horse-battery"
_PW_HASH = hash_password(PASSWORD)


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


@pytest.fixture(scope="session", autouse=True)
def database() -> str:
    dbname = TEST_DB_URL.rsplit("/", 1)[1]
    assert dbname.endswith("_test"), "refusing to run tests against a non-test database"
    admin_url = _libpq(TEST_DB_URL.rsplit("/", 1)[0] + "/postgres")
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{dbname}"')
    subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"url={TEST_DB_URL}", "upgrade", "head"],
        cwd=BACKEND,
        check=True,
        capture_output=True,
    )
    return TEST_DB_URL


@pytest.fixture(scope="session")
async def engine(database):
    eng = create_async_engine(database, poolclass=NullPool)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session")
def maker(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=True)


@pytest.fixture(autouse=True)
async def clean(engine):
    async with engine.begin() as conn:
        tables = (
            (
                await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                        "AND tablename <> 'alembic_version'"
                    )
                )
            )
            .scalars()
            .all()
        )
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
async def db(maker) -> AsyncIterator[AsyncSession]:
    async with maker() as session:
        yield session


def make_settings(**overrides) -> Settings:
    values = dict(
        environment="test",
        database_url=TEST_DB_URL,
        redis_url=TEST_REDIS_URL,
        allowed_origins=ORIGIN,
        cookie_secure=False,
        instance_id="test",
        log_level="WARNING",
        ip_rate_limit_per_minute=1000,
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
async def app():
    application = create_app(make_settings(), start_subscriber=False)
    async with application.router.lifespan_context(application):
        yield application


class Client:
    """Thin httpx wrapper that carries the session cookie and CSRF token."""

    def __init__(self, app):
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=ORIGIN,
            headers={"Origin": ORIGIN},
        )
        self.csrf: str | None = None
        self.me: dict | None = None

    async def login(self, email: str, password: str = PASSWORD) -> httpx.Response:
        r = await self.http.post("/api/auth/login", json={"email": email, "password": password})
        if r.status_code == 200:
            self.me = r.json()
            self.csrf = self.me["csrf_token"]
        return r

    def _headers(self, extra=None):
        h = {"X-CSRF-Token": self.csrf} if self.csrf else {}
        h.update(extra or {})
        return h

    async def get(self, url, **kw):
        return await self.http.get(url, **kw)

    async def post(self, url, json=None, headers=None):
        return await self.http.post(url, json=json, headers=self._headers(headers))

    async def patch(self, url, json=None, headers=None):
        return await self.http.patch(url, json=json, headers=self._headers(headers))

    async def put(self, url, json=None, headers=None):
        return await self.http.put(url, json=json, headers=self._headers(headers))

    async def delete(self, url, headers=None):
        return await self.http.delete(url, headers=self._headers(headers))

    async def aclose(self):
        await self.http.aclose()


@pytest.fixture
async def client_factory(app):
    clients: list[Client] = []

    async def make(email: str | None = None) -> Client:
        c = Client(app)
        clients.append(c)
        if email:
            r = await c.login(email)
            assert r.status_code == 200, r.text
        return c

    yield make
    for c in clients:
        await c.aclose()


# ---------------------------------------------------------------- data builders


class World:
    """Two food banks, an admin, a member per food bank, and a viewer."""

    admin: User
    viewer: User
    org_a: Organization
    org_b: Organization
    member_a: User
    member_a2: User
    member_b: User

    @property
    def admin_actor(self) -> Actor:
        return Actor(self.admin.id, "admin")

    @property
    def actor_a(self) -> Actor:
        return Actor(self.member_a.id, "member", self.org_a.id)

    @property
    def actor_a2(self) -> Actor:
        return Actor(self.member_a2.id, "member", self.org_a.id)

    @property
    def actor_b(self) -> Actor:
        return Actor(self.member_b.id, "member", self.org_b.id)


def _user(email, role, org=None):
    return User(
        email=email,
        display_name=email.split("@")[0],
        password_hash=_PW_HASH,
        role=role,
        organization_id=org.id if org else None,
        is_active=True,
        version=1,
    )


@pytest.fixture
async def world(maker) -> World:
    w = World()
    async with maker() as s:
        w.org_a = Organization(
            name="Alpha Pantry", default_weight=3, default_priority=1, is_active=True, version=1
        )
        w.org_b = Organization(
            name="Beta Food Shelf", default_weight=1, default_priority=2, is_active=True, version=1
        )
        s.add_all([w.org_a, w.org_b])
        await s.flush()
        w.admin = _user("admin@test.local", "admin")
        w.viewer = _user("viewer@test.local", "viewer")
        w.member_a = _user("a@test.local", "member", w.org_a)
        w.member_a2 = _user("a2@test.local", "member", w.org_a)
        w.member_b = _user("b@test.local", "member", w.org_b)
        s.add_all([w.admin, w.viewer, w.member_a, w.member_a2, w.member_b])
        await s.commit()
    return w


async def make_lot(maker, qty=100, name="Canned beans", unit="cases") -> InventoryLot:
    async with maker() as s:
        lot = InventoryLot(
            name=name,
            description="",
            unit=unit,
            quantity_on_hand=qty,
            quantity_reserved=0,
            version=1,
        )
        s.add(lot)
        await s.commit()
        return lot


async def make_round(
    maker,
    world: World,
    *,
    lots: list[tuple[int, int]],
    strategy="fcfs",
    open_round=True,
    deadline_minutes=60,
) -> int:
    async with maker() as s:
        changes = Changes()
        rnd = await round_service.create_round(
            s,
            world.admin_actor,
            changes,
            name="Test round",
            description="",
            strategy=strategy,
            request_deadline=datetime.now(UTC) + timedelta(minutes=deadline_minutes),
        )
        for lot_id, qty in lots:
            await round_service.reserve_lot(
                s, world.admin_actor, changes, rnd.id, lot_id=lot_id, quantity=qty
            )
        if open_round:
            await round_service.open_round(s, world.admin_actor, changes, rnd.id, version=None)
        await s.commit()
        return rnd.id
