"""The pilot report: what happened in one restaurant over the last few days, as Markdown, for the
people running the pilot (spec §48 Phase 11: customer ordering, waiter and kitchen workflow, PIN
usage, QR scanning, network failures, order mistakes, staff confusion).

    python -m app.cli pilot-report --code spice-garden --days 7 --out report.md

Built from data the system keeps anyway (orders and their timestamps, order events, the audit
log), the daily usage counts and staff problem reports. It counts and times things; it contains
no money (spec §37) and nothing about individual diners.
"""

from __future__ import annotations

import math
import statistics
import uuid
import zoneinfo
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Order, OrderEvent, Restaurant
from app.models.enums import OrderSource, OrderStatus
from app.services import problem_report_service, usage_service

CANCEL_REASONS = {
    "customer_changed_mind": "Guest changed their mind",
    "mistake": "Entered by mistake",
    "item_unavailable": "Dish not available",
    "duplicate": "Duplicate order",
    "kitchen_issue": "Kitchen problem",
    "other": "Other",
    None: "No reason given",
}
PROBLEM_CATEGORIES = {
    "order_mistake": "Order mistake",
    "customer_help": "Customer needed help",
    "app_confusing": "App was confusing",
    "network": "Connection problem",
    "slow": "Something was slow",
    "other": "Other",
}
_ACTIVE = {s.value for s in OrderStatus} - {OrderStatus.COMPLETED, OrderStatus.CANCELLED}


@dataclass(frozen=True)
class Window:
    zone: zoneinfo.ZoneInfo
    first_day: date
    last_day: date
    start: datetime  # UTC, inclusive
    end: datetime  # UTC, exclusive

    @classmethod
    def last_days(cls, timezone: str, days: int, now: datetime) -> Window:
        zone = zoneinfo.ZoneInfo(timezone)
        last_day = now.astimezone(zone).date()
        first_day = last_day - timedelta(days=days - 1)
        start = datetime.combine(first_day, time.min, zone).astimezone(UTC)
        end = datetime.combine(last_day + timedelta(days=1), time.min, zone).astimezone(UTC)
        return cls(zone, first_day, last_day, start, end)

    def days(self) -> list[date]:
        count = (self.last_day - self.first_day).days + 1
        return [self.first_day + timedelta(days=n) for n in range(count)]

    def local(self, at: datetime) -> datetime:
        return at.astimezone(self.zone)


def _minutes(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return (end - start).total_seconds() / 60


def _median_p90(values: list[float]) -> tuple[str, str]:
    if not values:
        return "—", "—"
    ordered = sorted(values)
    p90 = ordered[max(0, math.ceil(0.9 * len(ordered)) - 1)]
    return f"{statistics.median(ordered):.1f}", f"{p90:.1f}"


def _percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "—"


def _one_line(text: str) -> str:
    """Staff-written text on one line, so it cannot break the Markdown around it."""
    return " / ".join(part.strip() for part in text.splitlines() if part.strip())


async def build(
    db: AsyncSession, restaurant: Restaurant, *, days: int, now: datetime | None = None
) -> str:
    """The report for the `days` local days ending today. `db` must be bound to `restaurant`."""
    now = now or datetime.now(UTC)
    window = Window.last_days(restaurant.timezone, days, now)
    rid = restaurant.id

    orders = list(
        (
            await db.execute(
                select(Order).where(
                    Order.restaurant_id == rid,
                    Order.created_at >= window.start,
                    Order.created_at < window.end,
                )
            )
        ).scalars()
    )
    event_counts = dict(
        (
            await db.execute(
                select(OrderEvent.type, func.count())
                .where(
                    OrderEvent.restaurant_id == rid,
                    OrderEvent.created_at >= window.start,
                    OrderEvent.created_at < window.end,
                )
                .group_by(OrderEvent.type)
            )
        ).all()
    )
    audit_rows = (
        await db.execute(
            select(AuditLog.action, AuditLog.details).where(
                AuditLog.restaurant_id == rid,
                AuditLog.occurred_at >= window.start,
                AuditLog.occurred_at < window.end,
                AuditLog.action.in_(
                    (
                        "menu.item_availability_changed",
                        "menu.option_availability_changed",
                        "pin.rotated",
                        "table_sessions.invalidated",
                        "pin.bruteforce_suspected",
                    )
                ),
            )
        )
    ).all()
    usage = await usage_service.daily_counts(db, rid, window.first_day)
    reports = await problem_report_service.recent(db, rid, limit=1000, since=window.start)

    lines: list[str] = []
    out = lines.append
    delay = restaurant.kitchen_delay_minutes

    # --- derived numbers ---------------------------------------------------------------------
    by_day: dict[date, list[Order]] = defaultdict(list)
    for order in orders:
        by_day[window.local(order.created_at).date()].append(order)
    from_customers = sum(o.source == OrderSource.CUSTOMER for o in orders)
    cancelled = [o for o in orders if o.status == OrderStatus.CANCELLED]
    to_accept = [m for o in orders if (m := _minutes(o.created_at, o.accepted_at)) is not None]
    to_ready = [m for o in orders if (m := _minutes(o.created_at, o.ready_at)) is not None]
    to_serve = [m for o in orders if (m := _minutes(o.ready_at, o.served_at)) is not None]
    late = {o.id for o in orders if (m := _minutes(o.created_at, o.ready_at)) and m > delay}
    totals: Counter[str] = Counter()
    for counts in usage.values():
        totals.update(counts)
    pin_tries = totals["pin_accepted"] + totals["pin_wrong"]

    # --- header and summary ------------------------------------------------------------------
    out(f"# Pilot report — {restaurant.name} ({restaurant.code})")
    out("")
    out(
        f"{window.first_day:%a %d %b %Y} to {window.last_day:%a %d %b %Y} ({days} days). "
        f"Times are local ({restaurant.timezone}). Generated {window.local(now):%d %b %Y %H:%M}."
    )
    out("")
    out("## At a glance")
    out("")
    out(
        f"- **Orders:** {len(orders)} — {_percent(from_customers, len(orders))} from customers' "
        f"phones, {_percent(len(orders) - from_customers, len(orders))} entered by waiters."
    )
    mistakes = sum(o.cancel_reason_code == "mistake" for o in cancelled)
    out(
        f"- **Cancelled:** {len(cancelled)} ({_percent(len(cancelled), len(orders))}); "
        f"{mistakes} entered by mistake."
    )
    median_ready, _ = _median_p90(to_ready)
    out(
        f"- **Kitchen:** ready a median {median_ready} min after ordering; {len(late)} of "
        f"{len(to_ready)} ready orders took longer than the {delay}-minute delay mark."
    )
    out(
        f"- **Table PIN:** {totals['pin_accepted']} accepted, {totals['pin_wrong']} wrong "
        f"({_percent(totals['pin_wrong'], pin_tries)} of tries), {totals['pin_locked']} refused "
        f"by the guessing limit; QR codes opened {totals['qr_opened']} times."
    )
    out(
        f"- **Staff devices:** {totals['staff_connection_lost']} dropped connections, "
        f"{totals['staff_resync']} forced reloads."
    )
    out(f"- **Problem reports from staff:** {len(reports)}.")
    out("")

    # --- by day ------------------------------------------------------------------------------
    out("## Day by day")
    out("")
    out(
        "| Day | Orders | From customers | By waiters | Cancelled | Late | QR opened "
        "| PIN accepted | Wrong PIN | Locked out | Connections dropped |"
    )
    out("|---|---|---|---|---|---|---|---|---|---|---|")
    for day in window.days():
        day_orders = by_day.get(day, [])
        customers = sum(o.source == OrderSource.CUSTOMER for o in day_orders)
        day_cancelled = sum(o.status == OrderStatus.CANCELLED for o in day_orders)
        day_late = sum(o.id in late for o in day_orders)
        u = usage.get(day, {})
        out(
            f"| {day:%a %d %b} | {len(day_orders)} | {customers} | {len(day_orders) - customers} "
            f"| {day_cancelled} | {day_late} | {u.get('qr_opened', 0)} "
            f"| {u.get('pin_accepted', 0)} | {u.get('pin_wrong', 0)} | {u.get('pin_locked', 0)} "
            f"| {u.get('staff_connection_lost', 0)} |"
        )
    out("")

    # --- timing ------------------------------------------------------------------------------
    out("## How long things took (minutes)")
    out("")
    out("| Step | Median | 90% of orders within | Orders |")
    out("|---|---|---|---|")
    for label, values in (
        ("Ordered → accepted by the kitchen", to_accept),
        ("Ordered → ready", to_ready),
        ("Ready → served", to_serve),
    ):
        median, p90 = _median_p90(values)
        out(f"| {label} | {median} | {p90} | {len(values)} |")
    out("")

    hours = Counter(window.local(o.created_at).hour for o in orders)
    if hours:
        out("## Busiest hours")
        out("")
        out("| Hour | Orders |")
        out("|---|---|")
        for hour, count in sorted(hours.items(), key=lambda kv: (-kv[1], kv[0]))[:6]:
            out(f"| {hour:02d}:00–{(hour + 1) % 24:02d}:00 | {count} |")
        out("")

    # --- cancellations -----------------------------------------------------------------------
    out("## Cancellations")
    out("")
    if cancelled:
        out("| Reason | By customers | By staff |")
        out("|---|---|---|")
        reasons: Counter[tuple[str | None, bool]] = Counter(
            (o.cancel_reason_code, o.cancelled_by_user_id is not None) for o in cancelled
        )
        for code, label in CANCEL_REASONS.items():
            by_customer, by_staff = reasons[(code, False)], reasons[(code, True)]
            if by_customer or by_staff:
                out(f"| {label} | {by_customer} | {by_staff} |")
        out("")
    else:
        out("No cancellations.")
        out("")
    requested = event_counts.get("order.cancel_requested", 0)
    rejected = event_counts.get("order.cancel_request_rejected", 0)
    approved = sum(o.cancel_requested_at is not None for o in cancelled)
    waiting = sum(o.cancel_requested_at is not None and o.status in _ACTIVE for o in orders)
    out(
        f"Cancellation requests (the kitchen had started): {requested} made, {approved} "
        f"approved, {rejected} rejected, {waiting} still waiting for a manager."
    )
    out("")

    # --- operations --------------------------------------------------------------------------
    actions = Counter(action for action, _ in audit_rows)
    manual_pins = [
        d for action, d in audit_rows if action == "pin.rotated" and d.get("reason") == "manual"
    ]
    availability = actions["menu.item_availability_changed"]
    availability += actions["menu.option_availability_changed"]
    out("## Operations")
    out("")
    out(f"- Dishes or options switched sold out / back: {availability}.")
    out(
        f"- Table PIN changed by hand: {len(manual_pins)} "
        f"(ending {sum(int(d.get('sessions_invalidated', 0)) for d in manual_pins)} customer "
        f"sessions); all customer sessions ended at once: "
        f"{actions['table_sessions.invalidated']} times."
    )
    out(f"- PIN guessing alerts: {actions['pin.bruteforce_suspected']}.")
    out("")

    # --- problem reports ---------------------------------------------------------------------
    out("## Problem reports from staff")
    out("")
    if not reports:
        out("None.")
        out("")
    else:
        categories = Counter(r.category for r in reports)
        out("| What | Reports |")
        out("|---|---|")
        for code, label in PROBLEM_CATEGORIES.items():
            if categories[code]:
                out(f"| {label} | {categories[code]} |")
        out("")
        for report in sorted(reports, key=lambda r: r.created_at):
            parts = [
                f"{window.local(report.created_at):%a %d %b %H:%M}",
                f"{report.reported_by} ({report.reported_by_role})",
                PROBLEM_CATEGORIES.get(report.category, report.category),
            ]
            if report.order_number is not None:
                parts.append(f"order #{report.order_number}, table {report.table_label}")
            note = f" — {_one_line(report.note)}" if report.note else ""
            out(f"- {' · '.join(parts)}{note}")
        out("")

    out("## What the numbers mean")
    out("")
    out(
        "- *QR opened* counts every time a table's page was opened, reloads included; "
        "*PIN accepted* is a new phone joining a table. Many wrong PINs or lock-outs suggest the "
        "PIN is hard to find or read out."
    )
    out(
        f"- *Late* means ready more than {delay} minutes (the restaurant's delay setting) after "
        "the order was placed."
    )
    out(
        "- *Connections dropped* counts staff devices whose live connection ended without being "
        "closed (Wi-Fi drops, tablets going to sleep); devices reload the board when they "
        "reconnect, so drops delay updates but lose nothing."
    )
    out("- Wrong PINs refused by the per-phone limit (before the table is known) are not counted.")
    return "\n".join(lines) + "\n"


async def restaurant_by_id(db: AsyncSession, restaurant_id: uuid.UUID) -> Restaurant | None:
    return await db.scalar(select(Restaurant).where(Restaurant.id == restaurant_id))
