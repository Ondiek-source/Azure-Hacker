"""
Shared exception hierarchy for the worker.

Extracted here to avoid circular imports between worker.py, runner.py,
http_service.py, and network_policy.py.
"""

from __future__ import annotations


class WorkerError(Exception):
    """Base exception for all worker errors."""


class RetryableError(WorkerError):
    """Transient failure — safe to retry (429, 5xx, timeout, network)."""


class FatalError(WorkerError):
    """Permanent failure — abort immediately (4xx, SSRF, schema)."""


class SkipError(WorkerError):
    """Empty or non-fatal — skip this cycle, continue job."""


class RetryExhausted(WorkerError):
    """All retry attempts consumed."""

    def __init__(self, message: str, retry_count: int) -> None:
        super().__init__(message)
        self.retry_count = retry_count


class SSRFError(FatalError):
    """SSRF protection triggered."""
