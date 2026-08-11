from __future__ import annotations

import json
from typing import Any

from app.core.artifact_store import ArtifactStore
from app.core.state import WorkflowState


class WorkerContextBuilder:
    """Builds scoped worker payloads from task input refs and workflow state."""

    _COMPAT_PAYLOAD_FIELDS = ("payload_logic", "payload_style", "payload_mapper")

    def __init__(self, artifact_store: ArtifactStore) -> None:
        self.artifact_store = artifact_store

    @staticmethod
    def _value_summary(value: Any) -> dict[str, Any]:
        if isinstance(value, str):
            return {"type": "str", "chars": len(value)}
        if isinstance(value, dict):
            return {
                "type": "dict",
                "keys": sorted(str(key) for key in value.keys())[:20],
                "chars": len(json.dumps(value, ensure_ascii=False, default=str)),
            }
        if isinstance(value, list):
            return {
                "type": "list",
                "items": len(value),
                "chars": len(json.dumps(value, ensure_ascii=False, default=str)),
            }
        return {"type": type(value).__name__}

    def _filtered_input_refs(self, task: dict[str, Any]) -> list[str]:
        input_refs = [str(ref) for ref in task.get("input_refs", []) or []]
        allowed_input_refs = [str(ref) for ref in task.get("allowed_input_refs", []) or []]
        if allowed_input_refs and "*" not in allowed_input_refs:
            allowed = set(allowed_input_refs)
            input_refs = [ref for ref in input_refs if ref in allowed]
        return input_refs

    def _scoped_stage_outputs(self, state: WorkflowState, input_refs: list[str]) -> dict[str, Any]:
        all_outputs = self.artifact_store.stage_outputs(state)
        scoped: dict[str, Any] = {}
        for ref in input_refs:
            if not ref.startswith("artifacts.stage_outputs."):
                continue
            stage_name = ref.removeprefix("artifacts.stage_outputs.")
            if stage_name in all_outputs:
                scoped[stage_name] = all_outputs[stage_name]
        return scoped

    def _slim_artifact_value(
        self,
        *,
        source_ref: str,
        value: Any,
        artifact_refs: dict[str, Any],
    ) -> dict[str, Any]:
        slimmed: dict[str, Any] = {
            "_slimmed": True,
            "source_ref": source_ref,
            "manifest": self._value_summary(value),
        }
        artifact_ref = artifact_refs.get(source_ref)
        if artifact_ref:
            slimmed["artifact_ref"] = artifact_ref
        return slimmed

    def _slim_duplicate_artifact_inputs(
        self,
        *,
        enabled: bool,
        resolved_inputs: dict[str, Any],
        stage_outputs: dict[str, Any],
        artifact_refs: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not enabled:
            return resolved_inputs, stage_outputs

        slimmed_inputs: dict[str, Any] = {}
        for ref, value in resolved_inputs.items():
            if ref.startswith("artifacts.stage_outputs.") or ref.startswith("artifacts.payload_"):
                slimmed_inputs[ref] = self._slim_artifact_value(
                    source_ref=ref,
                    value=value,
                    artifact_refs=artifact_refs,
                )
            else:
                slimmed_inputs[ref] = value

        slimmed_stage_outputs: dict[str, Any] = {}
        for stage_name, value in stage_outputs.items():
            source_ref = f"artifacts.stage_outputs.{stage_name}"
            slimmed_stage_outputs[stage_name] = self._slim_artifact_value(
                source_ref=source_ref,
                value=value,
                artifact_refs=artifact_refs,
            )
        return slimmed_inputs, slimmed_stage_outputs

    def _slim_duplicate_document_inputs(
        self,
        *,
        document_excerpt: str,
        resolved_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        if not document_excerpt or "document.full_text" not in resolved_inputs:
            return resolved_inputs
        full_text = resolved_inputs.get("document.full_text")
        if not isinstance(full_text, str) or full_text != document_excerpt:
            return resolved_inputs
        slimmed = dict(resolved_inputs)
        slimmed["document.full_text"] = {
            "_slimmed": True,
            "source_ref": "document.full_text",
            "manifest": self._value_summary(full_text),
            "available_as": "document_excerpt",
        }
        return slimmed

    def _artifact_channel_payloads(
        self,
        state: WorkflowState,
        *,
        artifact_channels: list[str],
        artifact_channel_sources: dict[str, dict[str, str]],
        input_refs: list[str],
    ) -> dict[str, dict[str, Any]]:
        input_ref_set = set(input_refs)
        payloads: dict[str, dict[str, Any]] = {}
        requested_channels = set(artifact_channels) or set(artifact_channel_sources)
        for channel, source in artifact_channel_sources.items():
            stage_name = str(source.get("stage") or "")
            alias = str(source.get("alias") or channel)
            requested = (
                channel in requested_channels
                or f"artifacts.{alias}" in input_ref_set
                or (stage_name and f"artifacts.stage_outputs.{stage_name}" in input_ref_set)
            )
            payloads[channel] = (
                self.artifact_store.artifact_for_stage_or_alias(
                    state,
                    stage_name=stage_name,
                    alias=alias,
                )
                if requested
                else {}
            )
        for field in self._COMPAT_PAYLOAD_FIELDS:
            payloads.setdefault(field, {})
        return payloads

    def build_worker_payload(self, state: WorkflowState, task: dict[str, Any]) -> dict[str, Any]:
        task_type = str(task["task_type"])
        document_context = state.get("document_context", {}) or {}
        input_refs = self._filtered_input_refs(task)
        allowed_input_refs = list(task.get("allowed_input_refs") or [])
        artifact_channels = [str(item) for item in task.get("artifact_channels", []) or []]
        artifact_channel_sources = {
            str(channel): {
                "stage": str((source or {}).get("stage") or ""),
                "alias": str((source or {}).get("alias") or channel),
            }
            for channel, source in (task.get("artifact_channel_sources", {}) or {}).items()
            if isinstance(source, dict)
        }
        resolved_inputs = {
            ref: self.artifact_store.resolve_input_ref(state, ref)
            for ref in input_refs
        }
        stage_outputs = self._scoped_stage_outputs(state, input_refs)
        input_manifest = {
            ref: self._value_summary(value)
            for ref, value in resolved_inputs.items()
        }
        artifact_refs = self.artifact_store.artifact_refs_for_inputs(state, input_refs)
        payload_resolved_inputs, payload_stage_outputs = self._slim_duplicate_artifact_inputs(
            enabled=bool(task.get("dedupe_artifact_inputs", False)),
            resolved_inputs=resolved_inputs,
            stage_outputs=stage_outputs,
            artifact_refs=artifact_refs,
        )
        channel_payloads = self._artifact_channel_payloads(
            state,
            artifact_channels=artifact_channels,
            artifact_channel_sources=artifact_channel_sources,
            input_refs=input_refs,
        )
        payload = {
            "session_id": state.get("session_id", ""),
            "agent_name": task.get("agent_name") or task_type,
            "prompt_name": task.get("prompt_name", ""),
            "model_role": task.get("model_role", ""),
            "output_contract": task.get("output_contract", "text_artifact"),
            "stage_goal": task.get("stage_goal", ""),
            "stage_role": task.get("stage_role", ""),
            "input_refs": input_refs,
            "allowed_input_refs": allowed_input_refs,
            "artifact_channels": artifact_channels,
            "artifact_channel_sources": artifact_channel_sources,
            "dedupe_artifact_inputs": bool(task.get("dedupe_artifact_inputs", False)),
            "resolved_inputs": payload_resolved_inputs,
            "input_manifest": input_manifest,
            "artifact_refs": artifact_refs,
            "stage_outputs": payload_stage_outputs,
            "input_payload": payload_resolved_inputs,
            "temperature": task.get("temperature", 0.2),
            "max_tokens": task.get("max_tokens", 1800),
            "user_input": state.get("user_input", ""),
            "primary_discipline": state.get("primary_discipline", ""),
            "conference_name": state.get("conference_name", ""),
            "user_preferences": state.get("user_preferences", ""),
            "document_context_summary": state.get("document_context_summary", {}),
            "document_excerpt": document_context.get("combined_excerpt", ""),
            "payload_logic": channel_payloads["payload_logic"],
            "payload_style": channel_payloads["payload_style"],
            "payload_mapper": channel_payloads["payload_mapper"],
            "source_files": [
                item.get("name") for item in document_context.get("files", [])
            ],
        }
        if "document.full_text" in input_refs:
            payload["document_excerpt"] = (
                document_context.get("combined_text")
                or document_context.get("combined_excerpt", "")
                or ""
            )
            payload["resolved_inputs"] = self._slim_duplicate_document_inputs(
                document_excerpt=str(payload.get("document_excerpt") or ""),
                resolved_inputs=dict(payload.get("resolved_inputs") or {}),
            )
            payload["input_payload"] = payload["resolved_inputs"]
        if task.get("revision_mode"):
            payload["revision_context"] = task.get("revision_context", {})
        if task_type == "image_generation":
            return {
                "session_id": state.get("session_id", ""),
                "english_prompt": str(state.get("artifacts", {}).get("payload_final", {}).get("value", "") or ""),
                "checkpoint_id": state.get("prompt_checkpoint_id", ""),
                "image_attempt_id": state.get("image_attempt_id", ""),
            }
        return payload

    def build_review_task_input(
        self,
        task_input: dict[str, Any],
        *,
        excerpt_chars: int = 1200,
    ) -> dict[str, Any]:
        document_excerpt = str(task_input.get("document_excerpt") or "")
        input_refs = list(task_input.get("input_refs") or [])
        is_logic_full_text_review = (
            str(task_input.get("prompt_name") or "") == "logic_extraction"
            or str(task_input.get("agent_name") or "") in {"logic_extraction", "logic_extractor"}
        ) and "document.full_text" in input_refs
        review_excerpt_chars = len(document_excerpt) if is_logic_full_text_review else excerpt_chars
        return {
            "session_id": task_input.get("session_id", ""),
            "agent_name": task_input.get("agent_name", ""),
            "prompt_name": task_input.get("prompt_name", ""),
            "model_role": task_input.get("model_role", ""),
            "output_contract": task_input.get("output_contract", ""),
            "stage_goal": task_input.get("stage_goal", ""),
            "stage_role": task_input.get("stage_role", ""),
            "input_refs": input_refs,
            "allowed_input_refs": list(task_input.get("allowed_input_refs") or []),
            "input_manifest": dict(task_input.get("input_manifest") or {}),
            "artifact_refs": dict(task_input.get("artifact_refs") or {}),
            "artifact_channels": list(task_input.get("artifact_channels") or []),
            "document_context_summary": dict(task_input.get("document_context_summary") or {}),
            "source_files": list(task_input.get("source_files") or []),
            "primary_discipline": task_input.get("primary_discipline", ""),
            "conference_name": task_input.get("conference_name", ""),
            "user_preferences": task_input.get("user_preferences", ""),
            "user_input": task_input.get("user_input", ""),
            "document_excerpt_preview": document_excerpt[:review_excerpt_chars],
            "document_excerpt_chars": len(document_excerpt),
            "revision_mode": bool(task_input.get("revision_mode", False)),
            "revision_context": dict(task_input.get("revision_context") or {}),
        }

    def build_review_upstream_artifacts(
        self,
        state: WorkflowState,
        task: dict[str, Any] | str,
    ) -> dict[str, Any]:
        if isinstance(task, str):
            task = {"task_type": task}
        artifacts = state.get("artifacts", {})
        upstream: dict[str, Any] = {}
        for ref in self._filtered_input_refs(task):
            if ref.startswith("artifacts."):
                upstream[ref] = self.artifact_store.resolve_input_ref(state, ref)
        if upstream:
            return upstream
        artifact_channel_sources = task.get("artifact_channel_sources", {}) or {}
        return {
            channel: artifacts.get(
                str((artifact_channel_sources.get(channel) or {}).get("alias") or channel),
                {},
            )
            for channel in [str(item) for item in task.get("artifact_channels", []) or []]
            if str(channel).startswith("payload_")
        }
