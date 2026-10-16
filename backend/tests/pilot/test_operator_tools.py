"""Phase 11 operator tools: apply-setup (tables, staff, menu from one file), printable QR cards,
and the pilot report."""

from __future__ import annotations

import json
import uuid
import zoneinfo
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import httpx
import pytest

from app.cli import OperatorError, _operator, _pick_table, forget_failed_logins, load_setup
from app.core.config import Settings
from app.core.errors import InvalidInput, NotFound
from app.core.net import RequestMeta
from app.services import (
    pilot_report,
    qr_sheet,
    setup_service,
    staff_service,
    table_service,
    usage_service,
)
from app.services.setup_service import SetupFile
from tests.conftest import Seeder, create_table, login, login_ok, staff_headers
from tests.test_realtime import ORDERS, place

SETUP = {
    "tables": ["T1", "T2", "Patio 1"],
    "staff": [
        {"username": "ravi", "name": "Ravi", "role": "waiter"},
        {"username": "meena", "name": "Chef Meena", "role": "kitchen"},
    ],
    "option_groups": [
        {"name": "Spice level", "min": 1, "max": 1, "options": ["Mild", "Medium", "Hot"]},
        {
            "name": "Extras",
            "min": 0,
            "max": 2,
            "options": [{"name": "Extra cheese", "price": 40}, "Mint chutney"],
        },
    ],
    "menu": [
        {
            "category": "Starters",
            "dishes": [
                {
                    "name": "Paneer Tikka",
                    "type": "veg",
                    "price": 249,
                    "description": "Char-grilled cottage cheese",
                    "options": ["Spice level", "Extras"],
                },
                {"name": "Chicken 65", "type": "non_veg", "price": 279.5},
            ],
        },
        {"category": "Breads", "dishes": [{"name": "Butter Naan", "type": "veg", "price": 60}]},
    ],
}


async def run_setup(
    app: object, settings: Settings, code: str, data: dict, *, dry_run: bool = False
) -> setup_service.SetupResult:
    async with app.state.db.sessionmaker() as session:  # type: ignore[attr-defined]
        ctx = await _operator(session, code)
        return await setup_service.apply(
            session, ctx, settings, SetupFile.model_validate(data), dry_run=dry_run
        )


async def test_setup_creates_everything_once_and_is_safe_to_rerun(
    app: object, client: httpx.AsyncClient, seed: Seeder, settings: Settings
) -> None:
    await seed.restaurant("alpha")
    dry = await run_setup(app, settings, "alpha", SETUP, dry_run=True)
    assert len(dry.created) == 3 + 2 + 2 + 2 + 3  # tables, staff, groups, categories, dishes
    assert dry.new_staff == []
    owner = await staff_headers(client, "alpha", "owner")
    assert (await client.get("/api/v1/tables", headers=owner)).json() == []  # dry: nothing

    first = await run_setup(app, settings, "alpha", SETUP)
    assert sorted(first.created) == sorted(dry.created)
    assert [(u, r) for u, r, _ in first.new_staff] == [("ravi", "waiter"), ("meena", "kitchen")]

    menu = (await client.get("/api/v1/menu", headers=owner)).json()
    starters = next(c for c in menu["categories"] if c["name"] == "Starters")
    paneer = next(i for i in starters["items"] if i["name"] == "Paneer Tikka")
    assert paneer["display_price_paise"] == 24900
    assert paneer["dietary"] == "veg"
    groups = {g["id"]: g for g in menu["modifier_groups"]}
    assert [groups[g]["name"] for g in paneer["modifier_group_ids"]] == ["Spice level", "Extras"]
    extras = next(g for g in menu["modifier_groups"] if g["name"] == "Extras")
    assert [(m["name"], m["display_price_delta_paise"]) for m in extras["modifiers"]] == [
        ("Extra cheese", 4000),
        ("Mint chutney", None),
    ]
    chicken = next(i for i in starters["items"] if i["name"] == "Chicken 65")
    assert chicken["display_price_paise"] == 27950
    labels = [t["label"] for t in (await client.get("/api/v1/tables", headers=owner)).json()]
    assert labels == ["T1", "T2", "Patio 1"]  # in the file's order

    # New staff sign in with the temporary password and must change it first.
    username, _, password = first.new_staff[0]
    signed_in = await login_ok(client, "alpha", username, password)
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {signed_in['access_token']}"}
    )
    assert me.json()["must_change_password"] is True

    again = await run_setup(app, settings, "alpha", SETUP)
    assert (again.created, again.updated, again.new_staff) == ([], [], [])
    assert again.unchanged == 3 + 2 + 2 + 2 + 3


async def test_setup_updates_what_changed_and_never_deletes(
    app: object, client: httpx.AsyncClient, seed: Seeder, settings: Settings
) -> None:
    await seed.restaurant("alpha")
    await run_setup(app, settings, "alpha", SETUP)
    changed = json.loads(json.dumps(SETUP))
    changed["tables"] = ["T1"]  # T2 and Patio 1 left out: kept, not deleted
    changed["staff"] = []
    paneer = changed["menu"][0]["dishes"][0]
    paneer["price"] = 259
    paneer["options"] = ["Spice level"]
    changed["menu"][0]["dishes"] = [paneer]  # Chicken 65 left out: kept
    changed["option_groups"][1]["options"].append({"name": "Onion salad", "price": 0})
    changed["menu"].append({"category": "Drinks", "dishes": [{"name": "Masala Chai"}]})

    result = await run_setup(app, settings, "alpha", changed)
    assert sorted(result.created) == ["category Drinks", "dish Masala Chai"]
    assert sorted(result.updated) == ["dish Paneer Tikka", "option group Extras"]

    owner = await staff_headers(client, "alpha", "owner")
    menu = (await client.get("/api/v1/menu", headers=owner)).json()
    starters = next(c for c in menu["categories"] if c["name"] == "Starters")
    assert sorted(i["name"] for i in starters["items"]) == ["Chicken 65", "Paneer Tikka"]
    paneer_now = next(i for i in starters["items"] if i["name"] == "Paneer Tikka")
    assert paneer_now["display_price_paise"] == 25900
    assert len(paneer_now["modifier_group_ids"]) == 1
    assert len((await client.get("/api/v1/tables", headers=owner)).json()) == 3
    # Existing staff are never touched from the file (their passwords stay theirs).
    assert (await login(client, "alpha", "ravi", "wrong-password")).status_code == 401


async def test_setup_refuses_unknown_option_groups_before_changing_anything(
    app: object, client: httpx.AsyncClient, seed: Seeder, settings: Settings
) -> None:
    await seed.restaurant("alpha")
    broken = json.loads(json.dumps(SETUP))
    broken["menu"][0]["dishes"][0]["options"] = ["Spice level", "Sauces"]
    with pytest.raises(InvalidInput, match="Sauces"):
        await run_setup(app, settings, "alpha", broken)
    owner = await staff_headers(client, "alpha", "owner")
    assert (await client.get("/api/v1/tables", headers=owner)).json() == []


def test_setup_files_are_checked_before_anything_runs(tmp_path: Path) -> None:
    def problems(data: object) -> str:
        path = tmp_path / "setup.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(OperatorError) as raised:
            load_setup(path)
        return str(raised.value)

    assert "appears twice" in problems({"tables": ["T1", "t1"]})
    assert "min cannot exceed max" in problems(
        {"option_groups": [{"name": "Size", "min": 2, "max": 1, "options": ["S", "M"]}]}
    )
    assert "menu.0.dishes.0.price" in problems(
        {"menu": [{"category": "Mains", "dishes": [{"name": "Dal", "price": -5}]}]}
    )
    assert "role" in problems({"staff": [{"username": "boss", "name": "Boss", "role": "owner"}]})
    assert "tabels: Extra inputs are not permitted" in problems({"tabels": ["T1"]})
    path = tmp_path / "bad.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(OperatorError, match="not valid JSON"):
        load_setup(path)
    # A UTF-8 byte-order mark (Notepad, Excel exports) is fine.
    path.write_text("\N{BYTE ORDER MARK}" + json.dumps({"tables": ["T1"]}), encoding="utf-8")
    assert load_setup(path).tables == ["T1"]


async def test_qr_cards_hold_each_live_tables_link_and_escape_names(
    app: object, client: httpx.AsyncClient, seed: Seeder, settings: Settings
) -> None:
    restaurant = await seed.restaurant("alpha", name="Dhaba <script>alert(1)</script>")
    await run_setup(app, settings, "alpha", {"tables": ["T1", "T2"]})
    owner = await staff_headers(client, "alpha", "owner")
    tables = (await client.get("/api/v1/tables", headers=owner)).json()
    t2 = next(t for t in tables if t["label"] == "T2")
    await client.patch(f"/api/v1/tables/{t2['id']}", headers=owner, json={"is_active": False})

    async with app.state.db.sessionmaker() as session:  # type: ignore[attr-defined]
        ctx = await _operator(session, restaurant.code)
        views = await table_service.list_tables(session, ctx.restaurant_id)
        page = qr_sheet.render(ctx.restaurant, views, settings)
    t1 = next(t for t in tables if t["label"] == "T1")
    assert t1["qr_url"] in page
    assert t2["qr_url"] not in page  # switched-off tables get no card
    assert "<script>" not in page
    assert "Dhaba &lt;script&gt;" in page
    assert page.count('class="card"') == 1
    # The card shrinks the code: it must scale (viewBox), not keep a fixed size and get cropped
    # (that left only one corner square visible and unscannable).
    svg = page[page.index("<svg") : page.index(">", page.index("<svg")) + 1]
    assert "viewBox=" in svg
    assert "width=" not in svg and "height=" not in svg


async def test_pilot_report_counts_orders_timings_pins_and_problems(
    app: object,
    client: httpx.AsyncClient,
    crew: dict,
    owner_conn: asyncpg.Connection,
) -> None:
    state = app.state  # type: ignore[attr-defined]
    served = await place(client, crew)
    for status in ("accepted", "preparing", "ready", "served"):
        response = await client.post(
            f"{ORDERS}/{served['id']}/transition",
            headers=crew["chef"] if status != "served" else crew["waiter"],
            json={"to_status": status, "expected_version": served["version"]},
        )
        assert response.status_code == 200, response.text
        served = response.json()
    mistake = await place(client, crew)
    await client.post(
        f"{ORDERS}/{mistake['id']}/cancel",
        headers=crew["waiter"],
        json={"reason_code": "mistake", "expected_version": mistake["version"]},
    )
    await client.post(
        "/api/v1/problem-reports",
        headers=crew["waiter"],
        json={
            "category": "customer_help",
            "note": "Guest could not find the PIN\n| table | broken |",
            "order_id": mistake["id"],
        },
    )
    qr = crew["table"]["qr_token"]
    await client.get(f"/api/v1/public/tables/{qr}")
    await usage_service.flush(state.db, state.usage)
    # Make the served order look slow: ready 30 minutes after it was placed.
    await owner_conn.execute(
        "UPDATE orders SET ready_at = created_at + interval '30 minutes', "
        "served_at = created_at + interval '32 minutes' WHERE id = $1",
        uuid.UUID(served["id"]),
    )

    async with state.db.sessionmaker() as session:
        ctx = await _operator(session, "alpha")
        report = await pilot_report.build(session, ctx.restaurant, days=7)

    assert "# Pilot report — Alpha Kitchen (alpha)" in report
    assert "- **Orders:** 2 — 0% from customers' phones, 100% entered by waiters." in report
    assert "- **Cancelled:** 1 (50%); 1 entered by mistake." in report
    assert "1 of 1 ready orders took longer than the 15-minute delay mark" in report
    assert "QR codes opened 1 times" in report
    assert "| Entered by mistake | 0 | 1 |" in report
    assert "| Customer needed help | 1 |" in report
    # Staff text stays on one line: it cannot break the report's tables.
    assert "Guest could not find the PIN / | table | broken |" in report
    assert f"order #{mistake['order_number']}, table T4" in report
    today = datetime.now(UTC).astimezone(zoneinfo.ZoneInfo("Asia/Kolkata"))
    assert f"| {today:%a %d %b} | 2 | 0 | 2 | 1 | 1 | 1 |" in report
    for money in ("₹", "price", "total", "revenue", "paise"):
        assert money not in report.lower()


async def test_operator_commands_refuse_unknown_restaurants(app: object) -> None:
    async with app.state.db.sessionmaker() as session:  # type: ignore[attr-defined]
        with pytest.raises(OperatorError, match="No restaurant"):
            await _operator(session, "nowhere")


async def test_operator_commands_find_tables_by_label(
    app: object, seed: Seeder, settings: Settings
) -> None:
    await seed.restaurant("alpha")
    await run_setup(app, settings, "alpha", {"tables": ["T1", "Family 1"]})
    async with app.state.db.sessionmaker() as session:  # type: ignore[attr-defined]
        ctx = await _operator(session, "alpha")
        tables = await table_service.list_tables(session, ctx.restaurant_id)
    assert _pick_table(tables, " family 1 ").table.label == "Family 1"
    with pytest.raises(OperatorError, match="Tables: T1, Family 1"):
        _pick_table(tables, "T9")


async def test_operator_reset_lets_a_locked_out_owner_back_in(
    app: object, client: httpx.AsyncClient, seed: Seeder, owner_conn: asyncpg.Connection
) -> None:
    await seed.restaurant("alpha")
    await seed.restaurant("beta")
    before = await login_ok(client, "alpha", "owner")
    for _ in range(5):  # forgot the password and kept guessing
        assert (await login(client, "alpha", "owner", "Wrong-Guess-123")).status_code == 401
    assert (await login(client, "alpha", "owner")).status_code == 429  # locked for 15 minutes

    password = setup_service.temporary_password("owner")
    meta = RequestMeta(ip="127.0.0.1", user_agent="test", request_id="cli-reset-password")
    async with app.state.db.sessionmaker() as session:  # type: ignore[attr-defined]
        ctx = await _operator(session, "alpha")
        with pytest.raises(NotFound):
            await staff_service.operator_reset_password(session, ctx, meta, "nobody", password)
        user = await staff_service.operator_reset_password(session, ctx, meta, "owner", password)
    assert user.must_change_password
    limiter = app.state.rate_limiter  # type: ignore[attr-defined]
    assert await forget_failed_logins(limiter, "alpha", "owner")

    assert (await login(client, "alpha", "owner")).status_code == 401  # the old password is gone
    after = await login_ok(client, "alpha", "owner", password)
    assert after["user"]["must_change_password"]  # the app asks for a new one first
    old_device = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": before["refresh_token"]}
    )
    assert old_device.status_code == 401  # devices signed in before are signed out
    await login_ok(client, "beta", "owner")  # another restaurant's owner is untouched
    audit = await owner_conn.fetchrow(
        "SELECT actor_type, actor_user_id FROM audit_logs WHERE action = 'staff.password_reset'"
    )
    assert audit["actor_type"] == "system" and audit["actor_user_id"] is None


async def test_branding_from_the_setup_file_reaches_the_guest_page(
    app: object, client: httpx.AsyncClient, seed: Seeder, settings: Settings, tmp_path: Path
) -> None:
    await seed.restaurant("alpha")
    await seed.restaurant("beta")
    owner = await staff_headers(client, "alpha", "owner")
    table = await create_table(client, owner, "T1")
    landing = f"/api/v1/public/tables/{table['qr_token']}"
    before = (await client.get(landing)).json()
    assert before["theme"] is None and before["tagline"] is None  # the default look

    branded = {"branding": {"theme": "heritage", "tagline": "Since 1995"}}
    dry = await run_setup(app, settings, "alpha", branded, dry_run=True)
    assert dry.updated == ["branding tagline: Since 1995", "branding theme: heritage"]
    assert (await client.get(landing)).json()["theme"] is None  # dry: nothing

    await run_setup(app, settings, "alpha", branded)
    after = (await client.get(landing)).json()
    assert (after["theme"], after["tagline"]) == ("heritage", "Since 1995")
    again = await run_setup(app, settings, "alpha", branded)
    assert again.updated == [] and again.unchanged == 2

    # Only what is given changes; null goes back to the default.
    await run_setup(app, settings, "alpha", {"branding": {"tagline": None}})
    after = (await client.get(landing)).json()
    assert (after["theme"], after["tagline"]) == ("heritage", None)

    # Another restaurant keeps its own look.
    beta_owner = await staff_headers(client, "beta", "owner")
    beta_table = await create_table(client, beta_owner, "T1")
    beta_landing = f"/api/v1/public/tables/{beta_table['qr_token']}"
    assert (await client.get(beta_landing)).json()["theme"] is None

    # Unknown themes, unknown keys and overlong or multi-line taglines are refused before
    # anything changes.
    two_lines = "Hi" + chr(10) + "there"
    refused = ({"theme": "neon"}, {"tagline": "x" * 81}, {"tagline": two_lines}, {"colour": "red"})
    for bad in refused:
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"branding": bad}), encoding="utf-8")
        with pytest.raises(OperatorError):
            load_setup(path)
