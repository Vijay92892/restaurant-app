"""Phase 3: staff authentication, token lifecycle, RBAC and tenant isolation through the API."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest

from app.core.config import Settings
from app.core.security import create_access_token, passwords
from tests.conftest import PASSWORD, Seeder, bearer, login, login_ok

INVALID_CREDENTIALS = {"code": "invalid_credentials", "message": "Invalid credentials."}


# --- login -----------------------------------------------------------------------------------


async def test_login_issues_tokens_and_me_reflects_role(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    alpha = await seed.restaurant("alpha")
    response = await login(client, "alpha", "owner")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert body["user"]["restaurant_id"] == str(alpha.id)
    assert body["user"]["role"] == "owner"
    assert "staff.manage_admins" in body["user"]["permissions"]

    me = await client.get("/api/v1/auth/me", headers=bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["username"] == "owner"


async def test_login_normalises_code_and_username(client: httpx.AsyncClient, seed: Seeder) -> None:
    await seed.restaurant("alpha")
    response = await login(client, "  ALPHA ", " Owner")
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("code", "username", "password"),
    [
        ("alpha", "owner", "Wrong-Password-1"),  # wrong password
        ("alpha", "nobody", PASSWORD),  # unknown user
        ("nosuch", "owner", PASSWORD),  # unknown restaurant
        ("bravo", "waiter1", PASSWORD),  # valid credentials, but for another restaurant
    ],
)
async def test_login_failures_are_indistinguishable(
    client: httpx.AsyncClient, seed: Seeder, code: str, username: str, password: str
) -> None:
    alpha = await seed.restaurant("alpha")
    await seed.restaurant("bravo")
    await seed.user(alpha, "waiter", "waiter1")
    response = await login(client, code, username, password)
    assert response.status_code == 401
    assert response.json()["error"] == INVALID_CREDENTIALS


async def test_inactive_user_and_suspended_restaurant_cannot_login(
    client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    alpha = await seed.restaurant("alpha")
    await seed.user(alpha, "waiter", "sleepy", is_active=False)
    response = await login(client, "alpha", "sleepy")
    assert response.status_code == 401
    assert response.json()["error"] == INVALID_CREDENTIALS

    await owner_conn.execute("UPDATE restaurants SET status = 'suspended' WHERE id = $1", alpha.id)
    response = await login(client, "alpha", "owner")
    assert response.status_code == 401
    assert response.json()["error"] == INVALID_CREDENTIALS


async def test_failed_logins_are_audited_without_secrets(
    client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    alpha = await seed.restaurant("alpha")
    await login(client, "alpha", "owner", "Not-The-Password-9")
    tokens = await login_ok(client, "alpha", "owner")

    rows = await owner_conn.fetch(
        "SELECT action, details::text AS details, ip::text AS ip FROM audit_logs "
        "WHERE restaurant_id = $1 ORDER BY id",
        alpha.id,
    )
    actions = [r["action"] for r in rows]
    assert "auth.login.failed" in actions
    assert "auth.login.succeeded" in actions
    failed = next(r for r in rows if r["action"] == "auth.login.failed")
    assert json.loads(failed["details"]) == {"username": "owner", "reason": "bad_password"}
    assert failed["ip"].startswith("203.0.113.10")

    everything = " ".join(r["details"] for r in rows)
    for secret in ("Not-The-Password-9", PASSWORD, tokens["access_token"], tokens["refresh_token"]):
        assert secret not in everything


async def test_account_is_temporarily_locked_after_repeated_failures(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    alpha = await seed.restaurant("alpha")
    await seed.user(alpha, "waiter", "waiter1")
    for _ in range(5):
        assert (await login(client, "alpha", "owner", "Wrong-Password-1")).status_code == 401
    locked = await login(client, "alpha", "owner")  # correct password, still refused
    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) > 0
    assert locked.json()["error"]["code"] == "rate_limited"
    # Other accounts are unaffected.
    assert (await login(client, "alpha", "waiter1")).status_code == 200


async def test_login_is_rate_limited_per_ip(client: httpx.AsyncClient, seed: Seeder) -> None:
    await seed.restaurant("alpha")
    statuses = [(await login(client, "nosuch", f"user{i}", "x")).status_code for i in range(31)]
    assert statuses[:20] == [401] * 20  # unknown-restaurant failures
    assert 429 in statuses


@pytest.mark.parametrize(
    "attack",
    [
        "' OR '1'='1",
        "owner'--",
        '"; DROP TABLE users; --',
        "owner\u0000",
        "x" * 5000,
        "<script>alert(1)</script>",
    ],
)
async def test_injection_strings_are_rejected_safely(
    client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection, attack: str
) -> None:
    await seed.restaurant("alpha")
    for payload in (
        {"restaurant_code": attack, "username": "owner", "password": PASSWORD},
        {"restaurant_code": "alpha", "username": attack, "password": PASSWORD},
        {"restaurant_code": "alpha", "username": "owner", "password": attack},
    ):
        response = await client.post("/api/v1/auth/login", json=payload)
        assert response.status_code in (401, 422), response.text
        assert attack[:50] not in response.text
    assert await owner_conn.fetchval("SELECT count(*) FROM users") == 1


async def test_unknown_fields_and_wrong_types_are_rejected_without_echo(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "restaurant_code": "alpha",
            "username": "owner",
            "password": "Sup3r-Secret-Value",
            "role": "owner",
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert any(field["loc"][-1] == "role" for field in body["error"]["fields"])
    assert "Sup3r-Secret-Value" not in response.text

    response = await client.post(
        "/api/v1/auth/login",
        json={"restaurant_code": "alpha", "username": 12345, "password": "x"},
    )
    assert response.status_code == 422


# --- access tokens ---------------------------------------------------------------------------


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


async def test_malformed_forged_and_expired_access_tokens_are_rejected(
    client: httpx.AsyncClient, seed: Seeder, settings: Settings
) -> None:
    alpha = await seed.restaurant("alpha")
    tokens = await login_ok(client, "alpha", "owner")
    good_claims = json.loads(
        base64.urlsafe_b64decode(tokens["access_token"].split(".")[1] + "==").decode()
    )
    session_id = uuid.UUID(good_claims["sid"])

    other_key = settings.model_copy(update={"jwt_secret": settings.jwt_secret.__class__("x" * 40)})
    forged_signature, _ = create_access_token(
        other_key, user_id=alpha.owner_id, restaurant_id=alpha.id, session_id=session_id
    )
    alg_none = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(good_claims)}."
    expired, _ = create_access_token(
        settings,
        user_id=alpha.owner_id,
        restaurant_id=alpha.id,
        session_id=session_id,
        now=datetime.now(UTC) - timedelta(hours=1),
    )
    wrong_audience, _ = create_access_token(
        settings.model_copy(update={"jwt_audience": "someone-else"}),
        user_id=alpha.owner_id,
        restaurant_id=alpha.id,
        session_id=session_id,
    )

    cases = {
        "missing": {},
        "garbage": bearer("not-a-jwt"),
        "basic scheme": {"Authorization": f"Basic {tokens['access_token']}"},
        "forged signature": bearer(forged_signature),
        "alg none": bearer(alg_none),
        "wrong audience": bearer(wrong_audience),
        "refresh token as bearer": bearer(tokens["refresh_token"]),
    }
    for name, headers in cases.items():
        response = await client.get("/api/v1/auth/me", headers=headers)
        assert response.status_code == 401, name

    response = await client.get("/api/v1/auth/me", headers=bearer(expired))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_expired"


async def test_validly_signed_token_cannot_cross_tenants(
    client: httpx.AsyncClient, seed: Seeder, settings: Settings
) -> None:
    """Even a correctly signed token naming another restaurant finds nothing there (RLS)."""
    alpha = await seed.restaurant("alpha")
    bravo = await seed.restaurant("bravo")
    tokens = await login_ok(client, "alpha", "owner")
    claims = json.loads(
        base64.urlsafe_b64decode(tokens["access_token"].split(".")[1] + "==").decode()
    )
    cross, _ = create_access_token(
        settings,
        user_id=alpha.owner_id,
        restaurant_id=bravo.id,
        session_id=uuid.UUID(claims["sid"]),
    )
    response = await client.get("/api/v1/restaurant", headers=bearer(cross))
    assert response.status_code == 401


async def test_role_and_status_are_read_from_the_database_on_every_request(
    client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    alpha = await seed.restaurant("alpha")
    admin_id = await seed.user(alpha, "admin", "manager")
    headers = bearer((await login_ok(client, "alpha", "manager"))["access_token"])
    assert (await client.get("/api/v1/audit-logs", headers=headers)).status_code == 200

    await owner_conn.execute("UPDATE users SET role = 'waiter' WHERE id = $1", admin_id)
    assert (await client.get("/api/v1/audit-logs", headers=headers)).status_code == 403

    await owner_conn.execute("UPDATE users SET is_active = false WHERE id = $1", admin_id)
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401


@pytest.mark.parametrize(
    ("role", "audit_status"),
    [("owner", 200), ("admin", 200), ("waiter", 403), ("kitchen", 403)],
)
async def test_permissions_are_enforced_server_side(
    client: httpx.AsyncClient, seed: Seeder, role: str, audit_status: int
) -> None:
    alpha = await seed.restaurant("alpha")
    username = "owner" if role == "owner" else f"{role}1"
    if role != "owner":
        await seed.user(alpha, role, username)
    headers = bearer((await login_ok(client, "alpha", username))["access_token"])
    assert (await client.get("/api/v1/restaurant", headers=headers)).status_code == 200
    response = await client.get("/api/v1/audit-logs", headers=headers)
    assert response.status_code == audit_status
    if audit_status == 403:
        assert response.json()["error"]["code"] == "forbidden"


async def test_api_reads_are_tenant_scoped(client: httpx.AsyncClient, seed: Seeder) -> None:
    alpha = await seed.restaurant("alpha")
    bravo = await seed.restaurant("bravo")
    alpha_headers = bearer((await login_ok(client, "alpha", "owner"))["access_token"])
    await login_ok(client, "bravo", "owner")

    restaurant = (await client.get("/api/v1/restaurant", headers=alpha_headers)).json()
    assert restaurant["id"] == str(alpha.id)

    logs = (await client.get("/api/v1/audit-logs", headers=alpha_headers)).json()
    assert logs
    seen = {log["entity_id"] for log in logs} | {log["actor_user_id"] for log in logs}
    assert str(bravo.id) not in seen
    assert str(bravo.owner_id) not in seen


# --- refresh / logout ------------------------------------------------------------------------


async def _refresh(client: httpx.AsyncClient, token: str) -> httpx.Response:
    return await client.post("/api/v1/auth/refresh", json={"refresh_token": token})


async def test_refresh_rotates_tokens(client: httpx.AsyncClient, seed: Seeder) -> None:
    await seed.restaurant("alpha")
    first = await login_ok(client, "alpha", "owner")
    response = await _refresh(client, first["refresh_token"])
    assert response.status_code == 200
    second = response.json()
    assert second["refresh_token"] != first["refresh_token"]
    me = await client.get("/api/v1/auth/me", headers=bearer(second["access_token"]))
    assert me.status_code == 200


async def test_reusing_a_rotated_refresh_token_revokes_the_session(
    client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    alpha = await seed.restaurant("alpha")
    first = await login_ok(client, "alpha", "owner")
    second = (await _refresh(client, first["refresh_token"])).json()
    # Move the rotation outside the grace window, then replay the old token (theft).
    await owner_conn.execute(
        "UPDATE refresh_tokens SET rotated_at = rotated_at - interval '10 minutes' "
        "WHERE rotated_at IS NOT NULL"
    )
    replay = await _refresh(client, first["refresh_token"])
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "session_expired"

    assert (await _refresh(client, second["refresh_token"])).status_code == 401
    me = await client.get("/api/v1/auth/me", headers=bearer(second["access_token"]))
    assert me.status_code == 401
    assert (
        await owner_conn.fetchval(
            "SELECT count(*) FROM audit_logs WHERE restaurant_id = $1 "
            "AND action = 'auth.refresh.reuse_detected'",
            alpha.id,
        )
        == 1
    )


async def test_refresh_replay_within_grace_recovers_a_lost_response(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    await seed.restaurant("alpha")
    first = await login_ok(client, "alpha", "owner")
    lost = (await _refresh(client, first["refresh_token"])).json()  # response "lost"
    retry = await _refresh(client, first["refresh_token"])
    assert retry.status_code == 200
    recovered = retry.json()
    assert (await _refresh(client, lost["refresh_token"])).status_code == 401  # superseded
    assert (await _refresh(client, recovered["refresh_token"])).status_code == 200


async def test_refresh_replay_after_child_was_used_is_treated_as_theft(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    await seed.restaurant("alpha")
    first = await login_ok(client, "alpha", "owner")
    second = (await _refresh(client, first["refresh_token"])).json()
    third = (await _refresh(client, second["refresh_token"])).json()
    assert (await _refresh(client, first["refresh_token"])).status_code == 401
    assert (await _refresh(client, third["refresh_token"])).status_code == 401


async def test_refresh_fails_for_deactivated_user_and_expired_session(
    client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    alpha = await seed.restaurant("alpha")
    waiter_id = await seed.user(alpha, "waiter", "waiter1")
    waiter = await login_ok(client, "alpha", "waiter1")
    await owner_conn.execute("UPDATE users SET is_active = false WHERE id = $1", waiter_id)
    assert (await _refresh(client, waiter["refresh_token"])).status_code == 401

    owner = await login_ok(client, "alpha", "owner")
    await owner_conn.execute(
        "UPDATE auth_sessions SET expires_at = created_at + interval '1 millisecond' "
        "WHERE user_id = $1",
        alpha.owner_id,
    )
    assert (await _refresh(client, owner["refresh_token"])).status_code == 401


async def test_refresh_token_shape_is_validated(client: httpx.AsyncClient) -> None:
    for bad in ("", "short", "x" * 500, "has spaces in it 0123456789"):
        response = await _refresh(client, bad)
        assert response.status_code == 422
    unknown = await _refresh(client, "A" * 43)
    assert unknown.status_code == 401


async def test_logout_revokes_access_and_refresh_immediately(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    await seed.restaurant("alpha")
    tokens = await login_ok(client, "alpha", "owner")
    other = await login_ok(client, "alpha", "owner")
    headers = bearer(tokens["access_token"])
    assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401
    assert (await _refresh(client, tokens["refresh_token"])).status_code == 401
    # A different device's session is untouched by a single logout.
    assert (
        await client.get("/api/v1/auth/me", headers=bearer(other["access_token"]))
    ).status_code == 200


async def test_logout_all_revokes_every_session(client: httpx.AsyncClient, seed: Seeder) -> None:
    await seed.restaurant("alpha")
    sessions = [await login_ok(client, "alpha", "owner") for _ in range(3)]
    response = await client.post(
        "/api/v1/auth/logout-all", headers=bearer(sessions[0]["access_token"])
    )
    assert response.status_code == 204
    for tokens in sessions:
        me = await client.get("/api/v1/auth/me", headers=bearer(tokens["access_token"]))
        assert me.status_code == 401
        assert (await _refresh(client, tokens["refresh_token"])).status_code == 401


# --- passwords -------------------------------------------------------------------------------


async def _change_password(
    client: httpx.AsyncClient, access: str, current: str, new: str
) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/change-password",
        headers=bearer(access),
        json={"current_password": current, "new_password": new},
    )


async def test_change_password_revokes_other_sessions(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    await seed.restaurant("alpha")
    this_device = await login_ok(client, "alpha", "owner")
    other_device = await login_ok(client, "alpha", "owner")
    access = this_device["access_token"]

    wrong = await _change_password(client, access, "Wrong-Password-1", "Brand-New-Pass-77")
    assert wrong.status_code == 422
    assert wrong.json()["error"]["code"] == "invalid_current_password"
    for weak in ("password123", "aaaaaaaaaaa", "my-owner-pass"):
        response = await _change_password(client, access, PASSWORD, weak)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "weak_password"

    assert (
        await _change_password(client, access, PASSWORD, "Brand-New-Pass-77")
    ).status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=bearer(access))).status_code == 200
    other = await client.get("/api/v1/auth/me", headers=bearer(other_device["access_token"]))
    assert other.status_code == 401
    assert (await login(client, "alpha", "owner")).status_code == 401
    assert (await login(client, "alpha", "owner", "Brand-New-Pass-77")).status_code == 200


async def test_must_change_password_blocks_everything_else(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    alpha = await seed.restaurant("alpha")
    await seed.user(alpha, "admin", "newadmin", must_change_password=True)
    tokens = await login_ok(client, "alpha", "newadmin")
    headers = bearer(tokens["access_token"])
    assert tokens["user"]["must_change_password"] is True
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200
    blocked = await client.get("/api/v1/restaurant", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "password_change_required"

    changed = await _change_password(client, tokens["access_token"], PASSWORD, "Fresh-Start-2026")
    assert changed.status_code == 204
    assert (await client.get("/api/v1/restaurant", headers=headers)).status_code == 200


def _reset_token_from_outbox(app: object) -> str:
    message = app.state.mailer.outbox[-1]  # type: ignore[attr-defined]
    return message.body.split("#token=", 1)[1].split()[0]


async def test_password_reset_flow(client: httpx.AsyncClient, seed: Seeder, app: object) -> None:
    alpha = await seed.restaurant("alpha")
    old_session = await login_ok(client, "alpha", "owner")
    outbox = app.state.mailer.outbox  # type: ignore[attr-defined]

    unknown = await client.post(
        "/api/v1/auth/password-reset/request",
        json={"restaurant_code": "alpha", "email": "nobody@alpha.example"},
    )
    assert unknown.status_code == 202
    assert outbox == []

    known = await client.post(
        "/api/v1/auth/password-reset/request",
        json={"restaurant_code": "ALPHA", "email": alpha.owner_email.upper()},
    )
    assert known.status_code == 202
    assert known.json() == unknown.json()
    assert len(outbox) == 1 and outbox[0].to == alpha.owner_email
    token = _reset_token_from_outbox(app)

    weak = await client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "password1"}
    )
    assert weak.status_code == 422  # and the token is not consumed:
    confirm = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "Reset-Works-55"},
    )
    assert confirm.status_code == 204
    reuse = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "Another-One-66"},
    )
    assert reuse.status_code == 422
    assert reuse.json()["error"]["code"] == "invalid_reset_token"

    me = await client.get("/api/v1/auth/me", headers=bearer(old_session["access_token"]))
    assert me.status_code == 401
    assert (await login(client, "alpha", "owner", "Reset-Works-55")).status_code == 200


async def test_expired_reset_token_is_rejected(
    client: httpx.AsyncClient, seed: Seeder, app: object, owner_conn: asyncpg.Connection
) -> None:
    alpha = await seed.restaurant("alpha")
    await client.post(
        "/api/v1/auth/password-reset/request",
        json={"restaurant_code": "alpha", "email": alpha.owner_email},
    )
    token = _reset_token_from_outbox(app)
    await owner_conn.execute("UPDATE password_reset_tokens SET expires_at = now() - interval '1 s'")
    response = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "Reset-Works-55"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_reset_token"


async def test_password_reset_requests_are_rate_limited(client: httpx.AsyncClient) -> None:
    statuses = [
        (
            await client.post(
                "/api/v1/auth/password-reset/request",
                json={"restaurant_code": "alpha", "email": f"user{i}@alpha.example"},
            )
        ).status_code
        for i in range(6)
    ]
    assert statuses[:5] == [202] * 5
    assert statuses[5] == 429


async def test_passwords_are_argon2id_and_rehashed_when_parameters_change(
    client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection, settings: Settings
) -> None:
    alpha = await seed.restaurant("alpha")
    stored = await owner_conn.fetchval(
        "SELECT password_hash FROM users WHERE id = $1", alpha.owner_id
    )
    assert stored.startswith("$argon2id$v=19$m=1024,t=1,p=1$")

    passwords.configure(settings.model_copy(update={"argon2_time_cost": 2}))
    try:
        await login_ok(client, "alpha", "owner")
        upgraded = await owner_conn.fetchval(
            "SELECT password_hash FROM users WHERE id = $1", alpha.owner_id
        )
        assert upgraded.startswith("$argon2id$v=19$m=1024,t=2,p=1$")
    finally:
        passwords.configure(settings)
