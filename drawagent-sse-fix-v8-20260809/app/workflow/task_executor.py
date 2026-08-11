from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable
from uuid import uuid4

from app.core.run_events import RunEventStore


class VirtualTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class VirtualTaskRecord:
    run_id: str
    task_id: str
    session_id: str
    task_type: str
    status: VirtualTaskStatus
    started_at: str = ""
    completed_at: str = ""
    error: str = ""
    result: dict[str, Any] | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    future: Future[dict[str, Any]] | None = field(default=None, repr=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "task_type": self.task_type,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }


class VirtualTaskExecutor:
    """Lifecycle manager for virtual worker tasks.

    The executor keeps task records only while they are useful for cancellation,
    polling, or replay. Callers should cleanup terminal tasks after consuming
    results to avoid retaining large artifacts in memory.
    """

    def __init__(
        self,
        *,
        max_workers: int,
        event_store: RunEventStore | None = None,
    ) -> None:
        self.max_workers = max(1, int(max_workers or 1))
        self.event_store = event_store
        self._pool = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="drawagent-virtual-task",
        )
        self._lock = threading.Lock()
        self._records: dict[str, VirtualTaskRecord] = {}

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _event(self, event_type: str, record: VirtualTaskRecord, **payload: Any) -> None:
        if self.event_store is None:
            return
        self.event_store.append(
            event_type,
            session_id=record.session_id,
            task_run_id=record.run_id,
            task_id=record.task_id,
            task_type=record.task_type,
            status=record.status.value,
            **payload,
        )

    def submit(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        handler: Callable[[threading.Event], dict[str, Any]],
    ) -> str:
        task_id = str(task.get("task_id") or "")
        task_type = str(task.get("task_type") or "")
        run_id = f"{task_id or task_type}-{str(uuid4())[:8]}"
        record = VirtualTaskRecord(
            run_id=run_id,
            task_id=task_id,
            session_id=session_id,
            task_type=task_type,
            status=VirtualTaskStatus.PENDING,
        )
        with self._lock:
            self._records[run_id] = record
        self._event("task.submitted", record)

        def run() -> dict[str, Any]:
            with self._lock:
                record.status = VirtualTaskStatus.RUNNING
                record.started_at = self._now()
            self._event("task.started", record)
            try:
                if record.cancel_event.is_set():
                    with self._lock:
                        record.status = VirtualTaskStatus.CANCELLED
                        record.completed_at = self._now()
                    self._event("task.cancelled", record)
                    return {"task": task, "stage": "stopped", "messages": [], "reviews": [], "warnings": []}

                result = handler(record.cancel_event)
                with self._lock:
                    record.result = result
                    record.status = (
                        VirtualTaskStatus.CANCELLED
                        if record.cancel_event.is_set()
                        else VirtualTaskStatus.COMPLETED
                    )
                    record.completed_at = self._now()
                self._event("task.completed" if record.status == VirtualTaskStatus.COMPLETED else "task.cancelled", record)
                return result
            except BaseException as exc:
                with self._lock:
                    record.status = VirtualTaskStatus.FAILED
                    record.error = str(exc)
                    record.completed_at = self._now()
                self._event("task.failed", record, error=record.error)
                raise

        future = self._pool.submit(run)
        with self._lock:
            record.future = future
        return run_id

    def wait_many(self, run_ids: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for run_id in run_ids:
            record = self.get(run_id)
            if record is None or record.future is None:
                raise KeyError(run_id)
            results.append(record.future.result())
        return results

    def get(self, run_id: str) -> VirtualTaskRecord | None:
        with self._lock:
            return self._records.get(run_id)

    def list(self, *, session_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            records = list(self._records.values())
        if session_id is not None:
            records = [record for record in records if record.session_id == session_id]
        return [record.snapshot() for record in records]

    def cancel(self, run_id: str) -> bool:
        record = self.get(run_id)
        if record is None:
            return False
        record.cancel_event.set()
        if record.future is not None:
            record.future.cancel()
        self._event("task.cancel_requested", record)
        return True

    def cancel_session(self, session_id: str) -> int:
        with self._lock:
            run_ids = [
                run_id
                for run_id, record in self._records.items()
                if record.session_id == session_id
                and record.status in {VirtualTaskStatus.PENDING, VirtualTaskStatus.RUNNING}
            ]
        for run_id in run_ids:
            self.cancel(run_id)
        return len(run_ids)

    def cleanup(self, run_id: str) -> bool:
        with self._lock:
            record = self._records.get(run_id)
            if record is None:
                return False
            if record.status not in {
                VirtualTaskStatus.COMPLETED,
                VirtualTaskStatus.FAILED,
                VirtualTaskStatus.CANCELLED,
            }:
                return False
            del self._records[run_id]
        self._event("task.cleaned_up", record)
        return True

    def cleanup_many(self, run_ids: list[str]) -> None:
        for run_id in run_ids:
            self.cleanup(run_id)
