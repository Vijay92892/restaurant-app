"""Phase 5: menu image uploads are treated as hostile input (spec §32)."""

from __future__ import annotations

import io

import httpx
import pytest
from PIL import Image

from app.core.media import MediaStorage, process_image
from tests.conftest import Seeder, staff_headers
from tests.test_menu import M, build_menu


def make_image(
    fmt: str = "JPEG", size: tuple[int, int] = (1600, 1200), mode: str = "RGB", **save: object
) -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, color=(200, 80, 40) if mode == "RGB" else 1).save(
        buffer, format=fmt, **save
    )
    return buffer.getvalue()


def jpeg_with_gps() -> bytes:
    image = Image.new("RGB", (800, 600), (10, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "SecretPhoneMaker"  # Make
    exif[0x8825] = {1: "N", 2: (28.0, 36.0, 50.0), 3: "E", 4: (77.0, 12.0, 30.0)}  # GPS IFD
    exif[0x0112] = 6  # Orientation: rotate 90° when displayed
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


async def _upload(
    client: httpx.AsyncClient,
    headers: dict,
    item_id: str,
    data: bytes,
    filename: str = "dish.jpg",
    content_type: str = "image/jpeg",
) -> httpx.Response:
    return await client.post(
        f"{M}/items/{item_id}/image",
        headers=headers,
        files={"image": (filename, data, content_type)},
    )


async def _setup(client: httpx.AsyncClient, seed: Seeder) -> tuple[dict, dict]:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    return owner, await build_menu(client, owner)


async def test_upload_reencodes_resizes_and_serves(
    client: httpx.AsyncClient, seed: Seeder, app: object
) -> None:
    owner, built = await _setup(client, seed)
    response = await _upload(client, owner, built["paneer"]["id"], jpeg_with_gps())
    assert response.status_code == 200, response.text
    item = response.json()
    assert item["image_url"].startswith("/media/items/") and item["image_url"].endswith(".webp")
    assert item["thumbnail_url"].endswith("_sm.webp")

    large = await client.get(item["image_url"])
    small = await client.get(item["thumbnail_url"])
    for served in (large, small):
        assert served.status_code == 200
        assert served.headers["content-type"] == "image/webp"
        assert served.headers["x-content-type-options"] == "nosniff"
        assert "immutable" in served.headers["cache-control"]
    with Image.open(io.BytesIO(large.content)) as img:
        assert img.format == "WEBP"
        assert img.size == (600, 800)  # EXIF rotation applied, then the EXIF itself dropped
        assert not img.getexif()
    assert b"SecretPhoneMaker" not in large.content  # camera metadata gone
    with Image.open(io.BytesIO(small.content)) as img:
        assert max(img.size) <= 400


async def test_large_photos_are_downscaled(client: httpx.AsyncClient, seed: Seeder) -> None:
    owner, built = await _setup(client, seed)
    data = make_image("PNG", (3000, 1500))
    response = await _upload(client, owner, built["paneer"]["id"], data, "photo.png", "image/png")
    assert response.status_code == 200
    served = await client.get(response.json()["image_url"])
    with Image.open(io.BytesIO(served.content)) as img:
        assert img.size == (1200, 600)


async def test_replacing_and_removing_deletes_old_files(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    owner, built = await _setup(client, seed)
    item_id = built["paneer"]["id"]
    first = (await _upload(client, owner, item_id, make_image())).json()
    second = (
        await _upload(client, owner, item_id, make_image("WEBP"), "a.webp", "image/webp")
    ).json()
    assert first["image_url"] != second["image_url"]
    assert (await client.get(first["image_url"])).status_code == 404
    assert (await client.get(first["thumbnail_url"])).status_code == 404
    assert (await client.get(second["image_url"])).status_code == 200

    removed = await client.delete(f"{M}/items/{item_id}/image", headers=owner)
    assert removed.status_code == 200 and removed.json()["image_url"] is None
    assert (await client.get(second["image_url"])).status_code == 404


SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)">'
    b'<rect width="10" height="10"/></svg>'
)
HTML = b"<html><script>alert(document.cookie)</script></html>"


@pytest.mark.parametrize(
    ("data", "filename", "content_type"),
    [
        (SVG, "logo.svg", "image/svg+xml"),
        (SVG, "logo.png", "image/png"),  # SVG disguised as PNG
        (HTML, "dish.jpg", "image/jpeg"),  # HTML disguised as JPEG
        (b"MZ\x90\x00" + b"\x00" * 200, "dish.exe", "application/octet-stream"),
        (b"MZ\x90\x00" + b"\x00" * 200, "dish.jpg", "image/jpeg"),  # executable renamed
        (make_image("GIF", (100, 100), "RGB"), "anim.gif", "image/gif"),
        (make_image("GIF", (100, 100), "RGB"), "anim.jpg", "image/jpeg"),  # GIF renamed
        (make_image("BMP", (100, 100), "RGB"), "a.png", "image/png"),  # unsupported decoder
        (make_image("PNG", (10, 10)), "tiny.png", "image/png"),  # too small
        (make_image("PNG", (12000, 50), "1"), "wide.png", "image/png"),  # too wide
        (make_image("JPEG")[:500], "truncated.jpg", "image/jpeg"),
        (b"", "empty.jpg", "image/jpeg"),
    ],
    ids=[
        "svg",
        "svg-as-png",
        "html-as-jpeg",
        "exe",
        "exe-as-jpeg",
        "gif",
        "gif-as-jpeg",
        "bmp-as-png",
        "too-small",
        "too-wide",
        "truncated",
        "empty",
    ],
)
async def test_hostile_or_unsupported_files_are_rejected(
    client: httpx.AsyncClient, seed: Seeder, data: bytes, filename: str, content_type: str
) -> None:
    owner, built = await _setup(client, seed)
    response = await _upload(client, owner, built["paneer"]["id"], data, filename, content_type)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] in ("invalid_image", "validation_error")
    menu = (await client.get(M, headers=owner)).json()
    assert menu["categories"][0]["items"][0]["image_url"] is None


def test_decompression_bombs_are_rejected_before_decoding() -> None:
    # 9000 × 9000 = 81 MP: tiny on disk as a 1-bit PNG, ~240 MB if decoded as RGB.
    bomb = make_image("PNG", (9000, 9000), "1")
    assert len(bomb) < 2_000_000
    with pytest.raises(Exception) as excinfo:
        process_image(bomb)
    assert excinfo.value.__class__.__name__ == "InvalidImage"


def test_polyglot_payload_is_dropped_by_reencoding() -> None:
    polyglot = make_image("JPEG", (200, 200)) + b"<script>alert(1)</script>"
    processed = process_image(polyglot)
    assert b"<script>" not in processed.large and b"<script>" not in processed.small


async def test_oversized_uploads_are_refused(client: httpx.AsyncClient, seed: Seeder) -> None:
    owner, built = await _setup(client, seed)
    too_big = make_image("PNG", (2000, 2000)) + b"\x00" * (6 * 1_048_576)
    response = await _upload(client, owner, built["paneer"]["id"], too_big, "big.png", "image/png")
    assert response.status_code == 413
    # The raised limit applies only to the upload route.
    json_bomb = await client.post(
        f"{M}/categories",
        headers=owner,
        content=b'{"name":"' + b"a" * 2_000_000 + b'"}',
    )
    assert json_bomb.status_code == 413


async def test_only_managers_upload(client: httpx.AsyncClient, seed: Seeder) -> None:
    alpha = await seed.restaurant("alpha")
    await seed.user(alpha, "kitchen", "chef")
    owner = await staff_headers(client, "alpha", "owner")
    built = await build_menu(client, owner)
    chef = await staff_headers(client, "alpha", "chef")
    response = await _upload(client, chef, built["paneer"]["id"], make_image())
    assert response.status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/media/../app/main.py",
        "/media/items/../../pyproject.toml",
        "/media/items/not-a-uuid/AAAAAAAAAAAAAAAAAAAAAA.webp",
        "/media/items/00000000-0000-0000-0000-000000000000/AAAAAAAAAAAAAAAAAAAAAA.webp",
        "/media/items/00000000-0000-0000-0000-000000000000/AAAAAAAAAAAAAAAAAAAAAA.html",
        "/media/%2e%2e/%2e%2e/backend/.env",
    ],
)
async def test_media_route_cannot_escape_its_root(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 404


def test_storage_rejects_unsafe_keys(tmp_path: object) -> None:
    storage = MediaStorage(tmp_path)  # type: ignore[arg-type]
    for key in ("../x.webp", "items/../../x.webp", "/etc/passwd", "items/a/b.webp", "x.html"):
        assert storage.path_for(key) is None
