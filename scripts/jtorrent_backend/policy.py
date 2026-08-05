from __future__ import annotations

from .models import TorrentItem


class PolicyError(ValueError):
    """Deprecated compatibility exception.

    Policy enforcement was removed. The backend now treats config/sources.yml as
    the source allowlist: enabled sources are fetched and disabled sources are
    skipped.
    """


def validate_source(source: dict, policy: dict | None = None) -> None:
    """Deprecated no-op retained for old imports."""
    return None


def validate_item(item: TorrentItem, policy: dict | None = None) -> bool:
    """Deprecated no-op retained for old imports."""
    return True
