"""Order lines remember the display order of their chosen options (menu order, e.g.
"Spice level" before "Extras"), for the customer's order view and the kitchen screen.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(
        "ALTER TABLE order_item_modifiers ADD COLUMN position smallint NOT NULL DEFAULT 0"
    )
    bind.exec_driver_sql(
        "ALTER TABLE order_item_modifiers ADD CONSTRAINT ck_order_item_modifiers_position "
        "CHECK (position BETWEEN 0 AND 100)"
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql("ALTER TABLE order_item_modifiers DROP COLUMN position")
