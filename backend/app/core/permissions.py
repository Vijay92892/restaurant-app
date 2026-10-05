"""Role → permission mapping. The single source of truth for staff authorization.

The Flutter app receives the permission list only to decide what to *show*; every endpoint
re-checks permissions server-side.
"""

from __future__ import annotations

from enum import StrEnum

from app.models.enums import Role


class Permission(StrEnum):
    RESTAURANT_SETTINGS_READ = "restaurant.settings.read"
    RESTAURANT_SETTINGS_WRITE = "restaurant.settings.write"
    STAFF_READ = "staff.read"
    STAFF_MANAGE = "staff.manage"
    STAFF_MANAGE_ADMINS = "staff.manage_admins"
    TABLES_READ = "tables.read"
    TABLES_MANAGE = "tables.manage"
    TABLES_CLOSE = "tables.close"
    PIN_VIEW = "pin.view"
    PIN_MANAGE = "pin.manage"
    TABLE_SESSIONS_INVALIDATE_ALL = "table_sessions.invalidate_all"
    MENU_READ = "menu.read"
    MENU_MANAGE = "menu.manage"
    MENU_AVAILABILITY = "menu.availability"
    ORDERS_READ = "orders.read"
    ORDERS_CREATE = "orders.create"
    ORDERS_KITCHEN_TRANSITION = "orders.kitchen_transition"
    ORDERS_SERVE = "orders.serve"
    ORDERS_CANCEL = "orders.cancel"
    ORDERS_CANCEL_PRIVILEGED = "orders.cancel_privileged"
    ORDERS_CANCEL_REQUEST = "orders.cancel_request"
    ORDERS_CANCEL_REVIEW = "orders.cancel_review"
    AUDIT_READ = "audit.read"
    PROBLEM_REPORTS_CREATE = "problem_reports.create"
    PROBLEM_REPORTS_READ = "problem_reports.read"


P = Permission

_KITCHEN = frozenset(
    {
        P.RESTAURANT_SETTINGS_READ,
        P.TABLES_READ,
        P.MENU_READ,
        P.MENU_AVAILABILITY,
        P.ORDERS_READ,
        P.ORDERS_KITCHEN_TRANSITION,
        P.ORDERS_CANCEL_REQUEST,
        P.PROBLEM_REPORTS_CREATE,
    }
)

_WAITER = frozenset(
    {
        P.RESTAURANT_SETTINGS_READ,
        P.TABLES_READ,
        P.TABLES_CLOSE,
        P.PIN_VIEW,
        P.MENU_READ,
        P.ORDERS_READ,
        P.ORDERS_CREATE,
        P.ORDERS_SERVE,
        P.ORDERS_CANCEL,
        P.ORDERS_CANCEL_REQUEST,
        P.PROBLEM_REPORTS_CREATE,
    }
)

_ADMIN = (
    _KITCHEN
    | _WAITER
    | frozenset(
        {
            P.RESTAURANT_SETTINGS_WRITE,
            P.STAFF_READ,
            P.STAFF_MANAGE,
            P.TABLES_MANAGE,
            P.PIN_MANAGE,
            P.TABLE_SESSIONS_INVALIDATE_ALL,
            P.MENU_MANAGE,
            P.ORDERS_CANCEL_PRIVILEGED,
            P.ORDERS_CANCEL_REVIEW,
            P.AUDIT_READ,
            P.PROBLEM_REPORTS_READ,
        }
    )
)

_OWNER = _ADMIN | frozenset({P.STAFF_MANAGE_ADMINS})

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OWNER: _OWNER,
    Role.ADMIN: _ADMIN,
    Role.WAITER: _WAITER,
    Role.KITCHEN: _KITCHEN,
}

# Used by staff management to prevent granting a role above one's own.
ROLE_RANK: dict[Role, int] = {Role.OWNER: 3, Role.ADMIN: 2, Role.WAITER: 1, Role.KITCHEN: 1}


def permissions_for(role: str) -> frozenset[Permission]:
    try:
        return ROLE_PERMISSIONS[Role(role)]
    except ValueError:
        return frozenset()  # unknown role ⇒ no permissions (fail closed)
