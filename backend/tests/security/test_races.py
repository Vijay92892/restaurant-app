"""Attacks that only work when requests arrive at the same time.

Every failure budget (wrong PINs, wrong passwords, orders per table session) must hold under a
burst of simultaneous requests, and simultaneous duplicates must never create two of anything.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable

import asyncpg
import fakeredis
import httpx
import pytest

from app.core.errors import RateLimited
from app.core.rate_limit import (
    Budget,
    Limit,
    Limits,
    MemoryRateLimiter,
    RateLimiter,
    RedisRateLimiter,
)
from tests.conftest import (
    PASSWORD,
    Seeder,
    create_table,
    current_pin,
    login,
    login_ok,
    open_table_session,
    staff_headers,
    wrong_pin,
)
from tests.test_menu import build_menu

ORDERS = "/api/v1/orders"
PUBLIC_ORDERS = "/api/v1/public/orders"

ClientFactory = Callable[[str], httpx.AsyncClient]


async def burst(*calls: Awaitable[httpx.Response]) -> Counter[int]:
    responses = await asyncio.gather(*calls)
    return Counter(r.status_code for r in responses)


# --- failure budgets ------------------------------------------------------------------------------


async def test_simultaneous_pin_guesses_from_one_ip_stay_within_its_budget(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    table = await create_table(client, owner, "T1")
    guess = wrong_pin(await current_pin(client, owner))

    statuses = await burst(
        *(open_table_session(client, table["qr_token"], guess) for _ in range(30))
    )
    assert statuses[401] <= Limits.PIN_FAILURES_PER_IP.max, statuses
    assert statuses[401] + statuses[429] == 30, statuses


async def test_simultaneous_pin_guesses_from_many_ips_stay_within_the_tables_budget(
    client: httpx.AsyncClient, client_from: ClientFactory, seed: Seeder
) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    table = await create_table(client, owner, "T1")
    pin = await current_pin(client, owner)
    guess = wrong_pin(pin)
    attackers = [client_from(f"203.0.113.{i}") for i in range(15)]

    statuses = await burst(
        *(open_table_session(a, table["qr_token"], guess) for a in attackers for _ in range(3))
    )
    assert statuses[401] <= Limits.PIN_FAILURES_PER_TABLE.max, statuses
    # The table is now locked for this PIN, even for someone who knows it.
    legit = client_from("198.51.100.7")
    assert (await open_table_session(legit, table["qr_token"], pin)).status_code == 429


async def test_simultaneous_password_guesses_stay_within_the_accounts_budget(
    client: httpx.AsyncClient, client_from: ClientFactory, seed: Seeder
) -> None:
    await seed.restaurant("alpha")
    attackers = [client_from(f"203.0.113.{i}") for i in range(3)]

    statuses = await burst(
        *(
            login(a, "alpha", "owner", f"Wrong-Guess-{i}-{n}")
            for i, a in enumerate(attackers)
            for n in range(10)
        )
    )
    assert statuses[401] <= Limits.LOGIN_FAILURES_PER_ACCOUNT.max, statuses
    # Locked: even the right password is refused until the window passes.
    assert (await login(client, "alpha", "owner", PASSWORD)).status_code == 429


async def test_simultaneous_wrong_current_passwords_stay_within_the_users_budget(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    """A stolen access token cannot be used to brute-force the account's password."""
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")

    statuses = await burst(
        *(
            client.post(
                "/api/v1/auth/change-password",
                headers=owner,
                json={"current_password": f"Wrong-Guess-{n}", "new_password": "New-Pass-2026-x"},
            )
            for n in range(20)
        )
    )
    wrong = statuses[400] + statuses[422]
    assert wrong <= Limits.CHANGE_PASSWORD_FAILURES_PER_USER.max, statuses


async def test_simultaneous_orders_stay_within_the_sessions_cap(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    built = await build_menu(client, owner)
    table = await create_table(client, owner, "T1")
    pin = await current_pin(client, owner)
    assert (await open_table_session(client, table["qr_token"], pin)).status_code == 200

    def order(n: int) -> Awaitable[httpx.Response]:
        return client.post(
            PUBLIC_ORDERS,
            json={"items": [{"menu_item_id": built["naan"]["id"], "quantity": 1}]},
            headers={"Idempotency-Key": f"burst-order-{n:04d}"},
        )

    statuses = await burst(*(order(n) for n in range(25)))
    assert statuses[201] <= Limits.ORDERS_CREATED_PER_SESSION.max, statuses
    assert statuses[201] + statuses[429] == 25, statuses


# --- duplicates -----------------------------------------------------------------------------------


async def test_simultaneous_retries_of_one_order_create_exactly_one(
    client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    alpha = await seed.restaurant("alpha")
    await seed.user(alpha, "waiter", "ravi")
    owner = await staff_headers(client, "alpha", "owner")
    built = await build_menu(client, owner)
    table = await create_table(client, owner, "T1")
    waiter = await staff_headers(client, "alpha", "ravi")
    pin = await current_pin(client, owner)
    assert (await open_table_session(client, table["qr_token"], pin)).status_code == 200
    lines = {"items": [{"menu_item_id": built["naan"]["id"], "quantity": 2}]}

    # Below the session's order allowance (each in-flight request reserves a slot until it
    # knows it is a retry), every retry gets the one original order back.
    customer = await asyncio.gather(
        *(
            client.post(PUBLIC_ORDERS, json=lines, headers={"Idempotency-Key": "same-key-0001"})
            for _ in range(8)
        )
    )
    staff = await asyncio.gather(
        *(
            client.post(
                ORDERS,
                json={"table_id": table["id"], **lines},
                headers={**waiter, "Idempotency-Key": "same-key-0002"},
            )
            for _ in range(15)
        )
    )
    for responses in (customer, staff):
        assert {r.status_code for r in responses} <= {200, 201}, [r.text for r in responses]
        assert len({r.json()["id"] for r in responses}) == 1
    assert await owner_conn.fetchval("SELECT count(*) FROM orders") == 2
    assert await owner_conn.fetchval("SELECT count(*) FROM order_items") == 2


async def test_simultaneous_status_changes_apply_once(
    client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    alpha = await seed.restaurant("alpha")
    await seed.user(alpha, "waiter", "ravi")
    await seed.user(alpha, "kitchen", "chef")
    owner = await staff_headers(client, "alpha", "owner")
    built = await build_menu(client, owner)
    table = await create_table(client, owner, "T1")
    waiter = await staff_headers(client, "alpha", "ravi")
    chef = await staff_headers(client, "alpha", "chef")
    placed = await client.post(
        ORDERS,
        json={
            "table_id": table["id"],
            "items": [{"menu_item_id": built["naan"]["id"], "quantity": 1}],
        },
        headers={**waiter, "Idempotency-Key": "race-order-01"},
    )
    order = placed.json()

    statuses = await burst(
        *(
            client.post(
                f"{ORDERS}/{order['id']}/transition",
                headers=chef,
                json={"to_status": "accepted", "expected_version": order["version"]},
            )
            for _ in range(10)
        ),
        *(
            client.post(
                f"{ORDERS}/{order['id']}/cancel",
                headers=owner,
                json={"reason_code": "duplicate", "expected_version": order["version"]},
            )
            for _ in range(10)
        ),
    )
    assert statuses[200] == 1 and statuses[409] == 19, statuses
    row = await owner_conn.fetchrow(
        "SELECT version, status FROM orders WHERE id = $1", uuid.UUID(order["id"])
    )
    assert row["version"] == order["version"] + 1
    events = await owner_conn.fetchval(
        "SELECT count(*) FROM order_events WHERE order_id = $1", uuid.UUID(order["id"])
    )
    assert events == 2  # created + exactly one change


async def test_simultaneous_unique_names_create_one(
    client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")

    tables = await burst(
        *(client.post("/api/v1/tables", headers=owner, json={"label": "Patio"}) for _ in range(10))
    )
    staff = await burst(
        *(
            client.post(
                "/api/v1/staff",
                headers=owner,
                json={
                    "username": "twin",
                    "display_name": "Twin",
                    "role": "waiter",
                    "temporary_password": "Temp-Pass-2026-a",
                },
            )
            for _ in range(10)
        )
    )
    categories = await burst(
        *(
            client.post("/api/v1/menu/categories", headers=owner, json={"name": "Soups"})
            for _ in range(10)
        )
    )
    for statuses in (tables, staff, categories):
        assert statuses[201] == 1 and statuses[409] == 9, statuses


async def test_simultaneous_pin_rotations_leave_exactly_one_current_pin(
    client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    table = await create_table(client, owner, "T1")

    rotations = await burst(
        *(client.post("/api/v1/pin/rotate", headers=owner, json={}) for _ in range(8))
    )
    regenerations = await burst(
        *(
            client.post(f"/api/v1/tables/{table['id']}/qr/regenerate", headers=owner, json={})
            for _ in range(8)
        )
    )
    assert set(rotations) <= {200, 409} and set(regenerations) <= {200, 409}
    assert (
        await owner_conn.fetchval("SELECT count(*) FROM pin_versions WHERE superseded_at IS NULL")
        == 1
    )
    assert (
        await owner_conn.fetchval("SELECT count(*) FROM table_qr_tokens WHERE revoked_at IS NULL")
        == 1
    )


async def test_simultaneous_refreshes_leave_one_usable_refresh_token(
    client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    await seed.restaurant("alpha")
    tokens = await login_ok(client, "alpha", "owner")

    responses = await asyncio.gather(
        *(
            client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
            for _ in range(10)
        )
    )
    assert {r.status_code for r in responses} <= {200, 401}
    live = await owner_conn.fetchval(
        "SELECT count(*) FROM refresh_tokens WHERE rotated_at IS NULL AND revoked_at IS NULL"
    )
    assert live <= 1


# --- the budget primitive, on both backends -------------------------------------------------------


@pytest.mark.parametrize("backend", ["memory", "redis"])
async def test_budgets_are_taken_atomically_and_refunds_never_go_below_zero(backend: str) -> None:
    if backend == "memory":
        limiter: RateLimiter = MemoryRateLimiter()
    else:
        limiter = RedisRateLimiter("redis://unused")
        limiter._redis = fakeredis.FakeAsyncRedis()  # type: ignore[attr-defined]
    limit = Limit("t", 3, 60)

    async def attempt() -> bool:
        budget = Budget(limiter)
        try:
            await budget.take(limit, "k")
        except RateLimited:
            return False
        await asyncio.sleep(0)  # the check would happen here
        return True

    assert sum(await asyncio.gather(*(attempt() for _ in range(10)))) == 3

    other = Limit("other", 5, 60)
    budget = Budget(limiter)
    await budget.take(other, "k")
    with pytest.raises(RateLimited):  # over budget: what this attempt took is given back
        await budget.take(limit, "k")
    assert (await limiter.current(other, "k")).count == 0

    for _ in range(3):
        await limiter.refund(other, "never-taken")
    assert (await limiter.current(other, "never-taken")).count == 0
    await limiter.close()
