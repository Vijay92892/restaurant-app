from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    WAITER = "waiter"
    KITCHEN = "kitchen"


class RestaurantStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class SessionRevokeReason(StrEnum):
    LOGOUT = "logout"
    LOGOUT_ALL = "logout_all"
    PASSWORD_CHANGED = "password_changed"
    PASSWORD_RESET = "password_reset"
    REFRESH_TOKEN_REUSE = "refresh_token_reuse"
    USER_DEACTIVATED = "user_deactivated"
    ADMIN_RESET = "admin_reset"
    USER_DELETED = "user_deleted"


class PinRotationReason(StrEnum):
    INITIAL = "initial"
    MANUAL = "manual"
    SCHEDULED = "scheduled"


class TableSessionStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    CLOSED = "closed"


class TableSessionEndReason(StrEnum):
    INVALIDATED_BY_STAFF = "invalidated_by_staff"
    PIN_ROTATED = "pin_rotated"
    TABLE_CLOSED = "table_closed"
    CUSTOMER_LEFT = "customer_left"
    TABLE_DISABLED = "table_disabled"


class Dietary(StrEnum):
    VEG = "veg"
    NON_VEG = "non_veg"
    EGG = "egg"


class OrderSource(StrEnum):
    CUSTOMER = "customer"
    WAITER = "waiter"


class OrderStatus(StrEnum):
    PLACED = "placed"
    ACCEPTED = "accepted"
    PREPARING = "preparing"
    READY = "ready"
    SERVED = "served"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class CancelReasonCode(StrEnum):
    CUSTOMER_CHANGED_MIND = "customer_changed_mind"
    ITEM_UNAVAILABLE = "item_unavailable"
    DUPLICATE = "duplicate"
    KITCHEN_ISSUE = "kitchen_issue"
    MISTAKE = "mistake"  # the order was entered wrongly (a pilot observation category too)
    OTHER = "other"


class ProblemCategory(StrEnum):
    """What a staff problem report is about (pilot observation, spec §48 Phase 11)."""

    ORDER_MISTAKE = "order_mistake"
    CUSTOMER_HELP = "customer_help"  # a guest needed help with the QR code, PIN or menu
    APP_CONFUSING = "app_confusing"
    NETWORK = "network"
    SLOW = "slow"
    OTHER = "other"


class ActorType(StrEnum):
    STAFF = "staff"
    CUSTOMER = "customer"
    SYSTEM = "system"
    ANONYMOUS = "anonymous"
