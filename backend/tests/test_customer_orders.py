"""Phase 6: customer ordering — validation, idempotency, isolation, cancellation."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import httpx
import pytest

from tests.conftest import Seeder, create_table, current_pin, open_table_session, staff_headers
from tests.test_menu import M, _post, build_menu

ORDERS = "/api/v1/public/orders"


def key() -> str:
    return uuid.uuid4().hex


async def _seat(client: httpx.AsyncClient, owner: dict, label: str = "T4") -> dict:
    table = await create_table(client, owner, label)
    pin = await current_pin(client, owner)
    assert (await open_table_session(client, table["qr_token"], pin)).status_code == 200
    return table


def paneer_line(built: dict, option: int = 1, quantity: int = 2, **extra: object) -> dict:
    return {
        "menu_item_id": built["paneer"]["id"],
        "quantity": quantity,
        "modifier_ids": [built["options"][option]["id"]],
        **extra,
    }


async def place(client: httpx.AsyncClient, body: dict, idem: str | None = None) -> httpx.Response:
    return await client.post(ORDERS, json=body, headers={"Idempotency-Key": idem or key()})


@pytest.fixture
async def setup(client: httpx.AsyncClient, seed: Seeder) -> dict:
    await seed.restaurant("alpha", name="Spice Garden")
    owner = await staff_headers(client, "alpha", "owner")
    built = await build_menu(client, owner)
    table = await _seat(client, owner)
    return {"owner": owner, "built": built, "table": table}


async def test_customer_places_an_order(
    client: httpx.AsyncClient, setup: dict, owner_conn: asyncpg.Connection
) -> None:
    built = setup["built"]
    body = {
        "items": [
            paneer_line(built, notes="less oil please"),
            {"menu_item_id": built["naan"]["id"], "quantity": 4},
        ],
        "notes": "Birthday table",
    }
    response = await place(client, body)
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["order_number"] == 1 and order["status"] == "placed" and order["version"] == 1
    assert [(i["name"], i["quantity"]) for i in order["items"]] == [
        ("Paneer Tikka", 2),
        ("Butter Naan", 4),
    ]
    assert order["items"][0]["modifiers"] == ["Spice level: Spicy"]
    assert order["items"][0]["notes"] == "less oil please"
    # No money anywhere in an order.
    assert not any(k for k in str(order).lower().split("'") if "price" in k or "total" in k)

    row = await owner_conn.fetchrow("SELECT * FROM orders")
    assert str(row["table_id"]) == setup["table"]["id"]
    assert row["source"] == "customer" and row["table_session_id"] is not None
    assert await owner_conn.fetchval("SELECT count(*) FROM order_items") == 2
    assert await owner_conn.fetchval("SELECT type FROM order_events") == "order.created"

    second = await place(client, {"items": [{"menu_item_id": built["naan"]["id"], "quantity": 1}]})
    assert second.json()["order_number"] == 2


async def test_retries_and_double_taps_never_duplicate(
    client: httpx.AsyncClient, setup: dict, owner_conn: asyncpg.Connection
) -> None:
    body = {"items": [paneer_line(setup["built"])]}
    idem = key()
    first = await place(client, body, idem)
    retry = await place(client, body, idem)
    assert first.status_code == 201 and retry.status_code == 200
    assert retry.json()["id"] == first.json()["id"]

    reused = await place(client, {"items": [paneer_line(setup["built"], quantity=9)]}, idem)
    assert reused.status_code == 422 and reused.json()["error"]["code"] == "idempotency_mismatch"

    # Five identical taps arriving at the same instant: exactly one order.
    burst_key = key()
    responses = await asyncio.gather(*(place(client, body, burst_key) for _ in range(5)))
    assert sorted(r.status_code for r in responses) == [200, 200, 200, 200, 201]
    assert len({r.json()["id"] for r in responses}) == 1
    assert await owner_conn.fetchval("SELECT count(*) FROM orders") == 2


@pytest.mark.parametrize("bad_key", [None, "short", "has spaces in it!", "x" * 65])
async def test_idempotency_key_is_required_and_validated(
    client: httpx.AsyncClient, setup: dict, bad_key: str | None
) -> None:
    headers = {} if bad_key is None else {"Idempotency-Key": bad_key}
    response = await client.post(
        ORDERS, json={"items": [paneer_line(setup["built"])]}, headers=headers
    )
    assert response.status_code == 422


async def test_menu_rules_are_enforced_server_side(
    client: httpx.AsyncClient, setup: dict, seed: Seeder
) -> None:
    built, owner = setup["built"], setup["owner"]
    await seed.restaurant("bravo")
    bravo = await staff_headers(client, "bravo", "owner")
    foreign = await build_menu(client, bravo)
    hidden = await _post(
        client,
        owner,
        "/items",
        {"category_id": built["mains"]["id"], "name": "Hidden", "is_active": False},
    )
    off_cat = await _post(client, owner, "/categories", {"name": "Closed", "is_active": False})
    in_off_cat = await _post(
        client, owner, "/items", {"category_id": off_cat["id"], "name": "Ghost"}
    )
    gone = await _post(
        client, owner, "/items", {"category_id": built["mains"]["id"], "name": "Gone"}
    )
    await client.delete(f"{M}/items/{gone['id']}", headers=owner)

    def line(item_id: str, modifiers: list[str] | None = None, quantity: int = 1) -> dict:
        return {
            "items": [
                {"menu_item_id": item_id, "quantity": quantity, "modifier_ids": modifiers or []}
            ]
        }

    spice, cheese = built["options"], built["cheese"]["id"]
    cases = [
        (line(str(uuid.uuid4())), 422, "item_not_on_menu"),
        (line(foreign["naan"]["id"]), 422, "item_not_on_menu"),  # another restaurant's dish
        (line(hidden["id"]), 422, "item_not_on_menu"),
        (line(in_off_cat["id"]), 422, "item_not_on_menu"),
        (line(gone["id"]), 422, "item_not_on_menu"),
        (line(built["paneer"]["id"]), 422, "invalid_choice"),  # required spice level missing
        (line(built["paneer"]["id"], [spice[0]["id"], spice[1]["id"]]), 422, "invalid_choice"),
        (line(built["naan"]["id"], [cheese]), 422, "invalid_option"),  # option not offered for naan
        (line(built["paneer"]["id"], [foreign["options"][0]["id"]]), 422, "invalid_option"),
    ]
    for body, status, code in cases:
        response = await place(client, body)
        assert (response.status_code, response.json()["error"]["code"]) == (status, code), body

    await client.patch(
        f"{M}/modifiers/{spice[2]['id']}/availability", headers=owner, json={"is_available": False}
    )
    off_option = await place(client, line(built["paneer"]["id"], [spice[2]["id"]]))
    assert (
        off_option.status_code == 409 and off_option.json()["error"]["code"] == "option_unavailable"
    )

    await client.patch(
        f"{M}/items/{built['naan']['id']}/availability", headers=owner, json={"is_available": False}
    )
    sold_out = await place(client, line(built["naan"]["id"]))
    assert sold_out.status_code == 409
    assert sold_out.json()["error"]["code"] == "item_unavailable"
    assert sold_out.json()["error"]["menu_item_id"] == built["naan"]["id"]
    assert "Butter Naan" in sold_out.json()["error"]["message"]


@pytest.mark.parametrize(
    "body",
    [
        {"items": []},
        {"items": [{"menu_item_id": "{naan}", "quantity": 0}]},
        {"items": [{"menu_item_id": "{naan}", "quantity": 51}]},
        {"items": [{"menu_item_id": "{naan}", "quantity": "2"}]},
        {"items": [{"menu_item_id": "{naan}", "quantity": 2.5}]},
        {"items": [{"menu_item_id": "{naan}", "quantity": -3}]},
        {
            "items": [
                {"menu_item_id": "{naan}", "quantity": 50},
                {"menu_item_id": "{naan}", "quantity": 51},
            ]
        },
        {"items": [{"menu_item_id": "{naan}", "quantity": 1}] * 31},
        {"items": [{"menu_item_id": "{naan}", "quantity": 1, "notes": "x" * 201}]},
        {"items": [{"menu_item_id": "{naan}", "quantity": 1}], "notes": "x" * 501},
        {"items": [{"menu_item_id": "{naan}", "quantity": 1, "price": 1}]},
        {"items": [{"menu_item_id": "{naan}", "quantity": 1}], "table_id": "{naan}"},
        {"items": [{"menu_item_id": "{naan}", "quantity": 1}], "restaurant_id": "{naan}"},
        {"items": [{"menu_item_id": "{naan}", "quantity": 1}], "status": "ready"},
        {
            "items": [
                {"menu_item_id": "{naan}", "quantity": 1, "modifier_ids": ["{naan}", "{naan}"]}
            ]
        },
        {"items": [{"menu_item_id": "not-a-uuid", "quantity": 1}]},
    ],
)
async def test_quantity_and_shape_manipulation_is_rejected(
    client: httpx.AsyncClient, setup: dict, owner_conn: asyncpg.Connection, body: dict
) -> None:
    import json

    raw = json.dumps(body).replace("{naan}", setup["built"]["naan"]["id"])
    response = await client.post(
        ORDERS, content=raw, headers={"Idempotency-Key": key(), "Content-Type": "application/json"}
    )
    assert response.status_code == 422, raw[:120]
    assert await owner_conn.fetchval("SELECT count(*) FROM orders") == 0


async def test_order_needs_a_live_session(
    client: httpx.AsyncClient,
    setup: dict,
    client_from,  # noqa: ANN001
) -> None:
    body = {"items": [paneer_line(setup["built"])]}
    stranger = client_from("198.51.100.90")  # has the QR (could have photographed it), no PIN
    response = await place(stranger, body)
    assert (
        response.status_code == 401 and response.json()["error"]["code"] == "table_session_required"
    )

    closed = await client.post(
        f"/api/v1/tables/{setup['table']['id']}/close", headers=setup["owner"]
    )
    assert closed.status_code == 200
    assert (await place(client, body)).status_code == 401


async def test_chosen_options_keep_menu_order(client: httpx.AsyncClient, setup: dict) -> None:
    built = setup["built"]  # paneer offers "Spice level" then "Add-ons"
    line = paneer_line(built)
    line["modifier_ids"] = [built["cheese"]["id"], built["options"][2]["id"]]
    order = (await place(client, {"items": [line]})).json()
    assert order["items"][0]["modifiers"] == ["Spice level: Extra Spicy", "Add-ons: Extra Cheese"]


async def test_names_are_snapshotted_at_order_time(client: httpx.AsyncClient, setup: dict) -> None:
    built, owner = setup["built"], setup["owner"]
    await place(client, {"items": [paneer_line(built)]})
    await client.patch(
        f"{M}/items/{built['paneer']['id']}", headers=owner, json={"name": "Renamed"}
    )
    await client.patch(
        f"{M}/modifiers/{built['options'][1]['id']}", headers=owner, json={"name": "Hot"}
    )
    orders = (await client.get(ORDERS)).json()
    assert orders[0]["items"][0]["name"] == "Paneer Tikka"
    assert orders[0]["items"][0]["modifiers"] == ["Spice level: Spicy"]


async def test_diners_see_only_their_own_sessions_orders(
    client: httpx.AsyncClient,
    setup: dict,
    client_from,  # noqa: ANN001
) -> None:
    built = setup["built"]
    friend = client_from("198.51.100.91")  # same table, own phone, own session
    pin = await current_pin(client, setup["owner"])
    await open_table_session(friend, setup["table"]["qr_token"], pin)
    mine = (await place(client, {"items": [paneer_line(built)]})).json()
    theirs = (
        await place(friend, {"items": [{"menu_item_id": built["naan"]["id"], "quantity": 1}]})
    ).json()

    assert [o["id"] for o in (await client.get(ORDERS)).json()] == [mine["id"]]
    assert [o["id"] for o in (await friend.get(ORDERS)).json()] == [theirs["id"]]
    # And nobody can cancel someone else's order by guessing its id.
    stolen = await client.post(f"{ORDERS}/{theirs['id']}/cancel", json={})
    assert stolen.status_code == 404


async def test_customer_cancellation_rules(
    client: httpx.AsyncClient, setup: dict, owner_conn: asyncpg.Connection
) -> None:
    built = setup["built"]
    placed = (await place(client, {"items": [paneer_line(built)]})).json()
    cancelled = await client.post(
        f"{ORDERS}/{placed['id']}/cancel", json={"reason": "Ordered twice"}
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled" and cancelled.json()["version"] == 2

    accepted = (await place(client, {"items": [paneer_line(built)]})).json()
    await owner_conn.execute(
        "UPDATE orders SET status = 'accepted' WHERE id = $1", uuid.UUID(accepted["id"])
    )
    requested = await client.post(f"{ORDERS}/{accepted['id']}/cancel", json={})
    assert requested.status_code == 200
    assert requested.json()["status"] == "accepted" and requested.json()["cancel_requested"] is True

    cooking = (await place(client, {"items": [paneer_line(built)]})).json()
    await owner_conn.execute(
        "UPDATE orders SET status = 'preparing' WHERE id = $1", uuid.UUID(cooking["id"])
    )
    too_late = await client.post(f"{ORDERS}/{cooking['id']}/cancel", json={})
    assert too_late.status_code == 409 and too_late.json()["error"]["code"] == "order_too_late"

    events = [
        r["type"] for r in await owner_conn.fetch("SELECT type FROM order_events ORDER BY seq")
    ]
    assert events.count("order.cancelled") == 1 and events.count("order.cancel_requested") == 1
    audits = {r["action"] for r in await owner_conn.fetch("SELECT action FROM audit_logs")}
    assert {"order.cancelled", "order.cancel_requested"} <= audits


async def test_order_spam_is_rate_limited(client: httpx.AsyncClient, setup: dict) -> None:
    body = {"items": [{"menu_item_id": setup["built"]["naan"]["id"], "quantity": 1}]}
    statuses = [(await place(client, body)).status_code for _ in range(11)]
    assert statuses == [201] * 10 + [429]


async def test_rejected_attempts_do_not_use_up_the_order_allowance(
    client: httpx.AsyncClient, setup: dict
) -> None:
    unknown = {"items": [{"menu_item_id": str(uuid.uuid4()), "quantity": 1}]}
    for _ in range(12):
        assert (await place(client, unknown)).status_code == 422
    good = {"items": [{"menu_item_id": setup["built"]["naan"]["id"], "quantity": 1}]}
    assert (await place(client, good)).status_code == 201
    # ...but endless junk is still capped.
    statuses = [(await place(client, unknown)).status_code for _ in range(20)]
    assert 429 in statuses


async def test_order_list_supports_conditional_polling(
    client: httpx.AsyncClient, setup: dict
) -> None:
    await place(client, {"items": [paneer_line(setup["built"])]})
    first = await client.get(ORDERS)
    assert (
        await client.get(ORDERS, headers={"If-None-Match": first.headers["etag"]})
    ).status_code == 304
