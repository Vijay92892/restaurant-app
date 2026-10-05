from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Response, status

from app.api.deps import (
    current_staff_allow_password_change,
    get_auth_service,
    get_mailer,
)
from app.core.mailer import Mailer, send_safely
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    MeResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    TokenPairResponse,
)
from app.services.auth_service import AuthService, IssuedTokens, StaffContext

router = APIRouter(prefix="/auth", tags=["auth"])

Service = Annotated[AuthService, Depends(get_auth_service)]
# Endpoints that must keep working while a password change is pending.
AnyStaff = Annotated[StaffContext, Depends(current_staff_allow_password_change)]


def me_response(ctx: StaffContext) -> MeResponse:
    return MeResponse(
        id=ctx.user.id,
        restaurant_id=ctx.restaurant.id,
        restaurant_name=ctx.restaurant.name,
        restaurant_code=ctx.restaurant.code,
        username=ctx.user.username,
        display_name=ctx.user.display_name,
        role=ctx.user.role,
        must_change_password=ctx.user.must_change_password,
        permissions=sorted(p.value for p in ctx.permissions),
    )


def token_response(tokens: IssuedTokens) -> TokenPairResponse:
    return TokenPairResponse(
        access_token=tokens.access_token,
        expires_in=tokens.expires_in,
        refresh_token=tokens.refresh_token,
        user=me_response(tokens.context),
    )


@router.post("/login", response_model=TokenPairResponse)
async def login(body: LoginRequest, service: Service) -> TokenPairResponse:
    tokens = await service.login(body.restaurant_code, body.username, body.password)
    return token_response(tokens)


@router.post("/refresh", response_model=TokenPairResponse)
async def refresh(body: RefreshRequest, service: Service) -> TokenPairResponse:
    return token_response(await service.refresh(body.refresh_token))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(ctx: AnyStaff, service: Service) -> Response:
    await service.logout(ctx)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(ctx: AnyStaff, service: Service) -> Response:
    await service.logout_all(ctx)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=MeResponse)
async def me(ctx: AnyStaff) -> MeResponse:
    return me_response(ctx)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(body: ChangePasswordRequest, ctx: AnyStaff, service: Service) -> Response:
    await service.change_password(ctx, body.current_password, body.new_password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(
    body: PasswordResetRequest,
    service: Service,
    background: BackgroundTasks,
    mailer: Annotated[Mailer, Depends(get_mailer)],
) -> dict[str, str]:
    email = await service.request_password_reset(body.restaurant_code, body.email)
    if email is not None:
        background.add_task(send_safely, mailer, email)
    return {"status": "accepted"}


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_password_reset(body: PasswordResetConfirm, service: Service) -> Response:
    await service.confirm_password_reset(body.token, body.new_password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
