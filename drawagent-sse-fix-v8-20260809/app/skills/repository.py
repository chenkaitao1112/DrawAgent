from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.agents.registry import AgentRegistry
from app.skills.compiler import compile_skill_plan


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    plan: list[dict[str, Any]]
    body: str
    file_path: Path

    def summary(self) -> dict[str, Any]:
        plan_preview = [
            {
                "stage": str(stage.get("stage") or ""),
                "depends_on": list(stage.get("depends_on") or []),
                "input_refs": list(stage.get("input_refs") or []),
                "stage_goal": str(stage.get("stage_goal") or "")[:240],
            }
            for stage in self.plan[:8]
        ]
        return {
            "name": self.name,
            "description": self.description,
            "body_excerpt": self.body[:1600],
            "stage_count": len(self.plan),
            "plan_preview": plan_preview,
            "file_path": str(self.file_path),
        }


class SkillRepository:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.skills_dir = root_dir / "skills"
        self.agent_registry = AgentRegistry.from_root(root_dir)
        self._skills = self._load()

    def _load(self) -> dict[str, SkillDefinition]:
        skills: dict[str, SkillDefinition] = {}
        for path in sorted(self.skills_dir.glob("*/SKILL.md")):
            raw = path.read_text(encoding="utf-8")
            if raw.startswith("---"):
                _, frontmatter, body = raw.split("---", 2)
                data = yaml.safe_load(frontmatter) or {}
            else:
                data = {}
                body = raw
            skill_name = str(data.get("name") or path.parent.name)
            description = str(data.get("description") or "")
            plan = compile_skill_plan(
                list((data.get("plan") or {}).get("stages", [])),
                skill_description=description,
                skill_body=body.strip(),
            )
            plan = [self.agent_registry.enrich_stage(stage) for stage in plan]
            skills[skill_name] = SkillDefinition(
                name=skill_name,
                description=description,
                plan=plan,
                body=body.strip(),
                file_path=path,
            )
        return skills

    def get(self, skill_name: str) -> SkillDefinition:
        return self._skills[skill_name]

    def list(self) -> list[SkillDefinition]:
        return list(self._skills.values())

    def describe(self) -> list[dict[str, Any]]:
        return [item.summary() for item in self.list()]
