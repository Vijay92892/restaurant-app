"""Phase 7: staff order API — waiter ordering, the state machine, cancellations, isolation."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import httpx
import pytest

from tests.conftest import Seeder, create_table, staff_headers
from tests.test_menu import M, build_menu

ORDERS = "/api/v1/orders"


def key() -> str:
    return uuid.uuid4().hex


@pytest.fixture
async def crew(client: httpx.AsyncClient, seed: Seeder) -> dict:
    alpha = await seed.restaurant("alpha")
    for role, name in (
        ("admin", "manager"),
        ("waiter", "ravi"),
        ("waiter", "sita"),
        ("kitchen", "chef"),
    ):
        await seed.user(alpha, role, name)
    owner = await staff_headers(client, "alpha", "owner")
    built = await build_menu(client, owner)
    table = await create_table(client, owner, "T4")
    return {
        "restaurant": alpha,
        "owner": owner,
        "admin": await staff_headers(client, "alpha", "manager"),
        "waiter": await staff_headers(client, "alpha", "ravi"),
        "waiter2": await staff_headers(client, "alpha", "sita"),
        "kitchen": await staff_headers(client, "alpha", "chef"),
        "built": built,
        "table": table,
    }


def naan_order(crew: dict, quantity: int = 2) -> dict:
    return {
        "table_id": crew["table"]["id"],
        "items": [{"menu_item_id": crew["built"]["naan"]["id"], "quantity": quantity}],
    }


async def place(
    client: httpx.AsyncClient, headers: dict, body: dict, idem: str | None = None
) -> httpx.Response:
    return await client.post(
        ORDERS, json=body, headers={**headers, "Idempotency-Key": idem or key()}
    )


async def move(client: httpx.AsyncClient, headers: dict, order: dict, to: str) -> httpx.Response:
    return await client.post(
        f"{ORDERS}/{order['id']}/transition",
        headers=headers,
        json={"to_status": to, "expected_version": order["version"]},
    )


async def test_waiter_orders_for_a_table_without_a_pin(
    client: httpx.AsyncClient, crew: dict, owner_conn: asyncpg.Connection
) -> None:
    response = await place(client, crew["waiter"], naan_order(crew))
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["source"] == "waiter" and order["placed_by"] == "Ravi"
    assert order["table_label"] == "T4" and order["status"] == "placed"
    row = await owner_conn.fetchrow("SELECT table_session_id, placed_by_user_id FROM orders")
    assert row["table_session_id"] is None and row["placed_by_user_id"] is not None


async def test_staff_idempotency_is_per_person(client: httpx.AsyncClient, crew: dict) -> None:
    idem = key()
    first = await place(client, crew["waiter"], naan_order(crew), idem)
    retry = await place(client, crew["waiter"], naan_order(crew), idem)
    assert (first.status_code, retry.status_code) == (201, 200)
    assert first.json()["id"] == retry.json()["id"]
    other_waiter = await place(client, crew["waiter2"], naan_order(crew), idem)
    assert other_waiter.status_code == 201 and other_waiter.json()["id"] != first.json()["id"]


async def test_who_may_order_and_for_which_table(
    client: httpx.AsyncClient, crew: dict, seed: Seeder
) -> None:
    assert (await place(client, crew["kitchen"], naan_order(crew))).status_code == 403
    unknown = {**naan_order(crew), "table_id": str(uuid.uuid4())}
    assert (await place(client, crew["waiter"], unknown)).status_code == 404

    await seed.restaurant("bravo")
    bravo_owner = await staff_headers(client, "bravo", "owner")
    bravo_table = await create_table(client, bravo_owner, "B1")
    foreign = {**naan_order(crew), "table_id": bravo_table["id"]}
    assert (await place(client, crew["waiter"], foreign)).status_code == 404

    await client.patch(
        f"/api/v1/tables/{crew['table']['id']}", headers=crew["owner"], json={"is_active": False}
    )
    off = await place(client, crew["waiter"], naan_order(crew))
    assert off.status_code == 422 and off.json()["error"]["code"] == "table_unavailable"


async def test_full_lifecycle_with_the_right_roles(
    client: httpx.AsyncClient, crew: dict, owner_conn: asyncpg.Connection
) -> None:
    order = (await place(client, crew["waiter"], naan_order(crew))).json()

    assert (await move(client, crew["waiter"], order, "accepted")).status_code == 403
    order = (await move(client, crew["kitchen"], order, "accepted")).json()
    order = (await move(client, crew["kitchen"], order, "preparing")).json()
    order = (await move(client, crew["kitchen"], order, "ready")).json()
    assert order["ready_at"] is not None
    assert (await move(client, crew["kitchen"], order, "served")).status_code == 403
    order = (await move(client, crew["waiter"], order, "served")).json()
    order = (await move(client, crew["waiter"], order, "completed")).json()
    assert order["status"] == "completed" and order["version"] == 6

    row = await owner_conn.fetchrow(
        "SELECT accepted_at, preparing_at, ready_at, served_at, completed_at FROM orders"
    )
    assert all(row[k] is not None for k in row.keys())
    events = [
        r["to_status"]
        for r in await owner_conn.fetch("SELECT to_status FROM order_events ORDER BY seq")
    ]
    assert events == ["placed", "accepted", "preparing", "ready", "served", "completed"]
    assert (
        await owner_conn.fetchval(
            "SELECT count(*) FROM audit_logs WHERE action = 'order.status_changed'"
        )
        == 5
    )


async def test_kitchen_may_start_without_accepting(
    client: httpx.AsyncClient, crew: dict, owner_conn: asyncpg.Connection
) -> None:
    order = (await place(client, crew["waiter"], naan_order(crew))).json()
    started = await move(client, crew["kitchen"], order, "preparing")
    assert started.status_code == 200
    assert await owner_conn.fetchval("SELECT accepted_at IS NOT NULL FROM orders")


@pytest.mark.parametrize(
    ("steps", "illegal"),
    [
        ([], "ready"),  # skipping the kitchen
        ([], "served"),
        ([("kitchen", "preparing"), ("kitchen", "ready")], "preparing"),  # going backwards
        (
            [
                ("kitchen", "preparing"),
                ("kitchen", "ready"),
                ("waiter", "served"),
                ("waiter", "completed"),
            ],
            "served",
        ),
    ],
)
async def test_illegal_jumps_are_refused(
    client: httpx.AsyncClient, crew: dict, steps: list[tuple[str, str]], illegal: str
) -> None:
    order = (await place(client, crew["waiter"], naan_order(crew))).json()
    for role, to in steps:
        order = (await move(client, crew[role], order, to)).json()
    response = await move(client, crew["owner"], order, illegal)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_transition"


async def test_stale_versions_and_races_never_clobber(
    client: httpx.AsyncClient, crew: dict
) -> None:
    order = (await place(client, crew["waiter"], naan_order(crew))).json()
    stale = dict(order)
    await move(client, crew["kitchen"], order, "accepted")
    conflict = await move(client, crew["kitchen"], stale, "preparing")
    assert conflict.status_code == 409
    body = conflict.json()["error"]
    assert body["code"] == "version_conflict" and body["current_version"] == 2

    # Two tablets tap "ready" for the same order at the same instant: exactly one wins.
    current = (await client.get(f"{ORDERS}/{order['id']}", headers=crew["kitchen"])).json()
    current = (await move(client, crew["kitchen"], current, "preparing")).json()
    results = await asyncio.gather(
        move(client, crew["kitchen"], current, "ready"),
        move(client, crew["admin"], current, "ready"),
    )
    assert sorted(r.status_code for r in results) == [200, 409]


async def _cancel(
    client: httpx.AsyncClient, headers: dict, order: dict, code: str = "customer_changed_mind"
) -> httpx.Response:
    return await client.post(
        f"{ORDERS}/{order['id']}/cancel",
        headers=headers,
        json={
            "reason_code": code,
            "reason": "Guest changed their mind",
            "expected_version": order["version"],
        },
    )


async def test_cancellation_policy_by_role_and_stage(
    client: httpx.AsyncClient, crew: dict, owner_conn: asyncpg.Connection
) -> None:
    placed = (await place(client, crew["waiter"], naan_order(crew))).json()
    cancelled = await _cancel(client, crew["waiter"], placed)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancel_reason_code"] == "customer_changed_mind"
    again = await _cancel(client, crew["waiter"], cancelled.json())
    assert again.status_code == 409

    cooking = (await place(client, crew["waiter"], naan_order(crew))).json()
    cooking = (await move(client, crew["kitchen"], cooking, "preparing")).json()
    refused = await _cancel(client, crew["waiter"], cooking)
    assert (
        refused.status_code == 403 and refused.json()["error"]["code"] == "cancel_requires_approval"
    )
    kitchen = await _cancel(client, crew["kitchen"], cooking, "kitchen_issue")
    assert (
        kitchen.status_code == 403 and kitchen.json()["error"]["code"] == "cancel_requires_approval"
    )

    # The restaurant can allow waiters to cancel orders being prepared.
    await client.patch(
        "/api/v1/restaurant", headers=crew["owner"], json={"waiter_can_cancel_preparing": True}
    )
    assert (await _cancel(client, crew["waiter"], cooking)).status_code == 200

    ready = (await place(client, crew["waiter"], naan_order(crew))).json()
    ready = (await move(client, crew["kitchen"], ready, "preparing")).json()
    ready = (await move(client, crew["kitchen"], ready, "ready")).json()
    assert (await _cancel(client, crew["waiter"], ready)).status_code == 403
    assert (await _cancel(client, crew["admin"], ready, "other")).status_code == 200

    assert (
        await owner_conn.fetchval(
            "SELECT count(*) FROM audit_logs WHERE action = 'order.cancelled'"
        )
        == 3
    )


async def test_cancellation_requests_need_a_manager(client: httpx.AsyncClient, crew: dict) -> None:
    order = (await place(client, crew["waiter"], naan_order(crew))).json()
    order = (await move(client, crew["kitchen"], order, "preparing")).json()
    path = f"{ORDERS}/{order['id']}/cancellation-request"

    assert (await client.post(f"{path}/approve", headers=crew["admin"], json={})).status_code == 409
    flagged = await client.post(path, headers=crew["kitchen"], json={"reason": "Out of paneer"})
    assert flagged.status_code == 200
    assert flagged.json()["cancel_requested"] is True
    assert flagged.json()["cancel_request_reason"] == "Out of paneer"
    assert flagged.json()["status"] == "preparing"  # the kitchen is not stopped by a request
    assert (
        await client.post(f"{path}/approve", headers=crew["waiter"], json={})
    ).status_code == 403

    rejected = await client.post(
        f"{path}/reject", headers=crew["admin"], json={"note": "Use cottage cheese"}
    )
    assert rejected.status_code == 200 and rejected.json()["cancel_requested"] is False

    await client.post(path, headers=crew["waiter"], json={"reason": "Guest left"})
    approved = await client.post(f"{path}/approve", headers=crew["owner"], json={})
    assert approved.status_code == 200 and approved.json()["status"] == "cancelled"
    assert approved.json()["cancel_reason"] == "Guest left"


async def test_closing_a_table_completes_served_orders(
    client: httpx.AsyncClient, crew: dict
) -> None:
    served = (await place(client, crew["waiter"], naan_order(crew))).json()
    for role, to in (("kitchen", "preparing"), ("kitchen", "ready"), ("waiter", "served")):
        served = (await move(client, crew[role], served, to)).json()
    ready = (await place(client, crew["waiter"], naan_order(crew))).json()
    for role, to in (("kitchen", "preparing"), ("kitchen", "ready")):
        ready = (await move(client, crew[role], ready, to)).json()

    tables = (await client.get("/api/v1/tables", headers=crew["waiter"])).json()
    assert (tables[0]["open_orders"], tables[0]["ready_orders"]) == (2, 1)

    closed = await client.post(
        f"/api/v1/tables/{crew['table']['id']}/close", headers=crew["waiter"]
    )
    assert closed.json() == {"sessions_ended": 0, "orders_completed": 1}
    statuses = {
        o["id"]: o["status"] for o in (await client.get(ORDERS, headers=crew["waiter"])).json()
    }
    assert statuses == {served["id"]: "completed", ready["id"]: "ready"}


async def test_listing_filters_and_isolation(
    client: httpx.AsyncClient, crew: dict, seed: Seeder
) -> None:
    first = (await place(client, crew["waiter"], naan_order(crew))).json()
    second = (await place(client, crew["waiter"], naan_order(crew))).json()
    await move(client, crew["kitchen"], second, "accepted")

    listed = (await client.get(ORDERS, headers=crew["kitchen"])).json()
    assert [o["id"] for o in listed] == [second["id"], first["id"]]  # newest first
    only_placed = (
        await client.get(ORDERS, headers=crew["kitchen"], params={"status": "placed"})
    ).json()
    assert [o["id"] for o in only_placed] == [first["id"]]
    both = (
        await client.get(
            ORDERS, headers=crew["kitchen"], params=[("status", "placed"), ("status", "accepted")]
        )
    ).json()
    assert len(both) == 2
    assert (
        await client.get(ORDERS, headers=crew["kitchen"], params={"status": "paid"})
    ).status_code == 422

    await seed.restaurant("bravo")
    bravo = await staff_headers(client, "bravo", "owner")
    assert (await client.get(ORDERS, headers=bravo)).json() == []
    for method, path, body in (
        ("GET", f"{ORDERS}/{first['id']}", None),
        (
            "POST",
            f"{ORDERS}/{first['id']}/transition",
            {"to_status": "accepted", "expected_version": 1},
        ),
        ("POST", f"{ORDERS}/{first['id']}/cancel", {"reason_code": "other", "expected_version": 1}),
        ("POST", f"{ORDERS}/{first['id']}/cancellation-request", {"reason": "x"}),
    ):
        response = await client.request(method, path, headers=bravo, json=body)
        assert response.status_code == 404, (method, path)


async def test_ordering_menu_for_waiters(client: httpx.AsyncClient, crew: dict) -> None:
    await client.patch(
        f"{M}/items/{crew['built']['naan']['id']}/availability",
        headers=crew["owner"],
        json={"is_available": False},
    )
    menu = (await client.get(f"{M}/ordering", headers=crew["waiter"])).json()
    items = {i["name"]: i for c in menu["categories"] for i in c["items"]}
    assert items["Butter Naan"]["orderable"] is False and items["Paneer Tikka"]["orderable"] is True
    assert {g["name"] for g in menu["modifier_groups"]} == {"Spice level", "Add-ons"}
    sold_out = await place(client, crew["waiter"], naan_order(crew))
    assert sold_out.status_code == 409 and sold_out.json()["error"]["code"] == "item_unavailable"
