"""Location-based table access: guests at the restaurant may open a table session without the
PIN, when the restaurant turns it on.

* ``restaurants``: ``location_ordering_enabled`` (off by default), the branch's point
  (``location_latitude`` / ``location_longitude``, confirmed by the owner or a manager; never
  guessed), ``location_radius_m`` (25–500 m, default 100) and ``pin_fallback_enabled`` (the PIN
  still works when the location check fails; on by default). Location access cannot be on
  without a point.
* ``table_sessions.access_method``: how the session was opened, ``pin`` or ``location``. A PIN
  session keeps its ``pin_version``; a location session has none. Guests' coordinates are
  never stored.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATEMENTS = (
    """
    ALTER TABLE restaurants
      ADD COLUMN location_ordering_enabled boolean NOT NULL DEFAULT false,
      ADD COLUMN location_latitude double precision,
      ADD COLUMN location_longitude double precision,
      ADD COLUMN location_radius_m integer NOT NULL DEFAULT 100,
      ADD COLUMN pin_fallback_enabled boolean NOT NULL DEFAULT true
    """,
    """
    ALTER TABLE restaurants ADD CONSTRAINT ck_restaurants_location_point CHECK (
      (location_latitude IS NULL) = (location_longitude IS NULL)
      AND (location_latitude IS NULL OR (
        location_latitude BETWEEN -90 AND 90 AND location_longitude BETWEEN -180 AND 180)))
    """,
    "ALTER TABLE restaurants ADD CONSTRAINT ck_restaurants_location_radius "
    "CHECK (location_radius_m BETWEEN 25 AND 500)",
    "ALTER TABLE restaurants ADD CONSTRAINT ck_restaurants_location_enabled "
    "CHECK (NOT location_ordering_enabled OR location_latitude IS NOT NULL)",
    """
    ALTER TABLE table_sessions
      ALTER COLUMN pin_version DROP NOT NULL,
      ADD COLUMN access_method text NOT NULL DEFAULT 'pin'
    """,
    """
    ALTER TABLE table_sessions ADD CONSTRAINT ck_table_sessions_access CHECK (
      (access_method = 'pin' AND pin_version IS NOT NULL)
      OR (access_method = 'location' AND pin_version IS NULL))
    """,
)


def upgrade() -> None:
    bind = op.get_bind()
    for statement in STATEMENTS:
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("ALTER TABLE table_sessions DROP CONSTRAINT ck_table_sessions_access")
    # Location sessions have no PIN version; give them the restaurant's latest one so the
    # column can be NOT NULL again (orders keep pointing at their sessions). Session rows are
    # immutable for the application; the schema owner lifts that for this one update.
    bind.exec_driver_sql("ALTER TABLE table_sessions DISABLE TRIGGER trg_table_sessions_immutable")
    bind.exec_driver_sql(
        """
        UPDATE table_sessions s SET pin_version = (
          SELECT max(p.version) FROM pin_versions p WHERE p.restaurant_id = s.restaurant_id)
        WHERE s.pin_version IS NULL
        """
    )
    bind.exec_driver_sql("ALTER TABLE table_sessions ENABLE TRIGGER trg_table_sessions_immutable")
    bind.exec_driver_sql(
        "ALTER TABLE table_sessions ALTER COLUMN pin_version SET NOT NULL, "
        "DROP COLUMN access_method"
    )
    bind.exec_driver_sql(
        "ALTER TABLE restaurants "
        "DROP COLUMN pin_fallback_enabled, DROP COLUMN location_radius_m, "
        "DROP COLUMN location_longitude, DROP COLUMN location_latitude, "
        "DROP COLUMN location_ordering_enabled"
    )
