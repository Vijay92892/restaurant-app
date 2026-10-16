"""Restaurant branding for the customer ordering page: a visual theme and an optional tagline.

* ``restaurants.theme``: the name of one of the ordering page's built-in themes (for example
  ``heritage``: deep red, gold and cream). NULL is the default look. Only names of built-in
  themes are accepted by the application; the database checks the shape.
* ``restaurants.tagline``: a short line shown under the restaurant's name, only once the
  restaurant has approved it. Plain text, at most 80 characters.

Both are public: guests see them before entering the table PIN, like the restaurant's name.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATEMENTS = (
    "ALTER TABLE restaurants ADD COLUMN theme text, ADD COLUMN tagline text",
    "ALTER TABLE restaurants ADD CONSTRAINT ck_restaurants_theme "
    "CHECK (theme IS NULL OR theme ~ '^[a-z][a-z0-9-]{0,31}$')",
    "ALTER TABLE restaurants ADD CONSTRAINT ck_restaurants_tagline "
    "CHECK (tagline IS NULL OR char_length(tagline) BETWEEN 1 AND 80)",
)


def upgrade() -> None:
    bind = op.get_bind()
    for statement in STATEMENTS:
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE restaurants DROP COLUMN IF EXISTS tagline, DROP COLUMN IF EXISTS theme"
    )
