"""Cross-cutting hardening: error envelopes, leakage, body limits, headers, IPs, config guards."""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.net import resolve_client_ip
from app.core.permissions import ROLE_PERMISSIONS, Permission, permissions_for
from app.core.rate_limit import Limit, MemoryRateLimiter
from app.db.session import Database
from app.main import create_app, verify_runtime_db_role
from app.models.enums import ActorType, Role
from app.services import audit
from tests.conftest import CLIENT_IP, PgTestDatabase, make_settings


async def test_unknown_route_and_wrong_method_use_the_error_envelope(
    client: httpx.AsyncClient,
) -> None:
    missing = await client.get("/api/v1/does-not-exist")
    assert missing.status_code == 404
    body = missing.json()
    assert body["error"] == {"code": "not_found", "message": "Not found."}
    assert body["request_id"] == missing.headers["x-request-id"]

    wrong_method = await client.get("/api/v1/auth/login")
    assert wrong_method.status_code == 405
    assert wrong_method.json()["error"]["code"] == "method_not_allowed"


async def test_health_checks_answer_head_for_uptime_monitors(client: httpx.AsyncClient) -> None:
    for path in ("/healthz", "/readyz"):
        response = await client.head(path)
        assert response.status_code == 200, path
        assert response.content == b""


async def test_security_headers_are_set(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in response.headers  # only in production


async def test_docs_page_can_load_swagger_while_api_keeps_strict_csp(
    client: httpx.AsyncClient,
) -> None:
    docs = await client.get("/docs")
    assert docs.status_code == 200
    docs_csp = docs.headers["content-security-policy"]
    hosts = set(re.findall(r'(?:src|href)="(https://[^/"]+)', docs.text))
    assert hosts, "expected Swagger UI to reference external assets"
    for host in hosts:  # a blank docs page means the CSP blocked one of these
        assert host in docs_csp, host
    assert "'unsafe-inline'" in docs_csp  # Swagger boots from an inline <script>

    api = await client.get("/api/v1/auth/me")
    assert api.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"

    root = await client.get("/", follow_redirects=False)
    assert root.status_code == 307
    assert root.headers["location"] == "/docs"


async def test_docs_and_root_redirect_do_not_exist_in_production(settings: Settings) -> None:
    app = create_app(settings.model_copy(update={"env": "production"}))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://t") as client:
        for path in ("/docs", "/openapi.json", "/"):
            response = await client.get(path, follow_redirects=False)
            assert response.status_code == 404, path


async def test_inbound_request_id_is_validated(client: httpx.AsyncClient) -> None:
    good = await client.get("/healthz", headers={"X-Request-ID": "abc-123-def-456"})
    assert good.headers["x-request-id"] == "abc-123-def-456"
    evil = await client.get("/healthz", headers={"X-Request-ID": "x\r\nSet-Cookie: a=b"})
    assert evil.headers["x-request-id"] != "x\r\nSet-Cookie: a=b"
    assert "set-cookie" not in evil.headers


async def test_unhandled_errors_do_not_leak_internals(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("password=hunter2 at C:\\secret\\path.py line 42 SELECT * FROM users")

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="https://t") as client:
            response = await client.get("/boom")
    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "internal_error",
        "message": "Something went wrong.",
    }
    for leak in ("hunter2", "secret", "SELECT", "Traceback", "RuntimeError"):
        assert leak not in response.text


async def test_oversized_bodies_are_rejected(client: httpx.AsyncClient) -> None:
    big = b'{"x":"' + b"a" * (1_048_576 + 10) + b'"}'
    declared = await client.post(
        "/api/v1/auth/login", content=big, headers={"Content-Type": "application/json"}
    )
    assert declared.status_code == 413
    assert declared.json()["error"]["code"] == "payload_too_large"

    async def stream() -> AsyncIterator[bytes]:
        for _ in range(20):
            yield b"a" * 65_536

    streamed = await client.post(
        "/api/v1/auth/login", content=stream(), headers={"Content-Type": "application/json"}
    )
    assert streamed.status_code == 413


async def test_non_json_bodies_are_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        content="restaurant_code=alpha&username=owner&password=x",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 422


def test_forwarded_for_is_only_trusted_from_configured_proxies() -> None:
    proxies = ("10.0.0.0/8",)
    spoofed = "1.2.3.4"
    # Direct client: header ignored.
    assert resolve_client_ip(CLIENT_IP, spoofed, proxies) == CLIENT_IP
    # Via our proxy: right-most untrusted hop wins (left-most entries are client-controlled).
    assert resolve_client_ip("10.0.0.5", f"{spoofed}, 198.51.100.7", proxies) == "198.51.100.7"
    assert resolve_client_ip("10.0.0.5", "198.51.100.7, 10.0.0.9", proxies) == "198.51.100.7"
    # No proxies configured: never trust the header.
    assert resolve_client_ip("10.0.0.5", spoofed, ()) == "10.0.0.5"
    # Garbage in the header stops the walk rather than being returned.
    assert resolve_client_ip("10.0.0.5", "not-an-ip", proxies) == "10.0.0.5"


async def test_runtime_role_check_refuses_privileged_roles_in_production(
    clean_db: PgTestDatabase,
) -> None:
    owner_settings = make_settings(clean_db, database_url=clean_db.sqlalchemy_url("rr_owner"))
    db = Database(owner_settings)
    try:
        prod = owner_settings.model_copy(update={"env": "production"})
        with pytest.raises(RuntimeError, match="owns application tables"):
            await verify_runtime_db_role(db, prod)
    finally:
        await db.dispose()

    app_settings = make_settings(clean_db)
    db = Database(app_settings)
    try:
        await verify_runtime_db_role(db, app_settings.model_copy(update={"env": "production"}))
    finally:
        await db.dispose()


def test_production_refuses_insecure_configuration() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, env="production")  # type: ignore[call-arg]
    message = str(excinfo.value)
    assert "JWT_SECRET uses a development default" in message
    assert "RATE_LIMIT_BACKEND=redis" in message
    assert "EVENT_BUS_BACKEND=redis" in message
    assert "MAIL_BACKEND=smtp" in message

    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(_env_file=None, jwt_secret="short")  # type: ignore[call-arg]

    ok = Settings(  # type: ignore[call-arg]
        _env_file=None,
        env="production",
        jwt_secret="j" * 40,
        pin_hmac_key="h" * 40,
        pin_enc_key="e" * 40,
        rate_limit_backend="redis",
        event_bus_backend="redis",
        redis_url="redis://localhost:6379/0",
        mail_backend="smtp",
        smtp_host="smtp.example.com",
        password_reset_url="https://staff.example.com/reset",
        public_order_base_url="https://order.example.com",
        public_web_origins="https://order.example.com",
        registration_invite_codes="a-long-pilot-invite-code",
    )
    assert ok.is_production

    with pytest.raises(ValidationError, match="REGISTRATION_INVITE_CODES"):
        ok.model_validate({**ok.model_dump(), "registration_invite_codes": ["short"]})


async def test_memory_rate_limiter_windows_and_bounds() -> None:
    limiter = MemoryRateLimiter(max_keys=50)
    limit = Limit("t", 3, 60)
    results = [(await limiter.hit(limit, "k")).allowed for _ in range(4)]
    assert results == [True, True, True, False]
    assert (await limiter.current(limit, "k")).allowed is False
    await limiter.reset(limit, "k")
    assert (await limiter.current(limit, "k")).allowed is True
    for i in range(500):  # flooding with distinct keys stays bounded
        await limiter.hit(limit, f"flood-{i}")
    assert len(limiter._data) <= 50


async def test_audit_refuses_secret_looking_details() -> None:
    for key in ("password", "new_password", "refresh_token", "pin", "pin_code", "token_hash"):
        with pytest.raises(ValueError, match="secret"):
            await audit.record(
                None,  # type: ignore[arg-type]  # rejected before touching the session
                restaurant_id=uuid.uuid4(),
                action="x.y",
                actor_type=ActorType.SYSTEM,
                details={key: "value"},
            )


def test_role_permissions_are_least_privilege() -> None:
    kitchen = ROLE_PERMISSIONS[Role.KITCHEN]
    waiter = ROLE_PERMISSIONS[Role.WAITER]
    admin = ROLE_PERMISSIONS[Role.ADMIN]
    owner = ROLE_PERMISSIONS[Role.OWNER]
    assert Permission.ORDERS_SERVE not in kitchen
    assert Permission.ORDERS_KITCHEN_TRANSITION not in waiter
    assert Permission.PIN_VIEW not in kitchen
    assert not ({Permission.STAFF_MANAGE, Permission.PIN_MANAGE, Permission.AUDIT_READ} & waiter)
    assert Permission.STAFF_MANAGE_ADMINS not in admin
    assert kitchen | waiter <= admin < owner
    assert permissions_for("superadmin") == frozenset()
