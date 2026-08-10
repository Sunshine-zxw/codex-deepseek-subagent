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


def _safe_reconcile(registry, codex_home=None):
    result = _original_reconcile(registry, codex_home)
    compat = approval_fix.apply_fix(approval_fix.resolve_home(codex_home), write=True)
    return {**result, "external_provider_approval_compat": compat}


# All mutating multi-provider commands pass through reconcile(). Wrapping it keeps
# the compatibility policy transactional from the caller's perspective without
# changing the low-level manager implementation.
multi.reconcile = _safe_reconcile


if __name__ == "__main__":
    raise SystemExit(multi.main())
