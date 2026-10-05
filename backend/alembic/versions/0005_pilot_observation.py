"""Pilot observation: staff problem reports, daily usage counts, and a cancellation reason for
orders entered by mistake.

* ``problem_reports``: a staff member's note that something went wrong during service (an order
  mistake, a customer who needed help, a confusing screen, a Wi-Fi drop). Free text written by
  staff, bounded, optionally linked to one order of the same restaurant.
* ``usage_counters``: per restaurant, per local day, how many times each step of the customer
  journey (QR opened, PIN accepted / wrong / locked) and of the staff connection (connected,
  lost) happened. Counts only: no IP addresses, devices, sessions or people.
* ``orders.cancel_reason_code`` gains ``mistake`` ("entered by mistake"), so order mistakes can
  be told apart from guests changing their minds.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-05
"""

import re
from collections.abc import Sequence

from alembic import context, op
from app.core.config import get_settings

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REASON_CODES_BEFORE = (
    "customer_changed_mind",
    "item_unavailable",
    "duplicate",
    "kitchen_issue",
    "other",
)
REASON_CODES_AFTER = (*REASON_CODES_BEFORE[:4], "mistake", "other")

STATEMENTS = (
    """
    CREATE TABLE problem_reports (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      restaurant_id uuid NOT NULL REFERENCES restaurants (id),
      reported_by_user_id uuid NOT NULL,
      category text NOT NULL,
      note text,
      order_id uuid,
      created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT fk_problem_reports_user FOREIGN KEY (reported_by_user_id, restaurant_id)
        REFERENCES users (id, restaurant_id),
      CONSTRAINT fk_problem_reports_order FOREIGN KEY (order_id, restaurant_id)
        REFERENCES orders (id, restaurant_id),
      CONSTRAINT ck_problem_reports_category CHECK (category IN (
        'order_mistake', 'customer_help', 'app_confusing', 'network', 'slow', 'other')),
      CONSTRAINT ck_problem_reports_note CHECK (note IS NULL OR char_length(note) BETWEEN 1 AND 500)
    )
    """,
    "CREATE INDEX ix_problem_reports_recent ON problem_reports (restaurant_id, created_at DESC)",
    "CREATE INDEX ix_problem_reports_order ON problem_reports (order_id) "
    "WHERE order_id IS NOT NULL",
    """
    CREATE TABLE usage_counters (
      restaurant_id uuid NOT NULL REFERENCES restaurants (id),
      day date NOT NULL,
      metric text NOT NULL,
      count bigint NOT NULL,
      CONSTRAINT pk_usage_counters PRIMARY KEY (restaurant_id, day, metric),
      CONSTRAINT ck_usage_counters_metric CHECK (metric ~ '^[a-z_]{1,40}$'),
      CONSTRAINT ck_usage_counters_count CHECK (count >= 0)
    )
    """,
)

TABLES = ("problem_reports", "usage_counters")
GRANTS = {
    # Reports are append-only for the API; the retention job deletes old ones.
    "problem_reports": "SELECT, INSERT, DELETE",
    "usage_counters": "SELECT, INSERT, UPDATE",
}


def _app_role() -> str:
    role = context.config.attributes.get("app_db_role") or get_settings().app_db_role
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError("APP_DB_ROLE must be a plain lowercase identifier")
    return role


def _reason_check(codes: tuple[str, ...]) -> str:
    values = ", ".join(f"'{code}'" for code in codes)
    return (
        "ALTER TABLE orders ADD CONSTRAINT ck_orders_cancel_reason_code CHECK ("
        f"cancel_reason_code IS NULL OR cancel_reason_code IN ({values}))"
    )


def upgrade() -> None:
    bind = op.get_bind()
    app_role = _app_role()
    for statement in STATEMENTS:
        bind.exec_driver_sql(statement)
    for table in TABLES:
        bind.exec_driver_sql(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        bind.exec_driver_sql(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (restaurant_id = app_current_restaurant_id()) "
            "WITH CHECK (restaurant_id = app_current_restaurant_id())"
        )
        bind.exec_driver_sql(f'GRANT {GRANTS[table]} ON {table} TO "{app_role}"')
    bind.exec_driver_sql("ALTER TABLE orders DROP CONSTRAINT ck_orders_cancel_reason_code")
    bind.exec_driver_sql(_reason_check(REASON_CODES_AFTER))


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("ALTER TABLE orders DROP CONSTRAINT ck_orders_cancel_reason_code")
    bind.exec_driver_sql(
        "UPDATE orders SET cancel_reason_code = 'other' WHERE cancel_reason_code = 'mistake'"
    )
    bind.exec_driver_sql(_reason_check(REASON_CODES_BEFORE))
    for table in TABLES:
        bind.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
