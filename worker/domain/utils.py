"""
Domain utilities — pure helpers with zero external dependencies.

Lives at the innermost layer. Domain, engine, adapters, and platform
all depend on this module; it depends on nothing.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional


def iso_now(dt: Optional[datetime] = None) -> str:
    """Return an ISO-8601 UTC timestamp with second precision.

    Examples:
        >>> iso_now()
        '2025-04-11T12:00:00Z'
        >>> iso_now(datetime(2025, 1, 1, tzinfo=timezone.utc))
        '2025-01-01T00:00:00Z'

    Args:
        dt: Optional datetime. Defaults to ``datetime.now(timezone.utc)``.

    Returns:
        ISO-8601 string with ``+00:00`` replaced by ``Z``, microseconds stripped.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_unlink(path: str) -> None:
    """Remove a file, silently ignoring if it doesn't exist.

    Used for cleaning up temp files after ``os.replace`` or on error paths.

    Args:
        path: Filesystem path to delete.
    """
    try:
        os.unlink(path)
    except OSError:
        pass
