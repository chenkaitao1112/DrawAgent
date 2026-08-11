from __future__ import annotations

from typing import Any


def validate_skill_plan(plan: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    stage_names: list[str] = [str(stage.get("stage") or "") for stage in plan]
    clean_stage_names = [name for name in stage_names if name]

    if not clean_stage_names:
        errors.append("skill plan has no executable stages")

    seen: set[str] = set()
    duplicates: set[str] = set()
    for name in clean_stage_names:
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    for name in sorted(duplicates):
        errors.append(f"duplicate stage: {name}")

    stage_set = set(clean_stage_names)
    dependencies: dict[str, list[str]] = {}
    output_refs: dict[str, str] = {}
    for index, stage in enumerate(plan):
        stage_name = str(stage.get("stage") or "")
        if not stage_name:
            errors.append(f"stage at index {index} is missing stage name")
            continue

        deps = [str(item) for item in stage.get("depends_on", []) or []]
        dependencies[stage_name] = deps
        for dep in deps:
            if dep == stage_name:
                errors.append(f"stage {stage_name} depends on itself")
            elif dep not in stage_set:
                errors.append(f"stage {stage_name} depends on unknown stage {dep}")

        output_ref = str(stage.get("output_ref") or "")
        if output_ref:
            if output_ref in output_refs:
                errors.append(
                    f"stage {stage_name} reuses output_ref {output_ref} already produced by {output_refs[output_ref]}"
                )
            output_refs[output_ref] = stage_name

        for ref in stage.get("input_refs", []) or []:
            ref = str(ref)
            if ref.startswith("artifacts.stage_outputs."):
                upstream = ref.removeprefix("artifacts.stage_outputs.")
                if upstream not in stage_set:
                    errors.append(f"stage {stage_name} reads unknown stage output {upstream}")
                elif upstream not in deps:
                    warnings.append(
                        f"stage {stage_name} reads {upstream} without declaring it in depends_on"
                    )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(stage_name: str, path: list[str]) -> None:
        if stage_name in visited:
            return
        if stage_name in visiting:
            cycle_start = path.index(stage_name) if stage_name in path else 0
            cycle = " -> ".join(path[cycle_start:] + [stage_name])
            errors.append(f"dependency cycle detected: {cycle}")
            return
        visiting.add(stage_name)
        for dep in dependencies.get(stage_name, []):
            if dep in stage_set:
                visit(dep, path + [dep])
        visiting.remove(stage_name)
        visited.add(stage_name)

    for stage_name in clean_stage_names:
        visit(stage_name, [stage_name])

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
    }
