from __future__ import annotations

import logging
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from comet.utils.log_context import get_task_context


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class LogEntry:
    sequence: int
    timestamp: str
    task_id: str
    logger: str
    level: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "taskId": self.task_id,
            "logger": self.logger,
            "level": self.level,
            "message": self.message,
        }


@dataclass(slots=True)
class LogStreamState:
    task_id: str
    order: int
    status: str = "pending"
    started_at: str | None = None
    ended_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None
    first_entry_at: str | None = None
    last_entry_at: str | None = None
    total_entry_count: int = 0
    buffered_entry_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "taskId": self.task_id,
            "order": self.order,
            "status": self.status,
            "startedAt": self.started_at,
            "completedAt": self.completed_at,
            "endedAt": self.ended_at,
            "durationSeconds": self.duration_seconds,
            "firstEntryAt": self.first_entry_at,
            "lastEntryAt": self.last_entry_at,
            "bufferedEntryCount": self.buffered_entry_count,
            "totalEntryCount": self.total_entry_count,
        }


class RunLogRouter(logging.Handler):
    def __init__(self, max_entries_per_stream: int = 200) -> None:
        super().__init__()
        self.max_entries_per_stream = max_entries_per_stream
        self._lock = threading.RLock()
        self._sequence = 0
        self._stream_order = 0
        self._buffers: dict[str, deque[LogEntry]] = defaultdict(
            lambda: deque(maxlen=max_entries_per_stream)
        )
        self._streams: dict[str, LogStreamState] = {}
        self.ensure_stream("main", status="pending")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            task_id = getattr(record, "task_id", None) or get_task_context() or "main"
            entry = LogEntry(
                sequence=self._next_sequence(),
                timestamp=_utc_now_iso(),
                task_id=task_id,
                logger=record.name,
                level=record.levelname,
                message=record.getMessage(),
            )
            with self._lock:
                stream = self._ensure_stream_locked(task_id)
                if stream.started_at is None:
                    stream.started_at = entry.timestamp
                if stream.status not in {"completed", "failed"}:
                    stream.status = "running"
                if stream.first_entry_at is None:
                    stream.first_entry_at = entry.timestamp
                stream.last_entry_at = entry.timestamp
                stream.total_entry_count += 1
                self._buffers[task_id].append(entry)
                stream.buffered_entry_count = len(self._buffers[task_id])
        except Exception:
            self.handleError(record)

    def _next_sequence(self) -> int:
        with self._lock:
            self._sequence += 1
            return self._sequence

    def get_logs(self, task_id: str = "main") -> list[dict[str, Any]]:
        with self._lock:
            return [entry.to_dict() for entry in self._buffers.get(task_id, ())]

    def get_available_task_ids(self) -> list[str]:
        with self._lock:
            return [stream.task_id for stream in self._ordered_streams_locked()]

    def get_stream(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            stream = self._streams.get(task_id)
            return stream.to_dict() if stream is not None else None

    def ensure_stream(
        self,
        task_id: str,
        *,
        status: str | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        completed_at: str | None = None,
        duration_seconds: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            stream = self._ensure_stream_locked(task_id)
            self._merge_stream_state_locked(
                stream,
                status=status,
                started_at=started_at,
                ended_at=ended_at,
                completed_at=completed_at,
                duration_seconds=duration_seconds,
            )
            stream.buffered_entry_count = len(self._buffers.get(task_id, ()))
            return stream.to_dict()

    def sync_parallel_state(self, state: object) -> None:
        lifecycle_details = getattr(state, "get_task_lifecycle_details", None)
        if not callable(lifecycle_details):
            return

        details_result = lifecycle_details()
        if not isinstance(details_result, list):
            return
        details: list[dict[str, object]] = [
            target for target in details_result if isinstance(target, dict)
        ]

        with self._lock:
            for target in details:
                task_id = target.get("targetId")
                if not task_id:
                    continue
                status_value = target.get("status")
                status: str | None
                status = status_value if isinstance(status_value, str) else None
                started_at_value = target.get("startedAt")
                started_at: str | None
                started_at = (
                    started_at_value if isinstance(started_at_value, str) else None
                )
                ended_at_value = target.get("endedAt")
                ended_at: str | None
                ended_at = ended_at_value if isinstance(ended_at_value, str) else None
                completed_at_value = target.get("completedAt")
                completed_at: str | None
                completed_at = (
                    completed_at_value if isinstance(completed_at_value, str) else None
                )
                duration_value = target.get("durationSeconds")
                duration_seconds: float | None
                duration_seconds = (
                    float(duration_value)
                    if isinstance(duration_value, (int, float))
                    else None
                )
                stream = self._ensure_stream_locked(str(task_id))
                self._merge_stream_state_locked(
                    stream,
                    status=status,
                    started_at=started_at,
                    ended_at=ended_at,
                    completed_at=completed_at,
                    duration_seconds=duration_seconds,
                )
                stream.buffered_entry_count = len(self._buffers.get(str(task_id), ()))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            ordered_streams = self._ordered_streams_locked()
            items = [stream.to_dict() for stream in ordered_streams]
            counts = {
                stream.task_id: stream.buffered_entry_count
                for stream in ordered_streams
            }
        return {
            "taskIds": [stream["taskId"] for stream in items],
            "counts": counts,
            "maxEntriesPerStream": self.max_entries_per_stream,
            "items": items,
            "byTaskId": {stream["taskId"]: stream for stream in items},
        }

    def _ensure_stream_locked(
        self, task_id: str, order: int | None = None
    ) -> LogStreamState:
        stream = self._streams.get(task_id)
        if stream is not None:
            return stream

        next_order = self._stream_order if order is None else int(order)
        if order is None:
            self._stream_order += 1
        else:
            self._stream_order = max(self._stream_order, next_order + 1)

        stream = LogStreamState(task_id=task_id, order=next_order)
        self._streams[task_id] = stream
        return stream

    def _merge_stream_state_locked(
        self,
        stream: LogStreamState,
        *,
        status: str | None,
        started_at: str | None,
        ended_at: str | None,
        completed_at: str | None,
        duration_seconds: float | None,
    ) -> None:
        if started_at is not None and stream.started_at is None:
            stream.started_at = str(started_at)
        if status is not None:
            if stream.status in {"completed", "failed"} and status == "running":
                pass
            else:
                stream.status = str(status)
        if ended_at is not None:
            stream.ended_at = str(ended_at)
        if completed_at is not None:
            stream.completed_at = str(completed_at)
            if stream.ended_at is None:
                stream.ended_at = str(completed_at)
        if duration_seconds is not None:
            stream.duration_seconds = float(duration_seconds)

    def _ordered_streams_locked(self) -> list[LogStreamState]:
        return sorted(
            self._streams.values(),
            key=lambda stream: (stream.order, stream.started_at or "", stream.task_id),
        )
