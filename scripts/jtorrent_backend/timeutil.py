from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from dateutil import parser as date_parser


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _first_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        for item in value.values():
            found = _first_value(item)
            if found not in [None, ""]:
                return found
        return None
    if isinstance(value, (list, tuple, set)):
        for item in value:
            found = _first_value(item)
            if found not in [None, ""]:
                return found
        return None
    return value


def to_date(value: Any) -> str | None:
    value = _first_value(value)
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value).strip()
    if not text:
        return None
    try:
        if "," in text and ":" in text:
            return parsedate_to_datetime(text).date().isoformat()
    except Exception:
        pass
    try:
        return date_parser.parse(text, fuzzy=True).date().isoformat()
    except Exception:
        return None
