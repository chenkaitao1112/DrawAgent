from __future__ import annotations

import json
import sys
from typing import Any


def debug_event(event: str, **payload: Any) -> None:
    line = f"[drawAgent-v2][{event}] {json.dumps(payload, ensure_ascii=False, default=str)}"
    try:
        print(line, file=sys.stderr)
    except UnicodeEncodeError:
        safe = line.encode(sys.stdout.encoding or "utf-8", errors="backslashreplace").decode(
            sys.stdout.encoding or "utf-8",
            errors="ignore",
        )
        print(safe, file=sys.stderr)
