"""A printable sheet of table QR cards (one per table, four per A4 page) for the operator.

    python -m app.cli qr-sheet --code spice-garden --out qr-cards.html

Open the file in a browser and print it (A4, 100% scale); cut along the borders and place one
card on each table. The QR code holds only the table's link: it grants nothing without the
current table PIN (spec §13), which staff tell guests or show on a board.
"""

from __future__ import annotations

import html

from app.core.config import Settings
from app.models import Restaurant
from app.services import table_service
from app.services.table_service import TableView

_STYLE = """
@page { size: A4; margin: 10mm; }
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
       color: #111; background: #fff; }
.help { max-width: 190mm; margin: 0 auto 6mm; padding: 4mm; border: 1px dashed #999;
        font-size: 11pt; }
.sheet { display: grid; grid-template-columns: 1fr 1fr; gap: 6mm; width: 190mm; margin: 0 auto; }
.card { border: 1px solid #333; border-radius: 4mm; height: 130mm; padding: 7mm 6mm;
        display: flex; flex-direction: column; align-items: center; text-align: center;
        break-inside: avoid; page-break-inside: avoid; }
.restaurant { font-size: 13pt; font-weight: 600; }
.table { font-size: 26pt; font-weight: 800; margin: 2mm 0 3mm; }
.qr svg { width: 62mm; height: 62mm; display: block; }
.scan { font-size: 13pt; font-weight: 700; margin-top: 3mm; }
.pin { font-size: 11pt; margin-top: 2mm; }
.small { font-size: 8pt; color: #555; margin-top: auto; word-break: break-all; }
@media print { .help { display: none; } }
"""


def _card(restaurant: Restaurant, view: TableView, settings: Settings, hint: str) -> str:
    assert view.qr_token is not None
    url = table_service.qr_url(settings, view.qr_token)
    svg = table_service.render_qr_svg(url, scalable=True).decode("utf-8")
    return (
        '<section class="card">'
        f'<div class="restaurant">{html.escape(restaurant.name)}</div>'
        f'<div class="table">Table {html.escape(view.table.label)}</div>'
        f'<div class="qr">{svg}</div>'
        '<div class="scan">Scan to see the menu and order</div>'
        f'<div class="pin">{html.escape(hint)}</div>'
        f'<div class="small">No app or sign-up needed · {html.escape(url)}</div>'
        "</section>"
    )


def render(
    restaurant: Restaurant,
    tables: list[TableView],
    settings: Settings,
    *,
    hint: str = "You will need today's table PIN — please ask a staff member.",
) -> str:
    cards = [
        _card(restaurant, view, settings, hint)
        for view in tables
        if view.table.is_active and view.qr_token is not None
    ]
    title = html.escape(f"QR cards — {restaurant.name}")
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{title}</title><style>{_STYLE}</style></head><body>"
        '<p class="help">Print on A4 at 100% scale (no "fit to page"), then cut along the '
        f"borders. {len(cards)} tables. Scan one card with a phone before laminating: it must "
        "open this restaurant's table page. If a card is lost or copied, regenerate that "
        "table's QR code and print a new card — the old one stops working.</p>"
        f'<main class="sheet">{"".join(cards)}</main>'
        "</body></html>\n"
    )
