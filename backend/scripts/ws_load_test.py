"""Load test for live updates: many kitchen tablets, many restaurants, a reconnect storm.

    python scripts/ws_load_test.py --restaurants 30 --tablets 10 --orders 10
    python scripts/ws_load_test.py --workers 4 --redis-url redis://127.0.0.1:6379/0

What it does, against a real API (uvicorn on :8020, separate `restaurant_load` database on the
dev Postgres; one worker with the in-process bus, or several workers sharing a real Redis):

1. registers N restaurants over HTTP, each from its own client IP, with two kitchen users;
2. opens T sockets per restaurant (5 per kitchen user, the per-user cap), each authenticating
   in its first message and then loading the board, as the app does;
3. places orders in every restaurant at once and checks that every tablet receives each of its
   restaurant's orders exactly once, and nothing from any other restaurant; reports latency
   from "waiter taps Send" to "event on the tablet";
4. drops every socket at the same moment and reconnects them all at once (a worst case: real
   tablets add jittered backoff), reporting how long until every tablet is live again.

Numbers depend on the machine; the pass/fail checks (no loss, no duplicates, no cross-tenant
delivery, no failed reconnections) do not.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psutil
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

BACKEND = Path(__file__).resolve().parents[1]
API_PORT = 8020
API = f"http://127.0.0.1:{API_PORT}"
WS = f"ws://127.0.0.1:{API_PORT}/api/v1/ws/staff"
DATABASE = "restaurant_load"
PASSWORD = "Load-Test-Pass-2026"


@dataclass
class Tablet:
    restaurant: int
    token: str
    ip: str
    socket: ClientConnection | None = None
    events: list[tuple[float, dict]] = field(default_factory=list)
    reader: asyncio.Task[None] | None = None


@dataclass
class Restaurant:
    index: int
    ip: str
    owner: dict[str, str]
    table_id: str
    item_id: str
    kitchen_tokens: list[str]
    placed: dict[str, float] = field(default_factory=dict)  # order id -> time the POST started


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1))))]


# --- infrastructure -------------------------------------------------------------------------------


def start_api(workers: int, redis_url: str | None) -> subprocess.Popen[bytes]:
    python = sys.executable
    subprocess.run(  # noqa: S603 - fixed arguments
        [python, "scripts/dev_db.py", "--database", DATABASE, "--no-env"], cwd=BACKEND, check=True
    )
    db = f"127.0.0.1:54329/{DATABASE}"
    env = {
        **os.environ,
        "ENV": "development",
        "DATABASE_URL": f"postgresql+asyncpg://restaurant_app@{db}",
        "MIGRATION_DATABASE_URL": f"postgresql+asyncpg://restaurant_owner@{db}",
        "APP_DB_ROLE": "restaurant_app",
        "EVENT_BUS_BACKEND": "redis" if redis_url else "memory",
        "RATE_LIMIT_BACKEND": "redis" if redis_url else "memory",
        **({"REDIS_URL": redis_url} if redis_url else {}),
        "TRUSTED_PROXIES": "127.0.0.1/32",  # each restaurant gets its own client IP
        "REGISTRATION_MODE": "open",
        "PIN_SCHEDULER_ENABLED": "false",
        "RETENTION_ENABLED": "false",
        "LOG_LEVEL": "WARNING",
    }
    return subprocess.Popen(  # noqa: S603 - fixed arguments
        [
            python,
            "-m",
            "uvicorn",
            "app.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(API_PORT),
            "--workers",
            str(workers),
            "--ws-max-size",
            "65536",
            "--log-level",
            "warning",
        ],
        cwd=BACKEND,
        env=env,
    )


def server_memory_mb(process: subprocess.Popen[bytes]) -> float:
    root = psutil.Process(process.pid)
    processes = [root, *root.children(recursive=True)]
    return sum(p.memory_info().rss for p in processes if p.is_running()) / 1_048_576


async def wait_ready(client: httpx.AsyncClient) -> None:
    for _ in range(120):
        try:
            if (await client.get("/readyz")).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.5)
    raise SystemExit("API did not start")


# --- setting up restaurants -----------------------------------------------------------------------


async def make_restaurant(client: httpx.AsyncClient, run: str, index: int) -> Restaurant:
    ip = f"10.{index // 250}.{index % 250}.1"
    via = {"X-Forwarded-For": ip}

    async def call(method: str, path: str, token: str | None = None, **kw: object) -> dict:
        headers = {**via, **({"Authorization": f"Bearer {token}"} if token else {})}
        response = await client.request(method, f"/api/v1{path}", headers=headers, **kw)
        response.raise_for_status()
        return response.json() if response.content else {}

    owner = await call(
        "POST",
        "/restaurants",
        json={
            "restaurant_name": f"Load {index}",
            "restaurant_code": f"load-{run}-{index}",
            "owner_display_name": "Owner",
            "owner_username": "owner",
            "password": PASSWORD,
        },
    )
    token = owner["access_token"]
    kitchen_tokens = []
    for n in (1, 2):
        username = f"chef{n}"
        await call(
            "POST",
            "/staff",
            token,
            json={
                "username": username,
                "display_name": f"Chef {n}",
                "role": "kitchen",
                "temporary_password": f"Temp-Pass-2026-{n}",
            },
        )
        login = await call(
            "POST",
            "/auth/login",
            json={
                "restaurant_code": f"load-{run}-{index}",
                "username": username,
                "password": f"Temp-Pass-2026-{n}",
            },
        )
        await call(
            "POST",
            "/auth/change-password",
            login["access_token"],
            json={
                "current_password": f"Temp-Pass-2026-{n}",
                "new_password": f"Kitchen-Pass-2026-{n}",
            },
        )
        kitchen_tokens.append(login["access_token"])
    table = await call("POST", "/tables", token, json={"label": "T1"})
    category = await call("POST", "/menu/categories", token, json={"name": "Breads"})
    item = await call(
        "POST",
        "/menu/items",
        token,
        json={
            "category_id": category["id"],
            "name": "Butter Naan",
        },
    )
    return Restaurant(
        index=index,
        ip=ip,
        owner={"Authorization": f"Bearer {token}", **via},
        table_id=table["id"],
        item_id=item["id"],
        kitchen_tokens=kitchen_tokens,
    )


# --- tablets --------------------------------------------------------------------------------------


async def read_events(tablet: Tablet) -> None:
    assert tablet.socket is not None
    try:
        async for raw in tablet.socket:
            tablet.events.append((time.perf_counter(), json.loads(raw)))
    except ConnectionClosed:
        pass


async def go_live(tablet: Tablet, client: httpx.AsyncClient) -> float:
    """Connect, authenticate, wait for "ready", load the board. Returns the seconds it took."""
    started = time.perf_counter()
    tablet.socket = await connect(
        WS, additional_headers={"X-Forwarded-For": tablet.ip}, open_timeout=30, max_size=1_048_576
    )
    await tablet.socket.send(json.dumps({"type": "auth", "access_token": tablet.token}))
    ready = json.loads(await asyncio.wait_for(tablet.socket.recv(), 30))
    if ready.get("type") != "ready":
        raise RuntimeError(f"unexpected first message {ready}")
    board = await client.get(
        "/api/v1/kitchen/board",
        headers={"Authorization": f"Bearer {tablet.token}", "X-Forwarded-For": tablet.ip},
    )
    board.raise_for_status()
    tablet.reader = asyncio.create_task(read_events(tablet))
    return time.perf_counter() - started


async def connect_all(
    tablets: list[Tablet], client: httpx.AsyncClient
) -> tuple[list[float], list[str]]:
    results = await asyncio.gather(*(go_live(t, client) for t in tablets), return_exceptions=True)
    times = [r for r in results if isinstance(r, float)]
    errors = []
    for r in results:
        if isinstance(r, InvalidStatus):
            errors.append(f"HTTP {r.response.status_code}")
        elif isinstance(r, ConnectionClosed):
            errors.append(f"closed {r.rcvd.code if r.rcvd else '?'}")
        elif isinstance(r, BaseException):
            errors.append(type(r).__name__)
    return times, errors


async def drop_all(tablets: list[Tablet]) -> None:
    async def drop(tablet: Tablet) -> None:
        if tablet.socket is not None:
            await tablet.socket.close()
        if tablet.reader is not None:
            await tablet.reader
        tablet.socket = tablet.reader = None

    await asyncio.gather(*(drop(t) for t in tablets))


# --- the test -------------------------------------------------------------------------------------


async def run(args: argparse.Namespace) -> dict:
    report: dict[str, object] = {"config": dict(vars(args))}
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=200)
    async with httpx.AsyncClient(base_url=API, timeout=60, limits=limits) as client:
        await wait_ready(client)
        run_id = uuid.uuid4().hex[:6]
        started = time.perf_counter()
        restaurants = []
        for batch in range(0, args.restaurants, 10):
            restaurants += await asyncio.gather(
                *(
                    make_restaurant(client, run_id, i)
                    for i in range(batch, min(batch + 10, args.restaurants))
                )
            )
        report["setup_seconds"] = round(time.perf_counter() - started, 1)
        memory_idle = server_memory_mb(args.server)

        tablets = [
            Tablet(restaurant=r.index, token=r.kitchen_tokens[n % 2], ip=r.ip)
            for r in restaurants
            for n in range(args.tablets)
        ]
        times, errors = await connect_all(tablets, client)
        report["connect"] = {
            "sockets": len(tablets),
            "failed": errors,
            "p50_s": round(statistics.median(times), 3),
            "p95_s": round(percentile(times, 95), 3),
            "max_s": round(max(times), 3),
        }
        memory_live = server_memory_mb(args.server)
        report["server_memory_mb"] = {
            "idle": round(memory_idle, 1),
            "with_sockets": round(memory_live, 1),
            "per_socket_kb": round((memory_live - memory_idle) * 1024 / len(tablets), 1),
        }

        # Orders in every restaurant at once, every `--interval` seconds.
        retries: list[int] = []

        async def place(restaurant: Restaurant) -> None:
            for n in range(args.orders):
                t0 = time.perf_counter()
                # Like the app: a request lost on the way (here, a keep-alive connection the
                # server closed just as it was reused) is retried with the same idempotency key.
                for attempt in range(3):
                    try:
                        response = await client.post(
                            "/api/v1/orders",
                            headers={
                                **restaurant.owner,
                                "Idempotency-Key": f"load-{run_id}-{n:04d}",
                            },
                            json={
                                "table_id": restaurant.table_id,
                                "items": [{"menu_item_id": restaurant.item_id, "quantity": 1}],
                            },
                        )
                        break
                    except httpx.TransportError:
                        retries.append(restaurant.index)
                        if attempt == 2:
                            raise
                response.raise_for_status()
                restaurant.placed[response.json()["id"]] = t0
                await asyncio.sleep(args.interval)

        await asyncio.gather(*(place(r) for r in restaurants))
        await asyncio.sleep(3)  # let the last events arrive

        by_index = {r.index: r for r in restaurants}
        all_orders = {oid: r.index for r in restaurants for oid in r.placed}
        latencies, missing, duplicates, foreign = [], 0, 0, 0
        for tablet in tablets:
            mine = by_index[tablet.restaurant].placed
            seen: dict[str, int] = {}
            for at, event in tablet.events:
                if event.get("type") != "order.created":
                    continue
                order_id = event.get("order_id")
                if all_orders.get(order_id) != tablet.restaurant:
                    foreign += 1
                    continue
                seen[order_id] = seen.get(order_id, 0) + 1
                if seen[order_id] == 1:
                    latencies.append(at - mine[order_id])
            missing += len(set(mine) - set(seen))
            duplicates += sum(count - 1 for count in seen.values())
        report["fan_out"] = {
            "orders": len(all_orders),
            "deliveries": len(latencies),
            "expected_deliveries": len(all_orders) * args.tablets,
            "missing": missing,
            "duplicates": duplicates,
            "other_restaurants": foreign,
            "retried_requests": len(retries),
            **(
                {
                    "latency_p50_ms": round(statistics.median(latencies) * 1000, 1),
                    "latency_p95_ms": round(percentile(latencies, 95) * 1000, 1),
                    "latency_p99_ms": round(percentile(latencies, 99) * 1000, 1),
                    "latency_max_ms": round(max(latencies) * 1000, 1),
                }
                if latencies
                else {}
            ),
        }

        # Reconnect storm: every tablet drops and comes back in the same instant.
        await drop_all(tablets)
        started = time.perf_counter()
        times, errors = await connect_all(tablets, client)
        report["reconnect_storm"] = {
            "sockets": len(tablets),
            "failed": errors,
            "all_live_after_s": round(time.perf_counter() - started, 2),
            "p95_s": round(percentile(times, 95), 3) if times else None,
        }
        await drop_all(tablets)
    report["passed"] = (
        not report["connect"]["failed"]  # type: ignore[index]
        and not report["reconnect_storm"]["failed"]  # type: ignore[index]
        and missing == duplicates == foreign == 0
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--restaurants", type=int, default=30)
    parser.add_argument("--tablets", type=int, default=10, help="sockets per restaurant (≤ 10)")
    parser.add_argument("--orders", type=int, default=10, help="orders per restaurant")
    parser.add_argument("--interval", type=float, default=0.5, help="seconds between orders")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--redis-url", help="a real Redis; required for more than one worker")
    parser.add_argument("--out", type=Path, help="write the report as JSON here")
    args = parser.parse_args()
    if args.tablets > 10:
        parser.error("--tablets: two kitchen users × 5 sockets each (the per-user cap)")
    if args.workers > 1 and not args.redis_url:
        parser.error("several workers share live updates through Redis: pass --redis-url")

    server = start_api(args.workers, args.redis_url)
    args.server = server
    try:
        report = asyncio.run(run(args))
    finally:
        server.terminate()
        server.wait(timeout=30)
    del report["config"]["server"]  # type: ignore[attr-defined]
    report["config"]["out"] = str(args.out) if args.out else None  # type: ignore[index]
    if args.redis_url:  # never write the Redis password into the report
        redacted = re.sub(r"//[^@/]*@", "//***@", args.redis_url)
        report["config"]["redis_url"] = redacted  # type: ignore[index]
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
