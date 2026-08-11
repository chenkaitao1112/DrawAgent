from __future__ import annotations

import re
from typing import Any


def classify_error(exc: BaseException | str) -> dict[str, Any]:
    message = str(exc)
    lowered = message.lower()
    status_code = 0
    matched = re.search(r"status=(\d{3})", message)
    if not matched:
        matched = re.search(r"\b(\d{3})\s+(?:too many requests|bad gateway|service unavailable|gateway timeout|internal server error)\b", lowered)
    if matched:
        status_code = int(matched.group(1))

    if "timeout" in lowered or "timed out" in lowered:
        code = "timeout"
    elif status_code in {401, 403} or "unauthorized" in lowered or "forbidden" in lowered:
        code = "auth_failed"
    elif status_code == 429 or "rate limit" in lowered or "too many requests" in lowered:
        code = "rate_limited"
    elif status_code >= 500 or "bad gateway" in lowered or "service unavailable" in lowered:
        code = "provider_unavailable"
    elif (
        "did not return" in lowered
        or "invalid" in lowered
        or "json" in lowered
        or "expecting value" in lowered
        or "text/event-stream" in lowered
        or "sse response" in lowered
    ):
        code = "invalid_provider_response"
    else:
        code = "runtime_error"

    return {
        "code": code,
        "message": message,
        "provider_status_code": status_code,
    }
