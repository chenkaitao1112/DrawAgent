from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _slugify(value: str, fallback: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", (value or "").strip()).strip("_").lower()
    return text or fallback


class MessageEnvelope(StrictModel):
    id: str
    from_role: str
    to_role: str
    message_type: str
    status: Literal["pending", "processed"] = "pending"
    task_id: str | None = None
    task_type: str | None = None
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class VirtualTask(StrictModel):
    task_id: str
    task_type: str
    agent_name: str
    agent_description: str = ""
    prompt_name: str = ""
    model_role: str = ""
    output_contract: str = "text_artifact"
    output_ref: str
    input_refs: list[str] = Field(default_factory=list)
    allowed_input_refs: list[str] = Field(default_factory=list)
    artifact_aliases: list[str] = Field(default_factory=list)
    artifact_channels: list[str] = Field(default_factory=list)
    artifact_channel_sources: dict[str, dict[str, str]] = Field(default_factory=dict)
    dedupe_artifact_inputs: bool = False
    stage_goal: str = ""
    stage_role: str = ""
    temperature: float = 0.2
    max_tokens: int = 1800
    review_required: bool = True
    review_phase: str | None = None
    retry_count: int = 0
    max_retry: int = 3
    revision_mode: bool = False
    revision_context: dict[str, Any] = Field(default_factory=dict)


class TextArtifactEnvelope(StrictModel):
    key: str
    value: str = ""


class LogicArtifactEnvelope(TextArtifactEnvelope):
    key: Literal["logician"] = "logician"
    value: str = ""


class StyleArtifactEnvelope(TextArtifactEnvelope):
    key: Literal["style_designer"] = "style_designer"
    value: str = ""


class MapperArtifactEnvelope(TextArtifactEnvelope):
    key: Literal["visual_mapper"] = "visual_mapper"
    value: str = ""


class FinalArtifactEnvelope(TextArtifactEnvelope):
    key: Literal["summarizer"] = "summarizer"
    value: str = ""


class ImageArtifactValue(StrictModel):
    image_url: str = ""
    image_b64: str = ""
    image_mime_type: str = ""
    revised_prompt: str = ""
    local_path: str = ""
    public_url: str = ""
    image_attempt_id: str = ""
    provider_request_id: str = ""
    provider_request_path: str = ""
    provider_request_url: str = ""
    provider_request_headers: dict[str, str] = Field(default_factory=dict)
    provider_status_code: int = 0
    provider_started_at: str = ""
    provider_finished_at: str = ""
    provider_duration_ms: int = 0


class ImageArtifactEnvelope(StrictModel):
    key: Literal["image_generator"] = "image_generator"
    value: ImageArtifactValue


class ReviewVerdict(StrictModel):
    approved: bool
    signal: Literal["positive", "negative", "fatal"] | None = None
    blocking: bool = False
    retry_targets: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def _normalize_signal(self) -> "ReviewVerdict":
        if self.signal is None:
            self.signal = "positive" if self.approved else "negative"
        if self.signal == "positive":
            self.approved = True
        elif self.signal in {"negative", "fatal"}:
            self.approved = False
        return self
