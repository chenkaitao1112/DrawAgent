from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


@dataclass(frozen=True)
class LLMRoleConfig:
    role: str
    model: str
    base_url: str
    api_key: str
    api_mode: str


@dataclass(frozen=True)
class AppConfig:
    root_dir: Path
    raw: dict[str, Any]

    @classmethod
    def load(cls, root_dir: Path) -> "AppConfig":
        load_dotenv(root_dir / ".env", override=True)
        return cls(root_dir=root_dir, raw=load_yaml(root_dir / "config" / "settings.yaml"))

    def env(self, name: str, default: str = "") -> str:
        return os.getenv(name, default).strip()

    @staticmethod
    def _normalize_api_mode(raw: str) -> str:
        value = (raw or "").strip().lower()
        if value in {"chat_completions", "responses", "gemini_generate_content"}:
            return value
        return "chat_completions"

    @staticmethod
    def _role_env_prefix(role: str) -> str:
        return re.sub(r"[^A-Z0-9]+", "_", role.upper()).strip("_")

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for item in values:
            if not item or item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return ordered

    def _resolve_env_chain(self, candidates: list[str], default: str = "") -> str:
        for name in self._unique(candidates):
            value = os.getenv(name)
            if value is not None and value.strip():
                return value.strip()
        return default.strip()

    def llm_role(self, role: str) -> LLMRoleConfig:
        prefix = self._role_env_prefix(role)
        worker_roles = {"logic_extraction", "style_extraction", "visual_mapping", "summarization", "prompt_revision"}
        reviewer_aliases = {"reviewer", "review"}

        model_envs = [f"{prefix}_MODEL"]
        base_url_envs = [f"{prefix}_BASE_URL"]
        api_key_envs = [f"{prefix}_API_KEY"]
        api_mode_envs = [f"{prefix}_API_MODE"]

        if role in worker_roles:
            model_envs.append("WORKER_MODEL")
            base_url_envs.append("WORKER_BASE_URL")
            api_key_envs.append("WORKER_API_KEY")
            api_mode_envs.append("WORKER_API_MODE")
        elif role == "controller":
            model_envs.append("CONTROLLER_MODEL")
            base_url_envs.append("CONTROLLER_BASE_URL")
            api_key_envs.append("CONTROLLER_API_KEY")
            api_mode_envs.append("CONTROLLER_API_MODE")
        elif role in reviewer_aliases:
            model_envs.extend(["REVIEWER_MODEL", "REVIEW_MODEL"])
            base_url_envs.extend(["REVIEWER_BASE_URL", "REVIEW_BASE_URL"])
            api_key_envs.extend(["REVIEWER_API_KEY", "REVIEW_API_KEY"])
            api_mode_envs.extend(["REVIEWER_API_MODE", "REVIEW_API_MODE"])
        else:
            model_envs.append("WORKER_MODEL")
            base_url_envs.append("WORKER_BASE_URL")
            api_key_envs.append("WORKER_API_KEY")
            api_mode_envs.append("WORKER_API_MODE")

        model = self._resolve_env_chain(model_envs + ["LLM_MODEL"], "gpt-4o-mini")
        base_url = self._resolve_env_chain(base_url_envs + ["LLM_BASE_URL"], "")
        api_key = self._resolve_env_chain(api_key_envs + ["LLM_API_KEY"], "")
        api_mode = self._normalize_api_mode(
            self._resolve_env_chain(api_mode_envs + ["LLM_API_MODE"], "chat_completions")
        )
        return LLMRoleConfig(
            role=role,
            model=model,
            base_url=base_url,
            api_key=api_key,
            api_mode=api_mode,
        )

    @property
    def worker_default_llm(self) -> LLMRoleConfig:
        return LLMRoleConfig(
            role="worker_default",
            model=self._resolve_env_chain(["WORKER_MODEL", "LLM_MODEL"], "gpt-4o-mini"),
            base_url=self._resolve_env_chain(["WORKER_BASE_URL", "LLM_BASE_URL"], ""),
            api_key=self._resolve_env_chain(["WORKER_API_KEY", "LLM_API_KEY"], ""),
            api_mode=self._normalize_api_mode(
                self._resolve_env_chain(["WORKER_API_MODE", "LLM_API_MODE"], "chat_completions")
            ),
        )

    def runtime_dir(self, env_name: str, default_relative: str) -> Path:
        raw = self.env(env_name, default_relative)
        path = Path(raw)
        if not path.is_absolute():
            path = self.root_dir / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def prompt_manifest_path(self) -> Path:
        return self.root_dir / "config" / "prompt_manifest.yaml"

    @property
    def agent_registry_path(self) -> Path:
        return self.root_dir / "config" / "agent_registry.yaml"

    @property
    def controller_model(self) -> str:
        return self.llm_role("controller").model

    @property
    def worker_model(self) -> str:
        return self.worker_default_llm.model

    @property
    def review_model(self) -> str:
        return self.llm_role("reviewer").model

    @property
    def llm_base_url(self) -> str:
        return self.llm_role("controller").base_url

    @property
    def llm_api_key(self) -> str:
        return self.llm_role("controller").api_key

    @property
    def llm_api_mode(self) -> str:
        return self.llm_role("controller").api_mode

    @property
    def image_base_url(self) -> str:
        env_name = str(self.raw["image"]["base_url_env"])
        return self.env(env_name)

    @property
    def image_api_key(self) -> str:
        env_name = str(self.raw["image"]["api_key_env"])
        return self.env(env_name)

    @property
    def image_api_mode(self) -> str:
        env_name = str(self.raw["image"].get("api_mode_env", "IMAGE_API_MODE"))
        raw = self.env(env_name, "openai_images").lower()
        if raw not in {"openai_images", "gemini_generate_content"}:
            return "openai_images"
        return raw

    @property
    def image_model(self) -> str:
        env_name = str(self.raw["image"]["model_env"])
        return self.env(env_name, "gpt-image-1")

    @property
    def max_review_rounds(self) -> int:
        return int(self.raw.get("runtime", {}).get("max_review_rounds", 3))

    @property
    def max_concurrent_virtual_tasks(self) -> int:
        raw = self.env(
            "MAX_CONCURRENT_VIRTUAL_TASKS",
            str(self.raw.get("runtime", {}).get("max_concurrent_virtual_tasks", 2)),
        )
        try:
            return max(1, int(raw))
        except ValueError:
            return 2

    @property
    def max_review_failure_policy(self) -> str:
        raw = self.env(
            "MAX_REVIEW_FAILURE_POLICY",
            str(self.raw.get("runtime", {}).get("max_review_failure_policy", "release_with_warning")),
        ).lower()
        if raw in {"fail", "hard_fail", "release_with_warning"}:
            return raw
        return "release_with_warning"

    @property
    def default_skill(self) -> str:
        return str(self.raw.get("skills", {}).get("default_skill", "scientific_diagram"))

    @property
    def upload_dir(self) -> Path:
        return self.runtime_dir("UPLOAD_STORE_DIR", "./runtime/uploads")

    @property
    def output_dir(self) -> Path:
        return self.runtime_dir("OUTPUT_STORE_DIR", "./runtime/outputs")

    @property
    def artifact_dir(self) -> Path:
        return self.runtime_dir("ARTIFACT_STORE_DIR", "./runtime/artifacts")

    @property
    def review_mcp_command(self) -> str:
        configured = self.env(str(self.raw["mcp"]["review"]["command_env"]))
        if configured:
            return configured
        return sys.executable

    @property
    def review_mcp_args(self) -> list[str]:
        configured = self.env(str(self.raw["mcp"]["review"]["args_env"]))
        if configured:
            return [item for item in configured.split(" ") if item]
        return ["-m", str(self.raw["mcp"]["review"]["default_module"])]

    @property
    def review_mcp_timeout_seconds(self) -> float:
        env_name = str(self.raw["mcp"]["review"]["timeout_seconds_env"])
        return float(self.env(env_name, "120") or "120")

    @property
    def review_mcp_pool_size(self) -> int:
        raw = self.env("REVIEW_MCP_POOL_SIZE", str(self.max_concurrent_virtual_tasks))
        try:
            return max(1, int(raw))
        except ValueError:
            return max(1, self.max_concurrent_virtual_tasks)
