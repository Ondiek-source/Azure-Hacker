"""
JobRunner — Orchestrator only.

Wires dependencies, runs the main loop, handles shutdown signals.
Contains NO business logic, NO I/O, NO network code.
All concerns delegated to injected services.
"""

from __future__ import annotations

import logging
import signal
import time

from pathlib import Path
from typing import Any, Dict

from worker.adapters.network_policy import NetworkPolicy
from worker.engine.rate_limiter import RateLimiter
from worker.engine.host_pool import HostPool
from worker.engine.metrics import MetricsTracker
from worker.engine.budget import BudgetExhausted, ResourceBudget
from worker.domain.model import JobState
from worker.domain.extractors import (
    ApiLoopExtractor,
    BaseExtractor,
    CursorExtractor,
    PagedExtractor,
)
from worker.exceptions import FatalError, SkipError
from worker.adapters.http_service import HttpService
from worker.adapters.storage_service import StorageService

logger = logging.getLogger("worker")


class JobRunner:
    """
    Orchestrator: wires dependencies, runs loop, handles shutdown.

    Contains NO business logic, NO I/O, NO network code.
    All concerns delegated to injected services.

    Args:
        config: Validated job configuration dict.
    """

    MAX_EMPTY_CYCLES = 3
    STATUS_FLUSH_INTERVAL = 10

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self._shutdown = False

        # ── State ──
        self.state = JobState(
            job_id=config["job_id"],
            mode=config["mode"],
            max_requests=config["max_requests"],
        )

        # ── Budget ──
        self.budget = ResourceBudget(
            start_time=time.time(),
            max_seconds=config["max_runtime_minutes"] * 60,
            max_bytes=config["max_bytes"],
            max_requests=config["max_requests"],
            max_cost_usd=config.get("max_cost_usd", float("inf")),
        )

        # ── Storage ──
        self.storage = StorageService(Path(config["output_dir"]))

        # ── Network policy ──
        self.network_policy = NetworkPolicy(
            allowlist=config.get("allowlist"),
            denylist=config.get("denylist"),
            ssrf_check=config.get("ssrf_check", True),
        )

        # ── Rate limiter ──
        rps: float = config["requests_per_second_limit"]
        self.rate_limiter = RateLimiter(rate=rps, burst=max(1, int(rps)))

        # ── Host pool ──
        self.host_pool = HostPool(
            global_limit=config["concurrency"],
            per_host_limit=config["per_host_concurrency"],
        )

        # ── Metrics ──
        self.metrics = MetricsTracker()

        # ── HTTP service ──
        self.http = HttpService(
            budget=self.budget,
            host_pool=self.host_pool,
            rate_limiter=self.rate_limiter,
            network_policy=self.network_policy,
            metrics=self.metrics,
            max_response_size=config["max_response_size"],
            concurrency=config["concurrency"],
            max_retries=config["max_retries_per_cycle"],
        )

        # ── Extractor ──
        self.extractor = self._create_extractor()

    # ── Main entry point ──

    async def run(self) -> None:
        """Execute the full job lifecycle: setup → loop → finalize."""
        self._install_signals()

        if self.config.get("dry_run"):
            self.state.state = "completed"
            self.state.message = "Dry run completed"
            self.state.is_download_ready = True
            await self.storage.write_status(self.state)
            return

        self.state.state = "running"
        await self.storage.write_status(self.state)

        logger.info(
            "Job %s | mode=%s | target=%d | concurrency=%d | rps=%.1f",
            self.config["job_id"],
            self.config["mode"],
            self.config["max_requests"],
            self.config["concurrency"],
            self.config["requests_per_second_limit"],
        )

        await self.http.open()

        try:
            await self._main_loop()
        finally:
            await self.http.close()

        # ── Finalize ──
        await self.storage.flush()
        elapsed: float = self.budget.elapsed
        self.state.finalize(elapsed, self.budget.cost_usd, self._shutdown)
        self.state.per_host_stats = self.metrics.snapshot()
        await self.storage.generate_preview(self.config["preview_rows"])
        await self.storage.write_status(self.state)

        logger.info(
            "Job %s %s | %d records | %d requests | %.1fs | $%.4f",
            self.config["job_id"],
            self.state.state,
            self.state.records_collected,
            self.state.request_count,
            elapsed,
            self.state.estimated_cost_usd,
        )

    # ── Main loop ──

    async def _main_loop(self) -> None:
        consecutive_empty: int = 0

        while not self._shutdown:
            if not self.budget.ok:
                reason: str | None = self.budget.exceeded_reason()
                logger.info("Budget exhausted: %s", reason)
                break
            if self.extractor.should_stop(self.state):
                break
            if consecutive_empty >= self.MAX_EMPTY_CYCLES:
                logger.info(
                    "%d consecutive empty cycles — stopping",
                    consecutive_empty,
                )
                break

            try:
                result = await self.extractor.run_cycle(self.http, self.state)
            except BudgetExhausted as e:
                logger.info("Budget exhausted during cycle: %s", e)
                break
            except SkipError as e:
                consecutive_empty += 1
                logger.debug("Cycle skipped: %s", e)
                continue
            except FatalError as e:
                self.state.record_failure()
                logger.error("Fatal error: %s", e)
                break
            except Exception as e:
                self.state.record_failure()
                logger.error("Cycle %d failed: %s", self.state.iteration, e)
                if self.state.failure_count > 10:
                    logger.error("Too many failures — aborting")
                    break
                continue

            self.state.record_cycle(result)
            self.budget.update(result.bytes)

            self._update_progress()

            if result.has_data:
                consecutive_empty = 0
            else:
                consecutive_empty += 1

            await self.storage.append(result.records)

            if self.state.request_count % self.STATUS_FLUSH_INTERVAL == 0:
                await self.storage.write_status(self.state)

    # ── Helpers ──

    def _create_extractor(self) -> BaseExtractor:
        """Instantiate the correct extractor for the configured mode."""
        mode: str = self.config["mode"]
        if mode == "paged":
            return PagedExtractor(self.config)
        elif mode == "cursor":
            return CursorExtractor(self.config)
        elif mode == "api_loop":
            return ApiLoopExtractor(self.config)
        raise ValueError(f"Unknown mode: {mode}")

    def _update_progress(self) -> None:
        """Update ``state.progress`` based on mode-specific total."""
        if self.config["mode"] == "api_loop":
            total: int = len(self.config.get("query_variations", []))
        elif self.config["mode"] == "paged":
            total = self.config["page_end"] - self.config["page_start"] + 1
        else:
            total = max(self.state.iteration + 100, 1000)
        self.state.progress = min(1.0, self.state.iteration / max(1, total))

    def _install_signals(self) -> None:
        """Register SIGTERM/SIGINT for graceful shutdown."""

        def handler(signum: int, _frame: Any) -> None:
            logger.info("Signal %d — graceful shutdown", signum)
            self._shutdown = True

        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)
