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
import sys

from typing import Any, Dict

from worker.platform.config import validate_config
from worker.engine.runner import JobRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("worker")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Multi-mode async job worker v3")
    parser.add_argument("--config", required=True, help="Path to job config JSON")
    return parser.parse_args()


def main() -> int:
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


if __name__ == "__main__":
    raise SystemExit(main())
