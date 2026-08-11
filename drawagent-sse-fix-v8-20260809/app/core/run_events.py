from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


class RunEventStore:
    """Append-only JSONL event store for workflow and virtual task replay."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @staticmethod
    def _safe_name(value: str) -> str:
        cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value.strip())
        return cleaned or "global"

    def path_for(self, session_id: str) -> Path:
        return self.root_dir / f"{self._safe_name(session_id)}.jsonl"

    def append(self, event_type: str, *, session_id: str = "", **payload: Any) -> dict[str, Any]:
        event = {
            "event_id": str(uuid4()),
            "ts": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "session_id": session_id,
            **payload,
        }
        line = json.dumps(event, ensure_ascii=False, default=str)
        with self._lock:
            with self.path_for(session_id).open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.write("\n")
        return event

    def read(self, session_id: str) -> list[dict[str, Any]]:
        path = self.path_for(session_id)
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            events.append(json.loads(line))
        return events
