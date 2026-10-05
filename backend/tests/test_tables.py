"""Phase 4: tables, QR codes, closing tables, IDOR protection."""

from __future__ import annotations

import asyncpg
import httpx

from tests.conftest import (
    Seeder,
    create_table,
    current_pin,
    open_table_session,
    staff_headers,
)


async def test_table_crud_and_listing(client: httpx.AsyncClient, seed: Seeder) -> None:
    alpha = await seed.restaurant("alpha")
    await seed.user(alpha, "waiter", "ravi")
    await seed.user(alpha, "kitchen", "chef")
    owner = await staff_headers(client, "alpha", "owner")

    t4 = await create_table(client, owner, "T4")
    await create_table(client, owner, "T1")
    assert t4["qr_url"].startswith("https://order.example.test/t/")
    assert len(t4["qr_token"]) == 22  # 128-bit token

    renamed = await client.patch(
        f"/api/v1/tables/{t4['id']}", headers=owner, json={"label": "Window 4", "sort_order": 9}
    )
    assert renamed.status_code == 200
    assert renamed.json()["label"] == "Window 4"

    for username in ("ravi", "chef"):
        listed = await client.get(
            "/api/v1/tables", headers=await staff_headers(client, "alpha", username)
        )
        assert [t["label"] for t in listed.json()] == ["T1", "Window 4"]

    waiter = await staff_headers(client, "alpha", "ravi")
    assert (
        await client.post("/api/v1/tables", headers=waiter, json={"label": "X"})
    ).status_code == 403
    assert (
        await client.patch(f"/api/v1/tables/{t4['id']}", headers=waiter, json={"label": "X"})
    ).status_code == 403


async def test_labels_are_unique_case_insensitively_and_validated(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    await create_table(client, owner, "Patio 1")
    dup = await client.post("/api/v1/tables", headers=owner, json={"label": "  patio   1 "})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "table_label_taken"
    for bad in ("", "x" * 21, "T\n1", "<b>T1</b>" * 3):
        response = await client.post("/api/v1/tables", headers=owner, json={"label": bad})
        assert response.status_code == 422, bad
    # Plain text is stored as-is (escaped at render time), not rejected.
    ok = await client.post("/api/v1/tables", headers=owner, json={"label": "<b>T9</b>"})
    assert ok.status_code == 201 and ok.json()["label"] == "<b>T9</b>"


async def test_qr_regeneration_kills_the_old_code(client: httpx.AsyncClient, seed: Seeder) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    table = await create_table(client, owner, "T1")
    old = table["qr_token"]
    assert (await client.get(f"/api/v1/public/tables/{old}")).status_code == 200

    response = await client.post(f"/api/v1/tables/{table['id']}/qr/regenerate", headers=owner)
    assert response.status_code == 200
    new = response.json()["qr_url"].rsplit("/", 1)[1]
    assert new != old
    assert (await client.get(f"/api/v1/public/tables/{old}")).status_code == 404
    assert (await client.get(f"/api/v1/public/tables/{new}")).status_code == 200


async def test_qr_svg_download(client: httpx.AsyncClient, seed: Seeder) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    table = await create_table(client, owner, "T1")
    response = await client.get(f"/api/v1/tables/{table['id']}/qr.svg", headers=owner)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.text.lstrip().startswith("<svg")
    assert "<script" not in response.text
    assert response.headers["content-security-policy"].startswith("default-src 'none'")


async def test_disable_and_delete_end_customer_access_but_keep_history(
    client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    table = await create_table(client, owner, "T1")
    pin = await current_pin(client, owner)
    assert (await open_table_session(client, table["qr_token"], pin)).status_code == 200
    assert (await client.get("/api/v1/public/session")).status_code == 200

    off = await client.patch(
        f"/api/v1/tables/{table['id']}", headers=owner, json={"is_active": False}
    )
    assert off.status_code == 200 and off.json()["active_sessions"] == 0
    assert (await client.get("/api/v1/public/session")).status_code == 401
    assert (await client.get(f"/api/v1/public/tables/{table['qr_token']}")).status_code == 404
    assert (await open_table_session(client, table["qr_token"], pin)).status_code == 401

    await client.patch(f"/api/v1/tables/{table['id']}", headers=owner, json={"is_active": True})
    assert (await open_table_session(client, table["qr_token"], pin)).status_code == 200

    assert (await client.delete(f"/api/v1/tables/{table['id']}", headers=owner)).status_code == 204
    assert (await client.get("/api/v1/public/session")).status_code == 401
    assert (await client.get("/api/v1/tables", headers=owner)).json() == []
    row = await owner_conn.fetchrow(
        "SELECT deleted_at FROM dining_tables WHERE id = $1", table["id"]
    )
    assert row["deleted_at"] is not None  # soft-deleted: past orders keep their table
    assert await owner_conn.fetchval("SELECT count(*) FROM table_sessions") == 2  # history kept
    await create_table(client, owner, "T1")  # label reusable


async def test_close_table_ends_only_that_tables_sessions(
    client: httpx.AsyncClient,
    client_from,
    seed: Seeder,  # noqa: ANN001
) -> None:
    alpha = await seed.restaurant("alpha")
    await seed.user(alpha, "waiter", "ravi")
    await seed.user(alpha, "kitchen", "chef")
    owner = await staff_headers(client, "alpha", "owner")
    t1 = await create_table(client, owner, "T1")
    t2 = await create_table(client, owner, "T2")
    pin = await current_pin(client, owner)
    diner1, diner2 = client_from("198.51.100.11"), client_from("198.51.100.12")
    assert (await open_table_session(diner1, t1["qr_token"], pin)).status_code == 200
    assert (await open_table_session(diner2, t2["qr_token"], pin)).status_code == 200

    chef = await staff_headers(client, "alpha", "chef")
    assert (await client.post(f"/api/v1/tables/{t1['id']}/close", headers=chef)).status_code == 403
    waiter = await staff_headers(client, "alpha", "ravi")
    closed = await client.post(f"/api/v1/tables/{t1['id']}/close", headers=waiter)
    assert closed.status_code == 200 and closed.json() == {
        "sessions_ended": 1,
        "orders_completed": 0,
    }
    assert (await diner1.get("/api/v1/public/session")).status_code == 401
    assert (await diner2.get("/api/v1/public/session")).status_code == 200


async def test_other_restaurants_tables_are_invisible(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    await seed.restaurant("alpha")
    await seed.restaurant("bravo")
    alpha_owner = await staff_headers(client, "alpha", "owner")
    bravo_owner = await staff_headers(client, "bravo", "owner")
    table = await create_table(client, alpha_owner, "T1")

    assert (await client.get("/api/v1/tables", headers=bravo_owner)).json() == []
    for method, path, body in (
        ("PATCH", f"/api/v1/tables/{table['id']}", {"label": "pwned"}),
        ("DELETE", f"/api/v1/tables/{table['id']}", None),
        ("POST", f"/api/v1/tables/{table['id']}/qr/regenerate", None),
        ("GET", f"/api/v1/tables/{table['id']}/qr.svg", None),
        ("POST", f"/api/v1/tables/{table['id']}/close", None),
    ):
        response = await client.request(method, path, headers=bravo_owner, json=body)
        assert response.status_code == 404, (method, path)
    listed = (await client.get("/api/v1/tables", headers=alpha_owner)).json()
    assert listed[0]["label"] == "T1" and listed[0]["qr_url"] == table["qr_url"]
