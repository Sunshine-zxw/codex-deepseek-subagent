#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "codex-deepseek-subagent" / "scripts" / "external_provider_approval_fix.py"
spec = importlib.util.spec_from_file_location("approval_fix_under_test", SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError(SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def write_registry(home: Path, *, backend: str = "external") -> None:
    state = home / "codex-deepseek-subagent"
    state.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "providers": {
            "relay": {
                "provider": "relay",
                "provider_name": "Relay",
                "base_url": "https://relay.example/v1/",
                "backend": backend,
                "multi_agent_version": "auto",
                "credential_target": "test",
            }
        },
        "agents": {
            "Luna": {
                "role": "Luna",
                "provider": "relay",
                "model": "gpt-5.6-luna",
                "reasoning_effort": "high",
                "reasoning_efforts": None,
                "role_auto": True,
            }
        },
        "metadata": {},
    }
    (state / "registry.json").write_text(json.dumps(payload), encoding="utf-8")


def test_external_provider_routes_review_to_user_without_changing_sandbox() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        write_registry(home, backend="external")
        (home / "config.toml").write_text(
            'model = "gpt-5.6-sol"\n'
            'approvals_reviewer = "auto_review"\n'
            'sandbox_mode = "workspace-write"\n'
            '\n[features]\n'
            'multi_agent_v2 = false\n',
            encoding="utf-8",
        )
        result = mod.apply_fix(home)
        assert result["status"] == "patched"
        parsed = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
        assert parsed["approvals_reviewer"] == "user"
        assert parsed["sandbox_mode"] == "workspace-write"
        assert "approval_policy" not in parsed
        state = json.loads(
            (home / "codex-deepseek-subagent" / "approval-compat.json").read_text(encoding="utf-8")
        )
        assert state["previous_approvals_reviewer"] == "auto_review"


def test_openai_backend_does_not_modify_reviewer() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        write_registry(home, backend="openai")
        (home / "config.toml").write_text(
            'model = "gpt-5.6-sol"\napprovals_reviewer = "auto_review"\n',
            encoding="utf-8",
        )
        result = mod.apply_fix(home)
        assert result["status"] == "noop"
        parsed = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
        assert parsed["approvals_reviewer"] == "auto_review"


def test_check_mode_reports_without_writing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        write_registry(home, backend="external")
        original = 'model = "gpt-5.6-sol"\napprovals_reviewer = "auto_review"\n'
        (home / "config.toml").write_text(original, encoding="utf-8")
        result = mod.apply_fix(home, write=False)
        assert result["status"] == "would_patch"
        assert (home / "config.toml").read_text(encoding="utf-8") == original


def main() -> None:
    test_external_provider_routes_review_to_user_without_changing_sandbox()
    test_openai_backend_does_not_modify_reviewer()
    test_check_mode_reports_without_writing()
    print("external provider approval compatibility tests passed")


if __name__ == "__main__":
    main()
