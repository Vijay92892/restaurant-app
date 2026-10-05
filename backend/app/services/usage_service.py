"""Writes pilot usage counts (see `app.core.usage`) and reads them back for the pilot report."""

from __future__ import annotations

import asyncio
import logging
import uuid
import zoneinfo
from collections import defaultdict
from datetime import UTC, date, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.usage import Metric, UsageCounters
from app.db.session import Database, bind_tenant
from app.models import Restaurant, UsageCounter

logger = logging.getLogger("app.usage")

_UPSERT = text(
    """
    INSERT INTO usage_counters (restaurant_id, day, metric, count)
    VALUES (:rid, :day, :metric, :n)
    ON CONFLICT (restaurant_id, day, metric)
    DO UPDATE SET count = usage_counters.count + EXCLUDED.count
    """
)


def local_day(timezone: str, at: datetime | None = None) -> date:
    """The restaurant's calendar day at `at` (now by default). Computed here, not in SQL: the
    zone database ships with Python (`tzdata`), not with every PostgreSQL build."""
    try:
        zone = zoneinfo.ZoneInfo(timezone)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        zone = zoneinfo.ZoneInfo("Asia/Kolkata")
    return (at or datetime.now(UTC)).astimezone(zone).date()


async def flush(db: Database, counters: UsageCounters) -> int:
    """Write every pending count, one tenant-bound transaction per restaurant. Returns how many
    (restaurant, metric) rows were written. Counts that fail to write are dropped and logged:
    they are observations, not records anyone depends on."""
    by_restaurant: dict[uuid.UUID, dict[Metric, int]] = defaultdict(dict)
    for (restaurant_id, metric), n in counters.drain().items():
        by_restaurant[restaurant_id][metric] = n
    written = 0
    for restaurant_id, metrics in by_restaurant.items():
        try:
            async with db.sessionmaker() as session:
                await bind_tenant(session, restaurant_id)
                timezone = await session.scalar(
                    select(Restaurant.timezone).where(Restaurant.id == restaurant_id)
                )
                if timezone is None:  # no such restaurant (any more)
                    continue
                day = local_day(timezone)
                for metric, n in sorted(metrics.items()):
                    await session.execute(
                        _UPSERT,
                        {"rid": restaurant_id, "day": day, "metric": metric.value, "n": n},
                    )
                await session.commit()
            written += len(metrics)
        except Exception:
            logger.warning(
                "usage counts dropped", extra={"restaurant_id": str(restaurant_id)}, exc_info=True
            )
    return written


async def run_flusher(db: Database, counters: UsageCounters, interval_seconds: int) -> None:
    """Background loop inside the API process (every worker flushes its own counts)."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await flush(db, counters)
        except Exception:  # never let the loop die
            logger.exception("usage flush failed")


async def daily_counts(
    db: AsyncSession, restaurant_id: uuid.UUID, since: date
) -> dict[date, dict[str, int]]:
    """{day: {metric: count}} from `since` on, for a session already bound to the restaurant."""
    rows = await db.execute(
        select(UsageCounter.day, UsageCounter.metric, UsageCounter.count)
        .where(UsageCounter.restaurant_id == restaurant_id, UsageCounter.day >= since)
        .order_by(UsageCounter.day, UsageCounter.metric)
    )
    days: dict[date, dict[str, int]] = defaultdict(dict)
    for day, metric, count in rows:
        days[day][metric] = count
    return dict(days)
