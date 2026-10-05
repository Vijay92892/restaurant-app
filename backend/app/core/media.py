"""Menu image uploads (spec §32).

Every upload is treated as hostile:
  * declared extension and MIME type must be jpg/jpeg/png/webp;
  * the bytes are decoded *only* by Pillow's JPEG/PNG/WEBP decoders (no exotic formats, no SVG);
  * dimensions and pixel count are checked before decoding (decompression bombs);
  * the image is re-encoded to WebP — EXIF (incl. GPS), ICC profiles, comments and any appended
    payload (polyglot files) are dropped — at two sizes for weak mobile networks;
  * files get random names and live under MEDIA_ROOT, outside the application code, and are
    served as `image/webp` with `nosniff`.
"""

from __future__ import annotations

import io
import os
import re
import secrets
import tempfile
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path

import anyio
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.errors import AppError

ALLOWED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
ALLOWED_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
ALLOWED_FORMATS = ("JPEG", "PNG", "WEBP")
MIN_SIDE = 32
MAX_SIDE = 10_000
MAX_PIXELS = 40_000_000
LARGE_SIDE = 1200
SMALL_SIDE = 400
_KEY = re.compile(
    r"^items/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/"
    r"[A-Za-z0-9_-]{22}(_sm)?\.webp$"
)


class InvalidImage(AppError):
    status_code = 422
    code = "invalid_image"
    message = "Please upload a JPG, PNG or WebP photo."


class ImageTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"
    message = "That image file is too large."


@dataclass(frozen=True)
class ProcessedImage:
    large: bytes
    small: bytes


def check_declared(filename: str | None, content_type: str | None) -> None:
    extension = Path(filename or "").suffix.lower()
    if (
        extension not in ALLOWED_EXTENSIONS
        or (content_type or "").lower() not in ALLOWED_CONTENT_TYPES
    ):
        raise InvalidImage()


def _encode(image: Image.Image, side: int) -> bytes:
    copy = image.copy()
    copy.thumbnail((side, side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    copy.save(out, format="WEBP", quality=80, method=4)  # no exif/icc passed ⇒ stripped
    return out.getvalue()


def process_image(data: bytes) -> ProcessedImage:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(data), formats=ALLOWED_FORMATS) as source:
                width, height = source.size  # header only; nothing decoded yet
                if (
                    min(width, height) < MIN_SIDE
                    or max(width, height) > MAX_SIDE
                    or width * height > MAX_PIXELS
                ):
                    raise InvalidImage(
                        f"Photos must be between {MIN_SIDE} and {MAX_SIDE} pixels per side."
                    )
                source.seek(0)  # animated files: first frame only
                image = ImageOps.exif_transpose(source)  # respect camera rotation, then drop EXIF
                has_alpha = image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info
                image = image.convert("RGBA" if has_alpha else "RGB")
                return ProcessedImage(
                    large=_encode(image, LARGE_SIDE), small=_encode(image, SMALL_SIDE)
                )
        except InvalidImage:
            raise
        except (
            UnidentifiedImageError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            OSError,
            ValueError,
            SyntaxError,
            EOFError,
        ) as exc:
            raise InvalidImage() from exc


_processing_limiter: anyio.CapacityLimiter | None = None


async def process_image_async(data: bytes) -> ProcessedImage:
    """CPU-heavy: run in a worker thread, at most two at a time."""
    global _processing_limiter
    if _processing_limiter is None:
        _processing_limiter = anyio.CapacityLimiter(2)
    return await anyio.to_thread.run_sync(process_image, data, limiter=_processing_limiter)


def new_item_image_keys(restaurant_id: uuid.UUID) -> tuple[str, str]:
    token = secrets.token_urlsafe(16)
    base = f"items/{restaurant_id}/{token}"
    return f"{base}.webp", f"{base}_sm.webp"


def small_key(large_key: str) -> str:
    return large_key.removesuffix(".webp") + "_sm.webp"


class MediaStorage:
    """Local-disk storage under MEDIA_ROOT. Keys are validated against a strict pattern and the
    resolved path must stay inside the root, so no traversal is possible."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def path_for(self, key: str) -> Path | None:
        if not _KEY.fullmatch(key):
            return None
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            return None
        return path

    def _write(self, key: str, data: bytes) -> None:
        path = self.path_for(key)
        if path is None:
            raise ValueError("invalid media key")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp, path)  # atomic: readers never see a half-written file
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    async def save(self, key: str, data: bytes) -> None:
        await anyio.to_thread.run_sync(self._write, key, data)

    async def delete(self, key: str) -> None:
        path = self.path_for(key)
        if path is not None:
            await anyio.to_thread.run_sync(lambda: path.unlink(missing_ok=True))
