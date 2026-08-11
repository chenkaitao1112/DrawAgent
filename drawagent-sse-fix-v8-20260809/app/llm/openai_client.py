from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import quote

import httpx
from langchain_core.tools import BaseTool

from app.core.logging import debug_event


ApiMode = Literal["chat_completions", "responses", "gemini_generate_content"]
ImageApiMode = Literal["openai_images", "gemini_generate_content"]


@dataclass(frozen=True)
class ToolCallRequest:
    model: str
    system_prompt: str
    user_payload: dict[str, Any]
    tools: list[BaseTool]
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class ImageGenerationRequest:
    model: str
    prompt: str
    size: str
    image_attempt_id: str


def _normalize_responses_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize_responses_schema(item) for item in value]

    if not isinstance(value, dict):
        return value

    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"title", "default", "examples"}:
            continue
        cleaned[key] = _normalize_responses_schema(item)

    if "$defs" in cleaned and isinstance(cleaned["$defs"], dict):
        cleaned["$defs"] = {
            key: _normalize_responses_schema(item)
            for key, item in cleaned["$defs"].items()
        }

    if "definitions" in cleaned and isinstance(cleaned["definitions"], dict):
        cleaned["definitions"] = {
            key: _normalize_responses_schema(item)
            for key, item in cleaned["definitions"].items()
        }

    schema_type = cleaned.get("type")
    if schema_type == "object" or "properties" in cleaned:
        cleaned.setdefault("type", "object")
        cleaned.setdefault("properties", {})
        cleaned["additionalProperties"] = False

    if "properties" in cleaned and isinstance(cleaned["properties"], dict):
        cleaned["properties"] = {
            key: _normalize_responses_schema(item)
            for key, item in cleaned["properties"].items()
        }
        cleaned["required"] = list(cleaned["properties"].keys())

    if "items" in cleaned:
        cleaned["items"] = _normalize_responses_schema(cleaned["items"])

    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        if key in cleaned and isinstance(cleaned[key], list):
            cleaned[key] = [_normalize_responses_schema(item) for item in cleaned[key]]

    return cleaned


def _resolve_local_refs(value: Any, defs: dict[str, Any]) -> Any:
    if isinstance(value, list):
        return [_resolve_local_refs(item, defs) for item in value]

    if not isinstance(value, dict):
        return value

    if "$ref" in value:
        ref = str(value.get("$ref") or "")
        if ref.startswith("#/$defs/"):
            key = ref.split("/")[-1]
            target = defs.get(key, {})
            return _resolve_local_refs(target, defs)
        return {}

    return {
        key: _resolve_local_refs(item, defs)
        for key, item in value.items()
    }


def _collapse_union_for_gemini(value: Any) -> Any:
    if isinstance(value, list):
        return [_collapse_union_for_gemini(item) for item in value]

    if not isinstance(value, dict):
        return value

    if "anyOf" in value and isinstance(value["anyOf"], list):
        branches = [_collapse_union_for_gemini(item) for item in value["anyOf"]]
        object_branches = [item for item in branches if isinstance(item, dict)]
        non_envelope = []
        for item in object_branches:
            props = item.get("properties", {}) if isinstance(item.get("properties"), dict) else {}
            prop_keys = set(props.keys())
            if prop_keys != {"key", "value"}:
                non_envelope.append(item)
        candidates = non_envelope or object_branches or branches
        if candidates:
            candidates = sorted(
                candidates,
                key=lambda item: len((item.get("properties") or {}) if isinstance(item, dict) else {}),
                reverse=True,
            )
            return candidates[0]

    return {
        key: _collapse_union_for_gemini(item)
        for key, item in value.items()
        if key != "anyOf"
    }


def _normalize_gemini_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize_gemini_schema(item) for item in value]

    if not isinstance(value, dict):
        return value

    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if key == "properties" and isinstance(item, dict):
            cleaned["properties"] = item
            continue
        if key in {"$defs", "definitions", "title", "default", "examples", "additionalProperties"}:
            continue
        if key == "const":
            cleaned["enum"] = [item]
            continue
        cleaned[key] = _normalize_gemini_schema(item)

    if "properties" in cleaned and isinstance(cleaned["properties"], dict):
        cleaned["type"] = "object"
        cleaned["properties"] = {
            key: _normalize_gemini_schema(item)
            for key, item in cleaned["properties"].items()
        }

    if "items" in cleaned:
        cleaned["items"] = _normalize_gemini_schema(cleaned["items"])

    if "required" in cleaned and isinstance(cleaned["required"], list):
        cleaned["required"] = [str(item) for item in cleaned["required"]]

    return cleaned


def tool_to_chat_completions_schema(tool: BaseTool) -> dict[str, Any]:
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is None:
        parameters = {
            "type": "object",
            "properties": {},
        }
    else:
        parameters = args_schema.model_json_schema()
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": parameters,
        },
    }


def tool_to_responses_schema(tool: BaseTool) -> dict[str, Any]:
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is None:
        parameters = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    else:
        parameters = _normalize_responses_schema(args_schema.model_json_schema())
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description or "",
        "parameters": parameters,
        "strict": True,
    }


def tool_to_gemini_function_declaration(tool: BaseTool) -> dict[str, Any]:
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is None:
        parameters = {
            "type": "object",
            "properties": {},
        }
    else:
        raw_schema = args_schema.model_json_schema()
        defs = {}
        if isinstance(raw_schema.get("$defs"), dict):
            defs.update(raw_schema["$defs"])
        if isinstance(raw_schema.get("definitions"), dict):
            defs.update(raw_schema["definitions"])
        resolved = _resolve_local_refs(raw_schema, defs)
        collapsed = _collapse_union_for_gemini(resolved)
        parameters = _normalize_gemini_schema(collapsed)
    return {
        "name": tool.name,
        "description": tool.description or "",
        "parameters": parameters,
    }


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 180.0,
        api_mode: ApiMode = "chat_completions",
        image_api_mode: ImageApiMode = "openai_images",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.api_mode: ApiMode = (
            api_mode
            if api_mode in {"chat_completions", "responses", "gemini_generate_content"}
            else "chat_completions"
        )
        self.image_api_mode: ImageApiMode = (
            image_api_mode
            if image_api_mode in {"openai_images", "gemini_generate_content"}
            else "openai_images"
        )

    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    @staticmethod
    def _json_user_text(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def _default_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _gemini_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }

    def _make_tool_call_request(
        self,
        *,
        model: str,
        system_prompt: str,
        user_payload: dict[str, Any],
        tools: list[BaseTool],
        temperature: float,
        max_tokens: int,
    ) -> ToolCallRequest:
        return ToolCallRequest(
            model=model,
            system_prompt=system_prompt,
            user_payload=user_payload,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _make_image_request(
        self,
        *,
        model: str,
        prompt: str,
        size: str,
        image_attempt_id: str,
    ) -> ImageGenerationRequest:
        return ImageGenerationRequest(
            model=model,
            prompt=prompt,
            size=size,
            image_attempt_id=image_attempt_id,
        )

    def _tool_call_path_for_mode(self, api_mode: ApiMode) -> str:
        if api_mode == "responses":
            return "/responses"
        if api_mode == "gemini_generate_content":
            return "/v1beta/models/{model}:generateContent"
        return "/chat/completions"

    def _image_path_for_mode(self, image_api_mode: ImageApiMode) -> str:
        if image_api_mode == "gemini_generate_content":
            return "/v1beta/models/{model}:generateContent"
        return "/images/generations"

    def _tool_call_payload_for_mode(self, request: ToolCallRequest, api_mode: ApiMode) -> dict[str, Any]:
        if api_mode == "chat_completions":
            openai_tools = [tool_to_chat_completions_schema(item) for item in request.tools]
            return {
                "model": request.model,
                "messages": [
                    {"role": "system", "content": request.system_prompt},
                    {
                        "role": "user",
                        "content": self._json_user_text(request.user_payload),
                    },
                ],
                "tools": openai_tools,
                "tool_choice": "required",
                "parallel_tool_calls": False,
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
            }

        if api_mode == "responses":
            response_tools = [tool_to_responses_schema(item) for item in request.tools]
            return {
                "model": request.model,
                "instructions": request.system_prompt,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": self._json_user_text(request.user_payload),
                            }
                        ],
                    }
                ],
                "tools": response_tools,
                "tool_choice": "required",
                "parallel_tool_calls": False,
                "temperature": request.temperature,
                "max_output_tokens": request.max_tokens,
            }

        gemini_tools = [
            {
                "functionDeclarations": [
                    tool_to_gemini_function_declaration(item) for item in request.tools
                ]
            }
        ]
        return {
            "systemInstruction": {
                "parts": [{"text": request.system_prompt}],
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": self._json_user_text(request.user_payload),
                        }
                    ],
                }
            ],
            "tools": gemini_tools,
            "toolConfig": {
                "functionCallingConfig": {
                    "mode": "ANY",
                    "allowedFunctionNames": [item.name for item in request.tools],
                }
            },
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
            },
        }

    def _image_payload_for_mode(self, request: ImageGenerationRequest, image_api_mode: ImageApiMode) -> dict[str, Any]:
        if image_api_mode == "openai_images":
            return {
                "model": request.model,
                "prompt": request.prompt,
                "size": request.size,
            }

        return {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                f"{request.prompt}\n\n"
                                f"Target size: {request.size}. Return an image output."
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
            },
        }

    def _tool_call_target(self, request: ToolCallRequest, api_mode: ApiMode) -> tuple[str, dict[str, str], dict[str, Any]]:
        payload = self._tool_call_payload_for_mode(request, api_mode)
        if api_mode == "gemini_generate_content":
            base = self._normalized_gemini_base()
            url = f"{base}/v1beta/models/{quote(request.model, safe='')}:generateContent"
            return url, self._gemini_headers(), payload
        path = self._tool_call_path_for_mode(api_mode)
        return f"{self.base_url}{path}", self._default_headers(), payload

    def _image_target(self, request: ImageGenerationRequest, image_api_mode: ImageApiMode) -> tuple[str, dict[str, str], dict[str, Any]]:
        payload = self._image_payload_for_mode(request, image_api_mode)
        if image_api_mode == "gemini_generate_content":
            base = self._normalized_gemini_base()
            url = f"{base}/v1beta/models/{quote(request.model, safe='')}:generateContent"
            return url, self._gemini_headers(), payload
        path = self._image_path_for_mode(image_api_mode)
        return f"{self.base_url}{path}", self._default_headers(), payload

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        return self._post_json_absolute(url, payload, headers)

    @staticmethod
    def _parse_sse_response(text: str) -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        data_lines: list[str] = []

        def flush_event() -> None:
            if not data_lines:
                return
            raw = "\n".join(data_lines).strip()
            data_lines.clear()
            if not raw or raw == "[DONE]":
                return
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                events.append(parsed)

        for line in text.splitlines():
            if not line.strip():
                flush_event()
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        flush_event()

        if not events:
            raise json.JSONDecodeError("SSE response contained no JSON data events", text, 0)

        response_data: dict[str, Any] = {}
        output_items: dict[int, dict[str, Any]] = {}
        for event in events:
            event_type = str(event.get("type") or "")
            response = event.get("response")
            if isinstance(response, dict):
                response_data = dict(response)

            if event_type in {"response.output_item.added", "response.output_item.done"}:
                item = event.get("item")
                if isinstance(item, dict):
                    output_index = int(event.get("output_index", len(output_items)) or 0)
                    output_items[output_index] = dict(item)
                continue

            if event_type == "response.function_call_arguments.done":
                output_index = int(event.get("output_index", 0) or 0)
                item = output_items.setdefault(output_index, {"type": "function_call"})
                item["arguments"] = str(event.get("arguments") or "")

        if output_items and not response_data.get("output"):
            response_data["output"] = [output_items[index] for index in sorted(output_items)]
        if not response_data:
            response_data = {
                "object": "response",
                "status": "completed",
                "output": [output_items[index] for index in sorted(output_items)],
            }
        if not response_data.get("output"):
            raise json.JSONDecodeError("SSE response contained no completed output items", text, 0)
        return response_data

    @classmethod
    def _decode_json_or_sse_response(cls, response: httpx.Response) -> dict[str, Any]:
        content_type = str(response.headers.get("content-type") or "").lower()
        body_text = response.text
        if "text/event-stream" in content_type or body_text.lstrip().startswith("event:"):
            return cls._parse_sse_response(body_text)
        parsed = response.json()
        if not isinstance(parsed, dict):
            raise json.JSONDecodeError("Provider response was not a JSON object", body_text, 0)
        return parsed

    def _post_json_absolute(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        request_headers = headers or {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        max_attempts = 4
        for attempt in range(max_attempts):
            response: httpx.Response | None = None
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(url, headers=request_headers, json=payload)
                response.raise_for_status()
                return self._decode_json_or_sse_response(response)
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                last_error = exc
                if isinstance(exc, httpx.HTTPStatusError):
                    response = exc.response
                status_code = response.status_code if response is not None else 0
                retryable = (
                    status_code in {408, 409, 425, 429}
                    or status_code >= 500
                    or isinstance(exc, httpx.TransportError)
                    or isinstance(exc, json.JSONDecodeError)
                )
                if not retryable or attempt == max_attempts - 1:
                    content_type = str(response.headers.get("content-type") or "") if response is not None else ""
                    body_bytes = len(response.content) if response is not None else 0
                    body_text = (
                        response.text[:800]
                        if response is not None and "text/event-stream" not in content_type.lower()
                        else ""
                    )
                    provider_request_id = self._extract_provider_request_id(
                        response=response,
                        body_text=body_text,
                    )
                    raise RuntimeError(
                        f"LLM request failed after retries: status={status_code or 'unknown'} "
                        f"request_id={provider_request_id or '-'} error={exc} "
                        f"content_type={content_type or '-'} body_bytes={body_bytes}"
                        + (f" body={body_text}" if body_text else "")
                    ) from exc
                retry_after = response.headers.get("retry-after") if response is not None else ""
                try:
                    delay = float(retry_after) if retry_after else min(8.0, 1.5 * (2 ** attempt))
                except ValueError:
                    delay = min(8.0, 1.5 * (2 ** attempt))
                time.sleep(delay)
        raise RuntimeError(f"LLM request failed after retries: {last_error}") from last_error

    def _collect_trace_headers(self, response: httpx.Response) -> dict[str, str]:
        interesting_headers = (
            "x-request-id",
            "request-id",
            "x-amzn-requestid",
            "x-amzn-trace-id",
            "traceparent",
            "cf-ray",
            "openai-request-id",
            "anthropic-request-id",
        )
        collected: dict[str, str] = {}
        for header_name in interesting_headers:
            header_value = response.headers.get(header_name)
            if header_value:
                collected[header_name] = header_value
        return collected

    def _extract_provider_request_id(
        self,
        *,
        response: httpx.Response | None,
        data: dict[str, Any] | None = None,
        body_text: str = "",
    ) -> str:
        if response is not None:
            for header_name in (
                "x-request-id",
                "request-id",
                "openai-request-id",
                "x-amzn-requestid",
            ):
                header_value = response.headers.get(header_name)
                if header_value:
                    return str(header_value)

        if isinstance(data, dict):
            for key in ("request_id", "requestId", "id"):
                value = data.get(key)
                if value:
                    return str(value)
            error_payload = data.get("error")
            if isinstance(error_payload, dict):
                for key in ("request_id", "requestId", "id"):
                    value = error_payload.get(key)
                    if value:
                        return str(value)
                error_message = str(error_payload.get("message") or "")
                matched = re.search(r"request id:\s*([A-Za-z0-9_-]+)", error_message, re.IGNORECASE)
                if matched:
                    return matched.group(1)

        if body_text:
            matched = re.search(r"request id:\s*([A-Za-z0-9_-]+)", body_text, re.IGNORECASE)
            if matched:
                return matched.group(1)

        return ""

    def _post_json_absolute_with_meta(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        request_headers = headers or {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        max_attempts = 4
        for attempt in range(max_attempts):
            started_at = datetime.now(timezone.utc)
            response: httpx.Response | None = None
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(url, headers=request_headers, json=payload)
                finished_at = datetime.now(timezone.utc)
                response.raise_for_status()
                data = self._decode_json_or_sse_response(response)
                meta = {
                    "provider_status_code": response.status_code,
                    "provider_request_headers": self._collect_trace_headers(response),
                    "provider_request_id": self._extract_provider_request_id(response=response, data=data),
                    "provider_started_at": started_at.isoformat(),
                    "provider_finished_at": finished_at.isoformat(),
                    "provider_duration_ms": int((finished_at - started_at).total_seconds() * 1000),
                }
                return data, meta
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                finished_at = datetime.now(timezone.utc)
                last_error = exc
                if isinstance(exc, httpx.HTTPStatusError):
                    response = exc.response
                body_text = ""
                data: dict[str, Any] | None = None
                status_code = 0
                headers_map: dict[str, str] = {}
                provider_request_id = ""
                if response is not None:
                    status_code = response.status_code
                    headers_map = self._collect_trace_headers(response)
                    body_text = response.text
                    try:
                        data = response.json()
                    except json.JSONDecodeError:
                        data = None
                    provider_request_id = self._extract_provider_request_id(
                        response=response,
                        data=data,
                        body_text=body_text,
                    )
                retryable = (
                    status_code in {408, 409, 425, 429}
                    or status_code >= 500
                    or isinstance(exc, httpx.TransportError)
                    or isinstance(exc, json.JSONDecodeError)
                )
                error_message = (
                    f"LLM request failed after retries: status={status_code or 'unknown'} "
                    f"request_id={provider_request_id or '-'} error={exc}"
                    + (f" body={body_text[:800]}" if body_text else "")
                )
                if not retryable or attempt == max_attempts - 1:
                    raise RuntimeError(error_message) from exc
                retry_after = response.headers.get("retry-after") if response is not None else ""
                try:
                    delay = float(retry_after) if retry_after else min(8.0, 1.5 * (2 ** attempt))
                except ValueError:
                    delay = min(8.0, 1.5 * (2 ** attempt))
                time.sleep(delay)
        raise RuntimeError(f"LLM request failed after retries: {last_error}") from last_error

    def _invoke_selected_tool(self, *, name: str, arguments: dict[str, Any], tools: list[BaseTool]) -> dict[str, Any]:
        selected_tool = next((item for item in tools if item.name == name), None)
        if selected_tool is None:
            raise RuntimeError(f"Unknown tool selected by model: {name}")
        result = selected_tool.invoke(arguments)
        return {
            "tool_name": name,
            "tool_arguments": arguments,
            "tool_result": result,
        }

    def _run_tool_call_chat_completions(
        self,
        *,
        model: str,
        system_prompt: str,
        user_payload: dict[str, Any],
        tools: list[BaseTool],
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        request = self._make_tool_call_request(
            model=model,
            system_prompt=system_prompt,
            user_payload=user_payload,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        url, headers, payload = self._tool_call_target(request, "chat_completions")
        path = self._tool_call_path_for_mode("chat_completions")
        debug_event(
            "llm_request_started",
            request_kind="tool_call",
            request_api_mode="chat_completions",
            path=path,
            model=model,
            tool_names=[item.name for item in tools],
        )
        data = self._post_json_absolute(url, payload, headers)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            raise RuntimeError("Model did not return any tool call.")

        first_call = tool_calls[0]
        function_payload = first_call.get("function") or {}
        name = str(function_payload.get("name") or "")
        arguments_raw = str(function_payload.get("arguments") or "{}")
        try:
            arguments = json.loads(arguments_raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Tool call arguments are not valid JSON: {exc}") from exc

        result = self._invoke_selected_tool(name=name, arguments=arguments, tools=tools)
        result["raw_json"] = data
        result["model"] = data.get("model") or model
        result["request_api_mode"] = "chat_completions"
        debug_event(
            "llm_request_finished",
            request_kind="tool_call",
            request_api_mode="chat_completions",
            path=path,
            model=result["model"],
            tool_name=name,
        )
        return result

    def _extract_responses_function_call(self, data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        output_items = data.get("output") or []
        if not output_items:
            response_id = str(data.get("id") or "")
            response_status = str(data.get("status") or "")
            response_model = str(data.get("model") or "")
            raise RuntimeError(
                "Responses API returned no output items for a required tool call. "
                f"id={response_id}, status={response_status}, model={response_model}"
            )
        for item in output_items:
            if item.get("type") != "function_call":
                continue
            name = str(item.get("name") or "")
            arguments_raw = str(item.get("arguments") or "{}")
            try:
                arguments = json.loads(arguments_raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Responses tool call arguments are not valid JSON: {exc}") from exc
            return name, arguments
        raise RuntimeError("Responses API did not return any function_call item.")

    def _extract_gemini_function_call(self, data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        candidates = data.get("candidates") or []
        for candidate in candidates:
            content = candidate.get("content") or {}
            parts = content.get("parts") or []
            for part in parts:
                function_call = part.get("functionCall") or part.get("function_call")
                if not function_call:
                    continue
                name = str(function_call.get("name") or "")
                arguments = function_call.get("args") or {}
                if not isinstance(arguments, dict):
                    raise RuntimeError("Gemini function call args are not a JSON object.")
                return name, arguments

        response_id = str(data.get("responseId") or data.get("id") or "")
        raise RuntimeError(
            "Gemini generateContent did not return any functionCall part. "
            f"id={response_id or '-'}"
        )

    def _run_tool_call_responses(
        self,
        *,
        model: str,
        system_prompt: str,
        user_payload: dict[str, Any],
        tools: list[BaseTool],
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        request = self._make_tool_call_request(
            model=model,
            system_prompt=system_prompt,
            user_payload=user_payload,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        url, headers, payload = self._tool_call_target(request, "responses")
        path = self._tool_call_path_for_mode("responses")
        debug_event(
            "llm_request_started",
            request_kind="tool_call",
            request_api_mode="responses",
            path=path,
            model=model,
            tool_names=[item.name for item in tools],
        )
        data = self._post_json_absolute(url, payload, headers)
        name, arguments = self._extract_responses_function_call(data)
        result = self._invoke_selected_tool(name=name, arguments=arguments, tools=tools)
        result["raw_json"] = data
        result["model"] = data.get("model") or model
        result["request_api_mode"] = "responses"
        debug_event(
            "llm_request_finished",
            request_kind="tool_call",
            request_api_mode="responses",
            path=path,
            model=result["model"],
            tool_name=name,
        )
        return result

    def _normalized_gemini_base(self) -> str:
        base = self.base_url.rstrip("/")
        for suffix in ("/v1beta", "/v1"):
            if base.endswith(suffix):
                return base[: -len(suffix)]
        return base

    def _run_tool_call_gemini_generate_content(
        self,
        *,
        model: str,
        system_prompt: str,
        user_payload: dict[str, Any],
        tools: list[BaseTool],
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        request = self._make_tool_call_request(
            model=model,
            system_prompt=system_prompt,
            user_payload=user_payload,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        url, headers, payload = self._tool_call_target(request, "gemini_generate_content")
        path = self._tool_call_path_for_mode("gemini_generate_content")
        debug_event(
            "llm_request_started",
            request_kind="tool_call",
            request_api_mode="gemini_generate_content",
            path=path,
            model=model,
            tool_names=[item.name for item in tools],
        )
        data, meta = self._post_json_absolute_with_meta(url, payload, headers)
        name, arguments = self._extract_gemini_function_call(data)
        result = self._invoke_selected_tool(name=name, arguments=arguments, tools=tools)
        result["raw_json"] = data
        result["model"] = model
        result["request_api_mode"] = "gemini_generate_content"
        result["provider_request_id"] = meta.get("provider_request_id") or ""
        result["provider_duration_ms"] = meta.get("provider_duration_ms") or 0
        debug_event(
            "llm_request_finished",
            request_kind="tool_call",
            request_api_mode="gemini_generate_content",
            path=path,
            model=model,
            tool_name=name,
            provider_request_id=result["provider_request_id"],
            provider_duration_ms=result["provider_duration_ms"],
        )
        return result

    def run_tool_call(
        self,
        *,
        model: str,
        system_prompt: str,
        user_payload: dict[str, Any],
        tools: list[BaseTool],
        temperature: float = 0.2,
        max_tokens: int = 1600,
    ) -> dict[str, Any]:
        if not self.is_configured():
            raise RuntimeError("LLM provider is not configured.")

        if self.api_mode == "gemini_generate_content":
            return self._run_tool_call_gemini_generate_content(
                model=model,
                system_prompt=system_prompt,
                user_payload=user_payload,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        if self.api_mode == "responses":
            return self._run_tool_call_responses(
                model=model,
                system_prompt=system_prompt,
                user_payload=user_payload,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        return self._run_tool_call_chat_completions(
            model=model,
            system_prompt=system_prompt,
            user_payload=user_payload,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def generate_image(
        self,
        *,
        model: str,
        prompt: str,
        size: str = "1536x1024",
        image_attempt_id: str = "",
    ) -> dict[str, Any]:
        if not self.is_configured():
            raise RuntimeError("Image provider is not configured.")
        if self.image_api_mode == "gemini_generate_content":
            return self._generate_image_gemini_generate_content(
                model=model,
                prompt=prompt,
                size=size,
                image_attempt_id=image_attempt_id,
            )
        return self._generate_image_openai_images(
            model=model,
            prompt=prompt,
            size=size,
            image_attempt_id=image_attempt_id,
        )

    def _generate_image_openai_images(
        self,
        *,
        model: str,
        prompt: str,
        size: str,
        image_attempt_id: str,
    ) -> dict[str, Any]:
        request = self._make_image_request(
            model=model,
            prompt=prompt,
            size=size,
            image_attempt_id=image_attempt_id,
        )
        url, headers, payload = self._image_target(request, "openai_images")
        path = self._image_path_for_mode("openai_images")
        debug_event(
            "llm_request_started",
            request_kind="image_generation",
            request_api_mode="openai_images",
            path=path,
            model=model,
            image_attempt_id=image_attempt_id,
        )
        data, meta = self._post_json_absolute_with_meta(url, payload, headers)
        record = (data.get("data") or [{}])[0]
        result = {
            "image_url": record.get("url") or "",
            "image_b64": record.get("b64_json") or "",
            "image_mime_type": "image/png",
            "revised_prompt": record.get("revised_prompt") or "",
            "image_attempt_id": image_attempt_id,
            "provider_request_id": meta.get("provider_request_id") or "",
            "provider_request_path": path,
            "provider_request_url": url,
            "provider_request_headers": meta.get("provider_request_headers") or {},
            "provider_status_code": meta.get("provider_status_code") or 0,
            "provider_started_at": meta.get("provider_started_at") or "",
            "provider_finished_at": meta.get("provider_finished_at") or "",
            "provider_duration_ms": meta.get("provider_duration_ms") or 0,
            "raw_json": data,
        }
        debug_event(
            "llm_request_finished",
            request_kind="image_generation",
            request_api_mode="openai_images",
            path=path,
            model=model,
            image_attempt_id=image_attempt_id,
            provider_request_id=result["provider_request_id"],
            provider_duration_ms=result["provider_duration_ms"],
        )
        return result

    def _extract_gemini_image_parts(self, data: dict[str, Any]) -> tuple[str, str, str]:
        candidates = data.get("candidates") or []
        text_fragments: list[str] = []
        image_b64 = ""
        image_mime_type = "image/png"

        for candidate in candidates:
            content = candidate.get("content") or {}
            parts = content.get("parts") or []
            for part in parts:
                if "text" in part and part["text"]:
                    text_fragments.append(str(part["text"]))
                inline = part.get("inlineData") or part.get("inline_data") or {}
                if inline.get("data"):
                    image_b64 = str(inline.get("data") or "")
                    image_mime_type = str(inline.get("mimeType") or inline.get("mime_type") or "image/png")
                    return image_b64, image_mime_type, "\n".join(text_fragments).strip()

        if not image_b64:
            raise RuntimeError("Gemini generateContent response did not include any inline image data.")
        return image_b64, image_mime_type, "\n".join(text_fragments).strip()

    def _generate_image_gemini_generate_content(
        self,
        *,
        model: str,
        prompt: str,
        size: str,
        image_attempt_id: str,
    ) -> dict[str, Any]:
        request = self._make_image_request(
            model=model,
            prompt=prompt,
            size=size,
            image_attempt_id=image_attempt_id,
        )
        url, headers, payload = self._image_target(request, "gemini_generate_content")
        path = self._image_path_for_mode("gemini_generate_content")
        debug_event(
            "llm_request_started",
            request_kind="image_generation",
            request_api_mode="gemini_generate_content",
            path=path,
            model=model,
            image_attempt_id=image_attempt_id,
        )
        data, meta = self._post_json_absolute_with_meta(url, payload, headers)
        image_b64, image_mime_type, text_output = self._extract_gemini_image_parts(data)
        result = {
            "image_url": "",
            "image_b64": image_b64,
            "image_mime_type": image_mime_type,
            "revised_prompt": text_output,
            "image_attempt_id": image_attempt_id,
            "provider_request_id": meta.get("provider_request_id") or "",
            "provider_request_path": path,
            "provider_request_url": url,
            "provider_request_headers": meta.get("provider_request_headers") or {},
            "provider_status_code": meta.get("provider_status_code") or 0,
            "provider_started_at": meta.get("provider_started_at") or "",
            "provider_finished_at": meta.get("provider_finished_at") or "",
            "provider_duration_ms": meta.get("provider_duration_ms") or 0,
            "raw_json": data,
        }
        debug_event(
            "llm_request_finished",
            request_kind="image_generation",
            request_api_mode="gemini_generate_content",
            path=path,
            model=model,
            image_attempt_id=image_attempt_id,
            provider_request_id=result["provider_request_id"],
            provider_duration_ms=result["provider_duration_ms"],
        )
        return result
