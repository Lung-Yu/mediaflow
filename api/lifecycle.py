"""Lifecycle retention parsing for the API container (no pipeline imports)."""
import logging
from datetime import timedelta

log = logging.getLogger(__name__)


def parse_retention(value: str) -> "timedelta | None":
    """Parse a retention string. Returns timedelta, or None for keep-forever.

    "immediate" → timedelta(0), "Nd" → timedelta(N), "forever"/"keep"/"" → None.
    Invalid values return None (safe fallback — no data deleted).
    """
    if not value or value in ("forever", "keep"):
        return None
    if value == "immediate":
        return timedelta(0)
    if isinstance(value, str) and value.endswith("d"):
        try:
            return timedelta(days=int(value[:-1]))
        except ValueError:
            pass
    log.warning("Unrecognised retention value %r — treating as forever", value)
    return None
