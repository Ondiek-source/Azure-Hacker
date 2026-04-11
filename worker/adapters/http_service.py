"""
HTTP service — async transport layer.

Owns the httpx client, rate limiter, host pool, and network policy.
Enforces budget before every request, classifies errors, and handles
redirects with SSRF validation on every hop.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import httpx

from worker.adapters.network_policy import NetworkPolicy
from worker.engine.rate_limiter import RateLimiter
from worker.engine.host_pool import HostPool
from worker.engine.metrics import MetricsTracker
from worker.engine.budget import BudgetExhausted, ResourceBudget
from worker.exceptions import (
    FatalError,
    RetryableError,
    RetryExhausted,
    SkipError,
)

logger = logging.getLogger("worker")


class HttpService:
    """
    Network transport layer.

    Owns: httpx.AsyncClient, rate limiter, host pool, network policy.
    Enforces budget BEFORE every request.
    Classifies errors: retryable / fatal / skip.
    Handles redirects with SSRF validation on every hop.

    Args:
        budget:            Resource gate checked before every request.
        host_pool:         Per-host concurrency limiter.
        rate_limiter:      Global RPS limiter.
        network_policy:    SSRF + allow/deny validation.
        metrics:           Per-host latency/failure tracker.
        max_response_size: Bytes ceiling for a single response.
        concurrency:       Max simultaneous connections.
        max_retries:       Max retry attempts per request.
    """

    MAX_REDIRECTS = 10

    def __init__(
        self,
        budget: ResourceBudget,
        host_pool: HostPool,
        rate_limiter: RateLimiter,
        network_policy: NetworkPolicy,
        metrics: MetricsTracker,
        max_response_size: int,
        concurrency: int,
        max_retries: int,
    ) -> None:
        self.budget = budget
        self.pool = host_pool
        self.rate_limiter = rate_limiter
        self.policy = network_policy
        self.metrics = metrics
        self.max_response_size = max_response_size
        self.max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None
        self._concurrency = concurrency

    async def open(self) -> None:
        """Create the underlying httpx.AsyncClient."""
        limits = httpx.Limits(
            max_connections=self._concurrency + 4,
            max_keepalive_connections=self._concurrency,
        )
        timeouts = httpx.Timeout(
            connect=10.0,
            read=30.0,
            write=10.0,
            pool=5.0,
        )
        self._client = httpx.AsyncClient(
            timeout=timeouts,
            limits=limits,
            follow_redirects=not self.policy.ssrf_check,
            http2=True,
        )

    async def close(self) -> None:
        """Gracefully close the httpx client."""
        if self._client:
            await self._client.aclose()

    async def fetch(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Any, float, int]:
        """Fetch JSON with full lifecycle.

        Order: budget check → SSRF → rate limit → host pool →
        retry loop → redirect handling → response validation.

        Args:
            url:    Target URL.
            params: Optional query parameters.

        Returns:
            ``(data, latency_ms, response_bytes)``

        Raises:
            BudgetExhausted:  Budget limit hit before request.
            FatalError:       4xx, SSRF, schema, or unexpected error.
            RetryExhausted:   All retry attempts consumed.
            SkipError:        Non-fatal, skip this cycle.
        """
        # ── Budget gate (pre-request) ──
        self.budget.check()

        # ── Initial URL validation ──
        self.policy.validate(url)

        # ── Rate limit ──
        await self.rate_limiter.acquire()

        # ── Host pool + retry loop ──
        host = urlparse(url).netloc
        last_exc: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                async with await self.pool.acquire(host):
                    start = time.time()
                    data, resp_bytes = await self._send_and_follow(url, params)
                    latency_ms = (time.time() - start) * 1000

                    self.metrics.record_success(host, latency_ms, resp_bytes)
                    return data, latency_ms, resp_bytes

            except RetryableError as e:
                last_exc = e
                self.metrics.record_failure(host)
                if attempt < self.max_retries - 1:
                    delay = min(
                        60.0,
                        max(
                            0.0,
                            1.0 * (2**attempt) + random.uniform(-0.5, 0.5),
                        ),
                    )
                    logger.warning(
                        "Retry %d/%d for %s: %s (wait %.1fs)",
                        attempt + 1,
                        self.max_retries,
                        host,
                        e,
                        delay,
                    )
                    await asyncio.sleep(delay)
                # else: fall through to raise RetryExhausted below
            except (FatalError, BudgetExhausted, SkipError):
                raise
            except Exception as e:
                raise FatalError(f"Unexpected: {e}") from e

        raise RetryExhausted(str(last_exc), self.max_retries) from last_exc

    # ── Send + redirect handling ──

    async def _send_and_follow(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Any, int]:
        """Send request, follow redirects with SSRF validation.

        Args:
            url:    Starting URL.
            params: Optional query parameters.

        Returns:
            ``(parsed_data, response_bytes)``

        Raises:
            FatalError: Too many redirects.
        """
        assert self._client is not None

        response = await self._client.get(url, params=params or {})
        redirect_count = 0

        while response.has_redirect_location and redirect_count < self.MAX_REDIRECTS:
            redirect_url = str(response.headers.get("location", ""))
            if not redirect_url:
                break

            if self.policy.ssrf_check:
                self.policy.validate(redirect_url)

            response = await self._client.get(redirect_url)
            redirect_count += 1

        if redirect_count >= self.MAX_REDIRECTS:
            raise FatalError("Too many redirects")

        return self._parse_response(response)

    # ── Response parsing + error classification ──

    def _parse_response(self, response: httpx.Response) -> Tuple[Any, int]:
        """Classify HTTP status, check size/content-type, parse body.

        Args:
            response: Raw httpx response.

        Returns:
            ``(parsed_data, response_bytes)``

        Raises:
            RetryableError: 429, 5xx, 408.
            FatalError:     4xx, oversized, bad content-type, JSON parse failure.
        """
        status = response.status_code

        if status == 429:
            raise RetryableError(f"Rate limited (HTTP {status})")
        if status >= 500:
            raise RetryableError(f"Server error (HTTP {status})")
        if status == 408:
            raise RetryableError("Request timeout (HTTP 408)")
        if status >= 400:
            raise FatalError(f"Client error (HTTP {status})")

        response.raise_for_status()

        resp_bytes = len(response.content)
        if resp_bytes > self.max_response_size:
            raise FatalError(
                f"Response too large: {resp_bytes} > {self.max_response_size}"
            )

        ct = response.headers.get("content-type", "").lower()
        if "json" not in ct and "text" not in ct:
            raise FatalError(f"Invalid content-type: {ct}")

        try:
            data = response.json()
        except ValueError:
            if "json" in ct:
                raise FatalError("JSON parse failed")
            data = {"raw_text": response.text}

        return data, resp_bytes
