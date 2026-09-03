#!/usr/bin/env python3
"""
Decision-log view regenerator (v3.13.0+).

Walks `_hq/data/events.jsonl`, collects every `type: "decision"` event,
applies lifecycle overlays (superseded / resolved / reaffirmed / snoozed),
resolves IDs to display names via `_hq/data/entities.json`, localizes timestamps
to the workspace timezone via `shared/scripts/tz.py`, and atomic-writes
`_hq/views/DECISION_LOG.md`.

WHY THIS EXISTS:

Per the 2026-05-20 Cowork handoff #12/#20: decisions land in events.jsonl
canonically (M had 138 decisions in his substrate as of that date) but
the human-readable view at `_hq/views/DECISION_LOG.md` was last regenerated
2026-05-10 and showed only 81 decisions — ~57 stale. There was no regenerator
script in the plugin. The doc header claimed "AUTO-GENERATED" but no code
actually generated it.

The view also rendered NO lifecycle state — a `decision_superseded` event
would write fine to events.jsonl but the view still showed the original
decision as live. M's daily question "what did we decide about pricing?"
returned outdated answers because the substrate had the supersede but the
view didn't show it.

v3.13.0 ships this renderer + wires it into decision-write paths
(`decision-log`, `decision-revisit`, `apply-choices`, `meeting-notes`) so
the view never falls behind canonical substrate.

PUBLIC API:

  - regenerate(workspace_root) → dict
      Reads events.jsonl + entities.json, generates the view content,
      atomic-writes _hq/views/DECISION_LOG.md, returns a dict with
      counts of decisions by status. Idempotent.

  - regenerate_per_project_views(workspace_root, project_ids=None) → dict
      Optional v3.14.x-ish: per-project DECISIONS sections in each
      project's context file. Off by default; opt-in via the project_ids
      filter or callable from a project-context regenerator.

USAGE:

    python3 shared/scripts/render_decision_log.py <workspace_root>

    # Or from another skill:
    from render_decision_log import regenerate
    counts = regenerate(workspace_root)

Status taxonomy:
  - active: decision is current; no supersede/snooze event references it
  - superseded: a decision_superseded event names this decision (by id or by
    any accepted seq spelling — including the TOP-LEVEL `supersedes_seq` the
    schema defines; SUPERSEQ1 2026-08-29), OR a newer `decision` event
    restamps it via `supersedes_seq`, OR the decision's OWN `data.status` says
    "superseded" (DECSHAPES1 2026-09-02 — the field decision_match always
    honored and this reader ignored), and NO later decision_reaffirmed
    out-ranks it. Target chains live in
    event_types.decision_supersede_targets — one home, both readers.
  - resolved: the decision was CARRIED OUT — a decision_resolved event names
    it (id or any accepted seq spelling, via
    event_types.decision_resolve_targets), or its own status says "resolved"
    (DECSHAPES1). Distinct from superseded: nothing replaced this ruling, it
    was executed. Before DECSHAPES1 the taxonomy had no such bucket and every
    resolved decision rendered under Active.
  - reaffirmed: a decision_reaffirmed event references this seq (renders
    with the most-recent reaffirmation date + snooze window). WALKFIX1 FR-3:
    a reaffirm whose `reviewed_at` is LATER than the newest referencing
    supersede RESTORES the decision here, carrying the supersede on the line
    as history — supersede is no longer terminal, so a wrong one is
    repairable through the canonical vocabulary instead of being permanent.
    DECSHAPES1 widens that to every CLOSING signal: resolve and self-status
    are ordered on the same axis and are equally repairable.
  - snoozed: a decision_revisit_scheduled event references this seq with
    a future snooze_until_ts

LATEST SIGNAL WINS (WALKFIX1 FR-3, widened by DECSHAPES1). The closing
signals — supersede, resolve, and a decision's own closing status, whatever
their event shape — are ordered together by their own timestamps against the
newest reaffirm. The newest closing signal's KIND names the bucket; a later
reaffirm out-ranks all of them. A self-status is dated at the decision's own
event time, so it is out-ranked by every genuine later repair and out-ranks
nothing written after it.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_write import atomic_write_text, atomic_write_json_locked  # noqa: E402
from entities_io import entities_collection  # noqa: E402
from event_time import event_time, parse_ts  # noqa: E402
from event_types import (  # noqa: E402
    decision_supersede_targets,
    decision_resolve_targets,
    decision_self_status,
)

# Date localization is canonical in tz.localize_date (TZDATE2 — the TZDATE1
# helper hoisted; this module's local copy is deleted). Every call site MUST
# pass workspace_path — see the tz.py docstring for the F-12 history. The
# UTC-slice stub below runs only when tz.py itself is missing (stripped
# install) and matches the old _HAS_TZ=False behavior exactly.
try:
    from tz import localize_date as _localize_date  # noqa: E402
except ImportError:
    def _localize_date(ts: str | None, workspace_path: str | None = None) -> str:
        return ts[:10] if isinstance(ts, str) and ts else ""


def _events_path(workspace_root: Path) -> Path:
    return workspace_root / "_hq" / "data" / "events.jsonl"


def _entities_path(workspace_root: Path) -> Path:
    return workspace_root / "_hq" / "data" / "entities.json"


def _view_path(workspace_root: Path) -> Path:
    return workspace_root / "_hq" / "views" / "DECISION_LOG.md"


def _load_events(events_path: Path) -> list[dict]:
    """Read events.jsonl tolerantly — skip malformed lines, accept string
    fragments as parse failures (they're the pre-atomic-write race artifacts
    documented in the substrate audit; this reader ignores them per the
    pattern `isinstance(obj, dict)` enforced everywhere else).
    """
    if not events_path.exists():
        return []
    out = []
    text = events_path.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _load_entities(entities_path: Path) -> dict[str, Any]:
    if not entities_path.exists():
        return {}
    try:
        return json.loads(entities_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _build_name_index(entities: dict) -> dict[str, str]:
    """Build a flat id → canonical_name index across people / orgs / projects."""
    idx = {}
    for p in entities.get("people", []):
        pid = p.get("id")
        if pid:
            idx[pid] = p.get("canonical_name") or pid
    for o in entities.get("orgs", []):
        oid = o.get("id")
        if oid:
            idx[oid] = o.get("canonical_name") or oid
    for proj in entities_collection(entities, "projects"):
        pid = proj.get("id")
        if pid:
            idx[pid] = (
                proj.get("display_name")
                or proj.get("canonical_name")
                or proj.get("folder_name")
                or pid
            )
    return idx


def _resolve_name(name_idx: dict[str, str], entity_id: str | None) -> str:
    """Name for an entity id, or "" when it cannot be resolved.

    JARGONVIEWS1: an id that is NOT in the index used to fall through to the
    raw id, so a decision attributed to a pruned / merged / not-yet-written
    entity rendered `person_003` into the customer's DECISION_LOG.md — the
    same leak class SUPERSEQ2 fixed for supersede pointers. The attribution is
    optional at the render site (`if decided_by:`), so an unresolvable id
    drops the attribution rather than printing an internal token: no
    attribution is honest, an id is noise the customer cannot act on.
    """
    if not entity_id:
        return ""
    return name_idx.get(entity_id, "")


def _categorize_decisions(events: list[dict]) -> dict[str, Any]:
    """Walk events, return:
      - decisions: list of decision events
      - supersedes_map: {original_seq: [{new_seq, reason, reviewed_at}]}
      - resolves_map: {decision_seq: [{reason, reviewed_at}]} (DECSHAPES1)
      - reaffirms_map: {decision_seq: [{reason, reviewed_at, snooze_until}]}
      - revisits_map: {decision_seq: [{snooze_until_ts, reason}]}
      - proposals_map: {decision_id: [{score, evidence, proposed_at}]}
        (WALKFIX1 FR-2 — proposed, never applied; status is untouched)
    """
    decisions: list[dict] = []
    supersedes_map: dict[Any, list[dict]] = {}
    # DECSHAPES1 — the EXECUTION closer. `decision_resolved` has existed in the
    # schema enum and been written by decision_match since v3.4.5, and this
    # renderer's four-bucket taxonomy had no bucket for it: a decision the
    # ledger says was carried out rendered under **Active**, indistinguishable
    # from one still awaiting execution. SUPERSEQ1 listed it (delta #7) and
    # left it to a taxonomy ruling.
    resolves_map: dict[Any, list[dict]] = {}
    reaffirms_map: dict[Any, list[dict]] = {}
    revisits_map: dict[Any, list[dict]] = {}
    # WALKFIX1 FR-2 — proposed supersedes, keyed by DECISION ID (a proposal
    # names the id; the seq on the event is the proposal's own).
    proposals_map: dict[Any, list[dict]] = {}

    for ev in events:
        t = ev.get("type")
        data = ev.get("data") or {}
        if t == "decision":
            decisions.append(ev)
            # SUPERSEQ1 (walk finding F-11, 2026-08-29) — a RESTAMP: a NEW
            # decision carrying `supersedes_seq` (top level per the schema, or
            # the data-scope drift twin) IS a supersede of the ruling at that
            # seq. The gate accepts the field on any event and
            # decision-revisit's contract documents it as THE link between the
            # two writes — but this reader never looked, so a ruling restamped
            # ONLY via the field rendered twice: old and new both active. Same
            # drift class as the 2026-08-13 finding below; the target chain
            # now lives ONCE, in event_types.decision_supersede_targets.
            _ids, restamp_seqs = decision_supersede_targets(ev)
            if restamp_seqs:
                row = {
                    "new_seq": ev.get("seq"),
                    "reason": data.get("supersede_reason") or "",
                    "reviewed_at": event_time(ev),
                }
                for target_seq in restamp_seqs:
                    supersedes_map.setdefault(target_seq, []).append(row)
            # DECSHAPES1 — the decision's OWN write-time status. `decision_match`
            # has always honored this field and this renderer never looked, so
            # the same ruling was closed for the matcher and ACTIVE in the
            # customer's view (SUPERSEQ1 delta #6 — reader/reader drift).
            #
            # It folds as a SIGNAL DATED AT THIS EVENT'S OWN TIME, into the
            # SAME latest-signal-wins ordering as every overlay closer — NOT as
            # a verdict. That is the whole ruling: honoring it unconditionally
            # would make it terminal (nothing rewrites an appended event, so no
            # repair could ever reach it), re-opening the exact defect WALKFIX1
            # FR-3 closed for supersede. Dated at its own time, a LATER reaffirm
            # out-ranks it and an EARLIER one does not.
            self_status = decision_self_status(ev)
            if self_status is not None:
                self_row = {
                    "new_seq": None,
                    "reason": data.get("supersede_reason") or "",
                    "reviewed_at": event_time(ev),
                    # Marks the row for the line renderer: there is no separate
                    # closing event to point at, so the line must not promise a
                    # replacement or a reviewer it cannot name.
                    "self_status": True,
                }
                bucket = (supersedes_map if self_status == "superseded"
                          else resolves_map)
                # Keyed under BOTH of the decision's names, because
                # `_overlay_rows` looks up by seq AND id and a decision with no
                # seq must still fold. It stays ONE signal: `_overlay_rows`
                # deduplicates by row identity (the SUPERSEQ1 default #3
                # posture, and this is literally the same row object twice).
                for key in (ev.get("seq"), _decision_id(ev)):
                    if key is not None:
                        bucket.setdefault(key, []).append(self_row)
        elif t == "decision_resolved":
            # DECSHAPES1 — the execution closer, now a rendered status. Target
            # vocabulary comes from the shared home, the SAME id + seq chains
            # the supersede closer reads: the gate accepts every one of those
            # spellings on this type too, and a reader narrower than the gate
            # is the drift class this build is closing (see
            # event_types.DECISION_RESOLVE_ID_CHAIN).
            resolve_row = {
                "reason": (data.get("resolution")
                           or data.get("reason", "")
                           or data.get("evidence", "")),
                "reviewed_at": data.get("resolved_at")
                or data.get("reviewed_at")
                or event_time(ev),
            }
            target_ids, target_seqs = decision_resolve_targets(ev)
            for did in target_ids:
                resolves_map.setdefault(did, []).append(resolve_row)
            for target_seq in target_seqs:
                resolves_map.setdefault(target_seq, []).append(resolve_row)
        elif t == "decision_superseded":
            # WRITER/READER FIELD DRIFT, root-caused 2026-08-13 against the
            # live ledger: 129 of 130 `decision_superseded` events key their
            # target by `data.decision_id` (shape `decision_seq_<n>`), and
            # exactly ONE uses the seq-shaped fields this reader was built for.
            # `orig_seq` was therefore None for 129 of them, the supersede was
            # dropped on the floor, and every plainly-superseded decision
            # rendered ACTIVE — with `Superseded (historical)` showing a count
            # of 1, which is literally that one seq-shaped event. Both spellings
            # now join; the id path is the CURRENT one.
            #
            # SUPERSEQ1 (2026-08-29): the target chain moved to
            # event_types.decision_supersede_targets — the shared vocabulary
            # home decision_match reads too — and gained the TOP-LEVEL
            # `supersedes_seq` limb the schema/gate accept (this reader only
            # knew the data-scope spelling; third instance of the class). One
            # event spelling its target several ways keys the map under every
            # spelling; it stays ONE supersede because `_overlay_rows`
            # deduplicates by row identity.
            new_seq = data.get("new_decision_seq")
            if new_seq in (None, ""):
                # The decision_match builder's cross-link spelling.
                new_seq = data.get("superseded_by_decision_seq")
            row = {
                "new_seq": new_seq,
                "reason": data.get("reason", "") or data.get("evidence", ""),
                "reviewed_at": data.get("reviewed_at") or event_time(ev),
            }
            target_ids, target_seqs = decision_supersede_targets(ev)
            for did in target_ids:
                supersedes_map.setdefault(did, []).append(row)
            for orig_seq in target_seqs:
                supersedes_map.setdefault(orig_seq, []).append(row)
        elif t == "decision_reaffirmed":
            # These writers DO emit `decision_event_seq` and are unaffected by
            # the drift above — which is why the 13 reaffirms from the same
            # repair rendered correctly and made the defect look era-split. The
            # id path is accepted here too so the same drift cannot recur
            # silently in this reader's other half.
            reaffirm_row = {
                "reason": data.get("reaffirmation_reason") or data.get("reason", ""),
                "reviewed_at": data.get("reviewed_at") or event_time(ev),
                "snooze_until": data.get("snooze_until"),
            }
            did = data.get("decision_id")
            if did:
                reaffirms_map.setdefault(did, []).append(reaffirm_row)
            decision_seq = data.get("decision_event_seq") or data.get("original_decision_seq")
            if decision_seq is not None:
                reaffirms_map.setdefault(decision_seq, []).append(reaffirm_row)
        elif t == "decision_supersede_proposed":
            # WALKFIX1 FR-2 — a PROPOSED supersede. It changes no status; it
            # rides on the decision's own line so the person who owns the
            # decision adjudicates it where the decision lives, instead of the
            # fire quietly closing it. Keyed by decision id, because a proposal
            # names the id (the seq belongs to the proposal event itself).
            did = data.get("decision_id")
            if did:
                proposals_map.setdefault(did, []).append({
                    "score": data.get("score"),
                    "evidence": data.get("evidence", ""),
                    "proposed_at": data.get("reviewed_at") or event_time(ev),
                })
        elif t == "decision_revisit_scheduled":
            revisit_row = {
                "snooze_until_ts": data.get("snooze_until_ts") or data.get("snooze_until"),
                "reason": data.get("reason", ""),
            }
            did = data.get("decision_id")
            if did:
                revisits_map.setdefault(did, []).append(revisit_row)
            decision_seq = data.get("decision_event_seq") or data.get("original_decision_seq")
            if decision_seq is not None:
                revisits_map.setdefault(decision_seq, []).append(revisit_row)

    return {
        "decisions": decisions,
        "supersedes_map": supersedes_map,
        "resolves_map": resolves_map,
        "reaffirms_map": reaffirms_map,
        "revisits_map": revisits_map,
        "proposals_map": proposals_map,
    }


# The floor a missing/unparseable timestamp sorts to. Aware, so it can be
# compared against any parsed instant without raising.
_EPOCH = datetime.datetime(1, 1, 1, tzinfo=datetime.timezone.utc)


def _instant(value):
    """A timestamp as an AWARE datetime, or None when it cannot be read.

    THE DEFECT THIS EXISTS FOR (WALKFIX1 fix round 2, C-3). The first cut of
    FR-3 compared `reviewed_at` values as raw STRINGS. `event_time()` returns
    the stored spelling verbatim — it does not normalize — and on a real
    ledger the two sides of this comparison are never in the same format:
    every `decision_superseded` this product writes is UTC `Z`, while every
    `decision_reaffirmed` a human appends from a Pacific machine is `-07:00`.
    Lexically `"2026-08-10T13:30:00-07:00" < "2026-08-10T19:46:28Z"`, so a
    repair made at 13:30 Pacific — genuinely 44 minutes AFTER the supersede —
    read as earlier and was silently ignored. The whole working afternoon
    (12:46–19:46 Pacific against that day's supersedes) failed that way, with
    nothing reporting anything: the decision simply stayed under Superseded
    and the operator would conclude the repair vocabulary was broken.

    `EVENT_TYPES.md` already required this — "Every reader that orders or
    filters events by time goes through `shared/scripts/event_time.py`" — and
    this module was not doing it.
    """
    return parse_ts(value)


def _sort_key(value):
    """Sort key for a timestamp: parsed instant, unreadable sorts oldest."""
    return _instant(value) or _EPOCH


def _newest(rows: list[dict], key: str) -> dict:
    """The newest row by `key`, treating a missing/unparseable value as oldest.

    Sorts on the PARSED instant, not the string — see `_instant`. A mixed-
    offset list is the normal case on a live ledger, not an edge case.
    """
    return sorted(rows, key=lambda r: _sort_key(r.get(key)), reverse=True)[0]


# Deterministic tie-break between the two CLOSING kinds when they carry the
# same instant (or both carry unreadable times). Higher wins.
#
# WHY SUPERSEDED WINS AN EXACT TIE. The two statements are not symmetric about
# the ruling's CURRENCY: "resolved" says this ruling was carried out and is
# done; "superseded" says a DIFFERENT ruling is now in force. If both are true
# of the same decision at the same instant, the reader's next question is
# "so what governs now?" — and only the superseded line answers it, by
# pointing at the replacement. Rendering the resolved badge there would close
# the row while silently dropping the pointer, which is the strictly
# lossier of the two readings. Exact ties are not expected on a real ledger
# (the two closers are written by different passes); this exists so the
# taxonomy is total, not because the case is common.
_CLOSING_KIND_RANK = {"resolved": 0, "superseded": 1}

# THE closed statuses, for consumers that filter the fold's output rather than
# render it (`load_thread_knowledge` excludes them from its decisions section).
# Exported so a SIXTH bucket is added in ONE place: a consumer that hard-codes
# `status == "superseded"` is exactly how the resolved bucket would have leaked
# carried-out rulings into a payload as live ones.
CLOSED_DECISION_STATUSES = frozenset({"superseded", "resolved"})

# The complementary set — a ruling still in force. `reaffirmed` and `snoozed`
# are OPEN states: the decision stands, it has merely been re-confirmed or
# scheduled for another look.
OPEN_DECISION_STATUSES = frozenset({"active", "reaffirmed", "snoozed"})


def _newest_closing(supersedes: list[dict],
                    resolves: list[dict]) -> tuple[str, dict]:
    """The winning closing signal as (kind, row) — `kind` is "superseded" or
    "resolved". At least one list must be non-empty.

    Ordered on the PARSED instant (see `_instant` — a mixed-offset ledger is
    the normal case), then on the kind rank above. Unreadable times sort
    oldest, exactly as `_newest` treats them, so a closer with no readable
    stamp loses to any dated one rather than winning by accident.
    """
    rows = ([("superseded", r) for r in supersedes]
            + [("resolved", r) for r in resolves])
    return sorted(
        rows,
        key=lambda kr: (_sort_key(kr[1].get("reviewed_at")),
                        _CLOSING_KIND_RANK[kr[0]]),
        reverse=True,
    )[0]


def _overlay_rows(bucket: dict, keys) -> list[dict]:
    """Every overlay row filed against ANY of a decision's keys — its seq and
    its id — deduplicated by identity.

    A decision is named two ways on a live ledger and the writers do not agree
    on which; a reader that knows only one of them silently drops the other's
    events, which is the defect this function exists to make impossible."""
    rows: list[dict] = []
    for key in keys:
        if key is None:
            continue
        for row in bucket.get(key) or []:
            if not any(row is seen for seen in rows):
                rows.append(row)
    return rows


def _decision_status(ev: dict, overlays: dict) -> tuple[str, dict[str, Any]]:
    """Return (status, overlay_data) for one decision.

    LATEST SIGNAL WINS between supersede and reaffirm (WALKFIX1 FR-3).

    THE DEFECT THIS REPLACES. Supersede used to be checked first and
    unconditionally, which made it TERMINAL: once anything referenced a
    decision as superseded, no later event in the canonical vocabulary could
    ever bring it back. A `decision_reaffirmed` written afterwards — the exact
    event a human reaches for when they read the log and disagree with it —
    changed nothing at all, silently. That is not a display preference; it
    means a WRONG supersede is unrepairable through the product's own
    vocabulary, and the 2026-08-10 past-meetings fire wrote up to 19 of them
    into a live ledger in a single run (WALKFIX1 Item A).

    So the two signals are now compared by their own `reviewed_at`:

      * reaffirm NEWER than the newest supersede  -> `reaffirmed`. The decision
        is back in the active view where its owner put it. The supersede is
        NOT deleted and NOT hidden — history is append-only, so it is carried
        on the overlay as `superseded_history` and rendered on the line, which
        is what makes this a repair rather than a cover-up.
      * supersede newer, or the reaffirm carries no readable time ->
        `superseded`, exactly as before. An undated reaffirm cannot out-rank a
        dated supersede: "unknown" must never read as "later".

    Everything else is unchanged: snooze still outranks a plain reaffirm, and
    a decision with no overlay at all is `active`.

    Note what this does NOT do: it does not decide anything by itself. It
    gives `decision_reaffirmed` — a marker some human or repair pass appends —
    the power it always looked like it had. The repair appends themselves are
    a workspace-side job, not this module's.
    """
    # The decision EVENT, so both of its names are available. A bare seq is
    # still accepted — that was this function's whole signature until FLOOR3
    # and the WALKFIX1 suite exercises the status fold through it — but a
    # caller passing one gets only the seq-keyed overlays, which is exactly
    # the half-blindness the id path exists to end. Pass the event.
    if not isinstance(ev, dict):
        keys = (ev, None)
    else:
        keys = (ev.get("seq"), _decision_id(ev))
    supersedes = _overlay_rows(overlays["supersedes_map"], keys)
    # DECSHAPES1 — `resolves_map` may be absent when a caller built overlays
    # with an older categorizer (or a test hand-rolls the dict); an empty
    # bucket is exactly "no resolve signal", so default rather than raise.
    resolves = _overlay_rows(overlays.get("resolves_map") or {}, keys)
    reaffirms = _overlay_rows(overlays["reaffirms_map"], keys)

    if supersedes or resolves:
        # DECSHAPES1 — supersede ("a later ruling replaced it") and resolve
        # ("it was carried out") are both CLOSING signals, and they compete
        # with each other on the same axis they each already compete with a
        # reaffirm on: whichever was written last is what the ruling is now.
        # So the two are ordered together and the WINNER'S KIND names the
        # bucket — not a fixed precedence, which would let a stale signal
        # out-rank a fresh one purely by type.
        latest_close = _newest_closing(supersedes, resolves)
        latest_re = _newest(reaffirms, "reviewed_at") if reaffirms else None
        # Parsed instants, never raw strings — a `Z` supersede and a `-07:00`
        # reaffirm are the NORMAL shapes on a live ledger, and comparing them
        # lexically silently drops real repairs (see `_instant`).
        close_kind, latest_row = latest_close
        sup_at = _instant(latest_row.get("reviewed_at"))
        re_at = _instant((latest_re or {}).get("reviewed_at"))
        if not (re_at is not None and sup_at is not None and re_at > sup_at):
            return (close_kind, latest_row)
        # A later reaffirm restores the decision, and the closing signal rides
        # along so the line can say what was reversed and when.
        overlay = dict(latest_re)
        overlay["superseded_history"] = latest_row
        overlay["restored_from"] = close_kind
        return ("reaffirmed", overlay)

    revisits = _overlay_rows(overlays["revisits_map"], keys)
    if revisits:
        # Same class as `_instant` — ordered on the parsed instant, because a
        # revisit written from a local machine and one written in UTC are
        # not comparable as strings.
        latest = sorted(revisits,
                        key=lambda r: _sort_key(r.get("snooze_until_ts")),
                        reverse=True)[0]
        return ("snoozed", latest)

    if reaffirms:
        return ("reaffirmed", _newest(reaffirms, "reviewed_at"))

    return ("active", {})


# How much of an evidence string can stand in for a title. Long enough to be
# recognisable in a list, short enough that a row is still one line.
_TITLE_FROM_EVIDENCE_CHARS = 120

# How much of the SUPERSEDING decision's title rides on the superseded line's
# cross-link. Tighter than the row's own title budget: the cross-link is a
# pointer, not the row's subject, and two full-width titles on one line is a
# paragraph wearing a bullet's clothes.
_CROSSLINK_TITLE_CHARS = 80


def _seq_title_index(decisions: list[dict]) -> dict[Any, tuple[str, str]]:
    """seq → (title, decided_at_ts) for every decision in the stream.

    SUPERSEQ2 (2026-08-31). The superseded section used to render its
    cross-link as the raw internal token — "replaced by seq 12765" — in a
    customer-facing view. A seq number is substrate plumbing: the customer
    reading DECISION_LOG.md cannot look one up, and the house posture is that
    internal vocabulary never reaches a customer surface (the jargon guard
    enforces it on release manifests; this view simply predated the rule).

    The decisions are already all in hand when the view renders, so the fix
    is a lookup, not a new read: resolve the superseding seq to that
    decision's own TITLE (same `_decision_title` derivation its own row uses)
    and its decided_at, and render those instead. The timestamp is stored raw
    here and localized at the render site, like every other date on a line.
    """
    idx: dict[Any, tuple[str, str]] = {}
    for d in decisions:
        seq = d.get("seq")
        if seq is None:
            continue
        data = d.get("data") or {}
        idx[seq] = (_decision_title(data),
                    data.get("decided_at") or event_time(d))
    return idx


def _crosslink_text(new_seq: Any,
                    seq_index: dict[Any, tuple[str, str]] | None,
                    workspace_path: str | None) -> str:
    """The superseded line's replaced-by fragment, customer-readable.

    Resolved  → ` — replaced by "New ruling title" (2026-08-30)` — the seq is
                DROPPED entirely: the title plus date is the anchor a reader
                can actually use, and the raw number adds nothing they can
                act on (reviewer-visible default).
    Unresolved (the target decision is not in the stream — cross-workspace
                repair, pruned ledger, or a writer's bad pointer)
              → ` — replaced by a later ruling (record 12765)` — honest about
                the gap without leaning on internal vocabulary; "record N" is
                the neutral spelling a customer can quote back at support
                (reviewer-visible default).
    """
    resolved = (seq_index or {}).get(new_seq)
    if resolved:
        title, decided_ts = resolved
        if len(title) > _CROSSLINK_TITLE_CHARS:
            title = title[:_CROSSLINK_TITLE_CHARS].rstrip() + "…"
        when = _localize_date(decided_ts, workspace_path)
        return (f' — replaced by "{title}"'
                + (f" ({when})" if when else ""))
    return f" — replaced by a later ruling (record {new_seq})"


def _decision_title(data: dict) -> str:
    """The line's heading, from whichever field this era's writer filled.

    THE DEFECT THIS REPLACES: the chain read `title` then `decision` only, and
    the writer moved to `data.summary` around mid-May 2026. On the live ledger
    that rendered 415 of 648 rows as `(untitled decision)` — a regenerated view
    nobody could scan, and one the chat reply masked by summarising from the
    ledger instead of from the view it had just written.

    `summary` is preferred over `decision` because it is the CURRENT writer's
    field; `evidence` is a last resort that at least says what the decision was
    about. The literal fallback stays for a decision event carrying none of
    them, because a row with no words at all is worse than a labelled gap."""
    data = data or {}
    for field in ("title", "summary", "decision"):
        value = str(data.get(field) or "").strip()
        if value:
            return value
    evidence = str(data.get("evidence") or "").strip()
    if evidence:
        if len(evidence) > _TITLE_FROM_EVIDENCE_CHARS:
            return evidence[:_TITLE_FROM_EVIDENCE_CHARS].rstrip() + "…"
        return evidence
    return "(untitled decision)"


def _decision_id(ev: dict) -> str:
    """Stable id for a decision — the same derivation `decision_match` uses,
    including its `decision_seq_<seq>` fallback, so a proposal written by the
    matcher joins to the decision it names."""
    d = ev.get("data") or {}
    return d.get("id") or ev.get("id") or f"decision_seq_{ev.get('seq', '?')}"


def _format_decision_line(
    ev: dict,
    status: str,
    overlay: dict,
    name_idx: dict[str, str],
    proposals: list[dict] | None = None,
    workspace_path: str | None = None,
    seq_index: dict[Any, tuple[str, str]] | None = None,
) -> str:
    """Return one markdown line for a decision. Format:

        - **<title>** — <decided_by_name>, <decided_at_date> [<status_badge>] <status_extras>
          <rationale_first_sentence>

    All IDs resolved to display names. No raw person_NNN / org_NNN / project_NNN
    in user-facing output (per CONTRACT Rule 4).
    """
    data = ev.get("data") or {}
    title = _decision_title(data)
    decided_by_id = data.get("decided_by") or data.get("decided_by_id") or ev.get("person_id")
    decided_by = _resolve_name(name_idx, decided_by_id) if decided_by_id else ""
    decided_at = _localize_date(data.get("decided_at") or event_time(ev), workspace_path)
    rationale = data.get("rationale") or data.get("reasoning") or data.get("context") or ""
    # Trim rationale to ~120 chars for the inline summary
    if isinstance(rationale, str) and len(rationale) > 200:
        rationale = rationale[:200].rstrip() + "…"

    # Status badge
    badge = ""
    extras = ""
    if status == "superseded":
        badge = "[SUPERSEDED]"
        new_seq = overlay.get("new_seq")
        if new_seq is not None:
            # SUPERSEQ2 — the cross-link names the superseding decision, not
            # its internal seq token; see `_crosslink_text` for the shapes.
            extras = _crosslink_text(new_seq, seq_index, workspace_path)
        elif overlay.get("self_status"):
            # DECSHAPES1 — a ruling marked superseded on its OWN record. There
            # is no successor event to resolve, so the line says that plainly
            # instead of leaving a bare badge the reader cannot account for.
            # It must NOT borrow the "replaced by …" wording: nothing here
            # names a replacement, and inventing one would be the same
            # dishonesty `_crosslink_text`'s unresolved branch exists to avoid.
            extras = " — marked superseded on the record"
        if overlay.get("reason"):
            extras += f" ({overlay['reason'][:100]})"
    elif status == "resolved":
        # DECSHAPES1 — the execution closer. No cross-link: nothing replaced
        # this ruling, it was carried out, so the line says when and why
        # rather than pointing at a successor that does not exist.
        badge = "[RESOLVED]"
        reviewed_at = overlay.get("reviewed_at")
        if reviewed_at:
            extras = f" — closed out {_localize_date(reviewed_at, workspace_path)}"
        if overlay.get("reason"):
            extras += f" ({overlay['reason'][:100]})"
    elif status == "snoozed":
        badge = "[SNOOZED]"
        snooze_until = overlay.get("snooze_until_ts")
        if snooze_until:
            extras = f" — revisit after {_localize_date(snooze_until, workspace_path)}"
        if overlay.get("reason"):
            extras += f" ({overlay['reason'][:100]})"
    elif status == "reaffirmed":
        badge = "[REAFFIRMED]"
        reviewed_at = overlay.get("reviewed_at")
        if reviewed_at:
            extras = f" — reviewed {_localize_date(reviewed_at, workspace_path)}"
        snooze_until = overlay.get("snooze_until")
        if snooze_until:
            extras += f", revisit after {_localize_date(snooze_until, workspace_path)}"
        # WALKFIX1 FR-3 — a reaffirm that RESTORED a superseded decision says
        # so. History is append-only, so the supersede it out-ranked is still
        # a fact about this decision and the line carries it; hiding it would
        # make the restore look like the supersede never happened.
        history = overlay.get("superseded_history")
        if isinstance(history, dict):
            when = _localize_date(history.get("reviewed_at") or "", workspace_path)
            # DECSHAPES1 — the out-ranked closer may now be a RESOLVE as well
            # as a supersede, and the line must name which one was reversed:
            # "restored after a supersede" on a ruling that was actually
            # marked carried-out would misdescribe the history it exists to
            # preserve. Default stays "supersede" for overlays built before
            # this field existed.
            what = ("a close-out" if overlay.get("restored_from") == "resolved"
                    else "a supersede")
            extras += (f" — restored after {what}"
                       + (f" of {when}" if when else ""))
    # active gets no badge — it's the default

    parts = [f"- **{title}**"]
    meta = []
    if decided_by:
        meta.append(decided_by)
    if decided_at:
        meta.append(decided_at)
    if meta:
        parts.append(" — " + ", ".join(meta))
    if badge:
        parts.append(f" {badge}{extras}")

    # WALKFIX1 FR-2 — an open supersede PROPOSAL rides on the decision's own
    # line. It changes no status (the decision keeps whatever badge it had);
    # it is a question waiting for the person who owns the decision, put where
    # they will actually see it. Newest proposal only — a queue of them on one
    # line is a list wearing a sentence's clothes.
    if proposals:
        # Same class again (WALKFIX1 fix round 2 sweep): parsed, not lexical.
        newest = sorted(proposals,
                        key=lambda p: _sort_key(p.get("proposed_at")),
                        reverse=True)[0]
        when = _localize_date(newest.get("proposed_at") or "", workspace_path)
        score = newest.get("score")
        bits = []
        if when:
            bits.append(when)
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            bits.append(f"match {score:.2f}")
        detail = f" ({', '.join(bits)})" if bits else ""
        parts.append(f" [SUPERSEDE PROPOSED]{detail}")

    line = "".join(parts)

    if rationale:
        # Indent rationale under the bullet
        line += "\n  " + rationale

    return line


def _build_content(workspace_root: Path) -> tuple[str, dict[str, Any]]:
    """Build the DECISION_LOG.md content + counts WITHOUT writing.

    Factored out of regenerate() so the changed-only path
    (regenerate_if_changed) can compare candidate content against the existing
    view before deciding whether to write (SPEC CLEAN1 / D4 idempotence)."""
    events_path = _events_path(workspace_root)
    entities_path = _entities_path(workspace_root)

    events = _load_events(events_path)
    entities = _load_entities(entities_path)
    name_idx = _build_name_index(entities)
    overlays = _categorize_decisions(events)
    # SUPERSEQ2 — the supersede cross-links resolve against the decisions
    # already walked above; no second read of the ledger.
    seq_index = _seq_title_index(overlays["decisions"])

    # Group decisions by status
    by_status: dict[str, list[tuple[dict, str, dict]]] = {
        "active": [],
        "superseded": [],
        "resolved": [],
        "reaffirmed": [],
        "snoozed": [],
    }
    for d in overlays["decisions"]:
        status, overlay = _decision_status(d, overlays)
        by_status[status].append((d, status, overlay))

    # Sort each bucket newest-first by decided_at
    def _decided_at_key(d_tuple):
        d, _, _ = d_tuple
        data = d.get("data") or {}
        return data.get("decided_at") or event_time(d)

    for k in by_status:
        by_status[k].sort(key=_decided_at_key, reverse=True)

    # Build the document
    now_iso = datetime.datetime.now().replace(microsecond=0).isoformat()
    total = sum(len(v) for v in by_status.values())
    lines = [
        "<!-- AUTO-GENERATED by shared/scripts/render_decision_log.py — do not edit by hand. -->",
        f"<!-- regenerated-at: {now_iso} -->",
        f"<!-- total-decisions: {total} -->",
        f"<!-- source: _hq/data/events.jsonl + entities.json -->",
        "",
        "# Decision Log",
        "",
        f"_{total} decisions total · regenerated {now_iso}_",
        "",
        f"- **{len(by_status['active'])} active** — current, not closed or snoozed",
        f"- **{len(by_status['reaffirmed'])} reaffirmed** — explicitly re-confirmed",
        f"- **{len(by_status['snoozed'])} snoozed** — revisit scheduled later",
        f"- **{len(by_status['resolved'])} resolved** — carried out and closed",
        f"- **{len(by_status['superseded'])} superseded** — replaced by a later decision",
        "",
        "---",
        "",
    ]

    for status_label, status_title in [
        ("active", "Active"),
        ("reaffirmed", "Reaffirmed"),
        ("snoozed", "Snoozed"),
        # DECSHAPES1 — resolved sits between the live buckets and the
        # historical one: these rulings were carried out (a closed job, not a
        # reversed one), so they read after what is still open and before what
        # was replaced.
        ("resolved", "Resolved (closed out)"),
        ("superseded", "Superseded (historical)"),
    ]:
        bucket = by_status[status_label]
        if not bucket:
            continue
        lines.append(f"## {status_title} ({len(bucket)})")
        lines.append("")
        for d, status, overlay in bucket:
            lines.append(_format_decision_line(
                d, status, overlay, name_idx,
                overlays["proposals_map"].get(_decision_id(d)),
                workspace_path=str(workspace_root),
                seq_index=seq_index))
            lines.append("")
        lines.append("")

    content = "\n".join(lines).rstrip() + "\n"

    counts = {
        "total": total,
        "active": len(by_status["active"]),
        "reaffirmed": len(by_status["reaffirmed"]),
        "snoozed": len(by_status["snoozed"]),
        "resolved": len(by_status["resolved"]),
        "superseded": len(by_status["superseded"]),
    }
    return content, counts


# Header lines that change on every render (timestamps) even when the decision
# content is identical. Stripped before the changed-only comparison so a quiet
# workspace is a true no-op write.
def _strip_volatile(text: str) -> str:
    out = []
    for line in text.splitlines():
        if "regenerated-at:" in line:
            continue
        if line.startswith("_") and "· regenerated " in line:
            continue
        out.append(line)
    return "\n".join(out)


def regenerate(workspace_root: str | Path) -> dict[str, Any]:
    """Read events.jsonl + entities.json, build the view, atomic-write to
    _hq/views/DECISION_LOG.md. Returns a counts dict.

    The renderer is the canonical owner of `_hq/views/DECISION_LOG.md`
    (v3.13.0+ — pre-v3.13.0 the file was hand-edited or stale). Any skill
    that writes a decision-related event should call this after the write
    so the view never falls behind.
    """
    workspace_root = Path(workspace_root)
    view_path = _view_path(workspace_root)
    content, counts = _build_content(workspace_root)

    # Atomic write — no JSON lock needed (this is a .md view file, not the substrate),
    # but use atomic_write_text for fsync + rename safety.
    view_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(view_path, content)

    counts["view_path"] = str(view_path)
    return counts


def regenerate_if_changed(workspace_root: str | Path) -> dict[str, Any]:
    """Changed-only regeneration (SPEC CLEAN1 / D4). Build the candidate view,
    compare it (ignoring volatile timestamp lines) against what's on disk, and
    write ONLY if the decision content actually changed.

    cleanup calls this every weekly run so a missed decision-log regen never
    persists for weeks — while a workspace with no new decisions stays a true
    no-op write (the idempotence guarantee, acceptance #7). Returns the counts
    dict plus `changed` (bool: did the content differ / was a write made)."""
    workspace_root = Path(workspace_root)
    view_path = _view_path(workspace_root)
    content, counts = _build_content(workspace_root)

    old = view_path.read_text(encoding="utf-8") if view_path.exists() else ""
    changed = _strip_volatile(old) != _strip_volatile(content)
    if changed:
        view_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(view_path, content)

    counts["changed"] = changed
    counts["view_path"] = str(view_path)
    return counts


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python3 render_decision_log.py <workspace_root>", file=sys.stderr)
        return 2
    workspace_root = Path(argv[1])
    if not workspace_root.exists():
        print(f"ABORT: workspace not found: {workspace_root}", file=sys.stderr)
        return 2
    result = regenerate(workspace_root)
    print(f"OK — regenerated DECISION_LOG.md")
    print(f"  total: {result['total']}")
    print(f"  active: {result['active']}")
    print(f"  reaffirmed: {result['reaffirmed']}")
    print(f"  snoozed: {result['snoozed']}")
    print(f"  resolved: {result['resolved']}")
    print(f"  superseded: {result['superseded']}")
    print(f"  written to: {result['view_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
