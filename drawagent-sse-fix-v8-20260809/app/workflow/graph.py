from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from app.core.logging import debug_event
from app.core.artifact_store import ArtifactStore
from app.core.errors import classify_error
from app.core.message_bus import append_message, pop_next_pending_message
from app.core.state import WorkflowState
from app.skills.validator import validate_skill_plan
from app.tools.controller_tools import (
    dispatch_virtual_task,
    finalize_prompt,
    request_clarification,
    route_skill_decision,
    select_skill,
    trigger_image_generation,
)
from app.workflow.task_factory import build_virtual_task, ready_skill_stages
from app.workflow.context_builder import WorkerContextBuilder
from app.workflow.task_executor import VirtualTaskExecutor


class DualNodeWorkflowGraph:
    def __init__(self, runtime) -> None:
        self.runtime = runtime
        self.context_builder = WorkerContextBuilder(self._artifact_store())
        self.graph = self._build().compile()

    def run(self, state: WorkflowState) -> WorkflowState:
        return self.graph.invoke(state)

    def _build(self) -> StateGraph:
        graph = StateGraph(WorkflowState)
        graph.add_node("controller_node", self._controller_node)
        graph.add_node("virtual_worker_node", self._virtual_worker_node)
        graph.add_edge(START, "controller_node")
        graph.add_conditional_edges(
            "controller_node",
            self._route_from_controller,
            {
                "virtual_worker_node": "virtual_worker_node",
                "end": END,
            },
        )
        graph.add_edge("virtual_worker_node", "controller_node")
        return graph

    @staticmethod
    def _route_from_controller(state: WorkflowState) -> Literal["virtual_worker_node", "end"]:
        return "virtual_worker_node" if state.get("next_hop") == "worker" else "end"

    def _max_concurrent_virtual_tasks(self) -> int:
        config = getattr(self.runtime, "config", None)
        return int(getattr(config, "max_concurrent_virtual_tasks", 2) or 2)

    @staticmethod
    def _current_active_tasks(state: WorkflowState) -> list[dict[str, Any]]:
        tasks = [deepcopy(item) for item in state.get("active_tasks", []) if item]
        legacy_task = deepcopy(state.get("active_task") or {})
        if legacy_task and not any(item.get("task_id") == legacy_task.get("task_id") for item in tasks):
            tasks.insert(0, legacy_task)
        return tasks

    @staticmethod
    def _set_active_tasks(state: WorkflowState, tasks: list[dict[str, Any]]) -> WorkflowState:
        clean_tasks = [deepcopy(item) for item in tasks if item]
        state["active_tasks"] = clean_tasks
        state["active_task"] = clean_tasks[0] if clean_tasks else None
        return state

    @staticmethod
    def _is_retryable_worker_error(error_payload: dict[str, Any]) -> bool:
        code = str(error_payload.get("code") or "")
        message = str(error_payload.get("message") or "").lower()
        return (
            code in {"rate_limited", "timeout", "provider_unavailable", "invalid_provider_response"}
            or "unexpected_eof_while_reading" in message
            or "too many requests" in message
            or "bad gateway" in message
            or "service unavailable" in message
        )

    @staticmethod
    def _stage_spec_for_retry(state: WorkflowState, active_task: dict[str, Any], task_type: str) -> dict[str, Any]:
        stage_spec = next(
            (item for item in state.get("skill_plan", []) if item.get("stage") == task_type),
            None,
        )
        if stage_spec is not None:
            return stage_spec
        return {
            **active_task,
            "stage": task_type,
            "agent_name": active_task.get("agent_name") or task_type,
        }

    @staticmethod
    def _normalize_optional_text(value: Any) -> str:
        return str(value or "").strip()

    def _extract_design_context_from_text(self, text: str) -> dict[str, str]:
        raw = str(text or "")
        if not raw.strip():
            return {}

        patterns = {
            "primary_discipline": [
                r"(?im)^\s*(?:primary[_\-\s]?discipline|一级学科|学科|领域)\s*[:：]\s*(.+?)\s*$",
            ],
            "conference_name": [
                r"(?im)^\s*(?:conference[_\-\s]?name|venue|target[_\-\s]?venue|会议|期刊|目标会议|目标期刊)\s*[:：]\s*(.+?)\s*$",
            ],
            "user_preferences": [
                r"(?im)^\s*(?:user[_\-\s]?preferences|preferences|特殊要求|用户偏好|偏好|额外要求)\s*[:：]\s*(.+?)\s*$",
            ],
        }

        extracted: dict[str, str] = {}
        for key, regexes in patterns.items():
            for pattern in regexes:
                match = re.search(pattern, raw)
                if match:
                    extracted[key] = match.group(1).strip()
                    break
        return extracted

    def _apply_design_context(self, state: WorkflowState, updates: dict[str, Any] | None) -> WorkflowState:
        updates = updates or {}
        for key in ("primary_discipline", "conference_name", "user_preferences"):
            value = self._normalize_optional_text(updates.get(key))
            if value:
                state[key] = value
        return state

    def _current_design_context(self, state: WorkflowState) -> dict[str, str]:
        return {
            "primary_discipline": self._normalize_optional_text(state.get("primary_discipline")),
            "conference_name": self._normalize_optional_text(state.get("conference_name")),
            "user_preferences": self._normalize_optional_text(state.get("user_preferences")),
        }

    def _strip_design_context_lines(self, text: str) -> str:
        raw = str(text or "")
        if not raw.strip():
            return ""

        patterns = [
            r"(?im)^\s*(?:primary[_\-\s]?discipline|一级学科|学科|领域)\s*[:：]\s*.+$",
            r"(?im)^\s*(?:conference[_\-\s]?name|venue|target[_\-\s]?venue|会议|期刊|目标会议|目标期刊)\s*[:：]\s*.+$",
            r"(?im)^\s*(?:user[_\-\s]?preferences|preferences|特殊要求|用户偏好|偏好|额外要求)\s*[:：]\s*.+$",
        ]
        cleaned = raw
        for pattern in patterns:
            cleaned = re.sub(pattern, "", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _has_source_material(self, state: WorkflowState) -> bool:
        document_context = state.get("document_context", {}) or {}
        if self._normalize_optional_text(document_context.get("combined_excerpt")):
            return True
        if self._normalize_optional_text(document_context.get("combined_text")):
            return True

        non_design_user_text = self._strip_design_context_lines(str(state.get("user_input", "") or ""))
        normalized = re.sub(r"\s+", " ", non_design_user_text).strip()
        if len(normalized) >= 60:
            return True
        return False

    def _source_material_status(self, state: WorkflowState) -> dict[str, Any]:
        document_context = state.get("document_context", {}) or {}
        non_design_user_text = self._strip_design_context_lines(str(state.get("user_input", "") or ""))
        normalized = re.sub(r"\s+", " ", non_design_user_text).strip()
        return {
            "file_count": int(document_context.get("file_count", 0) or 0),
            "parsed_file_count": int(document_context.get("parsed_file_count", 0) or 0),
            "retrieval_ready": bool(document_context.get("retrieval_ready", False)),
            "combined_excerpt_length": len(str(document_context.get("combined_excerpt", "") or "")),
            "non_design_user_text_length": len(normalized),
        }

    def _missing_design_context_fields(self, state: WorkflowState) -> list[str]:
        context = self._current_design_context(state)
        return [key for key, value in context.items() if not value]

    def _skill_exists(self, skill_name: str) -> bool:
        if not skill_name:
            return False
        return any(skill.name == skill_name for skill in self.runtime.skill_repository.list())

    @staticmethod
    def _routing_features(value: str) -> set[str]:
        text = str(value or "").lower()
        features = {
            token
            for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_\-]{2,}", text)
            if token not in {"prompt", "diagram", "figure", "skill", "generate", "create"}
        }
        cjk_runs = re.findall(r"[\u4e00-\u9fff]{2,}", text)
        cjk_stopwords = {"生成", "绘图", "画图", "提示", "提示词", "根据", "请帮", "这个", "一个"}
        for run in cjk_runs:
            for size in (2, 3, 4):
                for index in range(0, max(0, len(run) - size + 1)):
                    token = run[index : index + size]
                    if token not in cjk_stopwords:
                        features.add(token)
        return features

    def _rank_skill_candidates(self, state: WorkflowState) -> list[dict[str, Any]]:
        request_text = "\n".join(
            [
                str(state.get("user_input", "") or ""),
                "source_material_available" if self._has_source_material(state) else "",
            ]
        )
        request_features = self._routing_features(request_text)
        candidates: list[dict[str, Any]] = []
        for skill in self.runtime.skill_repository.list():
            if skill.name == "document_ingestion_routing":
                continue
            skill_text = f"{skill.name}\n{skill.description}\n{skill.body[:3000]}"
            skill_features = self._routing_features(skill_text)
            score = len(request_features & skill_features)
            name_text = skill.name.replace("_", " ").lower()
            if name_text and name_text in request_text.lower():
                score += 8
            if skill.name.lower() in request_text.lower():
                score += 8
            if score <= 0:
                continue
            candidates.append(
                {
                    "skill_name": skill.name,
                    "score": score,
                    "confidence": round(min(1.0, score / max(6, len(request_features) or 1)), 3),
                    "description": skill.description,
                }
            )
        return sorted(candidates, key=lambda item: (-int(item["score"]), str(item["skill_name"])))

    @staticmethod
    def _skill_input_refs(skill) -> set[str]:
        refs: set[str] = set()
        for stage in skill.plan:
            refs.update(str(item) for item in stage.get("input_refs", []) or [])
        return refs

    def _skill_requires_source_material(self, skill) -> bool:
        refs = self._skill_input_refs(skill)
        return any(ref.startswith("document.") or ref == "document_context" for ref in refs)

    def _missing_required_user_fields_for_skill(self, skill, state: WorkflowState) -> list[str]:
        refs = self._skill_input_refs(skill)
        ref_to_state_key = {
            "user.primary_discipline": "primary_discipline",
            "user.conference_name": "conference_name",
            "user.preferences": "user_preferences",
        }
        missing: list[str] = []
        for ref, state_key in ref_to_state_key.items():
            if ref in refs and not self._normalize_optional_text(state.get(state_key)):
                missing.append(state_key)
        return missing

    def _prepare_selected_skill(
        self,
        state: WorkflowState,
        skill_name: str,
        *,
        reason: str,
        question: str = "",
        missing_fields: list[str] | None = None,
    ) -> WorkflowState:
        skill = self.runtime.skill_repository.get(skill_name)
        state["selected_skill"] = skill.name
        state["skill_plan"] = deepcopy(skill.plan)
        missing = list(missing_fields or self._missing_required_user_fields_for_skill(skill, state))
        if missing:
            return self._build_design_clarification_response(
                state,
                reason=reason or f"{skill.name} 需要补充进入任务前的用户约束。",
                question=question,
                missing_fields=missing,
            )
        if self._skill_requires_source_material(skill) and not self._has_source_material(state):
            return self._build_source_material_clarification_response(
                state,
                reason=reason or f"{skill.name} 需要可抽取结构的来源材料。",
            )
        state["missing_clarification_fields"] = []
        state["stage"] = "skill_selected"
        debug_event("skill_selected", skill_name=skill.name, reason=reason)
        return state

    @staticmethod
    def _clarification_labels() -> dict[str, str]:
        return {
            "primary_discipline": "primary_discipline",
            "conference_name": "conference_name",
            "user_preferences": "user_preferences",
        }

    def _build_design_clarification_response(
        self,
        state: WorkflowState,
        *,
        reason: str,
        question: str = "",
        missing_fields: list[str] | None = None,
    ) -> WorkflowState:
        missing_fields = list(missing_fields or self._missing_design_context_fields(state))
        labels = self._clarification_labels()
        requested = [labels.get(item, item) for item in missing_fields]
        question_text = question.strip() if question.strip() else (
            "开始科研绘图前，还需要你补充以下信息；可以一次提供，也可以分多次逐步补充：\n"
            "1. primary_discipline：你的一级学科或大领域\n"
            "2. conference_name：目标期刊或会议名称\n"
            "3. user_preferences：你对图的特殊偏好或限制\n\n"
            "建议直接按下面格式回复：\n"
            "primary_discipline: ...\n"
            "conference_name: ...\n"
            "user_preferences: ..."
        )
        state["missing_clarification_fields"] = missing_fields
        state["stage"] = "clarification_needed"
        state["final_response"] = {
            "chinese_explanation": question_text,
            "reason": reason,
            "missing_fields": requested,
            "collected_values": self._current_design_context(state),
        }
        state["next_hop"] = "end"
        return state

    def _build_source_material_clarification_response(
        self,
        state: WorkflowState,
        *,
        reason: str,
    ) -> WorkflowState:
        source_status = self._source_material_status(state)
        selected_skill = str(state.get("selected_skill") or state.get("target_skill") or "")
        is_scientific = selected_skill == "scientific_diagram"
        if source_status["file_count"] > 0 and source_status["parsed_file_count"] == 0:
            if is_scientific:
                question_text = (
                    "开始科研绘图前，还需要可用于逻辑提取的正文内容。\n"
                    "当前附件虽然已上传，但系统没有解析出可用正文。\n\n"
                    "请任选一种方式继续；可以分多次补充：\n"
                    "1. 重新上传可解析的 PDF / DOCX / Markdown\n"
                    "2. 直接粘贴论文摘要、方法部分、图注或相关正文原文\n\n"
                    "只有学科、会议和偏好信息，不足以进入 logic_extraction。"
                )
            else:
                question_text = (
                    "开始执行当前绘图 skill 前，还需要可用于抽取内容结构的来源材料。\n"
                    "当前附件虽然已上传，但系统没有解析出可用正文。\n\n"
                    "请任选一种方式继续；可以分多次补充：\n"
                    "1. 重新上传可解析的 PDF / DOCX / Markdown\n"
                    "2. 直接粘贴申请书、研究计划、正文片段或其他足够具体的原文材料"
                )
        else:
            if is_scientific:
                question_text = (
                    "开始科研绘图前，还需要论文正文或可用于抽取方法结构的原文内容。\n\n"
                    "请任选一种方式补充；可以分多次补充：\n"
                    "1. 上传论文 PDF / DOCX / Markdown\n"
                    "2. 直接粘贴摘要、方法部分、图注或相关正文\n\n"
                    "当前仅有学科、会议和偏好信息，不足以进入 logic_extraction。"
                )
            else:
                question_text = (
                    "开始执行当前绘图 skill 前，还需要来源材料来抽取内容结构。\n\n"
                    "请任选一种方式补充；可以分多次补充：\n"
                    "1. 上传 PDF / DOCX / Markdown\n"
                    "2. 直接粘贴申请书、项目背景、研究内容、技术路线、创新点或其他具体正文片段"
                )
        state["stage"] = "clarification_needed"
        state["final_response"] = {
            "chinese_explanation": question_text,
            "reason": reason,
            "missing_fields": [],
            "collected_values": self._current_design_context(state),
            "source_material_required": True,
            "source_material_status": source_status,
        }
        state["next_hop"] = "end"
        return state

    def _stop_if_requested(self, state: WorkflowState) -> WorkflowState | None:
        session_id = str(state.get("session_id") or "")
        if not self.runtime.is_stop_requested(session_id):
            return None
        state["stop_requested"] = True
        state["active_task"] = None
        state["active_tasks"] = []
        state["active_task_run_ids"] = []
        state["pending_tasks"] = []
        state["awaiting_user_confirmation"] = False
        state["stage"] = "stopped"
        state["next_hop"] = "end"
        state["final_response"] = {
            "chinese_explanation": "本轮流程已停止。可以重新上传附件或重新发起生成。",
            "stop_requested": True,
        }
        debug_event("workflow_stopped", session_id=session_id)
        return state

    def _controller_node(self, state: WorkflowState) -> dict[str, Any]:
        state = deepcopy(state)
        state.setdefault("messages", [])
        state.setdefault("completed_tasks", [])
        state.setdefault("active_tasks", [])
        state.setdefault("active_task_run_ids", [])
        state.setdefault("warnings", [])
        state.setdefault("review_history", [])
        state.setdefault("artifacts", {})
        state.setdefault("stage", "initial")
        state.setdefault("primary_discipline", "")
        state.setdefault("conference_name", "")
        state.setdefault("user_preferences", "")
        state.setdefault("detected_intent", "")
        state.setdefault("target_skill", "")
        state.setdefault("routing_reason", "")
        state.setdefault("orchestration_plan", {})
        state.setdefault("awaiting_orchestration_confirmation", False)
        state.setdefault("orchestration_confirmed", False)
        state.setdefault("missing_clarification_fields", [])
        state = self._apply_design_context(
            state,
            self._extract_design_context_from_text(state.get("user_input", "")),
        )
        stopped_state = self._stop_if_requested(state)
        if stopped_state is not None:
            return stopped_state

        if state.get("awaiting_user_confirmation"):
            state["next_hop"] = "end"
            return state

        if state.get("awaiting_orchestration_confirmation"):
            state["next_hop"] = "end"
            return state

        if self._current_active_tasks(state):
            state["next_hop"] = "worker"
            return state

        handled_message = False
        while True:
            pending_message, messages = pop_next_pending_message(state.get("messages", []), "controller")
            state["messages"] = messages
            if not pending_message:
                break
            handled_message = True
            debug_event("controller_message_received", message_type=pending_message.get("message_type"), task_type=pending_message.get("task_type"))
            state = self._handle_message(state, pending_message)
            if state.get("awaiting_user_confirmation") or state.get("stage") in {"clarification_needed", "completed", "image_generation_completed", "image_generation_failed", "image_generation_not_implemented", "review_failed"}:
                state["next_hop"] = "end"
                return state
        if handled_message:
            if self._current_active_tasks(state):
                state["next_hop"] = "worker"
                return state
            state = self._dispatch_next_pipeline_stage(state)
            state["next_hop"] = "worker" if self._current_active_tasks(state) else "end"
            return state

        if not state.get("selected_skill"):
            state = self._select_skill(state)
            if state.get("selected_skill") and state.get("stage") == "skill_selected":
                if not state.get("orchestration_confirmed"):
                    return self._build_orchestration_confirmation_response(state)
                state = self._dispatch_next_pipeline_stage(state)
                state["next_hop"] = "worker" if self._current_active_tasks(state) else "end"
            return state

        if state.get("stage") == "image_generation_requested":
            state = self._dispatch_image_generation(state)
            state["next_hop"] = "worker" if self._current_active_tasks(state) else "end"
            return state

        if state.get("selected_skill") and not state.get("orchestration_confirmed") and not state.get("completed_tasks"):
            return self._build_orchestration_confirmation_response(state)

        state = self._dispatch_next_pipeline_stage(state)
        state["next_hop"] = "worker" if self._current_active_tasks(state) else "end"
        return state

    def _build_orchestration_batches(self, skill_plan: list[dict[str, Any]]) -> list[list[str]]:
        remaining = {str(stage.get("stage") or "") for stage in skill_plan if stage.get("stage")}
        dependencies = {
            str(stage.get("stage") or ""): [str(item) for item in stage.get("depends_on", [])]
            for stage in skill_plan
            if stage.get("stage")
        }
        completed: set[str] = set()
        batches: list[list[str]] = []
        max_parallel = self._max_concurrent_virtual_tasks()
        while remaining:
            ready = [
                stage_name
                for stage_name in remaining
                if all(dep in completed for dep in dependencies.get(stage_name, []))
            ]
            if not ready:
                batches.append(sorted(remaining))
                break
            ordered_ready = [
                str(stage.get("stage"))
                for stage in skill_plan
                if str(stage.get("stage")) in ready
            ][:max_parallel]
            batches.append(ordered_ready)
            completed.update(ordered_ready)
            remaining.difference_update(ordered_ready)
        return batches

    def _build_orchestration_plan(self, state: WorkflowState) -> dict[str, Any]:
        skill_plan = state.get("skill_plan", []) or []
        stages = []
        for stage in skill_plan:
            stages.append(
                {
                    "stage": stage.get("stage", ""),
                    "agent_name": stage.get("agent_name", stage.get("stage", "")),
                    "agent_description": stage.get("agent_description", ""),
                    "prompt_name": stage.get("prompt_name", ""),
                    "model_role": stage.get("model_role", ""),
                    "output_contract": stage.get("output_contract", ""),
                    "stage_role": stage.get("stage_role", ""),
                    "stage_goal": stage.get("stage_goal", ""),
                    "depends_on": list(stage.get("depends_on", []) or []),
                    "input_refs": list(stage.get("input_refs", []) or []),
                    "allowed_input_refs": list(stage.get("allowed_input_refs", []) or []),
                    "output_ref": stage.get("output_ref", ""),
                    "artifact_aliases": list(stage.get("artifact_aliases", []) or []),
                    "review_required": bool(stage.get("review_required", True)),
                    "review_phase": stage.get("review_phase", ""),
                    "trigger": stage.get("trigger", ""),
                }
            )
        return {
            "selected_skill": state.get("selected_skill", ""),
            "detected_intent": state.get("detected_intent", ""),
            "routing_reason": state.get("routing_reason", ""),
            "max_parallel": self._max_concurrent_virtual_tasks(),
            "validation": validate_skill_plan(skill_plan),
            "stages": stages,
            "batches": self._build_orchestration_batches(skill_plan),
        }

    def _build_orchestration_confirmation_response(self, state: WorkflowState) -> WorkflowState:
        plan = self._build_orchestration_plan(state)
        validation = plan.get("validation", {}) or {}
        state["orchestration_plan"] = plan
        state["awaiting_orchestration_confirmation"] = True
        state["orchestration_confirmed"] = False
        state["stage"] = "awaiting_orchestration_confirmation"
        state["next_hop"] = "end"
        state["final_response"] = {
            "chinese_explanation": (
                "已根据当前 skill 生成任务编排结构。请确认后再开始执行虚拟节点。"
                if validation.get("ok", True)
                else "已生成任务编排结构，但编排验证未通过，请先修改 skill 描述或编排结构。"
            ),
            "selected_skill": state.get("selected_skill", ""),
            "orchestration_plan": plan,
            "confirm_action": "confirm_orchestration",
            "confirm_session_id": state.get("session_id", ""),
            "can_confirm_orchestration": bool(validation.get("ok", True)),
        }
        debug_event(
            "orchestration_confirmation_required",
            session_id=state.get("session_id", ""),
            selected_skill=state.get("selected_skill", ""),
            stage_count=len(plan.get("stages", [])),
        )
        return state

    def _select_skill(self, state: WorkflowState) -> WorkflowState:
        prompt = self.runtime.prompt_repository.render("controller", {})
        deterministic_route = self._detect_entry_intent(state)
        user_payload = {
            "mode": "select_skill",
            "user_input": state.get("user_input", ""),
            "document_context_summary": state.get("document_context_summary", {}),
            "available_skills": self.runtime.describe_skills(),
            "routing_skill": "document_ingestion_routing",
            "deterministic_route_hint": deterministic_route,
            "design_context": self._current_design_context(state),
            "source_material_status": self._source_material_status(state),
            "selection_policy": (
                "Select the best concrete skill from available_skills by reading each skill description, "
                "body_excerpt, and plan_preview. deterministic_route_hint is a local candidate hint, not a hard override."
            ),
        }
        try:
            result = self.runtime.llm_client.run_tool_call(
                model=self.runtime.config.controller_model,
                system_prompt=prompt,
                user_payload=user_payload,
                tools=[route_skill_decision],
                temperature=0.1,
                max_tokens=800,
            )
            tool_result = result["tool_result"]
        except Exception as exc:
            if state.get("document_context", {}).get("file_count", 0) or state.get("user_input", "").strip():
                tool_result = deterministic_route | {
                    "reason": f"{deterministic_route.get('reason', '')} Controller fallback used due to runtime error: {exc}",
                }
            else:
                tool_result = {
                    "action": "request_clarification",
                    "skill_name": "document_ingestion_routing",
                    "detected_intent": "clarification_needed",
                    "target_skill": "",
                    "question": "请先上传材料，或描述你想把材料生成哪类图或 Prompt。",
                    "reason": f"Controller fallback requested clarification due to runtime error: {exc}",
                }
        route_candidates = list(deterministic_route.get("skill_candidates") or [])
        top_candidate = route_candidates[0] if route_candidates else {}
        top_skill = str(top_candidate.get("skill_name") or "")
        selected_skill = str(tool_result.get("skill_name") or tool_result.get("target_skill") or "")
        if (
            top_skill
            and selected_skill
            and selected_skill != "document_ingestion_routing"
            and selected_skill != top_skill
            and self._skill_exists(selected_skill)
            and int(top_candidate.get("score") or 0) >= 3
        ):
            tool_result = {
                **tool_result,
                "action": "select_skill",
                "skill_name": top_skill,
                "target_skill": top_skill,
                "detected_intent": "skill_request",
                "reason": (
                    f"{tool_result.get('reason', '')} Local skill-catalog match favored {top_skill}; "
                    "using the catalog-derived candidate instead of an inconsistent model choice."
                ).strip(),
            }
        state = self._apply_design_context(state, tool_result)
        selected_from_tool = str(tool_result.get("skill_name") or "")
        target_from_tool = str(tool_result.get("target_skill") or "")
        if selected_from_tool == "document_ingestion_routing" and target_from_tool:
            state["selected_skill"] = selected_from_tool
        elif self._skill_exists(selected_from_tool):
            state["selected_skill"] = selected_from_tool
            state["target_skill"] = selected_from_tool
        elif self._skill_exists(target_from_tool):
            state["selected_skill"] = "document_ingestion_routing"
            state["target_skill"] = target_from_tool
        else:
            state["selected_skill"] = selected_from_tool
        state["detected_intent"] = str(tool_result.get("detected_intent") or deterministic_route.get("detected_intent") or "")
        state["target_skill"] = str(state.get("target_skill") or tool_result.get("target_skill") or deterministic_route.get("target_skill") or "")
        state["routing_reason"] = str(tool_result.get("reason") or deterministic_route.get("reason") or "")

        if state["selected_skill"] == "document_ingestion_routing":
            routed = self._handle_document_ingestion_route(state, tool_result)
            if routed is not None:
                return routed

        if tool_result["action"] == "request_clarification":
            return self._build_design_clarification_response(
                state,
                reason=tool_result.get("reason", ""),
                question=tool_result.get("question", ""),
                missing_fields=tool_result.get("missing_fields", []),
            )

        chosen_skill = str(state.get("target_skill") or tool_result.get("skill_name") or "")
        return self._prepare_selected_skill(
            state,
            chosen_skill,
            reason=tool_result.get("reason", ""),
            question=tool_result.get("question", ""),
            missing_fields=tool_result.get("missing_fields", []),
        )

    def _detect_entry_intent(self, state: WorkflowState) -> dict[str, Any]:
        text = str(state.get("user_input", "") or "").lower()
        document_context = state.get("document_context", {}) or {}
        file_count = int(document_context.get("file_count", 0) or 0)
        parsed_file_count = int(document_context.get("parsed_file_count", 0) or 0)
        has_source = self._has_source_material(state)
        skill_candidates = self._rank_skill_candidates(state)
        top_candidate = skill_candidates[0] if skill_candidates else {}
        target_skill = str(top_candidate.get("skill_name") or "")
        route = {
            "action": "select_skill",
            "skill_name": "document_ingestion_routing",
            "target_skill": target_skill,
            "detected_intent": "clarification_needed",
            "skill_candidates": skill_candidates[:4],
            "reason": "入口路由只提供候选 skill，最终由主控根据 available_skills 决定。",
        }
        if re.search(r"\b(stop|cancel|abort)\b|停止|取消|终止", text):
            return route | {
                "action": "request_clarification",
                "target_skill": "",
                "detected_intent": "stop_request",
                "reason": "用户表达了停止当前流程的意图。",
            }
        if re.search(r"修订|修改|调整|优化|继续改|revise|revision|modify|edit", text) and "prompt" in text:
            return route | {
                "action": "request_clarification",
                "target_skill": "",
                "detected_intent": "prompt_revision_request",
                "reason": "用户表达了继续修订 Prompt 的意图。",
            }
        if re.search(r"开始生图|确认出图|生成图片|出图|confirm|generate image", text):
            return route | {
                "action": "request_clarification",
                "target_skill": "",
                "detected_intent": "image_confirmation_request",
                "reason": "用户表达了确认出图的意图。",
            }
        if file_count > 0 and parsed_file_count == 0:
            return route | {
                "action": "request_clarification",
                "detected_intent": "document_parse_failed",
                "reason": "附件已上传但没有解析出可用正文。",
            }
        if file_count > 0 and not text.strip():
            return route | {
                "action": "request_clarification",
                "detected_intent": "document_only_upload",
                "reason": "用户仅上传了文档，还没有说明绘图目标和设计上下文。",
            }
        if top_candidate:
            return route | {
                "action": "select_skill",
                "target_skill": str(top_candidate["skill_name"]),
                "detected_intent": "skill_request",
                "reason": "本地 skill 描述匹配给出了候选 skill，主控仍需根据 available_skills 自行确认。",
            }
        if has_source or re.search(r"绘图|画图|框架图|方法图|科研图|architecture|diagram|figure|prompt", text):
            return route | {
                "action": "select_skill",
                "detected_intent": "skill_request",
                "reason": "用户请求像绘图或 Prompt 任务，但没有足够明确的 skill 匹配，主控需要选择或澄清。",
            }
        return route

    def _handle_document_ingestion_route(
        self,
        state: WorkflowState,
        tool_result: dict[str, Any],
    ) -> WorkflowState | None:
        intent = str(state.get("detected_intent") or "")
        target_skill = str(state.get("target_skill") or "")
        debug_event(
            "document_ingestion_routed",
            detected_intent=intent,
            target_skill=target_skill,
            reason=state.get("routing_reason", ""),
        )

        if intent == "document_parse_failed":
            return self._build_source_material_clarification_response(
                state,
                reason="document_ingestion_routing 识别到附件解析失败，需要重新提供可解析来源材料。",
            )

        if intent == "document_only_upload":
            state["stage"] = "clarification_needed"
            state["final_response"] = {
                "chinese_explanation": (
                    "文档已进入系统。请继续说明你想把这份材料变成哪类图或 Prompt；可以用自然语言描述目标。\n\n"
                    "例如：基于论文生成科研方法图 Prompt；或基于基金申请书生成技术路线图 Prompt。"
                ),
                "reason": "document_ingestion_routing 识别到用户只上传了文档，还缺少目标任务。",
                "missing_fields": ["target_intent"],
                "collected_values": self._current_design_context(state),
                "source_material_status": self._source_material_status(state),
            }
            state["next_hop"] = "end"
            return state

        if target_skill and target_skill != "document_ingestion_routing" and self._skill_exists(target_skill):
            return self._prepare_selected_skill(
                state,
                target_skill,
                reason=tool_result.get("reason", "") or state.get("routing_reason", ""),
                question=tool_result.get("question", ""),
                missing_fields=tool_result.get("missing_fields", []),
            )

        if intent == "prompt_revision_request":
            state["stage"] = "routing_instruction"
            state["final_response"] = {
                "chinese_explanation": "识别到你想继续修订 Prompt。请在已生成 Prompt 的消息下使用“修订 Prompt”按钮，或携带 checkpoint_id 调用修订接口。",
                "detected_intent": intent,
                "routing_reason": state.get("routing_reason", ""),
            }
            state["next_hop"] = "end"
            return state

        if intent == "image_confirmation_request":
            state["stage"] = "routing_instruction"
            state["final_response"] = {
                "chinese_explanation": "识别到你想确认出图。请先完成 Prompt 生成并使用对应 checkpoint 的“开始生图”按钮。",
                "detected_intent": intent,
                "routing_reason": state.get("routing_reason", ""),
            }
            state["next_hop"] = "end"
            return state

        if intent == "stop_request":
            state["stage"] = "routing_instruction"
            state["final_response"] = {
                "chinese_explanation": "识别到你想停止流程。正在运行的任务请使用停止按钮或 `/session/stop` 接口。",
                "detected_intent": intent,
                "routing_reason": state.get("routing_reason", ""),
            }
            state["next_hop"] = "end"
            return state

        if tool_result.get("action") == "request_clarification":
            return self._build_design_clarification_response(
                state,
                reason=tool_result.get("reason", ""),
                question=tool_result.get("question", ""),
                missing_fields=tool_result.get("missing_fields", []),
            )
        return None

    def _dispatch_next_pipeline_stage(self, state: WorkflowState) -> WorkflowState:
        selected_skill_name = str(state.get("selected_skill") or "")
        if selected_skill_name and self._skill_exists(selected_skill_name):
            selected_skill = self.runtime.skill_repository.get(selected_skill_name)
            missing_fields = self._missing_required_user_fields_for_skill(selected_skill, state)
            if missing_fields:
                return self._build_design_clarification_response(
                    state,
                    reason=f"{selected_skill_name} 在进入后续虚拟节点前仍缺少必要用户约束。",
                    missing_fields=missing_fields,
                )
            if self._skill_requires_source_material(selected_skill) and not self._has_source_material(state):
                return self._build_source_material_clarification_response(
                    state,
                    reason=f"{selected_skill_name} 在进入虚拟节点前缺少可抽取结构的来源材料。",
                )

        ready_stages = ready_skill_stages(
            state.get("skill_plan", []),
            state.get("completed_tasks", []),
            active_tasks=self._current_active_tasks(state),
            include_image_generation=False,
            max_parallel=self._max_concurrent_virtual_tasks(),
        )
        if not ready_stages:
            if str(state.get("artifacts", {}).get("payload_final", {}).get("value", "") or "").strip():
                checkpoint_id, checkpoint_path = self.runtime.save_prompt_checkpoint(state)
                state["prompt_checkpoint_id"] = checkpoint_id
                state["prompt_checkpoint_path"] = checkpoint_path
                state["awaiting_user_confirmation"] = True
                state["stage"] = "awaiting_user_confirmation"
                state["next_hop"] = "end"
            else:
                stage_outputs = state.get("artifacts", {}).get("stage_outputs", {}) or {}
                if stage_outputs and not state.get("final_response"):
                    completed_names = [
                        str(item.get("task_type") or "")
                        for item in state.get("completed_tasks", [])
                        if item.get("task_type")
                    ]
                    latest_name = next(
                        (name for name in reversed(completed_names) if name in stage_outputs),
                        next(reversed(stage_outputs), ""),
                    )
                    latest_artifact = stage_outputs.get(latest_name, {})
                    state["final_response"] = {
                        "chinese_explanation": "当前 skill 已完成，结果已写入工作流 artifact，可供后续任务复用。",
                        "selected_skill": state.get("selected_skill", ""),
                        "result_stage": latest_name,
                        "result_artifact": latest_artifact,
                    }
                state["stage"] = "completed"
                state["next_hop"] = "end"
            return state

        prompt = self.runtime.prompt_repository.render("controller", {})
        user_payload = {
            "mode": "dispatch_next_task_batch",
            "selected_skill": state.get("selected_skill", ""),
            "next_stage_candidate": ready_stages[0],
            "ready_stage_candidates": ready_stages,
            "completed_tasks": state.get("completed_tasks", []),
            "warnings": state.get("warnings", []),
            "orchestration_rule": "Dispatch every ready stage whose dependencies are already satisfied. Independent stages may run in one virtual-worker batch.",
        }
        try:
            result = self.runtime.llm_client.run_tool_call(
                model=self.runtime.config.controller_model,
                system_prompt=prompt,
                user_payload=user_payload,
                tools=[dispatch_virtual_task],
                temperature=0.1,
                max_tokens=600,
            )
            tool_result = result["tool_result"]
        except Exception as exc:
            tool_result = {
                "action": "dispatch_virtual_task",
                "task_type": ready_stages[0]["stage"],
                "reason": f"Controller fallback dispatched next stage due to runtime error: {exc}",
                "revision_mode": False,
                "release_after_failure": False,
            }

        requested_task_type = str(tool_result.get("task_type") or "")
        if requested_task_type:
            ordered_stages = [stage for stage in ready_stages if str(stage.get("stage") or "") == requested_task_type]
            ordered_stages.extend(
                stage for stage in ready_stages
                if str(stage.get("stage") or "") != requested_task_type
            )
        else:
            ordered_stages = ready_stages

        active_tasks = [
            build_virtual_task(
                stage_spec=stage,
                max_retry=self.runtime.config.max_review_rounds,
                revision_mode=bool(tool_result.get("revision_mode", False)),
            )
            for stage in ordered_stages
        ]
        self._set_active_tasks(state, active_tasks)
        state["pending_tasks"] = state.get("pending_tasks", []) + active_tasks
        state["stage"] = (
            "parallel_virtual_tasks"
            if len(active_tasks) > 1
            else active_tasks[0]["task_type"]
        )
        debug_event(
            "task_batch_dispatched",
            task_types=[task["task_type"] for task in active_tasks],
            revision_mode=bool(tool_result.get("revision_mode", False)),
        )
        return state

    def _dispatch_image_generation(self, state: WorkflowState) -> WorkflowState:
        prompt = self.runtime.prompt_repository.render("controller", {})
        user_payload = {
            "mode": "trigger_image_generation",
            "checkpoint_id": state.get("prompt_checkpoint_id", ""),
            "payload_final": state.get("artifacts", {}).get("payload_final", {}),
        }
        try:
            result = self.runtime.llm_client.run_tool_call(
                model=self.runtime.config.controller_model,
                system_prompt=prompt,
                user_payload=user_payload,
                tools=[trigger_image_generation],
                temperature=0.0,
                max_tokens=300,
            )
            _ = result["tool_result"]
        except Exception:
            pass

        stage_spec = {
            "stage": "image_generation",
            "agent_name": "image_generation",
            "review_required": False,
        }
        image_task = build_virtual_task(
            stage_spec=stage_spec,
            max_retry=0,
            revision_mode=False,
        )
        self._set_active_tasks(state, [image_task])
        state["pending_tasks"] = state.get("pending_tasks", []) + [image_task]
        return state

    def _handle_message(self, state: WorkflowState, message: dict[str, Any]) -> WorkflowState:
        task_type = str(message.get("task_type") or "")
        message_type = str(message.get("message_type") or "")
        payload = message.get("payload") or {}

        if message_type == "fatal_review":
            review = payload.get("review") or {}
            state["stage"] = "review_failed"
            state["error"] = f"{task_type} 收到 fatal 审查信号，流程已停止。"
            state["final_response"] = {
                "chinese_explanation": state["error"],
                "review_status": "fatal",
                "review_approved": False,
                "latest_review_feedback": review,
            }
            state["next_hop"] = "end"
            debug_event(
                "fatal_review_received",
                task_type=task_type,
                review_phase=review.get("review_phase", ""),
                issues=review.get("issues", []),
            )
            return state

        if message_type == "positive_check":
            debug_event("task_approved", task_type=task_type, stage=state.get("stage", ""))
            state["completed_tasks"] = state.get("completed_tasks", []) + [
                {"task_type": task_type, "status": "approved"}
            ]
            if task_type == "summarization":
                prompt = self.runtime.prompt_repository.render("controller", {})
                summary_text = str(state.get("artifacts", {}).get("payload_final", {}).get("value", "") or "").strip()
                user_payload = {
                    "mode": "finalize_prompt",
                    "payload_final": state.get("artifacts", {}).get("payload_final", {}),
                    "review_history_tail": state.get("review_history", [])[-1:],
                }
                try:
                    result = self.runtime.llm_client.run_tool_call(
                        model=self.runtime.config.controller_model,
                        system_prompt=prompt,
                        user_payload=user_payload,
                        tools=[finalize_prompt],
                        temperature=0.0,
                        max_tokens=600,
                    )
                    tool_result = result["tool_result"]
                except Exception as exc:
                    tool_result = {
                        "action": "finalize_prompt",
                        "chinese_explanation": (
                            "已生成一版可直接用于当前绘图任务的英文 Prompt，请确认后开始生图。"
                            if summary_text
                            else f"Prompt 已生成，请确认后继续。({exc})"
                        ),
                        "release_with_warnings": False,
                        "warning_note": "",
                    }
                state["final_response"] = {
                    "chinese_explanation": tool_result["chinese_explanation"],
                    "review_warning": "",
                    "review_status": "approved",
                    "review_approved": True,
                    "latest_review_feedback": state.get("review_history", [])[-1] if state.get("review_history") else {},
                }
                checkpoint_id, checkpoint_path = self.runtime.save_prompt_checkpoint(state)
                state["prompt_checkpoint_id"] = checkpoint_id
                state["prompt_checkpoint_path"] = checkpoint_path
                state["awaiting_user_confirmation"] = True
                state["stage"] = "awaiting_user_confirmation"
                debug_event(
                    "prompt_ready",
                    checkpoint_id=checkpoint_id,
                    released_with_warnings=state.get("released_with_warnings", False),
                )
                return state

            if task_type == "image_generation":
                state["completed_tasks"] = state.get("completed_tasks", []) + [
                    {"task_type": "image_generation", "status": "approved"}
                ]
                state["stage"] = "image_generation_completed"
                state["next_hop"] = "end"
                return state

            state["stage"] = f"{task_type}_approved"
            return state

        if message_type == "image_generation_failed":
            warning = str(payload.get("warning") or "图片生成失败。")
            debug_event(
                "image_generation_failed",
                task_type=task_type,
                warning=warning,
            )
            state["warnings"] = state.get("warnings", []) + [warning]
            state["stage"] = "image_generation_failed"
            state["next_hop"] = "end"
            return state

        if message_type == "negative_review":
            review = payload.get("review") or {}
            debug_event(
                "review_rejected",
                task_type=task_type,
                retry_count=(payload.get("task") or {}).get("retry_count", 0),
                max_retry=(payload.get("task") or {}).get("max_retry", self.runtime.config.max_review_rounds),
                issues=review.get("issues", []),
            )
            active_task = payload.get("task") or {}
            retry_count = int(active_task.get("retry_count", 0))
            max_retry = int(active_task.get("max_retry", self.runtime.config.max_review_rounds))
            is_transient_worker_error = bool(review.get("transient_error"))

            if retry_count < max_retry:
                if is_transient_worker_error:
                    retry_task = build_virtual_task(
                        stage_spec=self._stage_spec_for_retry(state, active_task, task_type),
                        max_retry=max_retry,
                        retry_count=retry_count + 1,
                        revision_mode=bool(active_task.get("revision_mode", False)),
                        revision_context=dict(active_task.get("revision_context") or {}),
                    )
                    self._set_active_tasks(state, [retry_task])
                    state["pending_tasks"] = state.get("pending_tasks", []) + [retry_task]
                    state["stage"] = f"{task_type}_retrying"
                    debug_event(
                        "transient_worker_retry_scheduled",
                        task_type=task_type,
                        error_code=review.get("error_code", ""),
                        next_retry_count=retry_count + 1,
                        max_retry=max_retry,
                    )
                    return state

                prompt = self.runtime.prompt_repository.render("controller", {})
                try:
                    result = self.runtime.llm_client.run_tool_call(
                        model=self.runtime.config.controller_model,
                        system_prompt=prompt,
                        user_payload={
                            "mode": "handle_negative_review",
                            "task_type": task_type,
                            "retry_count": retry_count,
                            "max_retry": max_retry,
                            "review": review,
                        },
                        tools=[dispatch_virtual_task, finalize_prompt],
                        temperature=0.1,
                        max_tokens=600,
                    )
                    tool_result = result["tool_result"]
                except Exception:
                    tool_result = {
                        "action": "dispatch_virtual_task",
                        "task_type": task_type,
                        "reason": "Retry same task after negative review.",
                        "revision_mode": True,
                        "release_after_failure": False,
                    }

                if tool_result["action"] == "dispatch_virtual_task":
                    stage_spec = next(
                        item for item in state.get("skill_plan", [])
                        if item.get("stage") == task_type
                    )
                    revision_context = {
                        "original_input": payload.get("task_input", {}),
                        "current_output": payload.get("task_output", {}),
                        "review_feedback": review,
                    }
                    retry_task = build_virtual_task(
                        stage_spec=stage_spec,
                        max_retry=max_retry,
                        retry_count=retry_count + 1,
                        revision_mode=True,
                        revision_context=revision_context,
                    )
                    self._set_active_tasks(state, [retry_task])
                    state["pending_tasks"] = state.get("pending_tasks", []) + [retry_task]
                    state["stage"] = f"{task_type}_retrying"
                    debug_event(
                        "revision_scheduled",
                        task_type=task_type,
                        next_retry_count=retry_count + 1,
                        max_retry=max_retry,
                    )
                return state

            if is_transient_worker_error:
                state["stage"] = "review_failed"
                state["error"] = f"{task_type} 因模型服务响应异常超过最大重试次数，流程已停止。"
                state["final_response"] = {
                    "chinese_explanation": state["error"],
                    "review_status": "failed",
                    "review_approved": False,
                    "latest_review_feedback": review,
                }
                state["next_hop"] = "end"
                debug_event(
                    "transient_worker_retry_exhausted",
                    task_type=task_type,
                    max_retry=max_retry,
                    error_code=review.get("error_code", ""),
                )
                return state

            if self.runtime.config.max_review_failure_policy in {"fail", "hard_fail"}:
                state["stage"] = "review_failed"
                state["error"] = f"{task_type} 超过最大审查轮次，当前结果仍未通过审查，流程已失败。"
                state["final_response"] = {
                    "chinese_explanation": state["error"],
                    "review_status": "failed",
                    "review_approved": False,
                    "latest_review_feedback": review,
                }
                state["next_hop"] = "end"
                debug_event(
                    "max_retry_hard_failed",
                    task_type=task_type,
                    max_retry=max_retry,
                    review_phase=review.get("review_phase", ""),
                )
                return state

            warning = f"{task_type} 超过最大审查轮次，当前结果未通过审查，已带 warning 放行。"
            state["warnings"] = state.get("warnings", []) + [warning]
            state["released_with_warnings"] = True
            debug_event(
                "warning_release",
                task_type=task_type,
                warning=warning,
                review_phase=review.get("review_phase", ""),
            )
            state["completed_tasks"] = state.get("completed_tasks", []) + [
                {"task_type": task_type, "status": "released_with_warnings"}
            ]
            if task_type == "summarization":
                state["final_response"] = {
                    "chinese_explanation": "已生成一版英文绘图 Prompt，但该版本尚未通过审查，请先自行检查后再决定是否生图。",
                    "review_warning": warning,
                    "review_status": "not_approved_but_released",
                    "review_approved": False,
                    "latest_review_feedback": review,
                }
                checkpoint_id, checkpoint_path = self.runtime.save_prompt_checkpoint(state)
                state["prompt_checkpoint_id"] = checkpoint_id
                state["prompt_checkpoint_path"] = checkpoint_path
                state["awaiting_user_confirmation"] = True
                state["stage"] = "awaiting_user_confirmation"
                debug_event(
                    "prompt_ready",
                    checkpoint_id=checkpoint_id,
                    released_with_warnings=True,
                )
                return state

            state["stage"] = f"{task_type}_released_with_warnings"
            return state

        return state

    def _fallback_worker_artifact(self, state: WorkflowState, task: dict[str, Any]) -> dict[str, Any]:
        task_type = str(task["task_type"])
        summary = state.get("document_context_summary", {})
        document_context = state.get("document_context", {}) or {}
        excerpt = str(
            document_context.get("combined_text")
            or document_context.get("combined_excerpt", "")
            or ""
        )
        user_input = str(state.get("user_input", "") or "")

        if task_type == "logic_extraction":
            if not excerpt.strip():
                return {
                    "key": "logician",
                    "value": (
                        "1. 核心方法描述 (Core Method Description)\n"
                        "- 当前输入仅包含学科/会议/偏好或过于概括的需求，缺少可用于抽取方法结构的论文正文。\n\n"
                        "2. 方法结构图对应的目标图表标题 (Target Chart Title)\n"
                        "- 待补充（缺少原文内容）\n\n"
                        "3. 逻辑流验证 (Logical Flow)\n"
                        "[Step 1] 输入: 待补充 -> 技术: 待补充 -> 输出: 待补充\n\n"
                        "4. 关键实体提取 (Key Entities)\n"
                        "- 待补充（缺少原文内容，无法提取具体组件与依赖关系）\n\n"
                        "5. 绘图必须保留的依赖关系 (Mandatory Dependencies)\n"
                        "- 待补充（缺少原文内容）\n\n"
                        "6. 可视化重点提示 (Figure-critical Notes)\n"
                        "- 请先提供论文摘要、方法部分、图注或上传可解析附件。"
                    ),
                }
            return {
                "key": "logician",
                "value": (
                    "1. 逻辑流验证 (Logical Flow)\n"
                    f"{excerpt[:1200] or user_input[:600] or '待根据附件提炼具体方法结构。'}\n\n"
                    "2. 关键实体提取 (Key Entities)\n"
                    "- Input Context\n"
                    "- Core Method\n"
                    "- Output\n\n"
                    "3. 目标图标题 (Target Figure Title)\n"
                    "- Scientific Diagram Draft\n\n"
                    "4. 核心方法描述 (Core Method Summary)\n"
                    f"- {excerpt[:500] or user_input[:300] or 'Method summary unavailable.'}\n\n"
                    "5. 绘图必须保留的依赖关系 (Mandatory Dependencies)\n"
                    "- Input Context feeds into Core Method\n"
                    "- Core Method produces Output\n\n"
                    f"Source refs: {', '.join(item.get('name') for item in summary.get('files', []) if item.get('name'))}"
                ),
            }
        if task_type == "style_extraction":
            return {
                "key": "style_designer",
                "value": (
                    f"Primary discipline: {state.get('primary_discipline', '') or 'computer science'}\n"
                    "Specialized field: scientific diagram\n"
                    f"Target venue: {state.get('conference_name', '') or 'top-tier conference'}\n\n"
                    "Palette\n"
                    "- #1F2937 - primary text and borders\n"
                    "- #334155 - secondary module emphasis\n"
                    "- #64748B - grouping outlines and helper connectors\n"
                    "- #E2E8F0 - subtle background containers\n"
                    "- #F8FAFC - canvas background\n\n"
                    "Typography\n"
                    "- Title font: bold sans-serif\n"
                    "- Body font: clean sans-serif\n\n"
                    "Shape rules\n"
                    "- Input/data: parallelogram\n"
                    "- Core modules: rounded rectangle\n"
                    "- Loss/scoring: diamond\n"
                    "- Outputs: ellipse\n\n"
                    "Layout preferences\n"
                    "- Left-to-right flow\n"
                    "- Clear grouping\n"
                    "- Consistent spacing\n\n"
                    "Negative rules\n"
                    "- No photorealism\n"
                    "- No heavy shadows\n"
                    "- No decorative clutter"
                ),
            }
        if task_type == "visual_mapping":
            logic_value = str(state.get("artifacts", {}).get("payload_logic", {}).get("value", "") or "").strip()
            return {
                "key": "visual_mapper",
                "value": (
                    "Canvas: 1536x1024, white background.\n"
                    "Main layout: left-to-right primary flow with one dominant center lane.\n"
                    "Grouping: use subtle bounded containers to separate input, core method, and output regions.\n"
                    "Placement rule: keep the main method block centered, let supporting branches sit above or below the main lane, and avoid edge crossings.\n"
                    "Legend: include only when connector semantics such as dashed supervision or training feedback need explanation.\n"
                    "Logic reference:\n"
                    f"{logic_value[:1600] or 'Use the logic artifact as the source of truth for module ordering and dependencies.'}"
                ),
            }
        if task_type == "summarization":
            logic_value = str(state.get("artifacts", {}).get("payload_logic", {}).get("value", "") or "").strip()
            style_value = str(state.get("artifacts", {}).get("payload_style", {}).get("value", "") or "").strip()
            mapper_value = str(state.get("artifacts", {}).get("payload_mapper", {}).get("value", "") or "").strip()
            return {
                "key": "summarizer",
                "value": (
                    "Create a professional, publication-ready scientific architecture diagram in a clean flat 2D vector style. "
                    "Use a structured academic composition, readable labels, clear hierarchy, and strict alignment. "
                    "Preserve the actual method logic from the source material instead of collapsing it into a generic pipeline. "
                    f"Logic requirements: {logic_value[:1200] or 'show a faithful progression from input through core method to outputs'}. "
                    f"Style requirements: {style_value[:900] or 'use subdued conference-style colors, concise labels, and no decorative clutter'}. "
                    f"Layout requirements: {mapper_value[:900] or 'arrange the figure with a clear left-to-right flow and grouped sections'}. "
                    "Avoid 3D effects, photorealism, glossy rendering, poster-style gradients, heavy shadows, and crowded text."
                ),
            }
        raise RuntimeError(f"Unsupported fallback task type: {task_type}")

    @staticmethod
    def _stage_outputs(state: WorkflowState) -> dict[str, Any]:
        return ArtifactStore.stage_outputs(state)

    def _artifact_for_stage_or_alias(
        self,
        state: WorkflowState,
        *,
        stage_name: str,
        alias: str,
    ) -> dict[str, Any]:
        return self._artifact_store().artifact_for_stage_or_alias(
            state,
            stage_name=stage_name,
            alias=alias,
        )

    def _resolve_input_ref(self, state: WorkflowState, ref: str) -> Any:
        return self._artifact_store().resolve_input_ref(state, ref)

    def _build_worker_payload(self, state: WorkflowState, task: dict[str, Any]) -> dict[str, Any]:
        return self._context_builder().build_worker_payload(state, task)

    def _build_review_upstream_artifacts(self, state: WorkflowState, task: dict[str, Any] | str) -> dict[str, Any]:
        return self._context_builder().build_review_upstream_artifacts(state, task)

    def _build_review_task_input(self, task_input: dict[str, Any]) -> dict[str, Any]:
        return self._context_builder().build_review_task_input(task_input)

    def _store_task_artifact(
        self,
        state: WorkflowState,
        *,
        task: dict[str, Any],
        artifact_ref: str,
        artifact: dict[str, Any],
    ) -> None:
        self._artifact_store().store_task_artifact(
            state,
            task=task,
            artifact_ref=artifact_ref,
            artifact=artifact,
        )

    def _run_image_generation_task(self, state: WorkflowState, task: dict[str, Any]) -> dict[str, Any]:
        task_type = str(task["task_type"])
        image_warning = ""
        image_attempt_id = str(state.get("image_attempt_id") or task.get("task_id") or "")
        try:
            image_result = self.runtime.image_client.generate_image(
                model=self.runtime.config.image_model,
                prompt=str(state.get("artifacts", {}).get("payload_final", {}).get("value", "") or ""),
                size="1536x1024",
                image_attempt_id=image_attempt_id,
            )
        except Exception as exc:
            error_payload = classify_error(exc)
            image_result = {
                "image_url": "",
                "image_b64": "",
                "image_mime_type": "image/png",
                "revised_prompt": f"Image generation fallback used: {exc}",
                "error": error_payload,
                "raw_json": {},
                "image_attempt_id": image_attempt_id,
            }
            image_warning = f"图片模型未成功返回结果：{error_payload['code']} - {error_payload['message']}"
        artifact = self.runtime.save_image_artifact(
            state["session_id"],
            {"key": "image_generator", "value": image_result},
        )
        if artifact.get("value", {}).get("public_url") or artifact.get("value", {}).get("image_url"):
            message = append_message(
                [],
                from_role="virtual_worker",
                to_role="controller",
                message_type="positive_check",
                task_id=task["task_id"],
                task_type=task_type,
                summary="Image generation completed.",
                payload={"artifact": artifact},
            )[0]
            stage = "image_generation_completed"
        else:
            message = append_message(
                [],
                from_role="virtual_worker",
                to_role="controller",
                message_type="image_generation_failed",
                task_id=task["task_id"],
                task_type=task_type,
                summary="Image generation failed.",
                payload={
                    "artifact": artifact,
                    "warning": image_warning or "图片生成失败，未返回可显示图片。",
                },
            )[0]
            stage = "image_generation_failed"
        return {
            "task": task,
            "artifact_ref": "image_result",
            "artifact": artifact,
            "messages": [message],
            "reviews": [],
            "warnings": [image_warning] if image_warning else [],
            "stage": stage,
        }

    def _run_text_virtual_task(self, state: WorkflowState, task: dict[str, Any]) -> dict[str, Any]:
        task_type = str(task["task_type"])
        user_payload = self._build_worker_payload(state, task)
        review_task_input = self._build_review_task_input(user_payload)
        try:
            result = self.runtime.run_virtual_agent(
                agent_type=task_type,
                user_payload=user_payload,
                revision_mode=bool(task.get("revision_mode", False)),
            )
            artifact = result["artifact"]
            debug_event(
                "worker_completed_via_mcp",
                task_type=task_type,
                selection_source=result.get("selection_source", ""),
                model_name=result.get("model_name", ""),
                request_api_mode=result.get("request_api_mode", ""),
                prompt_name=result.get("prompt_name", ""),
            )
        except Exception as exc:
            error_payload = classify_error(exc)
            retryable_error = self._is_retryable_worker_error(error_payload)
            review = {
                "approved": False,
                "signal": "negative" if retryable_error else "fatal",
                "blocking": not retryable_error,
                "retry_targets": [task_type],
                "issues": [
                    f"{task_type} failed before producing a reviewable artifact: "
                    f"{error_payload['code']} - {error_payload['message']}"
                ],
                "recommendations": [
                    "这是模型服务限流或网络类错误，优先重试当前虚拟节点。"
                    if retryable_error
                    else "请稍后重试，或检查对应 LLM/MCP 服务配置与上游网关状态。"
                ],
                "notes": "Virtual worker execution failed; no fallback artifact was released.",
                "transient_error": retryable_error,
                "error_code": error_payload["code"],
            }
            message = append_message(
                [],
                from_role="virtual_worker",
                to_role="controller",
                message_type="negative_review" if retryable_error else "fatal_review",
                task_id=task["task_id"],
                task_type=task_type,
                summary=f"{task_type} failed before review.",
                payload={
                    "review": review,
                    "task": task,
                    "task_input": review_task_input,
                    "error": error_payload,
                },
            )[0]
            debug_event(
                "worker_failed",
                task_type=task_type,
                error_code=error_payload["code"],
                error_message=error_payload["message"],
            )
            return {
                "task": task,
                "artifact_ref": "",
                "artifact": None,
                "messages": [message],
                "reviews": [review],
                "warnings": [],
                "stage": "review_failed",
            }

        review = self.runtime.run_review(
            review_phase=str(task.get("review_phase") or ""),
            target_goal=f"Review the {task_type} output before the workflow continues.",
            task_type=task_type,
            task_input=review_task_input,
            task_output=artifact,
            upstream_artifacts=self._build_review_upstream_artifacts(state, task),
            prior_reviews=[],
        )
        debug_event(
            "review_completed",
            task_type=task_type,
            review_phase=review.get("review_phase", ""),
            approved=review.get("approved", False),
            blocking=review.get("blocking", False),
            issues=review.get("issues", []),
        )
        review_signal = str(review.get("signal") or ("positive" if review.get("approved") else "negative"))
        if review_signal == "fatal":
            message = append_message(
                [],
                from_role="review_tool",
                to_role="controller",
                message_type="fatal_review",
                task_id=task["task_id"],
                task_type=task_type,
                summary=f"{task_type} received a fatal review.",
                payload={
                    "review": review,
                    "task": task,
                    "task_input": review_task_input,
                    "task_output": artifact,
                },
            )[0]
        elif review["approved"]:
            message = append_message(
                [],
                from_role="review_tool",
                to_role="controller",
                message_type="positive_check",
                task_id=task["task_id"],
                task_type=task_type,
                summary=f"{task_type} passed review.",
                payload={"review": review, "artifact": artifact},
            )[0]
        else:
            message = append_message(
                [],
                from_role="review_tool",
                to_role="controller",
                message_type="negative_review",
                task_id=task["task_id"],
                task_type=task_type,
                summary=f"{task_type} failed review.",
                payload={
                    "review": review,
                    "task": task,
                    "task_input": review_task_input,
                    "task_output": artifact,
                },
            )[0]
        return {
            "task": task,
            "artifact_ref": task["output_ref"],
            "artifact": artifact,
            "messages": [message],
            "reviews": [review],
            "warnings": [],
            "stage": f"{task_type}_completed",
        }

    def _run_single_worker_task(self, state: WorkflowState, task: dict[str, Any]) -> dict[str, Any]:
        task_type = str(task["task_type"])
        debug_event(
            "worker_started",
            task_type=task_type,
            retry_count=task.get("retry_count", 0),
            revision_mode=task.get("revision_mode", False),
        )
        if task_type == "image_generation":
            return self._run_image_generation_task(state, task)
        return self._run_text_virtual_task(state, task)

    def _task_executor(self) -> VirtualTaskExecutor:
        executor = getattr(self.runtime, "task_executor", None)
        if executor is not None:
            return executor
        return VirtualTaskExecutor(max_workers=self._max_concurrent_virtual_tasks())

    def _artifact_store(self) -> ArtifactStore:
        store = getattr(self.runtime, "artifact_store", None)
        if store is not None:
            return store
        config = getattr(self.runtime, "config", None)
        root_dir = getattr(config, "artifact_dir", None)
        if root_dir is None:
            from pathlib import Path

            root_dir = Path("./runtime/artifacts")
        return ArtifactStore(root_dir / "artifacts")

    def _context_builder(self) -> WorkerContextBuilder:
        builder = getattr(self, "context_builder", None)
        if builder is not None:
            return builder
        return WorkerContextBuilder(self._artifact_store())

    def _virtual_worker_node(self, state: WorkflowState) -> dict[str, Any]:
        state = deepcopy(state)
        stopped_state = self._stop_if_requested(state)
        if stopped_state is not None:
            return stopped_state
        tasks = self._current_active_tasks(state)
        if not tasks:
            state["next_hop"] = "end"
            return state

        task_ids = {task.get("task_id") for task in tasks}
        state["pending_tasks"] = [
            item for item in state.get("pending_tasks", [])
            if item.get("task_id") not in task_ids
        ]
        self._set_active_tasks(state, [])

        if len(tasks) > 1:
            debug_event(
                "parallel_worker_batch_started",
                task_types=[task["task_type"] for task in tasks],
            )
        executor = self._task_executor()
        session_id = str(state.get("session_id") or "")
        run_ids = [
            executor.submit(
                session_id=session_id,
                task=task,
                handler=lambda _cancel_event, task=task: self._run_single_worker_task(deepcopy(state), task),
            )
            for task in tasks
        ]
        state["active_task_run_ids"] = run_ids
        results = executor.wait_many(run_ids)
        executor.cleanup_many(run_ids)
        state["active_task_run_ids"] = []

        if len(tasks) > 1:
            debug_event(
                "parallel_worker_batch_completed",
                task_types=[result["task"]["task_type"] for result in results],
            )

        stopped_state = self._stop_if_requested(state)
        if stopped_state is not None:
            return stopped_state

        for result in results:
            artifact_ref = result.get("artifact_ref")
            artifact = result.get("artifact")
            if artifact_ref and artifact is not None:
                self._store_task_artifact(
                    state,
                    task=result.get("task") or {},
                    artifact_ref=str(artifact_ref),
                    artifact=artifact,
                )
            state["review_history"] = state.get("review_history", []) + list(result.get("reviews", []))
            state["warnings"] = state.get("warnings", []) + list(result.get("warnings", []))
            state["messages"] = state.get("messages", []) + list(result.get("messages", []))

        state["stage"] = (
            "parallel_virtual_tasks_completed"
            if len(results) > 1
            else str(results[0].get("stage") or state.get("stage") or "")
        )
        state["next_hop"] = "controller"
        return state
