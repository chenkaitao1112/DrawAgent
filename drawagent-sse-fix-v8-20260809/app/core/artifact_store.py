from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.state import WorkflowState


class ArtifactStore:
    """Stores task artifacts in state and mirrors them to JSON files.

    State remains the compatibility surface for existing prompts and frontend
    code. The JSON file reference gives the runtime a stable handle for later
    context slimming and replay.
    """

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_name(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "").strip()).strip("_")
        return cleaned[:160] or "artifact"

    def _write_artifact_file(
        self,
        *,
        session_id: str,
        artifact_key: str,
        artifact: dict[str, Any],
    ) -> dict[str, Any]:
        safe_session = self._safe_name(session_id or "global")
        safe_key = self._safe_name(artifact_key)
        artifact_id = f"{safe_key}-{str(uuid4())[:8]}"
        session_dir = self.root_dir / safe_session
        session_dir.mkdir(parents=True, exist_ok=True)
        path = session_dir / f"{artifact_id}.json"
        path.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return {
            "artifact_id": artifact_id,
            "artifact_key": artifact_key,
            "path": str(path),
            "format": "json",
            "size_bytes": path.stat().st_size,
        }

    @staticmethod
    def stage_outputs(state: WorkflowState) -> dict[str, Any]:
        artifacts = state.get("artifacts", {}) or {}
        outputs = artifacts.get("stage_outputs", {})
        return outputs if isinstance(outputs, dict) else {}

    def artifact_for_stage_or_alias(
        self,
        state: WorkflowState,
        *,
        stage_name: str,
        alias: str,
    ) -> dict[str, Any]:
        artifacts = state.get("artifacts", {}) or {}
        output = self.stage_outputs(state).get(stage_name)
        if isinstance(output, dict):
            return output
        alias_value = artifacts.get(alias, {})
        return alias_value if isinstance(alias_value, dict) else {}

    def resolve_input_ref(self, state: WorkflowState, ref: str) -> Any:
        document_context = state.get("document_context", {}) or {}
        artifacts = state.get("artifacts", {}) or {}
        if ref == "document.full_text":
            return document_context.get("combined_text") or document_context.get("combined_excerpt", "") or ""
        if ref == "document.excerpt":
            return document_context.get("combined_excerpt", "") or ""
        if ref == "document.summary":
            return state.get("document_context_summary", {})
        if ref == "document.files":
            return [item.get("name") for item in document_context.get("files", [])]
        if ref == "user.input":
            return state.get("user_input", "")
        if ref == "user.primary_discipline":
            return state.get("primary_discipline", "")
        if ref == "user.conference_name":
            return state.get("conference_name", "")
        if ref in {"user.preferences", "user.user_preferences"}:
            return state.get("user_preferences", "")
        if ref.startswith("artifacts.stage_outputs."):
            stage_name = ref.removeprefix("artifacts.stage_outputs.")
            return self.stage_outputs(state).get(stage_name, {})
        if ref.startswith("artifacts."):
            artifact_name = ref.removeprefix("artifacts.")
            return artifacts.get(artifact_name, {})
        return ""

    def store_task_artifact(
        self,
        state: WorkflowState,
        *,
        task: dict[str, Any],
        artifact_ref: str,
        artifact: dict[str, Any],
    ) -> dict[str, Any] | None:
        artifacts = state.setdefault("artifacts", {})
        stage_outputs = artifacts.setdefault("stage_outputs", {})
        artifact_refs = artifacts.setdefault("artifact_refs", {})
        session_id = str(state.get("session_id") or "")
        task_type = str(task.get("task_type") or "")
        stored_ref: dict[str, Any] | None = None

        def register(key: str) -> None:
            nonlocal stored_ref
            if not key:
                return
            if stored_ref is None and session_id:
                stored_ref = self._write_artifact_file(
                    session_id=session_id,
                    artifact_key=key,
                    artifact=artifact,
                )
            if stored_ref is not None:
                artifact_refs[key] = stored_ref

        if task_type:
            stage_outputs[task_type] = artifact
            register(f"stage_outputs.{task_type}")

        if artifact_ref:
            if artifact_ref.startswith("stage_outputs."):
                stage_name = artifact_ref.removeprefix("stage_outputs.")
                if stage_name:
                    stage_outputs[stage_name] = artifact
                    register(f"stage_outputs.{stage_name}")
            else:
                artifacts[artifact_ref] = artifact
                register(artifact_ref)

        for alias in task.get("artifact_aliases", []) or []:
            alias = str(alias)
            if alias:
                artifacts[alias] = artifact
                register(alias)

        return stored_ref

    def artifact_refs_for_inputs(self, state: WorkflowState, input_refs: list[str]) -> dict[str, Any]:
        artifact_refs = (state.get("artifacts", {}) or {}).get("artifact_refs", {})
        if not isinstance(artifact_refs, dict):
            return {}
        selected: dict[str, Any] = {}
        for ref in input_refs:
            key = ref.removeprefix("artifacts.") if ref.startswith("artifacts.") else ref
            if key in artifact_refs:
                selected[ref] = artifact_refs[key]
            elif key.startswith("stage_outputs."):
                alt_key = key
                if alt_key in artifact_refs:
                    selected[ref] = artifact_refs[alt_key]
        return selected
