"""Operational commands.

python -m app.cli seed                           # synthetic demo data; empty database only
python -m app.cli reset-demo --confirm <dbname>  # wipe + reseed; refused in production
python -m app.cli create-admin --email ...       # bootstrap; password from FB_ADMIN_PASSWORD
python -m app.cli prune [--days 7]               # delete expired sessions and old keys
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from datetime import timedelta

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, load_settings_or_exit
from app.db import make_engine, make_sessionmaker
from app.models import IdempotencyKey, LoginFailure, User, UserSession
from app.security import hash_password


def _fail(message: str, code: int = 2) -> int:
    print(message, file=sys.stderr)
    return code


async def _seed(db: AsyncSession) -> int:
    from app.seed import seed

    accounts = await seed(db)
    print(f"Seeded demo data. Accounts (password: {accounts.pop('password')}):")
    for role, email in accounts.items():
        print(f"  {role:10} {email}")
    return 0


async def cmd_seed(db: AsyncSession, args: argparse.Namespace, settings: Settings) -> int:
    if await db.scalar(select(func.count()).select_from(User)):
        return _fail("Database already has users; use reset-demo to start over.", 1)
    return await _seed(db)


async def cmd_reset_demo(db: AsyncSession, args: argparse.Namespace, settings: Settings) -> int:
    if settings.environment == "production":
        return _fail("Refusing to reset: FB_ENVIRONMENT=production.")
    current = await db.scalar(text("SELECT current_database()"))
    if args.confirm != current:
        return _fail(f"Refusing to reset: pass --confirm {current} to wipe that database.")
    tables = (
        await db.scalars(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename <> 'alembic_version'"
            )
        )
    ).all()
    await db.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    await db.commit()
    print(f"Wiped all data in database {current!r}.")
    return await _seed(db)


async def cmd_create_admin(db: AsyncSession, args: argparse.Namespace, settings: Settings) -> int:
    password = os.environ.get("FB_ADMIN_PASSWORD", "")
    if len(password) < 10:
        return _fail("Set FB_ADMIN_PASSWORD (at least 10 characters).")
    db.add(
        User(
            email=args.email.lower(),
            display_name=args.name,
            password_hash=hash_password(password),
            role="admin",
            is_active=True,
            version=1,
        )
    )
    await db.commit()
    print(f"Created admin {args.email.lower()}")
    return 0


async def cmd_prune(db: AsyncSession, args: argparse.Namespace, settings: Settings) -> int:
    """Idempotency keys are kept `--days` so clients can retry lost responses for
    that long; expired/revoked sessions and stale login failures have no use."""
    cutoff = func.now() - timedelta(days=args.days)
    sessions = await db.execute(
        delete(UserSession).where(
            or_(UserSession.expires_at < func.now(), UserSession.revoked_at < cutoff)
        )
    )
    failures = await db.execute(
        delete(LoginFailure).where(LoginFailure.attempted_at < func.now() - timedelta(days=1))
    )
    keys = await db.execute(delete(IdempotencyKey).where(IdempotencyKey.created_at < cutoff))
    await db.commit()
    print(
        f"Pruned {sessions.rowcount} sessions, {failures.rowcount} login failures, "  # type: ignore[attr-defined]
        f"{keys.rowcount} idempotency keys."  # type: ignore[attr-defined]
    )
    return 0


Command = Callable[[AsyncSession, argparse.Namespace, Settings], Awaitable[int]]
COMMANDS: dict[str, Command] = {
    "seed": cmd_seed,
    "reset-demo": cmd_reset_demo,
    "create-admin": cmd_create_admin,
    "prune": cmd_prune,
}


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings_or_exit()
    engine = make_engine(settings)
    try:
        async with make_sessionmaker(engine)() as db:
            return await COMMANDS[args.command](db, args, settings)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed", help="seed synthetic demo data into an empty database")
    reset = sub.add_parser("reset-demo", help="wipe all data and reseed (non-production)")
    reset.add_argument("--confirm", required=True, help="name of the database to wipe")
    admin = sub.add_parser("create-admin", help="create an administrator account")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", default="Administrator")
    prune = sub.add_parser("prune", help="delete expired sessions and old idempotency keys")
    prune.add_argument("--days", type=int, default=7, help="idempotency key retention")
    raise SystemExit(asyncio.run(_run(parser.parse_args())))


if __name__ == "__main__":
    main()
