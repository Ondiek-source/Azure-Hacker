"""
CLI — entry point for the worker.

Parses command-line arguments, loads and validates the config,
then runs the job via JobRunner.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from typing import Any, Dict

from worker.platform.config import validate_config
from worker.engine.runner import JobRunner

logger = logging.getLogger("worker")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Namespace with ``config`` path.
    """
    parser = argparse.ArgumentParser(description="Multi-mode async job worker v3")
    parser.add_argument("--config", required=True, help="Path to job config JSON")
    return parser.parse_args()


def main() -> int:
    """Load config, validate, run job.  Returns exit code.

    Returns:
        0 on success, 130 on interrupt, 1 on error.
    """
    args = parse_args()
    try:
        with open(args.config, "r", encoding="utf-8") as f:
            raw: Dict[str, Any] = json.load(f)
        config = validate_config(raw)
        runner = JobRunner(config)
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        logger.info("Interrupted")
        return 130
    except Exception as e:
        logger.error("Job failed: %s", e, exc_info=True)
        return 1
    return 0
