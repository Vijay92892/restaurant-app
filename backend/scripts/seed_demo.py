"""Create a ready-to-try demo restaurant (development only).

    python scripts/seed_demo.py                         # code "demo-dhaba", random password
    python scripts/seed_demo.py --code my-test --password "Some-Pass-2026" --json

Creates: owner + one waiter ("waiter") + one kitchen account ("chef"), six tables, a small
Indian menu with option groups, and prints the logins, the table PIN and the table QR links.
Uses DATABASE_URL (the runtime role), exactly like the API. Refuses to run in production.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.core.net import RequestMeta  # noqa: E402
from app.core.permissions import permissions_for  # noqa: E402
from app.core.pin import PinCrypto  # noqa: E402
from app.core.security import passwords  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.models import User  # noqa: E402
from app.services import menu_service, pin_service, table_service  # noqa: E402
from app.services.auth_service import StaffContext  # noqa: E402
from app.services.restaurant_service import create_restaurant_with_owner  # noqa: E402

MENU = {
    "Starters": [
        (
            "Paneer Tikka",
            "veg",
            24900,
            "Char-grilled cottage cheese, mint chutney",
            ["Spice level", "Extras"],
        ),
        (
            "Chicken 65",
            "non_veg",
            27900,
            "Crispy, spicy fried chicken with curry leaves",
            ["Spice level"],
        ),
        ("Veg Spring Roll", "veg", 17900, None, []),
    ],
    "Main course": [
        ("Dal Makhani", "veg", 22900, "Black lentils slow-cooked with butter", ["Spice level"]),
        ("Butter Chicken", "non_veg", 32900, None, ["Spice level"]),
        ("Egg Curry", "egg", 19900, None, ["Spice level"]),
    ],
    "Breads": [
        ("Butter Naan", "veg", 6000, None, []),
        ("Garlic Naan", "veg", 7000, None, []),
        ("Tandoori Roti", "veg", 3000, None, []),
    ],
    "Drinks": [
        ("Masala Chai", "veg", 4000, None, []),
        ("Sweet Lassi", "veg", 9000, None, ["Size"]),
    ],
}
GROUPS = {
    "Spice level": (1, 1, [("Mild", None), ("Medium", None), ("Hot", None)]),
    "Extras": (0, 2, [("Extra cheese", 4000), ("Mint chutney", 2000)]),
    "Size": (1, 1, [("Regular", None), ("Large", 4000)]),
}


async def seed(code: str, name: str, password: str) -> dict[str, object]:
    settings = get_settings()
    if settings.is_production:
        raise SystemExit("seed_demo.py is for development only")
    passwords.configure(settings)
    crypto = PinCrypto(settings)
    db = Database(settings)
    meta = RequestMeta(ip="127.0.0.1", user_agent="seed_demo", request_id=None)
    try:
        async with db.sessionmaker() as s:
            restaurant, owner = await create_restaurant_with_owner(
                s,
                crypto,
                code=code,
                name=name,
                owner_username="owner",
                owner_display_name="Asha (owner)",
                owner_email=None,
                owner_password=password,
                source="demo_seed",
            )
            for username, display, role in (
                ("waiter", "Ravi", "waiter"),
                ("chef", "Chef Meena", "kitchen"),
            ):
                s.add(
                    User(
                        id=uuid.uuid4(),
                        restaurant_id=restaurant.id,
                        username=username,
                        display_name=display,
                        role=role,
                        password_hash=await passwords.hash(password),
                    )
                )
            await s.commit()
            ctx = StaffContext(
                user=owner,
                restaurant=restaurant,
                session_id=uuid.uuid4(),
                permissions=permissions_for("owner"),
            )
            group_ids: dict[str, uuid.UUID] = {}
            for group_name, (low, high, options) in GROUPS.items():
                group = await menu_service.create_group(
                    s,
                    ctx,
                    meta,
                    {"name": group_name, "min_select": low, "max_select": high, "sort_order": None},
                )
                group_ids[group_name] = group.id
                for option, delta in options:
                    await menu_service.create_modifier(
                        s,
                        ctx,
                        meta,
                        group.id,
                        {
                            "name": option,
                            "display_price_delta_paise": delta,
                            "sort_order": None,
                            "is_available": True,
                        },
                    )
            for category_name, items in MENU.items():
                category = await menu_service.create_category(
                    s,
                    ctx,
                    meta,
                    {
                        "name": category_name,
                        "description": None,
                        "sort_order": None,
                        "is_active": True,
                    },
                )
                for item_name, dietary, price, description, groups in items:
                    item = await menu_service.create_item(
                        s,
                        ctx,
                        meta,
                        {
                            "category_id": category.id,
                            "name": item_name,
                            "description": description,
                            "dietary": dietary,
                            "display_price_paise": price,
                            "sort_order": None,
                            "is_active": True,
                            "is_available": True,
                        },
                    )
                    if groups:
                        await menu_service.set_item_modifier_groups(
                            s, ctx, meta, item.id, [group_ids[g] for g in groups]
                        )
            tables = []
            for index in range(1, 7):
                table = await table_service.create_table(
                    s, ctx, settings, meta, label=f"T{index}", sort_order=index
                )
                tables.append(table)
            pin = pin_service.describe(
                crypto, restaurant, await pin_service.ensure_current_pin(s, crypto, restaurant.id)
            ).pin
            qr_links = {
                t.label: table_service.qr_url(
                    settings, await table_service.live_qr_token(s, restaurant.id, t.id)
                )
                for t in tables
            }
            await s.commit()
    finally:
        await db.dispose()
    return {
        "restaurant_code": code,
        "password": password,
        "logins": {"owner": "owner", "waiter": "waiter", "kitchen": "chef"},
        "table_pin": pin,
        "qr_links": qr_links,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", default="demo-dhaba")
    parser.add_argument("--name", default="Demo Dhaba")
    parser.add_argument(
        "--password", help="shared password for the demo accounts (default: random)"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()
    password = args.password or f"Demo-{secrets.token_urlsafe(9)}"
    try:
        result = asyncio.run(seed(args.code, args.name, password))
    except AppError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result))
        return 0
    print(f"\nDemo restaurant ready — restaurant code: {result['restaurant_code']}")
    print(f"  Password for all demo accounts: {password}")
    print("  Usernames: owner (owner), waiter (waiter), chef (kitchen)")
    print(f"  Current table PIN: {result['table_pin']}")
    print("  Customer QR links:")
    for label, link in result["qr_links"].items():  # type: ignore[union-attr]
        print(f"    {label}: {link}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
