#!/usr/bin/env python3
"""
Canonical writer for organization records in entities.json (v3.13.0+).

Mirrors `people_writer.py` for orgs. Every skill that creates / updates / merges
orgs MUST go through this module. Direct hand-rolled writes to entities.json
are forbidden (they're the bug class people_writer.py was built to eliminate;
the same bug class exists for orgs).

Why this exists:
  Per the 2026-05-20 auto-org-attribution handoff, no canonical org writer
  existed before v3.13.0. Skills hand-rolled org records, producing shape
  drift (5 of 21 orgs in M's workspace carry non-schema fields like
  `created_at`, `created_by`, `nicknames`, `industry`, `pending_review`).
  This also blocked auto-attribution: when a person was captured with an
  `org_hint`, there was no canonical path to dedup-then-create the org and
  link the person. Result: 34 of 83 people landed in M's workspace with no
  org link. This module is the fix.

PUBLIC API:
  - find_existing_org(workspace_root, *, name=None, domain=None, aliases=None) → dict | None
  - create_org(workspace_root, *, canonical_name, domains=None, ...) → dict
  - update_org(workspace_root, org_id, **fields) → dict
  - repair_org(workspace_root, org_id) → dict (normalizes legacy keys)
  - advisory_org_warnings(record) → list[str] (flag-only FYIs, e.g. off-enum
    relationship_type; never an error, never a repair trigger — HYG2/F-05)
  - get_org_domains(record) → list[str]
  - get_org_display_names(record) → list[str] (canonical + aliases dedup'd)

INVARIANTS:
  - All writes go through atomic_write_json_locked (cross-process lock + parse check).
  - All writes log an event to events.jsonl (org_created, org_updated, org_repaired).
  - Dedup-before-create: callers should still call find_existing_org first, but
    create_org also dedups internally and raises DuplicateOrgError if a record
    already exists for the same name / domain.
"""
from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from atomic_write import atomic_write_json_locked, atomic_append_jsonl  # noqa: E402
from entities_io import entities_collection  # noqa: E402


def _enforce_record_scope(workspace_root, *, provenance=None, source_ref=None,
                          account_address=None, holder="org_writer") -> None:
    """The CRM record wall (ACCOUNT_SCOPE §2, review fix 7) — delegate to
    account_scope_gate.enforce_record_scope. Import defensively: a missing
    module must never brick an org write (never-brick posture)."""
    try:
        from account_scope_gate import enforce_record_scope, AccountScopeError
    except ImportError:
        return
    try:
        enforce_record_scope(workspace_root, provenance=provenance,
                             source_ref=source_ref,
                             account_address=account_address, holder=holder)
    except AccountScopeError:
        raise
    except Exception:
        return


# Canonical org schema fields, mirrored from
# shared/data-schemas/entities.schema.json $defs.org.properties.
# DO NOT add fields here without updating the schema first.
ALLOWED_ORG_FIELDS = {
    "id",                  # required, format org_<slug-or-number>
    "canonical_name",      # required
    "legal_name",           # formal registered name, when it differs (F-05, v4.8.1)
    "aliases",              # array of strings
    "scope",                # enum
    "scope_label",          # free text if scope=other
    "parent_org_id",        # parent for nested structures
    "relationship_type",    # enum
    "relationship_label",   # free text if relationship_type=other
    "tier",                 # primary | secondary | external | passive
    "is_primary_focus",     # boolean
    "domains",              # array of email/web domains
    "slack_workspace_ids",  # array
    "inferred_from",        # array of enum strings (canonical for orgs, unlike persons)
    "first_seen",           # date | null
    "last_interaction",     # date | null
    "status",               # active | archived
    "type",                 # DEPRECATED
    "domain",               # DEPRECATED (use domains[])
    "notes",                # free text
    "needs_enrichment",     # bool — provisional org from reactive discovery, awaiting CEO confirm (deep-audit #18). On-entity flag; cleared on confirm. Replaces the forbidden pending_review.
    "money",                # grouped account/revenue object (SPEC HIST1 Part A) — written ONLY via set_org_money(confirmed=True); inner keys mirror quantify._MONEY_FIELDS; never estimated (Bug #92)
}

REQUIRED_ORG_FIELDS = {"id", "canonical_name"}

# Canonical relationship_type values, mirrored from
# shared/data-schemas/entities.schema.json $defs.org.properties.relationship_type.enum.
# DO NOT extend here without updating the schema first (lockstep test enforces).
#
# ADVISORY ONLY (HYG2 nit, F-05 lesson): an off-enum value (M's live workspace
# carries a legacy `relationship_type: "network"`) is real data with no
# sanctioned repair rule — flagging it as a hard error would reopen the F-05
# flag-loop (validator flags forever, repair path can't clear it, every bridge
# run re-surfaces the record). So the enum check NEVER raises, NEVER gates a
# write, and NEVER makes a record a repair candidate. It surfaces as an FYI
# via advisory_org_warnings() / the repair-all CLI only.
RELATIONSHIP_TYPES = frozenset({
    "operating", "partner", "board", "advisory", "investment", "client",
    "portfolio_company", "beneficiary", "vendor", "prospect",
    "service_provider", "other",
})

# Forbidden keys observed in M's workspace orgs (the 5 drifted records carry
# these). The mapping tells the validator what to recommend.
FORBIDDEN_ORG_FIELDS = {
    "name":             "canonical_name",
    "display_name":     "canonical_name",
    "nicknames":        "aliases",
    "relationship":     "relationship_type",
    "created_at":       "(remove — track via org_created event in events.jsonl)",
    "created_by":       "(remove — track via org_created event in events.jsonl)",
    "pending_review":   "(remove — gate via events.jsonl)",
    "industry":         "(remove — capture in notes or a future industry[] field if needed)",
    "tags":             "(remove — capture in notes)",
    "primary_user":     "(remove — workspace.user_id marks the workspace owner)",
}

LEGACY_KEY_RENAMES = {
    "name":         "canonical_name",
    "display_name": "canonical_name",
    "nicknames":    "aliases",
}

ORG_ID_RE = re.compile(r"^org_[a-z0-9_]+$")

# Inner keys of the grouped `money` object (SPEC HIST1 Part A / D4).
# The numeric names are EXACTLY quantify._MONEY_FIELDS entries so the grouped
# object resolves through _money_part's candidate list (the one-line B1 edit).
MONEY_NUMERIC_FIELDS = frozenset({"account_value", "revenue", "arr", "mrr"})
MONEY_STRING_FIELDS = frozenset({"currency", "source", "as_of"})
ALLOWED_MONEY_FIELDS = MONEY_NUMERIC_FIELDS | MONEY_STRING_FIELDS

# Fact categories (SPEC HIST1 D3) — mirrors people_writer.FACT_CATEGORIES.
FACT_CATEGORIES = frozenset({
    "preference", "contact", "personal", "role", "company_news", "other",
})

# Auto-eligible fact categories (SPEC HIST1 Part 2, D3/S2) — mirrors
# people_writer.AUTO_FACT_CATEGORIES; the auto-tier test pins the copies
# equal. role/company_news are identity-adjacent and stay confirm even from
# a structured source.
AUTO_FACT_CATEGORIES = frozenset({"preference", "contact", "personal"})

# True only while set_org_money is routing its own update_org call —
# update_org refuses a `money` field from any other caller, so the
# confirm-only/sourced discipline (SPEC HIST1 D4 / Bug #92) can't be
# bypassed by writing the field directly. Writer-lock serialization makes
# a module flag sufficient here.
_money_write_sanctioned = False


# ---------- exceptions ----------

class DuplicateOrgError(Exception):
    """Raised when create_org detects an existing record (by domain/name/alias)."""

    def __init__(self, org_id: str, canonical_name: str | None):
        self.org_id = org_id
        self.canonical_name = canonical_name
        super().__init__(
            f"duplicate detected: {org_id} (canonical_name={canonical_name!r}). "
            f"Use update_org({org_id}, ...) to extend, or pass skip_dedup=True "
            f"if you really want a separate record."
        )


# ---------- private helpers ----------

def _today_iso() -> str:
    return datetime.date.today().isoformat()


def _now_iso() -> str:
    # FS-03: UTC-aware, not naive local. entities.json `last_updated` and any
    # time-window consumer must never see a naive local timestamp (the −7h skew
    # mis-placed events across the append-gate's UTC lineage).
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _normalize_name(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _slugify_name(s: str) -> str:
    """Convert a display name to a slug for org_id generation.
    'Summit Food Truck' → 'summit_food_truck'.
    """
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unnamed"


def _entities_path(workspace_root: Path) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "entities.json"


def _events_path(workspace_root: Path) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _load_entities(workspace_root: Path) -> dict:
    return json.loads(_entities_path(workspace_root).read_text(encoding="utf-8"))


def _save_entities(workspace_root: Path, data: dict, source_skill: str) -> None:
    """Persist entities.json via the locked + parse-verified writer."""
    data["version"] = int(data.get("version", 0)) + 1
    data["last_updated"] = _now_iso()
    data["last_writer"] = source_skill
    atomic_write_json_locked(
        _entities_path(workspace_root),
        data,
        holder=source_skill,
    )


def _next_org_id(orgs: list[dict]) -> str:
    """Generate the next numeric org_id, matching the existing convention
    in M's workspace (org_001, org_002, ..., org_022).

    Callers can override by passing an explicit `org_id` arg with a custom
    readable slug (e.g., 'org_acme_co'). Both formats validate against the
    schema's pattern ^org_[a-z0-9_]+$.
    """
    max_n = 0
    for o in orgs:
        oid = o.get("id", "")
        m = re.match(r"^org_(\d{3,})$", oid)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"org_{max_n + 1:03d}"


def advisory_org_warnings(record: dict) -> list[str]:
    """ADVISORY checks — flag-only. Never raises, never gates a write, never
    triggers the repair path (the F-05 contract "anything the validator flags,
    the repair path must clear" applies to ERRORS; advisories are exempt
    precisely because there is no sanctioned auto-repair for them — only the
    CEO can say what an off-enum relationship actually is).

    Returns plain-English FYI strings, [] when clean.
    """
    warnings: list[str] = []
    rt = record.get("relationship_type")
    if rt is not None:
        if not isinstance(rt, str):
            warnings.append(
                f"{record.get('id', '(no id)')}: relationship_type should be a "
                f"string, got {type(rt).__name__} {rt!r} — advisory only, not "
                f"repaired automatically."
            )
        elif rt not in RELATIONSHIP_TYPES:
            warnings.append(
                f"{record.get('id', '(no id)')}: relationship_type {rt!r} is not "
                f"a canonical value ({', '.join(sorted(RELATIONSHIP_TYPES))}). "
                f"Advisory only — kept as-is; set a canonical value via "
                f"update_org when the CEO confirms one (use 'other' + "
                f"relationship_label for anything that doesn't fit)."
            )
    return warnings


def _validate_org(record: dict) -> list[str]:
    """Hard-validate `record` (raises ValueError on schema violations), then
    return advisory_org_warnings(record) — flag-only FYIs the caller may
    surface or ignore. Existing callers ignore the return value; nothing
    about the raise behavior changed (HYG2 relationship_type enum is advisory,
    NOT a hard check — see RELATIONSHIP_TYPES)."""
    extras = set(record) - ALLOWED_ORG_FIELDS
    if extras:
        msgs = []
        for k in sorted(extras):
            if k in FORBIDDEN_ORG_FIELDS:
                msgs.append(f"  - {k!r} → use {FORBIDDEN_ORG_FIELDS[k]!r}")
            else:
                msgs.append(f"  - {k!r} (not in schema)")
        raise ValueError(
            "org record has fields not allowed by the schema. "
            "If you genuinely need a new field, update "
            "shared/data-schemas/entities.schema.json $defs.org AND "
            "ALLOWED_ORG_FIELDS in org_writer.py first.\n" + "\n".join(msgs)
        )
    missing = REQUIRED_ORG_FIELDS - set(record)
    if missing:
        raise ValueError(f"org record missing required fields: {sorted(missing)}")
    if not ORG_ID_RE.match(record["id"]):
        raise ValueError(
            f"id must match ^org_[a-z0-9_]+$ (e.g. org_001 or org_acme_co), "
            f"got: {record['id']!r}"
        )
    if record.get("first_seen") is not None:
        try:
            datetime.date.fromisoformat(record["first_seen"])
        except (ValueError, TypeError):
            raise ValueError(
                f"first_seen must be ISO date YYYY-MM-DD or null, "
                f"got: {record['first_seen']!r}"
            )
    if record.get("last_interaction") is not None:
        try:
            datetime.date.fromisoformat(record["last_interaction"])
        except (ValueError, TypeError):
            raise ValueError(
                f"last_interaction must be ISO date YYYY-MM-DD or null, "
                f"got: {record['last_interaction']!r}"
            )
    money = record.get("money")
    if money is not None:
        if not isinstance(money, dict):
            raise ValueError(
                f"money must be a grouped object (SPEC HIST1 D4), got: {type(money).__name__}"
            )
        extras_m = set(money) - ALLOWED_MONEY_FIELDS
        if extras_m:
            raise ValueError(
                f"money object has unknown keys {sorted(extras_m)} — allowed: "
                f"{sorted(ALLOWED_MONEY_FIELDS)} (mirror quantify._MONEY_FIELDS)"
            )
        for k in MONEY_NUMERIC_FIELDS:
            v = money.get(k)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
                raise ValueError(
                    f"money.{k} must be a number or null, got: {v!r} — money is "
                    f"never a string or an estimate"
                )
    return advisory_org_warnings(record)


def _append_org_note(record: dict, line: str) -> None:
    """Append a migration note to `record['notes']` without losing anything
    (second-eyes finding 3): string notes get the line appended; legacy
    list-shaped notes (hand-rolled drift — the schema says string, but the
    validator doesn't type-check notes) keep every element and gain the line
    as a new one; empty/None becomes the line."""
    existing = record.get("notes")
    if isinstance(existing, str) and existing.strip():
        record["notes"] = existing.rstrip() + " " + line
    elif isinstance(existing, list):
        record["notes"] = existing + [line]
    else:
        record["notes"] = line


def _normalize_legacy_keys(record: dict) -> dict:
    """Return a copy of `record` with legacy keys renamed/cleaned up.

    Used by repair_org and during create-time dedup merges. Same pattern as
    people_writer._normalize_legacy_keys but with org-specific rename rules.
    """
    out = dict(record)

    # Rename legacy keys to canonical equivalents (skip if canonical already present).
    for old, new in LEGACY_KEY_RENAMES.items():
        if old in out:
            value = out.pop(old)
            if new not in out:
                out[new] = value
            elif new == "aliases" and isinstance(value, list):
                # Merge nicknames into existing aliases without duplicates
                existing = out.get(new) or []
                if not isinstance(existing, list):
                    existing = []
                for v in value:
                    if isinstance(v, str) and v.strip() and v.strip() not in existing:
                        existing.append(v.strip())
                out[new] = existing

    # F-05 (v4.8.1): legacy `relationship` — MIGRATE, don't drop. The value is
    # real relationship data from pre-schema records. Three cases:
    #   - `relationship_type` absent          → rename (the value IS the data)
    #   - equal to `relationship_type`        → redundant duplicate; safe to drop
    #   - conflicts with `relationship_type`  → preserve the legacy value into
    #     `notes` so nothing is silently discarded, then remove the key so the
    #     record validates.
    # The validator (_validate_org) and this repair path MUST stay in agreement:
    # every field the validator flags must have a repair rule here (rename,
    # dedup-drop, or preserve-into-notes) — otherwise the record re-flags on
    # every future update, permanently (the F-05 flag-loop).
    if "relationship" in out:
        legacy_rel = out.pop("relationship")
        current = out.get("relationship_type")
        legacy_str = legacy_rel.strip() if isinstance(legacy_rel, str) else None
        preserved = None
        if legacy_str:
            if current is None:
                out["relationship_type"] = legacy_str
            elif legacy_str != current:
                preserved = repr(legacy_str)
            # equal → redundant duplicate; dropping the key loses nothing
        elif legacy_rel not in (None, ""):
            # Non-string legacy value (list/dict/number — hand-rolled drift).
            # Never rename it into the enum field, never silently discard it
            # (second-eyes finding 2) — preserve verbatim into notes.
            preserved = repr(legacy_rel)
        if preserved is not None:
            kept = (f"; kept relationship_type {current!r})."
                    if current is not None else ").")
            _append_org_note(
                out,
                f"Legacy 'relationship' field carried {preserved} "
                f"(migrated {_today_iso()}{kept}",
            )

    # Drop forbidden provenance keys whose home is events.jsonl.
    KEYS_TO_DROP = {
        "created_at",
        "created_by",
        "pending_review",
        "industry",
        "tags",
        "primary_user",
    }
    for k in KEYS_TO_DROP:
        out.pop(k, None)

    return out


def _log_event(
    workspace_root: Path,
    event_type: str,
    record: dict,
    source_skill: str,
    before: dict | None = None,
) -> None:
    """Append a canonical-shape event to events.jsonl.

    v3.13.6+ — fixed event shape to match events.schema.json (`ts` not
    `timestamp`, per-event fields nested under `data`). Pre-v3.13.6 emitted
    rows that failed schema validation on every org create/update/repair.
    The event TYPE values (`org_created` / `org_updated` / `org_repaired`)
    are now enumerated in events.schema.json per v3.13.6 schema additions.
    """
    data: dict[str, Any] = {
        "org_id": record.get("id"),
        "canonical_name": record.get("canonical_name"),
    }
    if before is not None:
        data["before"] = before
    event: dict[str, Any] = {
        # FS-03: OMIT ts — the append gate stamps it UTC-aware. A hand-stamped
        # `datetime.now()` was naive local (the F-15 naive-local-clock bug).
        "type": event_type,
        "source_skill": source_skill,
        "data": data,
    }
    atomic_append_jsonl(_events_path(workspace_root), [event])


# ---------- public API ----------

def find_existing_org(
    workspace_root: str | Path,
    *,
    name: str | None = None,
    domain: str | None = None,
    aliases: list[str] | None = None,
) -> dict | None:
    """Look up an existing org. Match order, first hit wins:

      1. domain exact (case-insensitive against record's `domains[]` and legacy `domain`)
      2. name/alias exact (whitespace-normalized lowercase, against the record's
         canonical_name + legal_name (F-05, v4.8.1) + aliases)

    Returns the matching record dict, or None.
    """
    data = _load_entities(Path(workspace_root))
    orgs = entities_collection(data, "orgs")

    if domain:
        target = domain.strip().lower()
        for o in orgs:
            domains_arr = o.get("domains") or []
            if isinstance(domains_arr, list):
                for d in domains_arr:
                    if isinstance(d, str) and d.strip().lower() == target:
                        return o
            legacy = o.get("domain")
            if isinstance(legacy, str) and legacy.strip().lower() == target:
                return o

    candidates = [c for c in [name, *(aliases or [])] if c]
    targets_normed = {_normalize_name(c) for c in candidates}
    if targets_normed:
        for o in orgs:
            canon = o.get("canonical_name") or ""
            if _normalize_name(canon) in targets_normed:
                return o
            legal = o.get("legal_name")
            if isinstance(legal, str) and _normalize_name(legal) in targets_normed:
                return o
            for a in (o.get("aliases") or []):
                if isinstance(a, str) and _normalize_name(a) in targets_normed:
                    return o
    return None


def create_org(
    workspace_root: str | Path,
    *,
    canonical_name: str,
    domains: list[str] | None = None,
    aliases: list[str] | None = None,
    scope: str | None = None,
    relationship_type: str | None = None,
    tier: str | None = None,
    parent_org_id: str | None = None,
    is_primary_focus: bool = False,
    notes: str | None = None,
    inferred_from: list[str] | None = None,
    needs_enrichment: bool = False,
    org_id: str | None = None,
    source_skill: str = "unknown",
    skip_dedup: bool = False,
    provenance: dict | None = None,
    source_ref: str | None = None,
    account_address: str | None = None,
) -> dict:
    """Create a new org record. Dedups by domain → alias → canonical_name
    before creating; raises DuplicateOrgError if a match is found (unless
    skip_dedup=True).

    Required: canonical_name. Everything else is optional.

    Writes the new record to entities.json (via the locked writer) AND emits
    an `org_created` event to events.jsonl.

    Account-scope wall (review fix 7): when the org is derived from a
    connector read (e.g. org-domain inference off an inbound mail), pass the
    read's `provenance` / `source_ref` / `account_address` — an out-of-scope
    account raises AccountScopeError before the write. Manual adds pass.
    Scope inputs only; never stored on the record.

    Returns the created record.
    """
    workspace_root = Path(workspace_root)
    _enforce_record_scope(workspace_root, provenance=provenance,
                          source_ref=source_ref,
                          account_address=account_address,
                          holder=source_skill)
    data = _load_entities(workspace_root)
    orgs = entities_collection(data, "orgs")

    if not skip_dedup:
        existing = find_existing_org(
            workspace_root,
            name=canonical_name,
            domain=(domains[0] if domains else None),
            aliases=aliases,
        )
        if existing is not None:
            raise DuplicateOrgError(existing.get("id", ""), existing.get("canonical_name"))

    new_id = org_id or _next_org_id(orgs)
    record: dict[str, Any] = {
        "id": new_id,
        "canonical_name": canonical_name,
        "first_seen": _today_iso(),
        "status": "active",
    }
    if aliases:
        record["aliases"] = [a for a in aliases if isinstance(a, str) and a.strip()]
    if domains:
        record["domains"] = [d.strip().lower() for d in domains if isinstance(d, str) and d.strip()]
    if scope:
        record["scope"] = scope
    if relationship_type:
        record["relationship_type"] = relationship_type
    if tier:
        record["tier"] = tier
    if parent_org_id:
        record["parent_org_id"] = parent_org_id
    if is_primary_focus:
        record["is_primary_focus"] = True
    if notes:
        record["notes"] = notes
    if needs_enrichment:
        record["needs_enrichment"] = True
    if inferred_from:
        record["inferred_from"] = inferred_from

    _validate_org(record)
    orgs.append(record)
    _save_entities(workspace_root, data, source_skill=source_skill)
    _log_event(workspace_root, "org_created", record, source_skill)
    return record


def update_org(
    workspace_root: str | Path,
    org_id: str,
    *,
    source_skill: str = "unknown",
    **fields: Any,
) -> dict:
    """Update an existing org record. Pass only the fields to change.

    Merges fields into the existing record after _normalize_legacy_keys (so a
    legacy-shaped caller passing `nicknames` gets it rolled into `aliases`
    instead of rejected outright). Validates the merged record before saving.

    Array fields (`aliases`, `domains`, `inferred_from`, `slack_workspace_ids`)
    are EXTENDED (deduplicated), not replaced. Pass an explicit `aliases=[]`
    if you really want to clear them.

    Writes via the locked writer; emits `org_updated` event.

    Returns the updated record.
    """
    workspace_root = Path(workspace_root)
    data = _load_entities(workspace_root)
    orgs = entities_collection(data, "orgs")

    idx = next((i for i, o in enumerate(orgs) if o.get("id") == org_id), None)
    if idx is None:
        raise KeyError(f"org_id not found: {org_id!r}")

    before = dict(orgs[idx])
    record = dict(orgs[idx])

    # Apply legacy-key normalization on the incoming fields so callers don't
    # have to know the canonical names.
    normalized_in = _normalize_legacy_keys(fields)

    if "money" in normalized_in and not _money_write_sanctioned:
        raise ValueError(
            "org.money is written ONLY via set_org_money(confirmed=True) — "
            "the confirm-gated, sourced money writer (SPEC HIST1 D4 / "
            "Bug #92). Direct update_org(money=...) is refused."
        )

    ARRAY_FIELDS = {"aliases", "domains", "inferred_from", "slack_workspace_ids"}
    for k, v in normalized_in.items():
        if k in ARRAY_FIELDS and isinstance(v, list):
            existing = record.get(k) or []
            if not isinstance(existing, list):
                existing = []
            for item in v:
                if isinstance(item, str):
                    item = item.strip()
                if item and item not in existing:
                    existing.append(item)
            record[k] = existing
        else:
            record[k] = v

    _validate_org(record)
    orgs[idx] = record
    _save_entities(workspace_root, data, source_skill=source_skill)
    _log_event(workspace_root, "org_updated", record, source_skill, before=before)
    return record


def set_org_money(
    workspace_root: str | Path,
    org_id: str,
    money: dict,
    *,
    source_skill: str = "unknown",
    confirmed: bool = False,
) -> dict:
    """THE writer for the grouped org `money` object (SPEC HIST1 Part A/D4).

    Reachable ONLY from (a) an explicit user statement ("Acme Co is a
    $120k/yr account") or (b) a confirmed brain_proposal (Part 2 detector /
    QBO reader). Money is identity-adjacent trust: **always confirm-gated,
    never estimated, never auto** (Bug #92 / PIPE1 D9) — the caller must
    pass `confirmed=True`, which it may do only on those two paths. A
    silent/auto context that cannot truthfully pass it gets a loud raise.

    `money` carries any subset of {account_value, revenue, arr, mrr,
    currency, source, as_of}. `source` is REQUIRED (money is always
    sourced); `as_of` defaults to today. Partial updates MERGE into the
    existing money object (pass an explicit None value to clear a field).
    Routes through update_org, so the org_updated event carries the money
    delta in `before` — the change-feed input (D10). Returns the updated
    org record.
    """
    if confirmed is not True:
        raise ValueError(
            "set_org_money refused: money is confirm-only (SPEC HIST1 D4 / "
            "Bug #92). Pass confirmed=True ONLY from an explicit user "
            "statement or a confirmed proposal — never from a silent/auto "
            "path, and never with an estimated figure."
        )
    if not isinstance(money, dict) or not money:
        raise ValueError("set_org_money needs a non-empty money dict")
    extras = set(money) - ALLOWED_MONEY_FIELDS
    if extras:
        raise ValueError(
            f"set_org_money: unknown money keys {sorted(extras)} — allowed: "
            f"{sorted(ALLOWED_MONEY_FIELDS)}"
        )
    workspace_root = Path(workspace_root)

    data = _load_entities(workspace_root)
    orgs = entities_collection(data, "orgs")
    existing = next((o for o in orgs if o.get("id") == org_id), None)
    if existing is None:
        raise KeyError(f"org_id not found: {org_id!r}")

    merged = dict(existing.get("money") or {})
    for k, v in money.items():
        if v is None:
            merged.pop(k, None)
        else:
            merged[k] = v
    if not (isinstance(merged.get("source"), str) and merged["source"].strip()):
        raise ValueError(
            "set_org_money: money.source is required — record where the "
            "figure came from ('user statement', 'confirmed proposal', ...)"
        )
    merged.setdefault("as_of", _today_iso())
    merged.setdefault("currency", "USD")

    # update_org re-loads and emits org_updated with the money delta in
    # `before` — the single write path; no hand-rolled entities edit here.
    # The sanction flag is what lets update_org accept the money field at
    # all (it refuses money from every other caller).
    global _money_write_sanctioned
    _money_write_sanctioned = True
    try:
        return update_org(workspace_root, org_id, money=merged,
                          source_skill=source_skill)
    finally:
        _money_write_sanctioned = False


def record_org_fact(
    workspace_root: str | Path,
    org_id: str,
    fact: str,
    source_ref: str,
    *,
    category: str | None = None,
    confidence: str = "high",
    source_skill: str = "unknown",
    brain_batch_id: str | None = None,
    brain_change_class: str | None = None,
) -> dict:
    """Append an org_fact_observed event (SPEC HIST1 D3) — additive, sourced,
    never a mutation of the org record. Callers: an explicit user statement
    ("note that Acme Co raised a Series A"), a confirmed proposal, or
    (Part 2) the structured-connector auto tier — which MUST pass
    `brain_change_class="entity_fact_structured"` + `brain_batch_id` so the
    write is batch-reversible via brain_undo.undo_batch. Never a silent
    prose-inferred path (those ride the confirm rail). On the auto class,
    category is restricted to AUTO_FACT_CATEGORIES (S2 — enforced here,
    code-deep, mirroring people_writer.record_person_fact).

    Raises KeyError on an unknown org_id (callers run ENTITY_RESOLVE first).
    Returns the appended event. Top-level org_ids[] carries the org so the
    org-activity derivation and the history renderer see it.
    """
    workspace_root = Path(workspace_root)
    fact = (fact or "").strip()
    if not fact:
        raise ValueError("record_org_fact needs a non-empty fact")
    if not isinstance(source_ref, str) or not source_ref.strip():
        raise ValueError("record_org_fact needs a non-empty source_ref — facts are always sourced")
    if category is not None and category not in FACT_CATEGORIES:
        raise ValueError(
            f"unknown fact category {category!r} — use one of {sorted(FACT_CATEGORIES)}"
        )
    if (brain_batch_id is None) != (brain_change_class is None):
        raise ValueError(
            "brain_batch_id and brain_change_class travel together — an "
            "auto-tier fact write must be batch-reversible (D3/S1)")
    if brain_change_class is not None:
        if brain_change_class != "entity_fact_structured":
            raise ValueError(
                f"unknown brain_change_class {brain_change_class!r} for a "
                "fact write — only entity_fact_structured is registered")
        if category not in AUTO_FACT_CATEGORIES:
            raise ValueError(
                f"category {category!r} is not auto-eligible — the "
                "entity_fact_structured auto tier is limited to "
                f"{sorted(AUTO_FACT_CATEGORIES)} (SPEC HIST1 S2); "
                "role/company_news facts stay confirm even from a "
                "structured source")

    data = _load_entities(workspace_root)
    orgs = entities_collection(data, "orgs")
    target = next((o for o in orgs if o.get("id") == org_id), None)
    if target is None:
        raise KeyError(f"org_id not found: {org_id!r}")

    event: dict[str, Any] = {
        "type": "org_fact_observed",
        "source_skill": source_skill,
        "org_ids": [org_id],
        "data": {
            "org_id": org_id,
            "canonical_name": target.get("canonical_name"),
            "fact": fact,
            "category": category,
            "confidence": confidence,
            "source_ref": source_ref.strip(),
            "summary": fact,
        },
    }
    if brain_batch_id is not None:
        # Part 2 auto tier — the stamps brain_undo._changes_for_brain_batch
        # resolves, so ONE undo_batch retracts every fact this batch noted.
        event["data"]["brain_batch_id"] = brain_batch_id
        event["data"]["brain_change_class"] = brain_change_class
    atomic_append_jsonl(_events_path(workspace_root), [event])
    return event


def repair_org(
    workspace_root: str | Path,
    org_id: str,
    source_skill: str = "org_writer.repair_org",
) -> dict:
    """Normalize a single org record in-place. Used by the v3.13.0 migration
    script + by weekly-audit when it surfaces drifted orgs.

    Reads the record, runs _normalize_legacy_keys, drops forbidden provenance
    keys, validates the result, writes back. Emits `org_repaired` event.
    """
    workspace_root = Path(workspace_root)
    data = _load_entities(workspace_root)
    orgs = entities_collection(data, "orgs")

    idx = next((i for i, o in enumerate(orgs) if o.get("id") == org_id), None)
    if idx is None:
        raise KeyError(f"org_id not found: {org_id!r}")

    before = dict(orgs[idx])
    record = _normalize_legacy_keys(orgs[idx])
    _validate_org(record)
    orgs[idx] = record
    _save_entities(workspace_root, data, source_skill=source_skill)
    _log_event(workspace_root, "org_repaired", record, source_skill, before=before)
    return record


def _extract_domain(email: str) -> str | None:
    """Pull the lowercased domain from an email. None if it doesn't parse."""
    if not isinstance(email, str) or "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].strip().lower()
    return domain or None


def _is_work_domain(domain: str) -> bool:
    """True if `domain` looks like a work/org domain (not free-mail).

    THE free-mail question has ONE answer, and it lives in
    `identity_reconcile.is_free_mail_domain` (SPEC STAFFCUT §3.4). This module
    used to carry a second, weaker copy: an exact-match set of ~30 hosts, which
    missed every localized storefront of the same providers (`yahoo.fr`,
    `hotmail.es`, `outlook.de`) and every subdomain of them (`corp.yahoo.com`).
    Two lists meant two verdicts on the same address — and here the verdict
    drives org AUTO-ATTRIBUTION, so a miss silently attaches a person to an org
    invented from their personal mail provider. That is not a lint issue; it is
    the wrong-auto-link defect the shared predicate was written to prevent.

    The shared predicate is deliberately conservative in the safe direction: an
    unreadable or dotless value reads as free-mail, so "we could not tell" now
    means "no auto-attribution" instead of "attribute it". Imported lazily
    because `identity_reconcile` is a large module and this is a leaf helper
    called in a loop; `sys.modules` makes every call after the first a dict
    lookup.
    """
    if not domain:
        return False
    try:
        from identity_reconcile import is_free_mail_domain
    except ImportError:  # pragma: no cover — direct-path import
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from identity_reconcile import is_free_mail_domain
    return not is_free_mail_domain(domain)


def _parse_org_hint(hint: str) -> tuple[str | None, str | None]:
    """Parse the capture-event `org_hint` field into (domain, canonical_name).

    Expected format from cr-upcoming-meetings / cr-past-meetings:
      "summitfoodtruck.example.com — Summit Food Truck"
      "northstar.example.com — Northstar Partners (org_042)"
      "acmebakery.example.com — Acme Bakery (no record)"

    Returns (domain_or_None, name_or_None). Both can be None if hint is empty
    or malformed.
    """
    if not isinstance(hint, str) or not hint.strip():
        return (None, None)
    parts = re.split(r"\s+[—–-]\s+", hint.strip(), maxsplit=1)
    if len(parts) == 1:
        # No dash — assume the whole thing is the name (or the domain)
        s = parts[0].strip()
        if "." in s and " " not in s:
            return (s.lower(), None)
        return (None, s)
    left, right = parts[0].strip(), parts[1].strip()
    # Strip "(org_NNN)" / "(no record)" suffixes from the right side
    right = re.sub(r"\s*\([^)]*\)\s*$", "", right).strip()
    domain = left.lower() if "." in left else None
    name = right or None
    if not domain and "." in right and " " not in right:
        # The order was reversed — try right as domain
        domain = right.lower()
        name = left
    return (domain, name)


def attribute_person_to_org(
    workspace_root: str | Path,
    person_id: str,
    *,
    work_domains: list[str] | None = None,
    org_hint: str | None = None,
    source_skill: str = "unknown",
    dry_run: bool = False,
) -> tuple[dict | None, str]:
    """Attach `person_id` to an org based on capture-time signals (v3.13.0+).

    Used by apply-choices Step 3b after a successful create_person. Implements
    the auto-attach-on-strong-signal contract from the 2026-05-20
    auto-org-attribution handoff. Returns `(org_record_or_None, reason)` —
    the reason string is a short plain-English description suitable for
    surfacing in the consolidated apply-choices ack.

    Strong signals (in priority order):

      1. Any `work_domain` matches an existing org's `domains[]` (including
         the deprecated singular `domain` field) → attach to that org.
         Reason: "matched by work-domain {domain} to existing org {name}".

      2. `org_hint` contains a domain that matches an existing org → attach.
         Reason: "matched by capture hint to existing org {name}".

      3. `org_hint` contains a domain that doesn't match any existing org →
         create a new org with the hint name + domain, attach to it.
         Reason: "created new org {name} from capture hint and attached".

    Weak signal / no signal: returns (None, "no strong signal — left
    unattached"). The caller may follow up with a propose-and-confirm flow
    if appropriate, but this function does NOT prompt.

    Free-mail domains (gmail.com etc.) are filtered out from `work_domains`
    before matching. They never trigger auto-attribution.

    Auto-attach uses people_writer.update_person to set `primary_org_id`.
    Atomic-write + lock guarantees apply.

    `dry_run=True` runs the IDENTICAL match logic but writes nothing — it returns
    the SAME `(org_record_or_None, reason)` a real apply would, so a preview can
    never over-promise (Bug #100: the backfill's dry-run used to count anyone
    with a work-domain email, while apply only attaches on an actual org match;
    the preview lied). For the create-from-hint path, dry-run returns a synthetic
    record carrying `_dry_run_would_create: True` (no org is created).
    """
    workspace_root = Path(workspace_root)

    # Filter to work-domains only (skip gmail etc.)
    candidate_domains: list[str] = []
    for d in (work_domains or []):
        if isinstance(d, str) and _is_work_domain(d):
            candidate_domains.append(d.lower())

    # Parse org_hint if present — it may carry a domain we should also try
    hint_domain, hint_name = _parse_org_hint(org_hint or "")
    if hint_domain and _is_work_domain(hint_domain) and hint_domain not in candidate_domains:
        candidate_domains.append(hint_domain)

    # 1 + 2: try to find an existing org by domain
    matched_org = None
    for d in candidate_domains:
        match = find_existing_org(workspace_root, domain=d)
        if match is not None:
            matched_org = match
            matched_via_domain = d
            break

    # Lazy-import people_writer to avoid circular import at module load
    sys.path.insert(0, str(Path(__file__).parent))
    from people_writer import update_person  # noqa: E402

    if matched_org is not None:
        reason = (
            f"matched by work-domain {matched_via_domain} to existing org "
            f"{matched_org.get('canonical_name')}"
        )
        if dry_run:
            return (matched_org, "would attach — " + reason)
        update_person(
            workspace_root,
            person_id,
            primary_org_id=matched_org["id"],
            source_skill=source_skill,
            # Machine attribution is not a confirmed career move — lineage
            # rides only user-confirmed changes (SPEC HIST1 D2/§8). Today's
            # callers only touch unattached people (the both-sides-non-empty
            # gate already blocks), but a future re-attribution caller must
            # not emit person_org_changed from a domain match.
            suppress_lineage=True,
        )
        return (matched_org, reason)

    # 3: create new org from hint if hint has a usable domain + name
    if hint_domain and _is_work_domain(hint_domain) and hint_name:
        if dry_run:
            return (
                {"id": None, "canonical_name": hint_name, "_dry_run_would_create": True},
                f"would create new org {hint_name} from capture hint and attach",
            )
        new_org = create_org(
            workspace_root,
            canonical_name=hint_name,
            domains=[hint_domain],
            inferred_from=["calendar_attendee_cluster"],  # most common source
            source_skill=source_skill,
        )
        update_person(
            workspace_root,
            person_id,
            primary_org_id=new_org["id"],
            source_skill=source_skill,
            suppress_lineage=True,  # machine attribution, not a confirmed move (D2/§8)
        )
        return (
            new_org,
            f"created new org {new_org.get('canonical_name')} from capture hint and attached",
        )

    return (None, "no strong signal — left unattached")


def get_org_domains(record: dict) -> list[str]:
    """Return the unified list of domains for an org record.

    Records may carry `domains` (canonical array) and/or `domain` (deprecated
    singular). This merges both with `domains[]` first.
    """
    out: list[str] = []
    domains = record.get("domains") or []
    if isinstance(domains, list):
        for d in domains:
            if isinstance(d, str) and d.strip():
                out.append(d.strip().lower())
    single = record.get("domain")
    if isinstance(single, str) and single.strip() and single.strip().lower() not in out:
        out.append(single.strip().lower())
    return out


def count_failing_orgs(workspace_root) -> dict:
    """Entity-integrity summary for the system-health self-report (FS-09).

    Loads entities.json and runs each org record through `_validate_org`,
    counting how many fail the hard schema check. Returns
    `{n_orgs, n_failing, failing_ids, unreadable}`. `unreadable=True` means the
    entities file could not be parsed (JSONDecodeError / OSError) — a LOUD
    corruption signal the health check must surface, never swallow (FS-15).
    """
    from pathlib import Path as _P
    p = _P(workspace_root) / "_hq" / "data" / "entities.json"
    try:
        data = _load_entities(_P(workspace_root))
    except (json.JSONDecodeError, ValueError, OSError):
        return {"n_orgs": 0, "n_failing": 0, "failing_ids": [],
                "unreadable": True, "path": str(p)}
    orgs = data.get("orgs") or []
    failing: list[str] = []
    for rec in orgs:
        if not isinstance(rec, dict):
            failing.append("(malformed record)")
            continue
        try:
            _validate_org(rec)
        except ValueError:
            failing.append(rec.get("id") or "(no id)")
    return {"n_orgs": len(orgs), "n_failing": len(failing),
            "failing_ids": failing, "unreadable": False}


def get_org_display_names(record: dict) -> list[str]:
    """Return canonical_name + all aliases (deduplicated)."""
    out: list[str] = []
    canon = record.get("canonical_name")
    if isinstance(canon, str) and canon.strip():
        out.append(canon.strip())
    for a in (record.get("aliases") or []):
        if isinstance(a, str) and a.strip() and a.strip() not in out:
            out.append(a.strip())
    return out


# ---------- CLI ----------

def main() -> int:
    """CLI for bash callers (e.g., migration scripts that need to repair
    every org). Usage:

        python3 org_writer.py repair-all <workspace_root>
        python3 org_writer.py find <workspace_root> --name "Acme Co"
        python3 org_writer.py find <workspace_root> --domain acme.com
    """
    import argparse
    parser = argparse.ArgumentParser(description="Canonical org-record writer CLI.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_repair = sub.add_parser("repair-all", help="Repair every org record in the workspace.")
    p_repair.add_argument("workspace_root", type=Path)
    p_repair.add_argument("--dry-run", action="store_true")

    p_find = sub.add_parser("find", help="Find an org by name or domain.")
    p_find.add_argument("workspace_root", type=Path)
    p_find.add_argument("--name")
    p_find.add_argument("--domain")
    p_find.add_argument("--alias", action="append", help="Can be passed multiple times.")

    args = parser.parse_args()

    if args.cmd == "repair-all":
        # v3.13.6+ — transactional repair-all. Pre-v3.13.6 this loop called
        # repair_org() per-org, and each call did its own _save_entities()
        # write. If org_002 raised mid-loop, org_001 was already written +
        # version-bumped and org_003 was never touched → partial workspace.
        # Plus dry-run never validated, so the preview lied (showed "3 would
        # be repaired" then crashed mid-apply on validation).
        #
        # v3.13.6 model: walk every org → normalize → validate IN MEMORY. Only
        # if every record passes does the write happen. Mirrors the all-or-
        # nothing transactional model migrate_persons_v3_13_0.py uses.
        data = _load_entities(args.workspace_root)
        orgs = entities_collection(data, "orgs")

        candidates: list[tuple[dict, dict, list[str]]] = []  # (original, cleaned, dropped_keys)
        validation_failures: list[tuple[str, str, str]] = []  # (oid, display, error)
        advisories: list[str] = []  # flag-only FYIs — never gate, never repair (HYG2/F-05)

        for o in orgs:
            oid = o.get("id")
            if not oid:
                continue
            cleaned = _normalize_legacy_keys(o)
            # Advise on the POST-normalization shape: a legacy `relationship`
            # migrating into relationship_type this very run gets advised now,
            # not on the next run (second-eyes res1 finding 4).
            advisories.extend(advisory_org_warnings(cleaned))
            if cleaned == o:
                continue
            display = (
                o.get("canonical_name")
                or o.get("name")
                or o.get("display_name")
                or "(unknown)"
            )
            try:
                _validate_org(cleaned)
            except ValueError as e:
                validation_failures.append((oid, display, str(e)))
                continue
            dropped = sorted(set(o.keys()) - set(cleaned.keys()))
            candidates.append((o, cleaned, dropped))

        # Advisories are FYI-only: printed, never counted as failures, never
        # affect candidacy or the exit code (F-05 — an off-enum
        # relationship_type like the live "network" must not re-enter a
        # flag/repair loop).
        if advisories:
            print("Advisory (FYI only — nothing blocked, nothing auto-changed):")
            for line in advisories:
                print(f"  - {line}")
            print()

        # Friendly per-record summary
        if validation_failures:
            print("Some org records can't be repaired automatically — they're "
                  "missing fields the system needs.\n")
            for oid, display, err in validation_failures:
                print(f"  - {oid} ({display}): {err}")
                print(f"      → Add the missing fields to this org record in "
                      f"_hq/data/entities.json, then re-run.")
            if args.dry_run:
                print(f"\n{len(candidates)} org(s) would be repaired; "
                      f"{len(validation_failures)} need manual fixes first.")
                return 1
            # In apply mode, abort the whole batch — no partial writes.
            print(f"\nABORT — {len(validation_failures)} org(s) need manual "
                  f"fixes first. No changes written. Fix the records listed "
                  f"above, then re-run.")
            return 1

        # All candidates validate. Either preview or apply atomically.
        if args.dry_run:
            for o, cleaned, dropped in candidates:
                oid = o.get("id")
                display = (
                    cleaned.get("canonical_name")
                    or o.get("canonical_name")
                    or o.get("name")
                    or o.get("display_name")
                    or "(unknown)"
                )
                print(f"WOULD repair {oid} ({display})")
                if dropped:
                    print(f"  dropped: {dropped}")
            print(f"\n{len(candidates)} org(s) would be repaired")
            return 0

        # Apply mode — atomic write at the end.
        for o, cleaned, _dropped in candidates:
            repair_org(args.workspace_root, o["id"])
            print(f"repaired {o['id']}")
        print(f"\n{len(candidates)} org(s) repaired")
        return 0

    if args.cmd == "find":
        match = find_existing_org(
            args.workspace_root,
            name=args.name,
            domain=args.domain,
            aliases=args.alias,
        )
        if match is None:
            print("no match")
            return 1
        print(json.dumps(match, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
