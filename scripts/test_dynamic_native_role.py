#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "codex-deepseek-subagent" / "scripts" / "codex_provider_manager.py"


def load_module():
    name = "codex_provider_manager_native_role_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class DynamicNativeRoleTests(unittest.TestCase):
    def test_native_test_uses_profile_role(self) -> None:
        module = load_module()
        profile = module.ProviderProfile(
            mode="custom",
            base_url="https://relay.example/v1/",
            model="gpt-5.6-luna",
            provider="relay",
            role="Luna",
            reasoning_effort="ultra",
        )
        module.apply_profile(profile)

        stdout = "\n".join(
            [
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "collab_tool_call",
                            "tool": "spawn_agent",
                            "receiver_thread_ids": ["child-luna"],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "collab_tool_call",
                            "tool": "wait",
                            "agents_states": {
                                "child-luna": {
                                    "status": "completed",
                                    "message": "NATIVE_DEEPSEEK_OK",
                                }
                            },
                        },
                    }
                ),
            ]
        )
        proc = SimpleNamespace(returncode=0, stdout=stdout, stderr="")
        expected = {
            "model_provider": "relay",
            "model": "gpt-5.6-luna",
            "reasoning_effort": "ultra",
            "agent_role": "Luna",
        }

        with tempfile.TemporaryDirectory() as directory:
            paths = module.manager.resolve_paths(directory)
            paths.config.parent.mkdir(parents=True, exist_ok=True)
            paths.config.write_text('model = "gpt-5.6-sol"\n', encoding="utf-8")
            with mock.patch.object(
                module.manager,
                "choose_parent_model",
                return_value="gpt-5.6-sol",
            ), mock.patch.object(
                module.manager.subprocess,
                "run",
                return_value=proc,
            ) as run, mock.patch.object(
                module.manager,
                "wait_for_child_metadata",
                return_value=expected,
            ):
                result = module.manager.native_test(paths, "codex")

        prompt = run.call_args.args[0][-1]
        self.assertIn("agent_type to Luna", prompt)
        self.assertNotIn("agent_type to DeepSeek", prompt)
        self.assertEqual(result["agent_role"], "Luna")
        self.assertEqual(result["model"], "gpt-5.6-luna")


if __name__ == "__main__":
    unittest.main()
