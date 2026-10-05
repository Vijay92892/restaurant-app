"""Phase 9: the retention job deletes what nobody needs any more — and nothing else."""

from __future__ import annotations

import json
import os
import uuid

import asyncpg
import pytest

from app.core.config import Settings
from app.db.session import Database
from app.services import retention_service
from app.services.retention_service import Purged
from tests.conftest import PgTestDatabase
from tests.test_schema_rls import seed_full_tenant, set_tenant


def _hash() -> bytes:
    return os.urandom(32)


async def _auth_session(conn: asyncpg.Connection, t: dict, *, expires_in_days: int) -> uuid.UUID:
    sid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO auth_sessions (id, restaurant_id, user_id, created_at, expires_at) VALUES "
        "($1, $2, $3, now() + make_interval(days => $4 - 30), now() + make_interval(days => $4))",
        sid,
        t["restaurant"],
        t["user"],
        expires_in_days,
    )
    return sid


async def _refresh_token(
    conn: asyncpg.Connection,
    t: dict,
    session: uuid.UUID,
    *,
    expires_in_days: int,
    rotated: bool = False,
) -> uuid.UUID:
    tid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO refresh_tokens (id, restaurant_id, session_id, token_hash, expires_at, "
        "rotated_at) VALUES ($1, $2, $3, $4, now() + make_interval(days => $5), "
        "CASE WHEN $6 THEN now() - interval '3 days' END)",
        tid,
        t["restaurant"],
        session,
        _hash(),
        expires_in_days,
        rotated,
    )
    return tid


async def _table_session(
    conn: asyncpg.Connection, t: dict, *, expires_in_days: int, with_order: bool = False
) -> uuid.UUID:
    sid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO table_sessions (id, restaurant_id, table_id, token_hash, pin_version, "
        "created_at, expires_at) VALUES ($1, $2, $3, $4, 1, "
        "now() + make_interval(days => $5) - interval '3 hours', "
        "now() + make_interval(days => $5))",
        sid,
        t["restaurant"],
        t["table"],
        _hash(),
        expires_in_days,
    )
    if with_order:
        await conn.execute(
            "INSERT INTO orders (restaurant_id, order_number, table_id, source, table_session_id, "
            "idempotency_key, request_fingerprint, status, cancelled_at, cancel_reason_code) "
            "VALUES ($1, 2, $2, 'customer', $3, 'idem-key-0002', $4, 'cancelled', now(), "
            "'customer_changed_mind')",
            t["restaurant"],
            t["table"],
            sid,
            _hash(),
        )
    return sid


async def _audit(conn: asyncpg.Connection, t: dict, *, days_ago: int) -> int:
    return await conn.fetchval(
        "INSERT INTO audit_logs (restaurant_id, occurred_at, actor_type, action) "
        "VALUES ($1, now() - make_interval(days => $2), 'system', 'test.entry') RETURNING id",
        t["restaurant"],
        days_ago,
    )


async def _problem_report(conn: asyncpg.Connection, t: dict, *, days_ago: int) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO problem_reports (restaurant_id, reported_by_user_id, category, created_at) "
        "VALUES ($1, $2, 'network', now() - make_interval(days => $3)) RETURNING id",
        t["restaurant"],
        t["user"],
        days_ago,
    )


async def _exists(conn: asyncpg.Connection, table: str, row_id: object) -> bool:
    # Table names come from the test itself, never from input.
    return await conn.fetchval(f"SELECT count(*) FROM {table} WHERE id = $1", row_id) == 1  # noqa: S608


async def test_retention_deletes_what_expired_and_keeps_everything_else(
    owner_conn: asyncpg.Connection, settings: Settings
) -> None:
    rows: dict[str, dict] = {}
    for code in ("alpha", "bravo"):
        t = await seed_full_tenant(owner_conn, code)
        live = t["auth_session"]  # expires tomorrow
        old = await _auth_session(owner_conn, t, expires_in_days=-40)
        recent = await _auth_session(owner_conn, t, expires_in_days=-10)
        rows[code] = {
            "tenant": t,
            "gone": {
                "auth_sessions": [old],
                "refresh_tokens": [
                    await _refresh_token(owner_conn, t, old, expires_in_days=-40),
                    await _refresh_token(owner_conn, t, recent, expires_in_days=-10),
                    await _refresh_token(owner_conn, t, live, expires_in_days=-2, rotated=True),
                ],
                "table_sessions": [await _table_session(owner_conn, t, expires_in_days=-40)],
                "audit_logs": [await _audit(owner_conn, t, days_ago=400)],
                "problem_reports": [await _problem_report(owner_conn, t, days_ago=400)],
            },
            "kept": {
                "auth_sessions": [live, recent],
                "refresh_tokens": [
                    t["refresh_token"],
                    # Rotated but not expired: still needed to detect a stolen token's reuse.
                    await _refresh_token(owner_conn, t, live, expires_in_days=5, rotated=True),
                ],
                "table_sessions": [
                    t["table_session"],
                    await _table_session(owner_conn, t, expires_in_days=-10),
                    # Old, but it placed an order: kept with the order.
                    await _table_session(owner_conn, t, expires_in_days=-40, with_order=True),
                ],
                "audit_logs": [await _audit(owner_conn, t, days_ago=200)],
                "password_reset_tokens": [t["reset_token"]],
                "problem_reports": [
                    t["problem_report"],
                    await _problem_report(owner_conn, t, days_ago=300),
                ],
            },
        }
        old_reset = uuid.uuid4()
        await owner_conn.execute(
            "INSERT INTO password_reset_tokens (id, restaurant_id, user_id, token_hash, "
            "created_at, expires_at) VALUES ($1, $2, $3, $4, now() - interval '41 days', "
            "now() - interval '40 days')",
            old_reset,
            t["restaurant"],
            t["user"],
            _hash(),
        )
        rows[code]["gone"]["password_reset_tokens"] = [old_reset]

    db = Database(settings)
    try:
        purged = await retention_service.purge_expired(db, settings)
        again = await retention_service.purge_expired(db, settings)
    finally:
        await db.dispose()

    assert purged == Purged(
        refresh_tokens=6,
        auth_sessions=2,
        password_reset_tokens=2,
        table_sessions=2,
        problem_reports=2,
        audit_logs=2,
    )
    assert again == Purged()  # idempotent: nothing left to do
    for code, data in rows.items():
        for table, ids in data["gone"].items():
            for row_id in ids:
                assert not await _exists(owner_conn, table, row_id), (code, table)
        for table, ids in data["kept"].items():
            for row_id in ids:
                assert await _exists(owner_conn, table, row_id), (code, table)
        # Purging audit logs is itself audited.
        details = await owner_conn.fetchval(
            "SELECT details FROM audit_logs WHERE restaurant_id = $1 AND action = "
            "'audit_log.purged' AND actor_type = 'system'",
            data["tenant"]["restaurant"],
        )
        assert json.loads(details) == {"deleted": 1, "retention_days": 365}


async def test_one_restaurant_at_a_time_in_small_batches(
    owner_conn: asyncpg.Connection, settings: Settings
) -> None:
    alpha = await seed_full_tenant(owner_conn, "alpha")
    bravo = await seed_full_tenant(owner_conn, "bravo")
    for t in (alpha, bravo):
        for _ in range(5):
            await _table_session(owner_conn, t, expires_in_days=-40)

    db = Database(settings)
    try:
        purged = await retention_service.purge_restaurant(
            db, settings, alpha["restaurant"], batch=2
        )
    finally:
        await db.dispose()
    assert purged.table_sessions == 5  # three batches of at most 2
    remaining = await owner_conn.fetch(
        "SELECT restaurant_id, count(*) AS n FROM table_sessions GROUP BY restaurant_id"
    )
    assert {r["restaurant_id"]: r["n"] for r in remaining} == {
        alpha["restaurant"]: 1,  # the live session with an order
        bravo["restaurant"]: 6,  # untouched
    }


async def test_only_one_worker_purges_at_a_time(
    clean_db: PgTestDatabase, settings: Settings
) -> None:
    other = await asyncpg.connect(clean_db.dsn("rr_app"))
    db = Database(settings)
    try:
        async with other.transaction():
            await other.execute("SELECT pg_advisory_xact_lock(7311240027)")
            assert await retention_service.purge_expired(db, settings) is None
        assert await retention_service.purge_expired(db, settings) == Purged()
    finally:
        await db.dispose()
        await other.close()


async def test_audit_purge_cannot_touch_recent_history_or_other_restaurants(
    owner_conn: asyncpg.Connection, app_conn: asyncpg.Connection
) -> None:
    alpha = await seed_full_tenant(owner_conn, "alpha")
    bravo = await seed_full_tenant(owner_conn, "bravo")
    old_alpha = await _audit(owner_conn, alpha, days_ago=400)
    old_bravo = await _audit(owner_conn, bravo, days_ago=400)
    purge = "SELECT app_purge_audit_logs(now() - make_interval(days => $1), $2)"

    with pytest.raises(asyncpg.InsufficientPrivilegeError):  # no restaurant bound
        await app_conn.fetchval(purge, 365, 100)
    for days, limit in ((30, 100), (179, 100), (365, 0), (365, 10_001)):
        with pytest.raises(asyncpg.CheckViolationError):
            async with app_conn.transaction():
                await set_tenant(app_conn, alpha["restaurant"])
                await app_conn.fetchval(purge, days, limit)

    async with app_conn.transaction():
        await set_tenant(app_conn, alpha["restaurant"])
        assert await app_conn.fetchval(purge, 365, 100) == 1
        # The runtime role still cannot delete audit rows directly.
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with app_conn.transaction():
                await app_conn.execute("DELETE FROM audit_logs")

    assert not await _exists(owner_conn, "audit_logs", old_alpha)
    assert await _exists(owner_conn, "audit_logs", old_bravo)
    left = await owner_conn.fetchval(
        "SELECT count(*) FROM audit_logs WHERE restaurant_id = $1", alpha["restaurant"]
    )
    assert left == 1  # the recent login record from the seed


def test_retention_settings_cannot_shorten_audit_history_below_the_database_floor() -> None:
    with pytest.raises(ValueError, match="audit_log_retention_days"):
        Settings(_env_file=None, audit_log_retention_days=90)  # type: ignore[call-arg]
