"""Phase 11: staff problem reports (pilot observation) and the "entered by mistake" reason."""

from __future__ import annotations

import uuid

import httpx

from tests.conftest import Seeder, staff_headers
from tests.test_realtime import ORDERS, place

REPORTS = "/api/v1/problem-reports"


async def test_any_staff_member_reports_a_problem_and_managers_read_them(
    client: httpx.AsyncClient, crew: dict
) -> None:
    order = await place(client, crew)
    waiter = await client.post(
        REPORTS,
        headers=crew["waiter"],
        json={
            "category": "order_mistake",
            "note": "  Guest   wanted\r\nno onions  ",
            "order_id": order["id"],
        },
    )
    assert waiter.status_code == 201, waiter.text
    body = waiter.json()
    assert body["note"] == "Guest wanted\nno onions"  # cleaned like every other note
    assert body["order_number"] == order["order_number"]
    assert body["table_label"] == "T4"
    assert (body["reported_by"], body["reported_by_role"]) == ("Ravi", "waiter")

    chef = await client.post(REPORTS, headers=crew["chef"], json={"category": "network"})
    assert chef.status_code == 201, chef.text
    assert chef.json()["note"] is None
    assert chef.json()["order_id"] is None

    listing = await client.get(REPORTS, headers=crew["owner"])
    assert listing.status_code == 200, listing.text
    assert [r["category"] for r in listing.json()] == ["network", "order_mistake"]  # newest first
    assert listing.json()[1]["order_number"] == order["order_number"]

    # Floor and kitchen staff file reports; only managers read them.
    for headers in (crew["waiter"], crew["chef"]):
        assert (await client.get(REPORTS, headers=headers)).status_code == 403


async def test_problem_reports_validate_their_input(client: httpx.AsyncClient, crew: dict) -> None:
    invisible = "bad" + chr(0x202E) + "note"  # a right-to-left override, used for spoofing
    refused = [
        {"category": "fire"},
        {"category": "other", "note": "x" * 501},
        {"category": "other", "note": invisible},
        {"category": "other", "order_id": "not-a-uuid"},
        {"category": "other", "restaurant_id": str(crew["restaurant"].id)},
    ]
    for body in refused:
        response = await client.post(REPORTS, headers=crew["waiter"], json=body)
        assert response.status_code == 422, (body, response.status_code)

    # An order that does not exist (here or anywhere) is not found.
    missing = await client.post(
        REPORTS, headers=crew["waiter"], json={"category": "other", "order_id": str(uuid.uuid4())}
    )
    assert missing.status_code == 404

    blank = await client.post(
        REPORTS, headers=crew["waiter"], json={"category": "slow", "note": " "}
    )
    assert blank.status_code == 201
    assert blank.json()["note"] is None


async def test_problem_reports_are_rate_limited_per_person(
    client: httpx.AsyncClient, crew: dict
) -> None:
    for _ in range(20):
        response = await client.post(REPORTS, headers=crew["waiter"], json={"category": "other"})
        assert response.status_code == 201
    assert (
        await client.post(REPORTS, headers=crew["waiter"], json={"category": "other"})
    ).status_code == 429
    # Someone else on the same device or network is unaffected.
    assert (
        await client.post(REPORTS, headers=crew["chef"], json={"category": "other"})
    ).status_code == 201


async def test_managers_never_see_another_restaurants_reports(
    client: httpx.AsyncClient, crew: dict, seed: Seeder
) -> None:
    await client.post(REPORTS, headers=crew["waiter"], json={"category": "network"})
    bravo = await seed.restaurant("bravo")
    other = await staff_headers(client, bravo.code, "owner")
    assert (await client.get(REPORTS, headers=other)).json() == []


async def test_orders_entered_by_mistake_are_cancelled_with_that_reason(
    client: httpx.AsyncClient, crew: dict
) -> None:
    order = await place(client, crew)
    response = await client.post(
        f"{ORDERS}/{order['id']}/cancel",
        headers=crew["waiter"],
        json={"reason_code": "mistake", "expected_version": order["version"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["cancel_reason_code"] == "mistake"
    assert response.json()["status"] == "cancelled"


async def test_a_reporter_who_left_still_shows_by_name(
    client: httpx.AsyncClient, crew: dict
) -> None:
    await client.post(REPORTS, headers=crew["waiter"], json={"category": "app_confusing"})
    staff = (await client.get("/api/v1/staff", headers=crew["owner"])).json()
    ravi = next(s for s in staff if s["username"] == "ravi")
    assert (
        await client.delete(f"/api/v1/staff/{ravi['id']}", headers=crew["owner"])
    ).status_code == 204
    listing = (await client.get(REPORTS, headers=crew["owner"])).json()
    assert [r["reported_by"] for r in listing] == ["Ravi"]
