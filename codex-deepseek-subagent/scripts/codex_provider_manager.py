#!/usr/bin/env python3
"""Configure Codex native DeepSeek subagents through official or custom Responses-compatible providers."""

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
from typing import Any
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
ALLOWED_EFFORTS = ("low", "high", "max")
PROFILE_NAME = "provider-profile.json"
PROFILE_SCHEMA_VERSION = 2
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
    reasoning_effort: str = DEFAULT_EFFORT
    multi_agent_version: str = "auto"
    backend: str = "external"

    @property
    def effective_multi_agent_version(self) -> str:
        if self.multi_agent_version in {"v1", "v2"}:
            return self.multi_agent_version
        # Cross-provider / non-OpenAI backends currently need plaintext v1.
        return "v2" if self.backend == "openai" else "v1"

    @property
    def credential_target(self) -> str:
        if self.mode == "official" and self.base_url == OFFICIAL_BASE_URL:
            return "codex-deepseek-api-key"
        digest = hashlib.sha256(
            f"{self.provider}\0{self.base_url}".encode("utf-8")
        ).hexdigest()[:12]
        return f"codex-deepseek-api-key-{digest}"


def normalize_base_url(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise manager.ManagerError(
            "invalid_base_url",
            "base URL 必须是有效的 http:// 或 https:// 地址。",
        )
    if parsed.username or parsed.password or parsed.fragment:
        raise manager.ManagerError(
            "invalid_base_url",
            "base URL 不应包含用户名、密码或 URL fragment。",
        )
    return value.rstrip("/") + "/"


def validate_profile(profile: ProviderProfile) -> ProviderProfile:
    if profile.mode not in {"official", "custom"}:
        raise manager.ManagerError("invalid_profile", "mode 只能是 official 或 custom。")
    if not profile.model.strip():
        raise manager.ManagerError("invalid_model", "model 不能为空。")
    if not PROVIDER_RE.fullmatch(profile.provider):
        raise manager.ManagerError(
            "invalid_provider",
            "provider 只能包含字母、数字、下划线和连字符。",
        )
    if not ROLE_RE.fullmatch(profile.role):
        raise manager.ManagerError(
            "invalid_role",
            "role 只能包含字母、数字、下划线和连字符。",
        )
    effort = profile.reasoning_effort.strip() or DEFAULT_EFFORT
    if effort not in ALLOWED_EFFORTS:
        raise manager.ManagerError(
            "invalid_reasoning_effort",
            f"reasoning effort 只能是 {', '.join(ALLOWED_EFFORTS)}。",
        )
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
            "model": profile.model.strip(),
            "provider": profile.provider.strip(),
            "provider_name": profile.provider_name.strip() or profile.provider,
            "role": profile.role.strip(),
            "reasoning_effort": effort,
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
    if schema not in {1, PROFILE_SCHEMA_VERSION}:
        raise manager.ManagerError(
            "unsupported_profile",
            f"不支持的 Provider profile schema：{schema}",
        )
    raw = payload.get("profile")
    if not isinstance(raw, dict):
        raise manager.ManagerError("invalid_profile", "Provider profile 缺少 profile 对象。")
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
    names = (
        "base_url",
        "model",
        "provider",
        "provider_name",
        "reasoning_effort",
        "multi_agent_version",
        "backend",
    )
    has_override = any(getattr(args, name, None) is not None for name in names) or bool(
        getattr(args, "official", False)
    )
    if not has_override:
        return existing, False

    if args.official:
        if any(getattr(args, name, None) is not None for name in names):
            raise manager.ManagerError(
                "invalid_profile",
                "--official 不能与自定义 Provider 参数同时使用。",
            )
        return ProviderProfile(), True

    base = ProviderProfile() if not profile_path(paths).is_file() else existing
    values = asdict(base)
    for field in names:
        value = getattr(args, field, None)
        if value is not None:
            values[field] = value

    normalized_url = normalize_base_url(values["base_url"])
    values["base_url"] = normalized_url
    values["mode"] = (
        "official"
        if normalized_url == OFFICIAL_BASE_URL
        and values["model"] == OFFICIAL_MODEL
        and values["provider"] == DEFAULT_PROVIDER
        else "custom"
    )
    return validate_profile(ProviderProfile(**values)), True


def _recursive_model_alias(value: Any, model: str) -> Any:
    if isinstance(value, dict):
        return {key: _recursive_model_alias(item, model) for key, item in value.items()}
    if isinstance(value, list):
        return [_recursive_model_alias(item, model) for item in value]
    if isinstance(value, str) and value == OFFICIAL_MODEL:
        return model
    return value


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
        return f'''name = "{profile.role}"
description = "Text-only DeepSeek subagent for coding, repository research, review, and verification. Do not use it for image, video, screenshot, or other visual inspection; the parent agent must inspect visual inputs and pass the findings as text."
model = "{profile.model}"
model_provider = "{profile.provider}"
model_reasoning_effort = "{profile.reasoning_effort}"
developer_instructions = """
You are a focused DeepSeek subagent running inside Codex.

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

    def fetch_model_template() -> dict[str, Any]:
        model = copy.deepcopy(original_fetch_model())
        if profile.model == OFFICIAL_MODEL:
            return model
        model = _recursive_model_alias(model, profile.model)
        model["slug"] = profile.model
        return model

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
                "DeepSeek/自定义 Provider 直连测试失败。",
                {"stderr": proc.stderr[-1000:]},
            )
        return {"direct": True}

    manager.expected_agent_text = expected_agent_text
    manager.managed_provider_block = managed_provider_block
    manager.provider_conflicts = provider_conflicts
    manager.fetch_official_deepseek_model = fetch_model_template
    manager.store_credential_key = store_credential_key
    manager.direct_test = direct_test


def enrich(payload: dict[str, Any], profile: ProviderProfile) -> dict[str, Any]:
    result = dict(payload)
    result["provider_profile"] = {
        "mode": profile.mode,
        "provider": profile.provider,
        "provider_name": profile.provider_name,
        "base_url": profile.base_url,
        "model": profile.model,
        "role": profile.role,
        "reasoning_effort": profile.reasoning_effort,
        "multi_agent_version": profile.multi_agent_version,
        "effective_multi_agent_version": profile.effective_multi_agent_version,
        "backend": profile.backend,
        "wire_api": "responses",
        "resume_policy": "fresh-spawn-handoff",
        "credential_target": profile.credential_target,
    }
    warnings: list[str] = []
    if profile.multi_agent_version == "v2" and profile.backend == "external":
        warnings.append(
            "已强制启用 multi-agent v2，但第三方/跨 Provider 后端可能无法消费 OpenAI 专有的加密 agent_message；子 Agent 收不到任务时请切回 auto 或 v1。"
        )
    warnings.append(
        "当前 Codex 的 resume_agent 可能恢复历史但丢失自定义子 Agent 的原 model/provider/reasoning 配置；第三方 DeepSeek 线程建议 fresh spawn，并把上一子 Agent 的 handoff 作为上下文传入。"
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
    provider.add_argument("--reasoning-effort", choices=ALLOWED_EFFORTS)
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
    paths = manager.resolve_paths(args.codex_home)

    try:
        profile, overridden = profile_from_args(args, paths)
        if args.command not in {"setup", "repair"} and overridden:
            raise manager.ManagerError(
                "profile_override_not_allowed",
                "Provider 参数只允许在 setup 或 repair 时修改。",
            )
        apply_profile(profile)

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
