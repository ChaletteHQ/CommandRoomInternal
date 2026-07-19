#!/usr/bin/env python3
"""
Capability manifest loader + fingerprint re-pair (Layer A2 / A1b).

Loads `shared/data-schemas/connector_capabilities.json` (the KNOWN provider
rows) and provides:
  - capability lookup per provider (detect-once-persist: a workspace's stored
    detected row overrides these defaults; this module supplies the known
    defaults + the unknown-provider baseline)
  - fingerprint re-pair: match a reconnected server's tool-name SET to a known
    provider when the server-id changed (A1b). Confirm-with-user is the caller's
    job (interactive only — silent/scheduled sessions skip-and-flag, R13).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_MANIFEST_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data-schemas" / "connector_capabilities.json"
)


@lru_cache(maxsize=1)
def load_manifest() -> Dict[str, Any]:
    """The parsed manifest. {} on any failure (never raises — an unreadable
    manifest degrades to the baseline, = feature-detect everything)."""
    try:
        return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def providers() -> Dict[str, Any]:
    m = load_manifest()
    p = m.get("providers")
    return p if isinstance(p, dict) else {}


def provider_row(provider: Optional[str]) -> Optional[Dict[str, Any]]:
    if not provider:
        return None
    return providers().get(provider.lower())


def capabilities_for(provider: Optional[str],
                     detected: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Effective capabilities for a provider. `detected` (a per-workspace
    detect-once row) overrides the manifest defaults key-by-key (H-E). An
    unknown provider returns the category baseline (fail-closed booleans)."""
    row = provider_row(provider) or {}
    caps = dict(row.get("capabilities") or {})
    if not caps:
        # unknown provider — baseline by (guessed) category
        base = load_manifest().get("baseline") or {}
        cat = (row.get("category") or "email")
        caps = dict(base.get(cat) or base.get("email") or {})
    if detected:
        caps.update(detected)
    return caps


def supports(provider: Optional[str], capability: str,
             detected: Optional[Dict[str, Any]] = None) -> bool:
    """True iff the provider supports `capability` (missing = False,
    fail-closed). Non-bool capability values (threading_model, etc.) return
    True when present + truthy."""
    val = capabilities_for(provider, detected).get(capability)
    if isinstance(val, bool):
        return val
    return bool(val) and val not in ("", "false", "none")


def is_zapier_provider(provider: Optional[str]) -> bool:
    row = provider_row(provider) or {}
    return bool(row.get("is_zapier"))


# ---------------------------------------------------------------------------
# Fingerprint re-pair (A1b) — match a server's tool-name set to a provider
# ---------------------------------------------------------------------------

def _op_names(tool_ids: Iterable[str]) -> set:
    """Reduce fully-qualified tool ids to their bare operation names."""
    ops = set()
    for tid in tool_ids:
        if not isinstance(tid, str):
            continue
        parts = tid.split("__")
        ops.add(parts[-1].lower() if parts else tid.lower())
    return ops


def match_fingerprint(tool_ids: Iterable[str], *, min_overlap: int = 2
                      ) -> List[Tuple[str, int]]:
    """Rank known providers by how many of their fingerprint tools appear in
    `tool_ids` (a server's operation list). Returns [(provider, hits), …] sorted
    best-first, filtered to hits >= min_overlap. Empty = no confident match →
    caller feature-detects into the baseline (C4)."""
    ops = _op_names(tool_ids)
    scored: List[Tuple[str, int]] = []
    for name, row in providers().items():
        fp = row.get("fingerprint") or []
        hits = sum(1 for f in fp if f.lower() in ops)
        if hits >= min_overlap:
            scored.append((name, hits))
    scored.sort(key=lambda t: (-t[1], t[0]))
    return scored


def best_fingerprint_match(tool_ids: Iterable[str], *, min_overlap: int = 2
                           ) -> Optional[str]:
    """The single best provider match for a server's tool set, or None. Used to
    re-pin a reconnected server whose UUID changed (A1b). The caller confirms
    with the user before re-pinning (interactive only, R13)."""
    ranked = match_fingerprint(tool_ids, min_overlap=min_overlap)
    return ranked[0][0] if ranked else None


__all__ = [
    "load_manifest", "providers", "provider_row",
    "capabilities_for", "supports", "is_zapier_provider",
    "match_fingerprint", "best_fingerprint_match",
]
