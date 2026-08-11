from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.models import (
    FinalArtifactEnvelope,
    ImageArtifactEnvelope,
    ImageArtifactValue,
    LogicArtifactEnvelope,
    MapperArtifactEnvelope,
    ReviewVerdict,
    StyleArtifactEnvelope,
)


class StrictArgsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactToolArgsBase(StrictArgsModel):
    artifact: str = ""
    summary: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_tool_args(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        data = dict(value)
        artifact = data.get("artifact")
        summary = data.get("summary", "")

        if isinstance(artifact, dict):
            artifact_copy = dict(artifact)
            nested_summary = artifact_copy.pop("summary", None)
            if (not summary) and isinstance(nested_summary, str):
                data["summary"] = nested_summary
            nested_value = artifact_copy.get("value")
            if isinstance(nested_value, str):
                data["artifact"] = nested_value
            else:
                data["artifact"] = json.dumps(artifact_copy, ensure_ascii=False, indent=2)
        elif artifact is None:
            data["artifact"] = ""
        elif not isinstance(artifact, str):
            data["artifact"] = str(artifact)

        return data


WorkerTaskType = str


class SelectSkillArgs(StrictArgsModel):
    skill_name: str
    reason: str


class RouteSkillDecisionArgs(StrictArgsModel):
    action: Literal["select_skill", "request_clarification"]
    skill_name: str = ""
    target_skill: str = ""
    detected_intent: str = ""
    question: str = ""
    reason: str
    primary_discipline: str = ""
    conference_name: str = ""
    user_preferences: str = ""
    missing_fields: list[str] = Field(default_factory=list)


class RoutePromptFollowupArgs(StrictArgsModel):
    intent: Literal["revise_current_prompt", "new_task", "clarification_needed"]
    scope: Literal["visual_only", "layout_change", "logic_change", "new_task", "unclear"]
    restart_from: Literal["prompt_reviser", "visual_mapping", "logic_extraction", "full_pipeline", "clarification"]
    reason: str
    question: str = ""


class RequestClarificationArgs(StrictArgsModel):
    question: str
    reason: str
    primary_discipline: str = ""
    conference_name: str = ""
    user_preferences: str = ""
    missing_fields: list[str] = Field(default_factory=list)


class DispatchVirtualTaskArgs(StrictArgsModel):
    task_type: WorkerTaskType
    reason: str
    revision_mode: bool = False
    release_after_failure: bool = False


class FinalizePromptArgs(StrictArgsModel):
    chinese_explanation: str
    release_with_warnings: bool = False
    warning_note: str = ""


class TriggerImageGenerationArgs(StrictArgsModel):
    reason: str


class LogicToolArgs(ArtifactToolArgsBase):
    artifact: str = ""


class StyleToolArgs(ArtifactToolArgsBase):
    artifact: str = ""


class MapperToolArgs(ArtifactToolArgsBase):
    artifact: str = ""


class FinalToolArgs(ArtifactToolArgsBase):
    artifact: str = ""


class GenericToolArgs(ArtifactToolArgsBase):
    artifact: str = ""


class ImageToolArgs(ArtifactToolArgsBase):
    artifact: ImageArtifactEnvelope | ImageArtifactValue = Field(default_factory=ImageArtifactValue)


class ReviewToolArgs(StrictArgsModel):
    verdict: ReviewVerdict
    summary: str = ""


class ReviewRequestArgs(StrictArgsModel):
    review_phase: str
    target_goal: str
    task_type: WorkerTaskType
    task_input: dict[str, Any] = Field(default_factory=dict)
    task_output: dict[str, Any] = Field(default_factory=dict)
    upstream_artifacts: dict[str, Any] = Field(default_factory=dict)
    prior_reviews: list[dict[str, Any]] = Field(default_factory=list)


class RunVirtualAgentArgs(StrictArgsModel):
    agent_type: WorkerTaskType
    session_id: str = ""
    agent_name: str = ""
    prompt_name: str = ""
    model_role: str = ""
    output_contract: str = ""
    stage_goal: str = ""
    stage_role: str = ""
    input_refs: list[str] = Field(default_factory=list)
    allowed_input_refs: list[str] = Field(default_factory=list)
    resolved_inputs: dict[str, Any] = Field(default_factory=dict)
    input_manifest: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: dict[str, Any] = Field(default_factory=dict)
    artifact_channels: list[str] = Field(default_factory=list)
    artifact_channel_sources: dict[str, Any] = Field(default_factory=dict)
    dedupe_artifact_inputs: bool = False
    stage_outputs: dict[str, Any] = Field(default_factory=dict)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    temperature: float | None = None
    max_tokens: int | None = None
    user_input: str = ""
    primary_discipline: str = ""
    conference_name: str = ""
    user_preferences: str = ""
    document_context_summary: dict[str, Any] = Field(default_factory=dict)
    document_excerpt: str = ""
    payload_logic: dict[str, Any] = Field(default_factory=dict)
    payload_style: dict[str, Any] = Field(default_factory=dict)
    payload_mapper: dict[str, Any] = Field(default_factory=dict)
    source_files: list[str] = Field(default_factory=list)
    revision_context: dict[str, Any] = Field(default_factory=dict)
    revision_mode: bool = False
