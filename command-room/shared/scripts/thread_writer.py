#!/usr/bin/env python3
"""Canonical typed writer for threads/projects — the entity type that had NO
safe writer (deep-audit 2026-05-29, finding #6). Threads are the spine of the
workspace (every event carries a thread id; folders, briefings, the Orgs Map
all hang off them) yet were hand-rolled from LLM prose with a racy max+1 id and
no schema validation — the exact bug class org_writer/people_writer were built
to kill, left open for the most-written type.

Mirrors org_writer: ALLOWED/REQUIRED field maps, schema validation, dedup,
atomic locked write, and a canonical `thread_created` event. Routes ALL
collection access through `entities_collection` (the wrapper-aware floor) so a
new thread lands where the readers look on both nested and flat workspaces.

Schema-reality notes baked in (verified against the live substrate):
  - real threads carry `canonical_name` (schema's project def names only
    `display_name`) — both are allowed;
  - real statuses include `scoping` / `resolved` / `exploring` beyond the
    schema enum — VALID_STATUSES is the observed superset (the schema enum was
    widened to match in the same change that added this note);
  - `stage` is an integer per schema but legacy ingest parsers wrote it as a
    string — non-int stage is coerced to None;
  - `roster_overrides` (brain-substrate fix) is allowed.

Two field buckets, not one (2026-07-25 — the P0/P1 second-eyes review found
21 of 32 live threads REJECTED by _validate_thread, so `update_thread` raised
on two thirds of the real workspace; the shared workspace_mini fixture failed
4 of 4 for the same reason):

  ALLOWED_THREAD_FIELDS — canonical and writable. A field earns a place here
  by having a live reader (`notes` and `key_contact_id` are read by
  build_workspace_map_input / build_dcc_input) or by being the pair-mate of an
  allowed lifecycle field (`paused_at`/`paused_by` mirror
  `archived_at`/`archive_reason`; `paused` is a valid status).

  LEGACY_THREAD_FIELDS — present on pre-writer records, TOLERATED so a record
  round-trips without loss, never newly written (update_thread rejects them as
  explicit kwargs). These have no thread-side reader and do not belong on a
  thread, but deleting them on an unrelated update is data loss, not cleanup.
  That is not hypothetical: `created_at`/`created_by` used to sit in
  FORBIDDEN_THREAD_FIELDS, whose hint says "track via thread_created event" —
  but THREE of the four live threads carrying them (`project_020`, `_021`,
  `_026`) have NO thread_created event, so _coerce's silent drop destroyed
  their only creation provenance.

  `project_019` is the fourth, and it DOES have one — which the first survey
  missed. Reusable reason: pre-writer thread_created rows carry the thread id
  at TOP LEVEL (`primary_thread_id`, with `data.thread_id` alongside), not
  under `data.primary_thread_id` the way _log_event writes it today. Survey
  BOTH shapes; 1 of the 10 live thread_created rows uses the top-level form,
  so a `data.primary_thread_id`-only scan reports zero coverage for it.

The one rule both buckets serve: an unknown key still REJECTS loudly.
_coerce deliberately does not drop unknown keys — a loud, fixable failure
beats silently deleting a field two projectors read.

stdlib only.
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Any

from atomic_write import atomic_write_json_locked, atomic_append_jsonl  # noqa: E402
from entities_io import entities_collection  # noqa: E402

THREAD_ID_RE = re.compile(r"^project_[a-z0-9_]+$")

ALLOWED_THREAD_FIELDS = {
    "id", "canonical_name", "display_name", "folder_name", "status", "stage",
    "org_id", "parent_thread_id", "spawned_from_thread_id",
    "cross_refs", "kind", "project_class", "owner_person_id",
    "stakeholder_person_ids", "last_activity", "next_step", "success_criteria",
    "first_seen", "session_count", "dormancy_reviewed_at", "archived_at",
    "archive_reason", "roster_overrides", "deal", "objective", "cohort",
    # Live-read on threads (build_workspace_map_input._key_contact / the
    # project notes line; build_dcc_input's next_action fallback). These were
    # on 21 of 32 live threads while being rejected — the write path was dead
    # for two thirds of the workspace.
    "key_contact_id", "notes",
    # Pause provenance — the pair-mate of archived_at/archive_reason for the
    # `paused` status. User-authored ("who paused this, when"); no reader yet.
    "paused_at", "paused_by",
}
REQUIRED_THREAD_FIELDS = {"id", "status"}

# Tolerated on records that already carry them, NEVER newly written (see the
# module docstring). Value = why it is not canonical, surfaced when a caller
# tries to write one. Do not add to this map to make a new field "work" — add
# a canonical field to ALLOWED_THREAD_FIELDS (and the schema) instead.
LEGACY_THREAD_FIELDS = {
    "affiliation_id": (
        "(ENTITY1: `org_id` is the canonical thread→org field — the majority "
        "spelling in the fleet and the one the people-side writers use. "
        "affiliation_id is tolerated on records that already carry it and "
        "still read as an alias everywhere, but new writes land org_id; "
        "create_thread(affiliation_id=...) normalises to org_id for you)"),
    "is_primary_focus": (
        "(an ORG field — every reader takes it off an org record, never a "
        "thread; the live values are inconsistent across sibling threads of "
        "one org. Bubble a thread via its org, not a thread-side copy)"),
    "last_writer": (
        "(that's the entities.json ENVELOPE field set by _save_entities — it "
        "leaked onto one thread record from a prose write; not a thread field)"),
    "created_at": (
        "(creation provenance belongs in the thread_created event — but three "
        "of the four live records carrying this have no such event, so it is "
        "preserved rather than dropped. New threads get the event instead)"),
    "created_by": (
        "(same as created_at — preserved as the only provenance those three "
        "records have; new threads get a thread_created event instead)"),
    "aliases": (
        "(threads have no alias resolver — entity_resolve reads person.aliases "
        "and org.aliases only, and matches threads on canonical_name. Present "
        "on the workspace_mini fixture; absent from every live thread)"),
}

# --- Deal object (SPEC PIPE1) — allowed ONLY on kind="deal" threads. --------
# Single source for the enums; deal_state.py (the only writer of deal.* fields)
# imports these. entities.schema.json $defs.project.deal mirrors them.
DEAL_STAGES = ("lead", "qualified", "proposal_sent", "negotiating")
DEAL_LOSS_REASONS = (
    "no_decision", "price", "competitor", "diy", "timing", "bad_fit", "other",
)
DEAL_OUTCOMES = ("won", "lost")
ALLOWED_DEAL_FIELDS = {
    "value", "currency", "stage", "stage_entered", "expected_close",
    "forecast_category", "source", "outcome", "loss_reason", "loss_note",
    "opened_at", "closed_at",
}
DEAL_FORECAST_CATEGORIES = ("commit", "best_case", "pipeline")

# --- Objective object (SPEC OBJ1, DRAFT) — allowed ONLY on kind="objective"
# threads. Single source for the enums; objective_state.py (the only writer of
# objective.* fields) imports these. entities.schema.json
# $defs.project.objective mirrors them. Directional status is NOT here — it is
# never stored on the entity (derived from events by objective_math.py).
OBJECTIVE_BINDING_TYPES = ("meeting", "self", "activity")
OBJECTIVE_SERIES_MATCH = ("title_and_people", "title_only")
OBJECTIVE_OUTCOMES = ("completed", "archived")
ALLOWED_OBJECTIVE_FIELDS = {
    "statement", "horizon", "binding", "anchor_thread_id", "milestones",
    "outcome", "outcome_note", "opened_at", "closed_at",
}
ALLOWED_OBJECTIVE_BINDING_FIELDS = {
    "type", "series_key", "series_match", "series_people", "cadence_days",
    "entity_ids", "target_note",
}

# --- Cohort object (SPEC COACH1 §4.2) — allowed ONLY on kind="cohort"
# threads. Single source for the enums; the coach pack's writers import these.
# entities.schema.json $defs.project.cohort mirrors them. NOTHING derived lives
# here: session counts, arc items, billable tallies and renewal windows are
# computed by shared/scripts/coach_state.py from events, never stamped.
COHORT_CADENCES = ("monthly", "biweekly", "quarterly", "other")
COHORT_MEMBER_STATUSES = ("active", "paused", "departed")
BILLING_TARGET_KINDS = ("person", "org")
BILLING_UNITS = ("session", "hour", "retainer")
# M ruling 7: draft-then-send is the only legal posture in v1. Single-valued on
# purpose — a batch/auto-send option can only appear via a deliberate change.
INVOICE_POSTURES = ("draft_then_send",)
ALLOWED_COHORT_FIELDS = {
    "cadence", "seat_count", "members", "materials_thread_id",
    "term_end", "renewal_date", "billing",
}
ALLOWED_COHORT_MEMBER_FIELDS = {
    "person_id", "joined_at", "status", "departed_at", "also_1to1",
    "billing_target",
}
ALLOWED_COHORT_BILLING_FIELDS = {
    "payer", "unit", "rate", "currency", "consolidate_by_payer",
}

# Observed superset (schema enum is missing exploring/scoping/resolved).
VALID_STATUSES = {
    "active", "dormant", "paused", "blocked", "archived",
    "exploring", "scoping", "resolved",
}

# ENTITY1 §4a: a project belongs to an org, and "deliberately unaffiliated"
# must stay distinguishable from "never set". The sentinel is the SAME one the
# integrity checker and the entities schema already treat as legitimate
# (`personal`, per PR_honest1-residuals §"Not done here") — do not invent a
# second convention.
UNAFFILIATED_ORG_ID = "personal"


class DuplicateDealError(ValueError):
    """A second non-archived `kind: deal` thread under the same org (ENTITY1
    §4c). The writer proposes a merge instead of writing a twin — `existing`
    carries the record already holding the engagement. `cleanup` handles the
    after-the-fact case with an archive_reason; this is the write-time half of
    the same notion of "same engagement" (same org, live deal)."""

    def __init__(self, existing: dict, proposed_name: str):
        self.existing = existing
        super().__init__(
            f"a non-archived deal already exists under org "
            f"{thread_org_id(existing)!r}: {existing.get('id')} "
            f"({existing.get('canonical_name') or existing.get('display_name')}). "
            f"Proposing a merge instead of writing {proposed_name!r} as a twin — "
            f"fold this into the existing thread (update_thread / deal_state), "
            f"or archive it first if the old engagement genuinely ended. "
            f"skip_dedup=True overrides when these really are two engagements."
        )


def thread_org_id(t: dict) -> str | None:
    """Canonical read-side thread→org resolution (ENTITY1 §4b): `org_id`
    first, then the legacy `affiliation_id` alias still present on records
    that predate the collapse."""
    return t.get("org_id") or t.get("affiliation_id")

# Legacy → canonical guidance surfaced in validation errors. These are dropped
# by _coerce: each one is a MISNAMING of a field that lives elsewhere, so the
# value carries no information the canonical field doesn't already hold.
# (`created_at`/`created_by` were here until 2026-07-25 and are now
# LEGACY_THREAD_FIELDS — they are real provenance with no event behind them,
# so dropping them destroyed data. Misnamings get dropped; orphan facts don't.)
FORBIDDEN_THREAD_FIELDS = {
    "name": "(use 'canonical_name')",
    "primary_project_id": "(that's an EVENT field — use 'parent_thread_id' on a thread)",
    "members": "(do NOT store membership — derive it via thread_roster.derive_roster)",
}


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


def _now_iso() -> str:
    return _clock_now().isoformat(timespec="seconds")


def _today() -> str:
    return _clock_now().astimezone().date().isoformat()


def _entities_path(ws: Path) -> Path:
    return Path(ws) / "_hq" / "data" / "entities.json"


def _events_path(ws: Path) -> Path:
    return Path(ws) / "_hq" / "data" / "events.jsonl"


def _load_entities(ws: Path) -> dict:
    return json.loads(_entities_path(ws).read_text(encoding="utf-8"))


def _save_entities(ws: Path, data: dict, source_skill: str) -> None:
    data["version"] = int(data.get("version", 0)) + 1
    data["last_updated"] = _now_iso()
    data["last_writer"] = source_skill
    atomic_write_json_locked(_entities_path(ws), data, holder=source_skill)


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (s or "").lower().strip())
    return re.sub(r"_+", "_", s).strip("_") or "thread"


# Folders that are never *guessed* as a project thread. Mirrors
# integrity_check._NON_PROJECT_FOLDERS and its C10/C11 skip rules.
#
# SCOPE — this set answers ONE question: "when nothing was named, may this
# folder be INVENTED as a candidate?" It does NOT answer "may a thread bind
# here?" Those are different questions and conflating them is a real defect:
# `integrity_check`'s list exists to decide whether an *unregistered* folder is
# an orphan, and M's substrate already contains the counterexamples — several
# live threads are registered against `Command Room` today. So this set (and
# the depth cap below) gate the GUESSING legs of `resolve_folder_name` only.
# An explicit `folder_name` is checked against disk and nothing else.
_NON_PROJECT_FOLDERS = {
    "_hq", "_archive", "_people", "_exploring", "_unrouted",
    "Command Room",  # the product's own collateral folder
}

# How deep to GUESS a project folder. 1 = workspace root, 2 = one level of
# nesting (`Parent/Child`). This is a bound on invention, not on what exists:
# live records bind at depth 3 (`_hq/dormant/<name>`), and the explicit leg
# resolves at any depth because it walks the path it was handed.
_FOLDER_SEARCH_DEPTH = 2


def _candidate_folders(workspace_root: Path, depth: int = _FOLDER_SEARCH_DEPTH) -> list[str]:
    """Project folders as workspace-relative POSIX paths, root first then nested.

    Nested folders are in scope because `folder_name` is already a relative path
    in real records, not just a root-level basename (spec H: "the counterpart
    search must cover nested project folders").
    """
    out: list[str] = []

    def walk(d: Path, prefix: str, level: int) -> None:
        if level > depth:
            return
        try:
            children = sorted(d.iterdir())
        except OSError:
            return
        for c in children:
            if not c.is_dir() or c.name.startswith("."):
                continue
            if c.name in _NON_PROJECT_FOLDERS or c.name.startswith("_"):
                continue
            rel = f"{prefix}{c.name}"
            out.append(rel)
            walk(c, rel + "/", level + 1)

    walk(workspace_root, "", 1)
    return out


def _fold(s: str) -> str:
    """Case-fold a folder path for comparison, normalizing the separator only.

    The mount is case-insensitive (spec H3), so the compare must be too — a
    case-sensitive compare writes `null` for a folder that is really there
    (slug `roncroft` vs on-disk `Roncroft`). It must NOT go further and fold
    separators/punctuation away: `acme_widgets` and `Acme Widgets` are two
    genuinely different directories, and treating them as one is the opposite
    error — it revives the guess this function exists to kill.
    """
    return (s or "").strip().replace("\\", "/").strip("/").lower()


def _resolve_on_disk(workspace_root: Path, rel: str) -> str | None:
    """Walk `rel` segment by segment under the workspace root, recovering casing.

    Answers only "is this a real directory?" — no exclusion set, no depth cap.
    Returns the workspace-relative POSIX path as the filesystem actually spells
    it (so `command room` comes back as `Command Room`), or `None` if any
    segment is not a real directory.

    Each segment is compared through `_fold`, so the H3 contract holds here too:
    case folds, separators do not.
    """
    raw = (rel or "").strip().replace("\\", "/").strip("/")
    if not raw:
        return None
    parts = raw.split("/")
    # '.' / '..' are navigation, not folder names — a caller that hands us one
    # is not naming a folder, and honoring it would let a record bind outside
    # (or above) the workspace root.
    if any(p in ("", ".", "..") for p in parts):
        return None

    cur = workspace_root
    out: list[str] = []
    for want in parts:
        target = _fold(want)
        hit = None
        try:
            children = sorted(cur.iterdir())
        except OSError:
            return None
        for c in children:
            if c.is_dir() and _fold(c.name) == target:
                hit = c
                break
        if hit is None:
            return None
        out.append(hit.name)
        cur = hit
    return "/".join(out)


def resolve_folder_name(workspace_root: str | Path,
                        canonical_name: str,
                        folder_name: str | None = None) -> str | None:
    """Resolve a thread's `folder_name` against directories that actually exist.

    Returns the **real** directory path (original casing preserved) or `None`.
    Never guesses: `None` is an accepted schema value and an honest one, whereas
    a slug guess is strictly worse than nothing because it looks valid to every
    reader and, before FOLDERGUARD, got fabricated into a real folder on the
    next cleanup sweep.

    Two legs, and they are not the same question:

    * **Explicit** — the caller named a folder. Resolve it against disk and
      nothing else: any depth, no `_NON_PROJECT_FOLDERS` filter. Those
      exclusions bound what may be *invented*; applying them here refuses
      folders that exist and that live threads are already bound to (several
      bind `Command Room`, three bind under `_hq/`, two of those at depth 3).
      If it does not resolve, the answer is `None` — never a different folder.
      Falling through would silently swap a typo'd argument for some other real
      directory, producing a record that is disk-valid and semantically wrong.
    * **Guessing** — nothing was named, so a candidate is being invented from
      the canonical name and then its slug. Invention is exactly where the
      exclusion set and the depth cap belong.

    Every compare is case-folded (H3).
    """
    workspace_root = Path(workspace_root)

    # Any non-empty string counts as "the caller named a folder", including a
    # garbage one — whitespace-only and `/` are given-and-wrong, not unset, and
    # falling through on them is the same silent swap as a typo. `None` and `""`
    # are the two ways every caller says "no folder", so those guess.
    if folder_name:
        return _resolve_on_disk(workspace_root, folder_name)

    existing = _candidate_folders(workspace_root)
    by_fold = {_fold(p): p for p in existing}
    for candidate in (canonical_name, _slugify(canonical_name)):
        if not candidate:
            continue
        hit = by_fold.get(_fold(candidate))
        if hit is not None:
            return hit
    return None


def _threads(data: dict) -> list:
    """Live thread collection. Real data stores under `threads`; the legacy
    schema also names it `projects`. Prefer the one that already has rows."""
    threads = entities_collection(data, "threads")
    projects = entities_collection(data, "projects")
    if projects and not threads:
        return projects
    return threads


def _next_project_id(threads: list) -> str:
    max_n = 0
    for t in threads:
        m = re.match(r"^project_(\d{3,})$", t.get("id", ""))
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"project_{max_n + 1:03d}"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _validate_thread(record: dict) -> None:
    extras = set(record) - ALLOWED_THREAD_FIELDS - set(LEGACY_THREAD_FIELDS)
    if extras:
        msgs = []
        for k in sorted(extras):
            hint = FORBIDDEN_THREAD_FIELDS.get(k, "(not in schema)")
            msgs.append(f"  - {k!r} → {hint}")
        raise ValueError(
            "thread record has fields not allowed by the schema. If you "
            "genuinely need a new field, update entities.schema.json $defs.project "
            "AND ALLOWED_THREAD_FIELDS first.\n" + "\n".join(msgs))
    missing = REQUIRED_THREAD_FIELDS - set(record)
    if missing:
        raise ValueError(f"thread record missing required fields: {sorted(missing)}")
    if not THREAD_ID_RE.match(record["id"]):
        raise ValueError(f"id must match ^project_[a-z0-9_]+$, got: {record['id']!r}")
    if record.get("status") not in VALID_STATUSES:
        raise ValueError(
            f"status must be one of {sorted(VALID_STATUSES)}, got: {record.get('status')!r}")
    stage = record.get("stage")
    if stage is not None and (not isinstance(stage, int) or isinstance(stage, bool)):
        raise ValueError(f"stage must be an integer or null, got: {stage!r}")
    if "deal" in record:
        _validate_deal(record)
    if "objective" in record:
        _validate_objective(record)
    if "cohort" in record:
        _validate_cohort(record)


def _validate_deal(record: dict) -> None:
    """SPEC PIPE1 — the deal object is allowed ONLY on kind='deal' threads,
    with enum-validated sales fields. Guidance style mirrors
    FORBIDDEN_THREAD_FIELDS: name the fix, not just the failure. Write deal.*
    ONLY through shared/scripts/deal_state.py (the single writer/closure
    path); this validation is the schema floor beneath it."""
    deal = record["deal"]
    if deal is None:
        raise ValueError("deal must be an object, not null — omit the field entirely")
    if not isinstance(deal, dict):
        raise ValueError(f"deal must be an object, got: {type(deal).__name__}")
    if record.get("kind") != "deal":
        raise ValueError(
            "a deal object is only allowed on kind='deal' threads "
            f"(this thread's kind: {record.get('kind')!r}). Set kind='deal' "
            "or drop the deal object.")
    extras = set(deal) - ALLOWED_DEAL_FIELDS
    if extras:
        raise ValueError(
            "deal object has fields not in the schema: "
            f"{sorted(extras)}. Allowed: {sorted(ALLOWED_DEAL_FIELDS)}. "
            "Update entities.schema.json $defs.project.deal AND "
            "ALLOWED_DEAL_FIELDS first if you genuinely need a new field.")
    stage = deal.get("stage")
    if stage is not None and stage not in DEAL_STAGES:
        raise ValueError(
            f"deal.stage must be one of {list(DEAL_STAGES)}, got: {stage!r}. "
            "Won/lost are the terminal deal.outcome, not stages; the integer "
            "project-lifecycle stage is a different field.")
    outcome = deal.get("outcome")
    if outcome is not None and outcome not in DEAL_OUTCOMES:
        raise ValueError(
            f"deal.outcome must be won, lost, or null, got: {outcome!r} "
            "(set only via deal_state.close_deal)")
    reason = deal.get("loss_reason")
    if reason is not None and reason not in DEAL_LOSS_REASONS:
        raise ValueError(
            f"deal.loss_reason must be one of {list(DEAL_LOSS_REASONS)}, got: {reason!r}")
    if outcome == "lost" and reason is None:
        raise ValueError(
            "deal.outcome='lost' requires a loss_reason "
            f"(one of {list(DEAL_LOSS_REASONS)}) — close via "
            "deal_state.close_deal, which enforces this")
    fc = deal.get("forecast_category")
    if fc is not None and fc not in DEAL_FORECAST_CATEGORIES:
        raise ValueError(
            f"deal.forecast_category must be one of {list(DEAL_FORECAST_CATEGORIES)} "
            f"or null, got: {fc!r}")
    value = deal.get("value")
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
        raise ValueError(
            f"deal.value must be a number or null, got: {value!r} — never a "
            "string or range; store null + a note in deal.source for ranges")


def _validate_objective(record: dict) -> None:
    """SPEC OBJ1 (DRAFT) — the objective object is allowed ONLY on
    kind='objective' threads, with an enum-validated tracking binding.
    Guidance style mirrors _validate_deal: name the fix, not just the
    failure. Write objective.* ONLY through shared/scripts/objective_state.py
    (the single writer/closure path); this validation is the schema floor
    beneath it. Directional status is deliberately NOT a field here —
    it derives from events (objective_math.py), never stored."""
    obj = record["objective"]
    if obj is None:
        raise ValueError("objective must be an object, not null — omit the field entirely")
    if not isinstance(obj, dict):
        raise ValueError(f"objective must be an object, got: {type(obj).__name__}")
    if record.get("kind") != "objective":
        raise ValueError(
            "an objective object is only allowed on kind='objective' threads "
            f"(this thread's kind: {record.get('kind')!r}). Set kind='objective' "
            "or drop the objective object.")
    extras = set(obj) - ALLOWED_OBJECTIVE_FIELDS
    if extras:
        raise ValueError(
            "objective object has fields not in the schema: "
            f"{sorted(extras)}. Allowed: {sorted(ALLOWED_OBJECTIVE_FIELDS)}. "
            "Update entities.schema.json $defs.project.objective AND "
            "ALLOWED_OBJECTIVE_FIELDS first if you genuinely need a new field. "
            "(A 'status' field here is the one bug class this guard exists "
            "for — status derives from events, never stored.)")
    statement = obj.get("statement")
    if not isinstance(statement, str) or not statement.strip():
        raise ValueError(
            "objective.statement must be a non-empty string — the objective "
            "in the CEO's own words")
    binding = obj.get("binding")
    if not isinstance(binding, dict):
        raise ValueError(
            "objective.binding must be an object with a 'type' — the CEO's "
            "three-way tracking choice (meeting | self | activity)")
    b_extras = set(binding) - ALLOWED_OBJECTIVE_BINDING_FIELDS
    if b_extras:
        raise ValueError(
            "objective.binding has fields not in the schema: "
            f"{sorted(b_extras)}. Allowed: "
            f"{sorted(ALLOWED_OBJECTIVE_BINDING_FIELDS)}.")
    b_type = binding.get("type")
    if b_type not in OBJECTIVE_BINDING_TYPES:
        raise ValueError(
            f"objective.binding.type must be one of "
            f"{list(OBJECTIVE_BINDING_TYPES)}, got: {b_type!r}")
    if b_type == "meeting":
        if not isinstance(binding.get("series_key"), str) or not binding["series_key"].strip():
            raise ValueError(
                "a meeting binding requires a non-empty series_key (the "
                "normalized recurring-meeting title) — propose it from the "
                "meeting history, confirm with the user, never leave it blank")
        sm = binding.get("series_match")
        if sm is not None and sm not in OBJECTIVE_SERIES_MATCH:
            raise ValueError(
                f"objective.binding.series_match must be one of "
                f"{list(OBJECTIVE_SERIES_MATCH)} or null (null = "
                f"title_and_people default), got: {sm!r}")
    elif b_type == "self":
        cadence = binding.get("cadence_days")
        if cadence is not None and (isinstance(cadence, bool)
                                    or not isinstance(cadence, int) or cadence < 1):
            raise ValueError(
                f"objective.binding.cadence_days must be a positive integer "
                f"or null (null = weekly default), got: {cadence!r}")
    elif b_type == "activity":
        ids = binding.get("entity_ids")
        if not isinstance(ids, list) or not ids or not all(
                isinstance(i, str) and i for i in ids):
            raise ValueError(
                "an activity binding requires a non-empty entity_ids list of "
                "thread/deal ids — topic over party: bind to a thread or "
                "deal, never a bare person")
    anchor = obj.get("anchor_thread_id")
    if anchor is not None and (not isinstance(anchor, str) or not anchor.strip()):
        raise ValueError(
            f"objective.anchor_thread_id must be a thread id string or null, "
            f"got: {anchor!r}")
    outcome = obj.get("outcome")
    if outcome is not None and outcome not in OBJECTIVE_OUTCOMES:
        raise ValueError(
            f"objective.outcome must be one of {list(OBJECTIVE_OUTCOMES)} or "
            f"null, got: {outcome!r} (set only via objective_state."
            "complete_objective / archive_objective)")
    milestones = obj.get("milestones")
    if milestones is not None:
        if not isinstance(milestones, list):
            raise ValueError(
                f"objective.milestones must be a list, got: {type(milestones).__name__}")
        for m in milestones:
            if (not isinstance(m, dict) or not isinstance(m.get("title"), str)
                    or not m["title"].strip()
                    or set(m) - {"title", "done"}
                    or not isinstance(m.get("done", False), bool)):
                raise ValueError(
                    "each objective milestone must be {title: non-empty "
                    f"string, done?: bool}} — got: {m!r}")


def _validate_billing_target(target, where: str) -> None:
    """SPEC COACH1 §8.1 — a payer is a person OR an org, and an id-less payer
    routes an invoice nowhere. Null is legal and MEANINGFUL at the seat level
    ('bill this seat to the cohort's own payer'), so callers pass null through
    rather than omitting the key."""
    if target is None:
        return
    if not isinstance(target, dict):
        raise ValueError(
            f"{where} must be an object or null, got: {type(target).__name__}")
    extras = set(target) - {"kind", "id"}
    if extras:
        raise ValueError(
            f"{where} has fields not in the schema: {sorted(extras)}. "
            "Allowed: ['id', 'kind'].")
    kind = target.get("kind")
    if kind not in BILLING_TARGET_KINDS:
        raise ValueError(
            f"{where}.kind must be one of {list(BILLING_TARGET_KINDS)}, "
            f"got: {kind!r}")
    tid = target.get("id")
    if not isinstance(tid, str) or not tid.strip():
        raise ValueError(
            f"{where}.id must be a non-empty {kind}_id — an id-less payer "
            "bills nobody. Resolve the payer before writing.")


def _validate_cohort(record: dict) -> None:
    """SPEC COACH1 §4.2 — the cohort object is allowed ONLY on kind='cohort'
    threads, with an enum-validated roster. Guidance style mirrors
    _validate_deal / _validate_objective: name the fix, not just the failure.
    Write cohort.* ONLY through the coach pack's typed writer; this validation
    is the schema floor beneath it.

    The roster rules exist because of a real live defect (§4.2): two cohorts
    with overlapping member sets got crossed in prep. Duplicate person_ids
    inside one roster, and a departure with no date, are the two shapes that
    make a roster un-resolvable — both reject here.

    Derived numbers are deliberately NOT fields: session counts, arc items,
    billable tallies and renewal windows all come from coach_state.py. A
    stored `session_count` or `open_items` here would be the `last_activity`
    bug class all over again.
    """
    coh = record["cohort"]
    if coh is None:
        raise ValueError("cohort must be an object, not null — omit the field entirely")
    if not isinstance(coh, dict):
        raise ValueError(f"cohort must be an object, got: {type(coh).__name__}")
    if record.get("kind") != "cohort":
        raise ValueError(
            "a cohort object is only allowed on kind='cohort' threads "
            f"(this thread's kind: {record.get('kind')!r}). Set kind='cohort' "
            "or drop the cohort object. A 1:1 coaching engagement is "
            "kind='coaching' and carries no roster.")
    extras = set(coh) - ALLOWED_COHORT_FIELDS
    if extras:
        raise ValueError(
            "cohort object has fields not in the schema: "
            f"{sorted(extras)}. Allowed: {sorted(ALLOWED_COHORT_FIELDS)}. "
            "Update entities.schema.json $defs.project.cohort AND "
            "ALLOWED_COHORT_FIELDS first if you genuinely need a new field. "
            "(A stored count, tally, or 'last session' field here is the bug "
            "class this guard exists for — those derive from events via "
            "coach_state.py, never stored.)")

    cadence = coh.get("cadence")
    if cadence is not None and cadence not in COHORT_CADENCES:
        raise ValueError(
            f"cohort.cadence must be one of {list(COHORT_CADENCES)} or null, "
            f"got: {cadence!r}")

    seats = coh.get("seat_count")
    if seats is not None and (isinstance(seats, bool)
                              or not isinstance(seats, int) or seats < 0):
        raise ValueError(
            f"cohort.seat_count must be a non-negative integer or null, got: "
            f"{seats!r}. It is the CONTRACTED seat count, not a cached "
            "len(members) — occupancy derives from the roster.")

    members = coh.get("members")
    if members is not None:
        if not isinstance(members, list):
            raise ValueError(
                f"cohort.members must be a list, got: {type(members).__name__}")
        seen: set[str] = set()
        for i, m in enumerate(members):
            at = f"cohort.members[{i}]"
            if not isinstance(m, dict):
                raise ValueError(f"{at} must be an object, got: {type(m).__name__}")
            m_extras = set(m) - ALLOWED_COHORT_MEMBER_FIELDS
            if m_extras:
                raise ValueError(
                    f"{at} has fields not in the schema: {sorted(m_extras)}. "
                    f"Allowed: {sorted(ALLOWED_COHORT_MEMBER_FIELDS)}.")
            pid = m.get("person_id")
            if not isinstance(pid, str) or not pid.strip():
                raise ValueError(
                    f"{at}.person_id must be a non-empty canonical person id — "
                    "resolve the name through entity_resolve before writing "
                    "(a bare name on a roster is the seat that can never be "
                    "billed or briefed).")
            if pid in seen:
                raise ValueError(
                    f"{at}.person_id {pid!r} is already on this roster. One "
                    "seat per person per cohort: a duplicate makes the roster "
                    "un-resolvable, which is the §4.2 crossing defect. A "
                    "person who left and rejoined keeps ONE entry — update "
                    "status/joined_at rather than appending a second.")
            seen.add(pid)
            status = m.get("status", "active")
            if status not in COHORT_MEMBER_STATUSES:
                raise ValueError(
                    f"{at}.status must be one of "
                    f"{list(COHORT_MEMBER_STATUSES)}, got: {status!r}")
            if status == "departed" and not m.get("departed_at"):
                raise ValueError(
                    f"{at} has status='departed' with no departed_at — a "
                    "departure with no date cannot be billed to a period "
                    "boundary or excluded from one. Set departed_at.")
            also = m.get("also_1to1")
            if also is not None and not isinstance(also, bool):
                raise ValueError(
                    f"{at}.also_1to1 must be a boolean or null, got: {also!r}")
            _validate_billing_target(m.get("billing_target"), f"{at}.billing_target")

    billing = coh.get("billing")
    if billing is not None:
        if not isinstance(billing, dict):
            raise ValueError(
                f"cohort.billing must be an object or null, got: "
                f"{type(billing).__name__}")
        b_extras = set(billing) - ALLOWED_COHORT_BILLING_FIELDS
        if b_extras:
            raise ValueError(
                f"cohort.billing has fields not in the schema: "
                f"{sorted(b_extras)}. Allowed: "
                f"{sorted(ALLOWED_COHORT_BILLING_FIELDS)}.")
        _validate_billing_target(billing.get("payer"), "cohort.billing.payer")
        unit = billing.get("unit")
        if unit is not None and unit not in BILLING_UNITS:
            raise ValueError(
                f"cohort.billing.unit must be one of {list(BILLING_UNITS)} or "
                f"null, got: {unit!r}")
        rate = billing.get("rate")
        if rate is not None and (isinstance(rate, bool)
                                 or not isinstance(rate, (int, float))):
            raise ValueError(
                f"cohort.billing.rate must be a number or null, got: {rate!r} "
                "— never a string or a range. Money is user-stated or "
                "user-confirmed only, never estimated.")


def _coerce(record: dict) -> dict:
    """Drop forbidden keys and coerce a legacy string `stage` to None so an
    ingest-parser record lands canonical instead of failing.

    Deliberately does NOT drop unknown keys. Dropping every unrecognized field
    would have "fixed" the 21 rejected live threads by deleting `notes` and
    `key_contact_id` off all of them — two fields the workspace-map and DCC
    projectors read. Unknown keys reject loudly; known-legacy keys are
    tolerated via LEGACY_THREAD_FIELDS. Silence is the one option that isn't
    on the table.
    """
    out = {k: v for k, v in record.items() if k not in FORBIDDEN_THREAD_FIELDS}
    if isinstance(out.get("stage"), str):
        out["stage"] = None
    return out


def _reject_uncanonical_writes(fields: dict) -> None:
    """Guard the explicit-kwarg path of update_thread.

    A field on disk may be tolerated (LEGACY_THREAD_FIELDS) or silently
    coerced away (FORBIDDEN_THREAD_FIELDS); a field a CALLER names in an
    update is a stated intention, and honoring it silently — or dropping it
    silently, which is what the forbidden path did before — hides a real
    caller bug. Both cases raise here instead.
    """
    bad = []
    for k in sorted(fields):
        if k in FORBIDDEN_THREAD_FIELDS:
            bad.append(f"  - {k!r} → {FORBIDDEN_THREAD_FIELDS[k]}")
        elif k in LEGACY_THREAD_FIELDS:
            bad.append(f"  - {k!r} → {LEGACY_THREAD_FIELDS[k]}")
    if bad:
        raise ValueError(
            "update_thread cannot write these fields — they are tolerated on "
            "records that already carry them, never written:\n"
            + "\n".join(bad))


def _log_event(ws: Path, event_type: str, record: dict, source_skill: str) -> None:
    event = {
        "ts": _now_iso(),
        "type": event_type,
        "source_skill": source_skill,
        "data": {
            "primary_thread_id": record.get("id"),
            "canonical_name": record.get("canonical_name") or record.get("display_name"),
            "status": record.get("status"),
        },
    }
    atomic_append_jsonl(_events_path(ws), [event])


def find_existing_thread(workspace_root: str | Path, *,
                         folder_name: str | None = None,
                         canonical_name: str | None = None) -> dict | None:
    """Match by folder_name exact, else canonical/display name (normalized)."""
    data = _load_entities(Path(workspace_root))
    threads = _threads(data)
    if folder_name:
        for t in threads:
            if t.get("folder_name") == folder_name:
                return t
    if canonical_name:
        target = _norm(canonical_name)
        for t in threads:
            if _norm(t.get("canonical_name") or t.get("display_name")) == target:
                return t
    return None


def create_thread(workspace_root: str | Path, *,
                  canonical_name: str,
                  status: str = "active",
                  folder_name: str | None = None,
                  kind: str | None = None,
                  affiliation_id: str | None = None,
                  org_id: str | None = None,
                  owner_person_id: str | None = None,
                  stakeholder_person_ids: list[str] | None = None,
                  parent_thread_id: str | None = None,
                  spawned_from_thread_id: str | None = None,
                  first_seen: str | None = None,
                  thread_id: str | None = None,
                  deal: dict | None = None,
                  objective: dict | None = None,
                  cohort: dict | None = None,
                  source_skill: str = "unknown",
                  skip_dedup: bool = False) -> dict:
    """Create a new thread record. Dedups by folder_name → canonical_name,
    validates against the schema, writes through the wrapper-aware collection
    (so it lands where readers look), and emits a `thread_created` event.

    ENTITY1: `org_id` is required — a project belongs to an org. Pass
    UNAFFILIATED_ORG_ID ("personal") explicitly for a deliberately
    unaffiliated thread; omitting the field is refused so "never set" cannot
    masquerade as a choice. `affiliation_id` is accepted as a legacy alias and
    normalised to `org_id` on write.
    """
    workspace_root = Path(workspace_root)

    # ENTITY1 §4b: one relationship, one field name.
    if affiliation_id and org_id and affiliation_id != org_id:
        raise ValueError(
            f"affiliation_id={affiliation_id!r} and org_id={org_id!r} name "
            f"different orgs for one thread — affiliation_id is an alias of "
            f"org_id; pass org_id alone.")
    org_id = org_id or affiliation_id

    # ENTITY1 §4a: refuse creation with no org reference.
    if not org_id:
        raise ValueError(
            "create_thread requires org_id — a project belongs to an org. "
            "Pass the owning org's id, or UNAFFILIATED_ORG_ID ('personal') "
            "explicitly if this thread is deliberately unaffiliated.")

    if not skip_dedup:
        existing = find_existing_thread(workspace_root, folder_name=folder_name,
                                        canonical_name=canonical_name)
        if existing is not None:
            raise ValueError(
                f"thread already exists: {existing.get('id')} "
                f"({existing.get('canonical_name') or existing.get('display_name')})")

    data = _load_entities(workspace_root)
    threads = _threads(data)

    # ENTITY1 §4c: a second OPEN deal under one org is a twin engagement —
    # propose a merge, don't write it. Closed deals (won/lost — outcome set,
    # thread resolved/archived) never block: a new deal after a closed one is
    # repeat business, not a duplicate.
    if kind == "deal" and org_id != UNAFFILIATED_ORG_ID and not skip_dedup:
        twin = next(
            (t for t in threads
             if isinstance(t, dict) and t.get("kind") == "deal"
             and t.get("status") not in ("archived", "resolved")
             and not (t.get("deal") or {}).get("outcome")
             and thread_org_id(t) == org_id),
            None)
        if twin is not None:
            raise DuplicateDealError(twin, canonical_name)

    record: dict[str, Any] = {
        "id": thread_id or _next_project_id(threads),
        "canonical_name": canonical_name.strip(),
        # FOLDERGUARD: resolved against real directories, never guessed. `None`
        # when nothing matches — an honest gap the integrity checker can see.
        "folder_name": resolve_folder_name(workspace_root, canonical_name, folder_name),
        "status": status,
        "first_seen": first_seen or _today(),
    }
    if kind:                    record["kind"] = kind
    record["org_id"] = org_id   # required above; affiliation_id already folded in
    if owner_person_id:         record["owner_person_id"] = owner_person_id
    if stakeholder_person_ids:  record["stakeholder_person_ids"] = list(stakeholder_person_ids)
    if parent_thread_id:        record["parent_thread_id"] = parent_thread_id
    if spawned_from_thread_id:  record["spawned_from_thread_id"] = spawned_from_thread_id
    if deal is not None:        record["deal"] = deal
    if objective is not None:   record["objective"] = objective
    if cohort is not None:      record["cohort"] = cohort

    record = _coerce(record)
    _validate_thread(record)

    threads.append(record)
    _save_entities(workspace_root, data, source_skill)
    _log_event(workspace_root, "thread_created", record, source_skill)
    return record


def update_thread(workspace_root: str | Path, thread_id: str, *,
                  source_skill: str = "unknown", **fields) -> dict:
    """Update allowed fields on an existing thread (e.g. status, last_activity,
    next_step, roster_overrides). Validates, atomic-writes, emits
    `thread_updated`.

    Any LEGACY_THREAD_FIELDS the record already carries survive untouched;
    naming one in `fields` raises (see _reject_uncanonical_writes)."""
    workspace_root = Path(workspace_root)
    data = _load_entities(workspace_root)
    threads = _threads(data)
    target = next((t for t in threads if t.get("id") == thread_id), None)
    if target is None:
        raise ValueError(f"thread not found: {thread_id}")
    _reject_uncanonical_writes(fields)
    for k, v in fields.items():
        target[k] = v
    coerced = _coerce(target)
    target.clear(); target.update(coerced)
    _validate_thread(target)
    _save_entities(workspace_root, data, source_skill)
    _log_event(workspace_root, "thread_updated", target, source_skill)
    return target
