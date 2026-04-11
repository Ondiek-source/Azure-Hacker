"""
Metrics tracker — pure per-host and global aggregation.

No I/O, no side effects.  Feed it successes and failures,
call ``snapshot()`` when you need a serializable summary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class _HostStat:
    """Accumulators for a single host."""

    requests: int = 0
    failures: int = 0
    total_latency_ms: float = 0.0
    total_bytes: int = 0

    @property
    def avg_latency_ms(self) -> float:
        """Mean latency across all recorded requests."""
        return self.total_latency_ms / self.requests if self.requests > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict suitable for JSON output."""
        return {
            "requests": self.requests,
            "failures": self.failures,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "total_bytes": self.total_bytes,
        }


class MetricsTracker:
    """Per-host + global metrics.  No I/O, no side effects."""

    def __init__(self) -> None:
        self._hosts: Dict[str, _HostStat] = {}

    def _get(self, host: str) -> _HostStat:
        """Return the stat object for *host*, creating it on first access."""
        if host not in self._hosts:
            self._hosts[host] = _HostStat()
        return self._hosts[host]

    def record_success(self, host: str, latency_ms: float, byte_count: int) -> None:
        """Record a successful request.

        Args:
            host:       Target hostname.
            latency_ms: Round-trip latency in milliseconds.
            byte_count: Response size in bytes.
        """
        s = self._get(host)
        s.requests += 1
        s.total_latency_ms += latency_ms
        s.total_bytes += byte_count

    def record_failure(self, host: str) -> None:
        """Record a failed request for *host*."""
        self._get(host).failures += 1

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """Return all per-host stats as a plain dict.

        Returns:
            ``{host: {"requests": …, "failures": …, …}}``
        """
        return {h: s.to_dict() for h, s in self._hosts.items()}
