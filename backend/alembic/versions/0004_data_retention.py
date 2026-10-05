"""Data retention: indexes for the purge queries, a tenant list for the retention job, and an
audit-log purge that can never touch recent history.

* ``app_restaurant_ids()`` returns routing ids only (like ``app_due_pin_rotations``), so the job
  can visit each restaurant and run its deletes under that restaurant's RLS.
* ``app_purge_audit_logs(p_before, p_limit)``: the runtime role still has no DELETE on
  ``audit_logs``. This SECURITY DEFINER function deletes only the *bound* tenant's rows, and
  refuses any cutoff newer than 180 days, so even a compromised API cannot erase recent history.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-04
"""

import re
from collections.abc import Sequence

from alembic import context, op
from app.core.config import get_settings

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FUNCTIONS = ("app_restaurant_ids()", "app_purge_audit_logs(timestamptz, integer)")

STATEMENTS = (
    # Retention deletes filter on expiry within one tenant.
    "CREATE INDEX ix_auth_sessions_expiry ON auth_sessions (restaurant_id, expires_at)",
    "CREATE INDEX ix_refresh_tokens_expiry ON refresh_tokens (restaurant_id, expires_at)",
    "CREATE INDEX ix_table_sessions_expiry ON table_sessions (restaurant_id, expires_at)",
    # Deleting a table session checks this foreign key; without an index that is a full scan.
    "CREATE INDEX ix_order_events_actor_session ON order_events (actor_table_session_id) "
    "WHERE actor_table_session_id IS NOT NULL",
    """
    CREATE FUNCTION app_restaurant_ids() RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
    AS $$ SELECT r.id FROM public.restaurants r ORDER BY r.id $$
    """,
    """
    CREATE FUNCTION app_purge_audit_logs(p_before timestamptz, p_limit integer) RETURNS integer
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public
    AS $$
    DECLARE
      tenant uuid := public.app_current_restaurant_id();
      deleted integer;
    BEGIN
      IF tenant IS NULL THEN
        RAISE EXCEPTION 'no restaurant is bound' USING ERRCODE = 'insufficient_privilege';
      END IF;
      IF p_before IS NULL OR p_before > now() - interval '180 days' THEN
        RAISE EXCEPTION 'audit logs are kept for at least 180 days'
          USING ERRCODE = 'check_violation';
      END IF;
      IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 10000 THEN
        RAISE EXCEPTION 'p_limit must be between 1 and 10000' USING ERRCODE = 'check_violation';
      END IF;
      DELETE FROM public.audit_logs
      WHERE id IN (
        SELECT a.id FROM public.audit_logs a
        WHERE a.restaurant_id = tenant AND a.occurred_at < p_before
        ORDER BY a.id
        LIMIT p_limit
      );
      GET DIAGNOSTICS deleted = ROW_COUNT;
      RETURN deleted;
    END $$
    """,
)


def _app_role() -> str:
    role = context.config.attributes.get("app_db_role") or get_settings().app_db_role
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError("APP_DB_ROLE must be a plain lowercase identifier")
    return role


def upgrade() -> None:
    bind = op.get_bind()
    app_role = _app_role()
    for statement in STATEMENTS:
        bind.exec_driver_sql(statement)
    for fn in FUNCTIONS:
        bind.exec_driver_sql(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        bind.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION {fn} TO "{app_role}"')


def downgrade() -> None:
    bind = op.get_bind()
    for fn in FUNCTIONS:
        bind.exec_driver_sql(f"DROP FUNCTION IF EXISTS {fn}")
    for index in (
        "ix_order_events_actor_session",
        "ix_table_sessions_expiry",
        "ix_refresh_tokens_expiry",
        "ix_auth_sessions_expiry",
    ):
        bind.exec_driver_sql(f"DROP INDEX IF EXISTS {index}")
