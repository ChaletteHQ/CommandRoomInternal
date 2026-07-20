#!/usr/bin/env python3
"""
S3 rider (Stage D) — AI-proposed, user-approved commitment noise thresholds
(Phase 6, Round 2).

The Stage-D capture floor (clear owner + clear deliverable + real consequence)
is what cut one live workspace's open set 71→33 — but it is a HARDCODED global
rule. The S3 rider makes noise thresholds learnable: when a particular
counterparty's captured commitments are mostly noise (repeatedly resolved as
`dropped` / `not mine`), this pass PROPOSES a `never-track` rule for that source,
the CEO approves it through the review widget, and on approval the rule is
appended to `_hq/config/commitment-rules.md` — the SAME file the capture floor
already reads before writing (COMMITMENT_SCHEMA §"Extraction triggers", item 6;
the `never track this` triage action writes there too). It rides the same
propose-approve machinery as the other loops (`proposal_ledger` cooldowns, the
review widget) — never a silent capture change.

Signal: a commitment `data.resolution` of `dropped` is the CEO saying "this
wasn't worth tracking." A counterparty above a small-n floor with a high drop
rate is a noise source. stdlib only; pure analysis, one small append helper.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Dict, List, Optional

try:
    from events_io import iter_events
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from events_io import iter_events  # type: ignore

MIN_COMMITMENTS = 8      # small-n floor: ≥8 resolved commitments from a source
MIN_DROP_RATE = 0.5      # ≥50% dropped → a noise source worth a rule
CAP = 3
PASS_NAME = "s3_commitment_noise"

_DROPPED = {"dropped"}


def analyze_noise(workspace_root) -> Dict[str, dict]:
    """Per-counterparty drop stats over resolved commitments. Returns
    {counterparty_key: {name, total, dropped, drop_rate}}. A commitment's
    counterparty is `data.counterparty_id` (preferred) or `data.counterparty_name`.
    Joins `commitment` events to their `commitment_resolved` by id. Never raises."""
    commitments: Dict[str, dict] = {}
    resolutions: Dict[str, str] = {}
    try:
        events = list(iter_events(Path(workspace_root) / "_hq" / "data"))
    except Exception:
        return {}
    for ev in events:
        t = ev.get("type")
        data = ev.get("data") or {}
        if t == "commitment":
            cid = data.get("id")
            # SUB1 D6 — sub-items never feed noise stats: a child is
            # user-created decomposition, not capture noise; its drops say
            # nothing about the extractor's precision for that counterparty.
            if cid and not data.get("parent_id"):
                commitments[cid] = {
                    "counterparty_id": data.get("counterparty_id"),
                    "counterparty_name": data.get("counterparty_name"),
                    "title": data.get("title") or "",
                }
        elif t == "commitment_resolved":
            cid = data.get("commitment_id") or data.get("id")
            res = data.get("resolution")
            if cid and res:
                resolutions[cid] = res

    stats: Dict[str, dict] = {}
    for cid, c in commitments.items():
        res = resolutions.get(cid)
        if not res:
            continue  # still open — not a terminal signal
        key = c.get("counterparty_id") or c.get("counterparty_name")
        if not key:
            continue
        slot = stats.setdefault(str(key), {
            "name": c.get("counterparty_name") or str(key),
            "total": 0, "dropped": 0, "drop_rate": 0.0})
        slot["total"] += 1
        if res in _DROPPED:
            slot["dropped"] += 1
    for slot in stats.values():
        slot["drop_rate"] = (slot["dropped"] / slot["total"]) if slot["total"] else 0.0
    return stats


def _fp(counterparty_key: str) -> str:
    return "cnz_" + hashlib.sha256(str(counterparty_key).encode("utf-8")).hexdigest()[:16]


def propose_noise_rules(
    stats: Dict[str, dict],
    *,
    existing_rules: Optional[List[str]] = None,
    cooldown_fingerprints: Optional[set] = None,
    cap: int = CAP,
    min_commitments: int = MIN_COMMITMENTS,
    min_drop_rate: float = MIN_DROP_RATE,
) -> List[dict]:
    """Propose per-source noise rules where the drop rate clears the floor. Pure.
    Returns up to `cap`:
      {fingerprint, counterparty_key, name, total, dropped, drop_rate, pattern, plain}
    `pattern` is the one-line never-track pattern to append on approval."""
    existing = {(r or "").strip().lower() for r in (existing_rules or [])}
    cooling = cooldown_fingerprints or set()
    out: List[dict] = []
    for key, s in sorted(stats.items(), key=lambda kv: -kv[1]["dropped"]):
        if s["total"] < min_commitments or s["drop_rate"] < min_drop_rate:
            continue
        pattern = f"never-track: low-consequence items from {s['name']}"
        if pattern.strip().lower() in existing:
            continue
        fp = _fp(key)
        if fp in cooling:
            continue
        out.append({
            "fingerprint": fp, "counterparty_key": key, "name": s["name"],
            "total": s["total"], "dropped": s["dropped"],
            "drop_rate": round(s["drop_rate"], 2), "pattern": pattern,
            "plain": (f"You've dropped {s['dropped']} of the last {s['total']} things "
                      f"I captured about {s['name']} — want me to stop tracking "
                      f"low-stakes items from them?"),
        })
        if len(out) >= cap:
            break
    return out


# ---------------------------------------------------------------------------
# Store — _hq/config/commitment-rules.md (read by every commitment producer)
# ---------------------------------------------------------------------------

def _rules_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "config" / "commitment-rules.md"


def load_never_track_rules(workspace_root) -> List[str]:
    """Existing never-track pattern lines (the `never-track:`-prefixed lines).
    Never raises."""
    path = _rules_path(workspace_root)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        s = ln.strip().lstrip("-* ").strip()
        if s.lower().startswith("never-track:"):
            out.append(s)
    return out


def append_never_track_rule(workspace_root, pattern: str) -> bool:
    """Append one approved never-track pattern to `_hq/config/commitment-rules.md`
    (the file the capture floor reads). Deduped; creates the file with a header
    if absent. Additive-only — never rewrites existing rules. Returns True on
    write. Never raises."""
    pattern = (pattern or "").strip()
    if not pattern:
        return False
    if pattern.strip().lower() in {r.strip().lower() for r in load_never_track_rules(workspace_root)}:
        return False
    path = _rules_path(workspace_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if not existing.strip():
            existing = ("# Commitment capture rules\n\n"
                        "Producers read this file before writing and skip items "
                        "matching a `never-track` pattern.\n\n")
        if not existing.endswith("\n"):
            existing += "\n"
        try:
            from atomic_write import atomic_write_text
            atomic_write_text(path, existing + f"- {pattern}\n", holder="commitment_noise")
        except Exception:
            path.write_text(existing + f"- {pattern}\n", encoding="utf-8")
        return True
    except Exception:
        return False


__all__ = [
    "MIN_COMMITMENTS", "MIN_DROP_RATE", "CAP", "PASS_NAME",
    "analyze_noise", "propose_noise_rules",
    "load_never_track_rules", "append_never_track_rule",
]
