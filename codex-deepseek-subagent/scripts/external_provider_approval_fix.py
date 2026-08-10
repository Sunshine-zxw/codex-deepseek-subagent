#!/usr/bin/env python3
"""Route approval requests to the user when custom subagent providers are in use.

Codex Guardian/Auto-review may try to call the internal model id
``codex-auto-review`` through the active custom provider. Many relays cannot serve
that model, causing approval-required operations to fail with 4xx errors.

This helper does not weaken the sandbox and does not grant extra permissions. It
only changes the approval reviewer to ``user`` so any genuine escalation is shown
to the user instead of being sent to an incompatible third-party reviewer path.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path
from typing import Any

STATE_DIR = "codex-deepseek-subagent"
REGISTRY_NAME = "registry.json"
LEGACY_PROFILE_NAME = "provider-profile.json"
STATE_NAME = "approval-compat.json"


class FixError(RuntimeError):
    pass


def resolve_home(value: str | None = None) -> Path:
    return Path(value or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixError(f"无法读取 JSON：{path}") from exc
    if not isinstance(payload, dict):
        raise FixError(f"JSON 根节点不是对象：{path}")
    return payload


def has_external_provider(home: Path) -> bool:
    state = home / STATE_DIR
    registry = state / REGISTRY_NAME
    if registry.is_file():
        payload = read_json(registry)
        providers = payload.get("providers") or {}
        agents = payload.get("agents") or {}
        if not isinstance(providers, dict) or not isinstance(agents, dict):
            raise FixError("registry 的 providers/agents 字段无效。")
        used = {
            spec.get("provider")
            for spec in agents.values()
            if isinstance(spec, dict) and isinstance(spec.get("provider"), str)
        }
        return any(
            provider_id in used
            and isinstance(spec, dict)
            and spec.get("backend", "external") == "external"
            for provider_id, spec in providers.items()
        )

    legacy = state / LEGACY_PROFILE_NAME
    if legacy.is_file():
        payload = read_json(legacy)
        profile = payload.get("profile") or {}
        return isinstance(profile, dict) and profile.get("backend", "external") == "external"
    return False


def top_level_string(text: str, key: str) -> str | None:
    try:
        value = tomllib.loads(text).get(key)
    except tomllib.TOMLDecodeError as exc:
        raise FixError(f"config.toml 无法解析：{exc}") from exc
    return value if isinstance(value, str) else None


def set_top_level_string(text: str, key: str, value: str) -> str:
    lines = text.splitlines()
    first_table = next((i for i, line in enumerate(lines) if line.strip().startswith("[")), len(lines))
    assignment = f'{key} = {json.dumps(value)}'
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index in range(first_table):
        if pattern.match(lines[index]):
            lines[index] = assignment
            return "\n".join(lines).rstrip() + "\n"
    lines.insert(first_table, assignment)
    if first_table and first_table < len(lines) - 1 and lines[first_table + 1].strip():
        lines.insert(first_table + 1, "")
    return "\n".join(lines).rstrip() + "\n"


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def apply_fix(home: Path, *, write: bool = True) -> dict[str, Any]:
    if not has_external_provider(home):
        return {"status": "noop", "reason": "no_external_provider"}

    config = home / "config.toml"
    text = config.read_text(encoding="utf-8") if config.is_file() else ""
    previous = top_level_string(text, "approvals_reviewer") if text.strip() else None
    if previous == "user":
        return {
            "status": "ok",
            "approvals_reviewer": "user",
            "changed": False,
            "sandbox_changed": False,
        }

    new_text = set_top_level_string(text, "approvals_reviewer", "user")
    if new_text.strip():
        try:
            tomllib.loads(new_text)
        except tomllib.TOMLDecodeError as exc:
            raise FixError(f"生成的 config.toml 无法解析：{exc}") from exc

    if write:
        state = home / STATE_DIR / STATE_NAME
        if not state.is_file():
            atomic_write(
                state,
                (json.dumps({"previous_approvals_reviewer": previous}, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            )
        atomic_write(config, new_text.encode("utf-8"))

    return {
        "status": "patched" if write else "would_patch",
        "previous_approvals_reviewer": previous,
        "approvals_reviewer": "user",
        "changed": True,
        "sandbox_changed": False,
        "approval_policy_changed": False,
        "note": "第三方子 Agent 的真实提权请求将交给用户审批；workspace 内无需提权的操作仍按原 sandbox 执行。",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = apply_fix(resolve_home(args.codex_home), write=not args.check)
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result)
        return 0
    except (FixError, OSError) as exc:
        result = {"status": "failed", "message": str(exc)}
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
