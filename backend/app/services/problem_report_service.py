"""Staff problem reports: "something went wrong during service" notes for the pilot (spec §48
Phase 11: observe order mistakes, staff confusion, network failures...).

Any staff member can file one; owners and admins read them in the app, and the operator's
`pilot-report` summarises them. The note is free text written by staff: stored as plain text,
bounded, and only ever rendered as text.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.models import DiningTable, Order, ProblemReport, User
from app.models.enums import ProblemCategory
from app.services.auth_service import StaffContext


@dataclass(frozen=True)
class ReportView:
    id: uuid.UUID
    category: str
    note: str | None
    order_id: uuid.UUID | None
    order_number: int | None
    table_label: str | None
    reported_by: str
    reported_by_role: str
    created_at: datetime


async def create(
    db: AsyncSession,
    ctx: StaffContext,
    *,
    category: ProblemCategory,
    note: str | None,
    order_id: uuid.UUID | None,
) -> ReportView:
    order_number = table_label = None
    if order_id is not None:
        found = (
            await db.execute(
                select(Order.order_number, DiningTable.label)
                .join(
                    DiningTable,
                    (DiningTable.id == Order.table_id)
                    & (DiningTable.restaurant_id == Order.restaurant_id),
                )
                .where(Order.restaurant_id == ctx.restaurant_id, Order.id == order_id)
            )
        ).one_or_none()
        if found is None:
            raise NotFound()  # includes other restaurants' orders: no existence oracle
        order_number, table_label = found
    report = ProblemReport(
        id=uuid.uuid4(),
        restaurant_id=ctx.restaurant_id,
        reported_by_user_id=ctx.user_id,
        category=category.value,
        note=note or None,
        order_id=order_id,
        created_at=datetime.now(UTC),
    )
    db.add(report)
    await db.commit()
    return ReportView(
        id=report.id,
        category=report.category,
        note=report.note,
        order_id=order_id,
        order_number=order_number,
        table_label=table_label,
        reported_by=ctx.user.display_name,
        reported_by_role=ctx.user.role,
        created_at=report.created_at,
    )


async def recent(
    db: AsyncSession,
    restaurant_id: uuid.UUID,
    *,
    limit: int,
    since: datetime | None = None,
) -> list[ReportView]:
    """Newest first. Reporters who were removed since still show by name (soft-deleted)."""
    query = (
        select(ProblemReport, User.display_name, User.role, Order.order_number, DiningTable.label)
        .join(
            User,
            (User.id == ProblemReport.reported_by_user_id)
            & (User.restaurant_id == ProblemReport.restaurant_id),
        )
        .outerjoin(
            Order,
            (Order.id == ProblemReport.order_id)
            & (Order.restaurant_id == ProblemReport.restaurant_id),
        )
        .outerjoin(
            DiningTable,
            (DiningTable.id == Order.table_id) & (DiningTable.restaurant_id == Order.restaurant_id),
        )
        .where(ProblemReport.restaurant_id == restaurant_id)
        .order_by(ProblemReport.created_at.desc(), ProblemReport.id)
        .limit(limit)
    )
    if since is not None:
        query = query.where(ProblemReport.created_at >= since)
    rows = await db.execute(query)
    return [
        ReportView(
            id=report.id,
            category=report.category,
            note=report.note,
            order_id=report.order_id,
            order_number=number,
            table_label=label,
            reported_by=name,
            reported_by_role=role,
            created_at=report.created_at,
        )
        for report, name, role, number, label in rows
    ]
