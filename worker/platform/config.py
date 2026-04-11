"""
Configuration — URL sanitization and config validation.

Loaded from JSON, validated against bounds and mode-specific rules,
and returned as a plain dict ready for JobRunner consumption.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List
from urllib.parse import urlparse


def sanitize_url(url: str, config: Dict[str, Any]) -> str:
    """Validate URL scheme and check against allow/deny lists.

    Args:
        url:    Absolute URL to sanitize.
        config: Job config (may contain ``allowlist`` and ``denylist``).

    Returns:
        The validated URL unchanged.

    Raises:
        ValueError: If the URL is malformed, uses an unsupported scheme,
                    matches a denylist entry, or misses the allowlist.
    """
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid URL: {url}")
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported scheme: {parsed.scheme}")

    denylist: List[str] = config.get("denylist") or []
    for deny in denylist:
        if deny in url:
            raise ValueError(f"URL denied: {url}")

    allowlist: List[str] = config.get("allowlist") or []
    if allowlist:
        if not any(a in url for a in allowlist):
            raise ValueError(f"URL not in allowlist: {url}")

    return url


def validate_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize a raw job configuration dict.

    Checks required fields, enforces bounds, applies defaults,
    sanitizes the target URL, and validates mode-specific fields.

    Args:
        config: Raw dict loaded from JSON.

    Returns:
        The same dict, mutated in-place with defaults applied
        and values validated.

    Raises:
        ValueError: If any required field is missing, out of bounds,
                    or mode-specific requirements are not met.
    """
    required = [
        "job_id",
        "mode",
        "target_url",
        "output_dir",
        "max_requests",
        "max_runtime_minutes",
        "concurrency",
        "preview_rows",
        "max_bytes",
        "max_response_size",
        "requests_per_second_limit",
        "max_retries_per_cycle",
    ]
    for k in required:
        if k not in config:
            raise ValueError(f"Missing required field: {k}")

    # ── Bounds ──
    max_requests: int = config["max_requests"]
    max_bytes: int = config["max_bytes"]
    max_response_size: int = config["max_response_size"]
    requests_per_second_limit: float = config["requests_per_second_limit"]
    max_retries_per_cycle: int = config["max_retries_per_cycle"]

    if not (1 <= max_requests <= 10000):
        raise ValueError("max_requests must be 1..10000")
    if not (1 <= max_bytes <= 100 * 1024 * 1024):
        raise ValueError("max_bytes must be 1..100MB")
    if not (1 <= max_response_size <= 10 * 1024 * 1024):
        raise ValueError("max_response_size must be 1..10MB")
    if not (0.1 <= requests_per_second_limit <= 50):
        raise ValueError("requests_per_second_limit must be 0.1..50")
    if not (0 <= max_retries_per_cycle <= 10):
        raise ValueError("max_retries_per_cycle must be 0..10")

    # ── Defaults ──
    config.setdefault("per_host_concurrency", 1)
    config.setdefault("allowlist", None)
    config.setdefault("denylist", None)
    config.setdefault("max_cost_usd", 3.0)
    config.setdefault("dry_run", False)
    config.setdefault("ssrf_check", True)
    config["per_host_concurrency"] = max(1, config["per_host_concurrency"])

    # ── Sanitize ──
    config["target_url"] = sanitize_url(config["target_url"], config)
    config["output_dir"] = os.path.abspath(config["output_dir"])

    # ── Mode-specific ──
    mode: str = config["mode"]
    if mode == "paged":
        for k in ("page_start", "page_end"):
            if k not in config:
                raise ValueError(f"Paged mode requires {k}")
        page_start: int = config["page_start"]
        page_end: int = config["page_end"]
        if page_start > page_end:
            raise ValueError("page_start must be <= page_end")
        if page_end - page_start > 1000:
            raise ValueError("Page range too large (max 1000)")
    elif mode == "cursor":
        for k in ("cursor_param", "next_cursor_key"):
            if k not in config:
                raise ValueError(f"Cursor mode requires {k}")
        config.setdefault("initial_cursor", None)
    elif mode == "api_loop":
        for k in ("query_variations", "max_iterations"):
            if k not in config:
                raise ValueError(f"Api loop mode requires {k}")
        max_iterations: int = config["max_iterations"]
        if not (1 <= max_iterations <= 1000):
            raise ValueError("max_iterations must be 1..1000")
        variations: List[Any] = config["query_variations"]
        if len(variations) == 0:
            raise ValueError("query_variations must be non-empty list")
    else:
        raise ValueError(f"Unknown mode: {mode}")

    return config
