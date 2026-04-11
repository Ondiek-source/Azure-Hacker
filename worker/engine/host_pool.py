"""
Host pool — per-host bounded semaphores with LRU eviction.

Provides a two-level concurrency gate: a global semaphore limits total
in-flight requests across all hosts, while per-host semaphores prevent
any single host from being overwhelmed.  An OrderedDict-backed LRU
evicts idle host slots when the pool reaches ``max_hosts``.
"""

from __future__ import annotations

import asyncio
import logging

from collections import OrderedDict
from types import TracebackType
from typing import Optional, Type

logger = logging.getLogger(__name__)


class HostPool:
    """
    Per-host concurrency with global semaphore + LRU eviction.

    Uses ``OrderedDict`` for correct LRU ordering (``dict.popitem``
    insertion order is CPython-implementation-dependent, not guaranteed).

    Args:
        global_limit:    Max concurrent requests across all hosts.
        per_host_limit:  Max concurrent requests per individual host.
        max_hosts:       Max distinct hosts tracked before LRU eviction.
    """

    def __init__(
        self,
        global_limit: int,
        per_host_limit: int,
        max_hosts: int = 512,
    ) -> None:
        self._global = asyncio.Semaphore(global_limit)
        self._per_host_limit = max(per_host_limit, 1)
        self._max_hosts = max_hosts
        self._hosts: OrderedDict[str, asyncio.Semaphore] = OrderedDict()
        self._lock = asyncio.Lock()

    async def acquire(self, host: str) -> _HostSlot:
        """Acquire a concurrency slot for *host*.

        Creates or refreshes the per-host semaphore, evicting the
        least-recently-used host if the pool is at capacity.

        Args:
            host: Hostname (``netloc``) to acquire a slot for.

        Returns:
            An async-context-manager that holds both the global and
            per-host semaphores for the duration of the ``async with`` block.
        """
        async with self._lock:
            if host in self._hosts:
                self._hosts.move_to_end(host)
            else:
                while len(self._hosts) >= self._max_hosts:
                    evicted, _ = self._hosts.popitem(last=False)
                    logger.debug("HostPool evicted: %s", evicted)
                self._hosts[host] = asyncio.Semaphore(self._per_host_limit)
            host_sem = self._hosts[host]

        return _HostSlot(self._global, host_sem)


class _HostSlot:
    """Async context manager that acquires both global + per-host semaphores.

    Acquisition order: global first, then per-host.
    Release order: per-host first, then global (reverse).
    """

    def __init__(
        self,
        global_sem: asyncio.Semaphore,
        host_sem: asyncio.Semaphore,
    ) -> None:
        self._global = global_sem
        self._host = host_sem

    async def __aenter__(self) -> _HostSlot:
        await self._global.__aenter__()
        await self._host.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self._host.__aexit__(exc_type, exc, tb)
        await self._global.__aexit__(exc_type, exc, tb)
