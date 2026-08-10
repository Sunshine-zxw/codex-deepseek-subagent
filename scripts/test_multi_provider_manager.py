#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "codex-deepseek-subagent" / "scripts" / "codex_subagent_manager.py"

spec = importlib.util.spec_from_file_location("codex_subagent_manager", SCRIPT)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
assert spec.loader is not None
spec.loader.exec_module(module)


class MultiProviderManagerTests(unittest.TestCase):
    def test_new_agents_default_to_max_reasoning_effort(self):
        args = module.build_parser().parse_args(
            ["agent-add", "--provider", "relay", "--model", "gpt-5.6-luna"]
        )
        self.assertEqual(args.reasoning_effort, "max")

    def provider(
        self,
        provider: str,
        *,
        backend: str = "external",
        multi_agent_version: str = "auto",
    ):
        return module.validate_provider(
            module.ProviderSpec(
                provider=provider,
                provider_name=provider,
                base_url=f"https://{provider}.example/v1",
                backend=backend,
                multi_agent_version=multi_agent_version,
            )
        )

    def agent(
        self,
        role: str,
        provider: str,
        model: str,
        effort: str = "high",
        efforts=None,
    ):
        return module.AgentSpec(
            role=role,
            provider=provider,
            model=model,
            reasoning_effort=effort,
            reasoning_efforts=efforts,
        )

    def test_any_v1_provider_keeps_parent_on_v1(self):
        registry = module.Registry(
            providers={
                "relay": self.provider("relay"),
                "openai_proxy": self.provider(
                    "openai_proxy", backend="openai", multi_agent_version="auto"
                ),
            },
            agents={
                "Luna": self.agent("Luna", "relay", "relay-luna"),
                "Terra": self.agent("Terra", "openai_proxy", "relay-terra"),
            },
        )
        self.assertEqual(module.global_multi_agent_version(registry), "v1")

    def test_all_v2_providers_allow_parent_v2(self):
        registry = module.Registry(
            providers={
                "a": self.provider("a", backend="openai"),
                "b": self.provider("b", backend="external", multi_agent_version="v2"),
            },
            agents={
                "Luna": self.agent("Luna", "a", "luna"),
                "Terra": self.agent("Terra", "b", "terra"),
            },
        )
        self.assertEqual(module.global_multi_agent_version(registry), "v2")

    def test_multiple_agents_can_share_one_provider(self):
        provider = self.provider("relay")
        registry = module.Registry(
            providers={"relay": provider},
            agents={
                "Luna": self.agent("Luna", "relay", "gpt-5.6-luna"),
                "Terra": self.agent("Terra", "relay", "gpt-5.6-terra"),
            },
        )
        for agent in registry.agents.values():
            validated = module.validate_agent(agent, registry)
            self.assertEqual(validated.provider, "relay")

    def test_unknown_subagent_model_is_hidden_from_picker(self):
        provider = self.provider("relay")
        registry = module.Registry(
            providers={"relay": provider},
            agents={"Luna": self.agent("Luna", "relay", "relay-luna")},
        )
        source = {
            "models": [
                {
                    "slug": "gpt-5.6-sol",
                    "display_name": "GPT-5.6 Sol",
                    "visibility": "list",
                    "supported_reasoning_levels": [{"effort": "high", "description": "High"}],
                }
            ]
        }
        catalog, injected = module.build_catalog(source, registry, "gpt-5.6-sol")
        child = next(item for item in catalog["models"] if item["slug"] == "relay-luna")
        self.assertEqual(child["visibility"], "hide")
        self.assertEqual(injected, {"relay-luna"})

    def test_existing_native_model_keeps_picker_visibility(self):
        provider = self.provider("relay")
        registry = module.Registry(
            providers={"relay": provider},
            agents={"Luna": self.agent("Luna", "relay", "gpt-5.6-luna")},
        )
        source = {
            "models": [
                {
                    "slug": "gpt-5.6-sol",
                    "display_name": "GPT-5.6 Sol",
                    "visibility": "list",
                    "supported_reasoning_levels": [],
                },
                {
                    "slug": "gpt-5.6-luna",
                    "display_name": "GPT-5.6 Luna",
                    "visibility": "list",
                    "supported_reasoning_levels": [],
                },
            ]
        }
        catalog, injected = module.build_catalog(source, registry, "gpt-5.6-sol")
        luna = next(item for item in catalog["models"] if item["slug"] == "gpt-5.6-luna")
        self.assertEqual(luna["visibility"], "list")
        self.assertEqual(injected, set())

    def test_custom_reasoning_effort_is_preserved(self):
        registry = module.Registry(providers={"relay": self.provider("relay")})
        agent = module.validate_agent(
            module.AgentSpec(
                role="Luna",
                provider="relay",
                model="relay-luna",
                reasoning_effort="adaptive-high",
                reasoning_efforts=("fast", "adaptive-high", "deep"),
            ),
            registry,
        )
        self.assertEqual(agent.reasoning_effort, "adaptive-high")
        self.assertIn("deep", agent.reasoning_efforts)

    def test_provider_remove_refuses_when_agents_still_reference_it(self):
        registry = module.Registry(
            providers={"relay": self.provider("relay")},
            agents={"Luna": self.agent("Luna", "relay", "relay-luna")},
        )
        args = SimpleNamespace(provider="relay", cascade=False, remove_credential=False)
        with self.assertRaises(module.manager.ManagerError) as caught:
            module.remove_provider_cmd(args, registry)
        self.assertEqual(caught.exception.code, "provider_in_use")

    def test_provider_block_contains_multiple_providers(self):
        registry = module.Registry(
            providers={"relay_a": self.provider("relay_a"), "relay_b": self.provider("relay_b")}
        )
        with mock.patch.object(
            module.manager, "credential_backend", return_value="windows-credential-manager"
        ):
            text = module.render_provider_block(registry)
        self.assertIn("[model_providers.relay_a]", text)
        self.assertIn("[model_providers.relay_b]", text)
        self.assertIn("_credential-get", text)

    def test_registry_payload_separates_providers_and_agents(self):
        registry = module.Registry(
            providers={"relay": self.provider("relay")},
            agents={"Luna": self.agent("Luna", "relay", "relay-luna")},
            injected_models={"relay-luna"},
        )
        payload = module.registry_to_payload(registry)
        self.assertIn("relay", payload["providers"])
        self.assertIn("Luna", payload["agents"])
        self.assertEqual(payload["agents"]["Luna"]["provider"], "relay")
        self.assertEqual(payload["metadata"]["injected_models"], ["relay-luna"])


if __name__ == "__main__":
    unittest.main()
