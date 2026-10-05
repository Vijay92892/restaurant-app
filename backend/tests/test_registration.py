"""Phase 4: restaurant self-registration and restaurant settings."""

from __future__ import annotations

import asyncpg
import httpx
import pytest

from app.main import create_app
from tests.conftest import INVITE_CODE, Seeder, bearer, make_settings, staff_headers

REGISTRATION = {
    "restaurant_name": "Spice Garden",
    "restaurant_code": "spice-garden",
    "owner_display_name": "Asha",
    "owner_username": "asha",
    "owner_email": "asha@example.com",
    "password": "Spice-Garden-2026",
    "invite_code": INVITE_CODE,
}


async def _register(client: httpx.AsyncClient, **overrides: object) -> httpx.Response:
    return await client.post("/api/v1/restaurants", json={**REGISTRATION, **overrides})


async def test_invite_registration_creates_restaurant_owner_and_pin(
    client: httpx.AsyncClient, owner_conn: asyncpg.Connection
) -> None:
    response = await _register(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["role"] == "owner"
    assert body["user"]["restaurant_code"] == "spice-garden"
    headers = bearer(body["access_token"])

    pin = (await client.get("/api/v1/pin", headers=headers)).json()
    assert pin["version"] == 1 and len(pin["pin"]) == 4 and pin["rotation_minutes"] == 240

    actions = await owner_conn.fetch(
        "SELECT action, details->>'source' AS source FROM audit_logs ORDER BY id"
    )
    assert ("restaurant.created", "self_service") in [(a["action"], a["source"]) for a in actions]


@pytest.mark.parametrize("invite", [None, "wrong-code", INVITE_CODE + "x", ""])
async def test_invite_mode_rejects_missing_or_wrong_codes(
    client: httpx.AsyncClient, owner_conn: asyncpg.Connection, invite: str | None
) -> None:
    response = await _register(client, invite_code=invite)
    if invite == "":
        assert response.status_code == 422  # empty string fails schema validation
    else:
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "invalid_invite_code"
    assert await owner_conn.fetchval("SELECT count(*) FROM restaurants") == 0


async def test_closed_and_open_modes(clean_db: object) -> None:
    for mode, expected in (("closed", 403), ("open", 201)):
        app = create_app(make_settings(clean_db, registration_mode=mode))  # type: ignore[arg-type]
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app, client=("198.51.100.1", 1))
            async with httpx.AsyncClient(transport=transport, base_url="https://t") as client:
                response = await _register(client, invite_code=None, restaurant_code=f"r-{mode}")
        assert response.status_code == expected, response.text
        if expected == 403:
            assert response.json()["error"]["code"] == "registration_closed"


async def test_registration_validation_and_conflicts(client: httpx.AsyncClient) -> None:
    assert (await _register(client)).status_code == 201
    duplicate = await _register(client, owner_username="other")
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "restaurant_code_taken"

    bad_inputs = [
        {"password": "password123"},
        {"password": "asha-is-great"},  # contains username
        {"restaurant_code": "Bad Code!"},
        {"restaurant_name": "Evil" + chr(0x202E) + "Name"},  # bidi override (spoofing)
        {"restaurant_name": ""},
        {"owner_email": "not-an-email"},
        {"role": "owner"},  # unknown field
    ]
    for overrides in bad_inputs:
        response = await _register(client, **{"restaurant_code": "another-one", **overrides})
        assert response.status_code == 422, overrides


async def test_registration_is_rate_limited(client: httpx.AsyncClient) -> None:
    statuses = [
        (await _register(client, restaurant_code=f"place-{i}", invite_code="nope")).status_code
        for i in range(6)
    ]
    assert statuses == [403] * 5 + [429]


async def test_restaurant_settings_update(
    client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    alpha = await seed.restaurant("alpha")
    await seed.user(alpha, "waiter", "waiter1")
    owner = await staff_headers(client, "alpha", "owner")
    waiter = await staff_headers(client, "alpha", "waiter1")

    update = {"name": "  Alpha   Bistro ", "timezone": "Asia/Kolkata", "kitchen_delay_minutes": 20}
    response = await client.patch("/api/v1/restaurant", headers=owner, json=update)
    assert response.status_code == 200
    assert response.json()["name"] == "Alpha Bistro"  # whitespace normalised
    assert response.json()["kitchen_delay_minutes"] == 20

    forbidden = await client.patch("/api/v1/restaurant", headers=waiter, json={"name": "X"})
    assert forbidden.status_code == 403
    for bad in (
        {"timezone": "Mars/Olympus"},
        {"table_session_ttl_minutes": 5},
        {"name": None},
        {},
        {"code": "new-code"},  # the restaurant code is not editable
        {"status": "suspended"},
    ):
        response = await client.patch("/api/v1/restaurant", headers=owner, json=bad)
        assert response.status_code == 422, bad
    assert (
        await owner_conn.fetchval(
            "SELECT count(*) FROM audit_logs WHERE action = 'restaurant.settings_changed'"
        )
        == 1
    )
