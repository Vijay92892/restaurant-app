"""Staff problem reports (pilot observation): any staff member files one, managers read them."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, LimiterDep, require
from app.core.permissions import Permission
from app.core.rate_limit import Limits
from app.models.enums import ProblemCategory
from app.schemas.problem_reports import CreateProblemReportRequest, ProblemReportResponse
from app.services import problem_report_service
from app.services.auth_service import StaffContext

router = APIRouter(prefix="/problem-reports", tags=["problem reports"])


@router.post("", response_model=ProblemReportResponse, status_code=status.HTTP_201_CREATED)
async def create_report(
    body: CreateProblemReportRequest,
    db: DbSession,
    limiter: LimiterDep,
    ctx: StaffContext = require(Permission.PROBLEM_REPORTS_CREATE),
) -> ProblemReportResponse:
    await limiter.enforce(Limits.PROBLEM_REPORTS_PER_USER, str(ctx.user_id))
    report = await problem_report_service.create(
        db,
        ctx,
        category=ProblemCategory(body.category),
        note=body.note,
        order_id=body.order_id,
    )
    return ProblemReportResponse(**report.__dict__)


@router.get("", response_model=list[ProblemReportResponse])
async def list_reports(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ctx: StaffContext = require(Permission.PROBLEM_REPORTS_READ),
) -> list[ProblemReportResponse]:
    reports = await problem_report_service.recent(db, ctx.restaurant_id, limit=limit)
    return [ProblemReportResponse(**report.__dict__) for report in reports]
