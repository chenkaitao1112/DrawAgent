from __future__ import annotations

from copy import deepcopy
from uuid import uuid4


def append_message(state_messages: list[dict], **payload: object) -> list[dict]:
    messages = deepcopy(state_messages)
    message = {
        "id": str(uuid4()),
        "status": "pending",
    }
    message.update(payload)
    messages.append(message)
    return messages


def pop_next_pending_message(state_messages: list[dict], to_role: str = "controller") -> tuple[dict | None, list[dict]]:
    messages = deepcopy(state_messages)
    for index, message in enumerate(messages):
        if message.get("status") == "pending" and message.get("to_role") == to_role:
            messages[index]["status"] = "processed"
            return deepcopy(messages[index]), messages
    return None, messages

