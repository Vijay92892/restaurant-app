"""Kitchen board: every order the kitchen still has to act on, oldest first.

The board is a snapshot that a kitchen device loads on start and after every (re)connection;
live events then carry each changed order in full. Aggregation ("Chilli Paneer x4: T4 x1, T7
x2, ...") is derived on the device from these orders, so it keeps working from the device's
cached orders while offline and never loses the item -> order -> table relationship.

The board also lists recently cancelled orders (id and final version only). A tablet that was
offline while an order it was cooking got cancelled learns about it on reconnection, can tell
the cook to stop, and remembers the final version so a late, older event cannot bring the order
back.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Order
from app.models.enums import OrderStatus
from app.services import order_service

KITCHEN_STATUSES = (
    OrderStatus.PLACED.value,
    OrderStatus.ACCEPTED.value,
    OrderStatus.PREPARING.value,
    OrderStatus.READY.value,
)
# A safety cap far above a busy medium-sized restaurant; the response stays bounded.
MAX_BOARD_ORDERS = 500
RECENT_CANCELLATIONS = timedelta(hours=2)
MAX_RECENT_CANCELLATIONS = 200


@dataclass(frozen=True)
class Board:
    server_time: datetime
    kitchen_delay_minutes: int
    orders: list[dict[str, Any]]
    truncated: bool
    recently_cancelled: list[dict[str, Any]]


async def board(db: AsyncSession, restaurant_id: uuid.UUID, kitchen_delay_minutes: int) -> Board:
    now = datetime.now(UTC)
    result = await db.execute(
        select(Order)
        .where(Order.restaurant_id == restaurant_id, Order.status.in_(KITCHEN_STATUSES))
        .order_by(Order.created_at, Order.order_number)
        .limit(MAX_BOARD_ORDERS + 1)
    )
    orders = list(result.scalars())
    truncated = len(orders) > MAX_BOARD_ORDERS
    views = await order_service.staff_order_views(db, restaurant_id, orders[:MAX_BOARD_ORDERS])
    cancelled = await db.execute(
        select(Order.id, Order.version, Order.cancelled_at)
        .where(
            Order.restaurant_id == restaurant_id,
            Order.status == OrderStatus.CANCELLED.value,
            Order.cancelled_at >= now - RECENT_CANCELLATIONS,
        )
        .order_by(Order.cancelled_at.desc())
        .limit(MAX_RECENT_CANCELLATIONS)
    )
    return Board(
        server_time=now,
        kitchen_delay_minutes=kitchen_delay_minutes,
        orders=views,
        truncated=truncated,
        recently_cancelled=[
            {"id": row.id, "version": row.version, "cancelled_at": row.cancelled_at}
            for row in cancelled
        ],
    )
