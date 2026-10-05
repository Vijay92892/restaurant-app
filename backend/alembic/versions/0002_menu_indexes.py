"""Menu: unique option names within a modifier group; index for group → items lookups.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(
        "CREATE UNIQUE INDEX uq_menu_modifiers_name ON menu_modifiers (group_id, lower(name)) "
        "WHERE deleted_at IS NULL"
    )
    bind.exec_driver_sql(
        "CREATE INDEX ix_menu_item_modifier_groups_group ON menu_item_modifier_groups (group_id)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("DROP INDEX IF EXISTS ix_menu_item_modifier_groups_group")
    bind.exec_driver_sql("DROP INDEX IF EXISTS uq_menu_modifiers_name")
