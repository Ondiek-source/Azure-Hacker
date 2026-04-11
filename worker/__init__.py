"""
Worker package — multi-mode async scraper.
"""

from worker.exceptions import (
    FatalError,
    RetryableError,
    RetryExhausted,
    SkipError,
    SSRFError,
    WorkerError,
)
from worker.adapters.http_service import HttpService
from worker.adapters.storage_service import StorageService
from worker.engine.runner import JobRunner
from worker.domain.model import JobState, CycleResult
from worker.engine.budget import ResourceBudget, BudgetExhausted

__all__ = [
    "WorkerError",
    "RetryableError",
    "FatalError",
    "SkipError",
    "RetryExhausted",
    "SSRFError",
    "HttpService",
    "StorageService",
    "JobRunner",
    "JobState",
    "CycleResult",
    "ResourceBudget",
    "BudgetExhausted",
]
