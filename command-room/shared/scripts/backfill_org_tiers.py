#!/usr/bin/env python3
"""
One-shot backfill: give untiered org records a `tier` + `relationship_type`
(ORGSCHEMA1 §2c) and migrate any locally-invented `view_exclude` keys (§3).

Why this exists as a SCRIPT and not prose: the update-bridge migration
`org_reclassification_v2_10_3` was gated to `from_version < 2.10.3`, so on
every current install it silently no-ops — the exact "honesty half shipped,
fix half did not" failure mode. The gate here is keyed on the DATA, not the
version: it fires whenever at least one org record is missing `tier`, and it
never touches an org whose tier is already set. Idempotent by construction.

Tier inference mirrors ORG_AND_THREAD_MODEL.md "Discovery" Stage 2
(volume-tier thresholds), using the last 90 days of events.jsonl signal
scaled to interactions/30d:

    is_primary_focus              -> primary / operating
    21+ interactions per 30d      -> secondary / client
    1-20 interactions per 30d     -> external  / vendor
    0 (no 90d signal)             -> external  / vendor

Zero-signal orgs deliberately land `external`, never `passive`: passive
suppresses the org from the Orgs Map roster, and an automated backfill must
not make records invisible. Passive stays a human decision (or a
view_exclude migration, which WAS a human decision).

relationship_type is only written when unset — an explicit value is the
customer's and is never overwritten (same rule as the update-bridge prose).

DRY-RUN by default. Pass `--apply` to actually write.

Usage:

    python3 shared/scripts/backfill_org_tiers.py <workspace_root> [--apply]
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_write import atomic_write_json_locked, atomic_append_jsonl  # noqa: E402
from entities_io import entities_collection  # noqa: E402
from org_writer import _default_tier, _normalize_legacy_keys, _validate_org  # noqa: E402

MIGRATION_ID = "org_tier_backfill_orgschema1"
WINDOW_DAYS = 90


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def needs_backfill(entities: dict) -> bool:
    """True when at least one org record is missing `tier` or carries the
    legacy `view_exclude` key. This is the WHOLE gate — no version check."""
    for o in entities_collection(entities, "orgs"):
        if not isinstance(o, dict):
            continue
        if not o.get("tier") or "view_exclude" in o or "view_exclude_reason" in o:
            return True
    return False


def _interactions_per_30d(events: list[dict], org_id: str, now: datetime.datetime) -> float:
    cutoff = now - datetime.timedelta(days=WINDOW_DAYS)
    n = 0
    for ev in events:
        if not isinstance(ev, dict):
            continue
        data = ev.get("data") or {}
        refs = set(ev.get("org_ids") or [])
        for key in ("org_id", "primary_org_id"):
            v = ev.get(key) or (data.get(key) if isinstance(data, dict) else None)
            if v:
                refs.add(v)
        if org_id not in refs:
            continue
        ts = ev.get("ts") or ""
        try:
            when = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=datetime.timezone.utc)
        except (ValueError, TypeError):
            continue
        if when >= cutoff:
            n += 1
    return n / (WINDOW_DAYS / 30)


def _infer(org: dict, per30: float) -> tuple[str, str]:
    """(tier, relationship_type) per the Stage 2 volume-tier table."""
    if org.get("is_primary_focus"):
        return "primary", "operating"
    if per30 >= 21:
        return "secondary", "client"
    return "external", "vendor"


def run(workspace_root: str | Path, *, apply: bool = False,
        source_skill: str = "command-room-update-bridge") -> dict:
    ws = Path(workspace_root)
    entities_path = ws / "_hq" / "data" / "entities.json"
    events_path = ws / "_hq" / "data" / "events.jsonl"
    data = json.loads(entities_path.read_text(encoding="utf-8"))
    orgs = entities_collection(data, "orgs")

    events: list[dict] = []
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    now = datetime.datetime.now(datetime.timezone.utc)
    changed, skipped, tier_events = [], [], []

    for i, org in enumerate(orgs):
        if not isinstance(org, dict) or not org.get("id"):
            continue
        needs_tier = not org.get("tier")
        needs_exclude = "view_exclude" in org or "view_exclude_reason" in org
        if not needs_tier and not needs_exclude:
            continue

        before_tier = org.get("tier")
        before_rel = org.get("relationship_type")
        candidate = _normalize_legacy_keys(org)  # migrates view_exclude → tier: passive

        per30 = None
        if not candidate.get("tier") or not candidate.get("relationship_type"):
            per30 = _interactions_per_30d(events, org["id"], now)
            inferred_tier, inferred_rel = _infer(candidate, per30)
            if not candidate.get("tier"):
                candidate["tier"] = inferred_tier
            if not candidate.get("relationship_type"):
                candidate["relationship_type"] = inferred_rel

        try:
            _validate_org(candidate)
        except ValueError as e:
            skipped.append({"org_id": org["id"], "reason": str(e)[:200]})
            continue

        changed.append({
            "org_id": org["id"],
            "previous_tier": before_tier, "new_tier": candidate["tier"],
            "previous_relationship_type": before_rel,
            "new_relationship_type": candidate["relationship_type"],
            "inference_signal_summary": (
                f"{per30:.1f} interactions/30d over the last {WINDOW_DAYS}d"
                if per30 is not None else "view_exclude migration only"),
        })
        tier_events.append({
            "ts": _now_iso(), "type": "tier_change", "source_skill": source_skill,
            "data": {**changed[-1], "triggered_by": MIGRATION_ID},
        })
        if apply:
            orgs[i] = candidate

    result = {
        "migration_id": MIGRATION_ID, "applied": apply,
        "orgs_examined": len(orgs), "orgs_changed": len(changed),
        "orgs_skipped": len(skipped), "changes": changed, "skipped": skipped,
    }
    if apply and changed:
        data["last_writer"] = source_skill
        data["last_updated"] = _now_iso()
        if isinstance(data.get("version"), int):
            data["version"] += 1
        atomic_write_json_locked(entities_path, data, holder=source_skill)
        atomic_append_jsonl(events_path, tier_events + [{
            "ts": _now_iso(), "type": "workspace_migration_applied",
            "source_skill": source_skill,
            "data": {"migration_id": MIGRATION_ID,
                     "orgs_examined": len(orgs),
                     "orgs_retiered": len(changed),
                     "orgs_skipped": len(skipped)},
        }])
    return result


def main() -> int:
    args = [a for a in sys.argv[1:]]
    apply = "--apply" in args
    roots = [a for a in args if a != "--apply"]
    if len(roots) != 1:
        print(__doc__)
        return 2
    result = run(roots[0], apply=apply)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not apply and result["orgs_changed"]:
        print(f"\nDRY-RUN — {result['orgs_changed']} org(s) would change. "
              f"Re-run with --apply to write.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
