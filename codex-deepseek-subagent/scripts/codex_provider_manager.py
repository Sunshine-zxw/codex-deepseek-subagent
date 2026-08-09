#!/usr/bin/env python3
"""Configure Codex native subagents through official or custom Responses-compatible providers."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

BASE_SCRIPT = Path(__file__).with_name("codex_deepseek.py")
_spec = importlib.util.spec_from_file_location("codex_deepseek_base", BASE_SCRIPT)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"Cannot load base manager: {BASE_SCRIPT}")
manager = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = manager
_spec.loader.exec_module(manager)

OFFICIAL_BASE_URL = "https://api.deepseek.com/"
OFFICIAL_MODEL = "deepseek-v4-flash"
DEFAULT_PROVIDER = "deepseek"
DEFAULT_PROVIDER_NAME = "DeepSeek"
DEFAULT_ROLE = "DeepSeek"
DEFAULT_EFFORT = "high"
PROFILE_NAME = "provider-profile.json"
PROFILE_SCHEMA_VERSION = 3
PROVIDER_RE = re.compile(r"^[A-Za-z0-9_-]+$")
ROLE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class ProviderProfile:
    mode: str = "official"
    base_url: str = OFFICIAL_BASE_URL
    model: str = OFFICIAL_MODEL
    provider: str = DEFAULT_PROVIDER
    provider_name: str = DEFAULT_PROVIDER_NAME
    role: str = DEFAULT_ROLE
    role_auto: bool = True
    reasoning_effort: str = DEFAULT_EFFORT
    reasoning_efforts: tuple[str, ...] | None = None
    multi_agent_version: str = "auto"
    backend: str = "external"

    @property
    def effective_multi_agent_version(self) -> str:
        if self.multi_agent_version in {"v1", "v2"}:
            return self.multi_agent_version
        return "v2" if self.backend == "openai" else "v1"

    @property
    def credential_target(self) -> str:
        if self.mode == "official" and self.base_url == OFFICIAL_BASE_URL:
            return "codex-deepseek-api-key"
        digest = hashlib.sha256(
            f"{self.provider}\0{self.base_url}".encode("utf-8")
        ).hexdigest()[:12]
        return f"codex-deepseek-api-key-{digest}"


def _validate_plain_value(value: str, label: str, max_bytes: int = 256) -> str:
    value = value.strip()
    if not value:
        raise manager.ManagerError(f"invalid_{label}", f"{label} 不能为空。")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise manager.ManagerError(f"invalid_{label}", f"{label} 不能包含控制字符。")
    if len(value.encode("utf-8")) > max_bytes:
        raise manager.ManagerError(f"invalid_{label}", f"{label} 过长。")
    return value


def normalize_base_url(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise manager.ManagerError(
            "invalid_base_url",
            "base URL 必须是有效的 http:// 或 https:// 地址。",
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise manager.ManagerError(
            "invalid_base_url",
            "base URL 不应包含用户名、密码、query string 或 URL fragment。",
        )
    return value.rstrip("/") + "/"


def normalize_reasoning_effort(value: str) -> str:
    return _validate_plain_value(value, "reasoning_effort", max_bytes=128)


def normalize_reasoning_efforts(
    values: Iterable[str] | None,
    selected: str,
) -> tuple[str, ...] | None:
    if values is None:
        return None
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        effort = normalize_reasoning_effort(value)
        if effort not in seen:
            normalized.append(effort)
            seen.add(effort)
    if selected not in seen:
        normalized.append(selected)
    return tuple(normalized)


def parse_reasoning_efforts(value: str | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    pieces = [item.strip() for item in value.split(",") if item.strip()]
    if not pieces:
        raise manager.ManagerError(
            "invalid_reasoning_efforts",
            "--reasoning-efforts 至少需要一个非空档位。",
        )
    return tuple(pieces)


def infer_role(model: str) -> str:
    all_tokens = [token.lower() for token in re.findall(r"[A-Za-z0-9]+", model)]
    known = (
        ("deepseek", "DeepSeek"),
        ("luna", "Luna"),
        ("terra", "Terra"),
        ("sol", "Sol"),
        ("kimi", "Kimi"),
        ("qwen", "Qwen"),
        ("glm", "GLM"),
        ("claude", "Claude"),
        ("gemini", "Gemini"),
    )
    for marker, role in known:
        if marker in all_tokens:
            return role

    leaf = model.rsplit("/", 1)[-1]
    tokens = re.findall(r"[A-Za-z0-9]+", leaf)
    ignored = {"gpt", "model", "chat", "instruct", "preview", "latest"}
    for token in reversed(tokens):
        if token.lower() in ignored or token.isdigit():
            continue
        candidate = token if token.isupper() else token.capitalize()
        if ROLE_RE.fullmatch(candidate):
            return candidate
    return "CustomAgent"


def validate_profile(profile: ProviderProfile) -> ProviderProfile:
    if profile.mode not in {"official", "custom"}:
        raise manager.ManagerError("invalid_profile", "mode 只能是 official 或 custom。")
    model = _validate_plain_value(profile.model, "model")
    provider = profile.provider.strip()
    if not PROVIDER_RE.fullmatch(provider):
        raise manager.ManagerError(
            "invalid_provider",
            "provider 只能包含字母、数字、下划线和连字符。",
        )
    role = profile.role.strip()
    if not ROLE_RE.fullmatch(role):
        raise manager.ManagerError(
            "invalid_role",
            "role 只能包含字母、数字、下划线和连字符。",
        )
    effort = normalize_reasoning_effort(profile.reasoning_effort)
    efforts = normalize_reasoning_efforts(profile.reasoning_efforts, effort)
    if profile.multi_agent_version not in {"auto", "v1", "v2"}:
        raise manager.ManagerError(
            "invalid_multi_agent_version",
            "multi-agent version 只能是 auto、v1 或 v2。",
        )
    if profile.backend not in {"external", "openai"}:
        raise manager.ManagerError(
            "invalid_backend",
            "backend 只能是 external 或 openai。",
        )
    return ProviderProfile(
        **{
            **asdict(profile),
            "base_url": normalize_base_url(profile.base_url),
            "model": model,
            "provider": provider,
            "provider_name": profile.provider_name.strip() or provider,
            "role": role,
            "reasoning_effort": effort,
            "reasoning_efforts": efforts,
        }
    )


def profile_path(paths: Any) -> Path:
    return paths.state_dir / PROFILE_NAME


def load_profile(paths: Any) -> ProviderProfile:
    path = profile_path(paths)
    if not path.is_file():
        return ProviderProfile()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise manager.ManagerError(
            "invalid_profile",
            f"无法读取 Provider profile：{path}",
        ) from exc

    schema = payload.get("schema_version")
    if schema not in {1, 2, PROFILE_SCHEMA_VERSION}:
        raise manager.ManagerError(
            "unsupported_profile",
            f"不支持的 Provider profile schema：{schema}",
        )
    raw = payload.get("profile")
    if not isinstance(raw, dict):
        raise manager.ManagerError("invalid_profile", "Provider profile 缺少 profile 对象。")
    raw = dict(raw)
    raw.setdefault("reasoning_efforts", None)
    if "role_auto" not in raw:
        raw["role_auto"] = raw.get("role") == infer_role(str(raw.get("model", OFFICIAL_MODEL)))
    try:
        return validate_profile(ProviderProfile(**raw))
    except TypeError as exc:
        raise manager.ManagerError("invalid_profile", f"Provider profile 字段无效：{exc}") from exc


def save_profile(paths: Any, profile: ProviderProfile) -> None:
    payload = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "profile": asdict(profile),
    }
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    manager.atomic_write(profile_path(paths), data)


def remove_profile(paths: Any) -> None:
    profile_path(paths).unlink(missing_ok=True)


def profile_from_args(args: argparse.Namespace, paths: Any) -> tuple[ProviderProfile, bool]:
    existing = load_profile(paths)
    has_profile = profile_path(paths).is_file()
    override_names = (
        "base_url",
        "model",
        "provider",
        "provider_name",
        "role",
        "reasoning_effort",
        "reasoning_efforts",
        "multi_agent_version",
        "backend",
    )
    has_override = any(getattr(args, name, None) is not None for name in override_names) or bool(
        getattr(args, "official", False)
    )
    if not has_override:
        return existing, False

    if args.official:
        if any(getattr(args, name, None) is not None for name in override_names):
            raise manager.ManagerError(
                "invalid_profile",
                "--official 不能与自定义 Provider 参数同时使用。",
            )
        return ProviderProfile(), True

    base = existing if has_profile else ProviderProfile(mode="custom")
    values = asdict(base)
    old_model = values["model"]

    for field in (
        "base_url",
        "model",
        "provider",
        "provider_name",
        "reasoning_effort",
        "multi_agent_version",
        "backend",
    ):
        value = getattr(args, field, None)
        if value is not None:
            values[field] = value

    if args.reasoning_efforts is not None:
        values["reasoning_efforts"] = parse_reasoning_efforts(args.reasoning_efforts)

    explicit_role = args.role is not None
    if explicit_role:
        values["role"] = args.role
        values["role_auto"] = False
    elif not has_profile or (values.get("role_auto", True) and values["model"] != old_model):
        values["role"] = infer_role(str(values["model"]))
        values["role_auto"] = True

    normalized_url = normalize_base_url(str(values["base_url"]))
    values["base_url"] = normalized_url
    values["mode"] = (
        "official"
        if normalized_url == OFFICIAL_BASE_URL
        and values["model"] == OFFICIAL_MODEL
        and values["provider"] == DEFAULT_PROVIDER
        else "custom"
    )
    if values["mode"] == "official" and not explicit_role and not has_profile:
        values["role"] = DEFAULT_ROLE
        values["role_auto"] = True
    return validate_profile(ProviderProfile(**values)), True


def _recursive_model_alias(value: Any, model: str) -> Any:
    if isinstance(value, dict):
        return {key: _recursive_model_alias(item, model) for key, item in value.items()}
    if isinstance(value, list):
        return [_recursive_model_alias(item, model) for item in value]
    if isinstance(value, str) and value == OFFICIAL_MODEL:
        return model
    return value


def _catalog_efforts(model: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for item in model.get("supported_reasoning_levels", []) or []:
        if isinstance(item, dict) and isinstance(item.get("effort"), str):
            values.append(item["effort"])
    return values


def _patch_catalog_reasoning(
    model: dict[str, Any],
    profile: ProviderProfile,
    preserve_existing: bool,
) -> dict[str, Any]:
    result = copy.deepcopy(model)
    existing_items = result.get("supported_reasoning_levels", []) or []
    descriptions = {
        item.get("effort"): item.get("description")
        for item in existing_items
        if isinstance(item, dict) and isinstance(item.get("effort"), str)
    }

    if profile.reasoning_efforts is not None:
        efforts = list(profile.reasoning_efforts)
    elif preserve_existing and _catalog_efforts(result):
        efforts = _catalog_efforts(result)
        if profile.reasoning_effort not in efforts:
            efforts.append(profile.reasoning_effort)
    else:
        efforts = [profile.reasoning_effort]

    result["default_reasoning_level"] = profile.reasoning_effort
    result["supported_reasoning_levels"] = [
        {
            "effort": effort,
            "description": descriptions.get(effort) or f"{effort} reasoning effort",
        }
        for effort in efforts
    ]
    return result


def apply_profile(profile: ProviderProfile) -> None:
    profile = validate_profile(profile)
    original_fetch_model = manager.fetch_official_deepseek_model

    manager.MODEL = profile.model
    manager.PROVIDER = profile.provider
    manager.ROLE = profile.role
    manager.EFFORT = profile.reasoning_effort
    manager.CREDENTIAL_TARGET = profile.credential_target
    manager.PARENT_MULTI_AGENT_VERSION = profile.effective_multi_agent_version
    manager.DESKTOP_MULTI_AGENT_V2 = profile.effective_multi_agent_version == "v2"

    def expected_agent_text() -> str:
        q = manager.toml_string
        return f'''name = {q(profile.role)}
description = {q(f"Text-only {profile.role} subagent for coding, repository research, review, and verification. Visual inputs must be converted to text by the parent agent.")}
model = {q(profile.model)}
model_provider = {q(profile.provider)}
model_reasoning_effort = {q(profile.reasoning_effort)}
developer_instructions = """
You are a focused subagent running inside Codex through the configured provider.

Complete the bounded task assigned by the parent agent, use available tools when needed, and return a concise evidence-based result.
You are text-only. Do not claim to inspect images, videos, screenshots, or other visual inputs. If visual evidence is required and the parent did not provide a textual description, report that limitation clearly.
Do not spawn additional subagents unless the user or parent explicitly asks for nested delegation.
Before finishing a task that may need continuation, include a compact handoff containing completed work, remaining work, relevant files/symbols, and blockers. The parent may need to spawn a fresh replacement agent instead of resuming this thread because current Codex versions can lose custom model/provider settings during resume_agent.
"""
'''

    def managed_provider_block() -> str:
        auth = manager.expected_provider_auth()
        provider = profile.provider
        return f'''
{manager.PROVIDER_BEGIN}
[model_providers.{provider}]
name = {manager.toml_string(profile.provider_name)}
base_url = {manager.toml_string(profile.base_url)}
wire_api = "responses"

[model_providers.{provider}.auth]
command = {manager.toml_string(auth["command"])}
args = {manager.toml_string_array(auth["args"])}
timeout_ms = 5000
refresh_interval_ms = 0
{manager.PROVIDER_END}
'''

    def provider_conflicts(provider_config: dict[str, Any] | None) -> list[str]:
        if not provider_config:
            return []
        issues: list[str] = []
        expected = {
            "name": profile.provider_name,
            "base_url": profile.base_url,
            "wire_api": "responses",
        }
        for key, value in expected.items():
            if provider_config.get(key) != value:
                issues.append(f"model_providers.{profile.provider}.{key}")
        auth = provider_config.get("auth")
        if not isinstance(auth, dict):
            issues.append(f"model_providers.{profile.provider}.auth")
            return issues
        for key, value in manager.expected_provider_auth().items():
            if auth.get(key) != value:
                issues.append(f"model_providers.{profile.provider}.auth.{key}")
        return issues

    def fetch_model_placeholder() -> dict[str, Any]:
        return {"slug": profile.model, "_provider_manager_placeholder": True}

    def merged_catalog(
        base: dict[str, Any],
        _placeholder: dict[str, Any],
        parent_model: str,
    ) -> dict[str, Any]:
        models = copy.deepcopy(base.get("models", []))
        existing = next(
            (item for item in models if item.get("slug") == profile.model),
            None,
        )
        if existing is not None:
            # The model already belongs to Codex's catalog. Preserve its picker
            # visibility so this Skill never hides a native model the user
            # already had before configuring the subagent.
            child_model = _patch_catalog_reasoning(existing, profile, preserve_existing=True)
        else:
            template = copy.deepcopy(original_fetch_model())
            child_model = _recursive_model_alias(template, profile.model)
            child_model["slug"] = profile.model
            if "display_name" in child_model:
                child_model["display_name"] = profile.role
            if "description" in child_model:
                child_model["description"] = f"{profile.model} via {profile.provider_name}"
            child_model = _patch_catalog_reasoning(child_model, profile, preserve_existing=False)
            # This entry exists only so the custom subagent runtime can resolve
            # model metadata. Keep it addressable by model id, but do not add it
            # to the main-session model picker.
            child_model["visibility"] = "hide"

        models = [item for item in models if item.get("slug") != profile.model]
        models.append(child_model)

        parent_found = False
        for model in models:
            if model.get("slug") == parent_model:
                model["multi_agent_version"] = manager.PARENT_MULTI_AGENT_VERSION
                parent_found = True
                break
        if not parent_found:
            raise manager.ManagerError("parent_model_missing", f"模型目录中没有父模型 {parent_model}。")
        models.sort(key=lambda item: item.get("slug", ""))
        return {"models": models}

    def store_credential_key(secret: str) -> None:
        secret = secret.strip()
        if not secret:
            raise manager.ManagerError("invalid_api_key", "API Key 不能为空。")
        if "\r" in secret or "\n" in secret:
            raise manager.ManagerError("invalid_api_key", "API Key 不能包含换行。")
        if len(secret.encode("utf-8")) > 8192:
            raise manager.ManagerError("invalid_api_key", "API Key 过长。")
        backend = manager.credential_backend()
        if backend == "macos-keychain":
            manager._macos_store_credential(secret)
            return
        if backend == "windows-credential-manager":
            manager._windows_store_credential(secret)
            return
        raise manager.ManagerError(
            "unsupported_platform",
            "当前只支持 macOS 和 Windows 系统凭据库。",
        )

    def direct_test(paths: Any, codex_bin: str) -> dict[str, Any]:
        env = dict(manager.os.environ)
        env["CODEX_HOME"] = str(paths.home)
        prompt = "Reply exactly DEEPSEEK_DIRECT_OK and nothing else."
        proc = manager.subprocess.run(
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
                profile.model,
                "-c",
                f'model_provider={manager.toml_string(profile.provider)}',
                "-c",
                f'model_reasoning_effort={manager.toml_string(profile.reasoning_effort)}',
                prompt,
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
        )
        if proc.returncode != 0 or "DEEPSEEK_DIRECT_OK" not in proc.stdout:
            raise manager.ManagerError(
                "direct_test_failed",
                "自定义 Provider 直连测试失败。",
                {"stderr": proc.stderr[-1000:]},
            )
        return {"direct": True}

    def native_test(paths: Any, codex_bin: str) -> dict[str, Any]:
        parent_model = manager.choose_parent_model(paths)
        env = dict(manager.os.environ)
        env["CODEX_HOME"] = str(paths.home)
        prompt = (
            f'Use the native spawn_agent tool exactly once. Set agent_type to {profile.role} and fork_turns to none. '
            'Give it this task: Reply exactly NATIVE_DEEPSEEK_OK. '
            "Then wait for that subagent and return only its final response."
        )
        proc = manager.subprocess.run(
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
            raise manager.ManagerError(
                "native_test_failed",
                f"新 Codex 任务中的原生 spawn_agent(agent_type={profile.role}) 测试失败。",
                {"stderr": proc.stderr[-1200:]},
            )

        child_ids: list[str] = []
        child_messages: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = event.get("item") or {}
            if (
                event.get("type") == "item.completed"
                and item.get("type") == "collab_tool_call"
                and item.get("tool") == "spawn_agent"
            ):
                child_ids.extend(item.get("receiver_thread_ids") or [])
            if (
                event.get("type") == "item.completed"
                and item.get("type") == "collab_tool_call"
                and item.get("tool") == "wait"
            ):
                for receiver_id, state in (item.get("agents_states") or {}).items():
                    if not isinstance(state, dict):
                        continue
                    message = state.get("message")
                    if state.get("status") == "completed" and isinstance(message, str):
                        child_messages[receiver_id] = message.strip()

        child_id = child_ids[0] if len(child_ids) == 1 else None
        child_message = child_messages.get(child_id) if child_id else None
        metadata = manager.wait_for_child_metadata(paths, child_id) if child_id else None
        expected = {
            "model_provider": profile.provider,
            "model": profile.model,
            "reasoning_effort": profile.reasoning_effort,
            "agent_role": profile.role,
        }
        if len(child_ids) != 1 or child_message != "NATIVE_DEEPSEEK_OK" or metadata != expected:
            raise manager.ManagerError(
                "native_route_mismatch",
                "原生子 Agent 路由验收证据不完整或不符合当前 Provider Profile。",
                {
                    "child_ids": child_ids,
                    "child_message": child_message,
                    "metadata": metadata,
                    "expected": expected,
                },
            )
        return {
            "desktop_fresh_session_native": True,
            "child_id": child_id,
            **expected,
        }

    manager.expected_agent_text = expected_agent_text
    manager.managed_provider_block = managed_provider_block
    manager.provider_conflicts = provider_conflicts
    manager.fetch_official_deepseek_model = fetch_model_placeholder
    manager.merged_catalog = merged_catalog
    manager.store_credential_key = store_credential_key
    manager.direct_test = direct_test
    manager.native_test = native_test


def cleanup_replaced_role(
    home: Path,
    old_profile: ProviderProfile,
    new_profile: ProviderProfile,
    old_manifest: dict[str, Any],
) -> dict[str, Any]:
    if old_profile.role == new_profile.role:
        return {}
    old_agent = home / "agents" / f"{old_profile.role}.toml"
    if not old_agent.is_file():
        return {"previous_role": old_profile.role, "old_agent_removed": False}
    expected_hash = old_manifest.get("agent_sha256")
    if old_manifest.get("managed_agent_file") and expected_hash:
        try:
            current_hash = manager.sha256_text_file(old_agent)
        except OSError:
            current_hash = None
        if current_hash == expected_hash:
            old_agent.unlink()
            return {
                "previous_role": old_profile.role,
                "old_agent_removed": True,
                "old_agent_path": str(old_agent),
            }
    return {
        "previous_role": old_profile.role,
        "old_agent_removed": False,
        "old_agent_preserved": str(old_agent),
    }


def enrich(payload: dict[str, Any], profile: ProviderProfile) -> dict[str, Any]:
    result = dict(payload)
    result["provider_profile"] = {
        "mode": profile.mode,
        "provider": profile.provider,
        "provider_name": profile.provider_name,
        "base_url": profile.base_url,
        "model": profile.model,
        "role": profile.role,
        "role_auto": profile.role_auto,
        "reasoning_effort": profile.reasoning_effort,
        "reasoning_efforts": list(profile.reasoning_efforts) if profile.reasoning_efforts else None,
        "multi_agent_version": profile.multi_agent_version,
        "effective_multi_agent_version": profile.effective_multi_agent_version,
        "backend": profile.backend,
        "wire_api": "responses",
        "resume_policy": "fresh-spawn-handoff",
        "credential_target": profile.credential_target,
    }
    warnings = list(result.get("warnings") or [])
    if profile.multi_agent_version == "v2" and profile.backend == "external":
        warnings.append(
            "已强制启用 multi-agent v2，但第三方/跨 Provider 后端可能无法消费 OpenAI 专有的加密 agent_message；子 Agent 收不到任务时请切回 auto 或 v1。"
        )
    warnings.append(
        "当前 Codex 的 resume_agent 可能恢复历史但丢失自定义子 Agent 的原 model/provider/reasoning 配置；第三方线程建议 fresh spawn，并把上一子 Agent 的 handoff 作为上下文传入。"
    )
    if result.get("old_agent_preserved"):
        warnings.append(
            f"旧角色文件已被修改或无法确认由本 Skill 管理，因此保留：{result['old_agent_preserved']}"
        )
    result["warnings"] = warnings
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("status", "setup", "test", "repair", "disable", "uninstall", "profile"),
    )
    parser.add_argument("--codex-home")
    parser.add_argument("--api-key-stdin", action="store_true")
    parser.add_argument("--replace-api-key-stdin", action="store_true")
    parser.add_argument("--skip-live-test", action="store_true")
    parser.add_argument("--remove-credential", action="store_true")
    parser.add_argument("--json", action="store_true")

    provider = parser.add_argument_group("provider")
    provider.add_argument("--official", action="store_true")
    provider.add_argument("--base-url")
    provider.add_argument("--model")
    provider.add_argument("--provider")
    provider.add_argument("--provider-name")
    provider.add_argument("--role", "--agent-name", dest="role")
    provider.add_argument(
        "--reasoning-effort",
        help="当前使用的 reasoning effort；支持 Codex 已知值和模型自定义的任意非空字符串。",
    )
    provider.add_argument(
        "--reasoning-efforts",
        help="可选：逗号分隔的模型支持档位，用于写入模型目录，例如 low,medium,high,xhigh。",
    )
    provider.add_argument(
        "--multi-agent-version",
        choices=("auto", "v1", "v2"),
    )
    provider.add_argument(
        "--backend",
        choices=("external", "openai"),
        help="auto 路由判断：external 默认 v1；openai 默认 v2。",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    bootstrap_paths = manager.resolve_paths(args.codex_home)

    try:
        old_profile_present = profile_path(bootstrap_paths).is_file()
        old_profile = load_profile(bootstrap_paths)
        old_manifest = manager.read_manifest(bootstrap_paths)
        profile, overridden = profile_from_args(args, bootstrap_paths)
        if args.command not in {"setup", "repair"} and overridden:
            raise manager.ManagerError(
                "profile_override_not_allowed",
                "Provider 参数只允许在 setup 或 repair 时修改。",
            )

        apply_profile(profile)
        paths = manager.resolve_paths(args.codex_home)

        if args.command == "profile":
            payload = manager.result("profile", profile=asdict(profile))
            manager.emit(enrich(payload, profile), args.json)
            return 0

        codex_bin = (
            manager.find_desktop_codex()
            if args.command in {"status", "setup", "repair", "test"}
            else None
        )

        context = manager.operation_lock(paths) if args.command != "status" else _NullContext()
        with context:
            old_secret: str | None = None
            replacing_secret = False
            if args.replace_api_key_stdin:
                if args.command not in {"setup", "repair"}:
                    raise manager.ManagerError(
                        "invalid_command",
                        "--replace-api-key-stdin 只允许用于 setup 或 repair。",
                    )
                if not manager.credential_available():
                    raise manager.ManagerError(
                        "unsupported",
                        "当前平台没有可用的系统凭据库。",
                    )
                old_secret = manager.read_credential_key() if manager.credential_has_key() else None
                secret = sys.stdin.readline().strip()
                if not secret:
                    raise manager.ManagerError(
                        "credential_missing",
                        "标准输入中没有 API Key。",
                    )
                manager.store_credential_key(secret)
                secret = ""
                replacing_secret = True

            try:
                if args.command == "status":
                    payload = manager.static_status(paths, codex_bin)
                elif args.command in {"setup", "repair"}:
                    payload = manager.setup(
                        paths,
                        codex_bin or "",
                        args.api_key_stdin,
                        args.skip_live_test,
                    )
                    if payload.get("status") not in {
                        "credential_missing",
                        "partial",
                        "failed",
                    }:
                        save_profile(paths, profile)
                        if old_profile_present:
                            payload.update(
                                cleanup_replaced_role(
                                    paths.home,
                                    old_profile,
                                    profile,
                                    old_manifest,
                                )
                            )
                elif args.command == "test":
                    payload = manager.run_tests(paths, codex_bin or "")
                elif args.command == "disable":
                    payload = manager.disable(paths)
                else:
                    payload = manager.uninstall(paths, args.remove_credential)
                    remove_profile(paths)
            except Exception:
                if replacing_secret:
                    if old_secret is None:
                        manager.remove_credential_key()
                    else:
                        manager.store_credential_key(old_secret)
                raise

        payload = enrich(payload, profile)
        manager.emit(payload, args.json)
        return 0 if payload["status"] not in {"partial", "credential_missing"} else 2
    except manager.ManagerError as exc:
        manager.emit(manager.result(exc.code, message=str(exc), **exc.details), args.json)
        return 2
    except manager.subprocess.TimeoutExpired:
        manager.emit(manager.result("timeout", message="操作超时，未输出任何凭据。"), args.json)
        return 3
    except Exception as exc:
        manager.emit(
            manager.result("failed", message=f"{type(exc).__name__}: {exc}"),
            args.json,
        )
        return 1


class _NullContext:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False


if __name__ == "__main__":
    raise SystemExit(main())
