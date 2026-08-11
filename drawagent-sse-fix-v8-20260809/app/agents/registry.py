from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


ROLE_DEFAULT_AGENTS = {
    "logic": "logic_extractor",
    "style": "style_extractor",
    "visual_mapping": "visual_mapper",
    "final_prompt": "prompt_summarizer",
    "image": "image_generator",
    "generic": "generic_worker",
}


@dataclass(frozen=True)
class AgentProfile:
    name: str
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    prompt_name: str = "generic_virtual_agent"
    output_contract: str = "text_artifact"
    default_role: str = "generic"
    model_role: str = "worker_default"
    temperature: float = 0.2
    max_tokens: int = 1800
    review_phase: str = ""
    allowed_input_refs: list[str] = field(default_factory=lambda: ["*"])
    artifact_channels: list[str] = field(default_factory=list)
    artifact_channel_sources: dict[str, dict[str, str]] = field(default_factory=dict)
    dedupe_artifact_inputs: bool = False

    @property
    def allows_any_input(self) -> bool:
        return "*" in self.allowed_input_refs

    def filter_input_refs(self, refs: list[str]) -> list[str]:
        if self.allows_any_input:
            return list(refs)
        allowed = set(self.allowed_input_refs)
        return [ref for ref in refs if ref in allowed]


class AgentRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._profiles = self._load()
        self._aliases = self._build_aliases()

    @classmethod
    def from_root(cls, root_dir: Path) -> "AgentRegistry":
        return cls(root_dir / "config" / "agent_registry.yaml")

    def _load(self) -> dict[str, AgentProfile]:
        if not self.path.exists():
            return {}
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        profiles: dict[str, AgentProfile] = {}
        for name, payload in (raw.get("agents") or {}).items():
            data = payload if isinstance(payload, dict) else {}
            raw_sources = data.get("artifact_channel_sources", {}) or {}
            channel_sources = {
                str(channel): {
                    "stage": str((source or {}).get("stage") or ""),
                    "alias": str((source or {}).get("alias") or channel),
                }
                for channel, source in raw_sources.items()
                if isinstance(source, dict)
            }
            profiles[str(name)] = AgentProfile(
                name=str(name),
                description=str(data.get("description") or ""),
                aliases=[str(item) for item in data.get("aliases", []) or []],
                prompt_name=str(data.get("prompt_name") or "generic_virtual_agent"),
                output_contract=str(data.get("output_contract") or "text_artifact"),
                default_role=str(data.get("default_role") or "generic"),
                model_role=str(data.get("model_role") or "worker_default"),
                temperature=float(data.get("temperature", 0.2) or 0.0),
                max_tokens=int(data.get("max_tokens", 1800) or 0),
                review_phase=str(data.get("review_phase") or ""),
                allowed_input_refs=[str(item) for item in data.get("allowed_input_refs", ["*"]) or ["*"]],
                artifact_channels=[str(item) for item in data.get("artifact_channels", []) or []],
                artifact_channel_sources=channel_sources,
                dedupe_artifact_inputs=bool(data.get("dedupe_artifact_inputs", False)),
            )
        return profiles

    def _build_aliases(self) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for name, profile in self._profiles.items():
            aliases[name] = name
            for alias in profile.aliases:
                aliases[alias] = name
            if profile.default_role:
                aliases.setdefault(profile.default_role, name)
        return aliases

    def get(self, name: str, *, role: str = "generic") -> AgentProfile:
        candidate = str(name or "").strip()
        resolved = self._aliases.get(candidate)
        if resolved and resolved in self._profiles:
            return self._profiles[resolved]
        default_name = ROLE_DEFAULT_AGENTS.get(role, "generic_worker")
        return self._profiles.get(default_name) or AgentProfile(name=default_name, default_role=role)

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": profile.name,
                "description": profile.description,
                "aliases": profile.aliases,
                "prompt_name": profile.prompt_name,
                "output_contract": profile.output_contract,
                "default_role": profile.default_role,
                "model_role": profile.model_role,
                "allowed_input_refs": profile.allowed_input_refs,
                "artifact_channels": profile.artifact_channels,
                "artifact_channel_sources": profile.artifact_channel_sources,
                "dedupe_artifact_inputs": profile.dedupe_artifact_inputs,
            }
            for profile in self._profiles.values()
        ]

    def enrich_stage(self, stage: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(stage)
        role = str(enriched.get("stage_role") or enriched.get("role") or "generic")
        requested_agent = str(enriched.get("agent_name") or enriched.get("stage") or role)
        profile = self.get(requested_agent, role=role)
        input_refs = [str(item) for item in enriched.get("input_refs", []) or []]
        filtered_refs = profile.filter_input_refs(input_refs)
        enriched["agent_name"] = profile.name
        enriched["agent_description"] = profile.description
        enriched["prompt_name"] = str(enriched.get("prompt_name") or profile.prompt_name)
        enriched["output_contract"] = str(enriched.get("output_contract") or profile.output_contract)
        enriched["model_role"] = str(enriched.get("model_role") or profile.model_role)
        enriched["temperature"] = float(enriched.get("temperature", profile.temperature))
        enriched["max_tokens"] = int(enriched.get("max_tokens", profile.max_tokens))
        enriched["allowed_input_refs"] = list(profile.allowed_input_refs)
        enriched["artifact_channels"] = list(enriched.get("artifact_channels") or profile.artifact_channels)
        enriched["artifact_channel_sources"] = dict(
            enriched.get("artifact_channel_sources") or profile.artifact_channel_sources
        )
        enriched["dedupe_artifact_inputs"] = bool(
            enriched.get("dedupe_artifact_inputs", profile.dedupe_artifact_inputs)
        )
        enriched["input_refs"] = filtered_refs
        if not enriched.get("review_phase") and profile.review_phase:
            enriched["review_phase"] = profile.review_phase
        if not enriched.get("stage_role"):
            enriched["stage_role"] = profile.default_role
        return enriched
