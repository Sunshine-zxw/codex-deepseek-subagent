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
STATE_SCHEMA_VERSION = 2
APPROVAL_KEY = "approvals_reviewer"


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


def parse_toml(text: str) -> dict[str, Any]:
    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise FixError(f"config.toml 无法解析：{exc}") from exc
    if not isinstance(payload, dict):
        raise FixError("config.toml 根节点不是对象。")
    return payload


def top_level_string(text: str, key: str) -> str | None:
    return _location_value(parse_toml(text), {"kind": "top_level"})[1]


def _profile_header_candidates(profile: str) -> set[str]:
    candidates = {f"[profiles.{profile}]"}
    escaped = json.dumps(profile, ensure_ascii=False)
    candidates.add(f"[profiles.{escaped}]")
    single_quoted = profile.replace("'", "''")
    candidates.add(f"[profiles.'{single_quoted}']")
    return candidates


def _header_value(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("["):
        return None
    # Profile names accepted by this manager do not contain '#'. Keeping the
    # header parser deliberately narrow avoids treating comments as tables.
    return stripped.split("#", 1)[0].rstrip()


def _section_bounds(lines: list[str], candidates: set[str]) -> tuple[int, int] | None:
    start: int | None = None
    for index, line in enumerate(lines):
        header = _header_value(line)
        if start is None:
            if header in candidates:
                start = index
            continue
        if header is not None:
            return start, index
    return (start, len(lines)) if start is not None else None


def _assignment_pattern(key: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*{re.escape(key)}\s*=")


def _profile_section_location(text: str, profile: str) -> dict[str, Any] | None:
    bounds = _section_bounds(text.splitlines(), _profile_header_candidates(profile))
    if bounds is None:
        return None
    return {"kind": "profile", "profile": profile}


def _active_location(text: str, parsed: dict[str, Any]) -> dict[str, Any]:
    profile = parsed.get("profile")
    profiles = parsed.get("profiles")
    if (
        isinstance(profile, str)
        and profile
        and isinstance(profiles, dict)
        and profile in profiles
        and isinstance(profiles[profile], dict)
        and _profile_section_location(text, profile) is not None
    ):
        return {"kind": "profile", "profile": profile}
    return {"kind": "top_level"}


def _location_key(location: dict[str, Any]) -> str:
    if location.get("kind") == "profile":
        return f"profile:{location.get('profile')}"
    return "top_level"


def _location_label(location: dict[str, Any]) -> str:
    if location.get("kind") == "profile":
        return f"profiles.{location.get('profile')}"
    return "top_level"


def _location_value(parsed: dict[str, Any], location: dict[str, Any]) -> tuple[bool, str | None]:
    if location.get("kind") == "profile":
        profiles = parsed.get("profiles")
        profile = location.get("profile")
        values = profiles.get(profile) if isinstance(profiles, dict) else None
        if not isinstance(values, dict):
            return False, None
        value = values.get(APPROVAL_KEY)
        return APPROVAL_KEY in values, value if isinstance(value, str) else None
    value = parsed.get(APPROVAL_KEY)
    return APPROVAL_KEY in parsed, value if isinstance(value, str) else None


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


def set_profile_string(text: str, profile: str, key: str, value: str) -> str:
    lines = text.splitlines()
    bounds = _section_bounds(lines, _profile_header_candidates(profile))
    if bounds is None:
        raise FixError(f"config.toml 中找不到活动 profile：{profile}")
    start, end = bounds
    assignment = f'{key} = {json.dumps(value, ensure_ascii=False)}'
    pattern = _assignment_pattern(key)
    for index in range(start + 1, end):
        if pattern.match(lines[index]):
            lines[index] = assignment
            return "\n".join(lines).rstrip() + "\n"
    lines.insert(start + 1, assignment)
    return "\n".join(lines).rstrip() + "\n"


def remove_top_level_string(text: str, key: str) -> str:
    lines = text.splitlines()
    first_table = next((i for i, line in enumerate(lines) if line.strip().startswith("[")), len(lines))
    pattern = _assignment_pattern(key)
    lines = [line for index, line in enumerate(lines) if not (index < first_table and pattern.match(line))]
    return "\n".join(lines).rstrip() + "\n"


def remove_profile_string(text: str, profile: str, key: str) -> str:
    lines = text.splitlines()
    bounds = _section_bounds(lines, _profile_header_candidates(profile))
    if bounds is None:
        return text
    start, end = bounds
    pattern = _assignment_pattern(key)
    lines = [
        line
        for index, line in enumerate(lines)
        if not (start < index < end and pattern.match(line))
    ]
    return "\n".join(lines).rstrip() + "\n"


def set_location_string(text: str, location: dict[str, Any], value: str) -> str:
    if location.get("kind") == "profile":
        return set_profile_string(text, str(location["profile"]), APPROVAL_KEY, value)
    return set_top_level_string(text, APPROVAL_KEY, value)


def remove_location_string(text: str, location: dict[str, Any]) -> str:
    if location.get("kind") == "profile":
        return remove_profile_string(text, str(location["profile"]), APPROVAL_KEY)
    return remove_top_level_string(text, APPROVAL_KEY)


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


def _state_path(home: Path) -> Path:
    return home / STATE_DIR / STATE_NAME


def _empty_state() -> dict[str, Any]:
    return {"schema_version": STATE_SCHEMA_VERSION, "changes": []}


def load_state(home: Path) -> dict[str, Any]:
    path = _state_path(home)
    if not path.is_file():
        return _empty_state()
    payload = read_json(path)
    changes = payload.get("changes")
    if isinstance(changes, list):
        normalized = []
        for change in changes:
            if not isinstance(change, dict):
                continue
            location = change.get("location")
            if not isinstance(location, dict) or location.get("kind") not in {"top_level", "profile"}:
                continue
            if location.get("kind") == "profile" and not isinstance(location.get("profile"), str):
                continue
            normalized.append(
                {
                    "location": location,
                    "present": bool(change.get("present")),
                    "value": change.get("value") if isinstance(change.get("value"), str) else None,
                }
            )
        return {"schema_version": STATE_SCHEMA_VERSION, "changes": normalized}

    # Migrate the original one-location state format without losing the
    # user's previous top-level reviewer.
    if "previous_approvals_reviewer" in payload:
        previous = payload.get("previous_approvals_reviewer")
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "changes": [
                {
                    "location": {"kind": "top_level"},
                    "present": previous is not None,
                    "value": previous if isinstance(previous, str) else None,
                }
            ],
        }
    return _empty_state()


def _record_change(state: dict[str, Any], location: dict[str, Any], parsed: dict[str, Any]) -> None:
    changes = state.setdefault("changes", [])
    key = _location_key(location)
    if any(_location_key(item.get("location") or {}) == key for item in changes if isinstance(item, dict)):
        return
    present, value = _location_value(parsed, location)
    changes.append({"location": location, "present": present, "value": value})


def _validate_generated_config(text: str) -> None:
    if text.strip():
        parse_toml(text)


def _compat_result(status: str, *, location: dict[str, Any] | None = None, changed: bool = False, **extra: Any) -> dict[str, Any]:
    result = {
        "status": status,
        "approvals_reviewer": "user"
        if status not in {"noop", "restored", "cleaned", "would_restore", "would_clean"}
        else None,
        "changed": changed,
        "sandbox_changed": False,
        "approval_policy_changed": False,
    }
    if location is not None:
        result["scope"] = _location_label(location)
    result.update(extra)
    return result


def _restore_previous(home: Path, *, write: bool) -> dict[str, Any]:
    state_path = _state_path(home)
    state = load_state(home)
    changes = state.get("changes") or []
    if not changes:
        return {"status": "noop", "reason": "no_compat_state"}

    config = home / "config.toml"
    text = config.read_text(encoding="utf-8") if config.is_file() else ""
    parsed = parse_toml(text) if text.strip() else {}
    new_text = text
    restored: list[str] = []
    skipped: list[str] = []
    for change in changes:
        location = change.get("location")
        if not isinstance(location, dict):
            continue
        key = _location_key(location)
        present, current = _location_value(parsed, location)
        if not present and not change.get("present"):
            restored.append(key)
            continue
        if not present or current != "user":
            skipped.append(key)
            continue
        if change.get("present"):
            new_text = set_location_string(new_text, location, str(change.get("value") or "user"))
        else:
            new_text = remove_location_string(new_text, location)
        parsed = parse_toml(new_text) if new_text.strip() else {}
        restored.append(key)

    changed = new_text != text
    _validate_generated_config(new_text)
    if write:
        if changed:
            atomic_write(config, new_text.encode("utf-8"))
        try:
            state_path.unlink()
        except FileNotFoundError:
            pass

    if write:
        status = "restored" if changed else "cleaned"
    else:
        status = "would_restore" if changed else "would_clean"
    return _compat_result(
        status,
        changed=changed,
        restored=restored,
        skipped=skipped,
        note="已移除第三方 Provider 后，仅恢复仍保持为 user 的兼容性修改；用户手动改过的值不会被覆盖。",
    )


def apply_fix(home: Path, *, write: bool = True) -> dict[str, Any]:
    if not has_external_provider(home):
        return _restore_previous(home, write=write)

    config = home / "config.toml"
    text = config.read_text(encoding="utf-8") if config.is_file() else ""
    parsed = parse_toml(text) if text.strip() else {}
    location = _active_location(text, parsed)
    present, previous = _location_value(parsed, location)
    if previous == "user":
        return _compat_result(
            "ok",
            location=location,
            changed=False,
            previous_approvals_reviewer=previous,
        )

    new_text = set_location_string(text, location, "user")
    _validate_generated_config(new_text)

    if write:
        state = load_state(home)
        _record_change(state, location, parsed)
        atomic_write(
            _state_path(home),
            (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        atomic_write(config, new_text.encode("utf-8"))

    return _compat_result(
        "patched" if write else "would_patch",
        location=location,
        changed=True,
        previous_approvals_reviewer=previous if present else None,
        note="第三方子 Agent 的真实提权请求将交给用户审批；workspace 内无需提权的操作仍按原 sandbox 执行。",
    )


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
