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


class RunLogRouter(logging.Handler):
    def __init__(self, max_entries_per_stream: int = 200) -> None:
        super().__init__()
        self.max_entries_per_stream = max_entries_per_stream
        self._lock = threading.RLock()
        self._sequence = 0
        self._buffers: dict[str, deque[LogEntry]] = defaultdict(
            lambda: deque(maxlen=max_entries_per_stream)
        )

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
                self._buffers[task_id].append(entry)
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
            task_ids = sorted(self._buffers.keys())
        if "main" in task_ids:
            task_ids.remove("main")
            return ["main", *task_ids]
        return task_ids

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counts = {
                task_id: len(entries) for task_id, entries in self._buffers.items()
            }
        return {
            "taskIds": self.get_available_task_ids(),
            "counts": counts,
            "maxEntriesPerStream": self.max_entries_per_stream,
        }
