"""Time helpers. as_of_date defaults to today (guide section 6) but can be pinned for demos/tests."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from app.core.config import get_settings


def today() -> date:
    override = get_settings().as_of_date_override
    if override:
        return date.fromisoformat(override)
    return datetime.now(timezone.utc).date()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_iso_date(value: Optional[str]) -> Optional[date]:
    """Parse YYYY-MM-DD; blank/None -> None; anything else raises ValueError."""
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    return date.fromisoformat(value)
