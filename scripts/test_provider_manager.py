#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "codex-deepseek-subagent" / "scripts" / "codex_provider_manager.py"
BASE_SCRIPT = ROOT / "codex-deepseek-subagent" / "scripts" / "codex_deepseek.py"


def load_module():
    name = f"codex_provider_manager_test_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ProviderManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_auto_uses_v1_for_external_backend(self) -> None:
        profile = self.module.ProviderProfile(backend="external", multi_agent_version="auto")
        self.assertEqual(profile.effective_multi_agent_version, "v1")

    def test_auto_uses_v2_for_openai_backend(self) -> None:
        profile = self.module.ProviderProfile(backend="openai", multi_agent_version="auto")
        self.assertEqual(profile.effective_multi_agent_version, "v2")

    def test_custom_base_url_is_normalized(self) -> None:
        profile = self.module.validate_profile(
            self.module.ProviderProfile(
                mode="custom",
                base_url="https://relay.example/v1",
                provider="relay",
            )
        )
        self.assertEqual(profile.base_url, "https://relay.example/v1/")

    def test_base_url_rejects_credentials_query_and_fragment(self) -> None:
        invalid = (
            "https://user:pass@relay.example/v1",
            "https://relay.example/v1?route=luna",
            "https://relay.example/v1#fragment",
        )
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(self.module.manager.ManagerError):
                self.module.normalize_base_url(url)

    def test_reasoning_effort_accepts_known_and_custom_values(self) -> None:
        efforts = (
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
            "ultra",
            "turbo-provider-tier",
        )
        for effort in efforts:
            with self.subTest(effort=effort):
                profile = self.module.validate_profile(
                    self.module.ProviderProfile(reasoning_effort=effort)
                )
                self.assertEqual(profile.reasoning_effort, effort)

    def test_reasoning_effort_rejects_empty_or_control_chars(self) -> None:
        for effort in ("", "   ", "high\nmax"):
            with self.subTest(effort=repr(effort)), self.assertRaises(self.module.manager.ManagerError):
                self.module.validate_profile(
                    self.module.ProviderProfile(reasoning_effort=effort)
                )

    def test_reasoning_efforts_are_deduplicated_and_include_selected(self) -> None:
        profile = self.module.validate_profile(
            self.module.ProviderProfile(
                reasoning_effort="ultra",
                reasoning_efforts=("low", "high", "low"),
            )
        )
        self.assertEqual(profile.reasoning_efforts, ("low", "high", "ultra"))

    def test_parser_accepts_custom_reasoning_effort_and_catalog_list(self) -> None:
        args = self.module.build_parser().parse_args(
            [
                "repair",
                "--reasoning-effort",
                "turbo-provider-tier",
                "--reasoning-efforts",
                "low,high,ultra,turbo-provider-tier",
            ]
        )
        self.assertEqual(args.reasoning_effort, "turbo-provider-tier")
        self.assertEqual(
            self.module.parse_reasoning_efforts(args.reasoning_efforts),
            ("low", "high", "ultra", "turbo-provider-tier"),
        )

    def test_infer_role_from_model(self) -> None:
        cases = {
            "gpt-5.6-luna": "Luna",
            "gpt-5.6-terra": "Terra",
            "deepseek-v4-flash": "DeepSeek",
            "vendor/kimi-k3": "Kimi",
            "vendor/custom-worker": "Worker",
        }
        for model, expected in cases.items():
            with self.subTest(model=model):
                self.assertEqual(self.module.infer_role(model), expected)

    def test_first_custom_setup_auto_names_luna(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self.module.manager.resolve_paths(directory)
            args = self.module.build_parser().parse_args(
                [
                    "setup",
                    "--base-url",
                    "https://relay.example/v1",
                    "--model",
                    "gpt-5.6-luna",
                    "--provider",
                    "relay",
                ]
            )
            profile, overridden = self.module.profile_from_args(args, paths)
        self.assertTrue(overridden)
        self.assertEqual(profile.role, "Luna")
        self.assertTrue(profile.role_auto)

    def test_explicit_role_overrides_auto_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self.module.manager.resolve_paths(directory)
            args = self.module.build_parser().parse_args(
                [
                    "setup",
                    "--base-url",
                    "https://relay.example/v1",
                    "--model",
                    "gpt-5.6-luna",
                    "--provider",
                    "relay",
                    "--role",
                    "FastLuna",
                ]
            )
            profile, _ = self.module.profile_from_args(args, paths)
        self.assertEqual(profile.role, "FastLuna")
        self.assertFalse(profile.role_auto)

    def test_agent_name_alias_maps_to_role(self) -> None:
        args = self.module.build_parser().parse_args(
            ["repair", "--agent-name", "Worker"]
        )
        self.assertEqual(args.role, "Worker")

    def test_manual_role_survives_model_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self.module.manager.resolve_paths(directory)
            existing = self.module.ProviderProfile(
                mode="custom",
                base_url="https://relay.example/v1/",
                model="gpt-5.6-luna",
                provider="relay",
                role="Worker",
                role_auto=False,
            )
            self.module.save_profile(paths, existing)
            args = self.module.build_parser().parse_args(
                ["repair", "--model", "gpt-5.6-terra"]
            )
            profile, _ = self.module.profile_from_args(args, paths)
        self.assertEqual(profile.role, "Worker")
        self.assertFalse(profile.role_auto)

    def test_auto_role_tracks_model_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self.module.manager.resolve_paths(directory)
            existing = self.module.ProviderProfile(
                mode="custom",
                base_url="https://relay.example/v1/",
                model="gpt-5.6-luna",
                provider="relay",
                role="Luna",
                role_auto=True,
            )
            self.module.save_profile(paths, existing)
            args = self.module.build_parser().parse_args(
                ["repair", "--model", "gpt-5.6-terra"]
            )
            profile, _ = self.module.profile_from_args(args, paths)
        self.assertEqual(profile.role, "Terra")
        self.assertTrue(profile.role_auto)

    def test_dynamic_role_changes_agent_path(self) -> None:
        profile = self.module.ProviderProfile(
            mode="custom",
            base_url="https://relay.example/v1/",
            model="gpt-5.6-luna",
            provider="relay",
            role="Luna",
        )
        self.module.apply_profile(profile)
        with tempfile.TemporaryDirectory() as directory:
            paths = self.module.manager.resolve_paths(directory)
        self.assertEqual(paths.agent.name, "Luna.toml")

    def test_catalog_preserves_existing_model_and_reasoning_options(self) -> None:
        profile = self.module.ProviderProfile(
            mode="custom",
            base_url="https://relay.example/v1/",
            model="gpt-5.6-luna",
            provider="relay",
            role="Luna",
            reasoning_effort="ultra",
        )
        self.module.apply_profile(profile)
        base = {
            "models": [
                {
                    "slug": "gpt-5.6-sol",
                    "multi_agent_version": "v2",
                },
                {
                    "slug": "gpt-5.6-luna",
                    "display_name": "GPT-5.6 Luna",
                    "default_reasoning_level": "low",
                    "supported_reasoning_levels": [
                        {"effort": "low", "description": "Low"},
                        {"effort": "high", "description": "High"},
                        {"effort": "ultra", "description": "Ultra"},
                    ],
                },
            ]
        }
        merged = self.module.manager.merged_catalog(base, {}, "gpt-5.6-sol")
        by_slug = {item["slug"]: item for item in merged["models"]}
        luna = by_slug["gpt-5.6-luna"]
        self.assertEqual(luna["display_name"], "GPT-5.6 Luna")
        self.assertEqual(luna["default_reasoning_level"], "ultra")
        self.assertEqual(
            [item["effort"] for item in luna["supported_reasoning_levels"]],
            ["low", "high", "ultra"],
        )
        self.assertEqual(by_slug["gpt-5.6-sol"]["multi_agent_version"], "v1")

    def test_catalog_can_override_advertised_reasoning_options(self) -> None:
        profile = self.module.ProviderProfile(
            mode="custom",
            base_url="https://relay.example/v1/",
            model="gpt-5.6-luna",
            provider="relay",
            role="Luna",
            reasoning_effort="turbo",
            reasoning_efforts=("minimal", "medium", "turbo"),
        )
        self.module.apply_profile(profile)
        base = {
            "models": [
                {"slug": "gpt-5.6-sol"},
                {
                    "slug": "gpt-5.6-luna",
                    "supported_reasoning_levels": [
                        {"effort": "low", "description": "Low"},
                        {"effort": "high", "description": "High"},
                    ],
                },
            ]
        }
        merged = self.module.manager.merged_catalog(base, {}, "gpt-5.6-sol")
        luna = next(item for item in merged["models"] if item["slug"] == "gpt-5.6-luna")
        self.assertEqual(
            [item["effort"] for item in luna["supported_reasoning_levels"]],
            ["minimal", "medium", "turbo"],
        )
        self.assertEqual(luna["default_reasoning_level"], "turbo")

    def test_custom_api_key_does_not_require_sk_prefix(self) -> None:
        profile = self.module.ProviderProfile(
            mode="custom",
            base_url="https://relay.example/v1/",
            provider="relay",
        )
        self.module.apply_profile(profile)
        with mock.patch.object(
            self.module.manager,
            "credential_backend",
            return_value="windows-credential-manager",
        ), mock.patch.object(
            self.module.manager,
            "_windows_store_credential",
        ) as store:
            self.module.manager.store_credential_key("relay-token-123")
        store.assert_called_once_with("relay-token-123")

    def test_agent_text_uses_dynamic_role_model_provider_and_effort(self) -> None:
        profile = self.module.ProviderProfile(
            mode="custom",
            base_url="https://relay.example/v1/",
            model="gpt-5.6-luna",
            provider="relay",
            role="Luna",
            reasoning_effort="ultra",
        )
        self.module.apply_profile(profile)
        text = self.module.manager.expected_agent_text()
        self.assertIn('name = "Luna"', text)
        self.assertIn('model = "gpt-5.6-luna"', text)
        self.assertIn('model_provider = "relay"', text)
        self.assertIn('model_reasoning_effort = "ultra"', text)
        self.assertIn("resume_agent", text)

    def test_direct_test_uses_selected_custom_effort(self) -> None:
        profile = self.module.ProviderProfile(
            mode="custom",
            base_url="https://relay.example/v1/",
            model="gpt-5.6-luna",
            provider="relay",
            role="Luna",
            reasoning_effort="turbo",
        )
        self.module.apply_profile(profile)
        with tempfile.TemporaryDirectory() as directory:
            paths = self.module.manager.resolve_paths(directory)
            proc = SimpleNamespace(returncode=0, stdout="DEEPSEEK_DIRECT_OK\n", stderr="")
            with mock.patch.object(
                self.module.manager.subprocess,
                "run",
                return_value=proc,
            ) as run:
                result = self.module.manager.direct_test(paths, "codex")
        argv = run.call_args.args[0]
        self.assertTrue(result["direct"])
        self.assertIn("gpt-5.6-luna", argv)
        self.assertIn('model_provider="relay"', argv)
        self.assertIn('model_reasoning_effort="turbo"', argv)

    def test_schema_v2_profile_is_upgraded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self.module.manager.resolve_paths(directory)
            path = self.module.profile_path(paths)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "profile": {
                            "mode": "custom",
                            "base_url": "https://relay.example/v1/",
                            "model": "gpt-5.6-luna",
                            "provider": "relay",
                            "provider_name": "Relay",
                            "role": "Luna",
                            "reasoning_effort": "high",
                            "multi_agent_version": "auto",
                            "backend": "external",
                        },
                    }
                ),
                encoding="utf-8",
            )
            loaded = self.module.load_profile(paths)
        self.assertEqual(loaded.role, "Luna")
        self.assertTrue(loaded.role_auto)
        self.assertIsNone(loaded.reasoning_efforts)

    def test_cleanup_replaced_managed_role(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            old_agent = home / "agents" / "Luna.toml"
            old_agent.parent.mkdir(parents=True, exist_ok=True)
            old_agent.write_text("managed\n", encoding="utf-8")
            manifest = {
                "managed_agent_file": True,
                "agent_sha256": self.module.manager.sha256_text_file(old_agent),
            }
            result = self.module.cleanup_replaced_role(
                home,
                self.module.ProviderProfile(role="Luna"),
                self.module.ProviderProfile(role="Terra"),
                manifest,
            )
        self.assertTrue(result["old_agent_removed"])

    def test_external_v2_reports_warning_and_resume_policy(self) -> None:
        profile = self.module.ProviderProfile(
            multi_agent_version="v2",
            backend="external",
        )
        payload = self.module.enrich({"status": "configured"}, profile)
        self.assertEqual(payload["provider_profile"]["resume_policy"], "fresh-spawn-handoff")
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
