"""Serves uploaded menu images. Public by design (diners see them before ordering); names are
random and unguessable, keys are validated against a strict pattern, and files are always served
as `image/webp` with `nosniff`. In production this route can be replaced by a CDN/reverse proxy
pointing at MEDIA_ROOT."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from app.core.errors import NotFound


def build_router(prefix: str) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["media"])

    @router.get("/{key:path}", include_in_schema=False)
    async def media(key: str, request: Request) -> FileResponse:
        path = request.app.state.media.path_for(key)
        if path is None or not path.is_file():
            raise NotFound()
        return FileResponse(
            path,
            media_type="image/webp",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    return router
