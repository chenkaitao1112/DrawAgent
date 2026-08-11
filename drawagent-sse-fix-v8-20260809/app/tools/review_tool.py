from __future__ import annotations

import queue
import threading
from typing import Any

from app.core.config import AppConfig
from app.mcp.stdio_client import MCPStdIOClient


class ReviewToolClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.pool_size = max(1, int(config.review_mcp_pool_size))
        self._available: queue.LifoQueue[MCPStdIOClient] = queue.LifoQueue()
        self._clients: list[MCPStdIOClient] = []
        self._lock = threading.Lock()
        self._closed = False

    def _new_client(self) -> MCPStdIOClient:
        return MCPStdIOClient(
            command=self.config.review_mcp_command,
            args=self.config.review_mcp_args,
            cwd=self.config.root_dir,
            timeout_seconds=self.config.review_mcp_timeout_seconds,
        ).start()

    def _acquire_client(self) -> MCPStdIOClient:
        if self._closed:
            raise RuntimeError("ReviewToolClient is closed.")
        try:
            client = self._available.get_nowait()
            if client.is_running():
                return client
            self._discard_client(client)
        except queue.Empty:
            pass

        with self._lock:
            if self._closed:
                raise RuntimeError("ReviewToolClient is closed.")
            if len(self._clients) < self.pool_size:
                client = self._new_client()
                self._clients.append(client)
                return client

        client = self._available.get(timeout=self.config.review_mcp_timeout_seconds)
        if client.is_running():
            return client
        self._discard_client(client)
        return self._acquire_client()

    def _release_client(self, client: MCPStdIOClient, *, reusable: bool) -> None:
        if reusable and not self._closed and client.is_running():
            self._available.put(client)
            return
        self._discard_client(client)

    def _discard_client(self, client: MCPStdIOClient) -> None:
        client.close()
        with self._lock:
            self._clients = [item for item in self._clients if item is not client]

    @staticmethod
    def _should_retry_with_fresh_client(exc: BaseException) -> bool:
        if isinstance(exc, TimeoutError):
            return False
        detail = str(exc).lower()
        if "mcp error from tools/call" in detail:
            return False
        return any(
            token in detail
            for token in [
                "process is not running",
                "closed the stdout",
                "broken pipe",
                "invalid mcp message",
            ]
        )

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(2):
            client = self._acquire_client()
            try:
                result = client.call_tool(name, arguments)
            except Exception as exc:
                retry = attempt == 0 and self._should_retry_with_fresh_client(exc)
                self._release_client(client, reusable=not retry and client.is_running())
                if retry:
                    continue
                raise
            self._release_client(client, reusable=True)
            return result
        raise RuntimeError("MCP tool call failed after retry.")

    def close(self) -> None:
        self._closed = True
        with self._lock:
            clients = list(self._clients)
            self._clients = []
        while True:
            try:
                self._available.get_nowait()
            except queue.Empty:
                break
        for client in clients:
            client.close()

    def __enter__(self) -> "ReviewToolClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def review_artifact(
        self,
        *,
        review_phase: str,
        target_goal: str,
        task_type: str,
        task_input: dict[str, Any],
        task_output: dict[str, Any],
        upstream_artifacts: dict[str, Any],
        prior_reviews: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._call_tool(
            "review_artifact",
            {
                "review_phase": review_phase,
                "target_goal": target_goal,
                "task_type": task_type,
                "task_input": task_input,
                "task_output": task_output,
                "upstream_artifacts": upstream_artifacts,
                "prior_reviews": prior_reviews,
            },
        )

    def run_virtual_agent(
        self,
        *,
        agent_type: str,
        agent_name: str,
        prompt_name: str,
        model_role: str,
        output_contract: str,
        stage_goal: str,
        stage_role: str,
        input_refs: list[str],
        allowed_input_refs: list[str],
        resolved_inputs: dict[str, Any],
        input_manifest: dict[str, Any],
        artifact_refs: dict[str, Any],
        artifact_channels: list[str],
        artifact_channel_sources: dict[str, Any],
        dedupe_artifact_inputs: bool,
        stage_outputs: dict[str, Any],
        input_payload: dict[str, Any],
        temperature: float,
        max_tokens: int,
        user_input: str,
        primary_discipline: str,
        conference_name: str,
        user_preferences: str,
        document_context_summary: dict[str, Any],
        document_excerpt: str,
        payload_logic: dict[str, Any],
        payload_style: dict[str, Any],
        payload_mapper: dict[str, Any],
        source_files: list[str],
        revision_context: dict[str, Any],
        revision_mode: bool,
    ) -> dict[str, Any]:
        return self._call_tool(
            "run_virtual_agent",
            {
                "agent_type": agent_type,
                "agent_name": agent_name,
                "prompt_name": prompt_name,
                "model_role": model_role,
                "output_contract": output_contract,
                "stage_goal": stage_goal,
                "stage_role": stage_role,
                "input_refs": input_refs,
                "allowed_input_refs": allowed_input_refs,
                "resolved_inputs": resolved_inputs,
                "input_manifest": input_manifest,
                "artifact_refs": artifact_refs,
                "artifact_channels": artifact_channels,
                "artifact_channel_sources": artifact_channel_sources,
                "dedupe_artifact_inputs": dedupe_artifact_inputs,
                "stage_outputs": stage_outputs,
                "input_payload": input_payload,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "user_input": user_input,
                "primary_discipline": primary_discipline,
                "conference_name": conference_name,
                "user_preferences": user_preferences,
                "document_context_summary": document_context_summary,
                "document_excerpt": document_excerpt,
                "payload_logic": payload_logic,
                "payload_style": payload_style,
                "payload_mapper": payload_mapper,
                "source_files": source_files,
                "revision_context": revision_context,
                "revision_mode": revision_mode,
            },
        )
