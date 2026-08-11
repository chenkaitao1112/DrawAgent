from __future__ import annotations

from typing import Any

from app.core.models import (
    FinalArtifactEnvelope,
    ImageArtifactEnvelope,
    ImageArtifactValue,
    LogicArtifactEnvelope,
    MapperArtifactEnvelope,
    StyleArtifactEnvelope,
    TextArtifactEnvelope,
)


def _as_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, dict) else {}
    return value if isinstance(value, dict) else {}


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if hasattr(value, "model_dump"):
        return _coerce_text(value.model_dump())
    if isinstance(value, dict):
        nested = value.get("value")
        if isinstance(nested, str):
            return nested.strip()
    return str(value).strip()


def normalize_logic_artifact(artifact: Any) -> dict[str, Any]:
    data = _as_dict(artifact)
    if "value" in data:
        return LogicArtifactEnvelope(key=str(data.get("key") or "logician"), value=_coerce_text(data.get("value"))).model_dump()
    return LogicArtifactEnvelope(value=_coerce_text(artifact)).model_dump()


def normalize_style_artifact(artifact: Any) -> dict[str, Any]:
    data = _as_dict(artifact)
    if "value" in data:
        return StyleArtifactEnvelope(
            key=str(data.get("key") or "style_designer"),
            value=_coerce_text(data.get("value")),
        ).model_dump()
    return StyleArtifactEnvelope(value=_coerce_text(artifact)).model_dump()


def normalize_mapper_artifact(artifact: Any) -> dict[str, Any]:
    data = _as_dict(artifact)
    if "value" in data:
        return MapperArtifactEnvelope(
            key=str(data.get("key") or "visual_mapper"),
            value=_coerce_text(data.get("value")),
        ).model_dump()
    return MapperArtifactEnvelope(value=_coerce_text(artifact)).model_dump()


def normalize_final_artifact(artifact: Any) -> dict[str, Any]:
    data = _as_dict(artifact)
    if "value" in data:
        return FinalArtifactEnvelope(
            key=str(data.get("key") or "summarizer"),
            value=_coerce_text(data.get("value")),
        ).model_dump()
    return FinalArtifactEnvelope(value=_coerce_text(artifact)).model_dump()


def normalize_text_artifact(artifact: Any, *, key: str = "generic_worker") -> dict[str, Any]:
    data = _as_dict(artifact)
    if "value" in data:
        return TextArtifactEnvelope(
            key=str(data.get("key") or key),
            value=_coerce_text(data.get("value")),
        ).model_dump()
    return TextArtifactEnvelope(key=key, value=_coerce_text(artifact)).model_dump()


def normalize_image_artifact(artifact: Any) -> dict[str, Any]:
    data = _as_dict(artifact)
    if "value" in data:
        return ImageArtifactEnvelope(**data).model_dump()
    return ImageArtifactEnvelope(value=ImageArtifactValue(**data)).model_dump()
