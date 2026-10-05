"""Phase 4: staff management, role-escalation guards, IDOR protection."""

from __future__ import annotations

import asyncpg
import httpx
import pytest

from tests.conftest import PASSWORD, Seeder, bearer, login, login_ok, staff_headers

TEMP_PASSWORD = "Temporary-Pass-1"


async def _create(
    client: httpx.AsyncClient, headers: dict[str, str], username: str, role: str
) -> httpx.Response:
    return await client.post(
        "/api/v1/staff",
        headers=headers,
        json={
            "username": username,
            "display_name": username.title(),
            "role": role,
            "temporary_password": TEMP_PASSWORD,
        },
    )


async def test_owner_creates_staff_who_must_change_password(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    for username, role in (("manager", "admin"), ("ravi", "waiter"), ("chef", "kitchen")):
        response = await _create(client, owner, username, role)
        assert response.status_code == 201, response.text
        assert response.json()["must_change_password"] is True

    listed = (await client.get("/api/v1/staff", headers=owner)).json()
    assert {u["username"] for u in listed} == {"owner", "manager", "ravi", "chef"}
    assert all(not ({"password", "password_hash"} & user.keys()) for user in listed)
    assert "$argon2" not in str(listed)

    tokens = await login_ok(client, "alpha", "ravi", TEMP_PASSWORD)
    assert tokens["user"]["must_change_password"] is True
    blocked = await client.get("/api/v1/tables", headers=bearer(tokens["access_token"]))
    assert blocked.json()["error"]["code"] == "password_change_required"


async def test_role_escalation_is_blocked(client: httpx.AsyncClient, seed: Seeder) -> None:
    alpha = await seed.restaurant("alpha")
    admin_id = await seed.user(alpha, "admin", "manager")
    other_admin = await seed.user(alpha, "admin", "manager2")
    waiter_id = await seed.user(alpha, "waiter", "ravi")
    admin = await staff_headers(client, "alpha", "manager")
    waiter = await staff_headers(client, "alpha", "ravi")

    # Nobody can create an owner (not even the owner): schema rejects the role.
    owner = await staff_headers(client, "alpha", "owner")
    assert (await _create(client, owner, "boss2", "owner")).status_code == 422
    # Admins manage waiters/kitchen, but not other admins, and cannot mint admins.
    assert (await _create(client, admin, "newadmin", "admin")).status_code == 403
    assert (await _create(client, admin, "kitchen2", "kitchen")).status_code == 201
    promote = await client.patch(
        f"/api/v1/staff/{waiter_id}", headers=admin, json={"role": "admin"}
    )
    assert promote.status_code == 403
    for method, path, body in (
        ("PATCH", f"/api/v1/staff/{other_admin}", {"is_active": False}),
        (
            "POST",
            f"/api/v1/staff/{other_admin}/reset-password",
            {"temporary_password": TEMP_PASSWORD},
        ),
        ("DELETE", f"/api/v1/staff/{other_admin}", None),
        ("PATCH", f"/api/v1/staff/{alpha.owner_id}", {"display_name": "Pwned"}),
        ("DELETE", f"/api/v1/staff/{alpha.owner_id}", None),
        ("PATCH", f"/api/v1/staff/{admin_id}", {"role": "admin", "display_name": "Me"}),  # self
    ):
        response = await client.request(method, path, headers=admin, json=body)
        assert response.status_code == 403, (method, path)
    # The owner *can* manage admins, but still not the owner account.
    assert (
        await client.patch(f"/api/v1/staff/{other_admin}", headers=owner, json={"role": "waiter"})
    ).status_code == 200
    assert (
        await client.patch(f"/api/v1/staff/{alpha.owner_id}", headers=owner, json={"role": "admin"})
    ).status_code == 403
    # Waiters cannot manage or even list staff.
    assert (await client.get("/api/v1/staff", headers=waiter)).status_code == 403
    assert (await _create(client, waiter, "sneaky", "kitchen")).status_code == 403


async def test_deactivation_reset_and_delete_take_effect_immediately(
    client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    alpha = await seed.restaurant("alpha")
    waiter_id = await seed.user(alpha, "waiter", "ravi")
    owner = await staff_headers(client, "alpha", "owner")
    waiter = await login_ok(client, "alpha", "ravi")

    off = await client.patch(f"/api/v1/staff/{waiter_id}", headers=owner, json={"is_active": False})
    assert off.status_code == 200
    assert (
        await client.get("/api/v1/auth/me", headers=bearer(waiter["access_token"]))
    ).status_code == 401
    assert (await login(client, "alpha", "ravi")).status_code == 401
    on = await client.patch(f"/api/v1/staff/{waiter_id}", headers=owner, json={"is_active": True})
    assert on.status_code == 200
    waiter = await login_ok(client, "alpha", "ravi")

    reset = await client.post(
        f"/api/v1/staff/{waiter_id}/reset-password",
        headers=owner,
        json={"temporary_password": TEMP_PASSWORD},
    )
    assert reset.status_code == 204
    assert (
        await client.get("/api/v1/auth/me", headers=bearer(waiter["access_token"]))
    ).status_code == 401
    assert (await login(client, "alpha", "ravi", PASSWORD)).status_code == 401
    assert (await login_ok(client, "alpha", "ravi", TEMP_PASSWORD))["user"]["must_change_password"]

    assert (await client.delete(f"/api/v1/staff/{waiter_id}", headers=owner)).status_code == 204
    assert (await login(client, "alpha", "ravi", TEMP_PASSWORD)).status_code == 401
    row = await owner_conn.fetchrow(
        "SELECT deleted_at, is_active FROM users WHERE id = $1", waiter_id
    )
    assert row["deleted_at"] is not None and row["is_active"] is False  # soft delete
    # The username becomes available again.
    assert (await _create(client, owner, "ravi", "waiter")).status_code == 201

    actions = {r["action"] for r in await owner_conn.fetch("SELECT action FROM audit_logs")}
    assert {
        "staff.deactivated",
        "staff.reactivated",
        "staff.password_reset",
        "staff.deleted",
        "staff.created",
    } <= actions


async def test_role_change_applies_to_existing_tokens(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    alpha = await seed.restaurant("alpha")
    cook_id = await seed.user(alpha, "kitchen", "cook")
    owner = await staff_headers(client, "alpha", "owner")
    cook = await staff_headers(client, "alpha", "cook")
    assert (await client.get("/api/v1/pin", headers=cook)).status_code == 403
    await client.patch(f"/api/v1/staff/{cook_id}", headers=owner, json={"role": "waiter"})
    assert (await client.get("/api/v1/pin", headers=cook)).status_code == 200


async def test_duplicates_nulls_and_validation(client: httpx.AsyncClient, seed: Seeder) -> None:
    alpha = await seed.restaurant("alpha")
    waiter_id = await seed.user(alpha, "waiter", "ravi", email="ravi@alpha.example")
    owner = await staff_headers(client, "alpha", "owner")

    dup = await _create(client, owner, "RAVI", "kitchen")
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "username_taken"
    taken = await client.patch(
        f"/api/v1/staff/{waiter_id}", headers=owner, json={"email": "owner@alpha.example"}
    )
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "email_taken"

    assert (
        await client.patch(f"/api/v1/staff/{waiter_id}", headers=owner, json={"display_name": None})
    ).status_code == 422
    cleared = await client.patch(f"/api/v1/staff/{waiter_id}", headers=owner, json={"email": None})
    assert cleared.status_code == 200 and cleared.json()["email"] is None

    weak = await client.post(
        "/api/v1/staff",
        headers=owner,
        json={
            "username": "x1x",
            "display_name": "X",
            "role": "waiter",
            "temporary_password": "password1",
        },
    )
    assert weak.status_code == 422


async def test_other_restaurants_staff_are_invisible(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    alpha = await seed.restaurant("alpha")
    await seed.restaurant("bravo")
    alpha_waiter = await seed.user(alpha, "waiter", "ravi")
    bravo_owner = await staff_headers(client, "bravo", "owner")

    listed = (await client.get("/api/v1/staff", headers=bravo_owner)).json()
    assert str(alpha_waiter) not in {u["id"] for u in listed}
    for method, path, body in (
        ("PATCH", f"/api/v1/staff/{alpha_waiter}", {"is_active": False}),
        (
            "POST",
            f"/api/v1/staff/{alpha_waiter}/reset-password",
            {"temporary_password": TEMP_PASSWORD},
        ),
        ("DELETE", f"/api/v1/staff/{alpha_waiter}", None),
    ):
        response = await client.request(method, path, headers=bravo_owner, json=body)
        assert response.status_code == 404, (method, path)  # not 403: no existence oracle
    assert (await login(client, "alpha", "ravi")).status_code == 200


@pytest.mark.parametrize("bad_id", ["not-a-uuid", "1", "../../etc/passwd"])
async def test_malformed_ids_are_rejected(
    client: httpx.AsyncClient, seed: Seeder, bad_id: str
) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    response = await client.patch(
        f"/api/v1/staff/{bad_id}", headers=owner, json={"is_active": False}
    )
    assert response.status_code in (404, 422)
