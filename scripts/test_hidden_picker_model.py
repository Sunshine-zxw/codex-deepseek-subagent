#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "codex-deepseek-subagent" / "scripts" / "codex_provider_manager.py"

spec = importlib.util.spec_from_file_location("codex_provider_manager_hidden_picker", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class HiddenPickerModelTests(unittest.TestCase):
    def _parent(self) -> dict:
        return {
            "slug": "gpt-parent",
            "display_name": "Parent",
            "description": "parent",
            "visibility": "list",
            "default_reasoning_level": "high",
            "supported_reasoning_levels": [
                {"effort": "high", "description": "high"}
            ],
        }

    def _template(self) -> dict:
        return {
            "slug": "deepseek-v4-flash",
            "display_name": "DeepSeek V4 Flash",
            "description": "template",
            "visibility": "list",
            "default_reasoning_level": "high",
            "supported_reasoning_levels": [
                {"effort": "high", "description": "high"}
            ],
        }

    def test_newly_injected_subagent_model_is_hidden_from_picker(self) -> None:
        profile = module.ProviderProfile(
            mode="custom",
            base_url="https://relay.example/v1/",
            model="relay-only-model",
            provider="relay",
            provider_name="Relay",
            role="Worker",
            role_auto=False,
            reasoning_effort="high",
        )
        with mock.patch.object(
            module.manager,
            "fetch_official_deepseek_model",
            return_value=self._template(),
        ):
            module.apply_profile(profile)
            merged = module.manager.merged_catalog(
                {"models": [self._parent()]},
                {"slug": profile.model},
                "gpt-parent",
            )

        child = next(item for item in merged["models"] if item["slug"] == profile.model)
        self.assertEqual(child["visibility"], "hide")

    def test_existing_native_model_keeps_original_picker_visibility(self) -> None:
        native = {
            "slug": "gpt-5.6-luna",
            "display_name": "GPT-5.6 Luna",
            "description": "native luna",
            "visibility": "list",
            "default_reasoning_level": "high",
            "supported_reasoning_levels": [
                {"effort": "high", "description": "high"}
            ],
        }
        profile = module.ProviderProfile(
            mode="custom",
            base_url="https://relay.example/v1/",
            model="gpt-5.6-luna",
            provider="relay",
            provider_name="Relay",
            role="Luna",
            reasoning_effort="high",
        )
        with mock.patch.object(
            module.manager,
            "fetch_official_deepseek_model",
            return_value=self._template(),
        ):
            module.apply_profile(profile)
            merged = module.manager.merged_catalog(
                {"models": [self._parent(), native]},
                {"slug": profile.model},
                "gpt-parent",
            )

        child = next(item for item in merged["models"] if item["slug"] == profile.model)
        self.assertEqual(child["visibility"], "list")
        self.assertEqual(child["display_name"], "GPT-5.6 Luna")


if __name__ == "__main__":
    unittest.main()
