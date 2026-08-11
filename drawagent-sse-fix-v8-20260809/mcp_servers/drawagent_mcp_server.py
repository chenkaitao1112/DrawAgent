from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from langchain_core.tools import BaseTool, tool

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.config import AppConfig  # noqa: E402
from app.core.models import ReviewVerdict  # noqa: E402
from app.agents.registry import AgentRegistry  # noqa: E402
from app.llm.openai_client import OpenAICompatibleClient  # noqa: E402
from app.prompts.repository import PromptRepository  # noqa: E402
from app.tools.contracts import (  # noqa: E402
    FinalToolArgs,
    GenericToolArgs,
    ImageToolArgs,
    LogicToolArgs,
    MapperToolArgs,
    ReviewRequestArgs,
    ReviewToolArgs,
    RunVirtualAgentArgs,
    StyleToolArgs,
)
from app.tools.normalizers import (  # noqa: E402
    normalize_final_artifact,
    normalize_image_artifact,
    normalize_logic_artifact,
    normalize_mapper_artifact,
    normalize_style_artifact,
    normalize_text_artifact,
)


@tool("submit_review_verdict", args_schema=ReviewToolArgs)
def submit_review_verdict(verdict, summary: str = "") -> dict:
    """Submit the structured review verdict."""
    return {
        "verdict": verdict.model_dump() if hasattr(verdict, "model_dump") else verdict,
        "summary": summary,
    }


@tool("submit_logic_artifact", args_schema=LogicToolArgs)
def submit_logic_artifact(artifact, summary: str = "") -> dict:
    """Return the validated logic extraction artifact."""
    return {
        "artifact": normalize_logic_artifact(artifact),
        "summary": summary,
    }


@tool("submit_style_artifact", args_schema=StyleToolArgs)
def submit_style_artifact(artifact, summary: str = "") -> dict:
    """Return the validated style extraction artifact."""
    return {
        "artifact": normalize_style_artifact(artifact),
        "summary": summary,
    }


@tool("submit_layout_artifact", args_schema=MapperToolArgs)
def submit_layout_artifact(artifact, summary: str = "") -> dict:
    """Return the validated visual mapping artifact."""
    return {
        "artifact": normalize_mapper_artifact(artifact),
        "summary": summary,
    }


@tool("submit_summary_artifact", args_schema=FinalToolArgs)
def submit_summary_artifact(artifact, summary: str = "") -> dict:
    """Return the validated prompt summary artifact."""
    return {
        "artifact": normalize_final_artifact(artifact),
        "summary": summary,
    }


@tool("submit_generic_artifact", args_schema=GenericToolArgs)
def submit_generic_artifact(artifact, summary: str = "") -> dict:
    """Return a generic validated text artifact for a skill-defined stage."""
    return {
        "artifact": normalize_text_artifact(artifact),
        "summary": summary,
    }


@tool("submit_image_artifact", args_schema=ImageToolArgs)
def submit_image_artifact(artifact, summary: str = "") -> dict:
    """Return the validated image generation artifact."""
    return {
        "artifact": normalize_image_artifact(artifact),
        "summary": summary,
    }


class DrawAgentMCPServer:
    def __init__(self) -> None:
        self.config = AppConfig.load(ROOT_DIR)
        self.prompt_repository = PromptRepository(ROOT_DIR, self.config.prompt_manifest_path)
        self.agent_registry = AgentRegistry.from_root(ROOT_DIR)
        self.role_clients: dict[str, OpenAICompatibleClient] = {}
        self.tools = self._build_tool_registry()
        self.virtual_agent_specs = self._build_virtual_agent_specs()

    def _client_for_role(self, role: str) -> OpenAICompatibleClient:
        cached = self.role_clients.get(role)
        if cached is not None:
            return cached
        role_llm = self.config.llm_role(role)
        client = OpenAICompatibleClient(
            base_url=role_llm.base_url,
            api_key=role_llm.api_key,
            api_mode=role_llm.api_mode,
        )
        self.role_clients[role] = client
        return client

    def _build_tool_registry(self) -> dict[str, dict[str, Any]]:
        return {
            "review_artifact": {
                "description": "Review one virtual worker output and return a structured verdict.",
                "inputSchema": ReviewRequestArgs.model_json_schema(),
                "handler": self._handle_review_artifact,
            },
            "run_virtual_agent": {
                "description": "Run one virtual worker by agent_type. The MCP server injects the matching prompt and output contract.",
                "inputSchema": RunVirtualAgentArgs.model_json_schema(),
                "handler": self._handle_run_virtual_agent,
            },
            "submit_logic_artifact": {
                "description": "Submit the logic extraction artifact.",
                "inputSchema": LogicToolArgs.model_json_schema(),
                "handler": self._handle_submit_logic_artifact,
            },
            "submit_style_artifact": {
                "description": "Submit the style extraction artifact.",
                "inputSchema": StyleToolArgs.model_json_schema(),
                "handler": self._handle_submit_style_artifact,
            },
            "submit_layout_artifact": {
                "description": "Submit the visual mapping artifact.",
                "inputSchema": MapperToolArgs.model_json_schema(),
                "handler": self._handle_submit_layout_artifact,
            },
            "submit_summary_artifact": {
                "description": "Submit the final prompt artifact.",
                "inputSchema": FinalToolArgs.model_json_schema(),
                "handler": self._handle_submit_summary_artifact,
            },
            "submit_generic_artifact": {
                "description": "Submit a generic text artifact for a skill-defined stage.",
                "inputSchema": GenericToolArgs.model_json_schema(),
                "handler": self._handle_submit_generic_artifact,
            },
            "submit_image_artifact": {
                "description": "Submit the image generation artifact.",
                "inputSchema": ImageToolArgs.model_json_schema(),
                "handler": self._handle_submit_image_artifact,
            },
        }

    def _build_virtual_agent_specs(self) -> dict[str, dict[str, Any]]:
        contract_tools = {
            "logic_artifact": submit_logic_artifact,
            "style_artifact": submit_style_artifact,
            "layout_artifact": submit_layout_artifact,
            "final_prompt_artifact": submit_summary_artifact,
            "image_artifact": submit_image_artifact,
            "text_artifact": submit_generic_artifact,
        }
        specs: dict[str, dict[str, Any]] = {}
        for profile in self.agent_registry.describe():
            output_contract = str(profile.get("output_contract") or "text_artifact")
            specs[str(profile["name"])] = {
                "prompt_name": profile.get("prompt_name") or "generic_virtual_agent",
                "tool": contract_tools.get(output_contract, submit_generic_artifact),
                "output_contract": output_contract,
            }
            for alias in profile.get("aliases", []) or []:
                specs[str(alias)] = specs[str(profile["name"])]
        return specs

    def _write(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        sys.stdout.buffer.write(header)
        sys.stdout.buffer.write(body)
        sys.stdout.buffer.flush()

    def _read(self) -> dict[str, Any]:
        headers: dict[str, str] = {}
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                raise EOFError
            if line in {b"\r\n", b"\n"}:
                break
            name, value = line.decode("utf-8").split(":", 1)
            headers[name.strip().lower()] = value.strip()
        length = int(headers.get("content-length", "0"))
        body = sys.stdin.buffer.read(length)
        return json.loads(body.decode("utf-8"))

    def _result(self, request_id: int | str, result: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _error(self, request_id: int | str | None, message: str) -> None:
        self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": message},
            }
        )

    @staticmethod
    def _tool_artifact_payload(tool_result: dict[str, Any]) -> dict[str, Any]:
        return {
            "artifact": tool_result.get("artifact", {}),
            "summary": tool_result.get("summary", ""),
        }

    def _handle_submit_logic_artifact(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._tool_artifact_payload(submit_logic_artifact.invoke(arguments))

    def _handle_submit_style_artifact(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._tool_artifact_payload(submit_style_artifact.invoke(arguments))

    def _handle_submit_layout_artifact(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._tool_artifact_payload(submit_layout_artifact.invoke(arguments))

    def _handle_submit_summary_artifact(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._tool_artifact_payload(submit_summary_artifact.invoke(arguments))

    def _handle_submit_generic_artifact(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._tool_artifact_payload(submit_generic_artifact.invoke(arguments))

    def _handle_submit_image_artifact(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._tool_artifact_payload(submit_image_artifact.invoke(arguments))

    @staticmethod
    def _cache_friendly_virtual_agent_payload(validated: RunVirtualAgentArgs) -> dict[str, Any]:
        raw = validated.model_dump()
        ordered: dict[str, Any] = {}
        has_document = bool(str(raw.get("document_excerpt") or ""))
        if has_document:
            raw.pop("document_context_summary", None)

        leading_keys = [
            "document_excerpt",
            "payload_logic",
            "payload_style",
            "payload_mapper",
            "stage_outputs",
            "artifact_refs",
            "input_refs",
            "input_manifest",
            "source_files",
            "artifact_channels",
            "artifact_channel_sources",
            "resolved_inputs",
            "input_payload",
            "user_input",
            "document_context_summary",
            "primary_discipline",
            "conference_name",
            "user_preferences",
            "stage_goal",
            "stage_role",
            "agent_type",
            "agent_name",
            "prompt_name",
            "model_role",
            "output_contract",
            "allowed_input_refs",
            "dedupe_artifact_inputs",
            "temperature",
            "max_tokens",
        ]
        trailing_keys = [
            "session_id",
            "revision_mode",
            "revision_context",
        ]

        for key in leading_keys:
            if key in raw:
                ordered[key] = raw.pop(key)
        for key in list(raw.keys()):
            if key not in trailing_keys:
                ordered[key] = raw.pop(key)
        for key in trailing_keys:
            if key in raw:
                ordered[key] = raw.pop(key)
        return ordered

    def _handle_run_virtual_agent(self, arguments: dict[str, Any]) -> dict[str, Any]:
        validated = RunVirtualAgentArgs(**arguments)
        profile = self.agent_registry.get(validated.agent_name or validated.agent_type, role=validated.stage_role or "generic")
        prompt_name = validated.prompt_name or profile.prompt_name
        model_role = validated.model_role or profile.model_role
        output_contract = validated.output_contract or profile.output_contract
        contract_tools = {
            "logic_artifact": submit_logic_artifact,
            "style_artifact": submit_style_artifact,
            "layout_artifact": submit_layout_artifact,
            "final_prompt_artifact": submit_summary_artifact,
            "image_artifact": submit_image_artifact,
            "text_artifact": submit_generic_artifact,
        }
        spec = self.virtual_agent_specs.get(validated.agent_type, {})
        tool_def = contract_tools.get(output_contract) or spec.get("tool") or submit_generic_artifact
        temperature = float(validated.temperature if validated.temperature is not None else profile.temperature)
        max_tokens = int(validated.max_tokens if validated.max_tokens is not None else profile.max_tokens)

        prompt_context = {
            "primary_discipline": validated.primary_discipline,
            "conference_name": validated.conference_name,
            "user_preferences": validated.user_preferences,
            "text_content": validated.document_excerpt or validated.user_input,
            "agent_type": validated.agent_type,
            "agent_name": validated.agent_name or profile.name,
            "stage_goal": validated.stage_goal,
            "stage_role": validated.stage_role,
            "input_refs": json.dumps(validated.input_refs, ensure_ascii=False, indent=2),
            "allowed_input_refs": json.dumps(validated.allowed_input_refs or profile.allowed_input_refs, ensure_ascii=False, indent=2),
            "resolved_inputs": json.dumps(validated.resolved_inputs, ensure_ascii=False, indent=2),
            "input_manifest": json.dumps(validated.input_manifest, ensure_ascii=False, indent=2),
            "artifact_refs": json.dumps(validated.artifact_refs, ensure_ascii=False, indent=2),
            "artifact_channels": json.dumps(validated.artifact_channels, ensure_ascii=False, indent=2),
            "artifact_channel_sources": json.dumps(validated.artifact_channel_sources, ensure_ascii=False, indent=2),
            "dedupe_artifact_inputs": json.dumps(validated.dedupe_artifact_inputs, ensure_ascii=False),
            "stage_outputs": json.dumps(validated.stage_outputs, ensure_ascii=False, indent=2),
        }
        prompt = self.prompt_repository.render(prompt_name, prompt_context)
        role_llm = self.config.llm_role(model_role)
        result = self._client_for_role(model_role).run_tool_call(
            model=role_llm.model,
            system_prompt=prompt,
            user_payload=self._cache_friendly_virtual_agent_payload(validated),
            tools=[tool_def],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        tool_result = result["tool_result"]
        artifact = tool_result.get("artifact", {})
        return {
            "agent_type": validated.agent_type,
            "artifact": artifact,
            "summary": tool_result.get("summary", ""),
            "selection_source": "mcp_virtual_agent",
            "model_name": result.get("model") or role_llm.model,
            "request_api_mode": result.get("request_api_mode") or role_llm.api_mode,
            "prompt_name": prompt_name,
            "agent_name": profile.name,
            "model_role": model_role,
            "output_contract": output_contract,
        }

    def _handle_review_artifact(self, arguments: dict[str, Any]) -> dict[str, Any]:
        validated = ReviewRequestArgs(**arguments)
        prompt = self.prompt_repository.render("reviewer", {})

        def deterministic_fallback() -> dict[str, Any]:
            task_output = validated.task_output or {}
            output_value = task_output.get("value", "") if isinstance(task_output, dict) else ""
            issues: list[str] = []
            retry_targets = [validated.task_type]

            if not str(output_value or "").strip():
                issues.append(f"{validated.task_type} artifact text is empty.")

            if validated.task_type == "prompt_revision":
                current_prompt = str(validated.task_input.get("current_prompt") or "").strip()
                revised_prompt = str(output_value or "").strip()
                if revised_prompt == current_prompt:
                    issues.append("The revised prompt is unchanged from the current prompt.")
                if "user revision requirement" in revised_prompt.lower():
                    issues.append("The revised prompt exposes an internal revision instruction block.")

            if issues:
                return {
                    "approved": False,
                    "signal": "negative",
                    "blocking": True,
                    "retry_targets": retry_targets,
                    "issues": issues,
                    "recommendations": ["Fill the required fields and keep the artifact aligned with upstream inputs."],
                    "notes": "Deterministic fallback review rejected the artifact due to missing required fields.",
                }

            return {
                "approved": True,
                "signal": "positive",
                "blocking": False,
                "retry_targets": [],
                "issues": [],
                "recommendations": [],
                "notes": "Deterministic fallback review approved the artifact because the required fields are present.",
            }

        try:
            review_llm = self.config.llm_role("reviewer")
            result = self._client_for_role("reviewer").run_tool_call(
                model=review_llm.model,
                system_prompt=prompt,
                user_payload=validated.model_dump(),
                tools=[submit_review_verdict],
                temperature=0.1,
                max_tokens=1400,
            )
            verdict = result["tool_result"]["verdict"]
        except Exception as exc:
            verdict = deterministic_fallback()
            verdict["notes"] = f"{verdict['notes']} Review model runtime error: {exc}"
        return ReviewVerdict(**verdict).model_dump()

    def _get_tool_handler(self, name: str) -> Callable[[dict[str, Any]], dict[str, Any]] | None:
        tool_def = self.tools.get(name)
        if tool_def is None:
            return None
        return tool_def["handler"]

    def serve_forever(self) -> None:
        while True:
            try:
                message = self._read()
            except EOFError:
                return

            request_id = message.get("id")
            method = message.get("method")
            params = message.get("params") or {}

            if method == "initialize":
                self._result(
                    request_id,
                    {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "drawagent-generic-mcp", "version": "0.3.0"},
                    },
                )
            elif method == "notifications/initialized":
                continue
            elif method == "tools/list":
                self._result(
                    request_id,
                    {
                        "tools": [
                            {
                                "name": name,
                                "description": definition["description"],
                                "inputSchema": definition["inputSchema"],
                            }
                            for name, definition in self.tools.items()
                        ]
                    },
                )
            elif method == "tools/call":
                tool_name = str(params.get("name") or "")
                handler = self._get_tool_handler(tool_name)
                if handler is None:
                    self._error(request_id, f"Unknown tool: {tool_name}")
                    continue
                try:
                    structured = handler(dict(params.get("arguments") or {}))
                except Exception as exc:
                    self._error(request_id, str(exc))
                    continue
                self._result(
                    request_id,
                    {
                        "content": [{"type": "text", "text": json.dumps(structured, ensure_ascii=False)}],
                        "structuredContent": structured,
                        "isError": False,
                    },
                )
            else:
                self._error(request_id, f"Unsupported method: {method}")


def main() -> None:
    DrawAgentMCPServer().serve_forever()


if __name__ == "__main__":
    main()
