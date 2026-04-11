"""
Rate limiter — async token-bucket with continuous refill.

Tokens refill based on elapsed wall-clock time, so the limiter
adapts to real scheduling jitter without manual resets.
"""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """
    Async token-bucket rate limiter.

    - Tokens refill continuously based on elapsed time.
    - Never resets tokens to zero manually.
    - Supports partial token consumption and burst up to *burst*.

    Args:
        rate:  Tokens per second (clamped to a minimum of 0.001).
        burst: Maximum tokens that can accumulate (clamped to >= 1).
    """

    def __init__(self, rate: float, burst: int = 1) -> None:
        self.rate = max(rate, 0.001)
        self.burst = max(burst, 1)
        self._tokens: float = float(self.burst)
        self._last: float = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Acquire one token, sleeping if necessary.

        Refills tokens based on elapsed time, then consumes exactly one.
        Any fractional token remainder is preserved across calls.
        """
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(float(self.burst), self._tokens + elapsed * self.rate)
            self._last = now

            if self._tokens < 1.0:
                deficit = 1.0 - self._tokens
                wait = deficit / self.rate
                await asyncio.sleep(wait)
                self._tokens = min(
                    float(self.burst),
                    self._tokens + wait * self.rate,
                )
                self._last = time.monotonic()

            self._tokens -= 1.0
