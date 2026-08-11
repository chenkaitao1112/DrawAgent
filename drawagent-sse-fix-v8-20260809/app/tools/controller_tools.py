from __future__ import annotations

from langchain_core.tools import tool

from app.tools.contracts import (
    DispatchVirtualTaskArgs,
    FinalizePromptArgs,
    RequestClarificationArgs,
    RoutePromptFollowupArgs,
    RouteSkillDecisionArgs,
    SelectSkillArgs,
    TriggerImageGenerationArgs,
)


@tool("select_skill", args_schema=SelectSkillArgs)
def select_skill(skill_name: str, reason: str) -> dict:
    """Select a skill for the current user request."""
    return {
        "action": "select_skill",
        "skill_name": skill_name,
        "reason": reason,
    }


@tool("route_skill_decision", args_schema=RouteSkillDecisionArgs)
def route_skill_decision(
    action: str,
    reason: str,
    skill_name: str = "",
    target_skill: str = "",
    detected_intent: str = "",
    question: str = "",
    primary_discipline: str = "",
    conference_name: str = "",
    user_preferences: str = "",
    missing_fields: list[str] | None = None,
) -> dict:
    """Choose whether to select a skill or request clarification for the current user request."""
    return {
        "action": action,
        "skill_name": skill_name,
        "target_skill": target_skill,
        "detected_intent": detected_intent,
        "question": question,
        "reason": reason,
        "primary_discipline": primary_discipline,
        "conference_name": conference_name,
        "user_preferences": user_preferences,
        "missing_fields": missing_fields or [],
    }


@tool("route_prompt_followup", args_schema=RoutePromptFollowupArgs)
def route_prompt_followup(
    intent: str,
    scope: str,
    restart_from: str,
    reason: str,
    question: str = "",
) -> dict:
    """Classify a follow-up request against the current reviewed drawing prompt."""
    return {
        "intent": intent,
        "scope": scope,
        "restart_from": restart_from,
        "reason": reason,
        "question": question,
    }


@tool("request_clarification", args_schema=RequestClarificationArgs)
def request_clarification(
    question: str,
    reason: str,
    primary_discipline: str = "",
    conference_name: str = "",
    user_preferences: str = "",
    missing_fields: list[str] | None = None,
) -> dict:
    """Ask the user for clarification when the current request is underspecified."""
    return {
        "action": "request_clarification",
        "question": question,
        "reason": reason,
        "primary_discipline": primary_discipline,
        "conference_name": conference_name,
        "user_preferences": user_preferences,
        "missing_fields": missing_fields or [],
    }


@tool("dispatch_virtual_task", args_schema=DispatchVirtualTaskArgs)
def dispatch_virtual_task(
    task_type: str,
    reason: str,
    revision_mode: bool = False,
    release_after_failure: bool = False,
) -> dict:
    """Dispatch a virtual worker task for the next workflow step or a revision step."""
    return {
        "action": "dispatch_virtual_task",
        "task_type": task_type,
        "reason": reason,
        "revision_mode": revision_mode,
        "release_after_failure": release_after_failure,
    }


@tool("finalize_prompt", args_schema=FinalizePromptArgs)
def finalize_prompt(
    chinese_explanation: str,
    release_with_warnings: bool = False,
    warning_note: str = "",
) -> dict:
    """Finalize the current prompt result and place the workflow into confirmation state."""
    return {
        "action": "finalize_prompt",
        "chinese_explanation": chinese_explanation,
        "release_with_warnings": release_with_warnings,
        "warning_note": warning_note,
    }


@tool("trigger_image_generation", args_schema=TriggerImageGenerationArgs)
def trigger_image_generation(reason: str) -> dict:
    """Trigger image generation after the user confirms the prompt checkpoint."""
    return {
        "action": "trigger_image_generation",
        "reason": reason,
    }


CONTROLLER_TOOLS = [
    route_prompt_followup,
    route_skill_decision,
    select_skill,
    request_clarification,
    dispatch_virtual_task,
    finalize_prompt,
    trigger_image_generation,
]
