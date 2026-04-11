"""
Worker — multi-mode async scraper.

This module is the public surface of the ``worker`` package.
It sets up logging and re-exports the key classes so callers can do::

    from worker import JobRunner, HttpService, StorageService

Individual modules remain importable directly for finer-grained access.

Architecture:
    domain/         Pure logic, zero I/O
    engine/         Orchestration, scheduling, budgets
    adapters/       I/O: HTTP, CSV, network policy
    platform/       CLI, config validation
    exceptions.py   Shared exception hierarchy
"""

from __future__ import annotations

import logging
import sys

# ── Logging (configured once on import) ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# ── Azure Container Instances pricing (East US Linux, per second) ──
COST_VCPU_PER_SEC = 0.0000216
COST_MEM_GB_PER_SEC = 0.000003

# ── Public API re-exports ──
from worker.exceptions import (  # noqa: E402
    FatalError,
    RetryableError,
    RetryExhausted,
    SkipError,
    SSRFError,
    WorkerError,
)
from worker.adapters.http_service import HttpService  # noqa: E402
from worker.adapters.storage_service import StorageService  # noqa: E402
from worker.engine.runner import JobRunner  # noqa: E402
from worker.domain.model import JobState, CycleResult  # noqa: E402
from worker.engine.budget import ResourceBudget, BudgetExhausted  # noqa: E402

__all__ = [
    # Exceptions
    "WorkerError",
    "RetryableError",
    "FatalError",
    "SkipError",
    "RetryExhausted",
    "SSRFError",
    # Core services
    "HttpService",
    "StorageService",
    "JobRunner",
    # Domain
    "JobState",
    "CycleResult",
    # Engine
    "ResourceBudget",
    "BudgetExhausted",
    # Constants
    "COST_VCPU_PER_SEC",
    "COST_MEM_GB_PER_SEC",
]
