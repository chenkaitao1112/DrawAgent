from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from app.core.models import VirtualTask


def completed_stage_names(completed_tasks: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("task_type")) for item in completed_tasks]


def _stage_dependencies(
    stage: dict[str, Any],
    previous_stage_names: list[str],
) -> list[str]:
    if "depends_on" in stage:
        raw_dependencies = stage.get("depends_on") or []
        if isinstance(raw_dependencies, str):
            return [raw_dependencies]
        return [str(item) for item in raw_dependencies]
    return list(previous_stage_names)


def ready_skill_stages(
    skill_plan: list[dict[str, Any]],
    completed_tasks: list[dict[str, Any]],
    *,
    active_tasks: list[dict[str, Any]] | None = None,
    include_image_generation: bool = False,
    max_parallel: int = 2,
) -> list[dict[str, Any]]:
    completed = set(completed_stage_names(completed_tasks))
    active = {str(item.get("task_type")) for item in (active_tasks or [])}
    previous_stage_names: list[str] = []
    ready: list[dict[str, Any]] = []

    for stage in skill_plan:
        stage_name = str(stage.get("stage") or "")
        if not stage_name:
            continue
        if stage_name == "image_generation" and not include_image_generation:
            previous_stage_names.append(stage_name)
            continue
        if stage_name in completed or stage_name in active:
            previous_stage_names.append(stage_name)
            continue

        dependencies = _stage_dependencies(stage, previous_stage_names)
        if all(item in completed for item in dependencies):
            ready.append(deepcopy(stage))
            if len(ready) >= max(1, max_parallel):
                break

        previous_stage_names.append(stage_name)

    return ready


def next_skill_stage(
    skill_plan: list[dict[str, Any]],
    completed_tasks: list[dict[str, Any]],
    *,
    include_image_generation: bool = False,
) -> dict[str, Any] | None:
    ready = ready_skill_stages(
        skill_plan,
        completed_tasks,
        include_image_generation=include_image_generation,
        max_parallel=1,
    )
    return ready[0] if ready else None


def build_virtual_task(
    *,
    stage_spec: dict[str, Any],
    max_retry: int,
    retry_count: int = 0,
    revision_mode: bool = False,
    revision_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_type = str(stage_spec.get("stage"))
    task = VirtualTask(
        task_id=str(uuid4()),
        task_type=task_type,
        agent_name=str(stage_spec.get("agent_name") or task_type),
        agent_description=str(stage_spec.get("agent_description") or ""),
        prompt_name=str(stage_spec.get("prompt_name") or ""),
        model_role=str(stage_spec.get("model_role") or ""),
        output_contract=str(stage_spec.get("output_contract") or "text_artifact"),
        output_ref=str(stage_spec.get("output_ref") or f"stage_outputs.{task_type}"),
        input_refs=list(stage_spec.get("input_refs") or []),
        allowed_input_refs=list(stage_spec.get("allowed_input_refs") or []),
        artifact_aliases=list(stage_spec.get("artifact_aliases") or []),
        artifact_channels=list(stage_spec.get("artifact_channels") or []),
        artifact_channel_sources=dict(stage_spec.get("artifact_channel_sources") or {}),
        dedupe_artifact_inputs=bool(stage_spec.get("dedupe_artifact_inputs", False)),
        stage_goal=str(stage_spec.get("stage_goal") or ""),
        stage_role=str(stage_spec.get("stage_role") or ""),
        temperature=float(stage_spec.get("temperature", 0.2) or 0.0),
        max_tokens=int(stage_spec.get("max_tokens", 1800) or 0),
        review_required=bool(stage_spec.get("review_required", True)),
        review_phase=stage_spec.get("review_phase"),
        retry_count=retry_count,
        max_retry=max_retry,
        revision_mode=revision_mode,
        revision_context=revision_context or {},
    )
    return task.model_dump()
