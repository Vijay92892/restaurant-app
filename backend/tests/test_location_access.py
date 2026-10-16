"""Location-based table access (migration 0007): a guest at the restaurant opens a table session
without the PIN, when the restaurant turns it on. A position report is the guest's own claim,
never proof of presence; these tests pin down what the server does and doesn't accept."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest

from app.core import geo
from tests.conftest import (
    SeededRestaurant,
    Seeder,
    create_table,
    current_pin,
    open_table_session,
    staff_headers,
)
from tests.test_customer_orders import place
from tests.test_menu import build_menu

ACCESS = "/api/v1/restaurant/customer-access"
CENTER = (28.2449519, 76.8135882)  # the demo branch's map pin
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def north(metres: float, of: tuple[float, float] = CENTER) -> tuple[float, float]:
    """A point this many metres due north (1 degree of latitude ≈ 111.2 km)."""
    return of[0] + metres / (math.pi * geo.EARTH_RADIUS_M / 180), of[1]


def report(
    at: tuple[float, float] = CENTER,
    accuracy: float = 20,
    captured_at: datetime | None = None,
) -> dict:
    return {
        "latitude": at[0],
        "longitude": at[1],
        "accuracy_m": accuracy,
        "captured_at": (captured_at or datetime.now(UTC)).isoformat(),
    }


def policy(enabled: bool = True, *, fallback: bool = True, radius: int = 100, point=CENTER) -> dict:
    return {
        "location_ordering_enabled": enabled,
        "latitude": None if point is None else point[0],
        "longitude": None if point is None else point[1],
        "radius_m": radius,
        "pin_fallback_enabled": fallback,
    }


def located(qr_token: str) -> str:
    return f"/api/v1/public/tables/{qr_token}/session/location"


async def seed_user_headers(
    client: httpx.AsyncClient, seed: Seeder, restaurant: SeededRestaurant, role: str, name: str
) -> dict[str, str]:
    await seed.user(restaurant, role, name)
    return await staff_headers(client, restaurant.code, name)


# --- distance and the location check (no database) ---------------------------------------------


def test_haversine_distances() -> None:
    assert geo.distance_m(*CENTER, *CENTER) == 0
    # One degree along the equator, and the same on a meridian.
    assert geo.distance_m(0, 0, 0, 1) == pytest.approx(111_195, abs=1)
    assert geo.distance_m(0, 0, 1, 0) == pytest.approx(111_195, abs=1)
    # London to Paris, about 343.6 km on the sphere; symmetric.
    london, paris = (51.5074, -0.1278), (48.8566, 2.3522)
    assert geo.distance_m(*london, *paris) == pytest.approx(343_560, rel=0.002)
    assert geo.distance_m(*london, *paris) == geo.distance_m(*paris, *london)
    # Short distances, the ones a geofence cares about, to well under a metre.
    assert geo.distance_m(*CENTER, *north(100)) == pytest.approx(100, abs=0.01)
    assert geo.distance_m(*CENTER, *north(5)) == pytest.approx(5, abs=0.01)
    # Opposite sides of the Earth: no domain error from rounding.
    assert geo.distance_m(0, 0, 0, 180) == pytest.approx(math.pi * geo.EARTH_RADIUS_M)


def judge(at: tuple[float, float], accuracy: float = 20, age: timedelta = timedelta()) -> str:
    fence = geo.Geofence(CENTER[0], CENTER[1], 100)
    position = geo.PositionReport(at[0], at[1], accuracy, NOW - age)
    return geo.judge(
        position,
        fence,
        now=NOW,
        max_accuracy_m=100,
        max_age=timedelta(seconds=120),
        clock_skew=timedelta(seconds=60),
    ).value


def test_the_location_check() -> None:
    assert judge(CENTER) == "inside"
    assert judge(north(99)) == "inside"
    assert judge(north(400)) == "outside"
    # Near the edge, half the phone's own uncertainty counts in its favour, no more.
    assert judge(north(110), accuracy=30) == "inside"  # 110 - 15 <= 100
    assert judge(north(110), accuracy=10) == "outside"  # 110 - 5 > 100
    assert judge(north(149), accuracy=100) == "inside"  # the most it can ever add: 50 m
    assert judge(north(151), accuracy=100) == "outside"
    # Too vague, broken numbers, too old, from the future (beyond the clock allowance).
    assert judge(CENTER, accuracy=101) == "inaccurate"
    assert judge(CENTER, accuracy=math.nan) == "inaccurate"
    assert judge(CENTER, accuracy=-1) == "inaccurate"
    assert judge(CENTER, age=timedelta(seconds=121)) == "stale"
    assert judge(CENTER, age=timedelta(seconds=-61)) == "stale"
    assert judge(CENTER, age=timedelta(seconds=-30)) == "inside"  # a phone clock a bit ahead


# --- the setting: owner and managers only ------------------------------------------------------


async def test_only_owner_and_managers_see_or_change_the_policy(
    client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    alpha = await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    manager = await seed_user_headers(client, seed, alpha, "admin", "kiran")
    waiter = await seed_user_headers(client, seed, alpha, "waiter", "ravi")
    kitchen = await seed_user_headers(client, seed, alpha, "kitchen", "chef")

    start = (await client.get(ACCESS, headers=owner)).json()
    assert start == {
        "location_ordering_enabled": False,  # off until the restaurant turns it on
        "latitude": None,
        "longitude": None,
        "radius_m": 100,
        "pin_fallback_enabled": True,
        "max_accuracy_m": 100,
    }
    for headers in (waiter, kitchen):
        assert (await client.get(ACCESS, headers=headers)).status_code == 403
        assert (await client.put(ACCESS, headers=headers, json=policy())).status_code == 403
    assert (await client.get(ACCESS)).status_code == 401

    saved = await client.put(
        ACCESS, headers=manager, json=policy(point=(28.24495191234, 76.81358823456), radius=150)
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["location_ordering_enabled"] is True
    assert (body["latitude"], body["longitude"], body["radius_m"]) == (28.244952, 76.813588, 150)
    assert (await client.get(ACCESS, headers=owner)).json() == body

    row = await owner_conn.fetchrow(
        "SELECT actor_user_id, details FROM audit_logs "
        "WHERE action = 'restaurant.customer_access_changed'"
    )
    details = json.loads(row["details"])
    assert details["location_ordering_enabled"] is True and details["radius_m"] == 150
    assert "location_latitude" in details["fields"]
    assert row["actor_user_id"] is not None

    # The whole policy must make sense before anything is stored.
    for bad in (
        policy(point=None),  # on without a point
        {**policy(), "longitude": None},  # half a point
        policy(point=(0, 0)),
        policy(point=(91, 76.8)),
        policy(point=(28.2, 181)),
        policy(radius=10),
        policy(radius=900),
        {**policy(), "restaurant_id": "x"},  # nothing extra
    ):
        refused = await client.put(ACCESS, headers=owner, json=bad)
        assert refused.status_code == 422, bad
    assert (await client.get(ACCESS, headers=owner)).json() == body


async def test_the_database_holds_the_same_rules(
    seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    alpha = await seed.restaurant("alpha")
    with pytest.raises(asyncpg.CheckViolationError):
        await owner_conn.execute(
            "UPDATE restaurants SET location_ordering_enabled = true WHERE id = $1", alpha.id
        )
    with pytest.raises(asyncpg.CheckViolationError):
        await owner_conn.execute(
            "UPDATE restaurants SET location_latitude = 28.2 WHERE id = $1", alpha.id
        )
    with pytest.raises(asyncpg.CheckViolationError):
        await owner_conn.execute(
            "UPDATE restaurants SET location_radius_m = 5000 WHERE id = $1", alpha.id
        )


# --- guests ------------------------------------------------------------------------------------


@pytest.fixture
async def place_alpha(client: httpx.AsyncClient, seed: Seeder) -> dict:
    """Restaurant alpha with a menu, two tables and location access on (PIN as backup)."""
    await seed.restaurant("alpha", name="Spice Garden")
    owner = await staff_headers(client, "alpha", "owner")
    built = await build_menu(client, owner)
    t1 = await create_table(client, owner, "T1")
    t2 = await create_table(client, owner, "T2")
    assert (await client.put(ACCESS, headers=owner, json=policy())).status_code == 200
    return {"owner": owner, "built": built, "t1": t1, "t2": t2}


async def test_off_by_default_the_pin_is_the_only_way_in(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    table = await create_table(client, owner, "T1")
    landing = (await client.get(f"/api/v1/public/tables/{table['qr_token']}")).json()
    assert (landing["pin_enabled"], landing["location_enabled"]) == (True, False)
    refused = await client.post(located(table["qr_token"]), json=report())
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "access_method_off")
    pin = await current_pin(client, owner)
    assert (await open_table_session(client, table["qr_token"], pin)).status_code == 200


async def test_a_guest_at_the_restaurant_orders_without_the_pin(
    client: httpx.AsyncClient, place_alpha: dict, owner_conn: asyncpg.Connection
) -> None:
    t1 = place_alpha["t1"]
    landing = await client.get(f"/api/v1/public/tables/{t1['qr_token']}")
    body = landing.json()
    assert (body["pin_enabled"], body["location_enabled"], body["location_radius_m"]) == (
        True,
        True,
        100,
    )
    # The restaurant's own coordinates are never public.
    assert "28.24" not in landing.text and "latitude" not in landing.text

    opened = await client.post(located(t1["qr_token"]), json=report(at=north(30)))
    assert opened.status_code == 200, opened.text
    cookie = opened.headers["set-cookie"].lower()
    assert cookie.startswith("__host-ts=")
    assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert (await client.get(f"/api/v1/public/tables/{t1['qr_token']}")).json()["has_session"]

    row = await owner_conn.fetchrow(
        "SELECT access_method, pin_version, token_hash FROM table_sessions"
    )
    assert (row["access_method"], row["pin_version"]) == ("location", None)
    assert len(row["token_hash"]) == 32  # only the hash; the cookie token is never stored
    # No column anywhere holds the guest's position.
    columns = await owner_conn.fetch(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_name ~ '(latitude|longitude|accuracy)'"
    )
    assert {(c["table_name"], c["column_name"]) for c in columns} == {
        ("restaurants", "location_latitude"),
        ("restaurants", "location_longitude"),
    }

    built = place_alpha["built"]
    order = await place(client, {"items": [{"menu_item_id": built["naan"]["id"], "quantity": 1}]})
    assert order.status_code == 201, order.text
    placed = await owner_conn.fetchrow("SELECT table_id, table_session_id FROM orders")
    assert str(placed["table_id"]) == place_alpha["t1"]["id"]


async def test_refusals_say_what_to_fix_but_never_how_far(
    client: httpx.AsyncClient, place_alpha: dict
) -> None:
    url = located(place_alpha["t1"]["qr_token"])
    cases = {
        "location_outside": report(at=north(2_000)),
        "location_inaccurate": report(accuracy=800),
        "location_stale": report(captured_at=datetime.now(UTC) - timedelta(minutes=10)),
    }
    for code, body in cases.items():
        refused = await client.post(url, json=body)
        assert (refused.status_code, refused.json()["error"]["code"]) == (403, code), body
        assert not any(ch.isdigit() for ch in refused.json()["error"]["message"])
    future = report(captured_at=datetime.now(UTC) + timedelta(minutes=10))
    assert (await client.post(url, json=future)).json()["error"]["code"] == "location_stale"
    # Malformed reports never reach the check.
    for bad in (
        {**report(), "latitude": 95},
        {**report(), "longitude": -200},
        {**report(), "captured_at": "yesterday"},
        {**report(), "captured_at": "2026-10-17T12:00:00"},  # no time zone
        {**report(), "is_near_restaurant": True},  # a client's yes is never authorisation
        {k: v for k, v in report().items() if k != "accuracy_m"},
    ):
        assert (await client.post(url, json=bad)).status_code == 422, bad
    nan = json.dumps(report()).replace('"accuracy_m": 20', '"accuracy_m": NaN')
    sent = await client.post(url, content=nan, headers={"Content-Type": "application/json"})
    assert sent.status_code == 422


async def test_one_report_opens_at_most_one_session(
    client: httpx.AsyncClient, client_from, place_alpha: dict
) -> None:
    url = located(place_alpha["t1"]["qr_token"])
    body = report()
    assert (await client.post(url, json=body)).status_code == 200
    replay = await client_from("198.51.100.9").post(url, json=body)  # captured and sent again
    assert (replay.status_code, replay.json()["error"]["code"]) == (403, "location_reused")
    # A replay of an old report is simply stale.
    old = report(captured_at=datetime.now(UTC) - timedelta(hours=2))
    assert (await client.post(url, json=old)).json()["error"]["code"] == "location_stale"


async def test_a_made_up_position_inside_is_accepted_so_it_is_not_proof(
    client_from, place_alpha: dict
) -> None:
    """The limitation, stated as a test: anyone who knows the restaurant's map pin can claim to
    be there. This is why the restaurant opts in, keeps the PIN as a backup if it wants, and
    why attempts are budgeted (docs/03 §4.5)."""
    faker = client_from("192.0.2.200")
    made_up = report(at=CENTER, accuracy=5)
    assert (
        await faker.post(located(place_alpha["t1"]["qr_token"]), json=made_up)
    ).status_code == 200


async def test_sessions_stay_on_their_own_table_and_restaurant(
    client: httpx.AsyncClient, seed: Seeder, place_alpha: dict, owner_conn: asyncpg.Connection
) -> None:
    t1, t2 = place_alpha["t1"], place_alpha["t2"]
    assert (await client.post(located(t1["qr_token"]), json=report())).status_code == 200
    # The cookie is for T1 only; ordering always goes to T1.
    assert not (await client.get(f"/api/v1/public/tables/{t2['qr_token']}")).json()["has_session"]
    built = place_alpha["built"]
    order = await place(client, {"items": [{"menu_item_id": built["naan"]["id"], "quantity": 1}]})
    assert order.status_code == 201
    assert str(await owner_conn.fetchval("SELECT table_id FROM orders")) == t1["id"]

    # Another restaurant (location on too) knows nothing of this session.
    await seed.restaurant("beta", name="Other Place")
    beta_owner = await staff_headers(client, "beta", "owner")
    beta_table = await create_table(client, beta_owner, "B1")
    far_away = (19.0760, 72.8777)
    assert (
        await client.put(ACCESS, headers=beta_owner, json=policy(point=far_away))
    ).status_code == 200
    beta_landing = await client.get(f"/api/v1/public/tables/{beta_table['qr_token']}")
    assert not beta_landing.json()["has_session"]
    menu = (await client.get("/api/v1/public/menu")).json()
    assert menu["restaurant_name"] == "Spice Garden"  # still alpha's, never beta's
    # Standing at alpha doesn't open beta's tables.
    refused = await client.post(located(beta_table["qr_token"]), json=report())
    assert refused.json()["error"]["code"] == "location_outside"
    # Unknown QR codes fail exactly like on the PIN path: no hint whether anything exists.
    unknown = await client.post(located("A" * 22), json=report())
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (401, "invalid_table_access")


async def test_without_a_pin_backup_only_the_location_check_opens_tables(
    client: httpx.AsyncClient, place_alpha: dict
) -> None:
    owner, t1 = place_alpha["owner"], place_alpha["t1"]
    pin = await current_pin(client, owner)
    assert (await client.put(ACCESS, headers=owner, json=policy(fallback=False))).status_code == 200
    assert not (await client.get(f"/api/v1/public/tables/{t1['qr_token']}")).json()["pin_enabled"]
    refused = await open_table_session(client, t1["qr_token"], pin)  # even the right PIN
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "access_method_off")
    assert (await client.post(located(t1["qr_token"]), json=report())).status_code == 200

    # Location access off again: the PIN is back, whatever the backup switch says.
    off = policy(enabled=False, fallback=False)
    assert (await client.put(ACCESS, headers=owner, json=off)).status_code == 200
    assert (await client.get(f"/api/v1/public/tables/{t1['qr_token']}")).json()["pin_enabled"]
    assert (await open_table_session(client, t1["qr_token"], pin)).status_code == 200


async def test_switching_off_stops_new_sessions_but_keeps_open_ones(
    client: httpx.AsyncClient, client_from, place_alpha: dict
) -> None:
    owner, t1, built = place_alpha["owner"], place_alpha["t1"], place_alpha["built"]
    assert (await client.post(located(t1["qr_token"]), json=report())).status_code == 200
    assert (await client.put(ACCESS, headers=owner, json=policy(enabled=False))).status_code == 200

    newcomer = client_from("198.51.100.20")
    refused = await newcomer.post(located(t1["qr_token"]), json=report())
    assert refused.json()["error"]["code"] == "access_method_off"
    # The table already seated keeps ordering (nobody's order is cut off) …
    order = await place(client, {"items": [{"menu_item_id": built["naan"]["id"], "quantity": 1}]})
    assert order.status_code == 201
    # … until staff end every session, as for PIN sessions.
    ended = await client.post("/api/v1/table-sessions/invalidate-all", headers=owner)
    assert ended.status_code == 200
    assert (await client.get("/api/v1/public/menu")).status_code == 401


async def test_pin_rotation_treats_location_sessions_like_pin_sessions(
    client: httpx.AsyncClient, place_alpha: dict
) -> None:
    owner, t1 = place_alpha["owner"], place_alpha["t1"]
    assert (await client.post(located(t1["qr_token"]), json=report())).status_code == 200
    rotated = await client.post("/api/v1/pin/rotate", headers=owner, json={})
    assert rotated.status_code == 200
    assert (await client.get("/api/v1/public/menu")).status_code == 200  # still seated
    ended = await client.post(
        "/api/v1/pin/rotate", headers=owner, json={"invalidate_active_sessions": True}
    )
    assert ended.json()["sessions_invalidated"] == 1
    assert (await client.get("/api/v1/public/menu")).status_code == 401


async def test_expired_sessions_are_refused(
    client: httpx.AsyncClient, place_alpha: dict, owner_conn: asyncpg.Connection
) -> None:
    assert (
        await client.post(located(place_alpha["t1"]["qr_token"]), json=report())
    ).status_code == 200
    # expires_at is immutable for the app; the schema owner simulates the passage of time.
    await owner_conn.execute(
        "ALTER TABLE table_sessions DISABLE TRIGGER trg_table_sessions_immutable"
    )
    try:
        await owner_conn.execute(
            "UPDATE table_sessions SET expires_at = created_at + interval '1 millisecond'"
        )
    finally:
        await owner_conn.execute(
            "ALTER TABLE table_sessions ENABLE TRIGGER trg_table_sessions_immutable"
        )
    assert (await client.get("/api/v1/public/menu")).status_code == 401


async def test_floods_are_cut_off(client_from, place_alpha: dict) -> None:
    url = located(place_alpha["t1"]["qr_token"])
    attacker = client_from("203.0.113.50")
    statuses = [
        (await attacker.post(url, json=report(at=north(5_000)))).status_code for _ in range(11)
    ]
    assert statuses[:10] == [403] * 10 and statuses[10] == 429  # 10 failures per address
    # Many addresses against one table: the table's own budget (30 per 15 minutes) holds.
    for n in range(20):
        await client_from(f"198.51.100.{100 + n}").post(url, json=report(at=north(5_000)))
    late = await client_from("198.51.100.250").post(url, json=report())
    assert late.status_code == 429  # even a good report waits once the table is flooded
