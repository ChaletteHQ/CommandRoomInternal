#!/usr/bin/env python3
"""
people_writer.py — canonical writer for person records in _hq/data/entities.json
(v3.2 candidate, lands with the cr-past-meetings person-record shape fix).

THE BUG THIS FIXES
==================

Two malformed person records appeared in entities.json on 2026-04-26 and
2026-04-30:

  - person_063 Rio Sample: display_name + current_org_id + first_seen_at +
    first_seen_source + confidence
  - person_064 Dustin Sample: display_name + normalized_name + emails[] +
    role_at_primary_org + inferred_from + last_seen

Both came through cr-past-meetings → apply-choices → people-crm. Both were
hand-rolled JSON by the agent against a prose field list in SKILL.md. The
agent invented different shapes on different fires. There was also no dedup
against existing canonical records — person_064 duplicates person_004.

THIS SCRIPT
===========

  - is the canonical writer for person records
  - validates field names against shared/data-schemas/entities.schema.json
    $defs.person (in-source allowlist; no jsonschema dep)
  - rejects unknown keys with a remediation hint per known wrong key
  - dedups on create by email exact / alias case-insensitive / canonical_name
    normalized
  - atomic-writes via shared/scripts/atomic_write.py (Drive-sync safe)
  - logs person_created / person_updated / person_merged / person_repaired
    events to events.jsonl

USAGE (Python)
--------------

    from people_writer import (
        create_person, update_person, find_existing_person,
        merge_person_into, repair_person, DuplicatePersonError,
    )

    existing = find_existing_person(
        workspace_root,
        name="Dustin Sample",
        email="dustin@example.com",
    )
    if existing:
        update_person(workspace_root, existing["id"],
                      last_interaction="2026-05-08")
    else:
        try:
            record = create_person(
                workspace_root,
                canonical_name="Rio Sample",
                primary_org_id="org_005",
                role="Project Manager",
                aliases=["Rio N"],
                notes="Project manager at Summit Company.",
            )
        except DuplicatePersonError as e:
            # surface "already exists as e.person_id" to the user
            ...

USAGE (CLI for bash callers)
----------------------------

    python people_writer.py --workspace "$WS" find \
        --name "Dustin Sample" --email dustin@example.com

    python people_writer.py --workspace "$WS" create \
        --canonical-name "Rio Sample" \
        --primary-org-id org_005 \
        --role "Project Manager" \
        --notes "Project manager at Summit Company."

    python people_writer.py --workspace "$WS" merge \
        --keep-id person_004 --duplicate-id person_064

    python people_writer.py --workspace "$WS" repair \
        --id person_063 \
        --rename display_name=canonical_name \
        --rename current_org_id=primary_org_id \
        --drop first_seen_at --drop first_seen_source --drop confidence \
        --set first_seen='"2026-04-30"'

Exit codes: 0 ok, 1 not-found (find), 2 duplicate / arg error.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any

# Sibling-import atomic_write.py from the same scripts/ folder.
sys.path.insert(0, str(Path(__file__).parent))
from atomic_write import atomic_write_json, atomic_write_json_locked, atomic_append_jsonl  # noqa: E402
from entities_io import entities_collection  # noqa: E402


def _clock_now(workspace_root=None):
    """CLOCK1 - the corroborated UTC instant this module stamps from.

    Swaps the CLOCK SOURCE only: every window, cutoff, threshold and output
    format around it is unchanged. A machine clock that has not synced used to
    write its own wrong reading straight into the permanent record; this reads
    the same clock, cross-checked against the newest timestamp the workspace
    already holds. Falls back to the raw machine clock if the helper is
    unavailable, so a stamp can never fail for want of corroboration.

    `workspace_root` is threaded in wherever the calling function already
    has one, because a helper that has to GUESS which workspace it is in
    guesses wrong exactly when it matters: a fire's early phases run in
    their own subprocesses, before anything has registered a root.
    """
    try:
        from trusted_now import trusted_now_utc

        return trusted_now_utc(workspace_root)
    except Exception:
        import datetime as _clock_dt

        return _clock_dt.datetime.now(_clock_dt.timezone.utc)


def _enforce_record_scope(workspace_root, *, provenance=None, source_ref=None,
                          account_address=None, holder="people_writer") -> None:
    """The CRM record wall (ACCOUNT_SCOPE §2, review fix 7) — delegate to
    account_scope_gate.enforce_record_scope. Import defensively: a missing
    module must never brick a person write (never-brick posture)."""
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


# Canonical person schema fields, mirrored from
# shared/data-schemas/entities.schema.json $defs.person.properties.
# DO NOT add fields here without first updating the schema. The whole point of
# this script is to refuse to write extra keys. Tests assert this stays in
# sync with the schema (see tests/run_people_writer_test.py).
#
# v3.13.0 evolution: added emails[], phones[], nicknames[] (arrays) — the
# de facto schema 75/83 records already used in M's workspace. The singular
# `email` field stays as deprecated/optional for back-compat (writers should
# prefer emails[] going forward; readers should use get_person_emails() to
# merge both). Same pattern for nicknames vs aliases: aliases is canonical for
# search/disambiguation; nicknames is informal display variants. Phones has
# no singular equivalent; the array is canonical.
ALLOWED_PERSON_FIELDS = {
    "id",                  # required, format person_NNN
    "canonical_name",      # required
    "first_seen",          # required, date YYYY-MM-DD
    "aliases",             # array of strings (canonical name-resolution corpus)
    "nicknames",           # array of strings (informal display variants, v3.13.0+)
    "role",                # string
    "primary_org_id",      # string | null
    "affiliation_ids",     # array of org ids
    "org_id",              # DEPRECATED, kept for back-compat reads
    "email",               # string | null (singular, deprecated v3.13.0+ — prefer emails[])
    "emails",              # array of strings (v3.13.0+ canonical email shape)
    "phones",              # array of strings (v3.13.0+)
    "project_ids",         # array of project ids
    "last_interaction",    # date | null
    "last_touched_at",     # ISO datetime | null (v3.13.6+ — intro-broker bumps on intro_made)
    "connections",         # array of {person_id, connection_source, connection_event_seq, connected_ts} (v3.13.6+ — intro-broker-namespaced relationship-graph edges)
    "notes",               # string | null
    "communication_style", # string | null
    "reports_to_id",       # string | null
    "status",              # "active" | "archived"
    "needs_enrichment",    # bool — provisional record awaiting the people-crm enrichment pull (v3.16+, deep-audit #21). The ON-ENTITY enrichment flag; REPLACES the forbidden pending_review/inferred_from trigger (which this writer strips). people-crm clears it (sets false) after enriching.
    "cadence_override_days",  # number | absent — Phase 6 Quick Win B. User-taught cadence baseline: the retired Pulse chat's "just busy" reply widened it (LIFECYCLE1 — read forever, never written again) so dormancy math (dormancy.effective_baseline) stops re-flagging a gap the CEO has said is normal for this person. Absent on legacy records (reads as no-override).
    "tie",                 # "work" | "personal" | absent (=> work) — SPEC BAL1 D1 partition line. tie=personal people belong to the Balance surface ONLY: Pulse/relationship-moves/team-intel/dormant-scan all skip them. NOT relationship_type (forbidden, org-level).
    "cadence_days",        # number | absent — SPEC BAL1 D1(b). Personal RE-SURFACE interval (date-night / call-Mom cadence), read ONLY by balance.py. Opposite meaning to cadence_override_days (a suppression widener); never fed to dormancy.effective_baseline.
}

REQUIRED_PERSON_FIELDS = {"id", "canonical_name", "first_seen"}

# Forbidden keys the agent has actually emitted in the wild. Listed by name so
# the validator can recommend the correct schema field.
#
# v3.13.0 removed `emails` from this list (now in ALLOWED_PERSON_FIELDS).
# Added entries for the smaller drift fields surfaced in the 2026-05-20 audit:
# first_contact, last_contact, first_name, last_name, company, related_people,
# is_primary_user, created_at, created_by, relationship_type, tier.
FORBIDDEN_PERSON_FIELDS = {
    "display_name":         "canonical_name",
    "name":                 "canonical_name",
    "normalized_name":      "(remove — canonical_name is the source of truth)",
    "current_org_id":       "primary_org_id",
    "org_ids":              "affiliation_ids (array) or primary_org_id (single)",
    "first_seen_at":        "first_seen (date only)",
    "first_contact":        "first_seen (date only)",
    "last_seen":            "last_interaction",
    "last_contact":         "last_interaction",
    "last_interaction_at":  "last_interaction",
    "first_seen_source":    "(remove — track in events.jsonl)",
    "confidence":           "(remove — track in events.jsonl)",
    "inferred_from":        "(remove — track in events.jsonl)",
    "role_at_primary_org":  "role",
    "thread_associations":  "project_ids",
    "pending_review":       "(remove — gate via events.jsonl)",
    "enriched_at":          "(remove — gate via events.jsonl)",
    "enriched_from":        "(remove — gate via events.jsonl)",
    "low_signal":           "(remove — gate via events.jsonl)",
    "first_name":           "(merge into canonical_name)",
    "last_name":            "(merge into canonical_name)",
    "company":              "primary_org_id (resolve org name to org_id first)",
    "related_people":       "reports_to_id (if reporting relationship) or notes",
    "is_primary_user":      "(remove — workspace.user_id in entities.json marks the workspace owner)",
    "created_at":           "(remove — track via person_created event in events.jsonl)",
    "created_by":           "(remove — track via person_created event in events.jsonl)",
    "relationship_type":    "(remove — this is org-level, not person-level)",
    "tier":                 "(remove — this is org-level, not person-level)",
}

PERSON_ID_RE = re.compile(r"^person_(\d{3,})$")


# Forbidden-to-canonical map for legacy data normalization during merges.
# Subset of FORBIDDEN_PERSON_FIELDS — only the keys that have a clean rename
# target. Keys mapped to "(remove ...)" are dropped, not renamed.
#
# v3.13.0: added first_contact → first_seen, last_contact → last_interaction
# (per the 2026-05-20 audit — 7 records used first_contact, 1 used last_contact).
LEGACY_KEY_RENAMES = {
    "display_name":        "canonical_name",
    "name":                "canonical_name",
    "current_org_id":      "primary_org_id",
    "org_ids":             "affiliation_ids",
    "first_seen_at":       "first_seen",
    "first_contact":       "first_seen",
    "last_seen":           "last_interaction",
    "last_contact":        "last_interaction",
    "last_interaction_at": "last_interaction",
    "role_at_primary_org": "role",
    "thread_associations": "project_ids",
}


# ---------- exceptions ----------

class DuplicatePersonError(Exception):
    """Raised when create_person detects an existing record."""

    def __init__(self, person_id: str, canonical_name: str | None):
        self.person_id = person_id
        self.canonical_name = canonical_name
        super().__init__(
            f"duplicate detected: {person_id} (canonical_name={canonical_name!r}). "
            f"Use update_person({person_id}, ...) to extend, or pass skip_dedup=True "
            f"if you really want a separate record."
        )


class MultipleCandidatesError(Exception):
    """Raised by `find_existing_person` when the query (name / alias) matches more
    than one record, OR when the only match comes via a single-token / alias-only
    hit that's too ambiguous to auto-commit (e.g., query="Bo" matches the
    record "Bo Sample" via that record's "Bo" alias — could be the same
    person or could be a different Bo).

    v3.13.7+ — added per Session-22 Bug #19. The earlier loose match silently
    auto-attached a new "Bo (Acme Co)" creation to an existing Bo Sample
    person record. Caught only because the writer was running through a
    diagnostic confirm prompt; in production it would have corrupted the entity
    graph silently.

    Caller contract: catch this error, render a disambiguation widget showing
    `self.candidates` so the user picks "same person — merge" / "different
    person with the same first name — create new" / "skip." See
    `skills/apply-choices/SKILL.md` Step 3a for the canonical handling pattern.

    Attributes:
      candidates: list of person record dicts that matched the query
      query: dict {name, email, aliases} reflecting what was passed in
      reason: plain-English explanation of why disambiguation is required
    """

    def __init__(self, candidates: list[dict], *, query: dict | None = None, reason: str = ""):
        self.candidates = list(candidates)
        self.query = query or {}
        self.reason = reason
        names = ", ".join(
            c.get("canonical_name") or c.get("id", "?") for c in self.candidates
        )
        query_str = ""
        if self.query:
            parts = []
            if self.query.get("name"):
                parts.append(f"name={self.query['name']!r}")
            if self.query.get("email"):
                parts.append(f"email={self.query['email']!r}")
            if self.query.get("aliases"):
                parts.append(f"aliases={self.query['aliases']!r}")
            query_str = " | ".join(parts)
        super().__init__(
            f"find_existing_person: {len(self.candidates)} candidate(s) require "
            f"disambiguation — {names}. {reason} [query: {query_str}]".strip()
        )


# ---------- private helpers ----------

def _today_iso() -> str:
    return _clock_now().astimezone().date().isoformat()


def _now_iso() -> str:
    # FS-03: UTC-aware, not naive local (the F-15 naive-local-clock bug class).
    return _clock_now().replace(microsecond=0).isoformat()


def _normalize_name(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _entities_path(workspace_root: Path) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "entities.json"


def _events_path(workspace_root: Path) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _load_entities(workspace_root: Path) -> dict:
    return json.loads(_entities_path(workspace_root).read_text(encoding="utf-8"))


def _save_entities(workspace_root: Path, data: dict, source_skill: str) -> None:
    """Persist entities.json via the locked + parse-verified writer (v3.13.0+).

    Routes through atomic_write_json_locked so:
      - concurrent writers can't overwrite each other's changes (cross-process
        sentinel lock at entities.json.lock — see acquire_write_lock)
      - post-write parse failure auto-restores from the newest backup and
        raises (the calling skill should retry or surface to the user)
      - the `holder` field in the lock file names the writing skill so any
        contention surfaces who's writing
    """
    data["version"] = int(data.get("version", 0)) + 1
    data["last_updated"] = _now_iso()
    data["last_writer"] = source_skill
    atomic_write_json_locked(
        _entities_path(workspace_root),
        data,
        holder=source_skill,
    )


def _next_person_id(people: list[dict]) -> str:
    max_n = 0
    for p in people:
        m = PERSON_ID_RE.match(p.get("id", ""))
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"person_{max_n + 1:03d}"


def _validate_person(record: dict) -> None:
    extras = set(record) - ALLOWED_PERSON_FIELDS
    if extras:
        msgs = []
        for k in sorted(extras):
            if k in FORBIDDEN_PERSON_FIELDS:
                msgs.append(f"  - {k!r} → use {FORBIDDEN_PERSON_FIELDS[k]!r}")
            else:
                msgs.append(f"  - {k!r} (not in schema)")
        raise ValueError(
            "person record has fields not allowed by the schema. "
            "If you genuinely need a new field, update "
            "shared/data-schemas/entities.schema.json $defs.person AND "
            "ALLOWED_PERSON_FIELDS in people_writer.py first.\n" + "\n".join(msgs)
        )
    missing = REQUIRED_PERSON_FIELDS - set(record)
    if missing:
        raise ValueError(f"person record missing required fields: {sorted(missing)}")
    if not PERSON_ID_RE.match(record["id"]):
        raise ValueError(f"id must match person_NNN, got: {record['id']!r}")
    try:
        datetime.date.fromisoformat(record["first_seen"])
    except (ValueError, TypeError):
        raise ValueError(
            f"first_seen must be ISO date YYYY-MM-DD, got: {record['first_seen']!r}"
        )
    if record.get("last_interaction") is not None:
        try:
            datetime.date.fromisoformat(record["last_interaction"])
        except (ValueError, TypeError):
            raise ValueError(
                f"last_interaction must be ISO date YYYY-MM-DD or null, "
                f"got: {record['last_interaction']!r}"
            )
    if record.get("tie") is not None and record["tie"] not in ("work", "personal"):
        raise ValueError(
            f"tie must be 'work' or 'personal', got: {record['tie']!r}"
        )


def _normalize_legacy_keys(record: dict) -> dict:
    """Return a copy of `record` with legacy keys renamed/cleaned up to match
    the canonical schema. Used by merge_person_into so a legacy-shaped
    duplicate's data isn't silently dropped by the strip-non-schema step.

    v3.13.0 changes:
      - `emails` (plural) is now CANONICAL — no longer downgraded to singular
        `email`. Both fields can co-exist; `email` is deprecated/optional.
      - `nicknames` and `phones` are now CANONICAL arrays — preserved as-is.
      - Added drop-handling for forbidden "(remove)" keys (`inferred_from`,
        `pending_review`, `created_at`, `created_by`, `is_primary_user`,
        `enriched_*`, `low_signal`, `confidence`, etc.). These are dropped
        rather than rename-mapped because the canonical home is events.jsonl,
        not the person record.
      - Added `first_name` + `last_name` → `canonical_name` (combined when
        both present and canonical_name is missing).
      - Added `first_contact` → `first_seen`, `last_contact` → `last_interaction`
        (per LEGACY_KEY_RENAMES).

    Special cases:
      - `first_seen_at` (datetime) → `first_seen` (date only). Truncates time.
    """
    out = dict(record)

    # Combine first_name + last_name → canonical_name when both present and
    # canonical_name absent. Drop the sub-fields after.
    if (
        "canonical_name" not in out
        and ("first_name" in out or "last_name" in out)
    ):
        parts = [str(out.get(k, "")).strip() for k in ("first_name", "last_name")]
        parts = [p for p in parts if p]
        if parts:
            out["canonical_name"] = " ".join(parts)
    out.pop("first_name", None)
    out.pop("last_name", None)

    # Rename legacy keys to their canonical equivalents. Skip if the canonical
    # key is already present (preserve canonical-side data).
    for old, new in LEGACY_KEY_RENAMES.items():
        if old in out:
            value = out.pop(old)
            if old == "first_seen_at" and isinstance(value, str):
                value = value[:10]
            if new not in out:
                out[new] = value

    # Drop forbidden keys whose canonical home is events.jsonl or workspace
    # config, not the person record. Per v3.13.0 — these were causing 76 of
    # 83 records to fail _validate_person before the legacy-key sweep.
    KEYS_TO_DROP = {
        "inferred_from",
        "pending_review",
        "created_at",
        "created_by",
        "is_primary_user",
        "enriched_at",
        "enriched_from",
        "low_signal",
        "confidence",
        "first_seen_source",
        "normalized_name",
        "related_people",
        "relationship_type",  # org-level, not person-level
        "tier",               # org-level, not person-level
        "company",            # should be resolved to primary_org_id by caller
    }
    for k in KEYS_TO_DROP:
        out.pop(k, None)

    return out


def get_person_emails(record: dict) -> list[str]:
    """Return the unified list of email addresses for a person record.

    v3.13.0+: records may carry `emails` (canonical array) and/or `email`
    (deprecated singular). This helper merges both into one list with the
    canonical `emails` array first, then `email` if not already present.
    Empty/whitespace-only values are filtered out.

    Use this in every consumer that needs to read a person's emails.
    Never read `record["email"]` directly — you'll miss data on records
    that only have the canonical `emails` array.
    """
    out: list[str] = []
    emails = record.get("emails") or []
    if isinstance(emails, list):
        for e in emails:
            if isinstance(e, str) and e.strip():
                out.append(e.strip())
    single = record.get("email")
    if isinstance(single, str) and single.strip() and single.strip() not in out:
        out.append(single.strip())
    return out


def get_person_phones(record: dict) -> list[str]:
    """Return the list of phone numbers for a person record (v3.13.0+).

    Person records carry `phones` (array) as the canonical field. No singular
    equivalent. Empty/whitespace-only entries are filtered out.
    """
    phones = record.get("phones") or []
    if not isinstance(phones, list):
        return []
    return [p.strip() for p in phones if isinstance(p, str) and p.strip()]


def get_person_display_names(record: dict) -> list[str]:
    """Return the unified list of names to use for display/disambiguation:
    canonical_name first, then all aliases and nicknames (deduplicated).

    v3.13.0+: `nicknames` is a canonical array of informal display variants
    (distinct from `aliases`, which is the name-resolution corpus). Most
    consumers want both flattened together with canonical_name leading.
    """
    out: list[str] = []
    canon = record.get("canonical_name")
    if isinstance(canon, str) and canon.strip():
        out.append(canon.strip())
    for source in ("aliases", "nicknames"):
        vals = record.get(source) or []
        if isinstance(vals, list):
            for v in vals:
                if isinstance(v, str) and v.strip() and v.strip() not in out:
                    out.append(v.strip())
    return out


def list_same_name_people(
    workspace_root: str | Path,
    name: str,
    *,
    include_archived: bool = False,
    max_candidates: int = 8,
) -> list[dict]:
    """All person records sharing a name token with `name` (F13, v4.8.1).

    The add-person elicit path's pre-check: before rendering the add form for
    a sparse input ("add a new person: Quinn"), the form header must NAME the
    existing same-name people ("You already have Quinn Sample and Quinn
    Stone — one of them?") — never just gesture at collision risk.
    `find_existing_person` can't provide that list: its exact-match tiers miss
    first-name-only overlap with multi-token canonical names ("quinn" !=
    "quinn sample"), and `entity_resolve.resolve_all` early-returns on the
    first exact alias hit, so neither reliably surfaces ALL same-name records.

    Token-level match: a record matches when ANY whitespace token of the query
    equals (case-insensitive) ANY token of its canonical_name, aliases, or
    nicknames. Deterministic, no fuzzy scoring — this feeds a form-header
    line, not an auto-match; create-time dedup stays with
    find_existing_person / create_person exactly as before.

    Returns up to `max_candidates` records, archived excluded unless
    `include_archived=True`; exact full-name matches sort first, then by
    canonical_name.
    """
    query_norm = _normalize_name(name or "")
    query_tokens = set(query_norm.split())
    if not query_tokens:
        return []

    data = _load_entities(Path(workspace_root))
    people = entities_collection(data, "people")

    matches: list[dict] = []
    for p in people:
        if not include_archived and p.get("status") == "archived":
            continue
        surface_tokens: set[str] = set()
        for surface in get_person_display_names(p):
            surface_tokens.update(_normalize_name(surface).split())
        if query_tokens & surface_tokens:
            matches.append(p)

    def sort_key(p: dict) -> tuple[int, str]:
        canon_norm = _normalize_name(p.get("canonical_name", ""))
        exact = 0 if canon_norm == query_norm else 1
        return (exact, canon_norm)

    matches.sort(key=sort_key)
    return matches[:max_candidates]


def _log_event(
    workspace_root: Path,
    event_type: str,
    record: dict,
    source_skill: str,
    before: dict | None = None,
    extra: dict | None = None,
) -> None:
    """Append a canonical-shape event to events.jsonl.

    `extra` merges additional keys into `data` (ATTENDEE1). It exists for the
    undo stamps — `brain_batch_id` / `brain_change_class` — which belong on the
    EVENT and never on the record: `_validate_person` owns the record's schema,
    and a batch id is a property of the gesture that made the record, not of
    the person. Keys already present are never overwritten, so the canonical
    `person_id` / `canonical_name` pair cannot be shadowed by a caller.

    v3.13.6+ — event shape matches events.schema.json: top-level `seq` + `ts`
    + `type` + `source_skill` + `data`. The pre-v3.13.6 shape used `timestamp`
    instead of `ts` and put `person_id` / `canonical_name` / `before` at the
    top level (off-schema). Every person create/update/merge/repair was
    polluting events.jsonl with rows that failed schema validation. Fixed
    by:
      - renaming `timestamp` → `ts`
      - nesting per-event fields under `data` (canonical Event shape)
      - delegating seq assignment to atomic_append_jsonl
    """
    data: dict[str, Any] = {
        "person_id": record.get("id"),
        "canonical_name": record.get("canonical_name"),
    }
    if before is not None:
        data["before"] = before
        # FB-plumbing item 4 — make the person_updated receipt legible: name the
        # fields that actually changed (added, removed, or re-valued) so a
        # reader can see "tie" / "cadence_days" moved without diffing the whole
        # record. Additive; `before` stays for full-history readers. Only
        # meaningful on updates (create/repair pass before implicitly).
        try:
            keys = set(before) | set(record)
            changed = sorted(
                k for k in keys if before.get(k) != record.get(k)
            )
            if changed:
                data["updated_fields"] = changed
        except Exception:
            pass
    for key, value in (extra or {}).items():
        if value is not None and key not in data:
            data[key] = value
    event: dict[str, Any] = {
        # FS-03: OMIT ts — the append gate stamps it UTC-aware.
        "type": event_type,
        "source_skill": source_skill,
        "data": data,
    }
    atomic_append_jsonl(_events_path(workspace_root), [event])


# Fact categories (SPEC HIST1 D3). `role` / `company_news` are
# identity-adjacent — the structured auto tier excludes them (S2); they ride
# the confirm rail even from a structured source. Explicit user statements
# and confirmed proposals may use any category.
FACT_CATEGORIES = frozenset({
    "preference", "contact", "personal", "role", "company_news", "other",
})

# The ONLY categories legal on the `entity_fact_structured` auto tier
# (SPEC HIST1 Part 2, D3/S2). A wrong auto `role`/`company_news` fact would
# poison every composer that reads facts even though it never mutates the
# record — identity-adjacent categories stay confirm, enforced here at the
# writer (code-deep, the R1 set_org_money-sanction discipline) and again in
# brain_proposals for any propose-path caller. org_writer mirrors this set;
# the auto-tier test pins the three copies equal.
AUTO_FACT_CATEGORIES = frozenset({"preference", "contact", "personal"})


def _last_appended_seq(workspace_root: Path) -> int | None:
    """Best-effort seq of the most recently appended event (SPEC HIST1 S4).

    Used to synthesize a non-null `source_ref` for auto-emitted lineage
    events ("update:<source_skill>:<seq>") referencing the person_updated
    event that just landed. next_seq() - 1 is exact in single-writer flows
    (tests, normal skill fires); under concurrent appends it is best-effort
    by design — the D10 contract is "never null", not "provably exact".
    """
    try:
        from next_seq import next_seq
        n = next_seq(_events_path(workspace_root))
        return n - 1 if n > 1 else None
    except Exception:
        return None


def _emit_lineage_events(
    workspace_root: Path,
    before: dict,
    after: dict,
    source_skill: str,
) -> list[dict]:
    """SPEC HIST1 D2 — append person_role_changed / person_org_changed when
    the applied update moved role / primary_org_id.

    Gate (HIST1 risk: lineage double-emit / backfill churn): emit ONLY when
    before != after AND both sides are non-empty. Filling an empty field for
    the first time (backfill/enrichment) is not a move; clearing a field is
    not a move. Callers running migration sets pass suppress_lineage=True to
    update_person and this never runs.

    The change was already confirm-gated upstream (person_update_proposal /
    the people-crm Writer Contract) — no new gate, no new prompt. The head
    field still updates; these events preserve the prior value as history.
    Returns the appended events (empty when nothing moved).
    """
    events: list[dict] = []
    upd_seq = None  # resolved lazily — only when something actually moved

    def _source_ref() -> str:
        nonlocal upd_seq
        if upd_seq is None:
            upd_seq = _last_appended_seq(workspace_root) or 0
        return f"update:{source_skill}:{upd_seq}"

    def _nonempty(v) -> bool:
        return isinstance(v, str) and bool(v.strip())

    name = after.get("canonical_name")

    from_role, to_role = before.get("role"), after.get("role")
    if _nonempty(from_role) and _nonempty(to_role) and from_role != to_role:
        events.append({
            "type": "person_role_changed",
            "source_skill": source_skill,
            "org_ids": [after["primary_org_id"]] if after.get("primary_org_id") else [],
            "data": {
                "person_id": after.get("id"),
                "canonical_name": name,
                "from_role": from_role,
                "to_role": to_role,
                "org_id": after.get("primary_org_id"),
                "source_ref": _source_ref(),
                "summary": f"Role: {from_role} → {to_role}",
            },
        })

    from_org, to_org = before.get("primary_org_id"), after.get("primary_org_id")
    if _nonempty(from_org) and _nonempty(to_org) and from_org != to_org:
        events.append({
            "type": "person_org_changed",
            "source_skill": source_skill,
            "org_ids": [from_org, to_org],
            "data": {
                "person_id": after.get("id"),
                "canonical_name": name,
                "from_org_id": from_org,
                "to_org_id": to_org,
                "from_role": before.get("role"),
                "to_role": after.get("role"),
                "source_ref": _source_ref(),
                "summary": "Moved company",
            },
        })

    if events:
        atomic_append_jsonl(_events_path(workspace_root), events)
    return events


def record_person_fact(
    workspace_root: str | Path,
    person_id: str,
    fact: str,
    source_ref: str,
    *,
    category: str | None = None,
    confidence: str = "high",
    source_skill: str = "people_writer",
    brain_batch_id: str | None = None,
    brain_change_class: str | None = None,
) -> dict:
    """Append a person_fact_observed event (SPEC HIST1 D3) — additive,
    sourced, and NEVER a mutation of the person record (facts are events;
    a history[]/facts[] field on the entity is the forbidden shape, D1).

    Callers: an explicit user statement ("remember Sam prefers Signal" —
    the user is the authority, confidence 'high', no proposal), a confirmed
    brain_proposal, or (Part 2) the structured-connector auto tier — which
    MUST pass `brain_change_class="entity_fact_structured"` +
    `brain_batch_id` so the write is batch-reversible via
    brain_undo.undo_batch (the entity_fact_retracted reverser). Never call
    this from a silent prose-inferred path — those go through the confirm
    rail (D3).

    source_ref is REQUIRED (facts are always sourced — D2/S4). category is
    optional, one of FACT_CATEGORIES when present — EXCEPT on the auto
    class, where it is required and restricted to AUTO_FACT_CATEGORIES
    (S2: identity-adjacent categories stay confirm even from a structured
    source; enforced here, code-deep). Raises KeyError on an unknown
    person_id (callers run ENTITY_RESOLVE first — never call this on an
    unresolved id, Bug #19 discipline). Returns the appended event.
    """
    workspace_root = Path(workspace_root)
    fact = (fact or "").strip()
    if not fact:
        raise ValueError("record_person_fact needs a non-empty fact")
    if not isinstance(source_ref, str) or not source_ref.strip():
        raise ValueError("record_person_fact needs a non-empty source_ref — facts are always sourced")
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
    people = entities_collection(data, "people")
    target = next((p for p in people if p.get("id") == person_id), None)
    if target is None:
        raise KeyError(f"no person with id {person_id!r}")

    event: dict[str, Any] = {
        "type": "person_fact_observed",
        "source_skill": source_skill,
        "data": {
            "person_id": person_id,
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


# ---------- public API ----------

def _resolve_shared_address(holders: list, name: str | None) -> dict:
    """TWO OR MORE records hold one address — which one does it resolve to?

    M's ruling, IDENT1 fix round 1 (review F-6). Two people really do share a
    mailbox (a household, a shared work account), and after a split BOTH
    records legitimately carry the address, because they both really use it.
    Before this, Tier 1 returned the FIRST record in list order, silently —
    so after a split every later proof, chase and brief still named the person
    the address was folded under, which is the complaint the split was built
    to answer.

    THE RULE:
      1. If the caller says WHO it is looking at — the given name the invite,
         thread or capture carries — the record answering to that name wins.
         An exact full-name match first, then the given name alone; the
         surname is shared in every shape this exists for, so the given name
         is the whole of the discriminating evidence.
      2. Otherwise the OLDER record wins (`first_seen`, then list order). Not
         a guess: with nothing naming a person, the address is more likely the
         one it always was, and stability beats novelty for a value every
         downstream reader keys on.

    Never raises. An ambiguity error here would turn a shared mailbox into a
    question on every single lookup, which is the opposite of the ruling.
    """
    want = _normalize_name(name or "")
    want_first = want.split()[0] if want else ""

    def key(item):
        idx, p = item
        cn = _normalize_name(p.get("canonical_name", ""))
        exact = bool(want) and cn == want
        given = bool(want_first) and cn.split()[:1] == [want_first]
        archived = str(p.get("status") or "") == "archived"
        seen = str(p.get("first_seen") or "9999-12-31")
        return (not exact, not given, archived, seen, idx)

    return sorted(holders, key=key)[0][1]


def find_existing_person(
    workspace_root: str | Path,
    *,
    name: str | None = None,
    email: str | None = None,
    aliases: list[str] | None = None,
) -> dict | None:
    """Look up an existing person record, with v3.13.7+ stricter disambiguation.

    Three tiers in order:

      Tier 1 — email exact (case-insensitive). Always safe to auto-match
      because email is unique. One holder returns that record, unchanged. When
      SEVERAL records hold one address — a shared mailbox, or a record the
      split rule pulled two people out of — `_resolve_shared_address` decides:
      the record whose given name the caller named, else the older record. It
      never raises, because a shared mailbox must not become a question on
      every lookup (review F-6, ruled 2026-09-08).

      Tier 2 — multi-token canonical_name exact match (query has ≥2 tokens AND
      matches an existing record's `canonical_name` exactly, whitespace-normalized
      lowercase). Safe to auto-match: a full name match is unambiguous. If 2+
      records share the same canonical_name (rare — usually a workspace hygiene
      problem), raises `MultipleCandidatesError` so the caller can disambiguate.

      Tier 3 — alias-only or single-token query hits. ANY match at this tier
      raises `MultipleCandidatesError`, even if only one candidate matches. The
      query is too ambiguous to safely commit a write — query="Bo" hitting
      an existing record's "Bo" alias could be the same person or a different
      person with the same first name. Caller must surface a disambiguation
      widget (apply-choices Step 3a pattern).

    Why Tier 3 raises on a single match (v3.13.7+ Bug #19 fix): pre-v3.13.7 the
    function returned the single record on alias hit, and `create_person` then
    raised `DuplicatePersonError` saying "X already exists." That's wrong when
    the caller's intent was "add a different X with the same first name" —
    silently routed to the existing record. Session-22 test caught the case
    only because Cowork was running a diagnostic confirm prompt; in production
    it would have corrupted the entity graph.

    Returns:
      - dict — single safe match (Tier 1 or Tier 2 with one canonical hit)
      - None — no candidates found

    Raises:
      MultipleCandidatesError — when Tier 2 has >1 hit, or when ANY Tier 3 hit
      surfaces (including single-candidate alias matches). Caller must catch
      this and route through a disambiguation widget.
    """
    data = _load_entities(Path(workspace_root))
    people = entities_collection(data, "people")

    # Tier 1 — email exact
    if email:
        target = email.strip().lower()
        holders: list[tuple[int, dict]] = []
        for idx, p in enumerate(people):
            # Check both canonical `emails` array (v3.13.0+) and deprecated
            # singular `email`. A record might carry one, the other, or both.
            for e in get_person_emails(p):
                if e.strip().lower() == target:
                    holders.append((idx, p))
                    break
        if len(holders) == 1:
            return holders[0][1]
        if holders:
            return _resolve_shared_address(holders, name)

    query_strings = [c for c in [name, *(aliases or [])] if c]
    if not query_strings:
        return None

    query_record = {"name": name, "email": email, "aliases": list(aliases or [])}

    # Tier 2 — multi-token exact canonical_name match
    canonical_matches: list[dict] = []
    canonical_seen: set[str] = set()
    for p in people:
        cn = _normalize_name(p.get("canonical_name", ""))
        if not cn:
            continue
        if p.get("id") in canonical_seen:
            continue
        for q in query_strings:
            qn = _normalize_name(q)
            if cn == qn and len(qn.split()) >= 2:
                canonical_matches.append(p)
                canonical_seen.add(p.get("id", ""))
                break

    if len(canonical_matches) == 1:
        return canonical_matches[0]
    if len(canonical_matches) > 1:
        raise MultipleCandidatesError(
            canonical_matches,
            query=query_record,
            reason=(
                "Multiple records share this canonical name — workspace likely has "
                "duplicates that need merging before further writes."
            ),
        )

    # Tier 3 — alias-only or single-token query hits. NEVER auto-match here.
    targets_normed = {_normalize_name(q) for q in query_strings if q}
    alias_candidates: list[dict] = []
    alias_seen: set[str] = set()
    for p in people:
        if p.get("id") in alias_seen:
            continue
        names = [p.get("canonical_name", "")] + list(p.get("aliases", []) or [])
        for n in names:
            if n and _normalize_name(n) in targets_normed:
                alias_candidates.append(p)
                alias_seen.add(p.get("id", ""))
                break

    if not alias_candidates:
        return None

    raise MultipleCandidatesError(
        alias_candidates,
        query=query_record,
        reason=(
            "Match via alias or single-token query — too ambiguous to auto-commit. "
            "Surface a disambiguation widget: is this the same person, or a "
            "different person with the same first name? Bug #19 fix per v3.13.7."
        ),
    )


SINGLE_WORD_NAME_LINE = (
    "Nothing was saved, because a person needs a first and last name here - "
    "say the full name and it will be added.")


class SingleWordNameRefused(ValueError):
    """IDN-01 (PARALLEL-A lane B, 2026-09-24): a NEW person record may not be
    created under a one-word canonical name. One-word stubs (a bare first
    name) are what the resolver used to pick over real contacts; refusing
    them at the writer keeps new ones out. Carries `cr_refusal_reason` and
    `line` so a door caller answers `ok:false` with the one sentence."""

    cr_refusal_reason = "single_word_name"

    def __init__(self, name: str):
        super().__init__(SINGLE_WORD_NAME_LINE)
        self.name = name
        self.line = SINGLE_WORD_NAME_LINE


def refuse_single_word_name(canonical_name: str) -> None:
    """Raise SingleWordNameRefused when `canonical_name` has fewer than two
    whole tokens. Existing records are never touched - only creation."""
    if len((canonical_name or "").strip().split()) < 2:
        raise SingleWordNameRefused(canonical_name)


def create_person(
    workspace_root: str | Path,
    *,
    canonical_name: str,
    primary_org_id: str | None = None,
    email: str | None = None,
    role: str | None = None,
    notes: str | None = None,
    aliases: list[str] | None = None,
    affiliation_ids: list[str] | None = None,
    project_ids: list[str] | None = None,
    first_seen: str | None = None,
    last_interaction: str | None = None,
    needs_enrichment: bool = False,
    source_skill: str = "people_writer",
    skip_dedup: bool = False,
    provenance: dict | None = None,
    source_ref: str | None = None,
    account_address: str | None = None,
    brain_batch_id: str | None = None,
    brain_change_class: str | None = None,
) -> dict:
    """Create a person record. Returns the new record (with assigned id).

    ATTENDEE1 — `brain_batch_id` / `brain_change_class` stamp the
    `person_created` event so `brain_undo.resolve_batch` can find this creation
    and its registered reverser can archive it. They TRAVEL TOGETHER or raise
    ValueError, the same rule `commitment_state.supersede_commitment` and
    `record_person_fact` enforce, and for the same reason: a batch id with no
    class is a change `undo_batch` will list and then refuse to reverse, which
    is worse than an unstamped write. The stamps land on the EVENT only — never
    on the record.

    Raises ValueError on schema violations. Raises DuplicatePersonError when an
    existing record matches by email / alias / canonical_name unless
    skip_dedup=True.

    Account-scope wall (connector-agnostic-v1, review fix 7): when the CALLER
    derived this record from a connector read, pass the read's provenance
    (`provenance` dict / `source_ref` / `account_address` — the mailbox the
    mail arrived through, NOT the contact's own email). A payload resolving to
    an out-of-scope account raises AccountScopeError BEFORE any entities.json
    write. Manual adds (no provenance kwargs) are unaffected. These kwargs are
    scope inputs only — they are never stored on the person record.
    """
    workspace_root = Path(workspace_root)
    if (brain_batch_id is None) != (brain_change_class is None):
        raise ValueError(
            "brain_batch_id and brain_change_class travel together — a batch "
            "id with no change class is a creation `undo_batch` will list and "
            "then refuse to reverse")
    refuse_single_word_name(canonical_name)
    _enforce_record_scope(workspace_root, provenance=provenance,
                          source_ref=source_ref,
                          account_address=account_address,
                          holder=source_skill)
    if not skip_dedup:
        existing = find_existing_person(
            workspace_root,
            name=canonical_name,
            email=email,
            aliases=aliases,
        )
        if existing:
            raise DuplicatePersonError(existing["id"], existing.get("canonical_name"))

    data = _load_entities(workspace_root)
    people = entities_collection(data, "people")

    record: dict[str, Any] = {
        "id": _next_person_id(people),
        "canonical_name": canonical_name.strip(),
        "first_seen": first_seen or _today_iso(),
    }
    if aliases:          record["aliases"] = list(aliases)
    if role:             record["role"] = role
    if primary_org_id:   record["primary_org_id"] = primary_org_id
    if affiliation_ids:  record["affiliation_ids"] = list(affiliation_ids)
    if email:            record["email"] = email
    if project_ids:      record["project_ids"] = list(project_ids)
    if needs_enrichment: record["needs_enrichment"] = True
    if last_interaction: record["last_interaction"] = last_interaction
    if notes:            record["notes"] = notes

    _validate_person(record)

    people.append(record)
    _save_entities(workspace_root, data, source_skill)
    _log_event(workspace_root, "person_created", record, source_skill,
               extra={"brain_batch_id": brain_batch_id,
                      "brain_change_class": brain_change_class})
    return record


def auto_add_person(
    workspace_root: str | Path,
    *,
    canonical_name: str,
    email: str | None = None,
    email_provenance: dict | str | None = None,
    source_skill: str = "people_writer",
    **create_kwargs,
) -> dict:
    """FS-11 (M ruling 2026-07-15) — auto-add a person from rich context, with
    two guardrails that make auto-creation safe:

      1. **Same-name dedup gate (runs BEFORE every auto-add).** If any existing
         person shares a name token with `canonical_name`
         (`list_same_name_people`), DO NOT auto-create — return
         `{"status": "needs_confirm", "matches": [...]}` so the surface asks
         "is this the same person?" instead of silently forking a duplicate.

      2. **Observed-provenance email capture (F-08 extended to capture).** An
         email is stored ONLY when it arrived with provenance — an OBSERVED
         source (the message / meeting the person surfaced from). A caller
         passing `email` WITHOUT `email_provenance` gets the person created
         WITHOUT the email and `email_dropped_no_provenance=True` in the result;
         a pattern-guessed / constructed address is NEVER written.

    On success returns `{"status": "added", "record": {...},
    "email_dropped_no_provenance": bool}`. Undo is ARCHIVE, not delete
    (`update_person(..., status="archived")`) — the R1 archive-never-delete
    reverser that `brain_undo` registers for person creation.

    ATTENDEE1 — `brain_batch_id` / `brain_change_class` ride through
    `create_kwargs` to `create_person`, which stamps them on the
    `person_created` event so that reverser can actually be REACHED. Pass both
    or neither; `create_person` raises on one alone. An auto rail that creates
    a record `undo` cannot find is not reversible, and reversibility is the
    predicate that licenses the auto tier at all.
    """
    matches = list_same_name_people(workspace_root, canonical_name)
    if matches:
        return {"status": "needs_confirm", "matches": matches, "record": None}

    email_dropped = False
    stored_email = email
    if email and not email_provenance:
        # F-08 at capture time: no observed source → don't store the address.
        stored_email = None
        email_dropped = True

    record = create_person(
        workspace_root,
        canonical_name=canonical_name,
        email=stored_email,
        source_skill=source_skill,
        # provenance/source_ref for the SCOPE wall are passed through if the
        # caller supplied them via create_kwargs; the email_provenance above is
        # the capture guard, a distinct concern.
        **create_kwargs,
    )
    return {"status": "added", "record": record,
            "email_dropped_no_provenance": email_dropped}


def update_person(
    workspace_root: str | Path,
    person_id: str,
    *,
    source_skill: str = "people_writer",
    provenance: dict | None = None,
    source_ref: str | None = None,
    account_address: str | None = None,
    suppress_lineage: bool = False,
    **fields: Any,
) -> dict:
    """Update fields on an existing person. Returns the updated record. Field
    names are validated against the schema; unknown keys raise ValueError with
    a remediation hint.

    `provenance` / `source_ref` / `account_address` are account-scope inputs
    (see create_person) — pass them when the update is derived from a connector
    read; an out-of-scope account raises AccountScopeError before the write.

    Lineage (SPEC HIST1 D2): when the applied update changed `role` or
    `primary_org_id` with both sides non-empty, a person_role_changed /
    person_org_changed event is appended alongside person_updated — the prior
    value is preserved as history instead of vanishing on overwrite. Bulk
    migrations / re-attribution sets pass `suppress_lineage=True` (a
    migration is not a career move — the backfill-churn gate).
    """
    workspace_root = Path(workspace_root)
    _enforce_record_scope(workspace_root, provenance=provenance,
                          source_ref=source_ref,
                          account_address=account_address,
                          holder=source_skill)
    data = _load_entities(workspace_root)
    people = entities_collection(data, "people")

    target = next((p for p in people if p.get("id") == person_id), None)
    if target is None:
        raise KeyError(f"no person with id {person_id!r}")

    extras = set(fields) - ALLOWED_PERSON_FIELDS
    if extras:
        msgs = []
        for k in sorted(extras):
            if k in FORBIDDEN_PERSON_FIELDS:
                msgs.append(f"  - {k!r} → use {FORBIDDEN_PERSON_FIELDS[k]!r}")
            else:
                msgs.append(f"  - {k!r} (not in schema)")
        raise ValueError("update_person rejected unknown fields:\n" + "\n".join(msgs))

    before = dict(target)
    nullable = {"primary_org_id", "email", "last_interaction", "notes",
                "communication_style", "reports_to_id", "org_id"}
    for k, v in fields.items():
        if v is None and k not in nullable:
            target.pop(k, None)
        else:
            target[k] = v

    _validate_person(target)
    _save_entities(workspace_root, data, source_skill)
    _log_event(workspace_root, "person_updated", target, source_skill, before=before)
    if not suppress_lineage:
        _emit_lineage_events(workspace_root, before, target, source_skill)
    return target


def _aliases_path(workspace_root: Path) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "aliases.json"


def add_person_alias(
    workspace_root: str | Path,
    person_id: str,
    alias: str,
    *,
    source_skill: str = "people_writer",
) -> dict:
    """THE Same-as writer (v4.6.1 W4b): a raw name spelling is an existing
    person — save it as a permanent resolution improvement so every future
    capture of this spelling resolves to them.

    Two writes, both canonical resolution surfaces (entity_resolve Tier 1a
    reads aliases.json mappings; Tier 1b reads the person record's aliases):
      1. aliases.json — append {"raw": alias, "canonical_id": person_id} to
         mappings.people (canonical dict-of-lists shape; a legacy flat-list
         mappings array is appended to in place — reader-back-compat, never
         a shape rewrite). Locked atomic write.
      2. entities.json — union the spelling into the person record's
         `aliases` array via update_person (validated, locked, logged as
         person_updated).

    Idempotent: an alias already mapped to this person returns
    {"status": "exists"} and writes nothing. An alias mapped to a DIFFERENT
    person raises ValueError — a raw spelling must never silently re-point
    (surface it for a human decision instead). KeyError when person_id
    doesn't exist (from update_person's lookup).
    """
    alias = (alias or "").strip()
    if not alias:
        raise ValueError("add_person_alias needs a non-empty alias spelling")
    workspace_root = Path(workspace_root)
    alias_norm = _normalize_name(alias)

    # Person record must exist (and we need its current aliases + name).
    data = _load_entities(workspace_root)
    people = entities_collection(data, "people")
    target = next((p for p in people if p.get("id") == person_id), None)
    if target is None:
        raise KeyError(f"no person with id {person_id!r}")

    # --- aliases.json mapping ------------------------------------------------
    apath = _aliases_path(workspace_root)
    if apath.exists():
        try:
            aliases_doc = json.loads(apath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Unreadable aliases.json is a workspace-corruption condition —
            # never overwrite it with a fresh doc from here (cleanup's
            # backup-restore owns that). Fail loud instead.
            raise ValueError(
                f"aliases.json at {apath} is unreadable — refusing to "
                "overwrite it; restore it (cleanup keeps backups) and retry"
            )
    else:
        aliases_doc = {"mappings": {"people": [], "projects": [], "orgs": []}}

    mappings = aliases_doc.setdefault("mappings", {})
    if isinstance(mappings, list):
        tier = mappings                      # legacy flat-list shape
    else:
        tier = mappings.setdefault("people", [])

    mapping_written = False
    existing = next(
        (m for m in tier
         if isinstance(m, dict) and isinstance(m.get("raw"), str)
         and _normalize_name(m["raw"]) == alias_norm),
        None,
    )
    if existing is not None:
        if existing.get("canonical_id") != person_id:
            raise ValueError(
                f"alias {alias!r} already maps to {existing.get('canonical_id')!r} "
                f"— refusing to silently re-point it to {person_id!r}. "
                "Surface the conflict for a human decision."
            )
    else:
        tier.append({"raw": alias, "canonical_id": person_id})
        atomic_write_json_locked(apath, aliases_doc, holder=source_skill)
        mapping_written = True

    # --- person record aliases union ------------------------------------------
    record_written = False
    known = {_normalize_name(n) for n in get_person_display_names(target)}
    if alias_norm not in known:
        new_aliases = list(target.get("aliases") or []) + [alias]
        update_person(workspace_root, person_id, aliases=new_aliases,
                      source_skill=source_skill)
        record_written = True

    status = "added" if (mapping_written or record_written) else "exists"
    return {
        "status": status,
        "person_id": person_id,
        "alias": alias,
        "mapping_written": mapping_written,
        "record_written": record_written,
    }


def remove_person_alias(
    workspace_root: str | Path,
    person_id: str,
    alias: str,
    *,
    source_skill: str = "people_writer",
) -> dict:
    """The exact inverse of `add_person_alias` (IDENT1).

    `add_person_alias` shipped with NO removal path, and that absence was
    load-bearing in the wrong direction: two separate auto rails
    (`attendee_evidence`, `contact_capture`) deliberately write no alias at
    all, purely so their reversers stay complete — "an undo that leaves
    residue is not a reversal". The cost of that omission is now visible on
    a live book: a nickname saved against one record, later found to be
    another person's name too, could not be taken back by any code path,
    so the collision had to be repaired by hand — which is exactly the kind
    of hand repair SPEC_FLOW1 forbids.

    Two writes, mirroring the two the adder makes:
      1. aliases.json — drop the `mappings.people` entry whose `raw`
         normalizes to this spelling AND whose `canonical_id` is this
         person. An entry pointing at a DIFFERENT person is left alone and
         reported (`status: "other_owner"`) — never silently re-pointed or
         deleted, the same asymmetry the adder enforces.
      2. entities.json — drop the spelling from the record's `aliases`
         array via `update_person`.

    Idempotent: nothing on file returns {"status": "absent"} and writes
    nothing. The person's CANONICAL name is never removable through here —
    a record must always have a name to be called by, and removing it would
    be a rename wearing an alias's clothes."""
    alias = (alias or "").strip()
    if not alias:
        raise ValueError("remove_person_alias needs a non-empty alias spelling")
    workspace_root = Path(workspace_root)
    alias_norm = _normalize_name(alias)

    data = _load_entities(workspace_root)
    people = entities_collection(data, "people")
    target = next((p for p in people if p.get("id") == person_id), None)
    if target is None:
        raise KeyError(f"no person with id {person_id!r}")
    if _normalize_name(target.get("canonical_name") or "") == alias_norm:
        raise ValueError(
            f"{alias!r} is {person_id}'s canonical name, not an alias — "
            "removing it would leave a record with nothing to call it by; "
            "rename through update_person if that is what you mean")

    apath = _aliases_path(workspace_root)
    mapping_removed = False
    other_owner = None
    if apath.exists():
        try:
            aliases_doc = json.loads(apath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raise ValueError(
                f"aliases.json at {apath} is unreadable — refusing to "
                "overwrite it; restore it (cleanup keeps backups) and retry"
            )
        mappings = aliases_doc.get("mappings")
        tier = mappings if isinstance(mappings, list) else (
            (mappings or {}).get("people") if isinstance(mappings, dict) else None)
        if isinstance(tier, list):
            keep = []
            for m in tier:
                if (isinstance(m, dict) and isinstance(m.get("raw"), str)
                        and _normalize_name(m["raw"]) == alias_norm):
                    if m.get("canonical_id") == person_id:
                        mapping_removed = True
                        continue
                    other_owner = m.get("canonical_id")
                keep.append(m)
            if mapping_removed:
                if isinstance(mappings, list):
                    aliases_doc["mappings"] = keep
                else:
                    aliases_doc["mappings"]["people"] = keep
                atomic_write_json_locked(apath, aliases_doc, holder=source_skill)

    record_written = False
    current = list(target.get("aliases") or [])
    pruned = [a for a in current if _normalize_name(str(a)) != alias_norm]
    if len(pruned) != len(current):
        update_person(workspace_root, person_id, aliases=pruned,
                      source_skill=source_skill)
        record_written = True

    if mapping_removed or record_written:
        status = "removed"
    elif other_owner:
        status = "other_owner"
    else:
        status = "absent"
    return {
        "status": status,
        "person_id": person_id,
        "alias": alias,
        "mapping_removed": mapping_removed,
        "record_written": record_written,
        "other_owner": other_owner,
    }


def merge_person_into(
    workspace_root: str | Path,
    *,
    keep_id: str,
    duplicate_id: str,
    source_skill: str = "people_writer",
) -> dict:
    """Merge fields from `duplicate_id` into `keep_id`, then delete duplicate.

    Field union rules:
      - canonical_name / id: keep wins absolutely
      - first_seen: keep wins if set; otherwise take duplicate's. Many legacy
        records pre-date the schema's first_seen requirement and lack the
        field — taking duplicate's earliest signal is the only way to satisfy
        the validator on merge without inventing a date.
      - email / role / primary_org_id / notes / communication_style /
        reports_to_id: keep wins; only fill if missing
      - last_interaction: max of both
      - aliases / affiliation_ids / project_ids: union, dedup, order preserved
      - duplicate's canonical_name appended to keep's aliases if distinct

    Logs `person_merged` event with both before-records and the merged result.
    """
    workspace_root = Path(workspace_root)
    data = _load_entities(workspace_root)
    people = entities_collection(data, "people")

    keep = next((p for p in people if p.get("id") == keep_id), None)
    dup = next((p for p in people if p.get("id") == duplicate_id), None)
    if keep is None:
        raise KeyError(f"keep_id not found: {keep_id!r}")
    if dup is None:
        raise KeyError(f"duplicate_id not found: {duplicate_id!r}")

    before_keep = dict(keep)
    before_dup = dict(dup)

    # Normalize legacy-shaped duplicate (e.g., person_064-style records with
    # display_name / current_org_id / last_seen) to canonical keys BEFORE
    # union logic runs — otherwise the strip-non-schema step at the end of
    # this function drops the duplicate's data instead of carrying it onto
    # the keeper.
    dup_normalized = _normalize_legacy_keys(dup)

    fill_if_missing = ("first_seen", "email", "role", "primary_org_id", "notes",
                       "communication_style", "reports_to_id")
    for f in fill_if_missing:
        if not keep.get(f) and dup_normalized.get(f):
            keep[f] = dup_normalized[f]

    keep_li = keep.get("last_interaction")
    dup_li = dup_normalized.get("last_interaction")
    if keep_li and dup_li:
        keep["last_interaction"] = max(keep_li, dup_li)
    elif dup_li and not keep_li:
        keep["last_interaction"] = dup_li

    def _union(field: str) -> None:
        merged = list(dict.fromkeys(list(keep.get(field) or []) + list(dup_normalized.get(field) or [])))
        if merged:
            keep[field] = merged

    _union("aliases")
    _union("affiliation_ids")
    _union("project_ids")

    if dup_normalized.get("canonical_name") and dup_normalized["canonical_name"] != keep.get("canonical_name"):
        keep.setdefault("aliases", [])
        if dup_normalized["canonical_name"] not in keep["aliases"]:
            keep["aliases"].append(dup_normalized["canonical_name"])

    for k in list(keep):
        if k not in ALLOWED_PERSON_FIELDS:
            del keep[k]

    _validate_person(keep)

    people[:] = [p for p in people if p.get("id") != duplicate_id]

    _save_entities(workspace_root, data, source_skill)
    _log_event(
        workspace_root,
        "person_merged",
        keep,
        source_skill,
        before={"keep": before_keep, "duplicate": before_dup},
    )
    return keep


def split_person_from_shared_address(
    workspace_root: str | Path,
    *,
    folded_person_id: str,
    canonical_name: str,
    shared_email: str,
    email_provenance: dict | str,
    brain_batch_id: str,
    brain_change_class: str = "person_split",
    source_skill: str = "people_writer",
) -> dict:
    """SPEC_FLOW1 Lane F rule 4 — the INVERSE of `merge_person_into`, for the
    one shape that produced it by accident: two people on ONE record because
    they share ONE address.

    `merge_person_into` is confirm-forever and has no reverser, so a record
    that got folded this way could only be repaired by hand-editing
    entities.json. That is exactly the hand repair the spec forbids, and it is
    why this writer exists: the product does the split, on its own evidence,
    receipted and reversible.

    WHAT IT WRITES, AND WHY THAT IS ALL
      * ONE new person record, carrying `canonical_name` and `shared_email`.
      * NOTHING on the folded record. The shared address STAYS on it — both
        people really do use that address, and stripping it from the original
        would make this an edit of somebody's existing record rather than an
        addition beside it. Add beside, never displace, applies to the entity
        graph too.

    So the reversal is complete by construction: `brain_undo`'s `person_split`
    reverser archives the new record AND clears the shared address off it, and
    the graph is byte-equivalent to before — the address resolves to the
    record it always resolved to.

    THE DEDUP IT KEEPS AND THE ONE IT DELIBERATELY BYPASSES. `create_person`'s
    dedup runs `find_existing_person(name=..., email=...)`, whose Tier 1 is
    email-exact — and email-exact is PRECISELY the tier that must not refuse
    here, because a shared address is the whole premise. So the email tier is
    bypassed (the address is attached after the create) while the NAME tiers
    are run FIRST and in full, by this function, against the same shipped
    resolver: a record already carrying this canonical name means the second
    person is already on file and there is nothing to split. An ambiguous name
    lookup refuses outright. Nothing here can fork a record on a name.

    F-08 AT CAPTURE, MIRRORED. `email_provenance` is REQUIRED and the call
    raises without it. The address is only ever written because it was
    OBSERVED on the invite or thread this split was decided from; a
    provenance-less split would be storing an address on a guess, which is the
    one thing the whole identity family refuses.

    Returns one of:
      {"status": "split", "person_id", "canonical_name", "shared_email",
       "from_person_id", "record"}
      {"status": "already_on_file", "person_id", "detail"}
      {"status": "refused", "detail"}
    Raises ValueError on a malformed call (no provenance, no batch id, a name
    equal to the folded record's own, a record that does not hold the address).
    """
    workspace_root = Path(workspace_root)
    canonical_name = (canonical_name or "").strip()
    shared_email = (shared_email or "").strip().lower()
    if not canonical_name:
        raise ValueError("a split needs the second person's name")
    if not shared_email:
        raise ValueError("a split needs the address the two records share")
    if not email_provenance:
        raise ValueError(
            "split_person_from_shared_address needs `email_provenance` — the "
            "address is written only because it was OBSERVED on the invite or "
            "thread this split was decided from (F-08 at capture)")
    if not brain_batch_id or not brain_change_class:
        raise ValueError(
            "a split is an automatic act, so it must be reversible: "
            "brain_batch_id and brain_change_class travel together")

    data = _load_entities(workspace_root)
    people = entities_collection(data, "people")
    folded = next((p for p in people if p.get("id") == folded_person_id), None)
    if folded is None:
        raise KeyError(f"no person with id {folded_person_id!r}")
    if str(folded.get("status") or "") == "archived":
        return {"status": "refused",
                "detail": "the record at that address is archived — splitting "
                          "somebody out of a retired record would resurrect "
                          "half of it"}
    if shared_email.lower() not in {e.strip().lower()
                                    for e in get_person_emails(folded)}:
        raise ValueError(
            f"{folded_person_id} does not carry {shared_email!r} — a split is "
            f"only defined on an address the two records actually share")
    if _normalize_name(folded.get("canonical_name", "")) == \
            _normalize_name(canonical_name):
        raise ValueError(
            "the split name equals the record's own name — that is the same "
            "person, not a second one")

    # The NAME tiers, in full, before anything is written. Tier 2 (exact
    # multi-token canonical) is the one that matters: it means the second
    # person already has their own record.
    try:
        by_name = find_existing_person(workspace_root, name=canonical_name)
    except MultipleCandidatesError:
        return {"status": "refused",
                "detail": "that name is ambiguous on this workspace — a split "
                          "is never the write that resolves an ambiguity"}
    if by_name is not None:
        return {"status": "already_on_file",
                "person_id": by_name.get("id"),
                "detail": "the second person is already on file"}

    record = create_person(
        workspace_root,
        canonical_name=canonical_name,
        email=None,                      # attached below — see the dedup note
        needs_enrichment=True,
        source_skill=source_skill,
        brain_batch_id=brain_batch_id,
        brain_change_class=brain_change_class,
    )
    try:
        record = update_person(
            workspace_root, record["id"], source_skill=source_skill,
            email=shared_email, suppress_lineage=True,
        )
    except Exception as exc:
        # The record exists and IS stamped, so `undo` reverses it cleanly; it
        # simply has no address yet. Loud, never silent, never half-claimed.
        return {"status": "split_no_address",
                "person_id": record.get("id"),
                "canonical_name": canonical_name,
                "from_person_id": folded_person_id,
                "detail": f"the record was created but the shared address "
                          f"could not be attached: {type(exc).__name__}: {exc}"}
    return {"status": "split", "person_id": record.get("id"),
            "canonical_name": canonical_name, "shared_email": shared_email,
            "from_person_id": folded_person_id, "record": record}


def repair_person(
    workspace_root: str | Path,
    person_id: str,
    *,
    field_renames: dict[str, str] | None = None,
    drop_fields: list[str] | None = None,
    set_fields: dict[str, Any] | None = None,
    source_skill: str = "people_writer",
) -> dict:
    """Rewrite an existing record in-place to bring it back to schema. Use for
    one-off data repairs of records that pre-date the writer contract (e.g.,
    person_063 Rio Sample shape).

    Order of operations: rename → drop → set → strip-non-schema → validate.
    """
    workspace_root = Path(workspace_root)
    data = _load_entities(workspace_root)
    people = entities_collection(data, "people")

    target = next((p for p in people if p.get("id") == person_id), None)
    if target is None:
        raise KeyError(f"no person with id {person_id!r}")

    before = dict(target)

    for old, new in (field_renames or {}).items():
        if old in target:
            target[new] = target.pop(old)

    for k in (drop_fields or []):
        target.pop(k, None)

    for k, v in (set_fields or {}).items():
        target[k] = v

    for k in list(target):
        if k not in ALLOWED_PERSON_FIELDS:
            del target[k]

    _validate_person(target)
    _save_entities(workspace_root, data, source_skill)
    _log_event(workspace_root, "person_repaired", target, source_skill, before=before)
    return target


# ---------- door wrappers (PEOPLEWRITE2, ruling D-W2-5) ----------
#
# people-crm's person writes cross the access layer's doors through these five
# functions and never through the raw writers above. Through the door a raise
# is `helper_failed` with a traceback tail, which a chat must never see
# (SURFACEFIX1 5.3), so each wrapper answers a JSON-able `{ok, ...}` for the
# DOMAIN refusals the raw writers raise (a duplicate, an ambiguous name, a
# one-word name, an out-of-scope account, an id that is not on file) and lets
# anything else raise as today, which the door reports. A typed refusal the
# door already knows (a lease lost, a writer with no identity) is never caught
# here: it carries `cr_refusal_reason` and the door answers it by its own line.
# `door_find_person` is the READ (on the helper door's list) and writes
# nothing; the other four are on the writer door's list, each with a row in
# `shared/WORKSPACE_ACCESS.md`'s writer table.

#: The one sentence a create answers when the record is already on file.
DOOR_DUPLICATE_LINE = "That person is already on file, so nothing new was added."
#: The one sentence an update or a fact answers for an id that is not on file.
DOOR_UNKNOWN_PERSON_LINE = "Nothing was saved, because that person is not on file yet."
#: The one sentence a write answers when the account-scope wall refused it.
DOOR_OUT_OF_SCOPE_LINE = (
    "Nothing was saved, because that contact came through an account that is "
    "not part of your business mail.")
#: The one sentence the dedup read answers when the people file cannot be read.
DOOR_UNREADABLE_LINE = (
    "Nothing was saved, because your people records could not be read just now.")
#: The one sentence an update answers when it tries to change who a record is.
DOOR_IDENTITY_FIELD_LINE = (
    "Nothing was saved, because a person's record cannot be renumbered or have "
    "its history hidden here.")
#: The keys `door_update_person` refuses (N-1): the record's identity, and the
#: switch that would drop the role and company lineage rows.
DOOR_UPDATE_REFUSED_KEYS = frozenset({"id", "suppress_lineage"})

DOOR_SOURCE_SKILL = "people-crm"


def _account_scope_error():
    """The account-scope wall's refusal class, or a class nothing raises when
    the module is absent (the writers' own never-brick posture)."""
    try:
        from account_scope_gate import AccountScopeError
    except ImportError:
        class _Absent(Exception):
            pass
        return _Absent
    return AccountScopeError


def _door_candidates(candidates) -> list:
    """The ambiguous answer's candidates: id and name only, JSON-able."""
    return [{"id": c.get("id"), "canonical_name": c.get("canonical_name")}
            for c in (candidates or []) if isinstance(c, dict)]


def _person_on_file(workspace_root, person_id) -> bool:
    """True when `person_id` names a record in the people collection."""
    data = _load_entities(Path(workspace_root))
    return any(p.get("id") == person_id
               for p in entities_collection(data, "people"))


def door_find_person(
    workspace_root: str | Path,
    *,
    name: str | None = None,
    email: str | None = None,
    aliases: list[str] | None = None,
) -> dict:
    """The dedup read through the helper door: `{ok: true, match}` (the record
    or null), or `{ok: false, reason: "ambiguous", candidates}` where
    `find_existing_person` raises `MultipleCandidatesError`, or `{ok: false,
    reason: "unreadable", line}` when the people file cannot be opened or
    parsed. Writes nothing."""
    try:
        match = find_existing_person(workspace_root, name=name, email=email,
                                     aliases=aliases)
    except MultipleCandidatesError as exc:
        return {"ok": False, "reason": "ambiguous",
                "candidates": _door_candidates(exc.candidates)}
    except (OSError, ValueError):  # ValueError: a JSON or a UTF-8 decode error (N-5)
        # An unreadable people file is never a null match (that would send
        # the caller on to a create over a damaged anchor, FS-15): it is a
        # stop, said in one sentence, and nothing is written.
        return {"ok": False, "reason": "unreadable",
                "line": DOOR_UNREADABLE_LINE}
    return {"ok": True, "match": match}


def door_create_person(
    workspace_root: str | Path,
    *,
    canonical_name: str,
    primary_org_id: str | None = None,
    email: str | None = None,
    role: str | None = None,
    notes: str | None = None,
    aliases: list[str] | None = None,
    affiliation_ids: list[str] | None = None,
    project_ids: list[str] | None = None,
    first_seen: str | None = None,
    last_interaction: str | None = None,
    needs_enrichment: bool = False,
    provenance: dict | None = None,
    source_ref: str | None = None,
    account_address: str | None = None,
    source_skill: str = DOOR_SOURCE_SKILL,
) -> dict:
    """`create_person` through the writer door. Dedup always runs (no
    `skip_dedup` here). Answers `{ok: true, id, canonical_name}`, or `ok:
    false` with `reason` `duplicate` (and `existing_id`), `ambiguous` (and
    `candidates`), `single_word_name` or `out_of_scope`, each with nothing
    written."""
    scope_error = _account_scope_error()
    try:
        record = create_person(
            workspace_root, canonical_name=canonical_name,
            primary_org_id=primary_org_id, email=email, role=role,
            notes=notes, aliases=aliases, affiliation_ids=affiliation_ids,
            project_ids=project_ids, first_seen=first_seen,
            last_interaction=last_interaction,
            needs_enrichment=needs_enrichment, provenance=provenance,
            source_ref=source_ref, account_address=account_address,
            source_skill=source_skill)
    except DuplicatePersonError as exc:
        return {"ok": False, "reason": "duplicate",
                "existing_id": exc.person_id,
                "canonical_name": exc.canonical_name,
                "line": DOOR_DUPLICATE_LINE}
    except MultipleCandidatesError as exc:
        return {"ok": False, "reason": "ambiguous",
                "candidates": _door_candidates(exc.candidates)}
    except SingleWordNameRefused as exc:
        return {"ok": False, "reason": "single_word_name", "line": exc.line}
    except scope_error:
        return {"ok": False, "reason": "out_of_scope",
                "line": DOOR_OUT_OF_SCOPE_LINE}
    return {"ok": True, "id": record.get("id"),
            "canonical_name": record.get("canonical_name")}


def door_update_person(
    workspace_root: str | Path,
    *,
    person_id: str,
    source_skill: str = DOOR_SOURCE_SKILL,
    **fields: Any,
) -> dict:
    """`update_person` through the writer door. Answers `{ok: true, id,
    record}`, or `ok: false` with `reason` `identity_field`,
    `unknown_person` or `out_of_scope`, each with nothing written. A field
    the schema refuses still raises, as today.

    `id` is the record's identity and `suppress_lineage` hides a role or
    company move from the history; neither is a change a chat can carry
    (REVIEW_W2_PEOPLEWRITE2 N-1: `id` passed through `update_person`, which
    has taken it since the base, left two records with one id and another
    person gone from the book)."""
    if DOOR_UPDATE_REFUSED_KEYS & set(fields):
        return {"ok": False, "reason": "identity_field",
                "line": DOOR_IDENTITY_FIELD_LINE}
    if not _person_on_file(workspace_root, person_id):
        return {"ok": False, "reason": "unknown_person",
                "line": DOOR_UNKNOWN_PERSON_LINE}
    scope_error = _account_scope_error()
    try:
        record = update_person(workspace_root, person_id,
                               source_skill=source_skill, **fields)
    except scope_error:
        return {"ok": False, "reason": "out_of_scope",
                "line": DOOR_OUT_OF_SCOPE_LINE}
    return {"ok": True, "id": person_id, "record": record}


def door_record_person_fact(
    workspace_root: str | Path,
    *,
    person_id: str,
    fact: str,
    source_ref: str,
    category: str | None = None,
    source_skill: str = DOOR_SOURCE_SKILL,
) -> dict:
    """`record_person_fact` through the writer door: one `person_fact_observed`
    row. Answers `{ok: true, person_id, canonical_name, fact, category}`, or
    `{ok: false, reason: "unknown_person"}` with nothing written."""
    if not _person_on_file(workspace_root, person_id):
        return {"ok": False, "reason": "unknown_person",
                "line": DOOR_UNKNOWN_PERSON_LINE}
    event = record_person_fact(workspace_root, person_id, fact, source_ref,
                               category=category, source_skill=source_skill)
    data = event.get("data") or {}
    return {"ok": True, "person_id": person_id,
            "canonical_name": data.get("canonical_name"),
            "fact": data.get("fact"), "category": data.get("category")}


def door_auto_add_person(
    workspace_root: str | Path,
    *,
    canonical_name: str,
    email: str | None = None,
    email_provenance: dict | str | None = None,
    role: str | None = None,
    primary_org_id: str | None = None,
    provenance: dict | None = None,
    source_ref: str | None = None,
    account_address: str | None = None,
    source_skill: str = DOOR_SOURCE_SKILL,
) -> dict:
    """`auto_add_person` through the writer door: the function's own status
    dict (`added` or `needs_confirm`) with `ok: true`, or `ok: false` with
    the create refusals `door_create_person` names, nothing written."""
    scope_error = _account_scope_error()
    extra = {k: v for k, v in (("role", role),
                               ("primary_org_id", primary_org_id),
                               ("provenance", provenance),
                               ("source_ref", source_ref),
                               ("account_address", account_address))
             if v is not None}
    try:
        answer = auto_add_person(workspace_root, canonical_name=canonical_name,
                                 email=email, email_provenance=email_provenance,
                                 source_skill=source_skill, **extra)
    except DuplicatePersonError as exc:
        return {"ok": False, "reason": "duplicate",
                "existing_id": exc.person_id,
                "canonical_name": exc.canonical_name,
                "line": DOOR_DUPLICATE_LINE}
    except MultipleCandidatesError as exc:
        return {"ok": False, "reason": "ambiguous",
                "candidates": _door_candidates(exc.candidates)}
    except SingleWordNameRefused as exc:
        return {"ok": False, "reason": "single_word_name", "line": exc.line}
    except scope_error:
        return {"ok": False, "reason": "out_of_scope",
                "line": DOOR_OUT_OF_SCOPE_LINE}
    return {"ok": True, **answer}


# ---------- CLI ----------

def _parse_kv(items: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"bad key=value arg: {item!r}")
        k, v = item.split("=", 1)
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Canonical writer for person records in entities.json."
    )
    parser.add_argument("--workspace", required=True, help="Workspace root.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_find = sub.add_parser("find", help="Look up an existing person.")
    p_find.add_argument("--name")
    p_find.add_argument("--email")
    p_find.add_argument("--alias", action="append", default=[])

    p_create = sub.add_parser("create", help="Create a new person record.")
    p_create.add_argument("--canonical-name", required=True)
    p_create.add_argument("--primary-org-id")
    p_create.add_argument("--email")
    p_create.add_argument("--role")
    p_create.add_argument("--notes")
    p_create.add_argument("--alias", action="append", default=[])
    p_create.add_argument("--first-seen")
    p_create.add_argument("--source-skill", default="people_writer-cli")
    p_create.add_argument("--skip-dedup", action="store_true")

    p_update = sub.add_parser("update", help="Update fields on an existing person.")
    p_update.add_argument("--id", required=True)
    p_update.add_argument("--field", action="append", default=[],
                          help="key=value, repeatable. JSON values supported.")
    p_update.add_argument("--source-skill", default="people_writer-cli")

    p_merge = sub.add_parser("merge", help="Merge duplicate into keeper.")
    p_merge.add_argument("--keep-id", required=True)
    p_merge.add_argument("--duplicate-id", required=True)
    p_merge.add_argument("--source-skill", default="people_writer-cli")

    p_repair = sub.add_parser("repair", help="Repair a malformed record in place.")
    p_repair.add_argument("--id", required=True)
    p_repair.add_argument("--rename", action="append", default=[],
                          help="old_key=new_key, repeatable.")
    p_repair.add_argument("--drop", action="append", default=[],
                          help="field name, repeatable.")
    p_repair.add_argument("--set", dest="setters", action="append", default=[],
                          help="key=value, repeatable. JSON values supported.")
    p_repair.add_argument("--source-skill", default="people_writer-cli")

    args = parser.parse_args()
    ws = Path(args.workspace)

    if args.cmd == "find":
        result = find_existing_person(ws, name=args.name, email=args.email, aliases=args.alias)
        print(json.dumps(result, indent=2) if result else "null")
        return 0 if result else 1

    if args.cmd == "create":
        try:
            record = create_person(
                ws,
                canonical_name=args.canonical_name,
                primary_org_id=args.primary_org_id,
                email=args.email,
                role=args.role,
                notes=args.notes,
                aliases=args.alias or None,
                first_seen=args.first_seen,
                source_skill=args.source_skill,
                skip_dedup=args.skip_dedup,
            )
        except DuplicatePersonError as e:
            print(f"DUPLICATE {e.person_id} {e.canonical_name}", file=sys.stderr)
            return 2
        print(json.dumps(record, indent=2))
        return 0

    if args.cmd == "update":
        record = update_person(ws, args.id,
                               source_skill=args.source_skill,
                               **_parse_kv(args.field))
        print(json.dumps(record, indent=2))
        return 0

    if args.cmd == "merge":
        record = merge_person_into(ws,
                                   keep_id=args.keep_id,
                                   duplicate_id=args.duplicate_id,
                                   source_skill=args.source_skill)
        print(json.dumps(record, indent=2))
        return 0

    if args.cmd == "repair":
        renames = dict(item.split("=", 1) for item in args.rename) if args.rename else None
        record = repair_person(ws, args.id,
                               field_renames=renames,
                               drop_fields=args.drop or None,
                               set_fields=_parse_kv(args.setters) or None,
                               source_skill=args.source_skill)
        print(json.dumps(record, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
