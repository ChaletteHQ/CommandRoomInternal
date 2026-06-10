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


# Canonical org schema fields, mirrored from
# shared/data-schemas/entities.schema.json $defs.org.properties.
# DO NOT add fields here without updating the schema first.
ALLOWED_ORG_FIELDS = {
    "id",                  # required, format org_<slug-or-number>
    "canonical_name",      # required
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
}

REQUIRED_ORG_FIELDS = {"id", "canonical_name"}

# Forbidden keys observed in M's workspace orgs (the 5 drifted records carry
# these). The mapping tells the validator what to recommend.
FORBIDDEN_ORG_FIELDS = {
    "name":             "canonical_name",
    "display_name":     "canonical_name",
    "nicknames":        "aliases",
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
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def _normalize_name(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _slugify_name(s: str) -> str:
    """Convert a display name to a slug for org_id generation.
    'Continental Floral Greens' → 'continental_floral_greens'.
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


def _validate_org(record: dict) -> None:
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
        "ts": _now_iso(),
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
      2. alias exact (case-insensitive against record's canonical_name + aliases)
      3. canonical_name exact (whitespace-normalized lowercase)

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
) -> dict:
    """Create a new org record. Dedups by domain → alias → canonical_name
    before creating; raises DuplicateOrgError if a match is found (unless
    skip_dedup=True).

    Required: canonical_name. Everything else is optional.

    Writes the new record to entities.json (via the locked writer) AND emits
    an `org_created` event to events.jsonl.

    Returns the created record.
    """
    workspace_root = Path(workspace_root)
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


# Free-mail / personal-email domains that should NOT trigger auto-attribution.
# A person with a gmail.com email isn't automatically affiliated with org_gmail.
FREE_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com",
    "yahoo.com", "ymail.com", "yahoo.co.uk", "yahoo.co.jp", "yahoo.ca",
    "hotmail.com", "outlook.com", "live.com", "msn.com",
    "icloud.com", "me.com", "mac.com",
    "aol.com",
    "protonmail.com", "proton.me", "pm.me",
    "fastmail.com", "fastmail.fm",
    "zoho.com",
    "qq.com", "163.com", "126.com",
    "mail.com", "gmx.com", "gmx.de", "gmx.net",
    "yandex.com", "yandex.ru",
    "tutanota.com",
    "duck.com",  # DuckDuckGo email
})


def _extract_domain(email: str) -> str | None:
    """Pull the lowercased domain from an email. None if it doesn't parse."""
    if not isinstance(email, str) or "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].strip().lower()
    return domain or None


def _is_work_domain(domain: str) -> bool:
    """True if `domain` looks like a work/org domain (not free-mail)."""
    if not domain:
        return False
    return domain.lower() not in FREE_MAIL_DOMAINS


def _parse_org_hint(hint: str) -> tuple[str | None, str | None]:
    """Parse the capture-event `org_hint` field into (domain, canonical_name).

    Expected format from cr-upcoming-meetings / cr-past-meetings:
      "cfgreens.com — Continental Floral Greens"
      "uscontinental.com — U.S. Continental (org_010)"
      "lhdottie.com — LH Dottie (no record)"

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

        for o in orgs:
            oid = o.get("id")
            if not oid:
                continue
            cleaned = _normalize_legacy_keys(o)
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
