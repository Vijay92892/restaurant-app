"""Phase 2: the database itself enforces tenant isolation, independent of application code."""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.session import TenantBindingError, bind_tenant
from tests.conftest import ALL_TABLES, PgTestDatabase

FAKE_HASH = "$argon2id$v=19$m=1024,t=1,p=1$c2FsdHNhbHQ$aGFzaGhhc2hoYXNoaGFzaA"

RESOLVERS = (
    "app_resolve_restaurant_code(text)",
    "app_resolve_qr_token(text)",
    "app_resolve_table_session(bytea)",
    "app_resolve_refresh_token(bytea)",
    "app_resolve_password_reset_token(bytea)",
    "app_due_pin_rotations(timestamptz)",
    "app_restaurant_ids()",
    "app_purge_audit_logs(timestamptz, integer)",
)


def _rand(n: int = 32) -> bytes:
    return os.urandom(n)


async def seed_full_tenant(conn: asyncpg.Connection, code: str) -> dict[str, uuid.UUID]:
    """Insert one row into every tenant table (as the schema owner, bypassing RLS)."""
    ids = {
        name: uuid.uuid4()
        for name in (
            "restaurant",
            "user",
            "auth_session",
            "refresh_token",
            "reset_token",
            "table",
            "table2",
            "qr",
            "pin",
            "table_session",
            "category",
            "item",
            "group",
            "modifier",
            "order",
            "order_item",
            "order_item_modifier",
            "event",
            "problem_report",
        )
    }
    rid = ids["restaurant"]
    q = conn.execute
    await q("INSERT INTO restaurants (id, code, name) VALUES ($1, $2, $3)", rid, code, code)
    await q(
        "INSERT INTO users (id, restaurant_id, username, display_name, role, password_hash) "
        "VALUES ($1, $2, 'owner', 'Owner', 'owner', $3)",
        ids["user"],
        rid,
        FAKE_HASH,
    )
    await q(
        "INSERT INTO auth_sessions (id, restaurant_id, user_id, expires_at) "
        "VALUES ($1, $2, $3, now() + interval '1 day')",
        ids["auth_session"],
        rid,
        ids["user"],
    )
    await q(
        "INSERT INTO refresh_tokens (id, restaurant_id, session_id, token_hash, expires_at) "
        "VALUES ($1, $2, $3, $4, now() + interval '1 day')",
        ids["refresh_token"],
        rid,
        ids["auth_session"],
        _rand(),
    )
    await q(
        "INSERT INTO password_reset_tokens (id, restaurant_id, user_id, token_hash, expires_at) "
        "VALUES ($1, $2, $3, $4, now() + interval '1 hour')",
        ids["reset_token"],
        rid,
        ids["user"],
        _rand(),
    )
    for key, label in (("table", "T1"), ("table2", "T2")):
        await q(
            "INSERT INTO dining_tables (id, restaurant_id, label) VALUES ($1, $2, $3)",
            ids[key],
            rid,
            label,
        )
    await q(
        "INSERT INTO table_qr_tokens (id, restaurant_id, table_id, token) VALUES ($1, $2, $3, $4)",
        ids["qr"],
        rid,
        ids["table"],
        f"qr_{code}_0123456789abcdef",
    )
    await q(
        "INSERT INTO pin_versions (id, restaurant_id, version, pin_hmac, pin_ciphertext, reason) "
        "VALUES ($1, $2, 1, $3, $4, 'initial')",
        ids["pin"],
        rid,
        _rand(),
        _rand(40),
    )
    await q(
        "INSERT INTO table_sessions (id, restaurant_id, table_id, token_hash, pin_version, "
        "expires_at) VALUES ($1, $2, $3, $4, 1, now() + interval '3 hours')",
        ids["table_session"],
        rid,
        ids["table"],
        _rand(),
    )
    await q(
        "INSERT INTO menu_categories (id, restaurant_id, name) VALUES ($1, $2, 'Starters')",
        ids["category"],
        rid,
    )
    await q(
        "INSERT INTO menu_items (id, restaurant_id, category_id, name) "
        "VALUES ($1, $2, $3, 'Chilli Paneer')",
        ids["item"],
        rid,
        ids["category"],
    )
    await q(
        "INSERT INTO menu_modifier_groups (id, restaurant_id, name) VALUES ($1, $2, 'Spice')",
        ids["group"],
        rid,
    )
    await q(
        "INSERT INTO menu_modifiers (id, restaurant_id, group_id, name) "
        "VALUES ($1, $2, $3, 'Extra Spicy')",
        ids["modifier"],
        rid,
        ids["group"],
    )
    await q(
        "INSERT INTO menu_item_modifier_groups (menu_item_id, group_id, restaurant_id) "
        "VALUES ($1, $2, $3)",
        ids["item"],
        ids["group"],
        rid,
    )
    await q(
        "INSERT INTO orders (id, restaurant_id, order_number, table_id, source, table_session_id, "
        "idempotency_key, request_fingerprint) VALUES ($1, $2, 1, $3, 'customer', $4, $5, $6)",
        ids["order"],
        rid,
        ids["table"],
        ids["table_session"],
        "idem-key-0001",
        _rand(),
    )
    await q(
        "INSERT INTO order_items (id, restaurant_id, order_id, line_no, menu_item_id, item_name, "
        "quantity) VALUES ($1, $2, $3, 1, $4, 'Chilli Paneer', 2)",
        ids["order_item"],
        rid,
        ids["order"],
        ids["item"],
    )
    await q(
        "INSERT INTO order_item_modifiers (id, restaurant_id, order_item_id, modifier_id, "
        "group_name, modifier_name) VALUES ($1, $2, $3, $4, 'Spice', 'Extra Spicy')",
        ids["order_item_modifier"],
        rid,
        ids["order_item"],
        ids["modifier"],
    )
    await q(
        "INSERT INTO order_events (id, restaurant_id, order_id, type, to_status, order_version, "
        "actor_type, actor_table_session_id) "
        "VALUES ($1, $2, $3, 'order.created', 'placed', 1, 'customer', $4)",
        ids["event"],
        rid,
        ids["order"],
        ids["table_session"],
    )
    await q(
        "INSERT INTO audit_logs (restaurant_id, actor_type, actor_user_id, action) "
        "VALUES ($1, 'staff', $2, 'auth.login.succeeded')",
        rid,
        ids["user"],
    )
    await q(
        "INSERT INTO problem_reports (id, restaurant_id, reported_by_user_id, category, note, "
        "order_id) VALUES ($1, $2, $3, 'order_mistake', 'Wrong table', $4)",
        ids["problem_report"],
        rid,
        ids["user"],
        ids["order"],
    )
    await q(
        "INSERT INTO usage_counters (restaurant_id, day, metric, count) "
        "VALUES ($1, current_date, 'qr_opened', 3)",
        rid,
    )
    return ids


async def set_tenant(conn: asyncpg.Connection, restaurant_id: uuid.UUID) -> None:
    await conn.execute("SELECT set_config('app.restaurant_id', $1, true)", str(restaurant_id))


async def test_runtime_role_cannot_bypass_rls(app_conn: asyncpg.Connection) -> None:
    role = await app_conn.fetchrow(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )
    assert role["rolsuper"] is False
    assert role["rolbypassrls"] is False
    owned = await app_conn.fetchval(
        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = current_user"
    )
    assert owned == 0


async def test_every_table_has_rls_enabled_with_a_policy(owner_conn: asyncpg.Connection) -> None:
    rows = await owner_conn.fetch(
        "SELECT c.relname, c.relrowsecurity, "
        "  (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'alembic_version'"
    )
    assert {r["relname"] for r in rows} == set(ALL_TABLES)
    for row in rows:
        assert row["relrowsecurity"], f"RLS disabled on {row['relname']}"
        assert row["policies"] >= 1, f"no policy on {row['relname']}"


async def test_without_tenant_context_every_table_is_empty(
    owner_conn: asyncpg.Connection, app_conn: asyncpg.Connection
) -> None:
    await seed_full_tenant(owner_conn, "alpha")
    for table in ALL_TABLES:
        assert await owner_conn.fetchval(f"SELECT count(*) FROM {table}") >= 1  # noqa: S608
        assert await app_conn.fetchval(f"SELECT count(*) FROM {table}") == 0, table  # noqa: S608


async def test_tenant_context_exposes_only_that_tenants_rows(
    owner_conn: asyncpg.Connection, app_conn: asyncpg.Connection
) -> None:
    alpha = await seed_full_tenant(owner_conn, "alpha")
    await seed_full_tenant(owner_conn, "bravo")
    async with app_conn.transaction():
        await set_tenant(app_conn, alpha["restaurant"])
        for table in ALL_TABLES:
            column = "id" if table == "restaurants" else "restaurant_id"
            rows = await app_conn.fetch(f"SELECT DISTINCT {column} AS rid FROM {table}")  # noqa: S608
            assert [r["rid"] for r in rows] == [alpha["restaurant"]], table


async def test_tenant_setting_does_not_outlive_its_transaction(
    owner_conn: asyncpg.Connection, app_conn: asyncpg.Connection
) -> None:
    alpha = await seed_full_tenant(owner_conn, "alpha")
    async with app_conn.transaction():
        await set_tenant(app_conn, alpha["restaurant"])
        assert await app_conn.fetchval("SELECT count(*) FROM orders") == 1
    # Same pooled connection, next transaction: nothing visible.
    assert await app_conn.fetchval("SELECT count(*) FROM orders") == 0


async def test_cannot_write_or_move_rows_across_tenants(
    owner_conn: asyncpg.Connection, app_conn: asyncpg.Connection
) -> None:
    alpha = await seed_full_tenant(owner_conn, "alpha")
    bravo = await seed_full_tenant(owner_conn, "bravo")

    async with app_conn.transaction():
        await set_tenant(app_conn, alpha["restaurant"])
        # Another tenant's rows are invisible, so updates/deletes simply match nothing.
        assert (
            await app_conn.execute(
                "UPDATE dining_tables SET label = 'pwned' WHERE id = $1", bravo["table"]
            )
            == "UPDATE 0"
        )
        assert (
            await app_conn.execute(
                "DELETE FROM table_sessions WHERE id = $1", bravo["table_session"]
            )
            == "DELETE 0"
        )

    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="row-level security"):
        async with app_conn.transaction():
            await set_tenant(app_conn, alpha["restaurant"])
            await app_conn.execute(
                "INSERT INTO dining_tables (restaurant_id, label) VALUES ($1, 'Evil')",
                bravo["restaurant"],
            )

    with pytest.raises(asyncpg.PostgresError):
        async with app_conn.transaction():
            await set_tenant(app_conn, alpha["restaurant"])
            await app_conn.execute(
                "UPDATE dining_tables SET restaurant_id = $1 WHERE id = $2",
                bravo["restaurant"],
                alpha["table"],
            )

    assert (
        await owner_conn.fetchval("SELECT label FROM dining_tables WHERE id = $1", bravo["table"])
        == "T1"
    )


async def test_composite_foreign_keys_block_cross_tenant_references_even_without_rls(
    owner_conn: asyncpg.Connection,
) -> None:
    alpha = await seed_full_tenant(owner_conn, "alpha")
    bravo = await seed_full_tenant(owner_conn, "bravo")
    # The owner connection bypasses RLS entirely; the schema must still refuse.
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await owner_conn.execute(
            "INSERT INTO table_sessions (restaurant_id, table_id, token_hash, pin_version, "
            "expires_at) VALUES ($1, $2, $3, 1, now() + interval '1 hour')",
            alpha["restaurant"],
            bravo["table"],
            _rand(),
        )
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await owner_conn.execute(
            "INSERT INTO order_items (restaurant_id, order_id, line_no, menu_item_id, item_name, "
            "quantity) VALUES ($1, $2, 2, $3, 'X', 1)",
            alpha["restaurant"],
            alpha["order"],
            bravo["item"],
        )


async def test_customer_order_must_be_for_its_sessions_table(
    owner_conn: asyncpg.Connection,
) -> None:
    alpha = await seed_full_tenant(owner_conn, "alpha")
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await owner_conn.execute(
            "INSERT INTO orders (restaurant_id, order_number, table_id, source, table_session_id, "
            "idempotency_key, request_fingerprint) VALUES ($1, 2, $2, 'customer', $3, $4, $5)",
            alpha["restaurant"],
            alpha["table2"],
            alpha["table_session"],
            "idem-key-0002",
            _rand(),
        )


async def test_logs_are_append_only_and_order_lines_immutable(
    owner_conn: asyncpg.Connection, app_conn: asyncpg.Connection
) -> None:
    alpha = await seed_full_tenant(owner_conn, "alpha")
    for statement in (
        "UPDATE audit_logs SET action = 'x.y'",
        "DELETE FROM audit_logs",
        "UPDATE order_events SET order_version = 99",
        "DELETE FROM order_events",
        "UPDATE order_items SET quantity = 50",
        "UPDATE order_item_modifiers SET modifier_name = 'x'",
        "DELETE FROM orders",
        "DELETE FROM users",
        "DELETE FROM restaurants",
    ):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with app_conn.transaction():
                await set_tenant(app_conn, alpha["restaurant"])
                await app_conn.execute(statement)


async def test_identity_columns_are_immutable(
    owner_conn: asyncpg.Connection, app_conn: asyncpg.Connection
) -> None:
    alpha = await seed_full_tenant(owner_conn, "alpha")
    for statement, args in (
        ("UPDATE table_sessions SET table_id = $1", (alpha["table2"],)),
        ("UPDATE table_sessions SET expires_at = expires_at + interval '1 day'", ()),
        ("UPDATE orders SET table_session_id = NULL, source = 'waiter'", ()),
        ("UPDATE pin_versions SET pin_hmac = $1", (_rand(),)),
        ("UPDATE refresh_tokens SET token_hash = $1", (_rand(),)),
    ):
        with pytest.raises(asyncpg.CheckViolationError, match="immutable"):
            async with app_conn.transaction():
                await set_tenant(app_conn, alpha["restaurant"])
                await app_conn.execute(statement, *args)

    async with app_conn.transaction():  # legitimate lifecycle updates still work
        await set_tenant(app_conn, alpha["restaurant"])
        await app_conn.execute(
            "UPDATE table_sessions SET status = 'closed', ended_at = now(), "
            "end_reason = 'table_closed'"
        )
        await app_conn.execute("UPDATE orders SET status = 'accepted', version = version + 1")


async def test_resolver_functions_are_not_executable_by_public(
    owner_conn: asyncpg.Connection,
) -> None:
    for fn in RESOLVERS:
        assert not await owner_conn.fetchval(
            "SELECT has_function_privilege('rr_probe', $1, 'EXECUTE')", fn
        ), fn
        assert await owner_conn.fetchval(
            "SELECT has_function_privilege('rr_app', $1, 'EXECUTE')", fn
        ), fn


async def test_resolvers_only_route_active_credentials(
    owner_conn: asyncpg.Connection, app_conn: asyncpg.Connection
) -> None:
    alpha = await seed_full_tenant(owner_conn, "alpha")
    qr = "qr_alpha_0123456789abcdef"

    row = await app_conn.fetchrow("SELECT * FROM app_resolve_qr_token($1)", qr)
    assert (row["restaurant_id"], row["table_id"]) == (alpha["restaurant"], alpha["table"])
    assert (
        await app_conn.fetchrow("SELECT * FROM app_resolve_qr_token('nope_0123456789abcd')") is None
    )
    assert (
        await app_conn.fetchval("SELECT app_resolve_restaurant_code('alpha')")
        == alpha["restaurant"]
    )

    await owner_conn.execute(
        "UPDATE dining_tables SET is_active = false WHERE id = $1", alpha["table"]
    )
    assert await app_conn.fetchrow("SELECT * FROM app_resolve_qr_token($1)", qr) is None
    await owner_conn.execute(
        "UPDATE dining_tables SET is_active = true WHERE id = $1", alpha["table"]
    )
    await owner_conn.execute(
        "UPDATE table_qr_tokens SET revoked_at = now() WHERE id = $1", alpha["qr"]
    )
    assert await app_conn.fetchrow("SELECT * FROM app_resolve_qr_token($1)", qr) is None

    session_hash = await owner_conn.fetchval(
        "SELECT token_hash FROM table_sessions WHERE id = $1", alpha["table_session"]
    )
    assert (
        await app_conn.fetchval("SELECT app_resolve_table_session($1)", session_hash)
        == alpha["restaurant"]
    )
    await owner_conn.execute(
        "UPDATE table_sessions SET status = 'revoked', ended_at = now(), "
        "end_reason = 'invalidated_by_staff' WHERE id = $1",
        alpha["table_session"],
    )
    assert await app_conn.fetchval("SELECT app_resolve_table_session($1)", session_hash) is None

    await owner_conn.execute(
        "UPDATE restaurants SET status = 'suspended' WHERE id = $1", alpha["restaurant"]
    )
    assert await app_conn.fetchval("SELECT app_resolve_restaurant_code('alpha')") is None


async def test_constraints_reject_invalid_data(owner_conn: asyncpg.Connection) -> None:
    alpha = await seed_full_tenant(owner_conn, "alpha")
    rid = alpha["restaurant"]
    insert_user = (
        "INSERT INTO users (restaurant_id, username, display_name, role, password_hash) "
        "VALUES ($1, $2, $3, $4, $5)"
    )
    bad_statements: tuple[tuple[type[Exception], str, tuple[object, ...]], ...] = (
        (asyncpg.UniqueViolationError, insert_user, (rid, "owner2", "Owner 2", "owner", FAKE_HASH)),
        (asyncpg.CheckViolationError, insert_user, (rid, "plain", "Plain", "waiter", "hunter2")),
        (asyncpg.CheckViolationError, insert_user, (rid, "boss", "Boss", "superadmin", FAKE_HASH)),
        (asyncpg.CheckViolationError, "UPDATE order_items SET quantity = 0", ()),
        (asyncpg.CheckViolationError, "UPDATE order_items SET quantity = 51", ()),
        (asyncpg.CheckViolationError, "UPDATE orders SET status = 'paid'", ()),
        (asyncpg.CheckViolationError, "UPDATE restaurants SET code = 'Bad Code'", ()),
        (asyncpg.CheckViolationError, "UPDATE restaurants SET pin_length = 3", ()),
        (asyncpg.CheckViolationError, "UPDATE menu_items SET display_price_paise = -1", ()),
        (
            asyncpg.UniqueViolationError,
            "INSERT INTO dining_tables (restaurant_id, label) VALUES ($1, 't1')",
            (rid,),
        ),
    )
    for error, statement, args in bad_statements:
        with pytest.raises(error):
            await owner_conn.execute(statement, *args)


async def test_bind_tenant_refuses_to_switch_tenants(clean_db: PgTestDatabase) -> None:
    engine = create_async_engine(clean_db.sqlalchemy_url("rr_app"))
    try:
        from sqlalchemy.ext.asyncio import AsyncSession

        async with AsyncSession(engine) as session:
            first = uuid.uuid4()
            await bind_tenant(session, first)
            await bind_tenant(session, first)  # idempotent
            with pytest.raises(TenantBindingError):
                await bind_tenant(session, uuid.uuid4())
    finally:
        await engine.dispose()
