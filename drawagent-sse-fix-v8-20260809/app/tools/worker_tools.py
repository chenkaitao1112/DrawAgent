from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from app.core.config import AppConfig
from app.mcp.stdio_client import MCPStdIOClient
from app.tools.contracts import FinalToolArgs, GenericToolArgs, ImageToolArgs, LogicToolArgs, MapperToolArgs, StyleToolArgs
from app.tools.normalizers import (
    normalize_final_artifact,
    normalize_image_artifact,
    normalize_logic_artifact,
    normalize_mapper_artifact,
    normalize_style_artifact,
    normalize_text_artifact,
)

ROOT_DIR = Path(__file__).resolve().parents[2]


def _call_worker_mcp_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    config = AppConfig.load(ROOT_DIR)
    with MCPStdIOClient(
        command=config.review_mcp_command,
        args=config.review_mcp_args,
        cwd=config.root_dir,
        timeout_seconds=config.review_mcp_timeout_seconds,
    ) as client:
        return client.call_tool(name, arguments)


@tool("submit_logic_artifact", args_schema=LogicToolArgs)
def submit_logic_artifact(artifact, summary: str = "") -> dict:
    """Submit the logic extraction artifact through the generic MCP server."""
    payload = {
        "artifact": normalize_logic_artifact(artifact),
        "summary": summary,
    }
    return _call_worker_mcp_tool("submit_logic_artifact", payload)


@tool("submit_style_artifact", args_schema=StyleToolArgs)
def submit_style_artifact(artifact, summary: str = "") -> dict:
    """Submit the style extraction artifact through the generic MCP server."""
    payload = {
        "artifact": normalize_style_artifact(artifact),
        "summary": summary,
    }
    return _call_worker_mcp_tool("submit_style_artifact", payload)


@tool("submit_layout_artifact", args_schema=MapperToolArgs)
def submit_layout_artifact(artifact, summary: str = "") -> dict:
    """Submit the visual mapping artifact through the generic MCP server."""
    payload = {
        "artifact": normalize_mapper_artifact(artifact),
        "summary": summary,
    }
    return _call_worker_mcp_tool("submit_layout_artifact", payload)


@tool("submit_summary_artifact", args_schema=FinalToolArgs)
def submit_summary_artifact(artifact, summary: str = "") -> dict:
    """Submit the final prompt artifact through the generic MCP server."""
    payload = {
        "artifact": normalize_final_artifact(artifact),
        "summary": summary,
    }
    return _call_worker_mcp_tool("submit_summary_artifact", payload)


@tool("submit_generic_artifact", args_schema=GenericToolArgs)
def submit_generic_artifact(artifact, summary: str = "") -> dict:
    """Submit a generic text artifact for a skill-defined virtual stage."""
    payload = {
        "artifact": normalize_text_artifact(artifact),
        "summary": summary,
    }
    return _call_worker_mcp_tool("submit_generic_artifact", payload)


@tool("submit_image_artifact", args_schema=ImageToolArgs)
def submit_image_artifact(artifact, summary: str = "") -> dict:
    """Submit the image generation artifact through the generic MCP server."""
    payload = {
        "artifact": normalize_image_artifact(artifact),
        "summary": summary,
    }
    return _call_worker_mcp_tool("submit_image_artifact", payload)


WORKER_TOOL_MAP = {
    "logic_extraction": [submit_logic_artifact],
    "style_extraction": [submit_style_artifact],
    "visual_mapping": [submit_layout_artifact],
    "summarization": [submit_summary_artifact],
    "image_generation": [submit_image_artifact],
}
