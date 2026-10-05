from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, ForeignKey, ForeignKeyConstraint, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, col_created_at, tenant_id, uuid_pk


class ProblemReport(Base):
    """A staff member's note that something went wrong during service (pilot observation)."""

    __tablename__ = "problem_reports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["reported_by_user_id", "restaurant_id"], ["users.id", "users.restaurant_id"]
        ),
        ForeignKeyConstraint(["order_id", "restaurant_id"], ["orders.id", "orders.restaurant_id"]),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = tenant_id()
    reported_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = col_created_at()


class UsageCounter(Base):
    """How often something happened in one restaurant on one local day. Counts only."""

    __tablename__ = "usage_counters"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("restaurants.id"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    metric: Mapped[str] = mapped_column(Text, primary_key=True)
    count: Mapped[int] = mapped_column(BigInteger, nullable=False)
