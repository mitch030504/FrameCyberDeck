"""Translations for user-facing text (GUI, help, patch descriptions).

Text is written in English in the code and wrapped: `tr("Install on Frame")`,
`tr("Install {title}").format(title=x)` (templates, never f-strings inside `tr()`, so the English text is the lookup
key), and `tr_n("{n} game", "{n} games", n)` for plurals. A translation is a JSON file `locales/<language>.json`
mapping each English text to the translated one (plural entries map to a list: [one, other]);
`scripts/i18n_extract.py` writes `locales/template.json` with every text that needs translating. Missing
translations fall back to English, so a partial translation is fine.

The language comes from the library setting `ui.language` (default: English). CLI output, logs and diagnostics stay
English.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

LOCALES = Path(__file__).with_name("locales")
_catalog: dict[str, str | list[str]] = {}
_language = "en"


def set_language(language: str | None) -> str:
    """Load a translation ("en" or None = English). Returns the language actually in use."""
    global _catalog, _language
    language = (language or "en").split("_")[0].split("-")[0].lower()
    path = LOCALES / f"{language}.json"
    if language == "en" or not path.exists():
        _catalog, _language = {}, "en"
    else:
        try:
            _catalog, _language = json.loads(path.read_text(encoding="utf-8")), language
        except (OSError, ValueError):
            _catalog, _language = {}, "en"
    return _language


def language() -> str:
    return _language


def available() -> list[str]:
    """Languages with a translation file, plus English."""
    return ["en"] + sorted(p.stem for p in LOCALES.glob("*.json") if p.stem not in ("en", "template"))


def language_name(code: str) -> str:
    """A language's own name, from its file's "_language" entry ("English" for en)."""
    if code == "en":
        return "English"
    try:
        return json.loads((LOCALES / f"{code}.json").read_text(encoding="utf-8")).get("_language") or code
    except (OSError, ValueError):
        return code


def tr(text: str) -> str:
    """The translation of an English text (the text itself when there is none)."""
    value = _catalog.get(text)
    return value if isinstance(value, str) and value else text


def tr_n(singular: str, plural: str, n: int, **kw) -> str:
    """Plural-aware text, formatted with n (and kw): tr_n("{n} game", "{n} games", count)."""
    value = _catalog.get(singular)
    if isinstance(value, list) and len(value) >= 2:
        text = value[0] if n == 1 else value[1]
    else:
        text = singular if n == 1 else plural
    return text.format(n=n, **kw)


# ------------------------------------------------------------------ numbers, sizes, dates (one place to localise)
def fmt_size(n: int | float, digits: int = 1) -> str:
    """Bytes as GiB / MiB / KiB."""
    n = float(n or 0)
    if n >= 2**30:
        return tr("{v} GiB").format(v=f"{n / 2**30:.{digits}f}")
    if n >= 2**20:
        return tr("{v} MiB").format(v=f"{n / 2**20:.0f}")
    return tr("{v} KiB").format(v=f"{max(n, 0) / 2**10:.0f}")


def fmt_datetime(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""


def fmt_percent(fraction: float) -> str:
    return f"{fraction:.0%}"
