"""Guard against invisible / bidirectional control characters in source ("Trojan Source").

Tests that need such characters must build them with chr(), never embed them literally.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".venv", ".devdb", "node_modules", ".dart_tool", "build", "__pycache__", ".git"}
SUFFIXES = {".py", ".md", ".sql", ".toml", ".ini", ".dart", ".ts", ".js", ".html", ".css"}


def test_no_invisible_or_bidi_characters_in_source() -> None:
    offenders = []
    for path in ROOT.rglob("*"):
        if path.suffix not in SUFFIXES or SKIP_DIRS & set(path.parts):
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for ch in line:
                if ch != "\t" and unicodedata.category(ch) in ("Cc", "Cf"):
                    offenders.append(f"{path.relative_to(ROOT)}:{lineno} U+{ord(ch):04X}")
    assert offenders == []
