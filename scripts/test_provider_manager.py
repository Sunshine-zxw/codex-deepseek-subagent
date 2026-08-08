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
BASE_SCRIPT = ROOT / "codex-deepseek-subagent" / "scripts" / "codex_deepseek.py"

spec = importlib.util.spec_from_file_location("codex_provider_manager", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class ProviderManagerTests(unittest.TestCase):
    def test_auto_uses_v1_for_external_backend(self) -> None:
        profile = module.ProviderProfile(backend="external", multi_agent_version="auto")
        self.assertEqual(profile.effective_multi_agent_version, "v1")

    def test_auto_uses_v2_for_openai_backend(self) -> None:
        profile = module.ProviderProfile(backend="openai", multi_agent_version="auto")
        self.assertEqual(profile.effective_multi_agent_version, "v2")

    def test_explicit_multi_agent_version_wins(self) -> None:
        self.assertEqual(
            module.ProviderProfile(backend="openai", multi_agent_version="v1").effective_multi_agent_version,
            "v1",
        )
        self.assertEqual(
            module.ProviderProfile(backend="external", multi_agent_version="v2").effective_multi_agent_version,
            "v2",
        )

    def test_custom_base_url_is_normalized(self) -> None:
        profile = module.validate_profile(
            module.ProviderProfile(
                mode="custom",
                base_url="https://relay.example/v1",
                provider="deepseek_proxy",
            )
        )
        self.assertEqual(profile.base_url, "https://relay.example/v1/")

    def test_base_url_rejects_embedded_credentials(self) -> None:
        with self.assertRaises(module.manager.ManagerError) as raised:
            module.normalize_base_url("https://user:pass@relay.example/v1")
        self.assertEqual(raised.exception.code, "invalid_base_url")

    def test_base_url_rejects_query_string(self) -> None:
        with self.assertRaises(module.manager.ManagerError) as raised:
            module.normalize_base_url("https://relay.example/v1?route=deepseek")
        self.assertEqual(raised.exception.code, "invalid_base_url")

    def test_reasoning_effort_supports_low_high_max(self) -> None:
        for effort in module.ALLOWED_EFFORTS:
            with self.subTest(effort=effort):
                profile = module.validate_profile(
                    module.ProviderProfile(reasoning_effort=effort)
                )
                self.assertEqual(profile.reasoning_effort, effort)

    def test_reasoning_effort_rejects_unsupported_value(self) -> None:
        with self.assertRaises(module.manager.ManagerError) as raised:
            module.validate_profile(module.ProviderProfile(reasoning_effort="medium"))
        self.assertEqual(raised.exception.code, "invalid_reasoning_effort")

    def test_parser_accepts_max_reasoning_effort(self) -> None:
        args = module.build_parser().parse_args(
            ["repair", "--reasoning-effort", "max"]
        )
        self.assertEqual(args.reasoning_effort, "max")

    def test_custom_credential_target_is_scoped_by_provider_and_url(self) -> None:
        first = module.ProviderProfile(
            mode="custom",
            provider="relay_a",
            base_url="https://a.example/v1/",
        ).credential_target
        second = module.ProviderProfile(
            mode="custom",
            provider="relay_b",
            base_url="https://b.example/v1/",
        ).credential_target
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("codex-deepseek-api-key-"))

    def test_custom_api_key_does_not_require_sk_prefix(self) -> None:
        profile = module.ProviderProfile(
            mode="custom",
            base_url="https://relay.example/v1/",
            provider="relay",
        )
        module.apply_profile(profile)
        with mock.patch.object(
            module.manager,
            "credential_backend",
            return_value="windows-credential-manager",
        ), mock.patch.object(
            module.manager,
            "_windows_store_credential",
        ) as store:
            module.manager.store_credential_key("relay-token-123")
        store.assert_called_once_with("relay-token-123")

    def test_agent_text_uses_profile_model_provider_and_effort(self) -> None:
        profile = module.ProviderProfile(
            mode="custom",
            base_url="https://relay.example/v1/",
            model="relay/deepseek-v4-flash",
            provider="relay",
            reasoning_effort="max",
        )
        module.apply_profile(profile)
        text = module.manager.expected_agent_text()
        self.assertIn('model = "relay/deepseek-v4-flash"', text)
        self.assertIn('model_provider = "relay"', text)
        self.assertIn('model_reasoning_effort = "max"', text)
        self.assertIn("compact handoff", text)
        self.assertIn("resume_agent", text)

    def test_provider_block_uses_custom_base_url(self) -> None:
        profile = module.ProviderProfile(
            mode="custom",
            base_url="https://relay.example/v1/",
            provider="relay",
            provider_name="Relay",
        )
        module.apply_profile(profile)
        with mock.patch.object(
            module.manager,
            "expected_provider_auth",
            return_value={
                "command": "cred-helper",
                "args": ["get"],
                "timeout_ms": 5000,
                "refresh_interval_ms": 0,
            },
        ):
            block = module.manager.managed_provider_block()
        parsed = module.manager.parse_toml_text(block)
        provider = parsed["model_providers"]["relay"]
        self.assertEqual(provider["base_url"], "https://relay.example/v1/")
        self.assertEqual(provider["wire_api"], "responses")

    def test_direct_test_uses_selected_reasoning_effort(self) -> None:
        profile = module.ProviderProfile(
            mode="custom",
            base_url="https://relay.example/v1/",
            model="relay/deepseek-v4-flash",
            provider="relay",
            reasoning_effort="max",
        )
        module.apply_profile(profile)
        with tempfile.TemporaryDirectory() as directory:
            paths = module.manager.resolve_paths(directory)
            proc = SimpleNamespace(
                returncode=0,
                stdout="DEEPSEEK_DIRECT_OK\n",
                stderr="",
            )
            with mock.patch.object(
                module.manager.subprocess,
                "run",
                return_value=proc,
            ) as run:
                result = module.manager.direct_test(paths, "codex")
        argv = run.call_args.args[0]
        self.assertTrue(result["direct"])
        self.assertIn("relay/deepseek-v4-flash", argv)
        self.assertIn('model_provider="relay"', argv)
        self.assertIn('model_reasoning_effort="max"', argv)
        self.assertNotIn('model_reasoning_effort="high"', argv)

    def test_profile_round_trip_persists_reasoning_and_routing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = module.manager.resolve_paths(directory)
            profile = module.ProviderProfile(
                mode="custom",
                base_url="https://relay.example/v1/",
                model="relay/deepseek-v4-flash",
                provider="relay",
                provider_name="Relay",
                reasoning_effort="max",
                multi_agent_version="auto",
                backend="external",
            )
            module.save_profile(paths, profile)
            loaded = module.load_profile(paths)
            payload = json.loads(module.profile_path(paths).read_text())
        self.assertEqual(payload["schema_version"], module.PROFILE_SCHEMA_VERSION)
        self.assertEqual(loaded, profile)
        self.assertEqual(loaded.effective_multi_agent_version, "v1")

    def test_schema_v1_profile_is_still_readable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = module.manager.resolve_paths(directory)
            path = module.profile_path(paths)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "profile": {
                            "mode": "custom",
                            "base_url": "https://relay.example/v1/",
                            "model": "deepseek-v4-flash",
                            "provider": "relay",
                            "provider_name": "Relay",
                            "role": "DeepSeek",
                            "reasoning_effort": "high",
                            "multi_agent_version": "auto",
                            "backend": "external",
                        },
                    }
                ),
                encoding="utf-8",
            )
            loaded = module.load_profile(paths)
        self.assertEqual(loaded.provider, "relay")
        self.assertEqual(loaded.reasoning_effort, "high")

    def test_external_v2_reports_warning_and_resume_policy(self) -> None:
        profile = module.ProviderProfile(
            multi_agent_version="v2",
            backend="external",
        )
        payload = module.enrich({"status": "configured"}, profile)
        self.assertEqual(
            payload["provider_profile"]["resume_policy"],
            "fresh-spawn-handoff",
        )
        self.assertTrue(any("multi-agent v2" in item for item in payload["warnings"]))
        self.assertTrue(any("resume_agent" in item for item in payload["warnings"]))

    def test_windows_lock_dependencies_are_conditionally_imported(self) -> None:
        source = BASE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("try:\n    import fcntl", source)
        self.assertIn("except ImportError:  # Windows", source)
        self.assertIn("try:\n    import msvcrt", source)
        self.assertIn("except ImportError:  # macOS / Linux", source)


if __name__ == "__main__":
    unittest.main()
