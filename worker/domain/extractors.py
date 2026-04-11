"""
Extractors — pure logic, zero side effects.

Each extractor computes the next request parameters and returns a
``CycleResult``.  No I/O, no network, no state mutation — only pure
computation.  ``HttpService`` is imported under ``TYPE_CHECKING``
only to avoid a circular dependency at runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, cast
from urllib.parse import urlparse

from worker.domain.model import CycleResult, JobState

if TYPE_CHECKING:
    from worker.adapters.http_service import HttpService


def _coerce_records(items: list[object]) -> List[Dict[str, Any]]:
    """Filter a list to only dict elements, typed correctly."""
    return [cast(Dict[str, Any], item) for item in items if isinstance(item, dict)]


def _extract_records(data: Any) -> List[Dict[str, Any]]:
    """Extract a list of dicts from a JSON API response.

    Handles three common shapes:

    1. **Top-level list** — each element that is a dict is kept.
    2. **Wrapped list** — looks for common wrapper keys
       (``data``, ``items``, ``results``, ``rows``, ``records``).
    3. **Unwrapped dict** — if the dict contains only primitive values
       (str, int, float, bool, list, dict, None), it is returned as a
       single-element list.  This covers flat single-record responses.

    Args:
        data: Parsed JSON value (dict, list, or None).

    Returns:
        List of dicts suitable for CSV row emission.
    """
    obj: object = data

    if obj is None:
        return []

    if isinstance(obj, list):
        return _coerce_records(cast(list[object], obj))

    if isinstance(obj, dict):
        d: Dict[str, Any] = cast(Dict[str, Any], obj)
        for key in ("data", "items", "results", "rows", "records"):
            payload: Any = d.get(key)
            if isinstance(payload, list):
                return _coerce_records(cast(list[object], payload))

        vals: list[object] = list(cast(Dict[str, object], d).values())
        if all(
            isinstance(v, (str, int, float, bool, list, dict, type(None))) for v in vals
        ):
            return [d]

    return []


def _host(url: str) -> str:
    """Extract the netloc from a URL.

    Args:
        url: Absolute URL.

    Returns:
        Hostname + port (``netloc``) as a string.
    """
    return urlparse(url).netloc


class BaseExtractor:
    """Abstract base for all extraction modes.

    Subclasses must implement :meth:`should_stop` and :meth:`run_cycle`.

    Args:
        config: Validated job configuration dict.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config

    def should_stop(self, state: JobState) -> bool:
        """Return ``True`` if the job should terminate.

        Args:
            state: Current job state.
        """
        raise NotImplementedError

    async def run_cycle(
        self,
        http: HttpService,
        state: JobState,
    ) -> CycleResult:
        """Execute one extraction cycle.

        Args:
            http:  HTTP transport (used for fetching).
            state: Current job state (iteration, cursor, etc.).

        Returns:
            A :class:`CycleResult` with records and metadata.
        """
        raise NotImplementedError


class PagedExtractor(BaseExtractor):
    """Page-number iteration: ``{page=N}`` in the URL template.

    Stops when ``state.iteration`` reaches the configured page range.
    """

    def should_stop(self, state: JobState) -> bool:
        """Stop when all pages in [page_start, page_end] have been fetched."""
        page_range: int = self.config["page_end"] - self.config["page_start"] + 1
        return state.iteration >= page_range

    async def run_cycle(
        self,
        http: HttpService,
        state: JobState,
    ) -> CycleResult:
        page: int = self.config["page_start"] + state.iteration
        url: str = self.config["target_url"].format(page=page)
        params: Dict[str, Any] = {"page": page}

        data, latency_ms, resp_bytes = await http.fetch(url, params)
        records: List[Dict[str, Any]] = _extract_records(data)

        return CycleResult(
            records=records,
            has_data=len(records) > 0,
            latency_ms=latency_ms,
            bytes=resp_bytes,
            query_params=params,
            host=_host(url),
        )


class CursorExtractor(BaseExtractor):
    """Cursor-based pagination: sends cursor token, extracts next from response.

    Stops only when the API explicitly signals no more pages:
    ``next_cursor`` is ``None`` **and** this is not the first cycle.
    """

    def should_stop(self, state: JobState) -> bool:
        """Stop when the API returned no next cursor after the first cycle."""
        return state.cursor is None and state.iteration > 0

    async def run_cycle(
        self,
        http: HttpService,
        state: JobState,
    ) -> CycleResult:
        params: Dict[str, Any] = {}
        if state.cursor:
            params[self.config["cursor_param"]] = state.cursor

        data, latency_ms, resp_bytes = await http.fetch(
            self.config["target_url"], params
        )
        records: List[Dict[str, Any]] = _extract_records(data)

        next_cursor: str | None = None
        if isinstance(data, dict):
            d: Dict[str, Any] = cast(Dict[str, Any], data)
            raw: Any = d.get(self.config["next_cursor_key"])
            if raw is not None and isinstance(raw, str) and 1 <= len(raw) <= 1000:
                next_cursor = raw

        return CycleResult(
            records=records,
            has_data=len(records) > 0,
            latency_ms=latency_ms,
            bytes=resp_bytes,
            next_cursor=next_cursor,
            query_params=params,
            host=_host(self.config["target_url"]),
        )


class ApiLoopExtractor(BaseExtractor):
    """Query-variation loop: iterates over a list of param dicts.

    Each cycle sends one entry from ``query_variations`` as the request
    params.  Stops when all variations have been tried.
    """

    def should_stop(self, state: JobState) -> bool:
        """Stop when all query variations have been exhausted."""
        variations: List[Dict[str, Any]] = self.config["query_variations"]
        return state.iteration >= len(variations)

    async def run_cycle(
        self,
        http: HttpService,
        state: JobState,
    ) -> CycleResult:
        variations: List[Dict[str, Any]] = self.config["query_variations"]
        idx: int = state.iteration
        params: Dict[str, Any] = variations[idx]

        data, latency_ms, resp_bytes = await http.fetch(
            self.config["target_url"], params
        )
        records: List[Dict[str, Any]] = _extract_records(data)

        return CycleResult(
            records=records,
            has_data=len(records) > 0,
            latency_ms=latency_ms,
            bytes=resp_bytes,
            query_params=params,
            host=_host(self.config["target_url"]),
        )
