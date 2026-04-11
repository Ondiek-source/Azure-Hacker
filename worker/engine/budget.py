"""
Resource budget — pre-request unified gate.

Checked BEFORE every request, never after.  Covers four dimensions:
runtime, bytes, request count, and estimated cloud cost.
"""

from __future__ import annotations

import time
from typing import Optional


# Azure Container Instances pricing (East US Linux, per second)
_COST_VCPU_PER_SEC = 0.0000216
_COST_MEM_GB_PER_SEC = 0.000003


class BudgetExhausted(Exception):
    """Resource budget consumed — graceful stop."""


class ResourceBudget:
    """
    Unified gate: time + bytes + requests + cost.

    Checked BEFORE every request — not after.

    Args:
        start_time:    ``time.time()`` at job start.
        max_seconds:   Hard runtime ceiling.
        max_bytes:     Total response bytes ceiling.
        max_requests:  Total request count ceiling.
        max_cost_usd:  Estimated cloud-cost ceiling.
        vcpus:         vCPU allocation (for cost calc).
        memory_gb:     Memory allocation in GB (for cost calc).
    """

    def __init__(
        self,
        start_time: float,
        max_seconds: float,
        max_bytes: int,
        max_requests: int,
        max_cost_usd: float = float("inf"),
        vcpus: float = 1.0,
        memory_gb: float = 1.5,
    ) -> None:
        self.start_time = start_time
        self.max_seconds = max_seconds
        self.max_bytes = max_bytes
        self.max_requests = max_requests
        self.max_cost_usd = max_cost_usd
        self.vcpus = vcpus
        self.memory_gb = memory_gb

        self.total_bytes: int = 0
        self.total_requests: int = 0

    # ── Properties ──

    @property
    def elapsed(self) -> float:
        """Seconds since ``start_time``."""
        return time.time() - self.start_time

    @property
    def cost_usd(self) -> float:
        """Estimated cloud cost based on elapsed time and resource allocation."""
        hours = self.elapsed / 3600
        hourly = (
            self.vcpus * _COST_VCPU_PER_SEC * 3600
            + self.memory_gb * _COST_MEM_GB_PER_SEC * 3600
        )
        return hourly * hours

    @property
    def ok(self) -> bool:
        """``True`` if all limits are still within budget."""
        if self.elapsed >= self.max_seconds:
            return False
        if self.total_bytes >= self.max_bytes:
            return False
        if self.total_requests >= self.max_requests:
            return False
        if self.cost_usd >= self.max_cost_usd:
            return False
        return True

    # ── Mutations ──

    def check(self) -> None:
        """Raise :class:`BudgetExhausted` if any limit is hit.

        Raises:
            BudgetExhausted: If elapsed time, bytes, requests, or cost
                exceed their respective limits.
        """
        if self.elapsed >= self.max_seconds:
            raise BudgetExhausted(f"Runtime limit: {self.max_seconds:.0f}s")

        if self.total_bytes >= self.max_bytes:
            raise BudgetExhausted(f"Byte limit: {self.max_bytes}")

        if self.total_requests >= self.max_requests:
            raise BudgetExhausted(f"Request limit: {self.max_requests}")

        if self.cost_usd >= self.max_cost_usd:
            raise BudgetExhausted(f"Cost limit: ${self.max_cost_usd:.4f}")

    def update(self, bytes_delta: int, requests_delta: int = 1) -> None:
        """Increment counters after a successful request.

        Args:
            bytes_delta:     Response bytes to add (clamped to >= 0).
            requests_delta:  Request count to add (default 1).
        """
        self.total_bytes += max(0, bytes_delta)
        self.total_requests += max(0, requests_delta)

    def exceeded_reason(self) -> Optional[str]:
        """Return a human-readable reason for budget exhaustion, or ``None``.

        Returns:
            String like ``"max_runtime (300s)"`` if a limit is hit,
            ``None`` if all limits are still within budget.
        """
        if self.elapsed >= self.max_seconds:
            return f"max_runtime ({self.max_seconds}s)"
        if self.total_bytes >= self.max_bytes:
            return f"max_bytes ({self.max_bytes})"
        if self.total_requests >= self.max_requests:
            return f"max_requests ({self.max_requests})"
        if self.cost_usd >= self.max_cost_usd:
            return f"max_cost (${self.max_cost_usd:.4f})"
        return None
