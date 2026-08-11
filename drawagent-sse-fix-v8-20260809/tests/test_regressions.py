from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.models import ReviewVerdict
from app.core.artifact_store import ArtifactStore
from app.core.run_events import RunEventStore
from app.ingestion.service import DocumentIngestionService
from app.mcp.stdio_client import MCPStdIOClient
from app.agents.registry import AgentRegistry
from app.skills.compiler import compile_skill_plan
from app.skills.repository import SkillRepository
from app.skills.validator import validate_skill_plan
from app.workflow.task_factory import ready_skill_stages
from app.workflow.graph import DualNodeWorkflowGraph
from app.workflow.runtime import DrawAgentRuntime
from app.workflow.context_builder import WorkerContextBuilder
from app.workflow.task_executor import VirtualTaskExecutor
from app.tools import review_tool as review_tool_module
from app.tools.review_tool import ReviewToolClient


def test_staged_file_validation_uses_manifest_path(monkeypatch, tmp_path):
    import app.main as main

    monkeypatch.setattr(main, "CONFIG", SimpleNamespace(upload_dir=tmp_path))
    batch_id = "batch-1"
    batch_dir = tmp_path / "staging" / batch_id
    batch_dir.mkdir(parents=True)
    uploaded = batch_dir / "paper.txt"
    uploaded.write_text("paper text", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (batch_dir / "upload_result.json").write_text(
        json.dumps(
            {
                "upload_batch_id": batch_id,
                "uploaded_files": [
                    {
                        "name": "paper.txt",
                        "content_type": "text/plain",
                        "size_bytes": uploaded.stat().st_size,
                        "saved_path": str(uploaded),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    files = main._validated_staged_files(
        batch_id,
        [{"name": "paper.txt", "saved_path": str(outside)}],
    )

    assert len(files) == 1
    assert files[0]["saved_path"] == str(uploaded.resolve())


def test_staged_file_validation_rejects_paths_outside_manifest(monkeypatch, tmp_path):
    import app.main as main

    monkeypatch.setattr(main, "CONFIG", SimpleNamespace(upload_dir=tmp_path))
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    assert main._validated_staged_files(
        "missing-batch",
        [{"name": "secret.txt", "saved_path": str(outside)}],
    ) == []


def test_pdf_fallback_does_not_treat_binary_as_parsed(tmp_path):
    pdf_path = tmp_path / "broken.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nnot a valid pdf body")

    text, _page_count, parser, warning = DocumentIngestionService()._read_pdf(pdf_path)

    assert text == ""
    assert parser in {"pypdf_failed", "pypdf_unavailable"}
    assert warning


def test_review_verdict_normalizes_fatal_signal():
    verdict = ReviewVerdict(
        approved=True,
        signal="fatal",
        blocking=True,
        issues=["cannot continue"],
    )

    assert verdict.approved is False
    assert verdict.signal == "fatal"


def test_runtime_stop_request_is_session_scoped():
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])

    assert runtime.request_stop("session-1") is True
    assert runtime.is_stop_requested("session-1") is True
    assert runtime.is_stop_requested("session-2") is False
    runtime.clear_stop_request("session-1")
    assert runtime.is_stop_requested("session-1") is False


def test_mcp_read_timeout_kills_unresponsive_process(tmp_path):
    client = MCPStdIOClient(
        command=sys.executable,
        args=["-c", "import time; time.sleep(5)"],
        cwd=tmp_path,
        timeout_seconds=0.2,
    )

    with pytest.raises(TimeoutError):
        client.__enter__()
    assert client._process is None or client._process.poll() is not None


def test_review_tool_client_reuses_persistent_mcp_client(monkeypatch, tmp_path):
    created = []

    class FakeMCPClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.running = False
            self.calls = []
            self.closed = False
            created.append(self)

        def start(self):
            self.running = True
            return self

        def is_running(self):
            return self.running

        def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            return {"name": name, "call_count": len(self.calls)}

        def close(self):
            self.running = False
            self.closed = True

    monkeypatch.setattr(review_tool_module, "MCPStdIOClient", FakeMCPClient)
    config = SimpleNamespace(
        review_mcp_pool_size=1,
        review_mcp_command="python",
        review_mcp_args=["-m", "server"],
        root_dir=tmp_path,
        review_mcp_timeout_seconds=5,
    )
    client = ReviewToolClient(config)

    assert client._call_tool("first", {"x": 1}) == {"name": "first", "call_count": 1}
    assert client._call_tool("second", {"x": 2}) == {"name": "second", "call_count": 2}
    assert len(created) == 1

    client.close()
    assert created[0].closed is True


def test_fatal_review_message_does_not_duplicate_review_history():
    review = {
        "approved": False,
        "signal": "fatal",
        "blocking": True,
        "issues": ["fatal issue"],
    }
    graph = DualNodeWorkflowGraph.__new__(DualNodeWorkflowGraph)
    state = {
        "review_history": [review],
        "messages": [],
        "stage": "summarization",
    }
    message = {
        "message_type": "fatal_review",
        "task_type": "summarization",
        "payload": {"review": review},
    }

    next_state = graph._handle_message(state, message)

    assert next_state["stage"] == "review_failed"
    assert next_state["review_history"] == [review]


def test_job_status_is_persisted(monkeypatch, tmp_path):
    import app.main as main

    monkeypatch.setattr(main, "JOB_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {})
    job = main._submit_job(
        kind="test",
        session_id="session-1",
        target=lambda: {"stage": "done", "ok": True},
    )

    deadline = time.time() + 3
    current = job
    while time.time() < deadline:
        current = main._get_job(job["job_id"])
        if current["status"] == "completed":
            break
        time.sleep(0.05)

    assert current["status"] == "completed"
    assert current["result"]["ok"] is True
    persisted = json.loads((tmp_path / f"{job['job_id']}.json").read_text(encoding="utf-8"))
    assert persisted["status"] == "completed"


def test_revise_prompt_checkpoint_creates_new_prompt_checkpoint(monkeypatch):
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])
    state = runtime.build_initial_state(
        session_id="test-revise-session",
        user_input="demo",
        document_context={"files": [], "combined_excerpt": ""},
    )
    state["artifacts"]["payload_final"] = {"key": "summarizer", "value": "Original prompt."}
    checkpoint_id, _path = runtime.save_prompt_checkpoint(state)

    monkeypatch.setattr(
        runtime.llm_client,
        "run_tool_call",
        lambda **_kwargs: {
            "tool_result": {
                "intent": "revise_current_prompt",
                "scope": "visual_only",
                "restart_from": "prompt_reviser",
                "reason": "Visual-only change.",
                "question": "",
            }
        },
    )

    worker_calls = []

    def fake_reviser(**kwargs):
        worker_calls.append(kwargs)
        return {
            "artifact": {
                "key": "summarizer",
                "value": "A complete revised prompt with shorter labels.",
            },
            "summary": "已缩短标签。",
        }

    monkeypatch.setattr(runtime, "run_virtual_agent", fake_reviser)
    monkeypatch.setattr(
        runtime,
        "run_review",
        lambda **_kwargs: {
            "approved": True,
            "signal": "positive",
            "blocking": False,
            "retry_targets": [],
            "issues": [],
            "recommendations": [],
            "notes": "Revision applied.",
        },
    )

    result = runtime.revise_prompt_checkpoint(
        checkpoint_id=checkpoint_id,
        revision_instruction="Make the labels shorter.",
    )

    assert result["checkpoint_id"] != checkpoint_id
    assert result["payload_final"]["value"] == "A complete revised prompt with shorter labels."
    assert "User revision requirement" not in result["payload_final"]["value"]
    assert result["confirm_required"] is True
    assert result["route_action"] == "revised_prompt"
    assert worker_calls[0]["user_payload"]["input_payload"]["current_prompt"] == "Original prompt."
    assert worker_calls[0]["user_payload"]["input_payload"]["revision_instruction"] == "Make the labels shorter."


def test_logic_changing_followup_routes_to_full_pipeline_without_prompt_reviser(monkeypatch):
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])
    state = runtime.build_initial_state(
        session_id="test-logic-followup",
        user_input="demo",
        document_context={"files": [], "combined_excerpt": ""},
    )
    state["artifacts"]["payload_final"] = {"key": "summarizer", "value": "Original prompt."}
    checkpoint_id, _path = runtime.save_prompt_checkpoint(state)

    monkeypatch.setattr(
        runtime.llm_client,
        "run_tool_call",
        lambda **_kwargs: {
            "tool_result": {
                "intent": "revise_current_prompt",
                "scope": "logic_change",
                "restart_from": "logic_extraction",
                "reason": "The request changes the scientific data flow.",
                "question": "",
            }
        },
    )
    monkeypatch.setattr(
        runtime,
        "run_virtual_agent",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("prompt reviser must not run")),
    )

    result = runtime.revise_prompt_checkpoint(
        checkpoint_id=checkpoint_id,
        revision_instruction="Change the data flow and add a new causal module.",
    )

    assert result["route_action"] == "full_pipeline"
    assert result["revision_route"]["restart_from"] == "logic_extraction"
    assert result["checkpoint_id"] == checkpoint_id


def test_document_ingestion_routing_skill_is_registered():
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])
    skills = {item["name"] for item in runtime.describe_skills()}

    assert "document_ingestion_routing" in skills
    assert "scientific_diagram" in skills
    assert "grant_diagram" in skills


def test_document_ingestion_routing_detects_only_uploaded_file():
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])
    route = runtime.graph_runner._detect_entry_intent(
        runtime.build_initial_state(
            session_id="intent-doc-only",
            user_input="",
            document_context={
                "file_count": 1,
                "parsed_file_count": 1,
                "combined_excerpt": "paper excerpt",
                "combined_text": "paper excerpt",
                "files": [{"name": "paper.txt"}],
            },
        )
    )

    assert route["skill_name"] == "document_ingestion_routing"
    assert route["detected_intent"] == "document_only_upload"
    assert route["target_skill"] == ""


def test_document_ingestion_routing_hands_off_to_scientific_diagram_when_ready():
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])
    state = runtime.build_initial_state(
        session_id="intent-ready",
        user_input=(
            "primary_discipline: Computer Science\n"
            "conference_name: NeurIPS\n"
            "user_preferences: clean vector style\n"
            "Please create a method diagram from this source text. " * 3
        ),
        document_context={
            "file_count": 0,
            "parsed_file_count": 0,
            "combined_excerpt": "",
            "combined_text": "",
            "files": [],
        },
    )
    state = runtime.graph_runner._apply_design_context(
        state,
        runtime.graph_runner._extract_design_context_from_text(state["user_input"]),
    )
    tool_result = {
        "action": "select_skill",
        "skill_name": "document_ingestion_routing",
        "target_skill": "scientific_diagram",
        "detected_intent": "skill_request",
        "reason": "ready",
    }
    state["selected_skill"] = "document_ingestion_routing"
    state["target_skill"] = "scientific_diagram"
    state["detected_intent"] = "skill_request"

    routed = runtime.graph_runner._handle_document_ingestion_route(state, tool_result)

    assert routed["selected_skill"] == "scientific_diagram"
    assert routed["stage"] == "skill_selected"
    assert routed["skill_plan"]


def test_document_ingestion_routing_selects_grant_diagram_for_grant_request():
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])
    state = runtime.build_initial_state(
        session_id="intent-grant-ready",
        user_input=(
            "请根据基金申请书生成技术路线图 Prompt。\n"
            "项目背景：本项目面向复杂时序数据异常检测，拟研究多尺度表征学习、"
            "异常模式识别和可解释诊断方法，形成面向工业场景的验证系统。"
        ),
        document_context={
            "file_count": 0,
            "parsed_file_count": 0,
            "combined_excerpt": "",
            "combined_text": "",
            "files": [],
        },
    )

    route = runtime.graph_runner._detect_entry_intent(state)

    assert route["skill_name"] == "document_ingestion_routing"
    assert route["detected_intent"] == "skill_request"
    assert route["target_skill"] == "grant_diagram"

    state["selected_skill"] = "document_ingestion_routing"
    state["target_skill"] = "grant_diagram"
    state["detected_intent"] = "skill_request"
    routed = runtime.graph_runner._handle_document_ingestion_route(
        state,
        {
            "action": "select_skill",
            "skill_name": "document_ingestion_routing",
            "target_skill": "grant_diagram",
            "detected_intent": "skill_request",
            "reason": "ready",
        },
    )

    assert routed["selected_skill"] == "grant_diagram"
    assert routed["stage"] == "skill_selected"
    assert [stage["stage"] for stage in routed["skill_plan"]][:2] == [
        "proposal_background_analysis",
        "innovation_extraction",
    ]
    assert routed["skill_plan"][-1]["artifact_aliases"] == ["payload_final"]


def test_grant_diagram_skill_is_natural_language_but_compiles_to_plan():
    skill_path = Path(__file__).resolve().parents[1] / "skills" / "grant_diagram" / "SKILL.md"
    raw = skill_path.read_text(encoding="utf-8")
    frontmatter = raw.split("---", 2)[1]
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])
    skill = runtime.skill_repository.get("grant_diagram")

    assert "plan:" not in frontmatter
    assert [stage["stage"] for stage in skill.plan] == [
        "proposal_background_analysis",
        "innovation_extraction",
        "technical_route_extraction",
        "grant_visual_mapping",
        "grant_prompt_summarization",
    ]
    assert skill.plan[-1]["stage_role"] == "final_prompt"


def test_controller_prompt_is_skill_neutral():
    prompt = (
        Path(__file__).resolve().parents[1]
        / "prompts"
        / "agents"
        / "controller.md"
    ).read_text(encoding="utf-8")

    assert "不要默认所有绘图请求都是科研论文方法图" in prompt
    assert "available_skills" in prompt
    assert "deterministic_route_hint` 只是本地候选提示" in prompt
    assert "grant_diagram_request" not in prompt
    assert "整个双节点科研绘图流程" not in prompt
    assert "标准科研绘图流水线" not in prompt


def test_select_skill_overrides_wrong_llm_choice_for_grant_request(monkeypatch):
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])
    state = runtime.build_initial_state(
        session_id="intent-grant-override",
        user_input=(
            "基金绘图：请根据项目申报书生成研究内容和技术路线图。"
            "本项目围绕跨模态数据治理、核心算法、系统验证和应用示范展开。"
            "首先构建多源异构数据采集与质量评估机制，其次研究统一表示学习模型，"
            "再设计面向业务场景的推理与诊断模块，最后通过原型系统完成示范验证。"
        ),
        document_context={
            "file_count": 0,
            "parsed_file_count": 0,
            "combined_excerpt": "",
            "combined_text": "",
            "files": [],
        },
    )

    def wrong_route(**_kwargs):
        return {
            "tool_result": {
                "action": "select_skill",
                "skill_name": "scientific_diagram",
                "target_skill": "scientific_diagram",
                "detected_intent": "scientific_diagram_request",
                "reason": "wrong model route",
            }
        }

    monkeypatch.setattr(runtime.llm_client, "run_tool_call", wrong_route)

    routed = runtime.graph_runner._select_skill(state)

    assert routed["selected_skill"] == "grant_diagram"
    assert routed["stage"] == "skill_selected"


def test_progressive_session_reuses_previous_document_context(tmp_path):
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])
    monkey_state = runtime.build_initial_state(
        session_id="progressive-session",
        user_input="请基于附件生成图 Prompt",
        document_context={
            "file_count": 1,
            "parsed_file_count": 1,
            "combined_excerpt": "source excerpt",
            "combined_text": "source excerpt",
            "files": [{"name": "paper.txt"}],
        },
    )
    monkey_state["primary_discipline"] = "Computer Science"
    runtime.save_session_state(monkey_state)

    next_state = runtime._build_progressive_initial_state(
        session_id="progressive-session",
        user_input="conference_name: KDD\nuser_preferences: clean",
        document_context={
            "file_count": 0,
            "parsed_file_count": 0,
            "combined_excerpt": "",
            "combined_text": "",
            "files": [],
        },
    )

    assert next_state["document_context"]["file_count"] == 1
    assert next_state["document_context"]["combined_excerpt"] == "source excerpt"
    assert next_state["primary_discipline"] == "Computer Science"


def test_style_extraction_payload_is_independent_from_logic_artifact():
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])
    state = runtime.build_initial_state(
        session_id="style-independent",
        user_input="demo",
        document_context={"files": [], "combined_excerpt": ""},
    )
    state["artifacts"]["payload_logic"] = {"key": "logician", "value": "logic should not leak into style"}
    state["artifacts"]["payload_style"] = {"key": "style_designer", "value": "style guide"}
    state["artifacts"]["payload_mapper"] = {"key": "visual_mapper", "value": "layout spec"}
    state["artifacts"]["stage_outputs"] = {
        "logic_extraction": state["artifacts"]["payload_logic"],
        "style_extraction": state["artifacts"]["payload_style"],
        "visual_mapping": state["artifacts"]["payload_mapper"],
    }

    style_payload = runtime.graph_runner._build_worker_payload(
        state,
        {"task_type": "style_extraction"},
    )
    visual_payload = runtime.graph_runner._build_worker_payload(
        state,
        {
            "task_type": "visual_mapping",
            "artifact_channels": ["payload_logic", "payload_style"],
            "artifact_channel_sources": {
                "payload_logic": {"stage": "logic_extraction", "alias": "payload_logic"},
                "payload_style": {"stage": "style_extraction", "alias": "payload_style"},
            },
            "dedupe_artifact_inputs": True,
            "input_refs": [
                "artifacts.stage_outputs.logic_extraction",
                "artifacts.stage_outputs.style_extraction",
            ],
        },
    )
    summary_payload = runtime.graph_runner._build_worker_payload(
        state,
        {
            "task_type": "summarization",
            "artifact_channels": ["payload_logic", "payload_style", "payload_mapper"],
            "artifact_channel_sources": {
                "payload_logic": {"stage": "logic_extraction", "alias": "payload_logic"},
                "payload_style": {"stage": "style_extraction", "alias": "payload_style"},
                "payload_mapper": {"stage": "visual_mapping", "alias": "payload_mapper"},
            },
            "dedupe_artifact_inputs": True,
            "input_refs": [
                "artifacts.stage_outputs.logic_extraction",
                "artifacts.stage_outputs.style_extraction",
                "artifacts.stage_outputs.visual_mapping",
            ],
        },
    )

    assert style_payload["payload_logic"] == {}
    assert style_payload["payload_style"] == {}
    assert style_payload["payload_mapper"] == {}
    assert style_payload["stage_outputs"] == {}
    assert visual_payload["payload_logic"]["value"] == "logic should not leak into style"
    assert visual_payload["payload_style"]["value"] == "style guide"
    assert visual_payload["payload_mapper"] == {}
    assert visual_payload["resolved_inputs"]["artifacts.stage_outputs.logic_extraction"]["_slimmed"] is True
    assert visual_payload["stage_outputs"]["logic_extraction"]["_slimmed"] is True
    assert sorted(visual_payload["stage_outputs"]) == ["logic_extraction", "style_extraction"]
    assert summary_payload["payload_mapper"]["value"] == "layout spec"
    assert summary_payload["resolved_inputs"]["artifacts.stage_outputs.visual_mapping"]["_slimmed"] is True
    assert sorted(summary_payload["stage_outputs"]) == [
        "logic_extraction",
        "style_extraction",
        "visual_mapping",
    ]
    assert runtime.graph_runner._build_review_upstream_artifacts(state, "style_extraction") == {}
    assert "payload_logic" in runtime.graph_runner._build_review_upstream_artifacts(
        state,
        {
            "task_type": "visual_mapping",
            "artifact_channels": ["payload_logic", "payload_style"],
            "artifact_channel_sources": {
                "payload_logic": {"stage": "logic_extraction", "alias": "payload_logic"},
                "payload_style": {"stage": "style_extraction", "alias": "payload_style"},
            },
        },
    )


def test_artifact_store_writes_refs_and_context_builder_exposes_manifest(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    builder = WorkerContextBuilder(store)
    state = {
        "session_id": "ctx-session",
        "user_input": "demo",
        "document_context": {
            "combined_text": "full text",
            "combined_excerpt": "excerpt",
            "files": [{"name": "paper.txt"}],
        },
        "document_context_summary": {},
        "artifacts": {"stage_outputs": {}, "artifact_refs": {}},
    }
    task = {
        "task_id": "logic-task",
        "task_type": "logic_extraction",
        "output_ref": "stage_outputs.logic_extraction",
        "artifact_aliases": ["payload_logic"],
    }
    artifact = {"key": "logician", "value": "logic artifact"}

    ref = store.store_task_artifact(
        state,
        task=task,
        artifact_ref="stage_outputs.logic_extraction",
        artifact=artifact,
    )
    payload = builder.build_worker_payload(
        state,
        {
            "task_type": "visual_mapping",
            "artifact_channels": ["payload_logic"],
            "artifact_channel_sources": {
                "payload_logic": {"stage": "logic_extraction", "alias": "payload_logic"},
            },
            "dedupe_artifact_inputs": True,
            "input_refs": ["artifacts.stage_outputs.logic_extraction"],
        },
    )

    assert ref is not None
    assert Path(ref["path"]).exists()
    assert state["artifacts"]["payload_logic"] == artifact
    assert state["artifacts"]["stage_outputs"]["logic_extraction"] == artifact
    assert "artifacts.stage_outputs.logic_extraction" in payload["artifact_refs"]
    assert payload["input_manifest"]["artifacts.stage_outputs.logic_extraction"]["type"] == "dict"
    assert payload["payload_logic"] == artifact
    assert payload["resolved_inputs"]["artifacts.stage_outputs.logic_extraction"]["_slimmed"] is True
    assert payload["resolved_inputs"]["artifacts.stage_outputs.logic_extraction"]["artifact_ref"]["path"] == ref["path"]
    assert payload["stage_outputs"]["logic_extraction"]["_slimmed"] is True

    generic_payload = builder.build_worker_payload(
        state,
        {
            "task_type": "custom_stage",
            "input_refs": ["artifacts.stage_outputs.logic_extraction"],
        },
    )
    assert generic_payload["resolved_inputs"]["artifacts.stage_outputs.logic_extraction"] == artifact


def test_scientific_diagram_dispatches_logic_and_style_as_parallel_batch(monkeypatch):
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])
    state = runtime.build_initial_state(
        session_id="parallel-dispatch",
        user_input="demo source material " * 8,
        document_context={"files": [], "combined_excerpt": ""},
    )
    state["selected_skill"] = "scientific_diagram"
    state["skill_plan"] = runtime.skill_repository.get("scientific_diagram").plan
    state["primary_discipline"] = "Computer Science"
    state["conference_name"] = "NeurIPS"
    state["user_preferences"] = "clean vector"

    monkeypatch.setattr(
        runtime.llm_client,
        "run_tool_call",
        lambda **_kwargs: {"tool_result": {"task_type": "logic_extraction", "revision_mode": False}},
    )

    next_state = runtime.graph_runner._dispatch_next_pipeline_stage(state)

    assert [task["task_type"] for task in next_state["active_tasks"]] == [
        "logic_extraction",
        "style_extraction",
    ]
    assert next_state["stage"] == "parallel_virtual_tasks"


def test_controller_outputs_orchestration_plan_before_dispatch():
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])
    state = runtime.build_initial_state(
        session_id="orchestration-preview",
        user_input="demo source material " * 8,
        document_context={"files": [], "combined_excerpt": "", "combined_text": "demo source material " * 8},
    )
    state["selected_skill"] = "scientific_diagram"
    state["skill_plan"] = runtime.skill_repository.get("scientific_diagram").plan
    state["primary_discipline"] = "Computer Science"
    state["conference_name"] = "NeurIPS"
    state["user_preferences"] = "clean vector"
    state["stage"] = "skill_selected"

    next_state = runtime.graph_runner._controller_node(state)

    assert next_state["stage"] == "awaiting_orchestration_confirmation"
    assert next_state["awaiting_orchestration_confirmation"] is True
    assert next_state["active_tasks"] == []
    plan = next_state["final_response"]["orchestration_plan"]
    assert [stage["stage"] for stage in plan["stages"][:3]] == [
        "logic_extraction",
        "style_extraction",
        "visual_mapping",
    ]
    assert plan["batches"][0] == ["logic_extraction", "style_extraction"]


def test_orchestration_confirmation_allows_dispatch(monkeypatch):
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])
    state = runtime.build_initial_state(
        session_id="orchestration-confirmed",
        user_input="demo source material " * 8,
        document_context={"files": [], "combined_excerpt": "", "combined_text": "demo source material " * 8},
    )
    state["selected_skill"] = "scientific_diagram"
    state["skill_plan"] = runtime.skill_repository.get("scientific_diagram").plan
    state["primary_discipline"] = "Computer Science"
    state["conference_name"] = "NeurIPS"
    state["user_preferences"] = "clean vector"
    state["stage"] = "skill_selected"
    state["orchestration_confirmed"] = True

    monkeypatch.setattr(
        runtime.llm_client,
        "run_tool_call",
        lambda **_kwargs: {"tool_result": {"task_type": "logic_extraction", "revision_mode": False}},
    )

    next_state = runtime.graph_runner._controller_node(state)

    assert next_state["stage"] == "parallel_virtual_tasks"
    assert [task["task_type"] for task in next_state["active_tasks"]] == [
        "logic_extraction",
        "style_extraction",
    ]


def test_runtime_confirm_orchestration_resumes_saved_session(monkeypatch):
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])
    state = runtime.build_initial_state(
        session_id="confirm-orchestration-session",
        user_input="demo",
        document_context={"files": [], "combined_excerpt": "", "combined_text": "demo"},
    )
    state["selected_skill"] = "scientific_diagram"
    state["skill_plan"] = runtime.skill_repository.get("scientific_diagram").plan
    state["stage"] = "awaiting_orchestration_confirmation"
    state["awaiting_orchestration_confirmation"] = True
    runtime.save_session_state(state)

    def fake_run(resumed_state):
        assert resumed_state["orchestration_confirmed"] is True
        assert resumed_state["awaiting_orchestration_confirmation"] is False
        resumed_state["stage"] = "completed"
        resumed_state["prompt_checkpoint_id"] = ""
        return resumed_state

    monkeypatch.setattr(runtime.graph_runner, "run", fake_run)

    result = runtime.confirm_orchestration("confirm-orchestration-session")

    assert result["stage"] == "completed"
    assert result["workflow_state"]["orchestration_confirmed"] is True


def test_ready_skill_stages_waits_for_declared_dependencies():
    skill_plan = [
        {"stage": "logic_extraction", "depends_on": []},
        {"stage": "style_extraction", "depends_on": []},
        {"stage": "visual_mapping", "depends_on": ["logic_extraction", "style_extraction"]},
    ]

    assert [item["stage"] for item in ready_skill_stages(skill_plan, [], max_parallel=2)] == [
        "logic_extraction",
        "style_extraction",
    ]
    assert ready_skill_stages(
        skill_plan,
        [{"task_type": "logic_extraction", "status": "approved"}],
        active_tasks=[{"task_type": "style_extraction"}],
        max_parallel=2,
    ) == []
    assert [item["stage"] for item in ready_skill_stages(
        skill_plan,
        [
            {"task_type": "logic_extraction", "status": "approved"},
            {"task_type": "style_extraction", "status": "approved"},
        ],
        max_parallel=2,
    )] == ["visual_mapping"]


def test_natural_language_skill_compiler_infers_scientific_diagram_plan():
    body = """
    这个 skill 用于科研绘图。
    需要先获得论文全文和用户设计偏好。
    可以并行做逻辑提取和风格提取。
    逻辑提取读取全文得到逻辑结构。
    风格提取读取学科、会议、偏好得到风格约束。
    视觉映射依赖前两者得到布局方案。
    最后汇总为英文绘图 prompt。
    """

    plan = compile_skill_plan([], skill_description="科研绘图", skill_body=body)

    assert [stage["stage"] for stage in plan] == [
        "logic_extraction",
        "style_extraction",
        "visual_mapping",
        "summarization",
    ]
    assert plan[0]["output_ref"] == "stage_outputs.logic_extraction"
    assert plan[0]["artifact_aliases"] == ["payload_logic"]
    assert plan[1]["artifact_aliases"] == ["payload_style"]
    assert plan[2]["depends_on"] == ["logic_extraction", "style_extraction"]
    assert "artifacts.stage_outputs.logic_extraction" in plan[2]["input_refs"]
    assert "payload_logic" not in plan[2]["input_refs"]


def test_scientific_diagram_logic_consumes_full_text_directly():
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])
    stages = {stage["stage"]: stage for stage in runtime.skill_repository.get("scientific_diagram").plan}

    assert "method_section_extraction" not in stages
    assert stages["logic_extraction"]["depends_on"] == []
    assert stages["logic_extraction"]["input_refs"] == ["document.full_text", "user.input"]


def test_natural_language_skill_compiler_builds_generic_stage_plan():
    body = """
    这个 skill 用于基金绘图。
    先分析申报书全文，得到研究背景。
    再提取创新点和技术路线。
    最后汇总生成基金图英文 prompt。
    """

    plan = compile_skill_plan([], skill_description="基金绘图", skill_body=body)

    assert len(plan) >= 3
    assert all(stage["output_ref"].startswith("stage_outputs.") for stage in plan)
    assert plan[0]["input_refs"][0] == "document.full_text"
    assert plan[1]["depends_on"] == [plan[0]["stage"]]
    assert plan[-1]["stage_role"] == "final_prompt"
    assert any(ref.startswith("artifacts.stage_outputs.") for ref in plan[-1]["input_refs"])


def test_skill_repository_compiles_natural_language_skill_without_yaml_plan(tmp_path):
    skill_dir = tmp_path / "skills" / "grant_diagram"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: grant_diagram
description: Grant diagram skill described in natural language.
---

First analyze the full proposal document to get the research background.
Then extract innovation points and the technical route.
Finally summarize everything into an English diagram prompt.
""",
        encoding="utf-8",
    )

    skill = SkillRepository(tmp_path).get("grant_diagram")

    assert skill.plan
    assert skill.plan[0]["output_ref"].startswith("stage_outputs.")
    assert skill.plan[0]["input_refs"][0] == "document.full_text"
    assert skill.plan[-1]["stage_role"] == "final_prompt"


def test_skill_plan_validator_rejects_cycles_and_unknown_refs():
    result = validate_skill_plan(
        [
            {
                "stage": "a",
                "depends_on": ["b"],
                "input_refs": ["artifacts.stage_outputs.missing"],
                "output_ref": "stage_outputs.a",
            },
            {
                "stage": "b",
                "depends_on": ["a"],
                "input_refs": ["artifacts.stage_outputs.a"],
                "output_ref": "stage_outputs.a",
            },
        ]
    )

    assert result["ok"] is False
    assert any("dependency cycle" in item for item in result["errors"])
    assert any("unknown stage output missing" in item for item in result["errors"])
    assert any("reuses output_ref" in item for item in result["errors"])


def test_orchestration_plan_includes_validation_result():
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])
    state = runtime.build_initial_state(
        session_id="validation-plan",
        user_input="demo",
        document_context={"files": [], "combined_excerpt": "", "combined_text": "demo"},
    )
    state["selected_skill"] = "scientific_diagram"
    state["skill_plan"] = runtime.skill_repository.get("scientific_diagram").plan

    plan = runtime.graph_runner._build_orchestration_plan(state)

    assert plan["validation"]["ok"] is True
    assert plan["validation"]["errors"] == []


def test_agent_registry_enriches_skill_plan_with_worker_profile():
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])
    plan = runtime.skill_repository.get("scientific_diagram").plan
    stages = {stage["stage"]: stage for stage in plan}

    assert stages["logic_extraction"]["agent_name"] == "logic_extractor"
    assert stages["logic_extraction"]["prompt_name"] == "logic_extraction"
    assert stages["logic_extraction"]["output_contract"] == "logic_artifact"
    assert stages["style_extraction"]["agent_name"] == "style_extractor"
    assert stages["visual_mapping"]["agent_name"] == "visual_mapper"
    assert stages["visual_mapping"]["input_refs"] == [
        "artifacts.stage_outputs.logic_extraction",
        "artifacts.stage_outputs.style_extraction",
        "user.input",
    ]
    assert stages["visual_mapping"]["artifact_channels"] == ["payload_logic", "payload_style"]
    assert stages["visual_mapping"]["artifact_channel_sources"]["payload_logic"] == {
        "stage": "logic_extraction",
        "alias": "payload_logic",
    }
    assert stages["visual_mapping"]["dedupe_artifact_inputs"] is True
    assert stages["summarization"]["artifact_channels"] == [
        "payload_logic",
        "payload_style",
        "payload_mapper",
    ]
    assert stages["summarization"]["artifact_channel_sources"]["payload_mapper"] == {
        "stage": "visual_mapping",
        "alias": "payload_mapper",
    }
    assert stages["summarization"]["dedupe_artifact_inputs"] is True
    assert "user.input" in stages["summarization"]["input_refs"]


def test_initial_user_requirements_reach_downstream_workers_and_reviewer():
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])
    requirement = "请突出双重注意力机制，并用强调色展示它与上下游模块的连接"
    state = runtime.build_initial_state(
        session_id="user-requirement-propagation",
        user_input=requirement,
        document_context={"files": [], "combined_excerpt": "paper", "combined_text": "paper"},
    )
    stages = {
        stage["stage"]: stage
        for stage in runtime.skill_repository.get("scientific_diagram").plan
    }

    for stage_name in ("visual_mapping", "summarization"):
        task = {**stages[stage_name], "task_type": stage_name}
        payload = runtime.graph_runner._build_worker_payload(state, task)
        assert payload["resolved_inputs"]["user.input"] == requirement
        assert payload["user_input"] == requirement
        review_input = runtime.graph_runner._build_review_task_input(payload)
        assert review_input["user_input"] == requirement


def test_agent_registry_filters_disallowed_stage_inputs():
    registry = AgentRegistry.from_root(Path(__file__).resolve().parents[1])
    enriched = registry.enrich_stage(
        {
            "stage": "style_extraction",
            "stage_role": "style",
            "agent_name": "style_extractor",
            "input_refs": [
                "user.primary_discipline",
                "artifacts.stage_outputs.logic_extraction",
            ],
        }
    )

    assert enriched["input_refs"] == ["user.primary_discipline"]
    assert "artifacts.stage_outputs.logic_extraction" not in enriched["input_refs"]


def test_mcp_virtual_agent_uses_registry_prompt_and_contract(monkeypatch):
    from mcp_servers.drawagent_mcp_server import DrawAgentMCPServer

    calls = {}

    class FakeClient:
        def run_tool_call(self, **kwargs):
            calls.update(kwargs)
            assert kwargs["tools"][0].name == "submit_logic_artifact"
            return {
                "model": kwargs["model"],
                "request_api_mode": "responses",
                "tool_result": {
                    "artifact": {"key": "logician", "value": "logic"},
                    "summary": "ok",
                },
            }

    server = DrawAgentMCPServer()
    monkeypatch.setattr(server, "_client_for_role", lambda role: FakeClient())

    result = server._handle_run_virtual_agent(
        {
            "agent_type": "logic_extraction",
            "agent_name": "logic_extractor",
            "stage_role": "logic",
            "document_excerpt": "full paper text",
            "input_refs": ["document.full_text"],
            "resolved_inputs": {"document.full_text": "full paper text"},
        }
    )

    assert result["prompt_name"] == "logic_extraction"
    assert result["output_contract"] == "logic_artifact"
    assert result["model_role"] == "logic_extraction"
    assert calls["max_tokens"] == 1600
    assert list(calls["user_payload"].keys())[:3] == [
        "document_excerpt",
        "payload_logic",
        "payload_style",
    ]
    assert "document_context_summary" not in calls["user_payload"]
    assert calls["user_payload"]["document_excerpt"] == "full paper text"
    assert list(calls["user_payload"].keys()).index("input_refs") < list(
        calls["user_payload"].keys()
    ).index("user_input")
    assert list(calls["user_payload"].keys())[-3:] == [
        "session_id",
        "revision_mode",
        "revision_context",
    ]


def test_llm_user_payload_json_is_compact_and_preserves_order():
    from app.llm.openai_client import OpenAICompatibleClient

    payload = {"document_excerpt": "paper", "session_id": "s1"}
    text = OpenAICompatibleClient._json_user_text(payload)

    assert text == '{"document_excerpt":"paper","session_id":"s1"}'


def test_responses_sse_reassembles_completed_tool_call():
    import httpx

    from app.llm.openai_client import OpenAICompatibleClient

    added = {
        "type": "response.output_item.added",
        "output_index": 0,
        "item": {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "submit_generic_artifact",
            "arguments": "",
            "status": "in_progress",
        },
    }
    done = {
        "type": "response.output_item.done",
        "output_index": 0,
        "item": {
            **added["item"],
            "arguments": '{"artifact":"OK","summary":"connected"}',
            "status": "completed",
        },
    }
    completed = {
        "type": "response.completed",
        "response": {
            "id": "resp_1",
            "object": "response",
            "status": "completed",
            "model": "gpt-5.6-luna",
            "output": [],
        },
    }
    body = "\n\n".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}"
        for event in (added, done, completed)
    ) + "\n\n"
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text=body,
    )

    parsed = OpenAICompatibleClient._decode_json_or_sse_response(response)
    name, arguments = OpenAICompatibleClient._extract_responses_function_call(
        OpenAICompatibleClient,
        parsed,
    )

    assert parsed["model"] == "gpt-5.6-luna"
    assert len(parsed["output"]) == 1
    assert name == "submit_generic_artifact"
    assert arguments == {"artifact": "OK", "summary": "connected"}


def test_invalid_json_provider_response_is_retryable():
    from app.core.errors import classify_error

    error = classify_error(
        "LLM request failed after retries: status=200 error=Expecting value: line 1 column 1"
    )

    assert error["code"] == "invalid_provider_response"
    assert DualNodeWorkflowGraph._is_retryable_worker_error(error) is True


def test_frontend_distinguishes_service_failure_from_content_review():
    source = (Path(__file__).resolve().parents[1] / "app/frontend/static/app.js").read_text(
        encoding="utf-8"
    )

    assert "模型服务调用失败" in source
    assert "内容审查未通过" in source


def test_logic_extraction_payload_uses_full_document_text():
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])
    state = runtime.build_initial_state(
        session_id="logic-full-text",
        user_input="demo",
        document_context={
            "files": [{"name": "paper.txt"}],
            "combined_excerpt": "short excerpt",
            "combined_text": "full document text with method details",
        },
    )

    logic_payload = runtime.graph_runner._build_worker_payload(
        state,
        {"task_type": "logic_extraction", "input_refs": ["document.full_text"]},
    )
    style_payload = runtime.graph_runner._build_worker_payload(
        state,
        {"task_type": "style_extraction"},
    )

    assert logic_payload["document_excerpt"] == "full document text with method details"
    assert logic_payload["resolved_inputs"]["document.full_text"]["_slimmed"] is True
    assert logic_payload["resolved_inputs"]["document.full_text"]["available_as"] == "document_excerpt"
    assert style_payload["document_excerpt"] == "short excerpt"

    review_task_input = runtime.graph_runner._build_review_task_input(logic_payload)
    assert "resolved_inputs" not in review_task_input
    assert "input_payload" not in review_task_input
    assert review_task_input["input_manifest"]["document.full_text"]["chars"] == len(
        "full document text with method details"
    )
    assert review_task_input["document_excerpt_preview"] == "full document text with method details"


def test_logic_review_task_input_keeps_full_document_for_long_source():
    runtime = DrawAgentRuntime(Path(__file__).resolve().parents[1])
    long_text = "intro " * 800 + "method details " * 500
    state = runtime.build_initial_state(
        session_id="logic-review-full-text",
        user_input="demo",
        document_context={
            "files": [{"name": "paper.txt"}],
            "combined_excerpt": long_text[:2000],
            "combined_text": long_text,
        },
    )

    logic_payload = runtime.graph_runner._build_worker_payload(
        state,
        {
            "task_type": "logic_extraction",
            "agent_name": "logic_extractor",
            "prompt_name": "logic_extraction",
            "input_refs": ["document.full_text"],
        },
    )
    review_task_input = runtime.graph_runner._build_review_task_input(logic_payload)

    assert review_task_input["document_excerpt_preview"] == long_text
    assert review_task_input["document_excerpt_chars"] == len(long_text)


def test_virtual_worker_review_judges_current_attempt_without_prior_review_ghosts():
    captured = {}

    def run_virtual_agent(**_kwargs):
        return {"artifact": {"key": "logician", "value": "current fixed logic"}}

    def run_review(**kwargs):
        captured.update(kwargs)
        return {"approved": True, "signal": "positive", "blocking": False}

    graph = DualNodeWorkflowGraph.__new__(DualNodeWorkflowGraph)
    graph.runtime = SimpleNamespace(
        config=SimpleNamespace(artifact_dir=Path("runtime/artifacts")),
        is_stop_requested=lambda _session_id: False,
        run_virtual_agent=run_virtual_agent,
        run_review=run_review,
    )
    task = {
        "task_id": "task-1",
        "task_type": "logic_extraction",
        "agent_name": "logic_extractor",
        "prompt_name": "logic_extraction",
        "output_ref": "payload_logic",
        "input_refs": ["document.full_text"],
        "revision_mode": True,
        "review_phase": "logic_review",
    }
    state = {
        "session_id": "review-current-attempt",
        "user_input": "demo",
        "document_context": {
            "combined_text": "full source text",
            "combined_excerpt": "short source text",
            "files": [],
        },
        "document_context_summary": {},
        "artifacts": {},
        "messages": [],
        "review_history": [{"approved": False, "issues": ["old issue"]}],
        "pending_tasks": [task],
        "active_task": task,
        "active_tasks": [task],
    }

    graph._virtual_worker_node(state)

    assert captured["prior_reviews"] == []


def test_virtual_worker_failure_emits_fatal_review_without_fallback_artifact():
    def fail_virtual_agent(**_kwargs):
        raise RuntimeError("401 Unauthorized")

    graph = DualNodeWorkflowGraph.__new__(DualNodeWorkflowGraph)
    graph.runtime = SimpleNamespace(
        is_stop_requested=lambda _session_id: False,
        run_virtual_agent=fail_virtual_agent,
    )
    task = {
        "task_id": "task-1",
        "task_type": "logic_extraction",
        "output_ref": "payload_logic",
        "revision_mode": False,
        "review_phase": "logic_review",
    }
    state = {
        "session_id": "worker-failure",
        "user_input": "demo",
        "document_context": {
            "combined_text": "full source text",
            "combined_excerpt": "short source text",
            "files": [],
        },
        "document_context_summary": {},
        "artifacts": {},
        "messages": [],
        "review_history": [],
        "pending_tasks": [task],
        "active_task": task,
    }

    next_state = graph._virtual_worker_node(state)

    assert "payload_logic" not in next_state["artifacts"]
    assert next_state["review_history"][-1]["signal"] == "fatal"
    assert next_state["messages"][-1]["message_type"] == "fatal_review"


def test_transient_worker_failure_retries_same_task_without_controller_llm():
    graph = DualNodeWorkflowGraph.__new__(DualNodeWorkflowGraph)
    graph.runtime = SimpleNamespace(
        config=SimpleNamespace(max_review_rounds=3),
    )
    task = {
        "task_id": "task-1",
        "task_type": "summarization",
        "agent_name": "prompt_summarizer",
        "output_ref": "stage_outputs.summarization",
        "retry_count": 0,
        "max_retry": 3,
        "revision_mode": False,
        "review_phase": "final_prompt_review",
    }
    state = {
        "session_id": "transient-worker-retry",
        "skill_plan": [
            {
                "stage": "summarization",
                "agent_name": "prompt_summarizer",
                "output_ref": "stage_outputs.summarization",
                "review_phase": "final_prompt_review",
            }
        ],
        "pending_tasks": [],
        "active_tasks": [],
    }
    review = {
        "approved": False,
        "signal": "negative",
        "blocking": False,
        "transient_error": True,
        "error_code": "rate_limited",
        "issues": ["429 Too Many Requests"],
    }

    next_state = graph._handle_message(
        state,
        {
            "message_type": "negative_review",
            "task_type": "summarization",
            "payload": {"review": review, "task": task},
        },
    )

    assert next_state["stage"] == "summarization_retrying"
    assert next_state["active_tasks"][0]["task_type"] == "summarization"
    assert next_state["active_tasks"][0]["retry_count"] == 1


def test_transient_worker_failure_hard_fails_after_retry_budget():
    graph = DualNodeWorkflowGraph.__new__(DualNodeWorkflowGraph)
    graph.runtime = SimpleNamespace(
        config=SimpleNamespace(max_review_rounds=3),
    )
    task = {
        "task_id": "task-1",
        "task_type": "summarization",
        "retry_count": 3,
        "max_retry": 3,
    }
    state = {
        "session_id": "transient-worker-exhausted",
        "skill_plan": [],
        "pending_tasks": [],
        "active_tasks": [],
    }
    review = {
        "approved": False,
        "signal": "negative",
        "blocking": False,
        "transient_error": True,
        "error_code": "rate_limited",
        "issues": ["429 Too Many Requests"],
    }

    next_state = graph._handle_message(
        state,
        {
            "message_type": "negative_review",
            "task_type": "summarization",
            "payload": {"review": review, "task": task},
        },
    )

    assert next_state["stage"] == "review_failed"
    assert next_state["final_response"]["review_status"] == "failed"


def test_virtual_worker_runs_independent_active_tasks_concurrently():
    barrier = threading.Barrier(2, timeout=2)

    def run_virtual_agent(agent_type, **_kwargs):
        barrier.wait()
        return {"artifact": {"key": agent_type, "value": agent_type}}

    graph = DualNodeWorkflowGraph.__new__(DualNodeWorkflowGraph)
    graph.runtime = SimpleNamespace(
        config=SimpleNamespace(max_concurrent_virtual_tasks=2),
        is_stop_requested=lambda _session_id: False,
        run_virtual_agent=run_virtual_agent,
        run_review=lambda **_kwargs: {"approved": True, "signal": "positive"},
    )
    tasks = [
        {
            "task_id": "task-logic",
            "task_type": "logic_extraction",
            "output_ref": "payload_logic",
            "revision_mode": False,
            "review_phase": "logic_review",
        },
        {
            "task_id": "task-style",
            "task_type": "style_extraction",
            "output_ref": "payload_style",
            "revision_mode": False,
            "review_phase": "style_review",
        },
    ]
    state = {
        "session_id": "parallel-worker",
        "user_input": "demo",
        "document_context": {
            "combined_text": "full source text",
            "combined_excerpt": "short source text",
            "files": [],
        },
        "document_context_summary": {},
        "artifacts": {},
        "messages": [],
        "review_history": [],
        "pending_tasks": tasks,
        "active_task": tasks[0],
        "active_tasks": tasks,
    }

    next_state = graph._virtual_worker_node(state)

    assert next_state["artifacts"]["payload_logic"]["value"] == "logic_extraction"
    assert next_state["artifacts"]["payload_style"]["value"] == "style_extraction"
    assert [message["task_type"] for message in next_state["messages"]] == [
        "logic_extraction",
        "style_extraction",
    ]
    assert next_state["active_tasks"] == []


def test_virtual_task_executor_records_events_and_cleans_up(tmp_path):
    store = RunEventStore(tmp_path / "runs")
    executor = VirtualTaskExecutor(max_workers=1, event_store=store)
    task = {"task_id": "task-1", "task_type": "logic_extraction"}

    run_id = executor.submit(
        session_id="session-1",
        task=task,
        handler=lambda _cancel_event: {"task": task, "stage": "done"},
    )

    assert executor.wait_many([run_id])[0]["stage"] == "done"
    assert executor.list(session_id="session-1")[0]["status"] == "completed"

    executor.cleanup(run_id)

    assert executor.list(session_id="session-1") == []
    event_types = [event["event_type"] for event in store.read("session-1")]
    assert event_types == [
        "task.submitted",
        "task.started",
        "task.completed",
        "task.cleaned_up",
    ]


def test_virtual_task_executor_cancel_session_signals_running_task(tmp_path):
    store = RunEventStore(tmp_path / "runs")
    executor = VirtualTaskExecutor(max_workers=1, event_store=store)
    task = {"task_id": "task-2", "task_type": "style_extraction"}
    handler_started = threading.Event()

    def cancellable_handler(cancel_event):
        handler_started.set()
        return {
            "task": task,
            "stage": "cancelled" if cancel_event.wait(timeout=1) else "not_cancelled",
        }

    run_id = executor.submit(
        session_id="session-cancel",
        task=task,
        handler=cancellable_handler,
    )

    assert handler_started.wait(timeout=1)

    assert executor.cancel_session("session-cancel") == 1
    assert executor.wait_many([run_id])[0]["stage"] == "cancelled"
    assert executor.list(session_id="session-cancel")[0]["status"] == "cancelled"
    executor.cleanup(run_id)

    event_types = [event["event_type"] for event in store.read("session-cancel")]
    assert "task.cancel_requested" in event_types
    assert "task.cancelled" in event_types
