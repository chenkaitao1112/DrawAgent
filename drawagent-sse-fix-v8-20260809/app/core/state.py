from __future__ import annotations

from typing import Any, TypedDict


class WorkflowState(TypedDict, total=False):
    session_id: str
    user_input: str
    document_context: dict[str, Any]
    document_context_summary: dict[str, Any]
    primary_discipline: str
    conference_name: str
    user_preferences: str
    missing_clarification_fields: list[str]
    selected_skill: str
    detected_intent: str
    target_skill: str
    routing_reason: str
    skill_plan: list[dict[str, Any]]
    orchestration_plan: dict[str, Any]
    awaiting_orchestration_confirmation: bool
    orchestration_confirmed: bool
    messages: list[dict[str, Any]]
    pending_tasks: list[dict[str, Any]]
    active_task: dict[str, Any] | None
    active_tasks: list[dict[str, Any]]
    active_task_run_ids: list[str]
    completed_tasks: list[dict[str, Any]]
    artifacts: dict[str, Any]
    review_history: list[dict[str, Any]]
    revision_history: list[dict[str, Any]]
    warnings: list[str]
    stage: str
    next_hop: str
    awaiting_user_confirmation: bool
    stop_requested: bool
    released_with_warnings: bool
    prompt_checkpoint_id: str
    prompt_checkpoint_path: str
    image_attempt_id: str
    final_response: dict[str, Any]
    error: str
