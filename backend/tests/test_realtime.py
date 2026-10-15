"""Phase 8: live staff events over WebSocket, the event bus, and the kitchen board."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import asyncpg
import fakeredis
import httpx
import pytest

from app.core.rate_limit import Limit, Limits
from app.realtime import outbox
from app.realtime.bus import LocalEventBus, RedisEventBus, Subscription, channel_for
from tests.conftest import (
    CLIENT_IP,
    Seeder,
    create_table,
    current_pin,
    login_ok,
    open_table_session,
    staff_headers,
)
from tests.test_menu import M, build_menu

WS_PATH = "/api/v1/ws/staff"
ORDERS = "/api/v1/orders"
BOARD = "/api/v1/kitchen/board"


def key() -> str:
    return uuid.uuid4().hex


class Closed(Exception):  # noqa: N818 - mirrors a client library's "disconnected" error
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


class StaffSocket:
    """Minimal in-process WebSocket client speaking ASGI directly (pure asyncio)."""

    def __init__(self, app: object, path: str, headers: dict[str, str]) -> None:
        self._to_app: asyncio.Queue[dict] = asyncio.Queue()
        self._from_app: asyncio.Queue[dict] = asyncio.Queue()
        raw_path, _, query = path.partition("?")
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "path": raw_path,
            "raw_path": raw_path.encode(),
            "root_path": "",
            "query_string": query.encode(),
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (CLIENT_IP, 50000),
            "server": ("testserver", 80),
            "subprotocols": [],
        }
        self._task = asyncio.create_task(app(scope, self._to_app.get, self._from_app.put))  # type: ignore[operator]
        self.closed_code: int | None = None

    async def _next(self, wait: float) -> dict:
        if self.closed_code is not None:
            raise Closed(self.closed_code)
        message = await asyncio.wait_for(self._from_app.get(), wait)
        if message["type"] == "websocket.close":
            self.closed_code = message.get("code", 1000)
            raise Closed(self.closed_code)
        return message

    async def open(self) -> None:
        await self._to_app.put({"type": "websocket.connect"})
        message = await self._next(3)
        assert message["type"] == "websocket.accept", message

    async def send_text(self, text: str) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": text})

    async def send_json(self, value: object) -> None:
        await self.send_text(json.dumps(value))

    async def receive_text(self, timeout: float = 3.0) -> str:  # noqa: ASYNC109
        message = await self._next(timeout)
        assert message["type"] == "websocket.send", message
        return message["text"]

    async def disconnect(self) -> None:
        if self.closed_code is None:
            await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(self._task, 3)


Connect = Callable[..., AbstractAsyncContextManager[StaffSocket]]


@pytest.fixture
def tuned(app: object) -> Iterator[Callable[..., None]]:
    """Temporarily change the shared app's settings (e.g. shorter WebSocket timers)."""
    state = app.state  # type: ignore[attr-defined]
    original = state.settings

    def apply(**changes: object) -> None:
        state.settings = original.model_copy(update=changes)

    yield apply
    state.settings = original


@pytest.fixture
def connect(app: object) -> Connect:
    @asynccontextmanager
    async def opened(
        token: str | None = None, headers: dict[str, str] | None = None, path: str = WS_PATH
    ) -> AsyncIterator[StaffSocket]:
        socket = StaffSocket(app, path, headers or {})
        try:
            await socket.open()
            if token is not None:
                await socket.send_json({"type": "auth", "access_token": token})
                ready = await receive(socket)
                assert ready["type"] == "ready", ready
            yield socket
        finally:
            await socket.disconnect()

    return opened


async def receive(socket: StaffSocket, wait: float = 3.0) -> dict:
    return json.loads(await socket.receive_text(timeout=wait))


async def closed_with(socket: StaffSocket, wait: float = 3.0) -> int:
    """Read until the server closes the socket; return the close code."""
    with pytest.raises(Closed) as closed:
        while True:
            await socket.receive_text(timeout=wait)
    return closed.value.code


async def nothing_more(socket: StaffSocket) -> None:
    with pytest.raises(TimeoutError):
        await socket.receive_text(timeout=0.3)


@pytest.fixture
async def crew(client: httpx.AsyncClient, seed: Seeder) -> dict:
    alpha = await seed.restaurant("alpha")
    await seed.user(alpha, "waiter", "ravi")
    await seed.user(alpha, "kitchen", "chef")
    owner = await staff_headers(client, "alpha", "owner")
    built = await build_menu(client, owner)
    table = await create_table(client, owner, "T4")
    chef = await login_ok(client, "alpha", "chef")
    waiter = await login_ok(client, "alpha", "ravi")
    return {
        "restaurant": alpha,
        "owner": owner,
        "built": built,
        "table": table,
        "chef_token": chef["access_token"],
        "chef_refresh": chef["refresh_token"],
        "chef": {"Authorization": f"Bearer {chef['access_token']}"},
        "waiter_token": waiter["access_token"],
        "waiter": {"Authorization": f"Bearer {waiter['access_token']}"},
    }


def naan_order(crew: dict, quantity: int = 2) -> dict:
    return {
        "table_id": crew["table"]["id"],
        "items": [{"menu_item_id": crew["built"]["naan"]["id"], "quantity": quantity}],
    }


async def place(client: httpx.AsyncClient, crew: dict, quantity: int = 2) -> dict:
    response = await client.post(
        ORDERS,
        json=naan_order(crew, quantity),
        headers={**crew["waiter"], "Idempotency-Key": key()},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def move(client: httpx.AsyncClient, headers: dict, order: dict, to: str) -> httpx.Response:
    return await client.post(
        f"{ORDERS}/{order['id']}/transition",
        headers=headers,
        json={"to_status": to, "expected_version": order["version"]},
    )


# --- connecting and authenticating ---------------------------------------------------------------


async def test_socket_requires_an_auth_message(
    connect: Connect, crew: dict, tuned: Callable[..., None]
) -> None:
    tuned(ws_auth_timeout_seconds=0.2)
    async with connect() as silent:
        assert await closed_with(silent) == 4401
    async with connect() as wrong_shape:
        await wrong_shape.send_json({"type": "ping"})
        assert await closed_with(wrong_shape) == 4401
    async with connect() as not_json:
        await not_json.send_text("hello")
        assert await closed_with(not_json) == 4401
    for bad_token in ("", "not-a-jwt", crew["chef_refresh"], 42):
        async with connect() as socket:
            await socket.send_json({"type": "auth", "access_token": bad_token})
            assert await closed_with(socket) == 4401


async def test_token_in_the_url_is_not_accepted(
    connect: Connect, crew: dict, tuned: Callable[..., None]
) -> None:
    tuned(ws_auth_timeout_seconds=0.2)
    path = f"{WS_PATH}?access_token={crew['chef_token']}"
    async with connect(headers=crew["chef"], path=path) as socket:
        assert await closed_with(socket) == 4401


async def test_users_who_must_change_password_cannot_subscribe(
    client: httpx.AsyncClient, connect: Connect, crew: dict, seed: Seeder
) -> None:
    await seed.user(crew["restaurant"], "kitchen", "newbie", must_change_password=True)
    token = (await login_ok(client, "alpha", "newbie"))["access_token"]
    async with connect() as socket:
        await socket.send_json({"type": "auth", "access_token": token})
        assert await closed_with(socket) == 4401


async def test_browser_origins_must_be_allowed(
    connect: Connect, crew: dict, tuned: Callable[..., None]
) -> None:
    with pytest.raises(Closed) as refused:
        async with connect(headers={"Origin": "https://evil.example"}):
            pass
    assert refused.value.code == 4403

    tuned(cors_origins=["https://staff.example.test"])
    async with connect(crew["chef_token"], headers={"Origin": "https://staff.example.test"}) as ok:
        await ok.send_json({"type": "ping"})
        assert (await receive(ok))["type"] == "pong"


async def test_connections_per_user_are_capped(
    connect: Connect, crew: dict, tuned: Callable[..., None]
) -> None:
    tuned(ws_max_connections_per_user=2)
    async with connect(crew["chef_token"]), connect(crew["chef_token"]):
        async with connect() as third:
            await third.send_json({"type": "auth", "access_token": crew["chef_token"]})
            assert await closed_with(third) == 4429
        async with connect(crew["waiter_token"]):  # the cap is per person
            pass
    async with connect(crew["chef_token"]):  # closed sockets free their slots
        pass


async def test_message_floods_are_cut_off(connect: Connect, crew: dict) -> None:
    async with connect(crew["chef_token"]) as socket:
        for _ in range(40):
            await socket.send_json({"type": "nonsense"})
        assert await closed_with(socket) == 4429
    async with connect(crew["chef_token"]) as socket:
        await socket.send_text("x" * 10_000)
        assert await closed_with(socket) == 1009


# --- events ------------------------------------------------------------------------------------


async def test_kitchen_sees_new_orders_and_status_changes_live(
    client: httpx.AsyncClient, connect: Connect, crew: dict
) -> None:
    async with connect(crew["chef_token"]) as kitchen:
        order = await place(client, crew)
        created = await receive(kitchen)
        assert created["type"] == "order.created"
        assert created["order_id"] == order["id"] and created["order_version"] == 1
        assert created["restaurant_id"] == str(crew["restaurant"].id)
        assert created["payload"]["order"] == order  # same shape as the REST staff view
        assert created["event_id"] and created["occurred_at"]

        accepted = (await move(client, crew["chef"], order, "accepted")).json()
        updated = await receive(kitchen)
        assert updated["type"] == "order.updated" and updated["order_version"] == 2
        assert updated["payload"]["order"]["status"] == "accepted"
        assert updated["payload"]["order"] == accepted
        await nothing_more(kitchen)


async def test_customer_orders_and_cancellations_reach_staff(
    client: httpx.AsyncClient,
    client_from: Callable[[str], httpx.AsyncClient],
    connect: Connect,
    crew: dict,
) -> None:
    diner = client_from("198.51.100.7")
    pin = await current_pin(client, crew["owner"])
    assert (await open_table_session(diner, crew["table"]["qr_token"], pin)).status_code == 200
    async with connect(crew["chef_token"]) as kitchen:
        response = await diner.post(
            "/api/v1/public/orders",
            json={"items": [{"menu_item_id": crew["built"]["naan"]["id"], "quantity": 1}]},
            headers={"Idempotency-Key": key()},
        )
        assert response.status_code == 201, response.text
        event = await receive(kitchen)
        assert event["type"] == "order.created"
        assert event["payload"]["order"]["source"] == "customer"
        assert event["payload"]["order"]["table_label"] == "T4"

        cancelled = await diner.post(
            f"/api/v1/public/orders/{response.json()['id']}/cancel", json={}
        )
        assert cancelled.status_code == 200, cancelled.text
        event = await receive(kitchen)
        assert event["type"] == "order.updated"
        assert event["payload"]["order"]["status"] == "cancelled"


async def test_events_never_cross_restaurants(
    client: httpx.AsyncClient, connect: Connect, crew: dict, seed: Seeder
) -> None:
    bravo = await seed.restaurant("bravo")
    await seed.user(bravo, "kitchen", "chef")
    bravo_chef = (await login_ok(client, "bravo", "chef"))["access_token"]
    bravo_owner = await staff_headers(client, "bravo", "owner")
    bravo_menu = await build_menu(client, bravo_owner)
    bravo_table = await create_table(client, bravo_owner, "B1")

    async with connect(bravo_chef) as bravo_kitchen, connect(crew["chef_token"]) as alpha_kitchen:
        await place(client, crew)
        await client.post(f"/api/v1/tables/{crew['table']['id']}/close", headers=crew["owner"])
        await client.post("/api/v1/pin/rotate", headers=crew["owner"], json={})
        assert (await receive(alpha_kitchen))["type"] == "order.created"
        await nothing_more(bravo_kitchen)

        response = await client.post(
            ORDERS,
            json={
                "table_id": bravo_table["id"],
                "items": [{"menu_item_id": bravo_menu["naan"]["id"], "quantity": 1}],
            },
            headers={**bravo_owner, "Idempotency-Key": key()},
        )
        assert response.status_code == 201
        event = await receive(bravo_kitchen)
        assert event["restaurant_id"] == str(bravo.id)
        seen = [(await receive(alpha_kitchen))["type"] for _ in range(2)]
        assert seen == ["table.closed", "pin.rotated"]
        await nothing_more(alpha_kitchen)


async def test_failed_changes_publish_nothing(
    client: httpx.AsyncClient, connect: Connect, crew: dict
) -> None:
    order = await place(client, crew)
    async with connect(crew["chef_token"]) as kitchen:
        stale = {**order, "version": 7}
        assert (await move(client, crew["chef"], stale, "accepted")).status_code == 409
        assert (await move(client, crew["waiter"], order, "accepted")).status_code == 403
        assert (await move(client, crew["chef"], order, "served")).status_code == 409
        await nothing_more(kitchen)


async def test_menu_and_pin_events_carry_no_secrets(
    client: httpx.AsyncClient, connect: Connect, crew: dict
) -> None:
    async with connect(crew["chef_token"]) as kitchen:
        naan = crew["built"]["naan"]["id"]
        sold_out = await client.patch(
            f"{M}/items/{naan}/availability", headers=crew["chef"], json={"is_available": False}
        )
        assert sold_out.status_code == 200
        event = await receive(kitchen)
        assert event["type"] == "menu.availability_changed"
        assert event["payload"] == {"menu_item_id": naan, "is_available": False}

        # A manager hiding the dish (and showing it again) reaches staff screens too; editing
        # anything else about the dish sends nothing.
        for shown in (False, True):
            hidden = await client.patch(
                f"{M}/items/{naan}", headers=crew["owner"], json={"is_active": shown}
            )
            assert hidden.status_code == 200, hidden.text
            event = await receive(kitchen)
            assert event["type"] == "menu.availability_changed"
            assert event["payload"] == {"menu_item_id": naan, "is_active": shown}
        renamed = await client.patch(
            f"{M}/items/{naan}", headers=crew["owner"], json={"name": "Butter Naan (2)"}
        )
        assert renamed.status_code == 200, renamed.text

        rotated = await client.post("/api/v1/pin/rotate", headers=crew["owner"], json={})
        assert rotated.status_code == 200, rotated.text
        new_pin = await current_pin(client, crew["owner"])
        raw = await kitchen.receive_text(timeout=3)
        event = json.loads(raw)
        assert event["type"] == "pin.rotated" and event["payload"] == {"version": 2}
        assert new_pin not in raw.replace(event["event_id"], "").replace(
            event["occurred_at"], ""
        ).replace(event["restaurant_id"], "")


async def test_pin_guessing_alerts_staff(
    client: httpx.AsyncClient,
    client_from: Callable[[str], httpx.AsyncClient],
    connect: Connect,
    crew: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Limits, "PIN_FAILURES_PER_RESTAURANT", Limit("pin_fail_restaurant", 3, 60))
    pin = await current_pin(client, crew["owner"])
    wrong = "".join(str((int(d) + 1) % 10) for d in pin)
    async with connect(crew["chef_token"]) as kitchen:
        for i in range(3):
            guesser = client_from(f"198.51.100.{i + 1}")
            await open_table_session(guesser, crew["table"]["qr_token"], wrong)
        event = await receive(kitchen)
        assert event["type"] == "pin.bruteforce_suspected"
        assert event["payload"] == {"table_id": crew["table"]["id"]}
        await open_table_session(client_from("198.51.100.9"), crew["table"]["qr_token"], wrong)
        await nothing_more(kitchen)  # one alert per window, not one per guess


async def test_ping_and_reauthentication(
    client: httpx.AsyncClient, connect: Connect, crew: dict
) -> None:
    async with connect(crew["chef_token"]) as socket:
        await socket.send_json({"type": "ping"})
        assert await receive(socket) == {"type": "pong"}

        fresh = (await login_ok(client, "alpha", "chef"))["access_token"]
        await socket.send_json({"type": "auth", "access_token": fresh})
        assert await receive(socket) == {"type": "reauthenticated"}

        await socket.send_json({"type": "auth", "access_token": crew["waiter_token"]})
        assert await closed_with(socket) == 4401


async def test_logout_and_deactivation_close_open_sockets(
    client: httpx.AsyncClient, connect: Connect, crew: dict, tuned: Callable[..., None]
) -> None:
    tuned(ws_recheck_seconds=0.2)
    async with connect(crew["chef_token"]) as socket:
        assert (await client.post("/api/v1/auth/logout", headers=crew["chef"])).status_code == 204
        assert await closed_with(socket) == 4401

    async with connect(crew["waiter_token"]) as socket:
        staff = (await client.get("/api/v1/staff", headers=crew["owner"])).json()
        ravi = next(s for s in staff if s["username"] == "ravi")
        response = await client.patch(
            f"/api/v1/staff/{ravi['id']}", headers=crew["owner"], json={"is_active": False}
        )
        assert response.status_code == 200, response.text
        assert await closed_with(socket) == 4401


async def test_an_expired_token_closes_the_socket(
    connect: Connect, crew: dict, tuned: Callable[..., None], app: object
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.core.security import create_access_token, decode_access_token

    settings = app.state.settings  # type: ignore[attr-defined]
    claims = decode_access_token(settings, crew["chef_token"])
    short, _ = create_access_token(
        settings,
        user_id=claims.user_id,
        restaurant_id=claims.restaurant_id,
        session_id=claims.session_id,
        now=datetime.now(UTC) - timedelta(seconds=settings.access_token_ttl_seconds - 1),
    )
    tuned(ws_recheck_seconds=0.3)
    async with connect(short) as socket:
        assert await closed_with(socket) == 4401


async def test_slow_consumers_are_told_to_resync(app: object, connect: Connect, crew: dict) -> None:
    bus = app.state.bus  # type: ignore[attr-defined]
    async with connect(crew["chef_token"]) as socket:
        subscriptions = bus._subscribers[str(crew["restaurant"].id)]
        assert len(subscriptions) == 1
        next(iter(subscriptions)).force_resync()
        assert await closed_with(socket) == 4408
    assert str(crew["restaurant"].id) not in bus._subscribers  # unsubscribed on close


# --- bus and outbox units ----------------------------------------------------------------------


async def test_subscription_overflow_requests_resync() -> None:
    subscription = Subscription(maxsize=2)
    for n in range(3):
        subscription.deliver({"n": n})
    assert await subscription.next() is None


async def test_local_bus_routes_by_restaurant() -> None:
    bus = LocalEventBus()
    a, b = uuid.uuid4(), uuid.uuid4()
    sub_a, sub_b = bus.subscribe(a), bus.subscribe(b)
    await bus.publish(a, {"restaurant_id": str(a), "n": 1})
    assert await sub_a.next() == {"restaurant_id": str(a), "n": 1}
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(sub_b.next(), 0.05)
    bus.unsubscribe(a, sub_a)
    bus.unsubscribe(b, sub_b)
    assert not bus._subscribers


async def test_outbox_publishes_only_committed_work(app: object, crew: dict) -> None:
    db = app.state.db  # type: ignore[attr-defined]
    bus = LocalEventBus()
    restaurant_id = crew["restaurant"].id
    subscription = bus.subscribe(restaurant_id)

    from app.db.session import bind_tenant

    async with db.sessionmaker() as session:
        await bind_tenant(session, restaurant_id)
        outbox.stage(session, restaurant_id, "test.rolled_back")
        await session.rollback()
        await bind_tenant(session, restaurant_id)
        outbox.stage(session, restaurant_id, "test.committed", payload={"x": 1})
        await session.commit()
        await outbox.publish_released(session, bus)

    event = await subscription.next()
    assert event is not None and event["type"] == "test.committed"
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(subscription.next(), 0.05)


async def test_redis_bus_fans_out_across_workers_per_tenant() -> None:
    server = fakeredis.FakeServer()
    worker_1 = RedisEventBus(fakeredis.FakeAsyncRedis(server=server))
    worker_2 = RedisEventBus(fakeredis.FakeAsyncRedis(server=server))
    await worker_1.start()
    await worker_2.start()
    try:
        a, b = uuid.uuid4(), uuid.uuid4()
        on_2_for_a = worker_2.subscribe(a)
        on_2_for_b = worker_2.subscribe(b)
        event = {"restaurant_id": str(a), "type": "order.created"}
        await worker_1.publish(a, event)
        assert await asyncio.wait_for(on_2_for_a.next(), 2) == event
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(on_2_for_b.next(), 0.1)

        # A message on A's channel claiming to be B's (or garbage) is dropped, not delivered.
        raw = fakeredis.FakeAsyncRedis(server=server)
        await raw.publish(channel_for(a), json.dumps({"restaurant_id": str(b)}))
        await raw.publish(channel_for(b), "not json")
        await raw.aclose()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(on_2_for_b.next(), 0.2)
    finally:
        await worker_1.close()
        await worker_2.close()


# --- kitchen board -------------------------------------------------------------------------------


async def test_kitchen_board_lists_open_kitchen_work_oldest_first(
    client: httpx.AsyncClient, crew: dict, seed: Seeder
) -> None:
    first = await place(client, crew, 1)
    second = await place(client, crew, 2)
    third = await place(client, crew, 3)
    done = await place(client, crew, 4)
    accepted = (await move(client, crew["chef"], second, "accepted")).json()
    ready = (await move(client, crew["chef"], third, "preparing")).json()
    ready = (await move(client, crew["chef"], ready, "ready")).json()
    served = (await move(client, crew["chef"], done, "preparing")).json()
    served = (await move(client, crew["chef"], served, "ready")).json()
    await move(client, crew["waiter"], served, "served")

    response = await client.get(BOARD, headers=crew["chef"])
    assert response.status_code == 200, response.text
    board = response.json()
    assert board["kitchen_delay_minutes"] == 15 and board["truncated"] is False
    assert [o["id"] for o in board["orders"]] == [first["id"], accepted["id"], ready["id"]]
    assert [o["status"] for o in board["orders"]] == ["placed", "accepted", "ready"]
    line = board["orders"][0]["items"][0]
    assert line["menu_item_id"] == crew["built"]["naan"]["id"] and line["quantity"] == 1
    assert "total" not in json.dumps(board) and "price" not in json.dumps(board)

    await seed.restaurant("bravo")
    bravo = await staff_headers(client, "bravo", "owner")
    assert (await client.get(BOARD, headers=bravo)).json()["orders"] == []
    assert (await client.get(BOARD)).status_code == 401


async def test_kitchen_board_reports_recent_cancellations(
    client: httpx.AsyncClient, crew: dict, seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    """A tablet that was offline learns which orders were cancelled (and at which version)."""
    cooking = await place(client, crew, 1)
    cooking = (await move(client, crew["chef"], cooking, "preparing")).json()
    earlier = await place(client, crew, 2)
    for order, code in ((cooking, "kitchen_issue"), (earlier, "duplicate")):
        response = await client.post(
            f"{ORDERS}/{order['id']}/cancel",
            headers=crew["owner"],
            json={"reason_code": code, "expected_version": order["version"]},
        )
        assert response.status_code == 200, response.text
    # Cancelled long ago: no longer interesting to a kitchen.
    await owner_conn.execute(
        "UPDATE orders SET cancelled_at = now() - interval '3 hours' WHERE id = $1",
        uuid.UUID(earlier["id"]),
    )

    board = (await client.get(BOARD, headers=crew["chef"])).json()
    assert board["orders"] == []
    assert [(c["id"], c["version"]) for c in board["recently_cancelled"]] == [
        (cooking["id"], cooking["version"] + 1)
    ]
    assert set(board["recently_cancelled"][0]) == {"id", "version", "cancelled_at"}

    await seed.restaurant("bravo")
    bravo = await staff_headers(client, "bravo", "owner")
    assert (await client.get(BOARD, headers=bravo)).json()["recently_cancelled"] == []
