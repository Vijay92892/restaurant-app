"""Kitchen board snapshot (live changes arrive over /ws/staff)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import DbSession, require
from app.core.permissions import Permission
from app.schemas.orders import CancelledOrderRef, KitchenBoardView, StaffOrderView
from app.services import kitchen_service
from app.services.auth_service import StaffContext

router = APIRouter(prefix="/kitchen", tags=["kitchen"])


@router.get("/board", response_model=KitchenBoardView)
async def board(
    db: DbSession, ctx: StaffContext = require(Permission.ORDERS_READ)
) -> KitchenBoardView:
    snapshot = await kitchen_service.board(
        db, ctx.restaurant_id, ctx.restaurant.kitchen_delay_minutes
    )
    return KitchenBoardView(
        server_time=snapshot.server_time,
        kitchen_delay_minutes=snapshot.kitchen_delay_minutes,
        truncated=snapshot.truncated,
        orders=[StaffOrderView.model_validate(view) for view in snapshot.orders],
        recently_cancelled=[
            CancelledOrderRef.model_validate(ref) for ref in snapshot.recently_cancelled
        ],
    )
