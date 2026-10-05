"""Phase 5: what diners see — only their own restaurant's live menu, compact and cacheable."""

from __future__ import annotations

import httpx

from tests.conftest import (
    Seeder,
    create_table,
    current_pin,
    open_table_session,
    staff_headers,
)
from tests.test_menu import M, _post, build_menu


async def _seat(client: httpx.AsyncClient, owner: dict, label: str = "T1") -> None:
    table = await create_table(client, owner, label)
    response = await open_table_session(client, table["qr_token"], await current_pin(client, owner))
    assert response.status_code == 200


async def test_menu_requires_a_table_session(client: httpx.AsyncClient, seed: Seeder) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    await build_menu(client, owner)
    response = await client.get("/api/v1/public/menu")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "table_session_required"


async def test_diners_see_only_live_items_with_options(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    await seed.restaurant("alpha", name="Spice Garden")
    owner = await staff_headers(client, "alpha", "owner")
    built = await build_menu(client, owner)
    hidden_category = await _post(
        client, owner, "/categories", {"name": "Staff only", "is_active": False}
    )
    await _post(
        client, owner, "/items", {"category_id": hidden_category["id"], "name": "Secret Thali"}
    )
    await _post(
        client,
        owner,
        "/items",
        {"category_id": built["starters"]["id"], "name": "Off-menu Soup", "is_active": False},
    )
    deleted = await _post(
        client, owner, "/items", {"category_id": built["mains"]["id"], "name": "Gone"}
    )
    await client.delete(f"{M}/items/{deleted['id']}", headers=owner)
    unused_group = await _post(client, owner, "/modifier-groups", {"name": "Unused"})
    await client.patch(
        f"{M}/items/{built['naan']['id']}/availability", headers=owner, json={"is_available": False}
    )
    await _seat(client, owner)

    menu = (await client.get("/api/v1/public/menu")).json()
    assert menu["restaurant_name"] == "Spice Garden" and menu["table_label"] == "T1"
    names = [item["name"] for c in menu["categories"] for item in c["items"]]
    assert names == ["Paneer Tikka", "Butter Naan"]
    assert [c["name"] for c in menu["categories"]] == ["Starters", "Mains"]

    paneer, naan = (item for c in menu["categories"] for item in c["items"])
    assert paneer["orderable"] is True and paneer["display_price_paise"] == 24900
    assert naan["is_available"] is False and naan["orderable"] is False  # sold out, still shown
    groups = {g["id"]: g for g in menu["modifier_groups"]}
    assert set(groups) == {built["spice"]["id"], built["addons"]["id"]}  # unused group omitted
    assert unused_group["id"] not in groups
    assert [o["name"] for o in groups[built["spice"]["id"]]["options"]] == [
        "Regular",
        "Spicy",
        "Extra Spicy",
    ]
    # Staff-only fields are not exposed to diners.
    for key in ("is_active", "sort_order", "category_id"):
        assert key not in paneer


async def test_item_is_not_orderable_when_a_required_choice_is_impossible(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    built = await build_menu(client, owner)  # paneer requires exactly one "Spice level"
    for option in built["options"]:
        await client.patch(
            f"{M}/modifiers/{option['id']}/availability",
            headers=owner,
            json={"is_available": False},
        )
    await _seat(client, owner)

    menu = (await client.get("/api/v1/public/menu")).json()
    paneer = menu["categories"][0]["items"][0]
    assert paneer["is_available"] is True and paneer["orderable"] is False

    await client.patch(
        f"{M}/modifiers/{built['options'][0]['id']}/availability",
        headers=owner,
        json={"is_available": True},
    )
    menu = (await client.get("/api/v1/public/menu")).json()
    assert menu["categories"][0]["items"][0]["orderable"] is True


async def test_each_session_sees_only_its_own_restaurant(
    client: httpx.AsyncClient,
    client_from,
    seed: Seeder,  # noqa: ANN001
) -> None:
    await seed.restaurant("alpha", name="Alpha Kitchen")
    await seed.restaurant("bravo", name="Bravo Bistro")
    alpha = await staff_headers(client, "alpha", "owner")
    bravo = await staff_headers(client, "bravo", "owner")
    await build_menu(client, alpha)
    bravo_cat = await _post(client, bravo, "/categories", {"name": "Bravo Specials"})
    await _post(client, bravo, "/items", {"category_id": bravo_cat["id"], "name": "Bravo Burger"})

    diner = client_from("198.51.100.60")
    await _seat(diner, bravo)
    menu = (await diner.get("/api/v1/public/menu")).json()
    assert menu["restaurant_name"] == "Bravo Bistro"
    assert [item["name"] for c in menu["categories"] for item in c["items"]] == ["Bravo Burger"]
    # There is no parameter a diner could use to ask for another restaurant's menu.
    spoofed = await diner.get(
        "/api/v1/public/menu", params={"restaurant_id": "x", "restaurant": "alpha"}
    )
    assert spoofed.json() == menu


async def test_menu_is_cacheable_with_etag(client: httpx.AsyncClient, seed: Seeder) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    built = await build_menu(client, owner)
    await _seat(client, owner)

    first = await client.get("/api/v1/public/menu")
    etag = first.headers["etag"]
    assert first.headers["cache-control"] == "private, no-cache"
    again = await client.get("/api/v1/public/menu", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""

    await client.patch(
        f"{M}/items/{built['paneer']['id']}", headers=owner, json={"name": "Paneer Tikka Masala"}
    )
    changed = await client.get("/api/v1/public/menu", headers={"If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["etag"] != etag


async def test_menu_text_is_returned_as_plain_text(client: httpx.AsyncClient, seed: Seeder) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    category = await _post(client, owner, "/categories", {"name": "Chef's <Specials>"})
    await _post(
        client,
        owner,
        "/items",
        {
            "category_id": category["id"],
            "name": "<img src=x onerror=alert(1)>",
            "description": "Don't make it too spicy & <b>bold</b>",
        },
    )
    await _seat(client, owner)
    response = await client.get("/api/v1/public/menu")
    assert response.headers["content-type"] == "application/json"
    item = response.json()["categories"][0]["items"][0]
    # Stored and returned verbatim as data; the customer web renders it with textContent.
    assert item["name"] == "<img src=x onerror=alert(1)>"
    assert item["description"] == "Don't make it too spicy & <b>bold</b>"
