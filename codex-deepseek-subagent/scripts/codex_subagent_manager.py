#!/usr/bin/env python3
"""Manage multiple Codex custom providers and native subagents without exposing injected models in the main picker."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LOW_LEVEL_SCRIPT = Path(__file__).with_name("codex_provider_manager.py")
_spec = importlib.util.spec_from_file_location("codex_provider_manager_low_level", LOW_LEVEL_SCRIPT)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"Cannot load provider manager: {LOW_LEVEL_SCRIPT}")
low = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = low
_spec.loader.exec_module(low)
manager = low.manager

REGISTRY_NAME = "registry.json"
REGISTRY_SCHEMA_VERSION = 1
CATALOG_NAME = "models-with-subagents.json"
PROVIDERS_BEGIN = "# BEGIN CODEX-CUSTOM-SUBAGENTS PROVIDERS"
PROVIDERS_END = "# END CODEX-CUSTOM-SUBAGENTS PROVIDERS"
LEGACY_PROFILE_NAME = low.PROFILE_NAME
# Native custom subagents default to the requested Luna-compatible maximum.
# Legacy single-profile migrations keep the effort stored in that profile.
DEFAULT_EFFORT = "max"


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    provider_name: str
    base_url: str
    backend: str = "external"
    multi_agent_version: str = "auto"
    credential_target: str = ""

    @property
    def effective_multi_agent_version(self) -> str:
        if self.multi_agent_version in {"v1", "v2"}:
            return self.multi_agent_version
        return "v2" if self.backend == "openai" else "v1"


@dataclass(frozen=True)
class AgentSpec:
    role: str
    provider: str
    model: str
    reasoning_effort: str = DEFAULT_EFFORT
    reasoning_efforts: tuple[str, ...] | None = None
    role_auto: bool = True


@dataclass
class Registry:
    providers: dict[str, ProviderSpec] = field(default_factory=dict)
    agents: dict[str, AgentSpec] = field(default_factory=dict)
    injected_models: set[str] = field(default_factory=set)
    previous_model_catalog_json: str | None = None
    previous_multi_agent_v2: bool | None = None


def registry_path(codex_home: str | None = None) -> Path:
    return manager.resolve_paths(codex_home).state_dir / REGISTRY_NAME


def catalog_path(codex_home: str | None = None) -> Path:
    return manager.resolve_paths(codex_home).home / CATALOG_NAME


def credential_target_for(provider: str) -> str:
    return f"codex-subagent-provider-{provider}"


def validate_provider(spec: ProviderSpec) -> ProviderSpec:
    provider = spec.provider.strip()
    if not low.PROVIDER_RE.fullmatch(provider):
        raise manager.ManagerError("invalid_provider", "provider 只能包含字母、数字、下划线和连字符。")
    name = low._validate_plain_value(spec.provider_name or provider, "provider_name")
    base_url = low.normalize_base_url(spec.base_url)
    if spec.backend not in {"external", "openai"}:
        raise manager.ManagerError("invalid_backend", "backend 只能是 external 或 openai。")
    if spec.multi_agent_version not in {"auto", "v1", "v2"}:
        raise manager.ManagerError("invalid_multi_agent_version", "multi-agent version 只能是 auto、v1 或 v2。")
    target = low._validate_plain_value(
        spec.credential_target.strip() or credential_target_for(provider),
        "credential_target",
        max_bytes=256,
    )
    return ProviderSpec(
        provider=provider,
        provider_name=name,
        base_url=base_url,
        backend=spec.backend,
        multi_agent_version=spec.multi_agent_version,
        credential_target=target,
    )


def validate_agent(spec: AgentSpec, registry: Registry | None = None) -> AgentSpec:
    role = spec.role.strip()
    if not low.ROLE_RE.fullmatch(role):
        raise manager.ManagerError("invalid_role", "role 只能包含字母、数字、下划线和连字符。")
    provider = spec.provider.strip()
    if registry is not None and provider not in registry.providers:
        raise manager.ManagerError("provider_missing", f"Agent {role} 引用的 Provider 不存在：{provider}")
    model = low._validate_plain_value(spec.model, "model")
    effort = low.normalize_reasoning_effort(spec.reasoning_effort)
    efforts = low.normalize_reasoning_efforts(spec.reasoning_efforts, effort)
    return AgentSpec(
        role=role,
        provider=provider,
        model=model,
        reasoning_effort=effort,
        reasoning_efforts=efforts,
        role_auto=bool(spec.role_auto),
    )


def registry_to_payload(registry: Registry) -> dict[str, Any]:
    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "providers": {key: asdict(value) for key, value in sorted(registry.providers.items())},
        "agents": {
            key: {
                **asdict(value),
                "reasoning_efforts": list(value.reasoning_efforts) if value.reasoning_efforts is not None else None,
            }
            for key, value in sorted(registry.agents.items())
        },
        "metadata": {
            "injected_models": sorted(registry.injected_models),
            "previous_model_catalog_json": registry.previous_model_catalog_json,
            "previous_multi_agent_v2": registry.previous_multi_agent_v2,
        },
    }


def save_registry(registry: Registry, codex_home: str | None = None) -> None:
    manager.atomic_write(
        registry_path(codex_home),
        (json.dumps(registry_to_payload(registry), ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def _registry_from_payload(payload: dict[str, Any]) -> Registry:
    if payload.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise manager.ManagerError("unsupported_registry", f"不支持的 registry schema：{payload.get('schema_version')}")
    providers_raw = payload.get("providers")
    agents_raw = payload.get("agents")
    if not isinstance(providers_raw, dict) or not isinstance(agents_raw, dict):
        raise manager.ManagerError("invalid_registry", "registry 缺少 providers/agents 对象。")
    registry = Registry()
    for key, raw in providers_raw.items():
        if not isinstance(raw, dict):
            raise manager.ManagerError("invalid_registry", f"Provider {key} 配置无效。")
        values = dict(raw)
        values.setdefault("provider", key)
        spec = validate_provider(ProviderSpec(**values))
        if spec.provider != key:
            raise manager.ManagerError("invalid_registry", f"Provider 键与 provider 字段不一致：{key}")
        registry.providers[key] = spec
    for key, raw in agents_raw.items():
        if not isinstance(raw, dict):
            raise manager.ManagerError("invalid_registry", f"Agent {key} 配置无效。")
        values = dict(raw)
        values.setdefault("role", key)
        if isinstance(values.get("reasoning_efforts"), list):
            values["reasoning_efforts"] = tuple(str(item) for item in values["reasoning_efforts"])
        spec = validate_agent(AgentSpec(**values), registry)
        if spec.role != key:
            raise manager.ManagerError("invalid_registry", f"Agent 键与 role 字段不一致：{key}")
        registry.agents[key] = spec
    metadata = payload.get("metadata") or {}
    registry.injected_models = {str(item) for item in metadata.get("injected_models") or []}
    previous_catalog = metadata.get("previous_model_catalog_json")
    registry.previous_model_catalog_json = previous_catalog if isinstance(previous_catalog, str) and previous_catalog else None
    previous_v2 = metadata.get("previous_multi_agent_v2")
    registry.previous_multi_agent_v2 = previous_v2 if isinstance(previous_v2, bool) else None
    return registry


def load_registry(codex_home: str | None = None, migrate_legacy: bool = True) -> tuple[Registry, bool]:
    path = registry_path(codex_home)
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise manager.ManagerError("invalid_registry", f"无法读取 registry：{path}") from exc
        return _registry_from_payload(payload), False
    if migrate_legacy:
        migrated = migrate_legacy_profile(codex_home)
        if migrated is not None:
            return migrated, True
    return Registry(), False


def _read_config(paths: Any) -> dict[str, Any]:
    if not paths.config.is_file():
        return {}
    text = paths.config.read_text(encoding="utf-8")
    return manager.parse_toml_text(text) if text.strip() else {}


def _read_config_text(paths: Any) -> str:
    return paths.config.read_text(encoding="utf-8") if paths.config.is_file() else ""


def migrate_legacy_profile(codex_home: str | None = None) -> Registry | None:
    paths = manager.resolve_paths(codex_home)
    legacy_path = paths.state_dir / LEGACY_PROFILE_NAME
    if not legacy_path.is_file():
        return None
    profile = low.load_profile(paths)
    provider = validate_provider(
        ProviderSpec(
            provider=profile.provider,
            provider_name=profile.provider_name,
            base_url=profile.base_url,
            backend=profile.backend,
            multi_agent_version=profile.multi_agent_version,
            credential_target=profile.credential_target,
        )
    )
    agent = validate_agent(
        AgentSpec(
            role=profile.role,
            provider=profile.provider,
            model=profile.model,
            reasoning_effort=profile.reasoning_effort,
            reasoning_efforts=profile.reasoning_efforts,
            role_auto=profile.role_auto,
        )
    )
    registry = Registry(providers={provider.provider: provider}, agents={agent.role: agent})
    config = _read_config(paths)
    current_catalog = config.get("model_catalog_json")
    if isinstance(current_catalog, str) and current_catalog:
        registry.previous_model_catalog_json = current_catalog
        candidate = Path(current_catalog).expanduser()
        if candidate.is_file():
            try:
                catalog = json.loads(candidate.read_text(encoding="utf-8"))
                entry = next((item for item in catalog.get("models", []) if item.get("slug") == agent.model), None)
                if isinstance(entry, dict) and entry.get("visibility") == "hide":
                    registry.injected_models.add(agent.model)
            except (OSError, json.JSONDecodeError):
                pass
    current_v2 = (config.get("features") or {}).get("multi_agent_v2")
    if isinstance(current_v2, bool):
        registry.previous_multi_agent_v2 = current_v2
    return registry


def _credential_with_target(target: str, fn, *args):
    previous = manager.CREDENTIAL_TARGET
    try:
        manager.CREDENTIAL_TARGET = target
        return fn(*args)
    finally:
        manager.CREDENTIAL_TARGET = previous


def credential_present(provider: ProviderSpec) -> bool:
    return bool(_credential_with_target(provider.credential_target, manager.credential_has_key))


def store_credential(provider: ProviderSpec, secret: str) -> None:
    secret = secret.strip()
    if not secret:
        raise manager.ManagerError("invalid_api_key", "API Key 不能为空。")
    if "\r" in secret or "\n" in secret:
        raise manager.ManagerError("invalid_api_key", "API Key 不能包含换行。")
    if len(secret.encode("utf-8")) > 8192:
        raise manager.ManagerError("invalid_api_key", "API Key 过长。")
    backend = manager.credential_backend()
    if backend == "macos-keychain":
        _credential_with_target(provider.credential_target, manager._macos_store_credential, secret)
        return
    if backend == "windows-credential-manager":
        _credential_with_target(provider.credential_target, manager._windows_store_credential, secret)
        return
    raise manager.ManagerError("unsupported_platform", "当前只支持 macOS 和 Windows 系统凭据库。")


def remove_credential(provider: ProviderSpec) -> bool:
    if not manager.credential_available():
        return False
    return bool(_credential_with_target(provider.credential_target, manager.remove_credential_key))


def read_credential(provider: ProviderSpec) -> str | None:
    return _credential_with_target(provider.credential_target, manager.read_credential_key)


def provider_auth(provider: ProviderSpec) -> dict[str, Any]:
    backend = manager.credential_backend()
    if backend == "windows-credential-manager":
        return {
            "command": sys.executable,
            "args": [str(Path(__file__).resolve()), "_credential-get", "--target", provider.credential_target],
        }
    if backend == "macos-keychain":
        return {
            "command": "/usr/bin/security",
            "args": [
                "find-generic-password",
                "-a",
                manager.credential_account(),
                "-s",
                provider.credential_target,
                "-w",
            ],
        }
    raise manager.ManagerError("unsupported_platform", "当前只支持 macOS 和 Windows 系统凭据库。")


def render_provider_block(registry: Registry) -> str:
    if not registry.providers:
        return ""
    lines = ["", PROVIDERS_BEGIN]
    for provider_id, spec in sorted(registry.providers.items()):
        auth = provider_auth(spec)
        lines.extend(
            [
                f"[model_providers.{provider_id}]",
                f"name = {manager.toml_string(spec.provider_name)}",
                f"base_url = {manager.toml_string(spec.base_url)}",
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
    lines.append(PROVIDERS_END)
    return "\n".join(lines) + "\n"


def render_agent(agent: AgentSpec) -> str:
    q = manager.toml_string
    return f'''name = {q(agent.role)}
description = {q(f"Text-only {agent.role} custom-provider subagent for coding, repository research, review, and verification.")}
model = {q(agent.model)}
model_provider = {q(agent.provider)}
model_reasoning_effort = {q(agent.reasoning_effort)}
developer_instructions = """
You are a focused subagent running inside Codex through the configured provider.

Complete the bounded task assigned by the parent agent, use available tools when needed, and return a concise evidence-based result.
You are text-only. If visual evidence is required, ask the parent agent to provide a textual description.
Do not spawn additional subagents unless the user or parent explicitly asks for nested delegation.
Before finishing a task that may need continuation, include a compact handoff with completed work, remaining work, relevant files/symbols, and blockers.
"""
'''


def global_multi_agent_version(registry: Registry) -> str:
    if not registry.agents:
        return "v1"
    versions = {registry.providers[agent.provider].effective_multi_agent_version for agent in registry.agents.values()}
    return "v2" if versions == {"v2"} else "v1"


def _strip_managed_blocks(text: str) -> str:
    text = manager.remove_marked_block(text, PROVIDERS_BEGIN, PROVIDERS_END)
    text = manager.remove_marked_block(text, manager.PROVIDER_BEGIN, manager.PROVIDER_END)
    text = manager.remove_marked_block(text, manager.ROLE_BEGIN, manager.ROLE_END)
    return text


def _provider_conflicts(unmanaged: dict[str, Any], registry: Registry) -> list[str]:
    providers = unmanaged.get("model_providers") or {}
    return [f"model_providers.{provider_id}" for provider_id in registry.providers if provider_id in providers]


def _load_catalog_source(paths: Any, config: dict[str, Any]) -> dict[str, Any]:
    configured = config.get("model_catalog_json")
    if isinstance(configured, str) and configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(payload.get("models"), list):
                    return payload
            except (OSError, json.JSONDecodeError):
                pass
    codex_bin = manager.find_desktop_codex()
    return manager.run_codex_models(codex_bin, paths)


def _patch_reasoning(model: dict[str, Any], agent: AgentSpec) -> dict[str, Any]:
    result = copy.deepcopy(model)
    existing = result.get("supported_reasoning_levels", []) or []
    descriptions = {
        item.get("effort"): item.get("description")
        for item in existing
        if isinstance(item, dict) and isinstance(item.get("effort"), str)
    }
    efforts = list(agent.reasoning_efforts) if agent.reasoning_efforts is not None else [agent.reasoning_effort]
    result["default_reasoning_level"] = agent.reasoning_effort
    result["supported_reasoning_levels"] = [
        {"effort": effort, "description": descriptions.get(effort) or f"{effort} reasoning effort"}
        for effort in efforts
    ]
    return result


def _template_for_injected_model(models: list[dict[str, Any]]) -> dict[str, Any]:
    preferred = next((item for item in models if item.get("slug") == low.OFFICIAL_MODEL), None)
    if preferred is not None:
        return copy.deepcopy(preferred)
    if models:
        return copy.deepcopy(models[0])
    raise manager.ManagerError("catalog_empty", "模型目录为空，无法为第三方子代理生成兼容元数据。")


def build_catalog(source: dict[str, Any], registry: Registry, parent_model: str) -> tuple[dict[str, Any], set[str]]:
    models = [
        copy.deepcopy(item)
        for item in source.get("models", [])
        if item.get("slug") not in registry.injected_models
    ]
    existing_slugs = {item.get("slug") for item in models}
    new_injected: set[str] = set()
    template = _template_for_injected_model(models)

    by_model: dict[str, AgentSpec] = {}
    for agent in registry.agents.values():
        previous = by_model.get(agent.model)
        if previous is not None:
            prev_efforts = previous.reasoning_efforts or (previous.reasoning_effort,)
            next_efforts = agent.reasoning_efforts or (agent.reasoning_effort,)
            if tuple(prev_efforts) != tuple(next_efforts):
                raise manager.ManagerError(
                    "model_reasoning_conflict",
                    f"多个 Agent 对模型 {agent.model} 声明了不同 reasoning 档位。",
                )
            continue
        by_model[agent.model] = agent

    for model_id, agent in sorted(by_model.items()):
        if model_id in existing_slugs:
            # Native/existing models remain untouched, including picker visibility.
            continue
        child = copy.deepcopy(template)
        child["slug"] = model_id
        child["display_name"] = agent.role
        child["description"] = f"{model_id} via {registry.providers[agent.provider].provider_name}"
        child = _patch_reasoning(child, agent)
        child["visibility"] = "hide"
        models.append(child)
        existing_slugs.add(model_id)
        new_injected.add(model_id)

    parent = next((item for item in models if item.get("slug") == parent_model), None)
    if parent is None:
        raise manager.ManagerError("parent_model_missing", f"模型目录中没有父模型 {parent_model}。")
    parent["multi_agent_version"] = global_multi_agent_version(registry)
    models.sort(key=lambda item: item.get("slug", ""))
    return {"models": models}, new_injected


def configured_parent_model(config: dict[str, Any]) -> str | None:
    value = config.get("model")
    return value if isinstance(value, str) and value else None


def reconcile(registry: Registry, codex_home: str | None = None) -> dict[str, Any]:
    paths = manager.resolve_paths(codex_home)
    paths.home.mkdir(parents=True, exist_ok=True)
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    config_text = _read_config_text(paths)
    parsed = manager.parse_toml_text(config_text) if config_text.strip() else {}
    parent_model = configured_parent_model(parsed)
    if not parent_model:
        raise manager.ManagerError("parent_model_unconfigured", "config.toml 中没有明确的父模型 model。")

    unmanaged_text = _strip_managed_blocks(config_text)
    unmanaged_parsed = manager.parse_toml_text(unmanaged_text) if unmanaged_text.strip() else {}
    conflicts = _provider_conflicts(unmanaged_parsed, registry)
    if conflicts:
        raise manager.ManagerError(
            "conflict",
            "发现与注册表 Provider ID 冲突的非托管配置。",
            {"fields": conflicts},
        )

    if registry.previous_model_catalog_json is None:
        old_catalog = parsed.get("model_catalog_json")
        if isinstance(old_catalog, str) and old_catalog:
            registry.previous_model_catalog_json = old_catalog
    if registry.previous_multi_agent_v2 is None:
        old_v2 = (parsed.get("features") or {}).get("multi_agent_v2")
        if isinstance(old_v2, bool):
            registry.previous_multi_agent_v2 = old_v2

    source = _load_catalog_source(paths, parsed)
    catalog, injected = build_catalog(source, registry, parent_model)
    registry.injected_models = injected

    target_catalog = catalog_path(codex_home)
    new_config = unmanaged_text.rstrip() + "\n"
    provider_block = render_provider_block(registry)
    if provider_block:
        new_config += provider_block
    new_config = manager.set_top_level_key(new_config, "model_catalog_json", str(target_catalog))
    version = global_multi_agent_version(registry)
    new_config = manager.set_table_bool(new_config, "features", "multi_agent_v2", version == "v2")
    manager.parse_toml_text(new_config)

    manager.atomic_write(
        target_catalog,
        (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )

    agents_dir = paths.home / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    expected_paths = set()
    for role, agent in registry.agents.items():
        path = agents_dir / f"{role}.toml"
        expected_paths.add(path)
        manager.atomic_write(path, render_agent(agent).encode("utf-8"), mode=0o644)

    previous_payload = None
    reg_path = registry_path(codex_home)
    if reg_path.is_file():
        try:
            previous_payload = json.loads(reg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous_payload = None
    if isinstance(previous_payload, dict):
        previous_agents = previous_payload.get("agents") or {}
        for role in previous_agents:
            old_path = agents_dir / f"{role}.toml"
            if old_path not in expected_paths and old_path.is_file():
                old_path.unlink()

    manager.atomic_write(paths.config, new_config.encode("utf-8"))
    save_registry(registry, codex_home)
    return {
        "parent_model": parent_model,
        "parent_multi_agent_version": version,
        "providers": len(registry.providers),
        "agents": len(registry.agents),
        "catalog": str(target_catalog),
    }


def status(registry: Registry, codex_home: str | None = None) -> dict[str, Any]:
    paths = manager.resolve_paths(codex_home)
    checks: dict[str, Any] = {}
    errors: list[str] = []
    try:
        config = _read_config(paths)
        checks["config_valid"] = True
    except manager.ManagerError as exc:
        config = {}
        checks["config_valid"] = False
        errors.append(str(exc))
    checks["registry_exists"] = registry_path(codex_home).is_file()
    checks["catalog_selected"] = (
        Path(config.get("model_catalog_json", "")).expanduser() == catalog_path(codex_home)
        if config.get("model_catalog_json")
        else False
    )

    provider_checks = {}
    parsed_providers = config.get("model_providers") or {}
    for provider_id, spec in registry.providers.items():
        actual = parsed_providers.get(provider_id) or {}
        provider_checks[provider_id] = {
            "registered": bool(actual),
            "base_url": actual.get("base_url") == spec.base_url,
            "wire_api": actual.get("wire_api") == "responses",
            "credential_present": credential_present(spec),
        }
    checks["providers"] = provider_checks

    agent_checks = {}
    for role, agent in registry.agents.items():
        path = paths.home / "agents" / f"{role}.toml"
        agent_checks[role] = {
            "file": str(path),
            "exists": path.is_file(),
            "content_valid": path.is_file() and path.read_text(encoding="utf-8") == render_agent(agent),
            "provider": agent.provider,
            "model": agent.model,
        }
    checks["agents"] = agent_checks

    catalog_ok = False
    hidden_ok = True
    path = catalog_path(codex_home)
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            catalog_ok = isinstance(payload.get("models"), list)
            for model in registry.injected_models:
                entry = next((item for item in payload.get("models", []) if item.get("slug") == model), None)
                hidden_ok = hidden_ok and isinstance(entry, dict) and entry.get("visibility") == "hide"
        except (OSError, json.JSONDecodeError):
            catalog_ok = False
    checks["catalog_valid"] = catalog_ok
    checks["injected_models_hidden"] = hidden_ok

    ready = (
        checks["config_valid"]
        and checks["catalog_selected"]
        and checks["catalog_valid"]
        and checks["injected_models_hidden"]
        and all(
            item["registered"] and item["base_url"] and item["wire_api"] and item["credential_present"]
            for item in provider_checks.values()
        )
        and all(item["exists"] and item["content_valid"] for item in agent_checks.values())
    )
    return {
        "status": "configured" if ready else "partial",
        "checks": checks,
        "errors": errors,
        "providers": [asdict(item) for item in registry.providers.values()],
        "agents": [_agent_payload(item) for item in registry.agents.values()],
    }


def direct_test(agent: AgentSpec, codex_home: str | None = None) -> dict[str, Any]:
    paths = manager.resolve_paths(codex_home)
    codex_bin = manager.find_desktop_codex()
    env = dict(os.environ)
    env["CODEX_HOME"] = str(paths.home)
    proc = subprocess.run(
        [
            codex_bin,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--json",
            "-s",
            "read-only",
            "-C",
            str(paths.home),
            "-m",
            agent.model,
            "-c",
            f"model_provider={manager.toml_string(agent.provider)}",
            "-c",
            f"model_reasoning_effort={manager.toml_string(agent.reasoning_effort)}",
            "Reply exactly SUBAGENT_DIRECT_OK and nothing else.",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    if proc.returncode != 0 or "SUBAGENT_DIRECT_OK" not in proc.stdout:
        raise manager.ManagerError("direct_test_failed", f"{agent.role} 直连测试失败。", {"stderr": proc.stderr[-1200:]})
    return {"direct": True}


def native_test(agent: AgentSpec, codex_home: str | None = None) -> dict[str, Any]:
    paths = manager.resolve_paths(codex_home)
    codex_bin = manager.find_desktop_codex()
    parent_model = manager.choose_parent_model(paths)
    env = dict(os.environ)
    env["CODEX_HOME"] = str(paths.home)
    prompt = (
        f'Use the native spawn_agent tool exactly once. Set agent_type to {agent.role} and fork_turns to none. '
        'Give it this task: Reply exactly NATIVE_SUBAGENT_OK. '
        "Then wait for that subagent and return only its final response."
    )
    proc = subprocess.run(
        [
            codex_bin,
            "exec",
            "--skip-git-repo-check",
            "--json",
            "-s",
            "read-only",
            "-C",
            str(paths.home),
            "-m",
            parent_model,
            prompt,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    if proc.returncode != 0:
        raise manager.ManagerError("native_test_failed", f"{agent.role} 原生 spawn_agent 测试失败。", {"stderr": proc.stderr[-1200:]})

    child_ids: list[str] = []
    child_messages: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and item.get("type") == "collab_tool_call" and item.get("tool") == "spawn_agent":
            child_ids.extend(item.get("receiver_thread_ids") or [])
        if event.get("type") == "item.completed" and item.get("type") == "collab_tool_call" and item.get("tool") == "wait":
            for receiver_id, state in (item.get("agents_states") or {}).items():
                if isinstance(state, dict) and state.get("status") == "completed":
                    message = state.get("message")
                    if isinstance(message, str):
                        child_messages[receiver_id] = message.strip()

    child_id = child_ids[0] if len(child_ids) == 1 else None
    child_message = child_messages.get(child_id) if child_id else None
    metadata = manager.wait_for_child_metadata(paths, child_id) if child_id else None
    expected = {
        "model_provider": agent.provider,
        "model": agent.model,
        "reasoning_effort": agent.reasoning_effort,
        "agent_role": agent.role,
    }
    if len(child_ids) != 1 or child_message != "NATIVE_SUBAGENT_OK" or metadata != expected:
        raise manager.ManagerError(
            "native_route_mismatch",
            f"{agent.role} 原生子 Agent 路由证据不匹配。",
            {"child_ids": child_ids, "child_message": child_message, "metadata": metadata, "expected": expected},
        )
    return {"native": True, "child_id": child_id, **expected}


def add_provider(args: argparse.Namespace, registry: Registry) -> dict[str, Any]:
    if args.provider in registry.providers:
        raise manager.ManagerError("provider_exists", f"Provider 已存在：{args.provider}")
    spec = validate_provider(
        ProviderSpec(
            provider=args.provider,
            provider_name=args.provider_name or args.provider,
            base_url=args.base_url,
            backend=args.backend,
            multi_agent_version=args.multi_agent_version,
        )
    )
    registry.providers[args.provider] = spec
    if args.api_key_stdin:
        secret = sys.stdin.readline().strip()
        if not secret:
            raise manager.ManagerError("credential_missing", "标准输入中没有 API Key。")
        store_credential(spec, secret)
    return {"provider": asdict(spec)}


def update_provider(args: argparse.Namespace, registry: Registry) -> dict[str, Any]:
    if args.provider not in registry.providers:
        raise manager.ManagerError("provider_missing", f"Provider 不存在：{args.provider}")
    values = asdict(registry.providers[args.provider])
    for name in ("provider_name", "base_url", "backend", "multi_agent_version"):
        value = getattr(args, name)
        if value is not None:
            values[name] = value
    spec = validate_provider(ProviderSpec(**values))
    registry.providers[args.provider] = spec
    if args.replace_api_key_stdin:
        secret = sys.stdin.readline().strip()
        if not secret:
            raise manager.ManagerError("credential_missing", "标准输入中没有 API Key。")
        store_credential(spec, secret)
    return {"provider": asdict(spec)}


def remove_provider_cmd(args: argparse.Namespace, registry: Registry) -> dict[str, Any]:
    spec = registry.providers.get(args.provider)
    if spec is None:
        raise manager.ManagerError("provider_missing", f"Provider 不存在：{args.provider}")
    users = [role for role, agent in registry.agents.items() if agent.provider == args.provider]
    if users and not args.cascade:
        raise manager.ManagerError("provider_in_use", f"Provider {args.provider} 仍被 Agent 使用。", {"agents": users})
    for role in users:
        registry.agents.pop(role, None)
    registry.providers.pop(args.provider)
    removed_credential = remove_credential(spec) if args.remove_credential else False
    return {"provider": args.provider, "removed_agents": users, "removed_credential": removed_credential}


def add_agent(args: argparse.Namespace, registry: Registry) -> dict[str, Any]:
    if args.provider not in registry.providers:
        raise manager.ManagerError("provider_missing", f"Provider 不存在：{args.provider}")
    role = args.role or low.infer_role(args.model)
    if role in registry.agents:
        raise manager.ManagerError("agent_exists", f"Agent 已存在：{role}")
    efforts = low.parse_reasoning_efforts(args.reasoning_efforts) if args.reasoning_efforts else None
    agent = validate_agent(
        AgentSpec(
            role=role,
            provider=args.provider,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            reasoning_efforts=efforts,
            role_auto=args.role is None,
        ),
        registry,
    )
    registry.agents[role] = agent
    return {"agent": _agent_payload(agent)}


def update_agent(args: argparse.Namespace, registry: Registry) -> dict[str, Any]:
    current = registry.agents.get(args.role)
    if current is None:
        raise manager.ManagerError("agent_missing", f"Agent 不存在：{args.role}")
    values = asdict(current)
    if args.provider is not None:
        values["provider"] = args.provider
    if args.model is not None:
        values["model"] = args.model
        if current.role_auto and args.new_role is None:
            values["role"] = low.infer_role(args.model)
            values["role_auto"] = True
    if args.reasoning_effort is not None:
        values["reasoning_effort"] = args.reasoning_effort
    if args.reasoning_efforts is not None:
        values["reasoning_efforts"] = low.parse_reasoning_efforts(args.reasoning_efforts)
    if args.new_role is not None:
        values["role"] = args.new_role
        values["role_auto"] = False
    agent = validate_agent(AgentSpec(**values), registry)
    if agent.role != args.role and agent.role in registry.agents:
        raise manager.ManagerError("agent_exists", f"目标 Agent 名称已存在：{agent.role}")
    registry.agents.pop(args.role)
    registry.agents[agent.role] = agent
    return {"agent": _agent_payload(agent), "previous_role": args.role}


def remove_agent_cmd(args: argparse.Namespace, registry: Registry) -> dict[str, Any]:
    if registry.agents.pop(args.role, None) is None:
        raise manager.ManagerError("agent_missing", f"Agent 不存在：{args.role}")
    return {"agent": args.role}


def _agent_payload(agent: AgentSpec) -> dict[str, Any]:
    return {
        **asdict(agent),
        "reasoning_efforts": list(agent.reasoning_efforts) if agent.reasoning_efforts else None,
    }


def list_payload(registry: Registry) -> dict[str, Any]:
    return {
        "status": "ok",
        "providers": [
            {
                **asdict(spec),
                "effective_multi_agent_version": spec.effective_multi_agent_version,
                "credential_present": credential_present(spec),
            }
            for spec in registry.providers.values()
        ],
        "agents": [_agent_payload(spec) for spec in registry.agents.values()],
        "parent_multi_agent_version": global_multi_agent_version(registry),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list")
    sub.add_parser("status")
    sub.add_parser("repair")
    sub.add_parser("migrate")

    p = sub.add_parser("provider-add")
    p.add_argument("--provider", required=True)
    p.add_argument("--provider-name")
    p.add_argument("--base-url", required=True)
    p.add_argument("--backend", choices=("external", "openai"), default="external")
    p.add_argument("--multi-agent-version", choices=("auto", "v1", "v2"), default="auto")
    p.add_argument("--api-key-stdin", action="store_true")

    p = sub.add_parser("provider-update")
    p.add_argument("--provider", required=True)
    p.add_argument("--provider-name")
    p.add_argument("--base-url")
    p.add_argument("--backend", choices=("external", "openai"))
    p.add_argument("--multi-agent-version", choices=("auto", "v1", "v2"))
    p.add_argument("--replace-api-key-stdin", action="store_true")

    p = sub.add_parser("provider-remove")
    p.add_argument("--provider", required=True)
    p.add_argument("--cascade", action="store_true")
    p.add_argument("--remove-credential", action="store_true")

    p = sub.add_parser("agent-add")
    p.add_argument("--provider", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--role")
    p.add_argument("--reasoning-effort", default=DEFAULT_EFFORT)
    p.add_argument("--reasoning-efforts")

    p = sub.add_parser("agent-update")
    p.add_argument("--role", required=True)
    p.add_argument("--new-role")
    p.add_argument("--provider")
    p.add_argument("--model")
    p.add_argument("--reasoning-effort")
    p.add_argument("--reasoning-efforts")

    p = sub.add_parser("agent-remove")
    p.add_argument("--role", required=True)

    p = sub.add_parser("test")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--role")
    group.add_argument("--all", action="store_true")

    p = sub.add_parser("_credential-get")
    p.add_argument("--target", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "_credential-get":
            provider = ProviderSpec(
                provider="internal",
                provider_name="internal",
                base_url="https://invalid.local/",
                credential_target=args.target,
            )
            secret = read_credential(provider)
            if not secret:
                return 2
            sys.stdout.write(secret)
            return 0

        registry, migrated = load_registry(args.codex_home, migrate_legacy=True)
        if args.command == "list":
            payload = list_payload(registry)
        elif args.command == "status":
            payload = status(registry, args.codex_home)
            payload["legacy_migration_available"] = migrated
        elif args.command == "migrate":
            if not migrated and registry_path(args.codex_home).is_file():
                payload = {"status": "ok", "message": "registry 已存在，无需迁移。"}
            elif not migrated:
                payload = {"status": "ok", "message": "没有检测到旧单 Profile 配置。"}
            else:
                result = reconcile(registry, args.codex_home)
                payload = {"status": "configured", "migrated": True, **result}
        elif args.command == "repair":
            result = reconcile(registry, args.codex_home)
            payload = {"status": "configured", **result}
        elif args.command == "test":
            roles = list(registry.agents) if args.all else [args.role]
            results = {}
            for role in roles:
                agent = registry.agents.get(role)
                if agent is None:
                    raise manager.ManagerError("agent_missing", f"Agent 不存在：{role}")
                results[role] = {**direct_test(agent, args.codex_home), **native_test(agent, args.codex_home)}
            payload = {"status": "ok", "tests": results}
        else:
            if args.command == "provider-add":
                mutation = add_provider(args, registry)
            elif args.command == "provider-update":
                mutation = update_provider(args, registry)
            elif args.command == "provider-remove":
                mutation = remove_provider_cmd(args, registry)
            elif args.command == "agent-add":
                mutation = add_agent(args, registry)
            elif args.command == "agent-update":
                mutation = update_agent(args, registry)
            elif args.command == "agent-remove":
                mutation = remove_agent_cmd(args, registry)
            else:
                raise manager.ManagerError("invalid_command", f"未知命令：{args.command}")
            result = reconcile(registry, args.codex_home)
            payload = {"status": "configured", **mutation, **result, "migrated_legacy": migrated}

        manager.emit(payload, args.json)
        return 0 if payload.get("status") not in {"partial", "failed"} else 2
    except manager.ManagerError as exc:
        manager.emit(manager.result(exc.code, message=str(exc), **exc.details), args.json)
        return 2
    except subprocess.TimeoutExpired:
        manager.emit(manager.result("timeout", message="操作超时。"), args.json)
        return 3
    except Exception as exc:
        manager.emit(manager.result("failed", message=f"{type(exc).__name__}: {exc}"), args.json)
        return 1


if __name__ == "__main__":
    # The auth command is invoked by Codex from model_providers.*.auth.command.
    # It must keep using the legacy implementation because the facade only
    # exposes the public management commands.
    import sys

    if len(sys.argv) > 1 and sys.argv[1].startswith("_credential-"):
        raise SystemExit(main())

    # Keep the historical path safe for users who still invoke this file
    # directly. The facade adds the external-provider approval compatibility
    # layer; imports used by the facade do not enter this branch.
    import runpy

    runpy.run_path(str(Path(__file__).with_name("codex_subagent_cli.py")), run_name="__main__")
