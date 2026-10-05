"""Phase 11: daily usage counts per restaurant (QR opened, PIN accepted/wrong/locked, staff
connections). Counts only — nothing that identifies a diner, a device or a network."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable

import asyncpg
import httpx

from app.core.usage import Metric, UsageCounters
from app.services import usage_service
from tests.conftest import Seeder, current_pin, open_table_session, wrong_pin
from tests.test_realtime import Connect, closed_with

TABLES = "/api/v1/public/tables"


async def counts(conn: asyncpg.Connection, restaurant_id: uuid.UUID) -> dict[str, int]:
    rows = await conn.fetch(
        "SELECT metric, sum(count)::int AS n FROM usage_counters WHERE restaurant_id = $1 "
        "GROUP BY metric",
        restaurant_id,
    )
    return {r["metric"]: r["n"] for r in rows}


async def test_the_customer_journey_is_counted_for_its_restaurant(
    app: object,
    client: httpx.AsyncClient,
    crew: dict,
    seed: Seeder,
    owner_conn: asyncpg.Connection,
) -> None:
    state = app.state  # type: ignore[attr-defined]
    alpha = crew["restaurant"].id
    bravo = await seed.restaurant("bravo")
    qr = crew["table"]["qr_token"]
    pin = await current_pin(client, crew["owner"])

    for _ in range(2):
        assert (await client.get(f"{TABLES}/{qr}")).status_code == 200
    assert (await client.get(f"{TABLES}/{'x' * 22}")).status_code == 404  # unknown: not counted
    assert (await open_table_session(client, qr, wrong_pin(pin))).status_code != 200
    assert (await open_table_session(client, qr, pin)).status_code == 200

    assert await usage_service.flush(state.db, state.usage) == 3
    assert await counts(owner_conn, alpha) == {"qr_opened": 2, "pin_wrong": 1, "pin_accepted": 1}
    assert await counts(owner_conn, bravo.id) == {}

    # Later flushes add to the same day's counts.
    assert (await client.get(f"{TABLES}/{qr}")).status_code == 200
    await usage_service.flush(state.db, state.usage)
    assert (await counts(owner_conn, alpha))["qr_opened"] == 3
    days = await owner_conn.fetchval(
        "SELECT count(DISTINCT day) FROM usage_counters WHERE restaurant_id = $1", alpha
    )
    assert days == 1


async def test_pin_lockouts_are_counted(
    app: object,
    client: httpx.AsyncClient,
    client_from: Callable[[str], httpx.AsyncClient],
    crew: dict,
) -> None:
    state = app.state  # type: ignore[attr-defined]
    qr = crew["table"]["qr_token"]
    bad = wrong_pin(await current_pin(client, crew["owner"]))
    # Ten wrong guesses for one table, from ten addresses (each under its own per-IP limit)...
    for n in range(10):
        response = await open_table_session(client_from(f"203.0.113.{n + 1}"), qr, bad)
        assert response.status_code not in (200, 429), response.status_code
    # ...and the table's guessing limit refuses the next one, from anywhere.
    locked = await open_table_session(client_from("203.0.113.99"), qr, bad)
    assert locked.status_code == 429
    rid = crew["restaurant"].id
    assert state.usage.pending(rid, Metric.PIN_WRONG) == 10
    assert state.usage.pending(rid, Metric.PIN_LOCKED) == 1


async def test_staff_connections_and_dropped_links_are_counted(
    app: object, connect: Connect, crew: dict
) -> None:
    usage = app.state.usage  # type: ignore[attr-defined]
    rid = crew["restaurant"].id
    async with connect(crew["chef_token"]):
        pass  # the app closes normally (1000): not a dropped link
    async with connect(crew["chef_token"]) as socket:
        # The connection vanishes without a close frame (Wi-Fi gone).
        await socket._to_app.put({"type": "websocket.disconnect", "code": 1006})
        await asyncio.wait_for(socket._task, 3)
    async with connect(crew["chef_token"]) as socket:
        bus = app.state.bus  # type: ignore[attr-defined]
        next(iter(bus._subscribers[str(rid)])).force_resync()
        assert await closed_with(socket) == 4408
    async with connect() as socket:  # never authenticated: belongs to no restaurant
        await socket.send_json({"type": "hello"})
        assert await closed_with(socket) == 4401

    assert usage.pending(rid, Metric.STAFF_CONNECTED) == 3
    assert usage.pending(rid, Metric.STAFF_CONNECTION_LOST) == 1
    assert usage.pending(rid, Metric.STAFF_RESYNC) == 1


async def test_counts_for_unknown_restaurants_are_dropped_quietly(app: object) -> None:
    state = app.state  # type: ignore[attr-defined]
    counters = UsageCounters()
    counters.add(uuid.uuid4(), Metric.QR_OPENED)
    await usage_service.flush(state.db, counters)  # writes nothing, raises nothing
    assert counters.drain() == {}


def test_pending_counts_are_bounded() -> None:
    counters = UsageCounters()
    counters.MAX_KEYS = 3
    ids = [uuid.uuid4() for _ in range(5)]
    for rid in ids:
        counters.add(rid, Metric.QR_OPENED)
    counters.add(ids[0], Metric.QR_OPENED, 2)  # an existing key still counts
    pending = counters.drain()
    assert len(pending) == 3
    assert pending[(ids[0], Metric.QR_OPENED)] == 3
