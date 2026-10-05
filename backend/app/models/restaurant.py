from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Integer, SmallInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, col_created_at, col_updated_at, uuid_pk


class Restaurant(Base):
    __tablename__ = "restaurants"

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, default="Asia/Kolkata")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    pin_length: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=4)
    pin_rotation_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True, default=240)
    table_session_ttl_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=180)
    waiter_can_cancel_preparing: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    kitchen_delay_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    order_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = col_created_at()
    updated_at: Mapped[datetime] = col_updated_at()
