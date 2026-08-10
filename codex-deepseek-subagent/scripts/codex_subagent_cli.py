#!/usr/bin/env python3
"""Canonical CLI for multi-provider Codex subagents, transport routing, and tool-call probes."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


multi = _load("codex_subagent_manager_impl", HERE / "codex_subagent_manager.py")
approval_fix = _load(
    "external_provider_approval_fix_impl",
    HERE / "external_provider_approval_fix.py",
)
bridge = _load("codex_transport_bridge_impl", HERE / "codex_transport_bridge.py")
manager = multi.manager

TRANSPORT_RESPONSES = "responses"
TRANSPORT_BRIDGE = "chat_completions_bridge"
TRANSPORTS = (TRANSPORT_RESPONSES, TRANSPORT_BRIDGE)
REQUEST_PROFILES = (
    "auto",
    "default",
    "auto-tool-choice",
    "deepseek-thinking",
    "deepseek-nonthinking",
)
TRANSPORT_STATE_SCHEMA_VERSION = 1

_original_reconcile = multi.reconcile
_original_status = multi.status
_ACTIVE_STATE: dict[str, Any] = {}


def _empty_transport_state() -> dict[str, Any]:
    return {
        "schema_version": TRANSPORT_STATE_SCHEMA_VERSION,
        "providers": {},
        "agents": {},
        "tool_compatibility": {},
    }


def _state_path(codex_home: str | None = None) -> Path:
    return bridge.transport_state_path(codex_home)


def load_transport_state(registry, codex_home: str | None = None) -> dict[str, Any]:
    path = _state_path(codex_home)
    if path.is_file():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise manager.ManagerError(
                "invalid_transport_state",
                f"无法读取 transport state：{path}",
            ) from exc
        if (
            not isinstance(state, dict)
            or state.get("schema_version") != TRANSPORT_STATE_SCHEMA_VERSION
        ):
            raise manager.ManagerError(
                "invalid_transport_state",
                "transport-state.json schema 无效。",
            )
    else:
        state = _empty_transport_state()

    providers = state.setdefault("providers", {})
    agents = state.setdefault("agents", {})
    compatibility = state.setdefault("tool_compatibility", {})
    if not all(isinstance(value, dict) for value in (providers, agents, compatibility)):
        raise manager.ManagerError(
            "invalid_transport_state",
            "transport state providers/agents/tool_compatibility 无效。",
        )

    for provider_id in registry.providers:
        entry = providers.setdefault(provider_id, {})
        if not isinstance(entry, dict):
            entry = providers[provider_id] = {}
        entry.setdefault("transport", TRANSPORT_RESPONSES)
    for role in registry.agents:
        entry = agents.setdefault(role, {})
        if not isinstance(entry, dict):
            entry = agents[role] = {}
        entry.setdefault("request_profile", "auto")

    for provider_id in list(providers):
        if provider_id not in registry.providers:
            providers.pop(provider_id, None)
    for role in list(agents):
        if role not in registry.agents:
            agents.pop(role, None)
    for role in list(compatibility):
        if role not in registry.agents:
            compatibility.pop(role, None)

    # Validate all persisted values early so a broken sidecar cannot silently
    # generate the wrong runtime provider block.
    for provider_id in registry.providers:
        provider_transport(state, provider_id)
    for role in registry.agents:
        agent_request_profile(state, role)
    return state


def save_transport_state(
    state: dict[str, Any],
    codex_home: str | None = None,
) -> None:
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    manager.atomic_write(_state_path(codex_home), payload.encode("utf-8"))


def provider_transport(state: dict[str, Any], provider_id: str) -> str:
    value = (
        ((state.get("providers") or {}).get(provider_id) or {}).get(
            "transport",
            TRANSPORT_RESPONSES,
        )
    )
    if value not in TRANSPORTS:
        raise manager.ManagerError(
            "invalid_transport",
            f"Provider {provider_id} transport 无效：{value}",
        )
    return value


def agent_request_profile(state: dict[str, Any], role: str) -> str:
    value = (
        ((state.get("agents") or {}).get(role) or {}).get(
            "request_profile",
            "auto",
        )
    )
    if value not in REQUEST_PROFILES:
        raise manager.ManagerError(
            "invalid_request_profile",
            f"Agent {role} request_profile 无效：{value}",
        )
    return value


def effective_provider_base_url(provider_id: str, spec, state: dict[str, Any]) -> str:
    if provider_transport(state, provider_id) == TRANSPORT_BRIDGE:
        return bridge.provider_base_url(provider_id)
    return spec.base_url


def _bridge_required(registry, state: dict[str, Any]) -> bool:
    return any(
        provider_transport(state, agent.provider) == TRANSPORT_BRIDGE
        for agent in registry.agents.values()
    )


def _render_provider_block(registry) -> str:
    if not registry.providers:
        return ""
    lines = ["", multi.PROVIDERS_BEGIN]
    for provider_id, spec in sorted(registry.providers.items()):
        auth = multi.provider_auth(spec)
        lines.extend(
            [
                f"[model_providers.{provider_id}]",
                f"name = {manager.toml_string(spec.provider_name)}",
                (
                    "base_url = "
                    + manager.toml_string(
                        effective_provider_base_url(provider_id, spec, _ACTIVE_STATE)
                    )
                ),
                'wire_api = "responses"',
                "",
                f"[model_providers.{provider_id}.auth]",
                f"command = {manager.toml_string(auth['command'])}",
                f"args = {manager.toml_string_array(auth['args'])}",
                "timeout_ms = 5000",
                "refresh_interval_ms = 0",
                "",
            ]
        )
    lines.append(multi.PROVIDERS_END)
    return "\n".join(lines) + "\n"


def _safe_reconcile(registry, codex_home=None):
    bridge_status: dict[str, Any] | None = None
    if _bridge_required(registry, _ACTIVE_STATE):
        try:
            bridge_status = bridge.ensure_running(codex_home)
        except bridge.BridgeError as exc:
            raise manager.ManagerError(exc.code, str(exc)) from exc

    result = _original_reconcile(registry, codex_home)
    save_transport_state(_ACTIVE_STATE, codex_home)
    approval = approval_fix.apply_fix(
        approval_fix.resolve_home(codex_home),
        write=True,
    )
    return {
        **result,
        "transport_bridge": bridge_status
        or {
            "required": False,
            "running": bridge.is_healthy(codex_home=codex_home),
        },
        "external_provider_approval_compat": approval,
    }


def _safe_status(registry, codex_home=None):
    result = _original_status(registry, codex_home)
    paths = manager.resolve_paths(codex_home)
    try:
        config = multi._read_config(paths)
    except manager.ManagerError:
        config = {}

    checks = result.get("checks") or {}
    provider_checks = checks.get("providers") or {}
    actual_providers = config.get("model_providers") or {}
    for provider_id, spec in registry.providers.items():
        item = provider_checks.get(provider_id)
        if not isinstance(item, dict):
            continue
        actual = actual_providers.get(provider_id) or {}
        item["base_url"] = actual.get("base_url") == effective_provider_base_url(
            provider_id,
            spec,
            _ACTIVE_STATE,
        )
        item["transport"] = provider_transport(_ACTIVE_STATE, provider_id)

    bridge_needed = _bridge_required(registry, _ACTIVE_STATE)
    bridge_running = bridge.is_healthy(codex_home=codex_home)
    checks["transport_bridge_required"] = bridge_needed
    checks["transport_bridge_running"] = bridge_running

    compatibility = _ACTIVE_STATE.get("tool_compatibility") or {}
    for role, agent_item in (checks.get("agents") or {}).items():
        if not isinstance(agent_item, dict):
            continue
        agent_item["request_profile"] = agent_request_profile(_ACTIVE_STATE, role)
        agent_item["tool_compatibility"] = compatibility.get(
            role,
            {"status": "unknown"},
        )

    ready = (
        checks.get("config_valid") is True
        and checks.get("catalog_selected") is True
        and checks.get("catalog_valid") is True
        and checks.get("injected_models_hidden") is True
        and (not bridge_needed or bridge_running)
        and all(
            item.get("registered")
            and item.get("base_url")
            and item.get("wire_api")
            and item.get("credential_present")
            for item in provider_checks.values()
        )
        and all(
            item.get("exists") and item.get("content_valid")
            for item in (checks.get("agents") or {}).values()
        )
    )
    result["status"] = "configured" if ready else "partial"

    approval = approval_fix.apply_fix(
        approval_fix.resolve_home(codex_home),
        write=False,
    )
    result["external_provider_approval_compat"] = approval
    if approval.get("status") == "would_patch":
        warnings = list(result.get("warnings") or [])
        warnings.append(
            "检测到 external Provider，但 approvals_reviewer 仍不是 user；"
            "Auto-review 可能通过第三方 Provider 请求 codex-auto-review。运行 repair 应用兼容修复。"
        )
        result["warnings"] = warnings
        result["status"] = "partial"
    elif approval.get("status") in {"would_restore", "would_clean"}:
        warnings = list(result.get("warnings") or [])
        warnings.append("审批兼容状态可清理；运行 repair 恢复此前 reviewer。")
        result["warnings"] = warnings
        result["status"] = "partial"
    return result


# The lower manager still owns registry/catalog/agent transactions. These two
# hooks only change the runtime Provider base URL and add the approval policy
# compatibility layer.
multi.render_provider_block = _render_provider_block
multi.reconcile = _safe_reconcile
multi.status = _safe_status


def _responses_endpoint(
    provider_id: str,
    spec,
    state: dict[str, Any],
    codex_home: str | None,
) -> str:
    if provider_transport(state, provider_id) == TRANSPORT_BRIDGE:
        try:
            bridge.ensure_running(codex_home)
        except bridge.BridgeError as exc:
            raise manager.ManagerError(exc.code, str(exc)) from exc
        return bridge.provider_base_url(provider_id).rstrip("/") + "/responses"
    return spec.base_url.rstrip("/") + "/responses"


def _post_responses(
    endpoint: str,
    api_key: str,
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool, dict[str, Any]]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
            "User-Agent": "codex-subagent-tool-probe/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            raw = response.read()
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")[-5000:]
        raise manager.ManagerError(
            "tool_probe_http_error",
            f"Tool Call probe HTTP {exc.code}：{error_body}",
            {"http_status": exc.code},
        ) from exc
    except urllib.error.URLError as exc:
        raise manager.ManagerError(
            "tool_probe_unreachable",
            f"Tool Call probe 无法连接：{exc.reason}",
        ) from exc

    text = raw.decode("utf-8", errors="replace")
    is_sse = "text/event-stream" in content_type or any(
        line.startswith("data:") for line in text.splitlines()
    )
    if is_sse:
        items: list[dict[str, Any]] = []
        completed: dict[str, Any] | None = None
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if (
                event.get("type") == "response.output_item.done"
                and isinstance(event.get("item"), dict)
            ):
                items.append(event["item"])
            if (
                event.get("type") == "response.completed"
                and isinstance(event.get("response"), dict)
            ):
                completed = event["response"]
        if completed is None:
            raise manager.ManagerError(
                "tool_probe_stream_incomplete",
                "Responses 流未返回 response.completed。",
            )
        return items, True, completed

    try:
        response_payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise manager.ManagerError(
            "tool_probe_invalid_response",
            "Tool Call probe 返回非 JSON/非 SSE。",
        ) from exc
    if not isinstance(response_payload, dict):
        raise manager.ManagerError(
            "tool_probe_invalid_response",
            "Tool Call probe 响应根对象无效。",
        )
    items = [
        item
        for item in response_payload.get("output") or []
        if isinstance(item, dict)
    ]
    return items, False, response_payload


def _message_text(items: list[dict[str, Any]]) -> str:
    pieces: list[str] = []
    for item in items:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                pieces.append(part["text"])
    return "\n".join(pieces).strip()


def _tool_call_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if item.get("type") in {"function_call", "custom_tool_call"}
    ]


def tool_compatibility_test(
    agent,
    registry,
    state: dict[str, Any],
    codex_home: str | None = None,
) -> dict[str, Any]:
    provider = registry.providers[agent.provider]
    transport = provider_transport(state, agent.provider)
    request_profile = agent_request_profile(state, agent.role)
    tested_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    stages = {
        "first_response": False,
        "streaming": False,
        "tool_call": False,
        "arguments_json": False,
        "tool_result_continuation": False,
    }
    result: dict[str, Any] = {
        "status": "fail",
        "transport": transport,
        "request_profile": request_profile,
        "tested_at": tested_at,
        "stages": stages,
    }

    try:
        api_key = multi.read_credential(provider)
        if not api_key:
            raise manager.ManagerError(
                "credential_missing",
                f"Provider {agent.provider} 没有 API Key。",
            )
        endpoint = _responses_endpoint(
            agent.provider,
            provider,
            state,
            codex_home,
        )

        tool = {
            "type": "function",
            "name": "codex_subagent_probe",
            "description": "Compatibility probe. Return the supplied integer value.",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            "strict": True,
        }
        user_item = {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": (
                        "Call codex_subagent_probe exactly once with value 7. "
                        "After the tool result is returned, reply exactly TOOL_PROBE_OK and nothing else."
                    ),
                }
            ],
        }

        first_request = {
            "model": agent.model,
            "input": [user_item],
            "tools": [tool],
            "tool_choice": {
                "type": "function",
                "name": "codex_subagent_probe",
            },
            "parallel_tool_calls": False,
            "stream": True,
            "reasoning": {"effort": agent.reasoning_effort},
        }
        first_items, first_streamed, _ = _post_responses(
            endpoint,
            api_key,
            first_request,
        )
        stages["first_response"] = True
        if not first_streamed:
            raise manager.ManagerError(
                "tool_probe_no_stream",
                "stream=true 但 Provider 没有返回 Responses SSE。",
            )

        calls = _tool_call_items(first_items)
        if len(calls) != 1:
            raise manager.ManagerError(
                "tool_probe_call_count",
                f"预期恰好一个 Tool Call，实际 {len(calls)} 个。",
            )
        call = calls[0]
        stages["tool_call"] = True
        if call.get("name") != "codex_subagent_probe":
            raise manager.ManagerError(
                "tool_probe_wrong_tool",
                f"模型调用了错误工具：{call.get('name')}",
            )

        try:
            arguments = json.loads(str(call.get("arguments") or "{}"))
        except json.JSONDecodeError as exc:
            raise manager.ManagerError(
                "tool_probe_bad_arguments",
                "Tool Call arguments 不是合法 JSON。",
            ) from exc
        if not isinstance(arguments, dict) or arguments.get("value") != 7:
            raise manager.ManagerError(
                "tool_probe_bad_arguments",
                f"Tool Call arguments 不符合预期：{arguments}",
            )
        stages["arguments_json"] = True

        continuation_items = [
            item
            for item in first_items
            if item.get("type")
            in {"reasoning", "function_call", "custom_tool_call"}
        ]
        tool_output = {
            "type": "function_call_output",
            "call_id": call.get("call_id"),
            "output": "probe-result-7",
        }
        second_request = {
            "model": agent.model,
            "input": [user_item, *continuation_items, tool_output],
            "tools": [tool],
            "tool_choice": "none",
            "parallel_tool_calls": False,
            "stream": True,
            "reasoning": {"effort": agent.reasoning_effort},
        }
        second_items, second_streamed, _ = _post_responses(
            endpoint,
            api_key,
            second_request,
        )
        if not second_streamed:
            raise manager.ManagerError(
                "tool_probe_no_stream_continuation",
                "Tool Result continuation 没有返回 Responses SSE。",
            )
        stages["streaming"] = True

        final_text = _message_text(second_items)
        if final_text.strip() != "TOOL_PROBE_OK":
            raise manager.ManagerError(
                "tool_probe_bad_continuation",
                f"Tool Result 后续响应不符合预期：{final_text!r}",
            )
        stages["tool_result_continuation"] = True
        result["status"] = "pass"
        result["endpoint_kind"] = transport
        return result
    except manager.ManagerError as exc:
        result["error"] = {
            "code": exc.code,
            "message": str(exc),
            **exc.details,
        }
        return result
    except Exception as exc:
        result["error"] = {
            "code": "tool_probe_failed",
            "message": f"{type(exc).__name__}: {exc}",
        }
        return result


def _annotate_list(
    payload: dict[str, Any],
    registry,
    state: dict[str, Any],
    codex_home: str | None,
) -> dict[str, Any]:
    compatibility = state.get("tool_compatibility") or {}
    for provider in payload.get("providers") or []:
        provider_id = provider.get("provider")
        spec = registry.providers.get(provider_id)
        if not provider_id or spec is None:
            continue
        provider["transport"] = provider_transport(state, provider_id)
        provider["runtime_base_url"] = effective_provider_base_url(
            provider_id,
            spec,
            state,
        )
    for agent in payload.get("agents") or []:
        role = agent.get("role")
        if not role:
            continue
        agent["request_profile"] = agent_request_profile(state, role)
        agent["tool_compatibility"] = compatibility.get(
            role,
            {"status": "unknown"},
        )

    payload["transport_bridge"] = {
        "required": _bridge_required(registry, state),
        "running": bridge.is_healthy(codex_home=codex_home),
        "port": bridge.bridge_port(),
    }
    return payload


def _subparsers(parser: argparse.ArgumentParser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("subparser action missing")


def build_parser() -> argparse.ArgumentParser:
    parser = multi.build_parser()
    sub = _subparsers(parser)

    sub.choices["provider-add"].add_argument(
        "--transport",
        choices=TRANSPORTS,
        default=TRANSPORT_RESPONSES,
    )
    sub.choices["provider-update"].add_argument(
        "--transport",
        choices=TRANSPORTS,
    )
    sub.choices["agent-add"].add_argument(
        "--request-profile",
        choices=REQUEST_PROFILES,
        default="auto",
    )
    sub.choices["agent-update"].add_argument(
        "--request-profile",
        choices=REQUEST_PROFILES,
    )

    tool_test = sub.add_parser("tool-test")
    group = tool_test.add_mutually_exclusive_group(required=True)
    group.add_argument("--role")
    group.add_argument("--all", action="store_true")

    sub.add_parser("bridge-status")
    sub.add_parser("bridge-start")
    sub.add_parser("bridge-stop")
    return parser


def _invalidate_provider_agents(
    provider_id: str,
    registry,
    state: dict[str, Any],
) -> None:
    compatibility = state.setdefault("tool_compatibility", {})
    for role, agent in registry.agents.items():
        if agent.provider == provider_id:
            compatibility.pop(role, None)


def _update_state_after_mutation(
    args,
    mutation: dict[str, Any],
    registry,
    state: dict[str, Any],
) -> None:
    providers = state.setdefault("providers", {})
    agents = state.setdefault("agents", {})
    compatibility = state.setdefault("tool_compatibility", {})

    if args.command == "provider-add":
        providers[args.provider] = {"transport": args.transport}
        return

    if args.command == "provider-update":
        if args.transport is not None:
            previous = provider_transport(state, args.provider)
            providers.setdefault(args.provider, {})["transport"] = args.transport
            if previous != args.transport:
                _invalidate_provider_agents(args.provider, registry, state)
        return

    if args.command == "provider-remove":
        providers.pop(args.provider, None)
        for role in mutation.get("removed_agents") or []:
            agents.pop(role, None)
            compatibility.pop(role, None)
        return

    if args.command == "agent-add":
        role = mutation["agent"]["role"]
        agents[role] = {"request_profile": args.request_profile}
        compatibility.pop(role, None)
        return

    if args.command == "agent-update":
        previous_role = mutation.get("previous_role")
        current_role = mutation["agent"]["role"]
        entry = agents.pop(previous_role, {"request_profile": "auto"})
        compatibility.pop(previous_role, None)
        if args.request_profile is not None:
            entry["request_profile"] = args.request_profile
        agents[current_role] = entry
        compatibility.pop(current_role, None)
        return

    if args.command == "agent-remove":
        agents.pop(args.role, None)
        compatibility.pop(args.role, None)


def _run_tool_tests(
    roles: list[str],
    registry,
    state: dict[str, Any],
    codex_home: str | None,
) -> tuple[dict[str, Any], bool]:
    results: dict[str, Any] = {}
    all_pass = True
    for role in roles:
        agent = registry.agents.get(role)
        if agent is None:
            raise manager.ManagerError(
                "agent_missing",
                f"Agent 不存在：{role}",
            )
        probe = tool_compatibility_test(
            agent,
            registry,
            state,
            codex_home,
        )
        state.setdefault("tool_compatibility", {})[role] = probe
        results[role] = probe
        all_pass = all_pass and probe.get("status") == "pass"
    save_transport_state(state, codex_home)
    return results, all_pass


def main() -> int:
    global _ACTIVE_STATE
    args = build_parser().parse_args()
    try:
        if args.command == "_credential-get":
            provider = multi.ProviderSpec(
                provider="internal",
                provider_name="internal",
                base_url="https://invalid.local/",
                credential_target=args.target,
            )
            secret = multi.read_credential(provider)
            if not secret:
                return 2
            sys.stdout.write(secret)
            return 0

        registry, migrated = multi.load_registry(
            args.codex_home,
            migrate_legacy=True,
        )
        state = load_transport_state(registry, args.codex_home)
        _ACTIVE_STATE = state

        if args.command == "bridge-status":
            payload = {
                "status": "ok",
                "running": bridge.is_healthy(codex_home=args.codex_home),
                "port": bridge.bridge_port(),
                "health": bridge.health_payload(),
            }
        elif args.command == "bridge-start":
            payload = {
                "status": "ok",
                **bridge.ensure_running(args.codex_home),
            }
        elif args.command == "bridge-stop":
            payload = {
                "status": "ok",
                **bridge.stop(args.codex_home),
            }
        elif args.command == "list":
            payload = _annotate_list(
                multi.list_payload(registry),
                registry,
                state,
                args.codex_home,
            )
        elif args.command == "status":
            payload = multi.status(registry, args.codex_home)
            payload["legacy_migration_available"] = migrated
        elif args.command == "migrate":
            if not migrated and multi.registry_path(args.codex_home).is_file():
                save_transport_state(state, args.codex_home)
                payload = {
                    "status": "ok",
                    "message": "registry 已存在；transport state 已初始化。",
                }
            elif not migrated:
                save_transport_state(state, args.codex_home)
                payload = {
                    "status": "ok",
                    "message": "没有检测到旧单 Profile 配置。",
                }
            else:
                result = multi.reconcile(registry, args.codex_home)
                payload = {
                    "status": "configured",
                    "migrated": True,
                    **result,
                }
        elif args.command == "repair":
            result = multi.reconcile(registry, args.codex_home)
            payload = {"status": "configured", **result}
        elif args.command == "tool-test":
            roles = list(registry.agents) if args.all else [args.role]
            results, all_pass = _run_tool_tests(
                roles,
                registry,
                state,
                args.codex_home,
            )
            payload = {
                "status": "compatible" if all_pass else "incompatible",
                "tool_tests": results,
            }
        elif args.command == "test":
            roles = list(registry.agents) if args.all else [args.role]
            tool_results, all_tools_pass = _run_tool_tests(
                roles,
                registry,
                state,
                args.codex_home,
            )
            tests: dict[str, Any] = {}
            for role in roles:
                agent = registry.agents.get(role)
                if agent is None:
                    raise manager.ManagerError(
                        "agent_missing",
                        f"Agent 不存在：{role}",
                    )
                tests[role] = {
                    **multi.direct_test(agent, args.codex_home),
                    "tool_compatibility": tool_results[role],
                    **multi.native_test(agent, args.codex_home),
                }
            payload = {
                "status": "ok" if all_tools_pass else "incompatible",
                "tests": tests,
            }
        else:
            if args.command == "provider-add":
                mutation = multi.add_provider(args, registry)
            elif args.command == "provider-update":
                mutation = multi.update_provider(args, registry)
            elif args.command == "provider-remove":
                mutation = multi.remove_provider_cmd(args, registry)
            elif args.command == "agent-add":
                mutation = multi.add_agent(args, registry)
            elif args.command == "agent-update":
                mutation = multi.update_agent(args, registry)
            elif args.command == "agent-remove":
                mutation = multi.remove_agent_cmd(args, registry)
            else:
                raise manager.ManagerError(
                    "invalid_command",
                    f"未知命令：{args.command}",
                )

            _update_state_after_mutation(
                args,
                mutation,
                registry,
                state,
            )
            _ACTIVE_STATE = state
            result = multi.reconcile(registry, args.codex_home)
            payload = {
                "status": "configured",
                **mutation,
                **result,
                "migrated_legacy": migrated,
            }

        manager.emit(payload, args.json)
        return 0 if payload.get("status") not in {
            "partial",
            "failed",
            "incompatible",
        } else 2
    except bridge.BridgeError as exc:
        manager.emit(
            manager.result(exc.code, message=str(exc)),
            getattr(args, "json", False),
        )
        return 2
    except manager.ManagerError as exc:
        manager.emit(
            manager.result(exc.code, message=str(exc), **exc.details),
            getattr(args, "json", False),
        )
        return 2
    except subprocess.TimeoutExpired:
        manager.emit(
            manager.result("timeout", message="操作超时。"),
            getattr(args, "json", False),
        )
        return 3
    except Exception as exc:
        manager.emit(
            manager.result(
                "failed",
                message=f"{type(exc).__name__}: {exc}",
            ),
            getattr(args, "json", False),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
