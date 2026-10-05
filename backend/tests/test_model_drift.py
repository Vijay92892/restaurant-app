"""The ORM models must describe exactly the tables/columns the migrations create."""

from __future__ import annotations

from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401
from app.db.base import Base
from tests.conftest import PgTestDatabase

# Indexes, unique and check constraints are authored in SQL in the migration (partial and
# expression indexes, regex checks) and verified by test_schema_rls; compare structure here.
_IGNORED = {
    "add_index",
    "remove_index",
    "add_constraint",
    "remove_constraint",
    "add_fk",
    "remove_fk",
}


def _flatten(diffs: list[Any]) -> list[Any]:
    flat: list[Any] = []
    for diff in diffs:
        flat.extend(diff if isinstance(diff, list) else [diff])
    return flat


async def test_models_match_migrated_schema(clean_db: PgTestDatabase) -> None:
    engine = create_async_engine(clean_db.sqlalchemy_url("rr_owner"))
    try:
        async with engine.connect() as conn:

            def diff(sync_conn: Any) -> list[Any]:
                ctx = MigrationContext.configure(
                    sync_conn,
                    opts={"compare_type": True, "compare_server_default": False},
                )
                return compare_metadata(ctx, Base.metadata)

            diffs = await conn.run_sync(diff)
    finally:
        await engine.dispose()

    relevant = [
        d
        for d in _flatten(diffs)
        if d[0] not in _IGNORED and not (d[0] == "remove_table" and d[1].name == "alembic_version")
    ]
    assert relevant == [], relevant
