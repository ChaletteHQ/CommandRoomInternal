#!/usr/bin/env python3
"""People identity reconciler — three-tier identity model + backfill (SPEC PID1).

WHY THIS EXISTS
The propose-only posture on people was burying the user: a ~25-row identity
queue every staff meeting, one person fragmented across multiple rows, and
FS-19-suppressed "already on file" proposals sitting open forever. Where the
evidence is strong, people-adding becomes AUTOMATIC — safely, on the existing
LB1 auto rail (`AUTO_ALLOWED["person_org_creation_structured_fact"]` + the
registered archive reverser), narrated and batch-undoable. Where it isn't, the
queue gets radically cheaper: one person = one identity-clustered row.

THE THREE TIERS (computed in code, per cluster — a rule table, never a score;
all four §0 decisions RULED by M 2026-07-18):

  AUTO-ADD       multi-token full name AND (an OBSERVED email in the cluster's
                 own captured text under the F-3 attribution rules, OR ≥2
                 independent source families, OR a calendar-attendee record —
                 which reaches this module as an observed address in the
                 calendar-sourced evidence, so it rides the same two branches)
                 AND zero same-name collision AND not already on file.
                 Applied via `people_writer.auto_add_person` (its internal
                 same-name gate is defense-in-depth: needs_confirm demotes to
                 CONFIRM, never forks), tombstones stamped with ONE
                 brain_batch_id so `brain_undo.undo_batch` reverses the run.
  CONFIRM        everything ambiguous. Lone first names live here permanently
                 (Bug #19); same-name collisions live here even with an email.
  MERGE-PROPOSE  evidence pointing at an EXISTING record. Never an auto-merge
                 (`merge_person_into` has no reverser — confirm-only forever).
                 Exact-email matches MAY silently resolve `same_as` (§0-2
                 ruling: YES) — EXCEPT role-shaped addresses (info@/office@/
                 admin@/support@/hello@-class), which stay merge-propose rows:
                 a shared inbox is the one way an exact email lies about
                 identity.

Plus: unnamed Granola speakers NEVER become person rows — they become
`unidentified_attendee_observed` annotations (fully silent per §0-4, one count
line in the weekly staff meeting), resolved when a name/known-email attaches.

ONE entry point (`run_identity_reconcile`) serves both the Sunday
`identity-reconcile` maintenance job (STEADY_CAPS) and the one-time
per-workspace backfill (BACKFILL_CAPS) — one classifier, two cap sets.
`person_backlog_sweep.plan_sweep` delegates its classification here (D7 —
one rule table, never forked). Caps spill NARRATED, never silent (§0-3).

DRY-RUN IS THE DEFAULT. `--apply` performs the writes. stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ---------------------------------------------------------------------------
# Policy constants (§0 rulings — widen by ruling, not by drift)
# ---------------------------------------------------------------------------

# §0-3 caps. Overflow spills to the review pile and is COUNTED in the receipt
# (no-silent-caps rule) — never silently dropped.
STEADY_CAPS = {"auto_add": 10, "merge_propose": 10}
BACKFILL_CAPS = {"auto_add": 15, "merge_propose": 10}

# §0-2 role-address guard: local parts that mark a SHARED inbox — an exact
# email match on one of these must NOT silently link (it stays a
# merge-propose row), and a shared role address is never duplicate-suspect
# evidence between existing records. The pattern list is a constant with a
# test (the ruling's exact words).
ROLE_ADDRESS_LOCAL_PARTS = frozenset({
    "info", "office", "admin", "support", "hello", "contact", "sales",
    "team", "billing", "accounts", "help", "hr", "careers", "jobs",
    "noreply", "no-reply", "donotreply", "mail", "enquiries", "inquiries",
})

# Second-eyes F4 (2026-07-19, live-proven): a canonical name is letters plus
# name punctuation (space - ' . ’). Anything else — parentheses, digits,
# slashes, '?', quotes, commas — marks a capture-side annotation or guess
# (the live queue carried "<name> (or <other> alt account)"), and a guess
# string must never become a record name on the AUTO rail. Conservative by
# design: a name this regex flags still renders as a confirm row for a
# human decision (non-Latin scripts therefore demote to confirm, never
# silently drop).
_NAME_ANNOTATION_RE = re.compile(r"[^A-Za-zÀ-ÖØ-öø-ÿ\s\-'.’]")

_SOURCE_FAMILY_TOKENS = (
    # Checked in order; first family whose token hits wins. Slack/calendar
    # before meeting/mail because their tokens are the most specific.
    ("slack", ("slack",)),
    ("calendar", ("calendar", "invite", "attendee")),
    ("meeting", ("granola", "transcript", "meeting")),
    ("mail", ("mail", "inbox", "thread", "sent", "email", "gmail",
              "superhuman")),
)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _events_path(ws) -> Path:
    return Path(ws) / "_hq" / "data" / "events.jsonl"


def is_role_address(email: str | None) -> bool:
    """True when the address's local part is role-shaped (shared inbox)."""
    if not email or "@" not in email:
        return False
    local = email.split("@", 1)[0].strip().lower()
    return local in ROLE_ADDRESS_LOCAL_PARTS


def _norm_name(s: str | None) -> str:
    # Same semantics as people_writer._normalize_name (whitespace-collapsed
    # lowercase) — inlined to keep this module import-light; the writer-side
    # gates still run people_writer's own normalization at write time.
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _source_family(row: dict) -> Optional[str]:
    """Which independent source FAMILY a proposal row came from (D2:
    independence is by family, not by row count). Unknown → None (an
    unclassifiable source never counts toward corroboration)."""
    text = f"{row.get('source_ref') or ''} {row.get('evidence') or ''}".lower()
    for family, tokens in _SOURCE_FAMILY_TOKENS:
        if any(t in text for t in tokens):
            return family
    return None


# ---------------------------------------------------------------------------
# Clustering (D1/D3) — a READ-side projection, like every other projector
# ---------------------------------------------------------------------------

def cluster_open_proposals(rows: list[dict]) -> dict:
    """Group open person-proposal rows into identity clusters.

    Input: rows from `confirm_flow.load_open_person_proposals` (any filter
    posture — the caller owns suppression/TTL choices). Returns:

      {"clusters":  [...],   # named add-clusters (one person = one cluster)
       "updates":   [...],   # standalone update-type rows (person_id-bearing
                             #   or no matching cluster) — existence is their
                             #   premise, they render separately
       "nameless":  [...]}   # nameless add-type rows (annotation tier — D5;
                             #   NEVER rendered as person rows)

    Add-type rows group by normalized name (exact — a bare "Quinn" is never
    absorbed into "Quinn Alvarez"; the fuller-name cluster stays distinct,
    Bug #19). Update-type rows with no person_id whose name matches an
    add-cluster fold into that cluster's evidence (the person is still
    proposed). Cluster fields:
      key, name (longest spelling), rows (newest-first), add_rows,
      update_rows, seqs (int seqs), fingerprints (seq-less rows' D8
      fingerprints), row_id (stable wire id), inferred_role, inferred_org,
      source_families.
    """
    clusters: dict[str, dict] = {}
    updates: list[dict] = []
    nameless: list[dict] = []
    pending_updates: list[dict] = []

    for row in rows:
        name = (row.get("name") or "").strip()
        if row.get("type") == "person_update_proposal":
            if row.get("person_id") or not name:
                updates.append(row)
            else:
                pending_updates.append(row)
            continue
        if not name:
            nameless.append(row)
            continue
        key = _norm_name(name)
        c = clusters.setdefault(key, {
            "key": key, "name": name, "rows": [], "add_rows": [],
            "update_rows": [], "seqs": [], "fingerprints": [],
            "inferred_role": None, "inferred_org": None,
        })
        c["rows"].append(row)
        c["add_rows"].append(row)
        c["name"] = _better_name(c["name"], name)
        _absorb_row(c, row)

    for row in pending_updates:
        key = _norm_name(row.get("name"))
        c = clusters.get(key)
        if c is None:
            updates.append(row)  # no proposed person to attach to
            continue
        c["rows"].append(row)
        c["update_rows"].append(row)
        _absorb_row(c, row)

    out = []
    for c in clusters.values():
        c["rows"].sort(key=lambda r: r.get("captured_ts") or "", reverse=True)
        c["source_families"] = sorted(
            {f for f in (_source_family(r) for r in c["add_rows"]) if f})
        c["row_id"] = _cluster_row_id(c)
        out.append(c)
    # Oldest cluster first (matches the queue's age ordering).
    out.sort(key=lambda c: min((r.get("captured_ts") or "") for r in c["rows"]))
    return {"clusters": out, "updates": updates, "nameless": nameless}


def _better_name(current: str, candidate: str) -> str:
    """The cluster's display title: more tokens wins; among equals, the
    spelling with more capitalization (a lowercase dictation transcript of
    the same name never displaces the properly-cased one)."""
    if len(_norm_name(candidate).split()) > len(_norm_name(current).split()):
        return candidate
    if _norm_name(candidate) == _norm_name(current):
        cap = sum(1 for ch in candidate if ch.isupper())
        cur = sum(1 for ch in current if ch.isupper())
        if cap > cur:
            return candidate
    return current


def _absorb_row(cluster: dict, row: dict) -> None:
    seq = row.get("seq")
    if isinstance(seq, int) and not isinstance(seq, bool):
        cluster["seqs"].append(seq)
    elif row.get("fingerprint"):
        cluster["fingerprints"].append(row["fingerprint"])
    for field in ("inferred_role", "inferred_org"):
        if not cluster[field] and row.get(field):
            cluster[field] = row[field]


def _cluster_row_id(cluster: dict) -> str:
    """Stable wire id: the oldest add row's seq (`person:<seq>` — the shape
    live widgets already carry), falling back to the D8 fingerprint for an
    all-seq-less cluster."""
    for row in sorted(cluster["add_rows"],
                      key=lambda r: r.get("captured_ts") or ""):
        seq = row.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            return f"person:{seq}"
    if cluster["fingerprints"]:
        return f"person:fp:{cluster['fingerprints'][0]}"
    return f"person:{cluster['key']}"


def person_queue_view(rows: list[dict], *, now_iso: Optional[str] = None) -> dict:
    """The RENDERED person-queue projection (D3) — what the Staff Meeting
    adapter emits and what the morning brief's pointer counts (step 10:
    pointer honesty — the two can never disagree because both call this).

    Applies the FS-17 low-context age-out per row, then clusters. Nameless
    add rows are in the view but NEVER counted/rendered (annotation tier)."""
    from event_time import parse_ts
    from brain_proposals import (PERSON_LOW_CONTEXT_STALE_DAYS,
                                 person_proposal_is_low_context)

    now = parse_ts(now_iso or _now_iso())
    kept = []
    for row in rows:
        if now is not None and person_proposal_is_low_context(row):
            opened = parse_ts(row.get("captured_ts"))
            if opened is not None and \
                    (now - opened).days > PERSON_LOW_CONTEXT_STALE_DAYS:
                continue  # aged name-only mention — never surfaces (FS-17)
        kept.append(row)
    return cluster_open_proposals(kept)


def count_person_rows(rows: list[dict], *, now_iso: Optional[str] = None) -> int:
    """How many person rows actually RENDER: named clusters + standalone
    update rows. THE count the morning-brief pointer uses (one person = one
    row — never the raw proposal-event count)."""
    view = person_queue_view(rows, now_iso=now_iso)
    return len(view["clusters"]) + len(view["updates"])


# ---------------------------------------------------------------------------
# Classification (D2 — the rule table)
# ---------------------------------------------------------------------------

def cluster_observed_email(cluster: dict):
    """(email, source_row) — the first OBSERVED address across the cluster's
    add rows, newest first, under `person_backlog_sweep._observed_email`'s
    F-3 attribution rules (ONE implementation — imported, never copied)."""
    from person_backlog_sweep import _observed_email

    for row in sorted(cluster["add_rows"],
                      key=lambda r: r.get("captured_ts") or "", reverse=True):
        probe = dict(row)
        probe["name"] = cluster["name"]
        email = _observed_email(probe)
        if email and is_role_address(email):
            # Second-eyes F1 (2026-07-19): §0-2's own doctrine applied to
            # CAPTURE — a role-shaped local part (shared inbox) never
            # attributes to a person. It neither corroborates an auto-add
            # nor lands on a record (a stored info@ would poison Tier-1
            # email resolution forever). Keep scanning older rows.
            continue
        if email:
            return email, row
    return None, None


def classify_cluster(workspace_root, cluster: dict) -> dict:
    """Tier one identity cluster: {"tier": "auto"|"confirm"|"merge_propose"|
    "annotation", "why": ..., "email": ..., "matched_person": ...}.

    A rule table mirroring the AUTO_ALLOWED doctrine — auditable, never a
    confidence score. The write-side gates (`auto_add_person`'s same-name
    gate, `find_existing_person`'s tiers) still run at apply time; this
    classification is the read-side plan."""
    from confirm_flow import person_name_on_file
    from people_writer import (MultipleCandidatesError, find_existing_person,
                               list_same_name_people)

    name = (cluster.get("name") or "").strip()
    if not name:
        return {"tier": "annotation", "why": "no name captured — an unnamed "
                "attendee is an annotation, never a person row (D5)",
                "email": None, "matched_person": None}

    email, _src = cluster_observed_email(cluster)

    if person_name_on_file(workspace_root, name):
        matched = None
        try:
            matched = find_existing_person(workspace_root, name=name,
                                           email=email)
        except MultipleCandidatesError:
            matched = None
        except Exception:
            matched = None
        return {"tier": "merge_propose", "email": email,
                "matched_person": matched, "silent_link_ok": True,
                "why": f"{name!r} confidently resolves to an existing record "
                       "— link/merge is a recorded resolution, not a render "
                       "filter (D4a)"}

    if email:
        # Second-eyes F2 (2026-07-19): an observed address that EXACTLY
        # matches an existing record means the evidence points at someone
        # already on file — a duplicate add (the auto tier) is never the
        # right write. The name did NOT confidently resolve (the branch
        # above), so the two signals DISAGREE: this is a human link ROW,
        # never the §0-2 silent link (F-3 sole-address attribution is
        # fallible — a quoted thread's sender address can misattribute).
        matched = None
        try:
            matched = find_existing_person(workspace_root, email=email)
        except MultipleCandidatesError:
            return {"tier": "confirm", "email": email,
                    "matched_person": None, "silent_link_ok": False,
                    "why": f"observed address {email} is ambiguous across "
                           "existing records — never auto on a disputed "
                           "match"}
        except Exception:
            matched = None
        if matched is not None:
            return {"tier": "merge_propose", "email": email,
                    "matched_person": matched, "silent_link_ok": False,
                    "why": f"observed address {email} already belongs to "
                           f"{(matched.get('canonical_name') or matched.get('id'))!r}"
                           " — link it, never a duplicate add (D4a)"}

    if _NAME_ANNOTATION_RE.search(name):
        # Second-eyes F4 (2026-07-19, live-proven): guess/annotation markers
        # in the captured name ("<name> (or <other> alt account)") — a
        # captured note is not a canonical name; a human decides.
        return {"tier": "confirm", "email": email, "matched_person": None,
                "why": "name carries annotation/guess markers — a captured "
                       "note is not a canonical name, never auto"}

    if len(_norm_name(name).split()) < 2:
        # Bug #19 pin: a lone first name is a permanent human decision — even
        # with role AND org AND an observed email.
        return {"tier": "confirm", "email": email, "matched_person": None,
                "why": "lone first name — a human decision, never auto "
                       "(Bug #19)"}

    try:
        collisions = list_same_name_people(workspace_root, name)
    except Exception:
        collisions = None  # unreadable substrate → fail safe, never auto
    if collisions is None:
        return {"tier": "confirm", "email": email, "matched_person": None,
                "why": "same-name collision check unavailable — never auto "
                       "on an unverified bar"}
    if collisions:
        names = ", ".join(sorted(
            (c.get("canonical_name") or c.get("id") or "?")
            for c in collisions)[:4])
        return {"tier": "confirm", "email": email, "matched_person": None,
                "why": f"same-name collision with {names} — auto-add with a "
                       "collision is Bug #19's exact shape"}

    n_families = len(cluster.get("source_families") or [])
    if email:
        # Covers the calendar-attendee route too: a calendar invitee's
        # display-name+address reaches the proposal as observed evidence text
        # (D10 capture-side attach), which lands in this branch.
        return {"tier": "auto", "email": email, "matched_person": None,
                "why": f"full name + observed address {email} + zero "
                       "collision (D2)"}
    if n_families >= 2:
        return {"tier": "auto", "email": None, "matched_person": None,
                "why": "full name + "
                       f"{n_families} independent source families "
                       f"({'/'.join(cluster['source_families'])}) + zero "
                       "collision (D2)"}
    return {"tier": "confirm", "email": email, "matched_person": None,
            "why": "full name but a single source and no observed address — "
                   "stays a human confirm (D2)"}


# ---------------------------------------------------------------------------
# UXR1 D3 — the auto-link gate (M ruling 2026-07-21): a person_link
# auto-applies instead of minting a confirm row ONLY when ALL of (a)-(d)
# hold. The gate is load-bearing — widen it by ruling, never by drift; the
# pin test moves WITH the policy, never around it.
# ---------------------------------------------------------------------------

def _record_org_name(workspace_root, record: dict) -> str:
    """The on-file record's org display name (primary_org_id resolved via
    entities.json; legacy org_id honored). "" when none / unreadable."""
    org_id = (record.get("primary_org_id") or record.get("org_id") or "")
    if not org_id:
        return ""
    try:
        data = json.loads((Path(workspace_root) / "_hq" / "data" /
                           "entities.json").read_text(encoding="utf-8"))
        ent = data.get("entities") if isinstance(data.get("entities"), dict) \
            else data
        for o in ent.get("orgs") or []:
            if o.get("id") == org_id:
                return (o.get("canonical_name") or "").strip()
    except Exception:
        return ""
    return ""


def sole_record_for_email(workspace_root, addr: str):
    """AUTOAPPLY §4a — the ONE non-archived person record carrying `addr`,
    or None when zero or 2+ do (normalized, case-folded).

    "Exactly one" is the whole strength of the clause: an address on two
    records is a collision, not an identifier, and must never auto-link.
    Fail-safe — an unreadable substrate returns None, so the caller asks."""
    addr = (addr or "").strip().lower()
    if not addr:
        return None
    try:
        from entities_io import entities_collection
        from people_writer import get_person_emails

        data = json.loads((Path(workspace_root) / "_hq" / "data" /
                           "entities.json").read_text(encoding="utf-8"))
        hits = []
        for p in entities_collection(data, "people"):
            if not isinstance(p, dict) or p.get("status") == "archived":
                continue
            if addr in {e.strip().lower() for e in get_person_emails(p)}:
                hits.append(p)
        return hits[0] if len(hits) == 1 else None
    except Exception:
        return None


def auto_link_eligible(workspace_root, cluster: dict, matched: dict,
                       email: str | None) -> tuple[bool, str]:
    """(eligible, why) — the UXR1 D3 (a)-(d) gate for ONE merge-propose
    entry whose cluster confidently resolved to `matched`.

      (a′) EITHER an exact normalized full-name match of ≥2 tokens, OR the
          mention carries an email address that exactly matches exactly ONE
          on-file person's address — and that person is `matched`
          (AUTOAPPLY §4a, M ruling "act when the evidence is corroborated
          and the action is reversible"). An id-level address is strictly
          stronger evidence than a name match, so requiring the name match
          ON TOP of it was the gate asking a question it already had the
          answer to: a first-name mention and its full-name record with the
          same address on both
          sides. Role/shared-inbox addresses are excluded (they identify a
          mailbox, not a person), and a lone first name still needs the
          address — the bare-name case alone is Bug #19 forever;
      (b) exactly ONE on-file candidate for that name — two same-name
          records are the IDM1 class, NEVER auto. THIS is also what keeps
          (a′)'s email clause honest: a mention named X whose only observed
          address belongs to record Y (the misattributed quoted-thread
          sender, second-eyes F2) fails here, because X's same-name
          candidate set never equals {Y};
      (c) no conflicting signal: the mention's observed email, when present,
          must belong to the record (a role/shared-inbox address is NOT
          corroboration and never auto-links — §0-2's guard applied here);
          the mention's inferred org, when present and resolvable, must not
          contradict the record's org;
      (d) the pair is not in scan_existing_duplicates' suspect set (a
          record under duplicate suspicion is not a safe link target).

    Fail-safe throughout: any unverifiable bar returns False (the row still
    renders as a confirm ask — a human decision is the safe floor)."""
    name = (cluster.get("name") or "").strip()
    matched_name = (matched or {}).get("canonical_name") or ""
    if _NAME_ANNOTATION_RE.search(name):
        return False, "name carries annotation/guess markers — never auto"
    # (c) observed-address scan — hoisted above (a′) because the email clause
    # reads it. RAW scan, not cluster_observed_email (that helper deliberately
    # SKIPS role-shaped addresses at capture — the F1 guard — so relying on it
    # here would auto-link a mention whose only observed address is a shared
    # inbox; the ruling says that case ASKS).
    observed: list[str] = []
    try:
        from person_backlog_sweep import _observed_email

        for row in cluster.get("add_rows") or []:
            probe = dict(row)
            probe["name"] = name
            addr = _observed_email(probe)
            if addr:
                observed.append(addr)
    except Exception:
        return False, "observed-address scan unavailable — never auto"
    record_emails = {e.strip().lower() for e in _record_emails(matched)}
    for addr in observed:
        if is_role_address(addr):
            return False, ("observed address is role-shaped (shared inbox) "
                           "— not corroboration, still asks (§0-2)")
        if addr.strip().lower() not in record_emails:
            return False, ("observed address does not belong to the record "
                           "— conflicting signal, still asks")
    # (a′) exact multi-token name match OR id-level email corroboration.
    exact_name = (_norm_name(name) == _norm_name(matched_name)
                  and len(_norm_name(name).split()) >= 2)
    id_level_email = None
    if not exact_name:
        for addr in observed:
            sole = sole_record_for_email(workspace_root, addr)
            if sole is not None and sole.get("id") == matched.get("id"):
                id_level_email = addr.strip().lower()
                break
    if not exact_name and not id_level_email:
        if _norm_name(name) != _norm_name(matched_name):
            return False, ("spelling differs from the record and no address "
                           "corroborates it — a human decision")
        return False, "lone first name with no address — never auto (Bug #19)"
    # (b) exactly one on-file candidate
    from people_writer import list_same_name_people

    try:
        candidates = list_same_name_people(workspace_root, name)
    except Exception:
        return False, "same-name candidate check unavailable — never auto"
    ids = {c.get("id") for c in (candidates or []) if c.get("id")}
    if ids != {matched.get("id")}:
        return False, (f"{len(ids)} on-file candidates for {name!r} — "
                       "two same-name records are the IDM1 class, never auto")
    # (c) no conflicting signal — org
    inferred_org = (cluster.get("inferred_org") or "").strip()
    if inferred_org:
        record_org = _record_org_name(workspace_root, matched)
        if record_org and _norm_name(inferred_org) != _norm_name(record_org):
            return False, (f"mention's org {inferred_org!r} contradicts the "
                           f"record's {record_org!r} — still asks")
    # (d) not a duplicate suspect
    try:
        suspects = scan_existing_duplicates(workspace_root)
    except Exception:
        return False, "duplicate-suspect scan unavailable — never auto"
    mid = matched.get("id")
    for s in suspects:
        if mid in (s["keep"].get("id"), s["duplicate"].get("id")):
            return False, ("record is in the duplicate-suspect set — "
                           "still asks")
    if id_level_email:
        return True, (f"{id_level_email} belongs to exactly one record — "
                      f"{matched_name!r} (UXR1 D3 gate a'-d, AUTOAPPLY §4a)")
    return True, (f"exact unique clean match to {matched_name!r} "
                  "(UXR1 D3 gate a-d)")


# ---------------------------------------------------------------------------
# Existing-record duplicate scan (D4b — the absorbed LB2 dedup detector)
# ---------------------------------------------------------------------------

def scan_existing_duplicates(workspace_root) -> list[dict]:
    """Duplicate suspects among EXISTING non-archived records: exact
    multi-token normalized canonical_name pairs, or a shared personal email.
    Single-token name pairs are EXCLUDED from name-based suspicion entirely
    (the live person_077/person_124 two-short-names pin — two different real
    people); role-shaped shared addresses are excluded from email-based
    suspicion (a shared inbox is not an identity). PROPOSE-only input —
    nothing here merges anything."""
    from entities_io import entities_collection
    from people_writer import get_person_emails

    try:
        data = json.loads((Path(workspace_root) / "_hq" / "data" /
                           "entities.json").read_text(encoding="utf-8"))
        people = [p for p in entities_collection(data, "people")
                  if p.get("status") != "archived"]
    except Exception:
        return []

    pairs: dict[tuple, dict] = {}

    by_name: dict[str, list[dict]] = {}
    for p in people:
        norm = _norm_name(p.get("canonical_name"))
        if len(norm.split()) >= 2:
            by_name.setdefault(norm, []).append(p)
    for norm, group in by_name.items():
        if len(group) < 2:
            continue
        for a, b in zip(group, group[1:]):
            keep, dup = _pick_keep(a, b)
            pairs[(keep["id"], dup["id"])] = {
                "keep": keep, "duplicate": dup,
                "why": f"exact canonical-name match {norm!r} on two records"}

    by_email: dict[str, list[dict]] = {}
    for p in people:
        for e in get_person_emails(p):
            e = e.strip().lower()
            if e and not is_role_address(e):
                by_email.setdefault(e, []).append(p)
    for email, group in by_email.items():
        seen_ids = {g["id"] for g in group}
        if len(seen_ids) < 2:
            continue
        uniq = list({g["id"]: g for g in group}.values())
        for a, b in zip(uniq, uniq[1:]):
            keep, dup = _pick_keep(a, b)
            pairs.setdefault((keep["id"], dup["id"]), {
                "keep": keep, "duplicate": dup,
                "why": f"both records carry {email}"})
    return list(pairs.values())


def _pick_keep(a: dict, b: dict) -> tuple[dict, dict]:
    """Keeper = the richer record (more non-empty fields); tie → the older
    (lower-numbered) id. The proposal row embeds both ids verbatim — the
    user's click is the verdict, this is just a sensible default order."""
    def richness(p):
        return sum(1 for v in p.values() if v not in (None, "", [], {}))
    ra, rb = richness(a), richness(b)
    if ra != rb:
        return (a, b) if ra > rb else (b, a)
    return (a, b) if str(a.get("id")) <= str(b.get("id")) else (b, a)


# ---------------------------------------------------------------------------
# Annotations (D5) — unnamed speakers are meeting annotations, never rows
# ---------------------------------------------------------------------------

def load_open_annotations(workspace_root) -> list[dict]:
    """Open `unidentified_attendee_observed` events: every annotation whose
    seq is NOT listed in any later `identity_reconcile_run` receipt's
    `data.annotations_resolved`. Fully silent surface (§0-4) — the ONLY
    render is the weekly staff meeting's one count line."""
    import event_refs

    path = _events_path(workspace_root)
    if not path.exists():
        return []
    events = event_refs.load_events(path)
    resolved: set[int] = set()
    for ev in events:
        if ev.get("type") != "identity_reconcile_run":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        for s in data.get("annotations_resolved") or []:
            if isinstance(s, int):
                resolved.add(s)
    out = []
    for ev in events:
        if ev.get("type") != "unidentified_attendee_observed":
            continue
        seq = ev.get("seq")
        if isinstance(seq, int) and seq in resolved:
            continue
        out.append(ev)
    return out


def count_open_annotations(workspace_root) -> int:
    """The staff meeting's ONE count line source (§0-4): 'N unnamed speakers
    pending identification — resolving against calendars.' Drop-empty."""
    return len(load_open_annotations(workspace_root))


# ---------------------------------------------------------------------------
# The plan (pure classify — no writes)
# ---------------------------------------------------------------------------

def plan_reconcile(workspace_root, *, now_iso: Optional[str] = None) -> dict:
    """Classify every open person proposal into the execution plan. Pure
    over the substrate — no writes. Reads WITHOUT suppress_on_file (like the
    sweep: on-file collisions must be SEEN to be resolved, not re-hidden)."""
    from brain_proposals import (PERSON_LOW_CONTEXT_STALE_DAYS,
                                 person_proposal_is_low_context)
    from confirm_flow import load_open_person_proposals
    from event_time import parse_ts

    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()
    now = parse_ts(now_iso)

    # Honor the ACTIVE dismissal set (review F-2 posture, verbatim from the
    # sweep): a snoozed identity is never adjudicated mid-snooze — a cluster
    # containing ANY snoozed row is wholly kept open.
    snoozed_seqs: set = set()
    snoozed_fps: set = set()
    try:
        import event_refs
        from mute_ledger import active_dismissal_target_ids

        events = event_refs.load_events(_events_path(ws)) \
            if _events_path(ws).exists() else []
        for tid in active_dismissal_target_ids(events, now_iso):
            t = str(tid)
            if t.startswith("person:fp:"):
                snoozed_fps.add(t.split("person:fp:", 1)[1])
                continue
            if t.startswith("person:"):
                t = t.split(":", 1)[1]
            if t.isdigit():
                snoozed_seqs.add(int(t))
    except Exception:
        snoozed_seqs, snoozed_fps = set(), set()

    rows = load_open_person_proposals(_events_path(ws))
    view = cluster_open_proposals(rows)

    plan: dict = {"auto": [], "confirm": [], "merge_propose": [],
                  "annotations": [], "expire": [], "keep_open": [],
                  "updates": view["updates"], "now_iso": now_iso}

    for row in view["nameless"]:
        if row.get("seq") in snoozed_seqs or \
                (row.get("fingerprint") in snoozed_fps):
            plan["keep_open"].append({"cluster": None, "proposal": row,
                                      "why": "snoozed by the user — the mute "
                                             "is honored"})
            continue
        plan["annotations"].append({
            "proposal": row,
            "why": "no name captured — converts to an unnamed-attendee "
                   "annotation (D5); never a person row"})

    for cluster in view["clusters"]:
        if any(s in snoozed_seqs for s in cluster["seqs"]) or \
                any(f in snoozed_fps for f in cluster["fingerprints"]):
            plan["keep_open"].append({"cluster": cluster,
                                      "why": "snoozed by the user — the "
                                             "reconciler never adjudicates "
                                             "a snoozed identity"})
            continue
        cls = classify_cluster(ws, cluster)
        if cls["tier"] == "merge_propose":
            email = cls["email"]
            matched = cls["matched_person"]
            exact_email = bool(
                cls.get("silent_link_ok")
                and email and matched and not is_role_address(email)
                and email.strip().lower() in {
                    e.strip().lower() for e in _record_emails(matched)})
            plan["merge_propose"].append({
                "cluster": cluster, "matched": matched, "email": email,
                "exact_email": exact_email, "why": cls["why"]})
            continue
        # FS-17 expiry (kept from the sweep): an AGED name-only single
        # mention expires instead of queueing forever. Only single-row
        # clusters — a second mention is corroboration, not staleness.
        if cls["tier"] == "confirm" and len(cluster["rows"]) == 1:
            row = cluster["rows"][0]
            if person_proposal_is_low_context(row):
                opened = parse_ts(row.get("captured_ts"))
                if opened is not None and now is not None and \
                        (now - opened).days > PERSON_LOW_CONTEXT_STALE_DAYS:
                    plan["expire"].append({
                        "cluster": cluster, "proposal": row,
                        "why": f"name-only mention, "
                               f"{(now - opened).days} days old (window "
                               f"{PERSON_LOW_CONTEXT_STALE_DAYS}d)"})
                    continue
        plan[cls["tier"]].append({"cluster": cluster, "email": cls["email"],
                                  "why": cls["why"]})

    plan["merge_suspects"] = scan_existing_duplicates(ws)
    return plan


def _record_emails(record: dict) -> list[str]:
    from people_writer import get_person_emails

    try:
        return get_person_emails(record)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Execution (D7 — one entry point, two cap sets)
# ---------------------------------------------------------------------------

def run_identity_reconcile(
    workspace_root,
    *,
    apply: bool = False,
    caps: Optional[dict] = None,
    now_iso: Optional[str] = None,
    exact_email_autolink: bool = True,
    fired_via: str = "scheduled",
    source_skill: str = "identity-reconcile",
) -> dict:
    """Plan and (with apply=True) execute the reconcile pass:

      - AUTO clusters   → `auto_add_person` on the existing R1 rail; every
                          member proposal tombstoned `person_added`, stamped
                          `brain_batch_id` + `brain_change_class` so ONE
                          `brain_undo.undo_batch` archives the adds and
                          reopens the expiry/annotation tombstones.
      - exact-email     → silent `same_as` (alias + tombstones, narrated in
        on-file matches   CHANGED via the receipt) — §0-2 YES ruling, with
                          the role-address guard; everything else on file
                          becomes a capped merge-propose row (kind
                          `person_link` on the bp rail).
      - dup suspects    → capped `person_merge` bp proposals (D4b). NO code
                          path merges records — `merge person records` is a
                          user click, forever.
      - nameless rows   → `unidentified_attendee_observed` annotation + a
                          reopenable tombstone (D5).
      - annotations     → resolved when their observed address now matches an
                          existing record (Tier-1 email — the substrate-side
                          join; calendar/Granola joins are capture-side D10).
      - aged low-ctx    → expire tombstone (FS-17, kept from the sweep).

    Ends with ONE `identity_reconcile_run` receipt (also the job's due-ness
    signal and the D6 CHANGED-narration source). Honesty rule: every receipt
    count comes from what was ACTUALLY written, never from the plan.
    """
    ws = Path(workspace_root)
    caps = dict(caps or STEADY_CAPS)
    now_iso = now_iso or _now_iso()
    plan = plan_reconcile(ws, now_iso=now_iso)
    batch_id = "idr_" + _dt.datetime.now(_dt.timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ")
    plan["batch_id"] = batch_id
    plan["caps"] = caps
    if not apply:
        plan["applied"] = False
        return plan

    from event_gate import append_event
    from confirm_flow import build_person_proposal_resolved_event
    from people_writer import add_person_alias, auto_add_person

    events_path = _events_path(ws)
    results: dict = {"added": [], "needs_confirm": [], "linked": [],
                     "auto_linked": [], "already_on_file": [],
                     "merge_rows_proposed": 0, "annotations": [],
                     "annotations_resolved": [], "expired": [], "errors": [],
                     "spilled": {"auto_add": 0, "merge_propose": 0}}

    def _tombstone(cluster_or_row, *, resolution, person_id=None, alias=None,
                   note="", change_class="person_proposal_tombstone",
                   extra_data=None):
        """One tombstone per member proposal (int seq or D8 fingerprint),
        batch-stamped for undo. `extra_data` (UXR1 D3) rides on every member
        tombstone — the person_link reverser's re-propose payload. Returns
        the events appended."""
        if isinstance(cluster_or_row, dict) and "rows" in cluster_or_row:
            members = cluster_or_row["rows"]
        else:
            members = [cluster_or_row]
        evs = []
        for row in members:
            seq = row.get("seq")
            kwargs = {}
            if not (isinstance(seq, int) and not isinstance(seq, bool)):
                seq = None
                kwargs["proposal_fingerprint"] = row.get("fingerprint")
            tomb = build_person_proposal_resolved_event(
                seq, resolution=resolution, source_skill=source_skill,
                person_id=person_id, alias=alias, note=note, **kwargs)
            tomb["data"]["brain_batch_id"] = batch_id
            tomb["data"]["brain_change_class"] = change_class
            if person_id:
                tomb["data"]["person_id"] = person_id
            if isinstance(extra_data, dict):
                for k, v in extra_data.items():
                    tomb["data"].setdefault(k, v)
            evs.append(tomb)
        if evs:
            append_event(events_path, evs, holder=source_skill)
        return evs

    # ---- AUTO tier (capped, spill narrated) --------------------------------
    for entry in plan["auto"]:
        cluster = entry["cluster"]
        if len(results["added"]) >= int(caps.get("auto_add", 0)):
            results["spilled"]["auto_add"] += 1
            plan["keep_open"].append({"cluster": cluster,
                                      "why": "auto cap reached — spilled to "
                                             "the review pile (§0-3, "
                                             "narrated never silent)"})
            continue
        email = entry["email"]
        _e, src_row = cluster_observed_email(cluster) if email else (None, None)
        try:
            res = auto_add_person(
                ws,
                canonical_name=cluster["name"],
                email=email,
                email_provenance=({"via": "person_proposal",
                                   "proposal_seq": (src_row or {}).get("seq"),
                                   "source_ref": (src_row or {}).get("source_ref")}
                                  if email else None),
                source_skill=source_skill,
                role=cluster.get("inferred_role") or None,
            )
        except Exception as exc:  # loud per-item, contained per-batch
            results["errors"].append({"row_id": cluster["row_id"],
                                      "error": f"{type(exc).__name__}: {exc}"})
            continue
        if res.get("status") == "needs_confirm":
            # Same-name gate fired at write time (defense-in-depth) — the
            # cluster DEMOTES to confirm, never forks.
            results["needs_confirm"].append({"row_id": cluster["row_id"],
                                             "name": cluster["name"]})
            continue
        record = res["record"]
        _tombstone(cluster, resolution="person_added",
                   person_id=record["id"],
                   note=f"identity reconcile {batch_id} — {entry['why']}",
                   change_class="person_org_creation_structured_fact")
        results["added"].append({
            "row_id": cluster["row_id"], "name": cluster["name"],
            "person_id": record["id"], "email": email,
            "n_proposals": len(cluster["rows"]),
            "email_dropped_no_provenance":
                res.get("email_dropped_no_provenance")})

    # ---- MERGE-PROPOSE tier -------------------------------------------------
    merge_cap = int(caps.get("merge_propose", 0))
    # AUTOAPPLY §4a consequence rule — records linked in THIS run, and the
    # clusters still open at the end of the loop (the sweep below pairs them).
    auto_linked_records: dict = {}
    still_open_clusters: list = [e["cluster"] for e in plan["confirm"]]
    pending_links: list = []
    for entry in plan["merge_propose"]:
        cluster, matched = entry["cluster"], entry["matched"]
        if exact_email_autolink and entry["exact_email"] and matched:
            # §0-2 ruling: exact-email matches resolve silently — narrated in
            # CHANGED via the receipt, reopenable via the tombstone reverser.
            try:
                if _norm_name(cluster["name"]) != \
                        _norm_name(matched.get("canonical_name")):
                    add_person_alias(ws, matched["id"], cluster["name"],
                                     source_skill=source_skill)
            except Exception as exc:
                # An alias mapped to a DIFFERENT person (or any write error)
                # is a human decision — demote to a merge-propose row.
                results["errors"].append({"row_id": cluster["row_id"],
                                          "error": f"{type(exc).__name__}: {exc}"})
                entry = dict(entry, exact_email=False)
            else:
                _tombstone(cluster, resolution="same_as",
                           person_id=matched["id"], alias=cluster["name"],
                           note=f"identity reconcile {batch_id} — exact-email "
                                "link (§0-2)")
                results["linked"].append({"row_id": cluster["row_id"],
                                          "name": cluster["name"],
                                          "person_id": matched["id"],
                                          "email": entry["email"]})
                continue
        # UXR1 D3 (M ruling 2026-07-21) — the OBVIOUS link auto-applies
        # instead of asking: exact-unique-clean per gate (a)-(d). The LB2
        # auto lifecycle contract holds — propose(tier="auto") + apply +
        # resolve in the SAME run; the auto proposal never rests open. A
        # non-"proposed" status (open confirm row after an undo, or the
        # 60d decline cooldown) falls through to the confirm row path —
        # the human's standing answer is never steamrolled.
        if matched is not None:
            eligible, why = auto_link_eligible(ws, cluster, matched,
                                               entry["email"])
            if eligible and _auto_apply_person_link(
                    ws, cluster, matched, entry, why, batch_id,
                    _tombstone, results, source_skill):
                if matched.get("id"):
                    auto_linked_records[matched["id"]] = matched
                continue
        # NOT proposed yet — the §4a sweep below runs FIRST. Proposing here
        # would mint a confirm row for a cluster the sweep is about to
        # resolve, leaving an orphaned ask pointing at a closed proposal.
        pending_links.append(entry)

    # ---- AUTOAPPLY §4a consequence rule: post-link already_on_file ---------
    # M's complaint was "duplicates asking to confirm" — the SAME person
    # rendered as two rows. Where both rows come from ONE cluster the
    # tombstone fan-out above already collapses them. This closes the other
    # half: a SEPARATE add cluster (a different captured spelling of the same
    # person — the typo'd mention gate (b) cannot token-match) that the link
    # just answered, which would otherwise keep asking to add someone now
    # demonstrably on file.
    #
    # The bar is deliberately the SAME evidence the link itself required — an
    # id-level address on the record, or an exact normalized full-name match.
    # No new evidence class is admitted here, so this adds no new risk: it
    # only stops re-asking a question already answered in this run.
    swept: set = set()
    for cluster in still_open_clusters + [e["cluster"] for e in pending_links]:
        resolved = _post_link_already_on_file(ws, cluster, auto_linked_records)
        if resolved is None:
            continue
        record, why = resolved
        try:
            # `same_as` is the shipped resolution for "this mention IS that
            # record" (PROPOSAL_RESOLUTIONS has no already_on_file member, and
            # inventing one would be a shared-vocabulary change for a local
            # need). No alias is passed — see _auto_apply_person_link on why
            # the auto rail stays alias-free. change_class keeps the
            # registered person_proposal_tombstone reverser, batch-stamped, so
            # `undo` reopens these rows with the rest of the batch.
            _tombstone(cluster, resolution="same_as",
                       person_id=record.get("id"),
                       note=f"identity reconcile {batch_id} — {why}",
                       change_class="person_proposal_tombstone")
            swept.add(cluster["row_id"])
            results["already_on_file"].append({
                "row_id": cluster["row_id"], "name": cluster.get("name"),
                "person_id": record.get("id"), "why": why})
        except Exception as exc:  # loud per-item, contained per-batch
            results["errors"].append({"row_id": cluster["row_id"],
                                      "error": f"{type(exc).__name__}: {exc}"})

    # ---- deferred confirm rows for everything the sweep left open ----------
    for entry in pending_links:
        cluster, matched = entry["cluster"], entry["matched"]
        if cluster["row_id"] in swept:
            continue
        if results["merge_rows_proposed"] >= merge_cap:
            results["spilled"]["merge_propose"] += 1
            continue
        if _propose_person_link(ws, cluster, matched, source_skill):
            results["merge_rows_proposed"] += 1

    # ---- D4b existing-record duplicate suspects ----------------------------
    for suspect in plan["merge_suspects"]:
        if results["merge_rows_proposed"] >= merge_cap:
            results["spilled"]["merge_propose"] += 1
            continue
        if _propose_person_merge(ws, suspect, source_skill):
            results["merge_rows_proposed"] += 1

    # ---- Annotations (D5) ---------------------------------------------------
    # Second-eyes F3 (2026-07-19): conversion is IDEMPOTENT per
    # (meeting_source_ref, attendee_hint). Undoing a conversion batch reopens
    # the proposal tombstone but the annotation event is immutable history —
    # without this guard the next run would mint a duplicate annotation and
    # the staff-meeting count line would double-count one speaker.
    _open_ann_keys = {
        ((a.get("data") or {}).get("meeting_source_ref"),
         (a.get("data") or {}).get("attendee_hint"))
        for a in load_open_annotations(ws)}
    for entry in plan["annotations"]:
        row = entry["proposal"]
        try:
            from meeting_capture import build_unidentified_attendee_event
            from person_backlog_sweep import _observed_email

            hint = (row.get("review_reason") or row.get("evidence")
                    or "unidentified attendee")
            ev = build_unidentified_attendee_event(
                row.get("source_ref") or "unknown",
                attendee_hint=hint[:120],
                attendee_email=_observed_email(row),
                source_skill=source_skill,
            )
            key = (ev["data"]["meeting_source_ref"],
                   ev["data"]["attendee_hint"])
            if key not in _open_ann_keys:
                append_event(events_path, [ev], holder=source_skill)
                _open_ann_keys.add(key)
            _tombstone(row, resolution="not_relevant",
                       note=f"identity reconcile {batch_id} — converted to "
                            "an unnamed-attendee annotation (D5)")
            results["annotations"].append({"seq": row.get("seq"),
                                           "source_ref": row.get("source_ref")})
        except Exception as exc:
            results["errors"].append({"seq": row.get("seq"),
                                      "error": f"{type(exc).__name__}: {exc}"})

    # ---- Annotation resolution (substrate-side join) -----------------------
    for ann in load_open_annotations(ws):
        data = ann.get("data") if isinstance(ann.get("data"), dict) else {}
        email = (data.get("attendee_email") or "").strip()
        if not email:
            continue
        try:
            from people_writer import find_existing_person

            if find_existing_person(ws, email=email) is not None:
                results["annotations_resolved"].append(ann.get("seq"))
        except Exception:
            continue  # ambiguity/error → the annotation stays open

    # ---- FS-17 expiry -------------------------------------------------------
    for entry in plan["expire"]:
        row = entry["proposal"]
        try:
            _tombstone(row, resolution="not_relevant",
                       note=f"expired by identity reconcile {batch_id} — "
                            f"{entry['why']}")
            results["expired"].append({"seq": row.get("seq"),
                                       "name": row.get("name")})
        except Exception as exc:
            results["errors"].append({"seq": row.get("seq"),
                                      "error": f"{type(exc).__name__}: {exc}"})

    # ---- ONE receipt (honesty: counts from what was WRITTEN) ---------------
    from receipts import log_receipt

    log_receipt(
        ws, "identity-reconcile",
        receipt_type="identity_reconcile_run",
        fired_via=fired_via,
        extra_data={
            "batch_id": batch_id,
            "n_auto_added": len(results["added"]),
            "n_linked": len(results["linked"]),
            # UXR1 D3 — the auto-link lane's own count (distinct from the
            # §0-2 exact-email n_linked): change_feed narrates it with the
            # undo affordance.
            "n_auto_linked": len(results["auto_linked"]),
            "people_auto_linked": [{"person_id": a["person_id"],
                                    "name": a["name"]}
                                   for a in results["auto_linked"]],
            "n_merge_proposed": results["merge_rows_proposed"],
            "n_clustered": len(plan["confirm"]),
            "n_annotations": len(results["annotations"]),
            "n_annotations_resolved": len(results["annotations_resolved"]),
            "n_expired": len(results["expired"]),
            "n_kept_open": len(plan["keep_open"]),
            "n_errors": len(results["errors"]),
            "caps": caps,
            "spilled": results["spilled"],
            "people_added": [{"person_id": a["person_id"], "name": a["name"]}
                             for a in results["added"]],
            "annotations_resolved": [s for s in results["annotations_resolved"]
                                     if isinstance(s, int)],
        },
    )
    plan["applied"] = True
    plan["results"] = results
    return plan


def _post_link_already_on_file(ws, cluster: dict, linked: dict):
    """AUTOAPPLY §4a consequence rule — `(record, why)` when this still-open
    add cluster is answered by a link applied EARLIER IN THE SAME RUN, else
    None.

    WHY THIS IS NOT `person_proposal_already_on_file` (a deliberate deviation
    from SPEC §4a's stated mechanism): that predicate delegates to
    `confirm_flow.person_name_on_file`, which is confident-matches-only and
    returns False for a lone first name — the Bug #19 discipline. Loosening
    it to catch the lone-first-name case would change the answer for every
    OTHER caller too (the shared queue loader's suppress_on_file, the morning
    brief, the commitments chat), silently hiding lone-first-name add rows
    across three surfaces on evidence none of them has seen. The re-ask this
    rule kills is local to the reconcile run, so the fix is local to it.

    Two bars, both exactly what `auto_link_eligible` already demanded of the
    link itself — no new evidence class is admitted:
      * an observed address on the cluster that belongs to exactly ONE on-file
        record, and that record is the one just linked (role addresses out);
      * an exact normalized multi-token full-name match to that record.
    Anything weaker keeps asking. Fail-safe: any error returns None."""
    if not linked:
        return None
    name = (cluster.get("name") or "").strip()
    if not name or _NAME_ANNOTATION_RE.search(name):
        return None
    try:
        from person_backlog_sweep import _observed_email

        for row in cluster.get("add_rows") or []:
            probe = dict(row)
            probe["name"] = name
            addr = _observed_email(probe)
            if not addr or is_role_address(addr):
                continue
            sole = sole_record_for_email(ws, addr)
            if sole is not None and sole.get("id") in linked:
                return linked[sole["id"]], (
                    f"already on file — {addr.strip().lower()} belongs to the "
                    "record linked earlier in this run")
        for pid, record in linked.items():
            canon = (record or {}).get("canonical_name") or ""
            if canon and _norm_name(name) == _norm_name(canon) \
                    and len(_norm_name(name).split()) >= 2:
                return record, ("already on file — exact name match to the "
                                "record linked earlier in this run")
    except Exception:
        return None
    return None


def _auto_apply_person_link(ws, cluster, matched, entry, why, batch_id,
                            tombstone, results, source_skill) -> bool:
    """UXR1 D3 — auto-apply ONE exact-unique-clean person_link on the LB2
    auto rail: propose(tier="auto") + apply + resolve in the SAME run.

    The APPLY is the same_as tombstone fan-out over the cluster's member
    proposals (change_class="person_link", batch-stamped + carrying the
    reverser's re-propose payload).

    NO ALIAS IS EVER WRITTEN HERE. Before AUTOAPPLY §4a the reason was that
    gate (a) required the normalized-exact spelling, so there was nothing to
    save. Gate (a′) admits an email-corroborated link whose spelling DIFFERS
    (a first-name mention onto its full-name record), so that reason is
    gone — but the rule stands
    on a stronger one: an alias is a SECOND mutation, on the record itself,
    and the registered `person_link` reverser does not remove it. Writing
    one would make the auto tier only partly reversible, which §3 forbids
    outright. The cost is that a future bare-name mention with no address
    asks again — correct, because without the address that mention really is
    ambiguous. (The exact-email §0-2 path keeps its own alias behavior: it is
    reached only when the NAME confidently resolves, a different bar.)

    Returns True when the link applied; False on ANY failure or non-
    "proposed" propose status (open confirm row after an undo; the 60d
    decline cooldown) — the caller falls through to the confirm-row path,
    so a human's standing answer is never steamrolled."""
    from brain_proposals import propose, resolve_proposal

    matched_name = (matched or {}).get("canonical_name") or ""
    matched_id = (matched or {}).get("id")
    fingerprint = f"person_link:{cluster['key']}:{matched_id or 'unresolved'}"
    evidence = (cluster["rows"][0].get("evidence") or
                cluster["rows"][0].get("source_ref") or "")
    try:
        res = propose(
            ws,
            kind="person_link",
            fingerprint=fingerprint,
            evidence=evidence,
            action_tuples=[{"action": "confirm proposal"},
                           {"action": "dismiss proposal"},
                           {"action": "snooze proposal 7d"}],
            tier="auto",
            change_class="person_link",
            detector="identity-reconcile",
            render_line=(f"Linked {cluster['name']} to {matched_name} "
                         f"({why})"),
            person_id=matched_id,
            extra={"title": cluster["name"],
                   "cluster_seqs": list(cluster["seqs"]),
                   "cluster_fingerprints": list(cluster["fingerprints"]),
                   "alias_name": cluster["name"],
                   "matched_name": matched_name},
        )
        if res.get("status") != "proposed":
            return False
        tombstone(cluster, resolution="same_as", person_id=matched_id,
                  alias=cluster["name"],
                  note=f"identity reconcile {batch_id} — auto-link "
                       f"(UXR1 D3: {why})",
                  change_class="person_link",
                  extra_data={"link_fingerprint": fingerprint,
                              "link_evidence": evidence,
                              "matched_name": matched_name,
                              # §7 — WHICH corroboration clause fired, one
                              # string, so a later audit reads the reason off
                              # the event instead of a vanished chat.
                              "auto_predicate": (
                                  "id_fact:email_match"
                                  if "belongs to exactly one record" in why
                                  else "exact_name:unique_clean")})
        resolve_proposal(ws, res["proposal_id"], "applied",
                         resolved_by=source_skill, source_skill=source_skill)
        results["auto_linked"].append({"row_id": cluster["row_id"],
                                       "name": cluster["name"],
                                       "person_id": matched_id,
                                       "why": why})
        return True
    except Exception as exc:  # loud per-item, contained per-batch
        results["errors"].append({"row_id": cluster["row_id"],
                                  "error": f"{type(exc).__name__}: {exc}"})
        return False


def _link_differentiator(ws, matched) -> str:
    """UXR1 D4 — what makes the on-file record recognizable in one glance:
    its org, else an email, else "last touched {date}", else the honest
    "no details on file". Never empty — a decision row must show what
    changes on a click."""
    org = _record_org_name(ws, matched or {})
    if org:
        return org
    for e in _record_emails(matched or {}):
        e = (e or "").strip()
        if e:
            return e
    rec = matched or {}
    date = (rec.get("last_interaction") or rec.get("last_touched_at")
            or rec.get("first_seen") or "")
    if date:
        from brain_proposals import _short_date

        short = _short_date(str(date))
        if short:
            return f"last touched {short}"
        return f"last touched {str(date)[:10]}"
    return "no details on file"


def person_link_ask_line(ws, name: str, matched, evidence: str) -> str:
    """UXR1 D4 — the decision-grade ask for a person_link row that still
    ASKS (it failed the D3 auto gate, or an undo returned the decision to
    the human): where the mention came from (its evidence — on every row,
    never rendered before D4) and which record it would link to (the
    differentiator: org > email > last touched > "no details on file").
    A multi-candidate name names the COUNT and never pre-fills —
    consistent with SPEC_IDM1's daily-rail ruling."""
    matched_name = (matched or {}).get("canonical_name") or "an existing record"
    appeared = (evidence or "").strip() or "a captured mention"
    if (matched or {}).get("id"):
        diff = _link_differentiator(ws, matched)
        return (f"'{name}' appeared as {appeared}. "
                f"Same as {matched_name} ({diff})? — link it?")
    # No single resolved record — count the same-name candidates so the
    # collision is named, never ranked-and-picked (IDM1).
    n = 0
    try:
        from people_writer import list_same_name_people
        n = len(list_same_name_people(ws, name) or [])
    except Exception:
        n = 0
    if n >= 2:
        return (f"'{name}' appeared as {appeared}. "
                f"{n} people named {name} on file — "
                "same as one of them? — link it?")
    return (f"'{name}' appeared as {appeared}. "
            f"Same as {matched_name}? — link it?")


def _propose_person_link(ws, cluster, matched, source_skill) -> bool:
    """One D4a merge-propose row on the bp rail. Adjudicated via the generic
    bp verbs (apply-choices `cr-brain` kind `person_link` handlers).
    Render line: person_link_ask_line (UXR1 D4 — decision-grade)."""
    from brain_proposals import propose

    matched_name = (matched or {}).get("canonical_name") or "an existing record"
    matched_id = (matched or {}).get("id")
    evidence = (cluster["rows"][0].get("evidence") or
                cluster["rows"][0].get("source_ref") or "")
    render_line = person_link_ask_line(ws, cluster["name"], matched, evidence)
    try:
        res = propose(
            ws,
            kind="person_link",
            fingerprint=f"person_link:{cluster['key']}:{matched_id or 'unresolved'}",
            evidence=evidence,
            action_tuples=[{"action": "confirm proposal"},
                           {"action": "dismiss proposal"},
                           {"action": "snooze proposal 7d"}],
            tier="confirm",
            detector="identity-reconcile",
            render_line=render_line,
            person_id=matched_id,
            extra={"title": cluster["name"],
                   "cluster_seqs": list(cluster["seqs"]),
                   "cluster_fingerprints": list(cluster["fingerprints"]),
                   "alias_name": cluster["name"],
                   "matched_name": matched_name},
        )
        return res.get("status") == "proposed"
    except Exception:
        return False


def _propose_person_merge(ws, suspect, source_skill) -> bool:
    """One D4b duplicate-suspect row. The `merge person records` verb — a
    USER CLICK in apply-choices, never this module — dispatches the record
    merge (no reverser exists; never in AUTO_ALLOWED)."""
    from brain_proposals import propose

    keep, dup = suspect["keep"], suspect["duplicate"]
    pair = sorted([str(keep.get("id")), str(dup.get("id"))])
    try:
        res = propose(
            ws,
            kind="person_merge",
            fingerprint=f"person_merge:{pair[0]}:{pair[1]}",
            evidence=suspect.get("why") or "",
            action_tuples=[{"action": "merge person records"},
                           {"action": "proposal not relevant"},
                           {"action": "snooze proposal 7d"}],
            tier="confirm",
            detector="identity-reconcile",
            render_line=(f"{keep.get('canonical_name')} and "
                         f"{dup.get('canonical_name')} look like the same "
                         f"person ({suspect.get('why')}) — merge the "
                         "records?"),
            person_id=keep.get("id"),
            extra={"title": f"{keep.get('canonical_name')} / "
                            f"{dup.get('canonical_name')}",
                   "keep_id": keep.get("id"),
                   "duplicate_id": dup.get("id")},
        )
        return res.get("status") == "proposed"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _narrate(plan: dict) -> str:
    lines = []
    mode = "APPLIED" if plan.get("applied") else "DRY RUN — nothing written"
    lines.append(f"Identity reconcile ({mode})")
    lines.append(f"  auto-add: {len(plan['auto'])}")
    for e in plan["auto"]:
        lines.append(f"    + {e['cluster']['name']!r} — {e['why']}")
    lines.append(f"  confirm clusters: {len(plan['confirm'])}")
    lines.append(f"  merge-propose (on file): {len(plan['merge_propose'])}")
    for e in plan["merge_propose"]:
        tag = "exact-email link" if e["exact_email"] else "row"
        lines.append(f"    ~ {e['cluster']['name']!r} → {tag}")
    lines.append(f"  duplicate suspects: {len(plan.get('merge_suspects') or [])}")
    lines.append(f"  annotations (no name): {len(plan['annotations'])}")
    lines.append(f"  expire (aged, name-only): {len(plan['expire'])}")
    lines.append(f"  left open: {len(plan['keep_open'])}")
    if plan.get("applied"):
        r = plan["results"]
        lines.append(
            f"  applied: {len(r['added'])} added, {len(r['linked'])} linked, "
            f"{len(r.get('auto_linked') or [])} auto-linked (UXR1 D3), "
            f"{r['merge_rows_proposed']} merge rows proposed, "
            f"{len(r['annotations'])} annotations, {len(r['expired'])} "
            f"expired, {len(r['needs_confirm'])} held for a same-name "
            f"confirm, {len(r['errors'])} errors")
        sp = r["spilled"]
        if sp["auto_add"] or sp["merge_propose"]:
            lines.append(f"  cap spill (narrated, never silent): "
                         f"{sp['auto_add']} auto, {sp['merge_propose']} "
                         f"merge-propose — they stay in the review pile")
        lines.append(f"  undo: the adds and tombstones reverse with batch id "
                     f"{plan['batch_id']} (adds archive, expiries reopen)")
    return "\n".join(lines)


def main() -> int:
    # Windows pipes default to cp1252 — the narration carries non-ASCII.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="perform the writes (default: dry-run plan only)")
    ap.add_argument("--backfill", action="store_true",
                    help="use the one-time backfill caps (auto-add 15) "
                         "instead of the weekly steady-state caps")
    ap.add_argument("--now", default=None, help="ISO now override (tests)")
    ap.add_argument("--json", action="store_true",
                    help="emit the machine-readable plan as well")
    args = ap.parse_args()
    plan = run_identity_reconcile(
        args.workspace, apply=args.apply,
        caps=BACKFILL_CAPS if args.backfill else STEADY_CAPS,
        now_iso=args.now,
        fired_via="manual" if args.backfill else "scheduled")
    print(_narrate(plan))
    if args.json:
        print(json.dumps(plan, default=str))
    return 0


__all__ = [
    "STEADY_CAPS",
    "BACKFILL_CAPS",
    "ROLE_ADDRESS_LOCAL_PARTS",
    "is_role_address",
    "cluster_open_proposals",
    "person_queue_view",
    "count_person_rows",
    "cluster_observed_email",
    "classify_cluster",
    "auto_link_eligible",
    "person_link_ask_line",
    "scan_existing_duplicates",
    "load_open_annotations",
    "count_open_annotations",
    "plan_reconcile",
    "run_identity_reconcile",
]


if __name__ == "__main__":
    raise SystemExit(main())
