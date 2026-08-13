#!/usr/bin/env python3
"""
Personal-content leak scanner (SPEC PGUARD1 D2) — the last line of defense
for the person/personal-data firewall.

WHY THIS EXISTS
---------------
The 2026-07-18 personal-side audit (item 7) found every leak scanner in the
plugin — docx_leak_scanner, chat_output_renderer's _LEAK_PATTERNS,
widget_transport's validate pass — blind to PERSONAL content. A personal
reminder ("call Mom", a family dinner) that reached a board pack or client
deliverable would sail through every gate, because the gates only knew about
internal IDs, substrate paths, and voice tells. This module adds the personal
axis, and the three validators wire it in SURFACE-GATED:

  - org / board / client / external surfaces  → BLOCKING finding
  - m_facing / owner surfaces                 → never blocks (personal content
                                                is legitimate there)
  - unknown / absent surface                  → never blocks (the risk rule:
                                                never default an m_facing
                                                surface to org; only a caller
                                                that DECLARES an org surface
                                                gets the block)

WHAT IT CAN AND CANNOT CATCH (stated honestly)
----------------------------------------------
Marker-based, not semantic. It catches the structural fingerprints a
personal-lane row leaves when it reaches a rendered surface: reminder ids
(`rem_<ULID>` — reminders should never render on ANY non-owner surface),
literal `personal: true` flags / `data-personal` attributes / `[personal]`
chips, `tie: personal` markers (BAL1's tie field), and the
balance-nudge event-type token. It cannot know that the STRING "dinner with
Sam" is personal — that classification lives on the ROW (`is_personal`), and
the row-level firewall (events_io.iter_events_org_scoped, reminders.py's
surface gate) is the layer that keeps classified rows out of org data views.
This scanner is the backstop for the row that slips through anyway.

Pure stdlib. `is_personal` itself reads nothing; the two substrate reads in
this module are `personal_tie_ids` (entities.json → the person-tie join input,
BUG-8330 item 12) and `business_thread_ids` (entities.json → the thread
register the join's override resolves against, BUG-8330 fix round 2), and
callers pass both results in.

M-REVIEWABLE SEAM (BUG-8330 item 12 / FX-5)
-------------------------------------------
What belongs on an org surface when one person is BOTH client and family is a
policy call, not a code call. The policy this module ships is one line:

    a GENUINE business binding wins, and everything withheld is MEASURED.

"Genuine" is `business_binding` — a thread id that resolves in the workspace's
own thread register to a business-lane thread, or an explicit org binding on
the row. Anything weaker fails closed: withheld, and counted into
`personal_withheld` / `personal_withheld_by_tie` on the org-scoped read. The
counters are the instrument for re-setting the policy with evidence instead of
guesses. M re-sets it at the merge gate; until then the conservative default
stands. Do not loosen the predicate without moving the counter with it — an
override that leaks while reporting zero withheld is strictly worse than the
over-withholding it replaced, which is exactly what fix round 1 shipped.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

# Reminder event family — mirrored from reminders.REMINDER_TYPES (kept as a
# literal so this module stays import-free / usable inside events_io without
# a cycle; run_personal_firewall_test pins the two lists equal).
_REMINDER_TYPES = ("reminder", "reminder_updated", "reminder_cleared")

# The BAL1 personal-lane event family: the Sunday nudge
# (balance.compute_balance) and the `book` confirm-path linkage
# (balance.record_actioned — a code writer since OI-3 B-1 2026-07-26; it was
# prose-only, which is what that finding closed). Type pinned in both SKILL
# prose sites, equality asserted by run_fu_pretest_pins_test.
_PERSONAL_EVENT_TYPES = ("balance_nudge_suggested", "balance_nudge_actioned")


# ---------------------------------------------------------------------------
# Surface classification — the gate that keeps this scanner from ever
# blocking an owner-facing render.
# ---------------------------------------------------------------------------

# Surfaces where personal content is a LEAK. Explicit allowlist of org tokens
# (normalized: lowercase, `_`→`-`): a surface tag must DECLARE itself org/
# board/client/external to get the blocking scan. Anything else — m_facing,
# staff-meeting, commitments, None, a tag we've never seen — is treated as
# not-org, per the PGUARD1 risk rule: never default an owner surface to org.
ORG_OUTPUT_SURFACES = frozenset({
    "org", "board", "client", "client-facing", "external",
    "board-pack", "board-pack-assembler", "advisor-export", "value-receipt",
})


def is_org_surface(surface: Optional[str]) -> bool:
    """True iff `surface` explicitly declares an org/board/client/external
    audience (the surfaces where a personal finding is BLOCKING). None or an
    unrecognized tag → False — the safe direction for owner surfaces."""
    if not surface or not isinstance(surface, str):
        return False
    return surface.strip().lower().replace("_", "-") in ORG_OUTPUT_SURFACES


# ---------------------------------------------------------------------------
# Row classification — is this event/row personal-lane?
# ---------------------------------------------------------------------------

def _data(row: dict) -> dict:
    d = row.get("data")
    return d if isinstance(d, dict) else {}


def personal_tie_ids(workspace_root) -> frozenset:
    """person_ids whose entities.json record carries `tie: "personal"` —
    the ONE marker the CEO already sets (BAL1 D1). This is the join input for
    `is_personal(personal_ids=…)`. Defensive: unreadable entities.json →
    empty set (the org reader then behaves exactly as before the join).

    balance.personal_ties / dormancy._is_personal_tie /
    relationship_moves._personal_tie_ids are per-surface variants of this
    read; this is the firewall's own copy so events_io can join without
    importing a surface module."""
    try:
        import json as _json
        p = Path(workspace_root) / "_hq" / "data" / "entities.json"
        data = _json.loads(p.read_text(encoding="utf-8"))
        entities = data.get("entities") if isinstance(data.get("entities"), dict) else data
        people = entities.get("people") or []
        return frozenset(
            rec.get("id") for rec in people
            if isinstance(rec, dict) and rec.get("tie") == "personal"
            and rec.get("id")
        )
    except Exception:
        return frozenset()


# Row fields that carry person linkage — the join keys (BUG-8330 item 12).
# Envelope `person_ids` plus the data-scope owner/counterparty spellings the
# commitment family actually writes.
_PERSON_REF_LIST_FIELDS = ("person_ids", "counterparty_ids")
_PERSON_REF_SCALAR_FIELDS = ("person_id", "owner_id", "counterparty_id")

# Every spelling of "this row points at a workspace thread". Mirrored from
# backfill_substrate._THREAD_KEYS (kept as a literal so this module stays
# import-free inside events_io); run_personal_tie_join_test pins the two
# tuples equal, so a new spelling there cannot silently bypass the resolver.
_THREAD_REF_FIELDS = ("primary_thread_id", "thread_id", "project_id",
                      "primary_project_id")

# The "deliberately unaffiliated / personal" org sentinel — thread_writer's
# UNAFFILIATED_ORG_ID and the same value org_activity.thread_org_map and
# org_activity.event_org_ids already exclude. A thread or a row bound to it
# is NOT a business binding.
_UNAFFILIATED_ORG_ID = "personal"
# The personal thread kind (references/ORG_AND_THREAD_MODEL.md § kind enum).
_PERSONAL_THREAD_KIND = "personal"


def business_thread_ids(workspace_root) -> frozenset:
    """Thread ids in the workspace's CANONICAL THREAD REGISTER that sit in the
    business lane — the resolver behind the person-tie join's override.

    BUG-8330 FIX ROUND 2. The first cut of the override keyed on the mere
    PRESENCE of `primary_thread_id`, which `capture_gate` stamps on every
    thread-sourced commitment (`capture_gate.py:823` / `:1107`) with no lane
    discrimination — so a family insurance claim captured from ordinary mail
    carried one and rode the override straight back onto every org surface,
    re-opening the exact leak item 12 was filed for. Presence of a thread id
    is provenance ("this row came from somewhere"), not a business signal.

    RESOLUTION is the signal. A thread id is a business signal only when it
    names a record in `entities.json`'s thread register (`threads`, legacy
    `projects`) that is not personal-lane:

      - `kind: "personal"`                       -> NOT business
      - org binding == `personal` (the UNAFFILIATED_ORG_ID sentinel)
                                                 -> NOT business
      - anything else in the register            -> business

    A thread with no org binding at all still counts: the register is the
    workspace's list of tracked workstreams, and org-less threads are live in
    real workspaces. An id that resolves to NOTHING — a raw mail/chat thread
    id, a typo, a retired record — fails closed: withheld and counted.

    Resolution mirrors `org_activity.thread_org_map` (`affiliation_id` first,
    legacy `org_id`, both collections read); this is the firewall's own copy
    so `events_io` can resolve without importing a surface module, exactly as
    `personal_tie_ids` is its own copy of the person-tie read. The test pins
    the two agree on the same substrate.

    Defensive: unreadable/absent entities.json -> empty set, which makes the
    override inert and leaves the conservative pre-override behaviour (every
    tie-touching row withheld, and counted)."""
    try:
        import json as _json
        p = Path(workspace_root) / "_hq" / "data" / "entities.json"
        data = _json.loads(p.read_text(encoding="utf-8"))
        view = (data.get("entities")
                if isinstance(data.get("entities"), dict) else data)
        out = set()
        for coll in ("threads", "projects"):
            for t in view.get(coll) or []:
                if not isinstance(t, dict):
                    continue
                tid = t.get("id")
                if not isinstance(tid, str) or not tid:
                    continue
                # Casefolded (CONFIRM #3 D-3): `kind` is written verbatim by
                # create_thread with no enum validation — the enum lives only
                # in ORG_AND_THREAD_MODEL.md prose, so a hand-edited or
                # prose-following record plausibly arrives capitalised, and an
                # exact-match exclusion FAILS OPEN (the personal thread reads
                # as business). This is a privacy fence; it fails closed.
                kind = t.get("kind")
                if (isinstance(kind, str)
                        and kind.strip().casefold() == _PERSONAL_THREAD_KIND):
                    continue
                oid = t.get("affiliation_id") or t.get("org_id")
                if (isinstance(oid, str)
                        and oid.strip().casefold() == _UNAFFILIATED_ORG_ID):
                    continue
                out.add(tid)
        return frozenset(out)
    except Exception:
        return frozenset()


def _row_org_ids(row: dict) -> set:
    """Every org id this row binds to, envelope + data scope, with the
    `personal` sentinel excluded (casefolded — same D-3 fence rule as
    `business_thread_ids`: an exclusion that is a privacy fence fails
    closed, so a capitalised sentinel must not read as a business org).
    Same field set as `org_activity.event_org_ids`' direct half
    (`org_ids[]`, `org_id`)."""
    def _is_sentinel(x) -> bool:
        return x.strip().casefold() == _UNAFFILIATED_ORG_ID
    out: set = set()
    d = _data(row)
    for holder in (row, d):
        v = holder.get("org_ids")
        if isinstance(v, list):
            out.update(x for x in v
                       if isinstance(x, str) and x and not _is_sentinel(x))
        v = holder.get("org_id")
        if isinstance(v, str) and v and not _is_sentinel(v):
            out.add(v)
    return out


def business_thread_id(row, business_threads=None) -> str:
    """The row's RESOLVED business-thread id, or "" when it has none.

    `business_threads` is the register set from
    `business_thread_ids(workspace_root)`. Every thread-id spelling
    (`_THREAD_REF_FIELDS`, envelope then data) is checked against it, so
    `data.project_id` counts exactly as `primary_thread_id` does. An id that
    is absent from the register — a raw mail thread id above all — is NOT a
    business signal and returns "".

    FAILS CLOSED: with no register supplied (`None`/empty) NOTHING resolves
    and this returns "". The override then never fires and the conservative
    pre-override behaviour stands. That is the safe direction: a caller that
    forgot to resolve over-withholds (measured) instead of leaking."""
    try:
        if not isinstance(row, dict) or not business_threads:
            return ""
        known = business_threads if isinstance(
            business_threads, (set, frozenset)) else set(business_threads)
        d = _data(row)
        for holder in (row, d):
            for field in _THREAD_REF_FIELDS:
                v = holder.get(field)
                if isinstance(v, str) and v.strip() and v.strip() in known:
                    return v.strip()
        return ""
    except Exception:
        return ""


def business_binding(row, business_threads=None) -> str:
    """The row's GENUINE business binding, or "" when it has none — the ONE
    predicate the person-tie join's override consults.

    Returns a reason token rather than a bool so a caller (and a pin) can see
    WHICH signal fired:

      "thread:<id>"  a thread-id spelling that RESOLVES in the workspace's
                     thread register to a business-lane thread
      "org:<id>"     an explicit org binding on the row itself (`org_id` /
                     `org_ids[]`, envelope or data), sentinel excluded

    The org half needs no register: `capture_gate` never stamps an org onto a
    commitment envelope, so an org id on a row is the writer's own explicit
    statement that this is work. It is also the shape FX-5 was written to
    rescue and did not — a commitment carrying `org_id` plus a project
    binding and no thread was still being withheld.

    This is the whole override surface. Explicit PERSONAL markers are checked
    BEFORE it in `is_personal` and are absolute: no business binding of any
    kind can rescue `data.personal`, a row-level `tie`, the reminder lane, the
    BAL1 types or a live account mask."""
    try:
        if not isinstance(row, dict):
            return ""
        tid = business_thread_id(row, business_threads)
        if tid:
            return f"thread:{tid}"
        orgs = _row_org_ids(row)
        if orgs:
            return f"org:{sorted(orgs)[0]}"
        return ""
    except Exception:
        return ""


def withheld_by_personal_tie(row, personal_ids=None,
                             business_threads=None) -> bool:
    """True when the person-tie JOIN — and ONLY that branch — is what keeps
    this row off an org surface. The count behind the `personal_withheld`
    receipt field; a row that is personal for any other reason (an explicit
    marker, the reminder lane, a live account mask) is not counted here.

    Takes the same `business_threads` register as `is_personal` and must be
    called with the SAME value, or the subset count disagrees with the drop."""
    try:
        if not personal_ids or not isinstance(row, dict):
            return False
        if not (_row_person_ids(row) & set(personal_ids)):
            return False
        if business_binding(row, business_threads):
            return False
        # Would it already be personal without the join?
        return not is_personal(row, masks=None, personal_ids=None)
    except Exception:
        return False


def _row_person_ids(row: dict) -> set:
    """Every person id this row references, envelope + data scope."""
    out: set = set()
    d = _data(row)
    for holder in (row, d):
        for field in _PERSON_REF_LIST_FIELDS:
            v = holder.get(field)
            if isinstance(v, list):
                out.update(x for x in v if isinstance(x, str) and x)
        for field in _PERSON_REF_SCALAR_FIELDS:
            v = holder.get(field)
            if isinstance(v, str) and v:
                out.add(v)
    return out


def is_personal(row, masks=None, personal_ids=None,
                business_threads=None) -> bool:
    """True when `row` (an events.jsonl event dict) belongs to the personal
    lane and must never feed an org/board/client/external output:

      - a reminder-family row whose effective `personal` flag is true —
        explicit `data.personal: true`, or (for a bare `reminder`) the D3
        default: no business ref (`data.ref`) and no `primary_thread_id`.
        A person reference alone does NOT make it work (D3 — "call Mom").
        Flag-less `reminder_updated` / `reminder_cleared` rows are personal
        too: they carry only the reminder id (unclassifiable without a
        join), an `edit` can carry a revised personal summary, and no org
        surface consumes lane-management rows — unknown fails closed;
      - a BAL1 personal-lane row (`_PERSONAL_EVENT_TYPES`: the Sunday
        `balance_nudge_suggested` nudge and the `book` confirm-path
        `balance_nudge_actioned` linkage — type alone classifies);
      - a row carrying `tie: "personal"` (top-level or in data — BAL1's
        personal-tie marker on person-scoped rows);
      - when `personal_ids` (a set from `personal_tie_ids(workspace_root)`)
        is given: a row ANY of whose person references (envelope
        `person_ids`, data owner/counterparty ids) resolves to a
        `tie: "personal"` person record AND that carries NO GENUINE BUSINESS
        BINDING (`business_binding`) — THE JOIN (BUG-8330 item 12) with its
        FIX-ROUND-2 override. The inline `tie` branch above was a reader with
        no writer: `tie` lives on PERSON RECORDS, and no capture path stamps
        it onto a commitment, so a family insurance claim captured from
        ordinary mail carried no marker at all. The join reuses the one
        marker the CEO already sets and classifies at read time, fixing every
        org-scoped reader at once. The override is the bounded half: a thread
        id that RESOLVES in the workspace's thread register to a business-lane
        thread, or an explicit org binding on the row, makes the row work — so
        a client who is also family keeps her business rows while a family row
        that merely came from a mail thread does not (`business_binding`,
        `business_threads=business_thread_ids(workspace_root)`);
      - when `masks` (a frozenset of account_ids from
        account_scope_gate.live_masks*) is given: a row whose account
        identity matches a live mask — masked-personal history.

    Never raises; a junk row is not personal (the account-scope wall and the
    defensive loaders own junk handling)."""
    try:
        if not isinstance(row, dict):
            return False
        t = row.get("type")
        d = _data(row)
        if t in _REMINDER_TYPES:
            personal = d.get("personal")
            if personal is None:
                if t == "reminder":
                    # Mirror of the reminders.py D3 default: org/thread refs
                    # make it work; a bare person reference does not.
                    personal = not (d.get("ref") or row.get("primary_thread_id"))
                else:
                    # reminder_updated / reminder_cleared carry only the
                    # reminder id — they cannot be classified without a join,
                    # an `edit` update can carry a revised personal summary,
                    # and no org surface consumes lane-management rows at
                    # all. Unknown → personal (fail closed for org output).
                    personal = True
            if bool(personal):
                return True
        if t in _PERSONAL_EVENT_TYPES:
            return True
        if row.get("tie") == "personal" or d.get("tie") == "personal":
            return True
        if personal_ids and (_row_person_ids(row) & set(personal_ids)):
            # BUG-8330 FIX ROUND 2 — the BUSINESS-BINDING override.
            #
            # Item 12's join was a pure OR over five person fields in two
            # scopes with no override at all, so ANY row touching a
            # personally-tied person vanished from every org surface: an
            # explicit org_id + project_id commitment with a business
            # counterparty dropped because one of three people on the thread
            # was family. That shape is not rare — a client who is also
            # family is live in the reporting workspace.
            #
            # Fix round 1 over-corrected the other way: it accepted the mere
            # PRESENCE of `primary_thread_id`, which capture_gate stamps on
            # EVERY thread-sourced commitment including one captured from
            # personal mail — so the marker-less family row the bug was filed
            # on came straight back onto every org surface. Presence is
            # provenance, not a business signal.
            #
            # The override now requires a GENUINE binding: a thread id that
            # RESOLVES in the workspace's own thread register to a
            # business-lane thread, or an explicit org binding on the row.
            # Everything else fails closed — withheld, and COUNTED, so the
            # cost of the conservative reading is measured rather than
            # guessed at.
            #
            # The explicit markers above (data.personal, tie on the row, the
            # reminder lane, BAL1 types) are checked FIRST and are absolute:
            # this override reaches only the inferred join, and the account
            # mask below is likewise beyond its reach.
            if not business_binding(row, business_threads):
                return True
        if masks:
            try:
                from account_scope_gate import _event_account_ids
            except ImportError:  # pragma: no cover — direct-path fallback
                import sys
                from pathlib import Path
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from account_scope_gate import _event_account_ids
            if _event_account_ids(row) & set(masks):
                return True
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Rendered-text scan — the validator-side backstop.
# ---------------------------------------------------------------------------

# Word-boundary-anchored patterns, same finding shape as
# docx_leak_scanner.scan_text_for_leaks so the validators can merge findings.
PERSONAL_LEAK_PATTERNS: list[tuple[str, re.Pattern]] = [
    # A reminder id on ANY rendered surface this scan runs on is a leak:
    # reminders render only on owner surfaces (show-my-reminders, the brief),
    # and those never invoke the org-gated scan.
    ("personal_reminder_id",
     re.compile(r"\brem_[0-9A-Za-z]{10,}\b", re.IGNORECASE)),
    # Literal personal flags — JSON-ish (`"personal": true`), key-value
    # (`personal: true` / `personal=true`), and the HTML wire attribute.
    ("personal_flag",
     re.compile(r"[\"']?personal[\"']?\s*[:=]\s*[\"']?true\b", re.IGNORECASE)),
    ("personal_wire_attr",
     re.compile(r"\bdata-personal\s*=\s*[\"']true[\"']", re.IGNORECASE)),
    # Rendered personal chips — `[personal]` / `(personal)` row badges.
    ("personal_chip",
     re.compile(r"[\[\(]\s*personal\s*[\]\)]", re.IGNORECASE)),
    # BAL1 tie marker rendered as text.
    ("personal_tie",
     re.compile(r"[\"']?tie[\"']?\s*[:=]\s*[\"']?personal\b", re.IGNORECASE)),
    # BAL1 personal-lane event type tokens (see _PERSONAL_EVENT_TYPES note).
    ("personal_event_type",
     re.compile(r"\bbalance_nudge_(?:suggested|actioned)\b", re.IGNORECASE)),
]


def scan_for_personal_leak(text_or_html) -> List[dict]:
    """Scan rendered output (chat text, widget HTML, extracted docx text) for
    personal-lane fingerprints. Returns findings shaped exactly like
    docx_leak_scanner.scan_text_for_leaks — {name, pattern, match, context} —
    empty list = clean. Never raises; the CALLER decides whether findings
    block (org/board/client surfaces) or are ignored (owner surfaces)."""
    if not text_or_html or not isinstance(text_or_html, str):
        return []
    findings: List[dict] = []
    for name, pat in PERSONAL_LEAK_PATTERNS:
        for m in pat.finditer(text_or_html):
            start, end = m.span()
            findings.append({
                "name": name,
                "pattern": pat.pattern,
                "match": m.group(0),
                "context": text_or_html[max(0, start - 20):
                                        min(len(text_or_html), end + 20)],
            })
    return findings


__all__ = [
    "ORG_OUTPUT_SURFACES",
    "PERSONAL_LEAK_PATTERNS",
    "is_org_surface",
    "is_personal",
    "personal_tie_ids",
    "scan_for_personal_leak",
]
