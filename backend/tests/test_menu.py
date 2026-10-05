"""Phase 5: menu management — categories, items, option groups, availability, isolation."""

from __future__ import annotations

import asyncpg
import httpx
import pytest

from tests.conftest import Seeder, staff_headers

M = "/api/v1/menu"


async def _post(client: httpx.AsyncClient, headers: dict, path: str, body: dict) -> dict:
    response = await client.post(f"{M}{path}", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def build_menu(client: httpx.AsyncClient, headers: dict) -> dict:
    starters = await _post(client, headers, "/categories", {"name": "Starters", "sort_order": 1})
    mains = await _post(client, headers, "/categories", {"name": "Mains", "sort_order": 2})
    paneer = await _post(
        client,
        headers,
        "/items",
        {
            "category_id": starters["id"],
            "name": "Paneer Tikka",
            "description": "Char-grilled cottage cheese",
            "dietary": "veg",
            "display_price_paise": 24900,
        },
    )
    naan = await _post(
        client, headers, "/items", {"category_id": mains["id"], "name": "Butter Naan"}
    )
    spice = await _post(
        client,
        headers,
        "/modifier-groups",
        {"name": "Spice level", "min_select": 1, "max_select": 1},
    )
    options = [
        await _post(client, headers, f"/modifier-groups/{spice['id']}/modifiers", {"name": name})
        for name in ("Regular", "Spicy", "Extra Spicy")
    ]
    addons = await _post(
        client, headers, "/modifier-groups", {"name": "Add-ons", "min_select": 0, "max_select": 2}
    )
    cheese = await _post(
        client,
        headers,
        f"/modifier-groups/{addons['id']}/modifiers",
        {"name": "Extra Cheese", "display_price_delta_paise": 3000},
    )
    linked = await client.put(
        f"{M}/items/{paneer['id']}/modifier-groups",
        headers=headers,
        json={"group_ids": [spice["id"], addons["id"]]},
    )
    assert linked.status_code == 200, linked.text
    return {
        "starters": starters,
        "mains": mains,
        "paneer": paneer,
        "naan": naan,
        "spice": spice,
        "options": options,
        "addons": addons,
        "cheese": cheese,
    }


async def test_owner_builds_a_menu_and_staff_can_read_it(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    alpha = await seed.restaurant("alpha")
    await seed.user(alpha, "kitchen", "chef")
    await seed.user(alpha, "waiter", "ravi")
    owner = await staff_headers(client, "alpha", "owner")
    built = await build_menu(client, owner)

    for username in ("chef", "ravi"):
        menu = (await client.get(M, headers=await staff_headers(client, "alpha", username))).json()
        assert [c["name"] for c in menu["categories"]] == ["Starters", "Mains"]
        paneer = menu["categories"][0]["items"][0]
        assert paneer["name"] == "Paneer Tikka" and paneer["dietary"] == "veg"
        assert paneer["display_price_paise"] == 24900
        assert paneer["modifier_group_ids"] == [built["spice"]["id"], built["addons"]["id"]]
        groups = {g["name"]: g for g in menu["modifier_groups"]}
        # No sort_order given ⇒ the order in which the owner created them.
        assert [m["name"] for m in groups["Spice level"]["modifiers"]] == [
            "Regular",
            "Spicy",
            "Extra Spicy",
        ]


async def test_only_managers_edit_but_kitchen_can_mark_sold_out(
    client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    alpha = await seed.restaurant("alpha")
    await seed.user(alpha, "kitchen", "chef")
    await seed.user(alpha, "waiter", "ravi")
    owner = await staff_headers(client, "alpha", "owner")
    built = await build_menu(client, owner)
    chef = await staff_headers(client, "alpha", "chef")
    waiter = await staff_headers(client, "alpha", "ravi")
    item, option = built["paneer"]["id"], built["options"][1]["id"]

    for headers in (chef, waiter):
        assert (
            await client.post(f"{M}/categories", headers=headers, json={"name": "X"})
        ).status_code == 403
        assert (
            await client.patch(f"{M}/items/{item}", headers=headers, json={"name": "X"})
        ).status_code == 403
        assert (await client.delete(f"{M}/items/{item}", headers=headers)).status_code == 403

    sold_out = await client.patch(
        f"{M}/items/{item}/availability", headers=chef, json={"is_available": False}
    )
    assert sold_out.status_code == 200 and sold_out.json()["is_available"] is False
    option_off = await client.patch(
        f"{M}/modifiers/{option}/availability", headers=chef, json={"is_available": False}
    )
    assert option_off.status_code == 200 and option_off.json()["is_available"] is False
    assert (
        await client.patch(
            f"{M}/items/{item}/availability", headers=waiter, json={"is_available": True}
        )
    ).status_code == 403

    actions = {r["action"] for r in await owner_conn.fetch("SELECT action FROM audit_logs")}
    assert {
        "menu.category_created",
        "menu.item_created",
        "menu.group_created",
        "menu.option_created",
        "menu.item_groups_changed",
        "menu.item_availability_changed",
        "menu.option_availability_changed",
    } <= actions


async def test_updates_nulls_and_validation(client: httpx.AsyncClient, seed: Seeder) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    built = await build_menu(client, owner)
    item = built["paneer"]["id"]

    moved = await client.patch(
        f"{M}/items/{item}",
        headers=owner,
        json={"category_id": built["mains"]["id"], "display_price_paise": None, "dietary": None},
    )
    assert moved.status_code == 200
    assert moved.json()["category_id"] == built["mains"]["id"]
    assert moved.json()["display_price_paise"] is None and moved.json()["dietary"] is None

    described = await client.patch(
        f"{M}/items/{item}",
        headers=owner,
        json={"description": "  Smoky\r\n\r\n\r\n  and   soft  "},
    )
    assert described.json()["description"] == "Smoky\n\nand soft"

    bad_bodies = [
        {"name": None},
        {"name": ""},
        {"display_price_paise": -1},
        {"display_price_paise": 12.5},
        {"display_price_paise": "249"},
        {"dietary": "vegan"},
        {"is_available": None},
        {"description": "x" * 501},
        {"name": "Tikka" + chr(0x200B)},  # zero-width space
        {"restaurant_id": "00000000-0000-0000-0000-000000000000"},
        {},
    ]
    for body in bad_bodies:
        response = await client.patch(f"{M}/items/{item}", headers=owner, json=body)
        assert response.status_code == 422, body

    for body in (
        {"name": "Bad", "min_select": 3, "max_select": 2},
        {"name": "Bad", "min_select": 0, "max_select": 0},
        {"name": "Bad", "min_select": 0, "max_select": 21},
    ):
        assert (
            await client.post(f"{M}/modifier-groups", headers=owner, json=body)
        ).status_code == 422
    narrowed = await client.patch(
        f"{M}/modifier-groups/{built['addons']['id']}", headers=owner, json={"min_select": 3}
    )
    assert narrowed.status_code == 422  # would exceed the existing max_select of 2


async def test_explicit_sort_order_wins_and_omitted_appends(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    group = await _post(client, owner, "/modifier-groups", {"name": "Size"})
    for name in ("Small", "Medium", "Large"):
        await _post(client, owner, f"/modifier-groups/{group['id']}/modifiers", {"name": name})
    await _post(
        client,
        owner,
        f"/modifier-groups/{group['id']}/modifiers",
        {"name": "Kids", "sort_order": 0},
    )
    menu = (await client.get(M, headers=owner)).json()
    names = [m["name"] for m in menu["modifier_groups"][0]["modifiers"]]
    assert names == ["Kids", "Small", "Medium", "Large"]  # ties broken alphabetically


async def test_conflicts_and_limits_on_deletion(client: httpx.AsyncClient, seed: Seeder) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    built = await build_menu(client, owner)

    dup = await client.post(f"{M}/categories", headers=owner, json={"name": "  starters "})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "category_name_taken"
    dup_option = await client.post(
        f"{M}/modifier-groups/{built['spice']['id']}/modifiers",
        headers=owner,
        json={"name": "SPICY"},
    )
    assert dup_option.status_code == 409
    assert dup_option.json()["error"]["code"] == "modifier_name_taken"

    not_empty = await client.delete(f"{M}/categories/{built['starters']['id']}", headers=owner)
    assert (
        not_empty.status_code == 409 and not_empty.json()["error"]["code"] == "category_not_empty"
    )
    assert (
        await client.delete(f"{M}/items/{built['paneer']['id']}", headers=owner)
    ).status_code == 204
    assert (
        await client.delete(f"{M}/categories/{built['starters']['id']}", headers=owner)
    ).status_code == 204
    # Name reusable after soft delete.
    await _post(client, owner, "/categories", {"name": "Starters"})

    assert (
        await client.delete(f"{M}/modifier-groups/{built['spice']['id']}", headers=owner)
    ).status_code == 204
    menu = (await client.get(M, headers=owner)).json()
    assert "Spice level" not in {g["name"] for g in menu["modifier_groups"]}
    # Deleted option groups disappear from items that used them.
    assert all(
        built["spice"]["id"] not in item["modifier_group_ids"]
        for category in menu["categories"]
        for item in category["items"]
    )


async def test_body_ids_must_belong_to_the_same_restaurant(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    await seed.restaurant("alpha")
    await seed.restaurant("bravo")
    alpha = await staff_headers(client, "alpha", "owner")
    bravo = await staff_headers(client, "bravo", "owner")
    alpha_menu = await build_menu(client, alpha)
    bravo_menu = await build_menu(client, bravo)

    # Bravo's item cannot be moved into alpha's category, nor use alpha's option group.
    move = await client.patch(
        f"{M}/items/{bravo_menu['paneer']['id']}",
        headers=bravo,
        json={"category_id": alpha_menu["starters"]["id"]},
    )
    assert move.status_code == 422 and move.json()["error"]["code"] == "unknown_category"
    create = await client.post(
        f"{M}/items",
        headers=bravo,
        json={"category_id": alpha_menu["mains"]["id"], "name": "Sneaky"},
    )
    assert create.status_code == 422
    link = await client.put(
        f"{M}/items/{bravo_menu['paneer']['id']}/modifier-groups",
        headers=bravo,
        json={"group_ids": [alpha_menu["spice"]["id"]]},
    )
    assert link.status_code == 422 and link.json()["error"]["code"] == "unknown_modifier_group"
    duplicate_ids = await client.put(
        f"{M}/items/{bravo_menu['paneer']['id']}/modifier-groups",
        headers=bravo,
        json={"group_ids": [bravo_menu["spice"]["id"], bravo_menu["spice"]["id"]]},
    )
    assert duplicate_ids.status_code == 422


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PATCH", "/categories/{starters}", {"name": "pwned"}),
        ("DELETE", "/categories/{mains}", None),
        ("PATCH", "/items/{paneer}", {"name": "pwned"}),
        ("DELETE", "/items/{paneer}", None),
        ("PATCH", "/items/{paneer}/availability", {"is_available": False}),
        ("PUT", "/items/{paneer}/modifier-groups", {"group_ids": []}),
        ("DELETE", "/items/{paneer}/image", None),
        ("PATCH", "/modifier-groups/{spice}", {"name": "pwned"}),
        ("DELETE", "/modifier-groups/{spice}", None),
        ("POST", "/modifier-groups/{spice}/modifiers", {"name": "pwned"}),
        ("PATCH", "/modifiers/{cheese}", {"name": "pwned"}),
        ("PATCH", "/modifiers/{cheese}/availability", {"is_available": False}),
        ("DELETE", "/modifiers/{cheese}", None),
    ],
)
async def test_other_restaurants_menu_is_unreachable(
    client: httpx.AsyncClient, seed: Seeder, method: str, path: str, body: dict | None
) -> None:
    await seed.restaurant("alpha")
    await seed.restaurant("bravo")
    alpha = await staff_headers(client, "alpha", "owner")
    bravo = await staff_headers(client, "bravo", "owner")
    built = await build_menu(client, alpha)
    ids = {key: value["id"] for key, value in built.items() if isinstance(value, dict)}
    response = await client.request(method, M + path.format(**ids), headers=bravo, json=body)
    assert response.status_code == 404
    assert (await client.get(M, headers=bravo)).json() == {"categories": [], "modifier_groups": []}
