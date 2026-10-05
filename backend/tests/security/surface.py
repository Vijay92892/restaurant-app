"""The whole HTTP surface of the API, declared once with who may call it.

`test_surface.py` checks that this declaration matches the application exactly — a new route
that is not listed here fails the suite, so nothing ships without a decision about its
protection — and then attacks every route according to it.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum
from typing import Any

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.core.permissions import Permission
from app.core.rate_limit import Limit, Limits


class Kind(Enum):
    INFRA = "infrastructure"  # health checks, menu photos (random keys)
    ANONYMOUS = "anonymous"  # sign-in, reset, registration, QR lookup, PIN entry: rate limited
    CUSTOMER = "customer"  # needs a live table-session cookie
    SIGNED_IN = "signed in"  # any valid staff token, even before a forced password change
    STAFF = "staff"  # a staff token whose role has the listed permissions


@dataclass(frozen=True)
class Access:
    kind: Kind
    permissions: frozenset[Permission] = frozenset()
    limit: Limit | None = None  # anonymous routes: the per-IP limit that caps them


P = Permission
INFRA = Access(Kind.INFRA)
CUSTOMER = Access(Kind.CUSTOMER)
SIGNED_IN = Access(Kind.SIGNED_IN)


def anonymous(limit: Limit) -> Access:
    return Access(Kind.ANONYMOUS, limit=limit)


def staff(*permissions: Permission) -> Access:
    return Access(Kind.STAFF, frozenset(permissions))


SURFACE: dict[str, Access] = {
    # infrastructure
    "GET /": INFRA,  # development only: redirects to the API docs
    "GET /healthz": INFRA,
    "GET /readyz": INFRA,
    "GET /media/{key:path}": INFRA,
    # staff sign-in
    "POST /api/v1/auth/login": anonymous(Limits.LOGIN_PER_IP),
    "POST /api/v1/auth/refresh": anonymous(Limits.REFRESH_PER_IP),
    "POST /api/v1/auth/password-reset/request": anonymous(Limits.RESET_REQUEST_PER_IP),
    "POST /api/v1/auth/password-reset/confirm": anonymous(Limits.RESET_CONFIRM_PER_IP),
    "POST /api/v1/restaurants": anonymous(Limits.REGISTRATION_PER_IP),
    "POST /api/v1/auth/logout": SIGNED_IN,
    "POST /api/v1/auth/logout-all": SIGNED_IN,
    "GET /api/v1/auth/me": SIGNED_IN,
    "POST /api/v1/auth/change-password": SIGNED_IN,
    # restaurant
    "GET /api/v1/restaurant": staff(P.RESTAURANT_SETTINGS_READ),
    "PATCH /api/v1/restaurant": staff(P.RESTAURANT_SETTINGS_WRITE),
    "GET /api/v1/audit-logs": staff(P.AUDIT_READ),
    # staff accounts
    "GET /api/v1/staff": staff(P.STAFF_READ),
    "POST /api/v1/staff": staff(P.STAFF_MANAGE),
    "PATCH /api/v1/staff/{user_id}": staff(P.STAFF_MANAGE),
    "POST /api/v1/staff/{user_id}/reset-password": staff(P.STAFF_MANAGE),
    "DELETE /api/v1/staff/{user_id}": staff(P.STAFF_MANAGE),
    # tables, QR codes, PIN, customer sessions
    "GET /api/v1/tables": staff(P.TABLES_READ),
    "POST /api/v1/tables": staff(P.TABLES_MANAGE),
    "PATCH /api/v1/tables/{table_id}": staff(P.TABLES_MANAGE),
    "DELETE /api/v1/tables/{table_id}": staff(P.TABLES_MANAGE),
    "POST /api/v1/tables/{table_id}/qr/regenerate": staff(P.TABLES_MANAGE),
    "GET /api/v1/tables/{table_id}/qr.svg": staff(P.TABLES_READ),  # printed on the table anyway
    "POST /api/v1/tables/{table_id}/close": staff(P.TABLES_CLOSE),
    "GET /api/v1/pin": staff(P.PIN_VIEW),
    "POST /api/v1/pin/rotate": staff(P.PIN_MANAGE),
    "PUT /api/v1/pin/settings": staff(P.PIN_MANAGE),
    "POST /api/v1/table-sessions/invalidate-all": staff(P.TABLE_SESSIONS_INVALIDATE_ALL),
    # menu
    "GET /api/v1/menu": staff(P.MENU_READ),
    "GET /api/v1/menu/ordering": staff(P.MENU_READ),
    "POST /api/v1/menu/categories": staff(P.MENU_MANAGE),
    "PATCH /api/v1/menu/categories/{category_id}": staff(P.MENU_MANAGE),
    "DELETE /api/v1/menu/categories/{category_id}": staff(P.MENU_MANAGE),
    "POST /api/v1/menu/items": staff(P.MENU_MANAGE),
    "PATCH /api/v1/menu/items/{item_id}": staff(P.MENU_MANAGE),
    "DELETE /api/v1/menu/items/{item_id}": staff(P.MENU_MANAGE),
    "PATCH /api/v1/menu/items/{item_id}/availability": staff(P.MENU_AVAILABILITY),
    "PUT /api/v1/menu/items/{item_id}/modifier-groups": staff(P.MENU_MANAGE),
    "POST /api/v1/menu/items/{item_id}/image": staff(P.MENU_MANAGE),
    "DELETE /api/v1/menu/items/{item_id}/image": staff(P.MENU_MANAGE),
    "POST /api/v1/menu/modifier-groups": staff(P.MENU_MANAGE),
    "PATCH /api/v1/menu/modifier-groups/{group_id}": staff(P.MENU_MANAGE),
    "DELETE /api/v1/menu/modifier-groups/{group_id}": staff(P.MENU_MANAGE),
    "POST /api/v1/menu/modifier-groups/{group_id}/modifiers": staff(P.MENU_MANAGE),
    "PATCH /api/v1/menu/modifiers/{modifier_id}": staff(P.MENU_MANAGE),
    "DELETE /api/v1/menu/modifiers/{modifier_id}": staff(P.MENU_MANAGE),
    "PATCH /api/v1/menu/modifiers/{modifier_id}/availability": staff(P.MENU_AVAILABILITY),
    # orders (transition and cancel apply finer rules inside; see INNER_RULES)
    "GET /api/v1/orders": staff(P.ORDERS_READ),
    "GET /api/v1/orders/{order_id}": staff(P.ORDERS_READ),
    "POST /api/v1/orders": staff(P.ORDERS_CREATE),
    "POST /api/v1/orders/{order_id}/transition": staff(P.ORDERS_READ),
    "POST /api/v1/orders/{order_id}/cancel": staff(P.ORDERS_READ),
    "POST /api/v1/orders/{order_id}/cancellation-request": staff(P.ORDERS_CANCEL_REQUEST),
    "POST /api/v1/orders/{order_id}/cancellation-request/{decision}": staff(P.ORDERS_CANCEL_REVIEW),
    "GET /api/v1/kitchen/board": staff(P.ORDERS_READ),
    # pilot observation
    "POST /api/v1/problem-reports": staff(P.PROBLEM_REPORTS_CREATE),
    "GET /api/v1/problem-reports": staff(P.PROBLEM_REPORTS_READ),
    # customers (QR -> PIN -> session cookie)
    "GET /api/v1/public/tables/{qr_token}": anonymous(Limits.QR_LOOKUP_PER_IP),
    "POST /api/v1/public/tables/{qr_token}/session": anonymous(Limits.PIN_ATTEMPTS_PER_IP),
    "GET /api/v1/public/menu": CUSTOMER,
    "POST /api/v1/public/orders": CUSTOMER,
    "GET /api/v1/public/orders": CUSTOMER,
    "POST /api/v1/public/orders/{order_id}/cancel": CUSTOMER,
    "GET /api/v1/public/session": CUSTOMER,
    "DELETE /api/v1/public/session": CUSTOMER,
}

# The one WebSocket route; tests/test_realtime.py attacks it.
WEBSOCKETS = {"/api/v1/ws/staff"}


# --- reading the application ----------------------------------------------------------------------


def walk_routes(app: FastAPI) -> Iterator[tuple[str, Any]]:
    """(full path, route) for every route, through nested routers."""

    def walk(routes: list[Any], prefix: str) -> Iterator[tuple[str, Any]]:
        for route in routes:
            if hasattr(route, "original_router"):  # an included router
                yield from walk(route.original_router.routes, prefix + route.include_context.prefix)
            else:
                yield prefix + route.path, route

    yield from walk(app.routes, "")


def http_routes(app: FastAPI) -> dict[str, APIRoute]:
    return {
        f"{method} {path}": route
        for path, route in walk_routes(app)
        if isinstance(route, APIRoute)
        for method in route.methods
        if method != "HEAD"
    }


def _dependencies(dependant: Dependant) -> Iterator[Dependant]:
    for sub in dependant.dependencies:
        yield sub
        yield from _dependencies(sub)


def declared_access(route: APIRoute) -> Access | None:
    """What the route's dependencies actually demand (None: nothing staff/customer related)."""
    names = set()
    permissions: set[Permission] = set()
    for sub in _dependencies(route.dependant):
        call = sub.call
        name = getattr(call, "__name__", "")
        names.add(name)
        if name == "checker":  # app.api.deps.require(...)
            permissions |= set(inspect.getclosurevars(call).nonlocals["permissions"])
    if permissions:
        return staff(*permissions)
    if "current_staff_allow_password_change" in names:
        return SIGNED_IN
    if "current_customer" in names:
        return CUSTOMER
    return None


# --- concrete requests for every route ---------------------------------------------------------

# A tiny valid PNG for the photo upload route.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478"
    "da63f8cfc0f01f0005000201f5a0d2a50000000049454e44ae426082"
)


def fill(path: str, ids: dict[str, str]) -> str:
    """Put real (or random) ids into a route's path."""
    for name in (
        "user_id",
        "table_id",
        "category_id",
        "item_id",
        "group_id",
        "modifier_id",
        "order_id",
        "qr_token",
    ):
        path = path.replace("{" + name + "}", ids.get(name, str(uuid.uuid4())))
    return path.replace("{decision}", "approve").replace("{key:path}", "missing.jpg")


def body_for(key: str, ids: dict[str, str]) -> dict[str, Any] | None:
    """A body that passes validation, so the request reaches the authorization checks inside
    the handler. `ids` are the caller's own ids for anything the body refers to."""
    bodies: dict[str, Callable[[], dict[str, Any]]] = {
        "PATCH /api/v1/restaurant": lambda: {"kitchen_delay_minutes": 20},
        "POST /api/v1/staff": lambda: {
            "username": f"new{uuid.uuid4().hex[:8]}",
            "display_name": "New",
            "role": "waiter",
            "temporary_password": "Temp-Pass-2026-zz",
        },
        "PATCH /api/v1/staff/{user_id}": lambda: {"display_name": "Renamed"},
        "POST /api/v1/staff/{user_id}/reset-password": lambda: {
            "temporary_password": "Temp-Pass-2026-zz"
        },
        "POST /api/v1/tables": lambda: {"label": f"N{uuid.uuid4().hex[:6]}"},
        "PATCH /api/v1/tables/{table_id}": lambda: {"label": f"R{uuid.uuid4().hex[:6]}"},
        "PUT /api/v1/pin/settings": lambda: {"rotation_minutes": 240, "pin_length": 4},
        "POST /api/v1/menu/categories": lambda: {"name": f"Cat {uuid.uuid4().hex[:6]}"},
        "PATCH /api/v1/menu/categories/{category_id}": lambda: {"name": "Renamed"},
        "POST /api/v1/menu/items": lambda: {"category_id": ids["category_id"], "name": "New dish"},
        "PATCH /api/v1/menu/items/{item_id}": lambda: {"name": "Renamed"},
        "PATCH /api/v1/menu/items/{item_id}/availability": lambda: {"is_available": False},
        "PUT /api/v1/menu/items/{item_id}/modifier-groups": lambda: {
            "group_ids": [ids["group_id"]]
        },
        "POST /api/v1/menu/modifier-groups": lambda: {"name": "Sides"},
        "PATCH /api/v1/menu/modifier-groups/{group_id}": lambda: {"name": "Renamed"},
        "POST /api/v1/menu/modifier-groups/{group_id}/modifiers": lambda: {"name": "Raita"},
        "PATCH /api/v1/menu/modifiers/{modifier_id}": lambda: {"name": "Renamed"},
        "PATCH /api/v1/menu/modifiers/{modifier_id}/availability": lambda: {"is_available": False},
        "POST /api/v1/orders": lambda: {
            "table_id": ids["table_id"],
            "items": [{"menu_item_id": ids["item_id"], "quantity": 1}],
        },
        "POST /api/v1/orders/{order_id}/transition": lambda: {
            "to_status": "accepted",
            "expected_version": 1,
        },
        "POST /api/v1/orders/{order_id}/cancel": lambda: {
            "reason_code": "other",
            "expected_version": 1,
        },
        "POST /api/v1/orders/{order_id}/cancellation-request": lambda: {"reason": "Out of stock"},
        "POST /api/v1/problem-reports": lambda: {
            "category": "order_mistake",
            "note": "Wrong dish",
            "order_id": ids["order_id"],
        },
        "POST /api/v1/orders/{order_id}/cancellation-request/{decision}": lambda: {},
        "POST /api/v1/public/orders": lambda: {
            "items": [{"menu_item_id": ids["item_id"], "quantity": 1}]
        },
        "POST /api/v1/public/orders/{order_id}/cancel": lambda: {},
        "POST /api/v1/tables/{table_id}/qr/regenerate": lambda: {},
        "POST /api/v1/tables/{table_id}/close": lambda: {},
        "POST /api/v1/pin/rotate": lambda: {},
        "POST /api/v1/table-sessions/invalidate-all": lambda: {},
        "POST /api/v1/auth/change-password": lambda: {
            "current_password": "Not-The-Password-1",
            "new_password": "New-Pass-2026-xyz",
        },
    }
    make = bodies.get(key)
    return make() if make else None
