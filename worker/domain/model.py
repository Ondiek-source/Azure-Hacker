from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta, timezone

from worker.domain.utils import iso_now


# ══════════════════════════════════════════════
#  CYCLE RESULT  (extractor return contract)
# ══════════════════════════════════════════════


@dataclass
class CycleResult:
    """Standardized return from every extractor."""

    records: List[Dict[str, Any]]
    has_data: bool
    latency_ms: float
    bytes: int
    next_cursor: Optional[str] = None
    query_params: Optional[Dict[str, Any]] = None
    host: str = ""


# ══════════════════════════════════════════════
#  JOB STATE  (single source of truth)
# ══════════════════════════════════════════════


@dataclass
class JobState:
    """
    All mutable job state in one place.
    All mutations go through methods — no scattered dict updates.
    """

    job_id: str
    mode: str
    max_requests: int = 5000

    state: str = "initializing"
    started_at: str = ""
    updated_at: str = ""
    progress: float = 0.0
    message: str = ""

    records_collected: int = 0
    iteration: int = 0
    request_count: int = 0
    failure_count: int = 0
    retry_count: int = 0
    total_bytes: int = 0
    total_latency_ms: float = 0.0

    page_start: Optional[int] = None
    page_end: Optional[int] = None
    cursor: Optional[str] = None
    last_cursor: Optional[str] = None
    max_iterations: Optional[int] = None
    query_params: Optional[Dict[str, Any]] = None

    is_preview_available: bool = False
    is_download_ready: bool = False
    requests_per_second: float = 0.0
    records_per_second: float = 0.0
    avg_latency_ms: float = 0.0
    estimated_completion_time: Optional[str] = None
    estimated_cost_usd: float = 0.0
    per_host_stats: Dict[str, Dict[str, Any]] = field(default_factory=lambda: {})

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = iso_now()
            self.updated_at = self.started_at

    def record_cycle(self, result: CycleResult) -> None:
        self.request_count += 1
        self.iteration += 1
        self.records_collected += len(result.records)
        self.total_bytes += result.bytes
        self.total_latency_ms += result.latency_ms
        self.is_preview_available = self.records_collected > 0
        if result.next_cursor is not None:
            self.last_cursor = self.cursor
            self.cursor = result.next_cursor
        if result.query_params is not None:
            self.query_params = result.query_params
        self.updated_at = iso_now()

    def record_failure(self) -> None:
        self.failure_count += 1
        self.updated_at = iso_now()

    def record_retries(self, count: int) -> None:
        self.retry_count += count

    def finalize(
        self,
        elapsed: float,
        cost_usd: float,
        interrupted: bool,
    ) -> None:
        self.state = "interrupted" if interrupted else "completed"
        self.is_download_ready = not interrupted
        self.requests_per_second = self.request_count / elapsed if elapsed > 0 else 0.0
        self.records_per_second = (
            self.records_collected / elapsed if elapsed > 0 else 0.0
        )
        self.avg_latency_ms = (
            self.total_latency_ms / self.request_count
            if self.request_count > 0
            else 0.0
        )
        self.estimated_cost_usd = round(cost_usd, 6)
        if 0 < self.progress < 1:
            remaining = elapsed / self.progress * (1 - self.progress)
            est = datetime.now(timezone.utc) + timedelta(seconds=remaining)
            self.estimated_completion_time = iso_now(est)
        self.message = (
            f"{'Interrupted' if interrupted else 'Completed'}. "
            f"{self.records_collected} records, "
            f"{self.request_count} requests, "
            f"{elapsed:.1f}s"
        )
        self.progress = 1.0 if not interrupted else self.progress
        self.updated_at = iso_now()

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "job_id": self.job_id,
            "mode": self.mode,
            "state": self.state,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "progress": round(self.progress, 4),
            "records_collected": self.records_collected,
            "max_requests": self.max_requests,
            "iteration": self.iteration,
            "request_count": self.request_count,
            "failure_count": self.failure_count,
            "retry_count": self.retry_count,
            "message": self.message,
            "total_bytes": self.total_bytes,
            "total_latency_ms": round(self.total_latency_ms, 2),
            "requests_per_second": round(self.requests_per_second, 4),
            "records_per_second": round(self.records_per_second, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "is_preview_available": self.is_preview_available,
            "is_download_ready": self.is_download_ready,
            "estimated_cost_usd": self.estimated_cost_usd,
            "per_host_stats": self.per_host_stats,
        }
        if self.page_start is not None:
            d["page_start"] = self.page_start
        if self.page_end is not None:
            d["page_end"] = self.page_end
        if self.cursor is not None:
            d["cursor"] = self.cursor
        if self.last_cursor is not None:
            d["last_cursor"] = self.last_cursor
        if self.max_iterations is not None:
            d["max_iterations"] = self.max_iterations
        if self.query_params is not None:
            d["query_params"] = self.query_params
        if self.estimated_completion_time is not None:
            d["estimated_completion_time"] = self.estimated_completion_time
        return d
