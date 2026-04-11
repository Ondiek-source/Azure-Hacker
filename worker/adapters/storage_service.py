"""
Storage service — append-only CSV + schema registry + atomic status writes.

Buffered writes flush on threshold or explicit ``flush()``.  Schema
expansion triggers a rare CSV rewrite so the header always matches
the widest column set seen so far.  Status is written atomically
via temp file + ``os.replace``.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import tempfile

from pathlib import Path
from typing import Any, Dict, List

from worker.domain.model import JobState
from worker.domain.utils import safe_unlink

logger = logging.getLogger("worker")


class StorageService:
    """
    Append-only CSV writer + evolving schema registry + atomic status.

    Design:
      full.csv       → append-only (rewritten only when schema expands)
      schema.json    → column registry (ordered, append-only growth)
      preview.csv    → generated once at end (first N rows)
      status.json    → atomic write (tmp + os.replace)

    Buffer:
      - Accumulates records up to ``FLUSH_THRESHOLD``.
      - Lock protects append + threshold check atomically.
      - Max buffer size caps memory under burst traffic.

    Args:
        output_dir: Directory for all output files (created if absent).
    """

    FLUSH_THRESHOLD = 1000
    MAX_BUFFER_SIZE = 5000  # backpressure cap

    def __init__(self, output_dir: Path) -> None:
        self._dir = output_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._full = self._dir / "full.csv"
        self._schema_path = self._dir / "schema.json"
        self._preview = self._dir / "preview.csv"
        self._status_path = self._dir / "status.json"

        # Schema
        self._fieldnames: List[str] = []
        self._fn_set: set[str] = set()
        self._load_schema()

        # Buffer
        self._buffer: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()

    # ── Schema persistence ──

    def _load_schema(self) -> None:
        """Load persisted column order from ``schema.json``."""
        if self._schema_path.exists():
            try:
                self._fieldnames = json.loads(
                    self._schema_path.read_text(encoding="utf-8")
                )
                self._fn_set = set(self._fieldnames)
            except (json.JSONDecodeError, OSError):
                self._fieldnames = []
                self._fn_set = set()

    def _save_schema(self) -> None:
        """Persist current column order to ``schema.json`` atomically."""
        fd, tmp = tempfile.mkstemp(dir=str(self._dir), suffix=".schema.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._fieldnames, f)
            os.replace(tmp, str(self._schema_path))
        except BaseException:
            safe_unlink(tmp)
            raise

    def _expand_schema(self, records: List[Dict[str, Any]]) -> bool:
        """Add unseen keys from *records* to the column registry.

        Args:
            records: Batch of dicts whose keys extend the schema.

        Returns:
            ``True`` if at least one new column was added.
        """
        changed = False
        for record in records:
            for key in record:
                if key not in self._fn_set:
                    self._fieldnames.append(key)
                    self._fn_set.add(key)
                    changed = True
        return changed

    # ── CSV append (append-only, schema-aware) ──

    async def append(self, records: List[Dict[str, Any]]) -> None:
        """Buffer records.  Flush when threshold reached.

        May rewrite the CSV only when the schema expands (rare).

        Args:
            records: Rows to append.
        """
        if not records:
            return

        async with self._lock:
            self._buffer.extend(records)
            if len(self._buffer) >= self.MAX_BUFFER_SIZE:
                logger.warning(
                    "Buffer at max capacity (%d), forcing flush",
                    len(self._buffer),
                )
                batch = self._buffer[:]
                self._buffer.clear()
            elif len(self._buffer) >= self.FLUSH_THRESHOLD:
                batch = self._buffer[:]
                self._buffer.clear()
            else:
                batch = None

        if batch is not None:
            await self._flush_batch(batch)

    async def flush(self) -> None:
        """Write all remaining buffered records."""
        async with self._lock:
            if not self._buffer:
                return
            batch = self._buffer[:]
            self._buffer.clear()

        await self._flush_batch(batch)

    async def _flush_batch(self, batch: List[Dict[str, Any]]) -> None:
        """Flush a batch — create, append, or rewrite depending on schema state.

        Args:
            batch: Records to write.
        """
        if not batch:
            return

        schema_changed = self._expand_schema(batch)
        if schema_changed:
            self._save_schema()

        exists = self._full.exists()
        needs_rewrite = schema_changed and exists

        if needs_rewrite:
            await self._rewrite_with_expanded_schema(batch)
        elif not exists:
            await self._create_csv(batch)
        else:
            await self._append_rows(batch)

    async def _create_csv(self, records: List[Dict[str, Any]]) -> None:
        """Create ``full.csv`` with header and initial rows.

        Args:
            records: First batch of rows.
        """
        fd, tmp = tempfile.mkstemp(dir=str(self._dir), suffix=".csv.tmp")
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=self._fieldnames,
                    extrasaction="ignore",
                )
                writer.writeheader()
                for row in records:
                    writer.writerow({fn: row.get(fn, "") for fn in self._fieldnames})
            os.replace(tmp, str(self._full))
        except BaseException:
            safe_unlink(tmp)
            raise

    async def _append_rows(self, records: List[Dict[str, Any]]) -> None:
        """Append rows to existing CSV (schema unchanged).

        Args:
            records: Rows to append.
        """
        fd, tmp = tempfile.mkstemp(dir=str(self._dir), suffix=".csv.tmp")
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
                with open(self._full, "r", newline="", encoding="utf-8") as src:
                    f.write(src.read())
                writer = csv.DictWriter(
                    f,
                    fieldnames=self._fieldnames,
                    extrasaction="ignore",
                )
                for row in records:
                    writer.writerow({fn: row.get(fn, "") for fn in self._fieldnames})
            os.replace(tmp, str(self._full))
        except BaseException:
            safe_unlink(tmp)
            raise

    async def _rewrite_with_expanded_schema(
        self, new_records: List[Dict[str, Any]]
    ) -> None:
        """Rewrite CSV with expanded column set.

        Old rows get empty values for new columns.  Only triggered when
        the schema actually changes (rare).

        Args:
            new_records: Records that introduced new columns.
        """
        fd, tmp = tempfile.mkstemp(dir=str(self._dir), suffix=".csv.tmp")
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=self._fieldnames,
                    extrasaction="ignore",
                )
                writer.writeheader()
                with open(self._full, "r", newline="", encoding="utf-8") as src:
                    reader = csv.DictReader(src)
                    for row in reader:
                        writer.writerow(
                            {fn: row.get(fn, "") for fn in self._fieldnames}
                        )
                for row in new_records:
                    writer.writerow({fn: row.get(fn, "") for fn in self._fieldnames})
            os.replace(tmp, str(self._full))
        except BaseException:
            safe_unlink(tmp)
            raise

    # ── Preview generation ──

    async def generate_preview(self, max_rows: int) -> None:
        """Read first *max_rows* rows from ``full.csv`` → ``preview.csv``.

        Args:
            max_rows: Maximum rows to include in the preview.
        """
        if not self._full.exists():
            return
        try:
            preview: List[Dict[str, str]] = []
            with open(self._full, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for i, row in enumerate(reader):
                    if i >= max_rows:
                        break
                    preview.append(dict(row))
            if not preview:
                return
            fns = list(preview[0].keys())
            fd, tmp = tempfile.mkstemp(dir=str(self._dir), suffix=".preview.tmp")
            try:
                with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=fns)
                    writer.writeheader()
                    writer.writerows(preview)
                os.replace(tmp, str(self._preview))
            except BaseException:
                safe_unlink(tmp)
                raise
        except Exception as e:
            logger.error("Preview generation failed: %s", e)

    # ── Status (atomic write) ──

    async def write_status(self, state: JobState) -> None:
        """Atomically write job status to ``status.json``.

        Args:
            state: Current job state to serialize.
        """
        content = json.dumps(state.to_dict(), indent=2)
        fd, tmp = tempfile.mkstemp(dir=str(self._dir), suffix=".status.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp, str(self._status_path))
        except BaseException:
            safe_unlink(tmp)
            raise
