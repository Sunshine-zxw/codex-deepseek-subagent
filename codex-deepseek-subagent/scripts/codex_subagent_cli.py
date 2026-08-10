#!/usr/bin/env python3
"""Canonical CLI facade for the multi-provider Codex subagent manager."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


multi = _load("codex_subagent_manager_impl", HERE / "codex_subagent_manager.py")
approval_fix = _load("external_provider_approval_fix_impl", HERE / "external_provider_approval_fix.py")

_original_reconcile = multi.reconcile
_original_status = multi.status


def _safe_reconcile(registry, codex_home=None):
    result = _original_reconcile(registry, codex_home)
    compat = approval_fix.apply_fix(approval_fix.resolve_home(codex_home), write=True)
    return {**result, "external_provider_approval_compat": compat}


def _safe_status(registry, codex_home=None):
    result = _original_status(registry, codex_home)
    compat = approval_fix.apply_fix(approval_fix.resolve_home(codex_home), write=False)
    result["external_provider_approval_compat"] = compat
    if compat.get("status") == "would_patch":
        warnings = list(result.get("warnings") or [])
        warnings.append(
            "检测到 external Provider，但 approvals_reviewer 仍不是 user；Auto-review 可能通过第三方 Provider 请求 codex-auto-review 并失败。运行 repair 应用兼容修复。"
        )
        result["warnings"] = warnings
        if result.get("status") == "configured":
            result["status"] = "partial"
    return result


# All mutating multi-provider commands pass through reconcile(). Wrapping it keeps
# the compatibility policy in one place without weakening sandbox permissions.
multi.reconcile = _safe_reconcile
multi.status = _safe_status


if __name__ == "__main__":
    raise SystemExit(multi.main())
