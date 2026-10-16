"""Phase 4: table PIN management/rotation and customer table sessions (the PIN's consumer)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest
from cryptography.exceptions import InvalidTag

from app.core.config import Settings
from app.core.pin import PinCrypto, generate_pin, is_weak_pin
from app.db.session import Database
from app.services import pin_service
from tests.conftest import (
    Seeder,
    create_table,
    current_pin,
    open_table_session,
    staff_headers,
    wrong_pin,
)

INVALID = {"code": "invalid_table_access", "message": "Invalid table access code."}


# --- primitives --------------------------------------------------------------------------------


def test_generated_pins_are_never_weak_or_repeated() -> None:
    for length in (4, 6):
        for _ in range(500):
            pin = generate_pin(length, previous="1357"[:length] if length == 4 else None)
            assert len(pin) == length and pin.isdigit() and not is_weak_pin(pin)
            assert pin != "1357"
    for weak in ("0000", "1234", "4321", "8901", "1212", "2580", "123123", "999999"):
        assert is_weak_pin(weak), weak


def test_pin_crypto_binds_restaurant_and_version(settings: Settings) -> None:
    crypto = PinCrypto(settings)
    a, b = uuid.uuid4(), uuid.uuid4()
    blob = crypto.encrypt(a, 1, "4827")
    assert crypto.decrypt(a, 1, blob) == "4827"
    assert b"4827" not in blob
    for rid, version in ((b, 1), (a, 2)):  # ciphertext moved to another restaurant/version
        with pytest.raises(InvalidTag):
            crypto.decrypt(rid, version, blob)
    digest = crypto.digest(a, 1, "4827")
    assert crypto.matches(a, 1, "4827", digest)
    assert not crypto.matches(b, 1, "4827", digest)
    assert not crypto.matches(a, 2, "4827", digest)
    other_key = PinCrypto(
        settings.model_copy(update={"pin_hmac_key": settings.pin_hmac_key.__class__("k" * 40)})
    )
    assert not other_key.matches(a, 1, "4827", digest)  # a DB leak alone is not enough


# --- staff PIN management --------------------------------------------------------------------


async def test_pin_visibility_by_role_and_no_plaintext_at_rest(
    client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    alpha = await seed.restaurant("alpha")
    for role in ("admin", "waiter", "kitchen"):
        await seed.user(alpha, role, f"{role}1")
    owner = await staff_headers(client, "alpha", "owner")
    pin = await current_pin(client, owner)
    for username, expected in (("admin1", 200), ("waiter1", 200), ("kitchen1", 403)):
        response = await client.get(
            "/api/v1/pin", headers=await staff_headers(client, "alpha", username)
        )
        assert response.status_code == expected, username
    assert (await client.get("/api/v1/pin")).status_code == 401
    pin_response = await client.get("/api/v1/pin", headers=owner)
    assert pin_response.headers["cache-control"] == "no-store"

    row = await owner_conn.fetchrow("SELECT pin_hmac, pin_ciphertext FROM pin_versions")
    assert len(row["pin_hmac"]) == 32
    assert pin.encode() not in row["pin_hmac"] and pin.encode() not in row["pin_ciphertext"]
    logged = " ".join(
        r["d"] for r in await owner_conn.fetch("SELECT details::text AS d FROM audit_logs")
    )
    assert pin not in logged


async def test_rotation_kills_old_pin_but_keeps_existing_sessions(
    client: httpx.AsyncClient,
    client_from,
    seed: Seeder,
    owner_conn: asyncpg.Connection,  # noqa: ANN001
) -> None:
    alpha = await seed.restaurant("alpha")
    await seed.user(alpha, "waiter", "ravi")
    owner = await staff_headers(client, "alpha", "owner")
    table = await create_table(client, owner, "T4")
    old_pin = await current_pin(client, owner)

    customer_a = client_from("198.51.100.21")
    assert (await open_table_session(customer_a, table["qr_token"], old_pin)).status_code == 200

    waiter = await staff_headers(client, "alpha", "ravi")
    assert (await client.post("/api/v1/pin/rotate", headers=waiter, json={})).status_code == 403
    rotated = await client.post("/api/v1/pin/rotate", headers=owner, json={})
    assert rotated.status_code == 200
    body = rotated.json()
    assert body["version"] == 2 and body["pin"] != old_pin and body["sessions_invalidated"] == 0

    customer_b = client_from("198.51.100.22")
    old = await open_table_session(customer_b, table["qr_token"], old_pin)
    assert old.status_code == 401 and old.json()["error"] == INVALID
    assert (await open_table_session(customer_b, table["qr_token"], body["pin"])).status_code == 200
    # Customer A, who joined before the rotation, keeps their session.
    assert (await customer_a.get("/api/v1/public/session")).status_code == 200

    audit = await owner_conn.fetchrow("SELECT details FROM audit_logs WHERE action = 'pin.rotated'")
    assert '"reason": "manual"' in audit["details"] and '"pin_version": 2' in audit["details"]


async def test_rotate_and_invalidate_ends_every_session(
    client: httpx.AsyncClient,
    client_from,
    seed: Seeder,  # noqa: ANN001
) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    table = await create_table(client, owner, "T1")
    pin = await current_pin(client, owner)
    diners = [client_from(f"198.51.100.{30 + i}") for i in range(3)]
    for diner in diners:
        assert (await open_table_session(diner, table["qr_token"], pin)).status_code == 200

    rotated = await client.post(
        "/api/v1/pin/rotate", headers=owner, json={"invalidate_active_sessions": True}
    )
    assert rotated.json()["sessions_invalidated"] == 3
    for diner in diners:
        assert (await diner.get("/api/v1/public/session")).status_code == 401


async def test_invalidate_all_sessions_keeps_the_pin(
    client: httpx.AsyncClient,
    client_from,
    seed: Seeder,
    owner_conn: asyncpg.Connection,  # noqa: ANN001
) -> None:
    alpha = await seed.restaurant("alpha")
    await seed.user(alpha, "waiter", "ravi")
    owner = await staff_headers(client, "alpha", "owner")
    table = await create_table(client, owner, "T1")
    pin = await current_pin(client, owner)
    diner = client_from("198.51.100.40")
    await open_table_session(diner, table["qr_token"], pin)

    waiter = await staff_headers(client, "alpha", "ravi")
    assert (
        await client.post("/api/v1/table-sessions/invalidate-all", headers=waiter)
    ).status_code == 403
    response = await client.post("/api/v1/table-sessions/invalidate-all", headers=owner)
    assert response.json() == {"invalidated": 1}
    assert (await diner.get("/api/v1/public/session")).status_code == 401
    assert await current_pin(client, owner) == pin
    assert (await open_table_session(diner, table["qr_token"], pin)).status_code == 200
    assert (
        await owner_conn.fetchval(
            "SELECT count(*) FROM audit_logs WHERE action = 'table_sessions.invalidated'"
        )
        == 1
    )


async def test_pin_settings(client: httpx.AsyncClient, seed: Seeder) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    before = (await client.get("/api/v1/pin", headers=owner)).json()

    manual = await client.put(
        "/api/v1/pin/settings", headers=owner, json={"rotation_minutes": None, "pin_length": 4}
    )
    assert manual.status_code == 200
    assert manual.json()["rotation_minutes"] is None and manual.json()["next_rotation_at"] is None
    assert manual.json()["version"] == before["version"]  # interval change alone: same PIN

    six = await client.put(
        "/api/v1/pin/settings", headers=owner, json={"rotation_minutes": 120, "pin_length": 6}
    )
    body = six.json()
    assert len(body["pin"]) == 6 and body["version"] == before["version"] + 1
    assert body["rotation_minutes"] == 120 and body["next_rotation_at"] is not None

    for bad in (
        {"rotation_minutes": 300, "pin_length": 4},
        {"rotation_minutes": 120, "pin_length": 5},
        {"rotation_minutes": "120", "pin_length": 4},
        {"pin_length": 4},
    ):
        assert (
            await client.put("/api/v1/pin/settings", headers=owner, json=bad)
        ).status_code == 422


async def test_scheduled_rotation(
    seed: Seeder, settings: Settings, owner_conn: asyncpg.Connection
) -> None:
    every_2h = await seed.restaurant("alpha")
    manual = await seed.restaurant("bravo")
    await owner_conn.execute(
        "UPDATE restaurants SET pin_rotation_minutes = 120 WHERE id = $1", every_2h.id
    )
    await owner_conn.execute(
        "UPDATE restaurants SET pin_rotation_minutes = NULL WHERE id = $1", manual.id
    )

    db = Database(settings)
    crypto = PinCrypto(settings)
    try:
        now = datetime.now(UTC)
        assert await pin_service.rotate_due_pins(db, crypto, now=now + timedelta(minutes=30)) == 0
        assert await pin_service.rotate_due_pins(db, crypto, now=now + timedelta(hours=3)) == 1
        # Same tick again (e.g. a second worker): nothing left to do.
        assert await pin_service.rotate_due_pins(db, crypto, now=now + timedelta(hours=3)) == 0
    finally:
        await db.dispose()

    versions = dict(
        await owner_conn.fetch(
            "SELECT restaurant_id, max(version) FROM pin_versions GROUP BY restaurant_id"
        )
    )
    assert versions == {every_2h.id: 2, manual.id: 1}
    assert (
        await owner_conn.fetchval(
            "SELECT details->>'reason' FROM audit_logs WHERE action = 'pin.rotated'"
        )
        == "scheduled"
    )


# --- customer table sessions -----------------------------------------------------------------


async def test_customer_opens_a_session_with_qr_and_current_pin(
    client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    await seed.restaurant("alpha", name="Spice Garden")
    owner = await staff_headers(client, "alpha", "owner")
    table = await create_table(client, owner, "T4")
    pin = await current_pin(client, owner)
    diner = client  # cookies live in the client's jar

    landing = await diner.get(f"/api/v1/public/tables/{table['qr_token']}")
    assert landing.json() == {
        "restaurant_name": "Spice Garden",
        "table_label": "T4",
        "pin_length": 4,
        "has_session": False,
        "theme": None,  # the default look (branding: migration 0006)
        "tagline": None,
    }
    # The QR alone grants nothing.
    assert (await diner.get("/api/v1/public/session")).status_code == 401

    opened = await open_table_session(diner, table["qr_token"], pin)
    assert opened.status_code == 200
    assert opened.json()["table_label"] == "T4"
    cookie = opened.headers["set-cookie"]
    for attribute in (
        "__Host-ts=",
        "HttpOnly",
        "Secure",
        "SameSite=strict",
        "Path=/",
        "Max-Age=10800",
    ):
        assert attribute.lower() in cookie.lower(), attribute
    token = diner.cookies["__Host-ts"]
    assert pin not in token and len(token) >= 43

    assert (await diner.get(f"/api/v1/public/tables/{table['qr_token']}")).json()["has_session"]
    assert (await diner.get("/api/v1/public/session")).status_code == 200
    stored = await owner_conn.fetchval("SELECT token_hash FROM table_sessions")
    assert token.encode() not in stored and len(stored) == 32  # only the hash is stored

    left = await diner.delete("/api/v1/public/session")
    assert left.status_code == 204
    assert (await diner.get("/api/v1/public/session")).status_code == 401


async def test_every_failure_looks_the_same(
    client: httpx.AsyncClient,
    client_from,
    seed: Seeder,
    owner_conn: asyncpg.Connection,  # noqa: ANN001
) -> None:
    alpha = await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    table = await create_table(client, owner, "T1")
    disabled = await create_table(client, owner, "T2")
    await client.patch(f"/api/v1/tables/{disabled['id']}", headers=owner, json={"is_active": False})
    pin = await current_pin(client, owner)

    cases = [
        (table["qr_token"], wrong_pin(pin)),  # wrong PIN
        (table["qr_token"], pin[:-1]),  # wrong length
        (table["qr_token"], "12a4"),  # not digits
        (table["qr_token"], f"{pin} "),  # padded
        ("A" * 22, pin),  # unknown QR
        (disabled["qr_token"], pin),  # disabled table
    ]
    for i, (qr, attempt) in enumerate(cases):
        response = await open_table_session(client_from(f"198.51.100.{50 + i}"), qr, attempt)
        assert response.status_code == 401, (qr, attempt)
        assert response.json()["error"] == INVALID

    await owner_conn.execute("UPDATE restaurants SET status = 'suspended' WHERE id = $1", alpha.id)
    response = await open_table_session(client_from("198.51.100.99"), table["qr_token"], pin)
    assert response.status_code == 401 and response.json()["error"] == INVALID


async def test_pin_brute_force_is_capped_per_ip(client: httpx.AsyncClient, seed: Seeder) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    table = await create_table(client, owner, "T1")
    pin = await current_pin(client, owner)
    for _ in range(5):
        assert (
            await open_table_session(client, table["qr_token"], wrong_pin(pin))
        ).status_code == 401
    blocked = await open_table_session(client, table["qr_token"], pin)  # correct, but cooling down
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0


async def test_pin_brute_force_is_capped_per_table_across_ips_and_reset_by_rotation(
    client: httpx.AsyncClient,
    client_from,
    seed: Seeder,  # noqa: ANN001
) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    table = await create_table(client, owner, "T1")
    other_table = await create_table(client, owner, "T2")
    pin = await current_pin(client, owner)
    for i in range(10):  # ten attackers, one guess each
        attacker = client_from(f"203.0.113.{100 + i}")
        assert (
            await open_table_session(attacker, table["qr_token"], wrong_pin(pin))
        ).status_code == 401
    legit = client_from("198.51.100.200")
    assert (await open_table_session(legit, table["qr_token"], pin)).status_code == 429
    assert (await open_table_session(legit, other_table["qr_token"], pin)).status_code == 200

    # Staff rotate the PIN: guesses against the old PIN are worthless, so the lockout lifts.
    new_pin = (await client.post("/api/v1/pin/rotate", headers=owner, json={})).json()["pin"]
    newcomer = client_from("198.51.100.201")
    assert (await open_table_session(newcomer, table["qr_token"], new_pin)).status_code == 200


async def test_restaurant_wide_cap_alerts_staff(
    client: httpx.AsyncClient,
    client_from,
    seed: Seeder,
    owner_conn: asyncpg.Connection,  # noqa: ANN001
) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    tables = [await create_table(client, owner, f"T{i}") for i in range(7)]
    pin = await current_pin(client, owner)
    guesses = 0
    for i in range(60):  # spread across tables (≤10 each) and IPs (≤5 each)
        attacker = client_from(f"203.0.113.{i}")
        response = await open_table_session(attacker, tables[i % 7]["qr_token"], wrong_pin(pin))
        guesses += response.status_code == 401
    assert guesses == 60
    blocked = await open_table_session(client_from("198.51.100.250"), tables[0]["qr_token"], pin)
    assert blocked.status_code == 429
    assert (
        await owner_conn.fetchval(
            "SELECT count(*) FROM audit_logs WHERE action = 'pin.bruteforce_suspected'"
        )
        == 1
    )


async def test_sessions_are_bound_to_one_table_and_expire(
    client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    await seed.restaurant("alpha")
    await seed.restaurant("bravo")
    alpha_owner = await staff_headers(client, "alpha", "owner")
    bravo_owner = await staff_headers(client, "bravo", "owner")
    t1 = await create_table(client, alpha_owner, "T1")
    t2 = await create_table(client, alpha_owner, "T2")
    bravo_table = await create_table(client, bravo_owner, "T1")
    await open_table_session(client, t1["qr_token"], await current_pin(client, alpha_owner))

    # The same cookie does not count as a session for another table or restaurant.
    assert (await client.get(f"/api/v1/public/tables/{t2['qr_token']}")).json()[
        "has_session"
    ] is False
    other = await client.get(f"/api/v1/public/tables/{bravo_table['qr_token']}")
    assert other.status_code == 200 and other.json()["has_session"] is False
    assert (await client.get("/api/v1/public/session")).json()["table_label"] == "T1"

    # expires_at is immutable for the app; the schema owner simulates the passage of time.
    await owner_conn.execute(
        "ALTER TABLE table_sessions DISABLE TRIGGER trg_table_sessions_immutable"
    )
    try:
        await owner_conn.execute(
            "UPDATE table_sessions SET expires_at = created_at + interval '1 millisecond'"
        )
    finally:
        await owner_conn.execute(
            "ALTER TABLE table_sessions ENABLE TRIGGER trg_table_sessions_immutable"
        )
    assert (await client.get("/api/v1/public/session")).status_code == 401
    assert (await client.get(f"/api/v1/public/tables/{t1['qr_token']}")).json()[
        "has_session"
    ] is False


async def test_public_writes_check_origin_when_configured(clean_db: object) -> None:
    from app.main import create_app
    from tests.conftest import make_settings

    settings = make_settings(clean_db, public_web_origins="https://order.example.test")  # type: ignore[arg-type]
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("198.51.100.77", 1))
        async with httpx.AsyncClient(
            transport=transport, base_url="https://order.example.test"
        ) as c:
            path = "/api/v1/public/tables/" + "A" * 22 + "/session"
            evil = await c.post(
                path, json={"pin": "1234"}, headers={"Origin": "https://evil.example"}
            )
            assert evil.status_code == 403
            assert evil.json()["error"]["code"] == "origin_not_allowed"
            ours = await c.post(
                path, json={"pin": "1234"}, headers={"Origin": "https://order.example.test"}
            )
            assert ours.status_code == 401  # passes the origin check, then fails normally
            form = await c.post(
                path,
                content="pin=1234",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert form.status_code == 422
