#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = ROOT / "codex-deepseek-subagent" / "scripts" / "codex_transport_bridge.py"
CLI_PATH = ROOT / "codex-deepseek-subagent" / "scripts" / "codex_subagent_cli.py"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bridge = load("transport_bridge_test_impl", BRIDGE_PATH)
cli = load("subagent_cli_transport_test_impl", CLI_PATH)


def test_auto_profile_is_conservative() -> None:
    assert bridge.effective_request_profile("auto") == "default"
    assert bridge.effective_request_profile("auto-tool-choice") == "auto-tool-choice"
    assert bridge.effective_request_profile("deepseek-thinking") == "deepseek-thinking"


def test_deepseek_tool_choice_and_reasoning_roundtrip() -> None:
    payload = {
        "model": "deepseek-v4-flash",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "use the tool"}],
            }
        ],
        "tools": [
            {
                "type": "function",
                "name": "probe",
                "description": "probe",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                },
            }
        ],
        "tool_choice": {"type": "function", "name": "probe"},
        "reasoning": {"effort": "max"},
    }
    chat, mapping = bridge.responses_to_chat(payload, "deepseek-thinking")
    assert chat["tool_choice"] == "auto"
    assert chat["thinking"] == {"type": "enabled"}
    assert chat["reasoning_effort"] == "max"
    assert mapping["probe"]["name"] == "probe"

    items = bridge.chat_message_to_items(
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "private chain for continuation",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "probe", "arguments": '{"value":7}'},
                }
            ],
        },
        mapping,
    )
    assert items[0]["type"] == "reasoning"
    assert items[0]["content"][0]["text"] == "private chain for continuation"
    assert items[1]["type"] == "function_call"
    assert items[1]["call_id"] == "call_1"

    history = bridge.translate_input(
        [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "use"}],
            },
            items[0],
            items[1],
            {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
        ],
        mapping,
    )
    assistant = next(row for row in history if row["role"] == "assistant")
    assert assistant["reasoning_content"] == "private chain for continuation"
    assert assistant["tool_calls"][0]["id"] == "call_1"
    tool_row = next(row for row in history if row["role"] == "tool")
    assert tool_row["tool_call_id"] == "call_1"


def test_auto_tool_choice_does_not_inject_deepseek_thinking() -> None:
    payload = {
        "model": "deepseek-v4-flash",
        "input": "use a tool",
        "tools": [
            {
                "type": "function",
                "name": "probe",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
        "tool_choice": {"type": "function", "name": "probe"},
        "reasoning": {"effort": "high"},
    }
    chat, _ = bridge.responses_to_chat(payload, "auto-tool-choice")
    assert chat["tool_choice"] == "auto"
    assert "thinking" not in chat


def test_custom_tool_translation() -> None:
    tools, mapping = bridge.translate_tools(
        [{"type": "custom", "name": "shell", "description": "run shell text"}]
    )
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["parameters"]["required"] == ["input"]
    items = bridge.chat_message_to_items(
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_shell",
                    "type": "function",
                    "function": {"name": "shell", "arguments": '{"input":"pwd"}'},
                }
            ],
        },
        mapping,
    )
    assert items[0]["type"] == "custom_tool_call"
    assert items[0]["input"] == "pwd"


def test_sse_contains_codex_terminal_events() -> None:
    response = {
        "id": "resp_test",
        "output": [
            {
                "type": "message",
                "id": "msg_test",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "ok"}],
            }
        ],
        "usage": {
            "input_tokens": 1,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 1,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 2,
        },
    }
    events = bridge.response_sse_events(response)
    assert events[0]["type"] == "response.created"
    assert any(event["type"] == "response.output_item.done" for event in events)
    assert not any(event["type"] == "response.output_text.delta" for event in events)
    assert events[-1]["type"] == "response.completed"


def _write_registry_and_state(home: Path, *, model: str = "deepseek-v4-flash") -> None:
    state_dir = home / "codex-deepseek-subagent"
    state_dir.mkdir(parents=True)
    (state_dir / "registry.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "providers": {
                    "go": {
                        "provider": "go",
                        "provider_name": "opencode Go",
                        "base_url": "https://opencode.ai/zen/go/v1/",
                        "backend": "external",
                        "multi_agent_version": "auto",
                        "credential_target": "x",
                    }
                },
                "agents": {
                    "DeepSeek": {
                        "role": "DeepSeek",
                        "provider": "go",
                        "model": model,
                        "reasoning_effort": "high",
                        "reasoning_efforts": None,
                        "role_auto": True,
                    }
                },
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    (state_dir / "transport-state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "providers": {"go": {"transport": "chat_completions_bridge"}},
                "agents": {"DeepSeek": {"request_profile": "auto-tool-choice"}},
                "tool_compatibility": {},
            }
        ),
        encoding="utf-8",
    )


def test_sidecar_transport_state_resolution() -> None:
    with tempfile.TemporaryDirectory() as temp:
        home = Path(temp)
        _write_registry_and_state(home)
        provider, profile = bridge.provider_for_request(
            "go",
            "deepseek-v4-flash",
            str(home),
        )
        assert provider["base_url"].startswith("https://opencode.ai/")
        assert profile == "auto-tool-choice"


def test_bridge_rejects_unregistered_model() -> None:
    with tempfile.TemporaryDirectory() as temp:
        home = Path(temp)
        _write_registry_and_state(home)
        try:
            bridge.provider_for_request("go", "not-registered", str(home))
        except bridge.BridgeError as exc:
            assert exc.code == "model_not_registered"
        else:
            raise AssertionError("unregistered model should be rejected")


def test_cli_transport_options() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "provider-add",
            "--provider",
            "go",
            "--base-url",
            "https://example.test/v1",
            "--transport",
            "chat_completions_bridge",
        ]
    )
    assert args.transport == "chat_completions_bridge"

    args = parser.parse_args(
        [
            "agent-add",
            "--provider",
            "go",
            "--model",
            "deepseek-v4-flash",
            "--request-profile",
            "deepseek-thinking",
        ]
    )
    assert args.request_profile == "deepseek-thinking"

    args = parser.parse_args(["tool-test", "--role", "DeepSeek"])
    assert args.command == "tool-test"
    assert args.role == "DeepSeek"


def main() -> None:
    tests = [
        test_auto_profile_is_conservative,
        test_deepseek_tool_choice_and_reasoning_roundtrip,
        test_auto_tool_choice_does_not_inject_deepseek_thinking,
        test_custom_tool_translation,
        test_sse_contains_codex_terminal_events,
        test_sidecar_transport_state_resolution,
        test_bridge_rejects_unregistered_model,
        test_cli_transport_options,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
