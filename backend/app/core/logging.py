from __future__ import annotations

import logging

from app.middleware.http import request_id_var


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


def configure_logging(level: str) -> None:
    root = logging.getLogger()
    if any(getattr(h, "_restaurant_handler", False) for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler._restaurant_handler = True  # type: ignore[attr-defined]
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s [req=%(request_id)s] %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level.upper())
