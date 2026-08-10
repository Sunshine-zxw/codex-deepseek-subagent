#!/usr/bin/env python3
"""Lightweight localhost Responses -> Chat Completions bridge for Codex custom subagents.

Codex still speaks the Responses wire API. Providers explicitly configured with
`chat_completions_bridge` are translated to an OpenAI-compatible
/chat/completions upstream. Tool calls, tool results, and DeepSeek
`reasoning_content` are preserved across turns without Node.js or LiteLLM.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 48671
REGISTRY_RELATIVE = Path("codex-deepseek-subagent") / "registry.json"
TRANSPORT_STATE_RELATIVE = Path("codex-deepseek-subagent") / "transport-state.json"
PID_RELATIVE = Path("codex-deepseek-subagent") / "transport-bridge.pid"
LOG_RELATIVE = Path("codex-deepseek-subagent") / "transport-bridge.log"
TRANSPORT = "chat_completions_bridge"
MAX_BODY_BYTES = 64 * 1024 * 1024
TOOL_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


class BridgeError(RuntimeError):
    def __init__(self, status: int, message: str, code: str = "bridge_error"):
        super().__init__(message)
        self.status = status
        self.code = code


def resolve_home(codex_home: str | None = None) -> Path:
    return Path(
        codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex"
    ).expanduser().resolve()


def registry_path(codex_home: str | None = None) -> Path:
    return resolve_home(codex_home) / REGISTRY_RELATIVE


def transport_state_path(codex_home: str | None = None) -> Path:
    return resolve_home(codex_home) / TRANSPORT_STATE_RELATIVE


def pid_path(codex_home: str | None = None) -> Path:
    return resolve_home(codex_home) / PID_RELATIVE


def log_path(codex_home: str | None = None) -> Path:
    return resolve_home(codex_home) / LOG_RELATIVE


def bridge_port() -> int:
    value = os.environ.get("CODEX_SUBAGENT_BRIDGE_PORT")
    if value:
        try:
            port = int(value)
        except ValueError as exc:
            raise BridgeError(500, "CODEX_SUBAGENT_BRIDGE_PORT 不是有效端口。") from exc
        if not 1 <= port <= 65535:
            raise BridgeError(500, "CODEX_SUBAGENT_BRIDGE_PORT 超出有效范围。")
        return port
    return DEFAULT_PORT


def provider_base_url(provider_id: str, port: int | None = None) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", provider_id):
        raise BridgeError(500, f"Provider ID 无法用于 bridge 路径：{provider_id}")
    return f"http://{DEFAULT_HOST}:{port or bridge_port()}/providers/{provider_id}"


def health_url(port: int | None = None) -> str:
    return f"http://{DEFAULT_HOST}:{port or bridge_port()}/health"


def _load_json(path: Path, missing_ok: bool = False) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if missing_ok:
            return {}
        raise
    if not isinstance(payload, dict):
        raise ValueError(f"JSON 根对象必须是 object：{path}")
    return payload


def _load_registry(codex_home: str | None = None) -> dict[str, Any]:
    path = registry_path(codex_home)
    try:
        return _load_json(path)
    except FileNotFoundError as exc:
        raise BridgeError(503, f"registry 不存在：{path}", "registry_missing") from exc
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise BridgeError(500, f"registry 无法读取：{path}", "registry_invalid") from exc


def _load_transport_state(codex_home: str | None = None) -> dict[str, Any]:
    path = transport_state_path(codex_home)
    try:
        return _load_json(path, missing_ok=True)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise BridgeError(500, f"transport state 无法读取：{path}", "transport_state_invalid") from exc


def provider_for_request(
    provider_id: str,
    model: str,
    codex_home: str | None = None,
) -> tuple[dict[str, Any], str]:
    registry = _load_registry(codex_home)
    state = _load_transport_state(codex_home)
    providers = registry.get("providers") or {}
    agents = registry.get("agents") or {}
    provider = providers.get(provider_id)
    if not isinstance(provider, dict):
        raise BridgeError(404, f"未知 Provider：{provider_id}", "provider_missing")

    transport = ((state.get("providers") or {}).get(provider_id) or {}).get("transport", "responses")
    if transport != TRANSPORT:
        raise BridgeError(400, f"Provider {provider_id} 未配置 {TRANSPORT}。", "transport_mismatch")

    agent_profiles = state.get("agents") or {}
    profiles = set()
    for role, agent in agents.items():
        if not isinstance(agent, dict):
            continue
        if agent.get("provider") != provider_id or agent.get("model") != model:
            continue
        profile_state = agent_profiles.get(role) or {}
        profiles.add(str(profile_state.get("request_profile", "auto")))
    if len(profiles) > 1:
        raise BridgeError(
            409,
            f"Provider {provider_id} / model {model} 被多个 Agent 配置了不同 request_profile。",
            "request_profile_conflict",
        )
    request_profile = next(iter(profiles), "auto")
    return provider, request_profile


def effective_request_profile(model: str, configured: str) -> str:
    if configured != "auto":
        return configured
    return "deepseek-thinking" if "deepseek" in model.lower() else "default"


def _text_from_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for part in content:
            if isinstance(part, str):
                pieces.append(part)
            elif isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    pieces.append(part["text"])
                elif isinstance(part.get("output"), str):
                    pieces.append(part["output"])
        return "\n".join(piece for piece in pieces if piece)
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        if isinstance(content.get("output"), str):
            return content["output"]
    return json.dumps(content, ensure_ascii=False)


def _tool_output_text(output: Any) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        if isinstance(output.get("content"), list):
            return _text_from_content(output["content"])
        if isinstance(output.get("text"), str):
            return output["text"]
    if isinstance(output, list):
        return _text_from_content(output)
    return json.dumps(output, ensure_ascii=False)


def _safe_tool_name(namespace: str | None, name: str, used: set[str]) -> str:
    raw = f"{namespace}__{name}" if namespace else name
    candidate = TOOL_NAME_RE.sub("_", raw).strip("_") or "tool"
    candidate = candidate[:64]
    base = candidate
    suffix = 2
    while candidate in used:
        tail = f"_{suffix}"
        candidate = f"{base[: max(1, 64 - len(tail))]}{tail}"
        suffix += 1
    used.add(candidate)
    return candidate


def translate_tools(tools: Any) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not isinstance(tools, list):
        return [], {}
    result: list[dict[str, Any]] = []
    mapping: dict[str, dict[str, Any]] = {}
    used: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        kind = str(tool.get("type", "function"))
        function_payload = tool.get("function") if isinstance(tool.get("function"), dict) else {}
        name = str(tool.get("name") or function_payload.get("name") or "tool")
        namespace = tool.get("namespace") if isinstance(tool.get("namespace"), str) else None
        chat_name = _safe_tool_name(namespace, name, used)
        description = tool.get("description")
        if not isinstance(description, str):
            description = str(function_payload.get("description") or "")

        if kind == "custom":
            parameters = {
                "type": "object",
                "properties": {
                    "input": {
                        "type": "string",
                        "description": "Raw input for the original Codex custom tool.",
                    }
                },
                "required": ["input"],
                "additionalProperties": False,
            }
        else:
            parameters = tool.get("parameters")
            if not isinstance(parameters, dict):
                parameters = function_payload.get("parameters")
            if not isinstance(parameters, dict):
                parameters = {"type": "object", "properties": {}}

        function: dict[str, Any] = {
            "name": chat_name,
            "description": description,
            "parameters": parameters,
        }
        strict = tool.get("strict")
        if isinstance(strict, bool):
            function["strict"] = strict
        result.append({"type": "function", "function": function})
        mapping[chat_name] = {"kind": kind, "name": name, "namespace": namespace}
    return result, mapping


def _chat_name_for_original(
    name: str,
    namespace: str | None,
    mapping: dict[str, dict[str, Any]],
) -> str:
    for chat_name, meta in mapping.items():
        if meta.get("name") == name and meta.get("namespace") == namespace:
            return chat_name
    raw = f"{namespace}__{name}" if namespace else name
    return TOOL_NAME_RE.sub("_", raw).strip("_")[:64] or "tool"


def translate_input(input_value: Any, tool_mapping: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(input_value, str):
        return [{"role": "user", "content": input_value}]
    if not isinstance(input_value, list):
        return []

    messages: list[dict[str, Any]] = []
    pending_reasoning: str | None = None
    for item in input_value:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "reasoning":
            reasoning = _text_from_content(item.get("content"))
            if reasoning:
                pending_reasoning = reasoning
            continue
        if kind == "message" or (kind is None and item.get("role")):
            role = str(item.get("role") or "user")
            if role == "developer":
                role = "system"
            message: dict[str, Any] = {"role": role, "content": _text_from_content(item.get("content"))}
            if role == "assistant" and pending_reasoning:
                message["reasoning_content"] = pending_reasoning
                pending_reasoning = None
            messages.append(message)
            continue
        if kind in {"function_call", "custom_tool_call"}:
            name = str(item.get("name") or "tool")
            namespace = item.get("namespace") if isinstance(item.get("namespace"), str) else None
            chat_name = _chat_name_for_original(name, namespace, tool_mapping)
            if kind == "custom_tool_call":
                arguments = json.dumps({"input": str(item.get("input") or "")}, ensure_ascii=False)
            else:
                arguments = str(item.get("arguments") or "{}")
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": str(item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex}"),
                        "type": "function",
                        "function": {"name": chat_name, "arguments": arguments},
                    }
                ],
            }
            if pending_reasoning:
                message["reasoning_content"] = pending_reasoning
                pending_reasoning = None
            messages.append(message)
            continue
        if kind in {"function_call_output", "custom_tool_call_output"}:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(item.get("call_id") or ""),
                    "content": _tool_output_text(item.get("output")),
                }
            )
    return ensure_tool_result_order(coalesce_assistant_messages(messages))


def coalesce_assistant_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        previous = result[-1] if result else None
        if (
            previous
            and previous.get("role") == "assistant"
            and message.get("role") == "assistant"
            and (previous.get("tool_calls") or message.get("tool_calls"))
        ):
            if message.get("content"):
                if previous.get("content"):
                    previous["content"] = f"{previous['content']}\n{message['content']}"
                else:
                    previous["content"] = message["content"]
            if message.get("tool_calls"):
                previous.setdefault("tool_calls", []).extend(message["tool_calls"])
            if message.get("reasoning_content") and not previous.get("reasoning_content"):
                previous["reasoning_content"] = message["reasoning_content"]
            continue
        result.append(message)
    return result


def ensure_tool_result_order(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop orphan tool rows while preserving valid rows directly after their call."""
    result: list[dict[str, Any]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.get("role") == "tool":
            index += 1
            continue
        result.append(message)
        calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        call_ids = [str(call.get("id")) for call in calls or [] if call.get("id")]
        if not call_ids:
            index += 1
            continue
        index += 1
        tool_rows: dict[str, dict[str, Any]] = {}
        while index < len(messages) and messages[index].get("role") == "tool":
            row = messages[index]
            call_id = str(row.get("tool_call_id") or "")
            if call_id in call_ids and call_id not in tool_rows:
                tool_rows[call_id] = row
            index += 1
        for call_id in call_ids:
            if call_id in tool_rows:
                result.append(tool_rows[call_id])
    return result


def _translate_tool_choice(value: Any, mapping: dict[str, dict[str, Any]]) -> Any:
    if value is None or isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return value
    kind = value.get("type")
    name = value.get("name")
    namespace = value.get("namespace") if isinstance(value.get("namespace"), str) else None
    if kind in {"function", "custom"} and isinstance(name, str):
        chat_name = _chat_name_for_original(name, namespace, mapping)
        return {"type": "function", "function": {"name": chat_name}}
    function = value.get("function")
    if isinstance(function, dict) and isinstance(function.get("name"), str):
        return value
    return "auto"


def deepseek_effort(value: Any) -> str:
    value = str(value or "high")
    if value in {"none", "minimal", "low"}:
        return "low"
    if value in {"xhigh", "max", "ultra"}:
        return "max"
    return "high"


def responses_to_chat(payload: dict[str, Any], request_profile: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    chat_tools, tool_mapping = translate_tools(payload.get("tools"))
    messages: list[dict[str, Any]] = []
    instructions = payload.get("instructions")
    if isinstance(instructions, str) and instructions:
        messages.append({"role": "system", "content": instructions})
    messages.extend(translate_input(payload.get("input"), tool_mapping))

    chat: dict[str, Any] = {"model": payload.get("model"), "messages": messages, "stream": False}
    if chat_tools:
        chat["tools"] = chat_tools
    tool_choice = _translate_tool_choice(payload.get("tool_choice"), tool_mapping)
    if tool_choice is not None:
        chat["tool_choice"] = tool_choice
    if isinstance(payload.get("parallel_tool_calls"), bool):
        chat["parallel_tool_calls"] = payload["parallel_tool_calls"]
    if isinstance(payload.get("max_output_tokens"), int):
        chat["max_tokens"] = payload["max_output_tokens"]
    for name in ("temperature", "top_p"):
        if payload.get(name) is not None:
            chat[name] = payload[name]

    reasoning = payload.get("reasoning")
    effort = reasoning.get("effort") if isinstance(reasoning, dict) else payload.get("reasoning_effort")
    profile = effective_request_profile(str(payload.get("model") or ""), request_profile)
    if profile == "deepseek-thinking":
        chat["thinking"] = {"type": "enabled"}
        chat["reasoning_effort"] = deepseek_effort(effort)
        chat.pop("temperature", None)
        chat.pop("top_p", None)
        # Same compatibility rule as Codex Router: DeepSeek thinking rejects
        # required/function-object tool_choice, while auto still allows tools.
        if chat.get("tool_choice") not in {None, "none"}:
            chat["tool_choice"] = "auto"
    elif profile == "deepseek-nonthinking":
        chat["thinking"] = {"type": "disabled"}
        chat.pop("reasoning_effort", None)
    elif profile == "auto-tool-choice":
        if chat.get("tool_choice") not in {None, "none"}:
            chat["tool_choice"] = "auto"
    elif effort is not None:
        chat["reasoning_effort"] = str(effort)
    return chat, tool_mapping


def chat_message_to_items(
    message: dict[str, Any],
    tool_mapping: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    reasoning_content = message.get("reasoning_content")
    if isinstance(reasoning_content, str) and reasoning_content:
        items.append(
            {
                "type": "reasoning",
                "id": f"rs_{uuid.uuid4().hex}",
                "summary": [],
                "content": [{"type": "reasoning_text", "text": reasoning_content}],
                "encrypted_content": None,
            }
        )

    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        chat_name = str(function.get("name") or "tool")
        meta = tool_mapping.get(chat_name, {"kind": "function", "name": chat_name, "namespace": None})
        call_id = str(call.get("id") or f"call_{uuid.uuid4().hex}")
        arguments = str(function.get("arguments") or "{}")
        if meta.get("kind") == "custom":
            try:
                parsed = json.loads(arguments)
                raw_input = parsed.get("input") if isinstance(parsed, dict) else arguments
            except json.JSONDecodeError:
                raw_input = arguments
            item = {
                "type": "custom_tool_call",
                "id": f"ctc_{uuid.uuid4().hex}",
                "call_id": call_id,
                "name": meta.get("name") or chat_name,
                "input": str(raw_input or ""),
                "status": "completed",
            }
        else:
            item = {
                "type": "function_call",
                "id": f"fc_{uuid.uuid4().hex}",
                "call_id": call_id,
                "name": meta.get("name") or chat_name,
                "arguments": arguments,
            }
        if meta.get("namespace"):
            item["namespace"] = meta["namespace"]
        items.append(item)

    text = _text_from_content(message.get("content"))
    if text:
        items.append(
            {
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex}",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        )
    return items


def _usage_payload(chat_usage: Any) -> dict[str, Any] | None:
    if not isinstance(chat_usage, dict):
        return None
    input_tokens = int(chat_usage.get("prompt_tokens") or 0)
    output_tokens = int(chat_usage.get("completion_tokens") or 0)
    total_tokens = int(chat_usage.get("total_tokens") or input_tokens + output_tokens)
    details = chat_usage.get("completion_tokens_details") or {}
    reasoning_tokens = int(details.get("reasoning_tokens") or 0) if isinstance(details, dict) else 0
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
        "total_tokens": total_tokens,
    }


def chat_to_responses(chat_payload: dict[str, Any], tool_mapping: dict[str, dict[str, Any]]) -> dict[str, Any]:
    choices = chat_payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise BridgeError(502, "Chat Completions 响应缺少 choices[0]。", "upstream_invalid")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise BridgeError(502, "Chat Completions 响应缺少 message。", "upstream_invalid")
    response_id = f"resp_{uuid.uuid4().hex}"
    return {
        "id": response_id,
        "object": "response",
        "status": "completed",
        "model": chat_payload.get("model"),
        "output": chat_message_to_items(message, tool_mapping),
        "usage": _usage_payload(chat_payload.get("usage")),
    }


def response_sse_events(response: dict[str, Any]) -> list[dict[str, Any]]:
    response_id = str(response.get("id") or f"resp_{uuid.uuid4().hex}")
    events: list[dict[str, Any]] = [
        {
            "type": "response.created",
            "response": {"id": response_id, "object": "response", "status": "in_progress"},
        }
    ]
    for index, item in enumerate(response.get("output") or []):
        events.append({"type": "response.output_item.added", "output_index": index, "item": item})
        if item.get("type") == "message":
            text = _text_from_content(item.get("content"))
            if text:
                events.append(
                    {
                        "type": "response.output_text.delta",
                        "item_id": item.get("id"),
                        "output_index": index,
                        "content_index": 0,
                        "delta": text,
                    }
                )
        events.append({"type": "response.output_item.done", "output_index": index, "item": item})
    events.append(
        {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "object": "response",
                "status": "completed",
                "usage": response.get("usage"),
                "end_turn": True,
            },
        }
    )
    return events


def _join_endpoint(base_url: str, suffix: str) -> str:
    return base_url.rstrip("/") + "/" + suffix.lstrip("/")


def call_upstream(
    provider: dict[str, Any],
    chat_payload: dict[str, Any],
    authorization: str,
) -> dict[str, Any]:
    base_url = str(provider.get("base_url") or "")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BridgeError(500, "Provider base_url 无效。", "provider_invalid")
    url = _join_endpoint(base_url, "chat/completions")
    body = json.dumps(chat_payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "codex-custom-subagent-bridge/1",
    }
    if authorization:
        headers["Authorization"] = authorization
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=300) as upstream:
            raw = upstream.read()
            status = upstream.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        message = raw.decode("utf-8", errors="replace")[-4000:]
        raise BridgeError(exc.code, message or f"上游 HTTP {exc.code}", "upstream_http_error") from exc
    except urllib.error.URLError as exc:
        raise BridgeError(502, f"无法连接 Chat Completions 上游：{exc.reason}", "upstream_unreachable") from exc
    if status >= 400:
        raise BridgeError(status, raw.decode("utf-8", errors="replace")[-4000:], "upstream_http_error")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise BridgeError(502, "Chat Completions 上游返回了非 JSON 响应。", "upstream_invalid") from exc
    if not isinstance(payload, dict):
        raise BridgeError(502, "Chat Completions 上游响应根对象无效。", "upstream_invalid")
    return payload


def translate_request(
    provider_id: str,
    payload: dict[str, Any],
    authorization: str,
    codex_home: str | None = None,
) -> dict[str, Any]:
    model = str(payload.get("model") or "")
    if not model:
        raise BridgeError(400, "Responses 请求缺少 model。", "model_missing")
    provider, request_profile = provider_for_request(provider_id, model, codex_home)
    chat_payload, tool_mapping = responses_to_chat(payload, request_profile)
    upstream = call_upstream(provider, chat_payload, authorization)
    response = chat_to_responses(upstream, tool_mapping)
    response["model"] = model
    return response


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "CodexSubagentBridge/1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[bridge] " + (fmt % args) + "\n")

    @property
    def codex_home(self) -> str:
        return str(getattr(self.server, "codex_home"))

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/health":
            self._json(200, {"ok": True, "service": "codex-subagent-transport-bridge"})
            return
        self._json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        match = re.fullmatch(r"/providers/([^/]+)/responses/?", self.path.split("?", 1)[0])
        if not match:
            self._json(404, {"error": {"message": "unsupported route"}})
            return
        provider_id = unquote(match.group(1))
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            self._json(413 if length > MAX_BODY_BYTES else 400, {"error": {"message": "invalid body length"}})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("request must be object")
            authorization = self.headers.get("Authorization") or ""
            if not authorization:
                raise BridgeError(401, "缺少 Authorization。", "credential_missing")
            response = translate_request(provider_id, payload, authorization, self.codex_home)
            if payload.get("stream") is False:
                self._json(200, response)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            for event in response_sse_events(response):
                line = "data: " + json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n\n"
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
        except BridgeError as exc:
            self._json(exc.status, {"error": {"type": exc.code, "message": str(exc)}})
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            self._json(400, {"error": {"type": "invalid_request", "message": str(exc)}})
        except BrokenPipeError:
            return
        except Exception as exc:  # pragma: no cover
            self._json(500, {"error": {"type": "bridge_internal", "message": f"{type(exc).__name__}: {exc}"}})


def serve(codex_home: str | None = None, host: str = DEFAULT_HOST, port: int | None = None) -> None:
    home = resolve_home(codex_home)
    state_dir = home / "codex-deepseek-subagent"
    state_dir.mkdir(parents=True, exist_ok=True)
    selected_port = port or bridge_port()
    server = ThreadingHTTPServer((host, selected_port), BridgeHandler)
    server.codex_home = str(home)  # type: ignore[attr-defined]
    pid_path(str(home)).write_text(str(os.getpid()), encoding="utf-8")
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        try:
            pid_path(str(home)).unlink(missing_ok=True)
        finally:
            server.server_close()


def is_healthy(port: int | None = None, timeout: float = 0.8) -> bool:
    try:
        with urllib.request.urlopen(health_url(port), timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def ensure_running(codex_home: str | None = None, timeout: float = 6.0) -> dict[str, Any]:
    port = bridge_port()
    if is_healthy(port):
        return {"running": True, "started": False, "port": port}
    home = resolve_home(codex_home)
    log = log_path(str(home))
    log.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log, "ab", buffering=0)
    cmd = [sys.executable, str(Path(__file__).resolve()), "serve", "--codex-home", str(home), "--port", str(port)]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": handle,
        "stderr": handle,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(cmd, **kwargs)
    finally:
        handle.close()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_healthy(port):
            return {"running": True, "started": True, "port": port, "log": str(log)}
        time.sleep(0.1)
    raise BridgeError(503, f"transport bridge 启动失败；查看日志：{log}", "bridge_start_failed")


def stop(codex_home: str | None = None) -> dict[str, Any]:
    path = pid_path(codex_home)
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, OSError, ValueError):
        return {"running": is_healthy(), "stopped": False}
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10)
        else:
            os.kill(pid, 15)
    except Exception:
        pass
    deadline = time.time() + 3
    while time.time() < deadline and is_healthy():
        time.sleep(0.1)
    path.unlink(missing_ok=True)
    return {"running": is_healthy(), "stopped": not is_healthy()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("serve")
    p.add_argument("--codex-home")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p = sub.add_parser("status")
    p.add_argument("--codex-home")
    p = sub.add_parser("start")
    p.add_argument("--codex-home")
    p = sub.add_parser("stop")
    p.add_argument("--codex-home")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "serve":
        serve(args.codex_home, args.host, args.port)
        return 0
    if args.command == "status":
        print(json.dumps({"running": is_healthy(), "port": bridge_port()}, ensure_ascii=False))
        return 0
    if args.command == "start":
        print(json.dumps(ensure_running(args.codex_home), ensure_ascii=False))
        return 0
    if args.command == "stop":
        print(json.dumps(stop(args.codex_home), ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
