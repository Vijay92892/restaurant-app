"""Test infrastructure: a real PostgreSQL 16 (via pgserver) with production-like roles.

rr_owner  owns the schema and runs migrations (like the deploy pipeline)
rr_app    runtime role: LOGIN, no superuser, no BYPASSRLS, owns nothing (like the API)
rr_probe  unprivileged role, used to prove functions aren't executable by PUBLIC
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import httpx
import pgserver
import pytest
from alembic.config import Config

from alembic import command
from app.core.config import Settings
from app.core.pin import PinCrypto
from app.core.security import passwords
from app.db.session import Database, bind_tenant
from app.main import create_app
from app.models import User
from app.services.restaurant_service import create_restaurant_with_owner

BACKEND_DIR = Path(__file__).resolve().parents[1]
PASSWORD = "Correct-Horse-42"
INVITE_CODE = "pilot-invite-2026-abcdef"
CLIENT_IP = "203.0.113.10"

ALL_TABLES = (
    "restaurants",
    "users",
    "auth_sessions",
    "refresh_tokens",
    "password_reset_tokens",
    "dining_tables",
    "table_qr_tokens",
    "pin_versions",
    "table_sessions",
    "menu_categories",
    "menu_items",
    "menu_modifier_groups",
    "menu_modifiers",
    "menu_item_modifier_groups",
    "orders",
    "order_items",
    "order_item_modifiers",
    "order_events",
    "audit_logs",
    "problem_reports",
    "usage_counters",
)


@dataclass(frozen=True)
class PgTestDatabase:
    port: int
    media_root: Path

    def dsn(self, role: str, database: str = "rr_test") -> str:
        return f"postgresql://{role}@127.0.0.1:{self.port}/{database}"

    def sqlalchemy_url(self, role: str) -> str:
        return f"postgresql+asyncpg://{role}@127.0.0.1:{self.port}/rr_test"


def make_settings(db: PgTestDatabase, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "env": "test",
        "database_url": db.sqlalchemy_url("rr_app"),
        "migration_database_url": db.sqlalchemy_url("rr_owner"),
        "app_db_role": "rr_app",
        "argon2_time_cost": 1,
        "argon2_memory_kib": 1024,
        "argon2_parallelism": 1,
        "mail_backend": "memory",
        "rate_limit_backend": "memory",
        "password_reset_url": "https://staff.example.test/reset",
        "log_level": "WARNING",
        "pin_scheduler_enabled": False,
        "retention_enabled": False,
        "usage_counters_enabled": False,  # tests flush explicitly
        "registration_mode": "invite",
        "registration_invite_codes": [INVITE_CODE],
        "public_order_base_url": "https://order.example.test",
        "media_root": db.media_root,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def _run_migrations(db: PgTestDatabase) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["database_url"] = db.sqlalchemy_url("rr_owner")
    cfg.attributes["app_db_role"] = "rr_app"
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
async def database(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[PgTestDatabase]:
    data_dir = tmp_path_factory.mktemp("pgdata")
    server = await asyncio.to_thread(pgserver.get_server, str(data_dir), cleanup_mode="stop")
    port = urlparse(server.get_uri()).port
    assert port
    db = PgTestDatabase(port=port, media_root=tmp_path_factory.mktemp("media"))

    conn = await asyncpg.connect(db.dsn("postgres", "postgres"))
    try:
        # Throwaway test cluster: durability is irrelevant, and per-test TRUNCATEs are far
        # faster without fsync (dramatically so on Windows). Never do this outside tests.
        for setting in ("fsync", "synchronous_commit", "full_page_writes"):
            await conn.execute(f"ALTER SYSTEM SET {setting} = off")
        await conn.execute("SELECT pg_reload_conf()")
        for statement in (
            "CREATE ROLE rr_owner LOGIN",
            "CREATE ROLE rr_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE",
            "CREATE ROLE rr_probe NOLOGIN",
            "CREATE DATABASE rr_test OWNER rr_owner",
            "REVOKE ALL ON DATABASE rr_test FROM PUBLIC",
            "GRANT CONNECT ON DATABASE rr_test TO rr_owner, rr_app",
        ):
            await conn.execute(statement)
    finally:
        await conn.close()

    conn = await asyncpg.connect(db.dsn("postgres"))
    try:
        await conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        await conn.execute("ALTER SCHEMA public OWNER TO rr_owner")
    finally:
        await conn.close()

    await asyncio.to_thread(_run_migrations, db)
    try:
        yield db
    finally:
        await asyncio.to_thread(server.cleanup)


@pytest.fixture(scope="session")
async def _cleaner(database: PgTestDatabase) -> AsyncIterator[asyncpg.Connection]:
    """Superuser connection that empties every table between tests. Plain DELETEs with
    triggers/FK checks disabled are much faster than TRUNCATE for tiny tables."""
    conn = await asyncpg.connect(database.dsn("postgres"))
    await conn.execute("SET session_replication_role = replica")
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def clean_db(database: PgTestDatabase, _cleaner: asyncpg.Connection) -> PgTestDatabase:
    # Table names come from the ALL_TABLES constant, never from input.
    await _cleaner.execute("; ".join(f"DELETE FROM {table}" for table in ALL_TABLES))  # noqa: S608
    return database


@pytest.fixture
async def owner_conn(clean_db: PgTestDatabase) -> AsyncIterator[asyncpg.Connection]:
    """Schema-owner connection: bypasses RLS. For seeding and inspecting only."""
    conn = await asyncpg.connect(clean_db.dsn("rr_owner"))
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def app_conn(clean_db: PgTestDatabase) -> AsyncIterator[asyncpg.Connection]:
    """Runtime-role connection: subject to RLS and grants, exactly like the API."""
    conn = await asyncpg.connect(clean_db.dsn("rr_app"))
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def session_settings(database: PgTestDatabase) -> Settings:
    return make_settings(database)


@pytest.fixture
def settings(clean_db: PgTestDatabase, session_settings: Settings) -> Settings:
    return session_settings


@pytest.fixture(scope="session")
async def _shared_app(session_settings: Settings) -> AsyncIterator[object]:
    """Building the app is the most expensive part of a test, so build it once and reset its
    per-test state (rate-limit counters, test mailbox, password hasher) before every test.
    Tests needing different settings create their own app."""
    application = create_app(session_settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def app(_shared_app: object, clean_db: PgTestDatabase) -> object:
    state = _shared_app.state  # type: ignore[attr-defined]
    state.rate_limiter.clear()
    state.mailer.outbox.clear()
    state.usage.drain()
    passwords.configure(state.settings)
    return _shared_app


@pytest.fixture
async def client(app: object) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, client=(CLIENT_IP, 50000))  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
        yield c


@pytest.fixture
async def client_from(app: object) -> AsyncIterator[Callable[[str], httpx.AsyncClient]]:
    """Factory for clients connecting from other IP addresses (separate cookie jars)."""
    clients: list[httpx.AsyncClient] = []

    def make(ip: str) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=app, client=(ip, 50000))  # type: ignore[arg-type]
        new = httpx.AsyncClient(transport=transport, base_url="https://testserver")
        clients.append(new)
        return new

    yield make
    for c in clients:
        await c.aclose()


@dataclass(frozen=True)
class SeededRestaurant:
    id: uuid.UUID
    code: str
    owner_id: uuid.UUID
    owner_email: str


class Seeder:
    def __init__(self, settings: Settings, db: Database | None = None) -> None:
        self.db = db or Database(settings)
        self._owns_db = db is None
        self.crypto = PinCrypto(settings)

    async def restaurant(self, code: str = "alpha", name: str | None = None) -> SeededRestaurant:
        email = f"owner@{code}.example"
        async with self.db.sessionmaker() as session:
            restaurant, owner = await create_restaurant_with_owner(
                session,
                self.crypto,
                code=code,
                name=name or f"{code.title()} Kitchen",
                owner_username="owner",
                owner_display_name="Owner",
                owner_email=email,
                owner_password=PASSWORD,
            )
            await session.commit()
        return SeededRestaurant(restaurant.id, code, owner.id, email)

    async def user(
        self,
        restaurant: SeededRestaurant,
        role: str,
        username: str,
        *,
        password: str = PASSWORD,
        email: str | None = None,
        is_active: bool = True,
        must_change_password: bool = False,
    ) -> uuid.UUID:
        async with self.db.sessionmaker() as session:
            await bind_tenant(session, restaurant.id)
            user = User(
                id=uuid.uuid4(),
                restaurant_id=restaurant.id,
                username=username,
                email=email,
                display_name=username.title(),
                role=role,
                password_hash=await passwords.hash(password),
                is_active=is_active,
                must_change_password=must_change_password,
            )
            session.add(user)
            await session.commit()
            return user.id

    async def close(self) -> None:
        if self._owns_db:
            await self.db.dispose()


@pytest.fixture
async def seed(settings: Settings, app: object) -> AsyncIterator[Seeder]:
    seeder = Seeder(settings, app.state.db)  # type: ignore[attr-defined]
    try:
        yield seeder
    finally:
        await seeder.close()


async def login(
    client: httpx.AsyncClient, code: str, username: str, password: str = PASSWORD
) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/login",
        json={"restaurant_code": code, "username": username, "password": password},
    )


async def login_ok(
    client: httpx.AsyncClient, code: str, username: str, password: str = PASSWORD
) -> dict:
    response = await login(client, code, username, password)
    assert response.status_code == 200, response.text
    return response.json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def staff_headers(
    client: httpx.AsyncClient, code: str, username: str, password: str = PASSWORD
) -> dict[str, str]:
    return bearer((await login_ok(client, code, username, password))["access_token"])


async def create_table(client: httpx.AsyncClient, headers: dict[str, str], label: str) -> dict:
    response = await client.post("/api/v1/tables", headers=headers, json={"label": label})
    assert response.status_code == 201, response.text
    body = response.json()
    body["qr_token"] = body["qr_url"].rsplit("/", 1)[1]
    return body


async def current_pin(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    response = await client.get("/api/v1/pin", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["pin"]


def wrong_pin(pin: str) -> str:
    return "".join(str((int(d) + 1) % 10) for d in pin)


async def open_table_session(client: httpx.AsyncClient, qr_token: str, pin: str) -> httpx.Response:
    return await client.post(f"/api/v1/public/tables/{qr_token}/session", json={"pin": pin})
