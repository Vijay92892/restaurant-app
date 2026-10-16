"""Restaurant setup from one file: tables, staff accounts, option groups and the menu (an operator
tool for onboarding a restaurant, such as the pilot restaurant).

    python -m app.cli apply-setup --code spice-garden --file setup.json [--dry-run]

Re-runnable: everything is matched by name (case-insensitive) and only what is missing is
created, or what differs updated. Nothing is ever deleted or renamed, and sold-out switches are
left alone (they belong to the kitchen during service). Every change goes through the same
services, validation and audit log as the app, recorded under the owner's account with request
id ``cli-apply-setup``. New staff get a random temporary password, shown once, which they must
change when they first sign in.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import InvalidInput
from app.core.net import RequestMeta
from app.core.security import validate_new_password
from app.schemas.common import DisplayName, RequestModel, TableLabel, Username, plain_text
from app.schemas.menu import (
    CategoryDescription,
    CategoryName,
    Dietary,
    GroupName,
    ItemDescription,
    ItemName,
    OptionName,
    SelectCount,
)
from app.services import menu_service, restaurant_service, staff_service, table_service
from app.services.auth_service import StaffContext

META = RequestMeta(ip="127.0.0.1", user_agent="app.cli apply-setup", request_id="cli-apply-setup")

# Prices are menu information only (spec §37), written in rupees: 249 or 249.50.
# Written as JSON numbers, so parsed leniently (the request models are strict otherwise).
Rupees = Annotated[Decimal, Field(strict=False, ge=0, le=1_000_000, decimal_places=2)]
RupeesDelta = Annotated[Decimal, Field(strict=False, ge=-1_000_000, le=1_000_000, decimal_places=2)]


def _paise(rupees: Decimal | None) -> int | None:
    return None if rupees is None else int(rupees * 100)


def _option_shorthand(value: object) -> object:
    return {"name": value} if isinstance(value, str) else value


class SetupOption(RequestModel):
    name: OptionName  # type: ignore[valid-type]
    price: RupeesDelta | None = None  # added to the dish's price, shown as "+₹40"


class SetupGroup(RequestModel):
    name: GroupName  # type: ignore[valid-type]
    min: SelectCount = 0
    max: Annotated[int, Field(ge=1, le=20)] = 1
    options: Annotated[
        list[Annotated[SetupOption, BeforeValidator(_option_shorthand)]],
        Field(min_length=1, max_length=menu_service.MAX_MODIFIERS_PER_GROUP),
    ]

    @model_validator(mode="after")
    def _check(self) -> SetupGroup:
        if self.min > self.max:
            raise ValueError(f"{self.name}: min cannot exceed max")
        if self.min > len(self.options):
            raise ValueError(f"{self.name}: min is larger than the number of options")
        _unique([o.name for o in self.options], f"options of {self.name}")
        return self


class SetupDish(RequestModel):
    name: ItemName  # type: ignore[valid-type]
    type: Dietary | None = None  # veg / non_veg / egg
    price: Rupees | None = None
    description: ItemDescription | None = None  # type: ignore[valid-type]
    options: Annotated[list[GroupName], Field(max_length=20)] | None = None  # type: ignore[valid-type]
    active: bool = True  # false hides the dish from the menu


class SetupCategory(RequestModel):
    category: CategoryName  # type: ignore[valid-type]
    description: CategoryDescription | None = None  # type: ignore[valid-type]
    dishes: Annotated[list[SetupDish], Field(max_length=menu_service.MAX_ITEMS)] = []

    @model_validator(mode="after")
    def _check(self) -> SetupCategory:
        _unique([d.name for d in self.dishes], f"dishes in {self.category}")
        return self


class SetupStaff(RequestModel):
    username: Username
    name: DisplayName  # type: ignore[valid-type]
    role: Literal["admin", "waiter", "kitchen"]


# The ordering page's built-in themes (customer-web/src/styles.css, [data-theme=...]).
Theme = Literal["heritage"]
Tagline = plain_text(1, 80)


class SetupBranding(RequestModel):
    # Only what is given changes; null switches back to the default look / removes the tagline.
    theme: Theme | None = None
    tagline: Tagline | None = None  # type: ignore[valid-type]


class SetupFile(RequestModel):
    branding: SetupBranding | None = None
    tables: Annotated[list[TableLabel], Field(max_length=5000)] = []  # type: ignore[valid-type]
    staff: Annotated[list[SetupStaff], Field(max_length=500)] = []
    option_groups: Annotated[list[SetupGroup], Field(max_length=menu_service.MAX_GROUPS)] = []
    menu: Annotated[list[SetupCategory], Field(max_length=menu_service.MAX_CATEGORIES)] = []

    @model_validator(mode="after")
    def _check(self) -> SetupFile:
        _unique(list(self.tables), "tables")
        _unique([s.username for s in self.staff], "staff usernames")
        _unique([g.name for g in self.option_groups], "option groups")
        _unique([c.category for c in self.menu], "categories")
        return self


def _unique(names: list[str], what: str) -> None:
    seen: set[str] = set()
    for name in names:
        if name.casefold() in seen:
            raise ValueError(f"{what}: {name!r} appears twice")
        seen.add(name.casefold())


@dataclass
class SetupResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: int = 0
    # (username, role, temporary password): shown once by the CLI, never stored in clear.
    new_staff: list[tuple[str, str, str]] = field(default_factory=list)


_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i look-alikes


def temporary_password(username: str) -> str:
    """Three groups of four random characters (about 59 bits), easy to read out and type."""
    while True:
        groups = ["".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(4)) for _ in range(3)]
        password = "-".join(groups)
        try:
            validate_new_password(password, username=username)
        except InvalidInput:
            continue
        return password


def _key(name: str) -> str:
    return name.casefold()


async def apply(
    db: AsyncSession,
    ctx: StaffContext,
    settings: Settings,
    setup: SetupFile,
    *,
    dry_run: bool = False,
) -> SetupResult:
    result = SetupResult()
    menu = await menu_service.load_menu(db, ctx.restaurant_id)

    # Check every dish's option groups before changing anything.
    known_groups = {_key(g.name) for g in menu.groups} | {_key(g.name) for g in setup.option_groups}
    for category in setup.menu:
        for dish in category.dishes:
            for name in dish.options or []:
                if _key(name) not in known_groups:
                    raise InvalidInput(
                        f"{dish.name}: option group {name!r} is not defined in option_groups."
                    )

    await _branding(db, ctx, setup, result, dry_run)
    await _tables(db, ctx, settings, setup, result, dry_run)
    await _staff(db, ctx, setup, result, dry_run)
    groups = await _groups(db, ctx, menu, setup, result, dry_run)
    await _menu(db, ctx, menu, groups, setup, result, dry_run)
    return result


async def _branding(
    db: AsyncSession, ctx: StaffContext, setup: SetupFile, result: SetupResult, dry_run: bool
) -> None:
    if setup.branding is None:
        return
    wanted = {name: getattr(setup.branding, name) for name in setup.branding.model_fields_set}
    changes = {k: v for k, v in wanted.items() if getattr(ctx.restaurant, k) != v}
    result.unchanged += len(wanted) - len(changes)
    for name in sorted(changes):
        result.updated.append(f"branding {name}: {changes[name] or '(default)'}")
    if changes and not dry_run:
        await restaurant_service.update_settings(db, ctx, META, changes)


async def _tables(
    db: AsyncSession,
    ctx: StaffContext,
    settings: Settings,
    setup: SetupFile,
    result: SetupResult,
    dry_run: bool,
) -> None:
    existing = {
        _key(view.table.label) for view in await table_service.list_tables(db, ctx.restaurant_id)
    }
    for position, label in enumerate(setup.tables):
        if _key(label) in existing:
            result.unchanged += 1
            continue
        if not dry_run:
            await table_service.create_table(
                db, ctx, settings, META, label=label, sort_order=position
            )
        result.created.append(f"table {label}")


async def _staff(
    db: AsyncSession, ctx: StaffContext, setup: SetupFile, result: SetupResult, dry_run: bool
) -> None:
    existing = {u.username for u in await staff_service.list_staff(db, ctx.restaurant_id)}
    for person in setup.staff:
        if person.username in existing:
            result.unchanged += 1  # existing accounts are managed in the app, not from here
            continue
        password = temporary_password(person.username)
        if not dry_run:
            await staff_service.create_staff(
                db,
                ctx,
                META,
                username=person.username,
                display_name=person.name,
                role=person.role,
                email=None,
                temporary_password=password,
            )
            result.new_staff.append((person.username, person.role, password))
        result.created.append(f"{person.role} {person.username}")


async def _groups(
    db: AsyncSession,
    ctx: StaffContext,
    menu: menu_service.MenuSnapshot,
    setup: SetupFile,
    result: SetupResult,
    dry_run: bool,
) -> dict[str, uuid.UUID]:
    """Creates/updates option groups; returns {casefolded name: id} of every live group."""
    ids = {_key(g.name): g.id for g in menu.groups}
    by_name = {_key(g.name): g for g in menu.groups}
    for spec in setup.option_groups:
        group = by_name.get(_key(spec.name))
        if group is None:
            result.created.append(f"option group {spec.name} ({len(spec.options)} options)")
            if dry_run:
                ids[_key(spec.name)] = uuid.uuid4()  # placeholder: nothing is written
                continue
            group = await menu_service.create_group(
                db,
                ctx,
                META,
                {"name": spec.name, "min_select": spec.min, "max_select": spec.max},
            )
            ids[_key(spec.name)] = group.id
            for option in spec.options:
                await menu_service.create_modifier(db, ctx, META, group.id, _option_data(option))
            continue

        changed = False
        if (group.min_select, group.max_select) != (spec.min, spec.max):
            if not dry_run:
                await menu_service.update_group(
                    db, ctx, META, group.id, {"min_select": spec.min, "max_select": spec.max}
                )
            changed = True
        options = {_key(m.name): m for m in menu.modifiers_by_group.get(group.id, [])}
        for option in spec.options:
            current = options.get(_key(option.name))
            if current is None:
                if not dry_run:
                    await menu_service.create_modifier(
                        db, ctx, META, group.id, _option_data(option)
                    )
                changed = True
            elif "price" in option.model_fields_set and current.display_price_delta_paise != (
                _paise(option.price)
            ):
                if not dry_run:
                    await menu_service.update_modifier(
                        db,
                        ctx,
                        META,
                        current.id,
                        {"display_price_delta_paise": _paise(option.price)},
                    )
                changed = True
        if changed:
            result.updated.append(f"option group {spec.name}")
        else:
            result.unchanged += 1
    return ids


def _option_data(option: SetupOption) -> dict[str, Any]:
    return {
        "name": option.name,
        "display_price_delta_paise": _paise(option.price),
        "sort_order": None,
        "is_available": True,
    }


async def _menu(
    db: AsyncSession,
    ctx: StaffContext,
    menu: menu_service.MenuSnapshot,
    groups: dict[str, uuid.UUID],
    setup: SetupFile,
    result: SetupResult,
    dry_run: bool,
) -> None:
    categories = {_key(c.name): c for c in menu.categories}
    for spec in setup.menu:
        category = categories.get(_key(spec.category))
        if category is None:
            result.created.append(f"category {spec.category}")
            if dry_run:
                result.created.extend(f"dish {d.name}" for d in spec.dishes)
                continue
            category = await menu_service.create_category(
                db,
                ctx,
                META,
                {"name": spec.category, "description": spec.description, "sort_order": None},
            )
        elif "description" in spec.model_fields_set and category.description != spec.description:
            if not dry_run:
                await menu_service.update_category(
                    db, ctx, META, category.id, {"description": spec.description}
                )
            result.updated.append(f"category {spec.category}")
        else:
            result.unchanged += 1

        dishes = {_key(i.name): i for i in menu.items if i.category_id == category.id}
        for dish in spec.dishes:
            await _dish(db, ctx, menu, groups, category.id, dishes, dish, result, dry_run)


async def _dish(
    db: AsyncSession,
    ctx: StaffContext,
    menu: menu_service.MenuSnapshot,
    groups: dict[str, uuid.UUID],
    category_id: uuid.UUID,
    dishes: dict[str, Any],
    dish: SetupDish,
    result: SetupResult,
    dry_run: bool,
) -> None:
    wanted = {
        "description": dish.description,
        "dietary": dish.type,
        "display_price_paise": _paise(dish.price),
        "is_active": dish.active,
    }
    given = {"description", "type", "price", "active"} & dish.model_fields_set
    fields = {"description": "description", "type": "dietary", "price": "display_price_paise"}
    fields["active"] = "is_active"
    group_ids = [groups[_key(name)] for name in dish.options or []]

    item = dishes.get(_key(dish.name))
    if item is None:
        result.created.append(f"dish {dish.name}")
        if dry_run:
            return
        item = await menu_service.create_item(
            db,
            ctx,
            META,
            {"category_id": category_id, "name": dish.name, "sort_order": None, **wanted},
        )
        if group_ids:
            await menu_service.set_item_modifier_groups(db, ctx, META, item.id, group_ids)
        return

    changes = {
        fields[name]: wanted[fields[name]]
        for name in given
        if getattr(item, fields[name]) != wanted[fields[name]]
    }
    regroup = dish.options is not None and menu.groups_by_item.get(item.id, []) != group_ids
    if not changes and not regroup:
        result.unchanged += 1
        return
    result.updated.append(f"dish {dish.name}")
    if dry_run:
        return
    if changes:
        await menu_service.update_item(db, ctx, META, item.id, changes)
    if regroup:
        await menu_service.set_item_modifier_groups(db, ctx, META, item.id, group_ids)
