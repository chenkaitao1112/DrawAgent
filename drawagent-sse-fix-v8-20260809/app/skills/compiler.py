from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


ROLE_STAGE_NAMES = {
    "logic": "logic_extraction",
    "style": "style_extraction",
    "visual_mapping": "visual_mapping",
    "final_prompt": "summarization",
    "image": "image_generation",
}

ROLE_AGENT_NAMES = {
    "logic": "logic_extractor",
    "style": "style_extractor",
    "visual_mapping": "visual_mapper",
    "final_prompt": "prompt_summarizer",
    "image": "image_generator",
    "generic": "generic_worker",
}


def _slugify(value: str, fallback: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", (value or "").strip()).strip("_").lower()
    return text or fallback


def _role_text(stage: dict[str, Any]) -> str:
    parts = [
        str(stage.get("stage") or ""),
        str(stage.get("agent_name") or ""),
        str(stage.get("role") or ""),
        str(stage.get("goal") or ""),
        str(stage.get("description") or ""),
        str(stage.get("stage_goal") or ""),
    ]
    return " ".join(parts).lower()


def infer_stage_role(stage: dict[str, Any]) -> str:
    text = _role_text(stage)
    if any(keyword in text for keyword in ["image", "生图", "出图", "图片"]):
        return "image"
    if any(keyword in text for keyword in ["summary", "summar", "final", "prompt", "汇总", "总结", "提示词"]):
        return "final_prompt"
    if any(keyword in text for keyword in ["visual", "mapping", "layout", "mapper", "布局", "映射", "视觉"]):
        return "visual_mapping"
    if any(keyword in text for keyword in ["style", "风格", "会议", "期刊", "偏好"]):
        return "style"
    if any(keyword in text for keyword in ["logic", "结构", "方法", "逻辑"]):
        return "logic"
    return "generic"


def _legacy_aliases(role: str) -> list[str]:
    aliases = {
        "logic": ["payload_logic"],
        "style": ["payload_style"],
        "visual_mapping": ["payload_mapper"],
        "final_prompt": ["payload_final"],
        "image": ["image_result"],
    }
    return list(aliases.get(role, []))


def _dependencies(stage: dict[str, Any], previous_stage_names: list[str]) -> list[str]:
    if "depends_on" in stage:
        raw = stage.get("depends_on") or []
        if isinstance(raw, str):
            return [raw]
        return [str(item) for item in raw]
    return list(previous_stage_names)


def _stage_output_ref(stage_name: str) -> str:
    return f"stage_outputs.{_slugify(stage_name, 'stage')}"


def _clean_line(line: str) -> str:
    line = re.sub(r"^\s*#{1,6}\s*", "", line.strip())
    line = re.sub(r"^\s*[-*+]\s+", "", line)
    line = re.sub(r"^\s*\d+[.)、]\s*", "", line)
    return line.strip()


def _split_natural_language_units(text: str) -> list[str]:
    units: list[str] = []
    for raw_line in text.splitlines():
        line = _clean_line(raw_line)
        if not line:
            continue
        if line.lower() in {"skill intent", "entry requirements", "orchestration rules", "review rules"}:
            continue
        for part in re.split(r"[。；;]", line):
            part = _clean_line(part)
            if part:
                units.append(part)
    return units


def _roles_mentioned(text: str) -> list[str]:
    lowered = text.lower()
    roles: list[str] = []
    patterns = [
        ("logic", ["logic", "逻辑", "方法", "结构"]),
        ("style", ["style", "风格", "会议", "期刊", "偏好"]),
        ("visual_mapping", ["visual", "mapping", "layout", "视觉", "映射", "布局"]),
        ("final_prompt", ["summary", "summar", "final", "prompt", "汇总", "总结", "提示词"]),
        ("image", ["image", "生图", "出图", "图片"]),
    ]
    for role, keywords in patterns:
        if any(keyword in lowered for keyword in keywords):
            roles.append(role)
    return roles


def _mentions_document(text: str) -> bool:
    lowered = text.lower()
    return any(
        keyword in lowered
        for keyword in ["全文", "正文", "论文", "文档", "材料", "附件", "申报书", "document", "paper", "source", "full text"]
    )


def _mentions_user_context(text: str) -> bool:
    lowered = text.lower()
    return any(
        keyword in lowered
        for keyword in ["学科", "会议", "期刊", "偏好", "用户", "要求", "preference", "conference", "discipline"]
    )


def _default_input_refs(
    *,
    role: str,
    dependencies: list[str],
    previous_stage_names: list[str],
) -> list[str]:
    if role == "image":
        return ["artifacts.payload_final"]
    if role == "logic":
        return ["document.full_text", "user.input"]
    if role == "style":
        return [
            "user.primary_discipline",
            "user.conference_name",
            "user.preferences",
            "user.input",
        ]
    if role == "final_prompt":
        upstream = previous_stage_names or dependencies
        return [f"artifacts.stage_outputs.{name}" for name in upstream] + ["user.input"]
    if dependencies:
        return [f"artifacts.stage_outputs.{name}" for name in dependencies] + ["user.input"]
    return ["user.input"]


def _infer_input_refs_from_text(
    *,
    role: str,
    text: str,
    dependencies: list[str],
    previous_stage_names: list[str],
) -> list[str]:
    refs: list[str] = []
    if _mentions_document(text):
        refs.append("document.full_text")
    if _mentions_user_context(text):
        lowered = text.lower()
        if "学科" in text or "discipline" in lowered:
            refs.append("user.primary_discipline")
        if "会议" in text or "期刊" in text or "conference" in lowered:
            refs.append("user.conference_name")
        if "偏好" in text or "preference" in lowered or "要求" in text:
            refs.append("user.preferences")
        refs.append("user.input")
    if dependencies:
        refs.extend(f"artifacts.stage_outputs.{item}" for item in dependencies)
    if role != "image" and "user.input" not in refs:
        refs.append("user.input")
    if not refs:
        refs = _default_input_refs(
            role=role,
            dependencies=dependencies,
            previous_stage_names=previous_stage_names,
        )
    deduped: list[str] = []
    for ref in refs:
        if ref not in deduped:
            deduped.append(ref)
    return deduped


def _stage_name_for_role(role: str, index: int, source_text: str) -> str:
    if role in ROLE_STAGE_NAMES:
        return ROLE_STAGE_NAMES[role]
    slug = _slugify(source_text[:48], f"stage_{index}")
    return slug if slug != "stage" else f"stage_{index}"


def _append_or_merge_stage(
    stages: list[dict[str, Any]],
    *,
    role: str,
    goal: str,
    dependencies: list[str],
    previous_stage_names: list[str],
) -> None:
    stage_name = _stage_name_for_role(role, len(stages) + 1, goal)
    existing = next((item for item in stages if item.get("stage") == stage_name), None)
    input_refs = _infer_input_refs_from_text(
        role=role,
        text=goal,
        dependencies=dependencies,
        previous_stage_names=previous_stage_names,
    )
    if existing is not None:
        existing_goal = str(existing.get("stage_goal") or existing.get("goal") or "")
        if goal and goal not in existing_goal:
            existing["stage_goal"] = f"{existing_goal}\n{goal}".strip()
        merged_refs = list(existing.get("input_refs") or [])
        for ref in input_refs:
            if ref not in merged_refs:
                merged_refs.append(ref)
        existing["input_refs"] = merged_refs
        if dependencies and not existing.get("depends_on"):
            existing["depends_on"] = dependencies
        return

    stages.append(
        {
            "stage": stage_name,
            "agent_name": ROLE_AGENT_NAMES.get(role, "generic_worker"),
            "role": role,
            "depends_on": dependencies,
            "input_refs": input_refs,
            "stage_goal": goal or f"Complete the {stage_name} stage.",
            "review_required": role != "image",
            "trigger": "user_confirm" if role == "image" else "",
        }
    )


def _looks_like_canonical_diagram_skill(text: str) -> bool:
    lowered = text.lower()
    return (
        any(keyword in lowered for keyword in ["logic", "逻辑", "方法结构"])
        and any(keyword in lowered for keyword in ["style", "风格", "会议", "偏好"])
        and any(keyword in lowered for keyword in ["visual", "mapping", "layout", "视觉", "映射", "布局"])
        and any(keyword in lowered for keyword in ["prompt", "summary", "汇总", "总结", "提示词"])
    )


def _looks_like_grant_diagram_skill(text: str) -> bool:
    lowered = text.lower()
    return any(
        keyword in lowered
        for keyword in [
            "基金",
            "申请书",
            "申报书",
            "研究计划",
            "技术路线",
            "创新点",
            "项目申请",
            "项目申报",
            "grant",
            "proposal",
            "funding application",
            "research plan",
        ]
    )


def _grant_diagram_plan(text: str) -> list[dict[str, Any]]:
    return [
        {
            "stage": "proposal_background_analysis",
            "agent_name": "generic_worker",
            "role": "generic",
            "depends_on": [],
            "input_refs": ["document.full_text", "user.input"],
            "stage_goal": "Read the grant or project application material and extract the background, problem context, project objectives, and major research questions.",
            "review_required": True,
            "review_phase": "generic_review",
        },
        {
            "stage": "innovation_extraction",
            "agent_name": "generic_worker",
            "role": "generic",
            "depends_on": ["proposal_background_analysis"],
            "input_refs": [
                "document.full_text",
                "artifacts.stage_outputs.proposal_background_analysis",
                "user.input",
            ],
            "stage_goal": "Extract the supported innovation points, research contents, expected contributions, and differentiating ideas from the proposal material.",
            "review_required": True,
            "review_phase": "generic_review",
        },
        {
            "stage": "technical_route_extraction",
            "agent_name": "generic_worker",
            "role": "generic",
            "depends_on": ["proposal_background_analysis"],
            "input_refs": [
                "document.full_text",
                "artifacts.stage_outputs.proposal_background_analysis",
                "user.input",
            ],
            "stage_goal": "Extract the technical route, research tasks, task dependencies, validation path, milestones, and expected outcomes.",
            "review_required": True,
            "review_phase": "generic_review",
        },
        {
            "stage": "grant_visual_mapping",
            "agent_name": "generic_worker",
            "role": "generic",
            "depends_on": ["innovation_extraction", "technical_route_extraction"],
            "input_refs": [
                "artifacts.stage_outputs.proposal_background_analysis",
                "artifacts.stage_outputs.innovation_extraction",
                "artifacts.stage_outputs.technical_route_extraction",
                "user.input",
            ],
            "stage_goal": "Map the project background, innovations, research tasks, and technical route into a clear grant-review-oriented visual structure.",
            "review_required": True,
            "review_phase": "generic_review",
        },
        {
            "stage": "grant_prompt_summarization",
            "agent_name": "generic_worker",
            "role": "final_prompt",
            "depends_on": ["grant_visual_mapping"],
            "input_refs": [
                "artifacts.stage_outputs.proposal_background_analysis",
                "artifacts.stage_outputs.innovation_extraction",
                "artifacts.stage_outputs.technical_route_extraction",
                "artifacts.stage_outputs.grant_visual_mapping",
                "user.input",
            ],
            "output_ref": "stage_outputs.grant_prompt_summarization",
            "artifact_aliases": ["payload_final"],
            "stage_goal": "Summarize all upstream grant-diagram artifacts into a final English drawing prompt for a clean project-application technical route figure.",
            "review_required": True,
            "review_phase": "final_prompt_review",
        },
    ]


def _canonical_diagram_plan(text: str) -> list[dict[str, Any]]:
    include_image = any(keyword in text.lower() for keyword in ["生图", "出图", "生成图片", "image generation"])
    stages = [
        {
            "stage": "logic_extraction",
            "agent_name": "logic_extractor",
            "role": "logic",
            "depends_on": [],
            "input_refs": ["document.full_text", "user.input"],
            "stage_goal": "Read the source document and extract the method logic structure.",
            "review_required": True,
            "review_phase": "logic_review",
        },
        {
            "stage": "style_extraction",
            "agent_name": "style_extractor",
            "role": "style",
            "depends_on": [],
            "input_refs": [
                "user.primary_discipline",
                "user.conference_name",
                "user.preferences",
                "user.input",
            ],
            "stage_goal": "Extract publication and user preference constraints for the diagram style.",
            "review_required": True,
            "review_phase": "style_review",
        },
        {
            "stage": "visual_mapping",
            "agent_name": "visual_mapper",
            "role": "visual_mapping",
            "depends_on": ["logic_extraction", "style_extraction"],
            "input_refs": [
                "artifacts.stage_outputs.logic_extraction",
                "artifacts.stage_outputs.style_extraction",
                "user.input",
            ],
            "stage_goal": "Map the logic structure and style constraints into a visual layout plan.",
            "review_required": True,
            "review_phase": "visual_review",
        },
        {
            "stage": "summarization",
            "agent_name": "prompt_summarizer",
            "role": "final_prompt",
            "depends_on": ["visual_mapping"],
            "input_refs": [
                "artifacts.stage_outputs.logic_extraction",
                "artifacts.stage_outputs.style_extraction",
                "artifacts.stage_outputs.visual_mapping",
                "user.input",
            ],
            "stage_goal": "Summarize the upstream artifacts into a final English drawing prompt.",
            "review_required": True,
            "review_phase": "final_prompt_review",
        },
    ]
    if include_image:
        stages.append(
            {
                "stage": "image_generation",
                "agent_name": "image_generator",
                "role": "image",
                "depends_on": ["summarization"],
                "input_refs": ["artifacts.payload_final"],
                "stage_goal": "Generate the image after user confirmation.",
                "review_required": False,
                "trigger": "user_confirm",
            }
        )
    return stages


def infer_plan_from_natural_language(
    *,
    skill_description: str = "",
    skill_body: str = "",
) -> list[dict[str, Any]]:
    text = f"{skill_description}\n{skill_body}".strip()
    if not text:
        return []
    if _looks_like_grant_diagram_skill(text):
        return _grant_diagram_plan(text)
    if _looks_like_canonical_diagram_skill(text):
        return _canonical_diagram_plan(text)

    stages: list[dict[str, Any]] = []
    previous_stage_names: list[str] = []
    for unit in _split_natural_language_units(text):
        roles = _roles_mentioned(unit)
        if not roles and re.search(r"提取|抽取|分析|整理|规划|映射|生成|汇总|总结|extract|analy[sz]e|generate|summari[sz]e", unit, re.I):
            roles = ["generic"]
        if not roles:
            continue

        parallel = "并行" in unit or "同时" in unit or "parallel" in unit.lower()
        for role in roles:
            if parallel or (role in {"logic", "style"} and ("并行" in text or "parallel" in text.lower())):
                dependencies: list[str] = []
            elif "前两者" in unit and len(previous_stage_names) >= 2:
                dependencies = previous_stage_names[-2:]
            elif role == "final_prompt":
                dependencies = list(previous_stage_names)
            elif role == "image":
                dependencies = previous_stage_names[-1:] if previous_stage_names else []
            else:
                dependencies = previous_stage_names[-1:] if previous_stage_names else []

            _append_or_merge_stage(
                stages,
                role=role,
                goal=unit,
                dependencies=dependencies,
                previous_stage_names=previous_stage_names,
            )
            stage_name = stages[-1]["stage"]
            if stage_name not in previous_stage_names:
                previous_stage_names.append(stage_name)

    return stages


def compile_skill_plan(
    plan: list[dict[str, Any]],
    *,
    skill_description: str = "",
    skill_body: str = "",
) -> list[dict[str, Any]]:
    """Normalize a skill plan into a runtime-ready task graph.

    Skill authors describe semantics; this compiler fills in internal artifact
    refs, dependencies, and input refs. If a skill has no explicit YAML plan,
    a deterministic natural-language compiler builds an initial plan from the
    prose body.
    """
    if not plan:
        plan = infer_plan_from_natural_language(
            skill_description=skill_description,
            skill_body=skill_body,
        )

    compiled: list[dict[str, Any]] = []
    previous_stage_names: list[str] = []

    for index, raw_stage in enumerate(plan):
        stage = deepcopy(raw_stage)
        stage_name = str(stage.get("stage") or stage.get("agent_name") or f"stage_{index + 1}")
        role = str(stage.get("role") or stage.get("stage_role") or infer_stage_role(stage))
        dependencies = _dependencies(stage, previous_stage_names)

        stage["stage"] = stage_name
        stage["stage_role"] = role
        stage["agent_name"] = str(stage.get("agent_name") or ROLE_AGENT_NAMES.get(role) or stage_name)
        stage["depends_on"] = dependencies
        stage["output_ref"] = str(stage.get("output_ref") or _stage_output_ref(stage_name))
        stage["artifact_aliases"] = list(stage.get("artifact_aliases") or _legacy_aliases(role))
        stage["input_refs"] = list(
            stage.get("input_refs")
            or _default_input_refs(
                role=role,
                dependencies=dependencies,
                previous_stage_names=previous_stage_names,
            )
        )
        stage["stage_goal"] = str(
            stage.get("stage_goal")
            or stage.get("goal")
            or stage.get("description")
            or f"Complete the {stage_name} stage for the selected skill."
        )
        stage["skill_description"] = skill_description
        stage["skill_excerpt"] = skill_body[:2400]

        compiled.append(stage)
        previous_stage_names.append(stage_name)

    return compiled
