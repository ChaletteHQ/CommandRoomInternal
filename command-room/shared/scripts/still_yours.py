#!/usr/bin/env python3
"""The "Still yours?" review: clear the late pile once (cr1#1706).

WHY THIS EXISTS
The Work tab showed about 170 late items, many four to eight weeks old and
several on a line of work the owner had retired. With everything late
nothing reads as urgent. This is a one-time review of every open item over
`REVIEW_DAYS` days old: Keep, Done or Drop per row, and one press that drops
every open item on a retired (archived) project. Every press is recorded and
has an Undo that puts the item back exactly as it was.

WHY THE AGE-OUT RULE DID NOT DO THIS (the evidence half of cr1#1706)
The owner's setting `commitment policy: exit.silence age out: on` drives the
silence door (`exit_doors.silence_candidates` / `run_silence_pass`), which
the Maintenance `exit-doors` job runs. That door never touches a promise
with a due date, by design: "a dated row is a promise with a deadline and
belongs to the overdue rules, not to silence", and it never rests an
overdue item or one due this week. Every capture carries a date or an
explicit `no_due` (`capture_gate` S2 due-nudge), and a meeting extraction
proposes one from what was said, so a late meeting promise is dated and the
door passes over it. It also leaves alone a capture still waiting for the
owner's yes (`pending_review`, exit_doors S-3), and it acts at 45 days (rest)
and 60 days (let go), not 30. So age-out is source-blind, and it covers an
undated, confirmed meeting promise exactly as it covers any other; what it
does not cover is the late pile, because the late pile is dated.
`tests/run_still_yours_test.py` reproduces this on a synthetic book.

WHAT THIS MODULE OWNS
No record. Every write goes through the writer that already owns it:

  * Done:   `cockpit_helpers.apply_commitment_choices` verb `done`, the Hub's
            own close (`close_commitment`, resolution done);
  * Drop:   the same writer, verb `drop` (resolution dropped);
  * Keep:   `commitment_state.add_commitment_note`, one note on the item in
            the owner's word (`KEEP_NOTE`). The note is the record that it
            was reviewed, so it leaves the review list; the item itself is
            not changed;
  * Undo of Done or Drop: the same writer, verb `reopen`, naming the close
            it takes back (`back_of` + `back_seq`): an item closed again
            since is left as it is;
  * Undo of Keep: `brain_undo.undo_batch` on the note's own batch, the
            registered `commitment_note` reverser (the note stays in
            history, retracted).

ONE WRITE PER APPLY (review round 1, MUST 1)
Every Done, Drop and project drop of one Apply goes to the Hub's writer in
ONE call, so its one-write close path (cr1#1608,
`commitment_state.close_commitments_one_write`) lands them all in one locked
write under one audit row. Every Undo reopen of one Apply is one call too.
The review is read once for the presses of an Apply and kept up to date in
memory as they are taken, never rebuilt after each write.

THE FENCE
Every press is checked against the review as the book stands when the Apply
reads it (`review`), never against what a page sent: an item must be open,
confirmed, over the age bar and not already kept or pressed in the same
Apply; a project press must name an archived project with open items. An
item on the personal lane, or with a person the owner put off limits on it,
is never listed and every press on it is refused (`hub_work.fence`, the Work
tab's own); so is every Undo on it. A wall, an owner or a book that cannot be
read refuses every press and every Undo and writes nothing. An Undo reopens
only a close this module wrote (its batch label, in this module's own
format, on a close by the Hub's writer) and takes back only one Keep note.

stdlib only. Nothing here sends anything.
"""
from __future__ import annotations

import datetime as _dt
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

#: An item is on the review once it has been open MORE than this many days.
REVIEW_DAYS = 30
#: The presses a review row takes, and the Undo of each.
VERBS = frozenset({"keep", "done", "drop", "drop_project", "undo", "refresh"})
#: cr1#1706 page part: the review's own small document, built by a writer
#: (Maintenance's open-list job once a workspace day, every review Apply, and
#: the page's `refresh` press when the review opens) and only READ by the
#: Hub refresh's `feed/stillyours`, never inside `feed/plate`.
NOW_REL = "_hq/data/now/still_yours/still_yours.json"
#: The oldest this many rows travel in the document; `n` is the whole count.
FEED_ROWS_CAP = 100
#: A project's ids travel up to this many (its press is refused past 200).
FEED_PROJECT_IDS_CAP = 201
REFRESH_LINE = "The review is up to date."
#: The note a Keep writes. Its exact words are how a kept item is told apart.
KEEP_NOTE = "Still yours: kept in the review on the Command Room Hub."
SOURCE_SKILL = "cockpit"
#: The batch label on every close this review writes: the prefix, then the
#: Apply's instant with the colons taken out (`_label`). An Undo reopens only
#: a close whose label has exactly this shape (review round 1, NOTE 6).
BATCH_PREFIX = "stillyours:"
LABEL_RE = re.compile(r"^stillyours:\d{4}-\d{2}-\d{2}T\d{6}(?:\.\d{1,6})?(?:[+-]\d{4}|Z)?$")
#: The writer a close of this review is made by (`cockpit_helpers.COMMITMENT_SRC`).
CLOSE_SOURCE = "commitment-triage"
UNDO_SOURCE = "commitment-triage"
#: How deep a thread's parents are walked to find an archived project.
PARENT_DEPTH_CAP = 8
#: The most items one project press may drop. Past this, nothing is written.
PROJECT_DROP_CAP = 200
#: The most Undo tokens one Undo row may carry (a project press's, at most).
UNDO_TOKEN_CAP = PROJECT_DROP_CAP

UNREADABLE_LINE = ("The review could not be checked just now (your book, your owner or who is off limits "
                   "could not be read), so nothing was written. Press Go, then try again.")
NOT_LISTED_LINE = "That one is not on the review any more, so it was left as it is; nothing was written."
HELD_LINE = "That one is personal or about someone you put off limits, so it is not changed from here; nothing was written."
NO_PROJECT_LINE = "Nothing open on that project can be dropped from here (it is not archived, or nothing is open on it); nothing was written."
TOO_MANY_LINE = "That project has more than {cap} open items, so nothing was dropped. Say it in a chat."
BAD_VERB_LINE = "That press is not one the review takes; nothing was written."
BAD_UNDO_LINE = "That Undo does not name a press of this review; nothing was written."
KEEP_LINE = "Kept. It stays on your list and leaves the review."
DONE_LINE = "Marked done."
DROP_LINE = "Dropped."
PROJECT_LINE = "Dropped {n} open item{s} on {name}."
PROJECT_LEFT_LINE = " {n} more on it stay open ({why}); they are not on the review."
PROJECT_NONE_LINE = "Nothing on {name} could be dropped just now; nothing was written."
PROJECT_FAILED_LINE = " {n} could not be dropped ({why})."
#: Fix round 3 (MUST 1): a failed row's reason inside a line, as a short
#: clause, never a whole sentence (whose "nothing was written" would sit
#: beside writes that did land).
WHY_SUBITEMS = "it has open sub-items"
WHY_NOT_WRITTEN = "it could not be written"
#: A row the Hub's writer gave no answer for, and whose close is not on the log.
NOT_WRITTEN_LINE = "That one could not be written just now, so it was left as it is. Press Apply again."
#: Fix round 4 (NOTE 2): the Hub's writer gave no answer and the log could not
#: be read to see what landed, so the press can be neither claimed nor denied.
UNCONFIRMED_LINE = "That one could not be confirmed just now; look at the item before you press again."
PROJECT_UNCONFIRMED_LINE = "The drop on {name} could not be confirmed just now; look at its items before you press again."
PROJECT_SOME_UNCONFIRMED_LINE = " {n} more could not be confirmed just now."
#: The feed answers this when the wall cannot be read when it is read.
FEED_UNREADABLE = "unreadable"
UNDO_FAILED_LINE = "That could not be put back just now, so it was left as it is. Press Apply again."
NO_UNDO_LINE = " Its Undo is not available here; reopen it from its own row."
PROJECT_NO_UNDO_LINE = " {n} of them have no Undo here; reopen those from their own rows."
UNDO_LINE = "Put back as it was."
UNDO_SOME_LINE = "Put back {n} of {total}; the rest were already put back or changed since, so they were left as they are."
UNDO_ALREADY_LINE = "That was already undone; nothing was written."
UNDO_MOVED_LINE = "That one was changed again after the press this Undo is for, so it was left as it is; nothing was written."
UNDO_HELD_LINE = ("That one is personal or about someone you put off limits now, so it is not put back from here; "
                  "nothing was written.")


def _iso(now: _dt.datetime) -> str:
    return now.isoformat()


def _label(now: _dt.datetime) -> str:
    return f"{BATCH_PREFIX}{_iso(now).replace(':', '')}"


def _threads(root: Path) -> Optional[Dict[str, Dict[str, Any]]]:
    """`{thread id: record}`, or None when the book cannot be read."""
    try:
        from entities_io import entities_collection, read_entities_json

        doc = read_entities_json(root / "_hq" / "data" / "entities.json")
        recs = entities_collection(doc, "threads")
    except Exception:  # noqa: BLE001 - unreadable is no answer
        return None
    if not isinstance(recs, list):
        return None
    return {str(t["id"]): t for t in recs if isinstance(t, dict) and t.get("id")}


def _retired_project(tid: str, threads: Dict[str, Dict[str, Any]]) -> str:
    """The archived project `tid` sits on (itself or a parent), or ""."""
    seen = set()
    cur = str(tid or "")
    for _ in range(PARENT_DEPTH_CAP):
        if not cur or cur in seen or cur not in threads:
            return ""
        seen.add(cur)
        rec = threads[cur]
        if str(rec.get("status") or "").strip().lower() == "archived":
            return cur
        cur = str(rec.get("parent_thread_id") or "")
    return ""


def kept_ids(events: List[Dict[str, Any]]) -> set:
    """The items with a standing Keep note (a taken-back one does not count)."""
    from commitment_state import commitment_notes

    ids = {str((e.get("data") or {}).get("commitment_id") or "") for e in events
           if isinstance(e, dict) and e.get("type") == "note"
           and str((e.get("data") or {}).get("summary") or "") == KEEP_NOTE}
    return {cid for cid in ids - {""}
            if any(n["text"] == KEEP_NOTE for n in commitment_notes(events, cid))}


def review(workspace_root, now_iso=None, *, days: int = REVIEW_DAYS) -> Dict[str, Any]:
    """READ. `{ok, days, rows, projects, n, n_held}`.

    `rows`: each open, confirmed item open more than `days` days, oldest
    first, not kept, not walled: `{id, title, age_days, direction, due,
    thread_id, project_id}` (`project_id` is the archived project it sits
    on, else ""). `n_held`: how many items over the bar the fence kept out
    (personal, or about someone off limits). `projects`: each archived
    project with open items, `{project_id, name, ids, left_open}`, where
    `ids` is EVERY open confirmed item on it that the fence lets through,
    whatever its age, and `left_open` counts what a project press leaves
    open there: `{snoozed, unconfirmed, held}`. `ok: False` (and nothing
    listed) when the owner, the wall or the book cannot be read. Writes
    nothing, never raises."""
    return _review_full(workspace_root, now_iso, days=days)[0]


def _review_full(workspace_root, now_iso=None, *, days: int = REVIEW_DAYS) -> Tuple[Dict[str, Any], set]:
    """`review`'s answer and the ids the fence held (for a press's wording)."""
    held: set = set()
    out: Dict[str, Any] = {"ok": False, "days": int(days), "rows": [], "projects": [], "n": 0, "n_held": 0}
    try:
        import cockpit_helpers as ch
        import hub_work
        import open_list as ol
        from events_io import load_events_owner_scoped

        root = Path(str(workspace_root))
        now = ch._now(now_iso)
        if not ch._owner_id(root):
            return out, held
        wall = hub_work.fence(root)
        threads = _threads(root)
        if wall is None or threads is None:
            return out, held
        events = [e for e in load_events_owner_scoped(str(root))[0] if isinstance(e, dict)]
        kept = kept_ids(events)
        # One read of the open list serves both halves: the review's rows
        # (confirmed, not snoozed) and what a project press leaves open.
        every = ol.query(root, sort="age", include_unconfirmed=True, include_snoozed=True, now_iso=_iso(now))["rows"]
        # cr1#1729: the late items the age-out door offers (overdue, quiet past
        # its threshold) are on the review whatever their age; the door never
        # closes them itself.
        import exit_doors

        late_quiet = {str(r["commitment_id"]): r for r in
                      exit_doors.silence_candidates(str(root), now_iso=_iso(now), events=events).get("review") or []}
        rows: List[Dict[str, Any]] = []
        projects: Dict[str, List[str]] = {}
        left: Dict[str, Dict[str, int]] = {}
        parties: Dict[str, Dict[str, Any]] = {}
        n_held = 0
        for r in every:
            cid = str(r.get("id") or "")
            if not cid or r.get("observed"):
                continue
            proj = _retired_project(str(r.get("thread_id") or ""), threads)
            age = r.get("age_days")
            over = isinstance(age, int) and not isinstance(age, bool) and age > int(days)
            if hub_work._held(r, wall):
                held.add(cid)
                if over and r.get("confirmed") and not r.get("snoozed"):
                    n_held += 1
                if proj:
                    left.setdefault(proj, {"snoozed": 0, "unconfirmed": 0, "held": 0})["held"] += 1
                continue
            if not r.get("confirmed") or r.get("snoozed"):
                if proj:
                    why = "unconfirmed" if not r.get("confirmed") else "snoozed"
                    left.setdefault(proj, {"snoozed": 0, "unconfirmed": 0, "held": 0})[why] += 1
                continue
            parties[cid] = {"owner_id": str(r.get("owner_id") or ""),
                            "counterparty_ids": [str(x) for x in r.get("counterparty_ids") or []]}
            if proj:
                projects.setdefault(proj, []).append(cid)
            if cid in kept or not over:
                continue
            rows.append({"id": cid, "title": str(r.get("title") or ""), "age_days": age,
                         "direction": str(r.get("direction") or ""), "due": str(r.get("due") or ""),
                         "thread_id": str(r.get("thread_id") or ""), "project_id": proj,
                         # why it is here: open more than the bar, or late and
                         # quiet past the age-out threshold (cr1#1729)
                         "why": "late_quiet" if cid in late_quiet else "old",
                         **({"days_quiet": int(late_quiet[cid]["days_quiet"])} if cid in late_quiet else {})})
        out["rows"] = rows
        out["projects"] = [{"project_id": pid, "name": str(threads[pid].get("canonical_name") or pid),
                            "ids": sorted(ids),
                            "left_open": dict(left.get(pid) or {"snoozed": 0, "unconfirmed": 0, "held": 0})}
                           for pid, ids in sorted(projects.items())]
        out["n"] = len(rows)
        out["n_held"] = n_held
        # fix round 4: who is on each listed item, so the feed can hold the
        # wall to them again when it is read (never shown on the page).
        listed_ids = {r["id"] for r in rows} | {x for ids in projects.values() for x in ids}
        out["parties"] = {k: v for k, v in parties.items() if k in listed_ids}
        out["ok"] = True
    except Exception:  # noqa: BLE001 - fail closed: nothing listed
        out.update(ok=False, rows=[], projects=[], n=0, n_held=0, parties={})
        held = set()
    return out, held


def _left_words(left: Dict[str, int]) -> Tuple[int, str]:
    parts = [f"{n} {w}" for w, n in (("snoozed", left.get("snoozed", 0)), ("waiting for your yes", left.get("unconfirmed", 0)),
                                     ("personal or off limits", left.get("held", 0))) if n]
    return sum(int(left.get(k, 0)) for k in ("snoozed", "unconfirmed", "held")), ", ".join(parts)


def _people(ev: Dict[str, Any]) -> set:
    from commitment_parties import counterparty_ids
    from cru_match import _commitment_field

    return ({str(_commitment_field(ev, "owner_id") or "")} | {str(p) for p in counterparty_ids(ev) or []}) - {""}


def _why_clause(ans: Dict[str, Any]) -> str:
    """The short reason a close was not written, for inside a line."""
    try:
        import cockpit_helpers as ch

        if ans.get("line") == ch.SUBITEMS_LINE or ans.get("reason") == "open_subitems":
            return WHY_SUBITEMS
    except Exception:  # noqa: BLE001
        pass
    return WHY_NOT_WRITTEN


def _landed_closes(root: Path, cids: set, label: str) -> Dict[str, int]:
    """`_landed_closes_read`'s map alone."""
    return _landed_closes_read(root, cids, label)[0]


def _landed_closes_read(root: Path, cids: set, label: str) -> Tuple[Dict[str, int], bool]:
    """`{item: seq}` of the closes THIS Apply landed (its own batch label, by
    the Hub's writer) and still standing, from one read of the log (fix round
    2, MUST 1). The Hub's writer answers a close without its seq when its
    one-write path raised and it fell back to single closes, and a raise can
    leave some closes landed with no answer at all; the log is the record of
    what was written, so it is asked. Never raises.

    Fix round 3, NOTE 5 (accepted, stated): a close made by the Hub's writer
    under this very Apply's label is claimed as this review's own. The label
    is minted per Apply from its instant, so only a call that reuses the same
    label at the same second can wear it; the source check keeps every other
    writer's close out."""
    out: Dict[str, int] = {}
    if not cids:
        return out, True
    try:
        from events_io import load_events_owner_scoped

        for e in load_events_owner_scoped(str(root))[0]:
            if not isinstance(e, dict):
                continue
            d = e.get("data") if isinstance(e.get("data"), dict) else {}
            cid = str(d.get("commitment_id") or "")
            if cid not in cids:
                continue
            if (e.get("type") == "commitment_resolved" and str(d.get("cockpit_batch") or "") == label
                    and str(e.get("source_skill") or "") == CLOSE_SOURCE and isinstance(e.get("seq"), int)):
                out[cid] = e["seq"]
            elif e.get("type") == "commitment_reopened":
                out.pop(cid, None)
    except Exception:  # noqa: BLE001 - unread: the caller says "could not be confirmed", never "nothing was written"
        return out, False
    return out, True


def _reopen_rows(root: Path, cids: List[str], actor: str, label: str) -> List[Dict[str, Any]]:
    """Every reopen of one Apply in one locked write. NOTE 4 (low risk,
    stated): a one-write reopen that lands and then raises falls back to the
    single reopens below, which answer `already_open` for what landed, so
    the Undo says "already undone" for those. When that write raises,
    each item is reopened alone (`reopen_commitment`), and a row that fails
    again fails alone, so a raise never drops the answers of the rows around
    it (fix round 2, SHOULD 2)."""
    import cockpit_helpers as ch
    import commitment_state as cs

    try:
        return cs.reopen_commitments_one_write(str(root), cids, reopened_by=actor, reason=ch.REOPEN_REASON,
                                               source_skill=CLOSE_SOURCE, user_confirmed=True,
                                               extra_data={"cockpit_batch": label})
    except Exception:  # noqa: BLE001 - fall back to one reopen per item
        pass
    out: List[Dict[str, Any]] = []
    for cid in cids:
        try:
            out.append(cs.reopen_commitment(str(root), cid, reopened_by=actor, reason=ch.REOPEN_REASON,
                                            source_skill=CLOSE_SOURCE, user_confirmed=True,
                                            extra_data={"cockpit_batch": label}))
        except Exception as exc:  # noqa: BLE001 - this row alone
            out.append({"status": "error", "commitment_id": cid, "error": type(exc).__name__})
    return out


def _undo_phase(root: Path, undo_rows: List[Tuple[int, Dict[str, Any]]], actor: str, now: _dt.datetime,
                label: str) -> Dict[int, Dict[str, Any]]:
    """Every Undo row of one Apply. The log is read once; every reopen goes
    to the Hub's writer in one call. Answers one result per row index."""
    import cockpit_helpers as ch
    import hub_work
    from cru_match import _commitment_id
    from events_io import load_events_owner_scoped

    out: Dict[int, Dict[str, Any]] = {}
    wall = hub_work.fence(root)
    try:
        events = [e for e in load_events_owner_scoped(str(root))[0] if isinstance(e, dict)]
    except Exception:  # noqa: BLE001 - fail closed
        events = None
    if wall is None or events is None:
        for i, c in undo_rows:
            out[i] = {"id": c.get("id"), "action": "undo", "outcome": "refused", "reason": "unreadable",
                      "line": UNREADABLE_LINE, "n_undone": 0, "n_already": 0, "n_refused": 0}
        return out
    by_seq = {e.get("seq"): e for e in events if isinstance(e.get("seq"), int)}
    standing: Dict[str, Optional[int]] = {}
    for e in events:
        if e.get("type") in ("commitment_resolved", "commitment_reopened"):
            d = e.get("data") if isinstance(e.get("data"), dict) else {}
            c_ = str(d.get("commitment_id") or "")
            if c_:
                standing[c_] = e.get("seq") if e.get("type") == "commitment_resolved" else None
    captures = {str(_commitment_id(e)): e for e in events if e.get("type") == "commitment"}
    notes_by_batch: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        if e.get("type") == "note":
            notes_by_batch.setdefault(str((e.get("data") or {}).get("brain_batch_id") or ""), []).append(e)

    def held(cid: str) -> bool:
        ev = captures.get(cid)
        if ev is None:
            return True  # an item the book cannot name is not put back from here
        return cid in wall["personal"] or bool(_people(ev) & wall["off"])

    tally = {i: {"n_undone": 0, "n_already": 0, "n_moved": 0, "n_refused": 0, "n_held": 0, "n_tokens": 0}
             for i, _ in undo_rows}
    standing_keeps = kept_ids(events)
    reopens: List[Tuple[int, str, int]] = []
    claimed: set = set()
    for i, c in undo_rows:
        toks = c.get("undo") if isinstance(c.get("undo"), list) else []
        if not toks or len(toks) > UNDO_TOKEN_CAP:
            tally[i]["n_refused"] += 1
            continue
        for k, tok in enumerate(toks):
            tally[i]["n_tokens"] += 1
            kind = tok.get("kind") if isinstance(tok, dict) else None
            cid = str((tok or {}).get("commitment_id") or "").strip() if isinstance(tok, dict) else ""
            if kind == "reopen" and cid and isinstance(tok.get("seq"), int) and not isinstance(tok.get("seq"), bool):
                closed = by_seq.get(tok["seq"]) or {}
                cd = closed.get("data") if isinstance(closed.get("data"), dict) else {}
                if (closed.get("type") != "commitment_resolved" or str(cd.get("commitment_id") or "") != cid
                        or not LABEL_RE.match(str(cd.get("cockpit_batch") or ""))
                        or str(closed.get("source_skill") or "") != CLOSE_SOURCE):
                    tally[i]["n_refused"] += 1
                    continue
                if held(cid):
                    tally[i]["n_held"] += 1
                    continue
                now_close = standing.get(cid)
                if now_close is None or cid in claimed:
                    tally[i]["n_already"] += 1  # open now (already undone), or named twice
                    continue
                if now_close != tok["seq"]:
                    tally[i]["n_moved"] += 1  # closed again since, by anyone
                    continue
                claimed.add(cid)
                reopens.append((i, cid, tok["seq"]))
            elif kind == "note" and cid and str(tok.get("batch_id") or "").strip():
                ours = notes_by_batch.get(str(tok["batch_id"])) or []
                # The batch must be exactly one Keep note on this item, so this
                # door never takes back any other batch.
                if (len(ours) != 1 or str(ours[0]["data"].get("summary") or "") != KEEP_NOTE
                        or str(ours[0]["data"].get("commitment_id") or "") != cid):
                    tally[i]["n_refused"] += 1
                    continue
                if held(cid):
                    tally[i]["n_held"] += 1
                    continue
                import brain_undo as bu

                res = bu.undo_batch(str(root), {"kind": "brain_batch", "batch_id": str(tok["batch_id"])},
                                    undone_by=actor, source_skill=UNDO_SOURCE, user_confirmed=True) or {}
                if int(res.get("n_undone") or 0) and not int(res.get("n_errors") or 0):
                    tally[i]["n_undone"] += 1
                elif cid not in standing_keeps:
                    tally[i]["n_already"] += 1  # the Keep note was already taken back
                else:
                    tally[i]["n_moved"] += 1
            else:
                tally[i]["n_refused"] += 1
    if reopens:
        # ONE locked write for every reopen of this Apply, then the one audit
        # row the Hub's own writer would have written for them.
        from apply_audit import build_apply_choices_applied_event
        from event_gate import append_event

        answers = _reopen_rows(root, [cid for _, cid, _ in reopens], actor, label)
        audit = []
        for (i, cid, seq), ans in zip(reopens, answers):
            st = (ans or {}).get("status")
            if st == "reopened":
                tally[i]["n_undone"] += 1
            elif st == "already_open":
                tally[i]["n_already"] += 1
            else:
                tally[i]["n_failed"] = tally[i].get("n_failed", 0) + 1
            audit.append({"n": cid, "action": "reopen", "note": f"cockpit {label}",
                          "said": "applied" if st == "reopened" else ("already" if st == "already_open" else "error"),
                          "handler_result": {k: v for k, v in (ans or {}).items() if k not in ("event", "exception")}})
        try:
            ev = build_apply_choices_applied_event(source=CLOSE_SOURCE, actions=audit)
            ev["data"]["batch_id"] = label
            ev["data"]["surface"] = ch.SOURCE_SKILL
            append_event(root / "_hq" / "data" / "events.jsonl", ev, holder="still_yours")
        except Exception:  # noqa: BLE001 - the reopens landed; the record of the batch did not
            for i, _, _ in reopens:
                tally[i]["audit_failed"] = True
    for i, c in undo_rows:
        t = tally[i]
        total = t["n_tokens"]
        if t["n_undone"] and t["n_undone"] == total:
            outcome, line = "applied", UNDO_LINE
        elif t["n_undone"]:
            outcome, line = "applied", UNDO_SOME_LINE.format(n=t["n_undone"], total=total)
        elif t.get("n_failed"):
            outcome, line = "error", UNDO_FAILED_LINE
        elif t["n_moved"]:
            outcome, line = "already", UNDO_MOVED_LINE
        elif t["n_already"]:
            outcome, line = "already", UNDO_ALREADY_LINE
        elif t["n_held"]:
            outcome, line = "refused", UNDO_HELD_LINE
        else:
            outcome, line = "refused", BAD_UNDO_LINE
        out[i] = {"id": c.get("id"), "action": "undo", "outcome": outcome, "line": line,
                  "n_undone": t["n_undone"], "n_already": t["n_already"] + t["n_moved"],
                  "n_refused": t["n_refused"] + t["n_held"], "n_failed": t.get("n_failed", 0),
                  **({"audit_failed": True} if t.get("audit_failed") else {})}
    return out


def review_apply(workspace_root, choices: Optional[List[Dict[str, Any]]] = None, *,
                 resolved_by: str = "", now_iso=None) -> Dict[str, Any]:
    """A WRITER. The review's presses, each through its owning writer.

    A choice is `{id, action, commitment_id}` for `keep`, `done` and `drop`;
    `{id, action: "drop_project", project_id}` for the retired-project
    press; `{id, action: "undo", undo: [token, ...]}` for an Undo, where the
    tokens are the `undo` list a press's result answered.

    The Undo rows go first, so the presses are judged against the book they
    leave. The presses are then checked against ONE read of the review, kept
    up to date in memory as each press is taken (an item pressed once in an
    Apply is not on the review for the next press), and every Done, Drop and
    project drop goes to the Hub's writer in ONE call: one locked write and
    one audit row. Answers exactly one result per choice, in the order given:
    `{ok, results, n_applied, n_errors, line?}`. A refusal of one row never
    stops the rest; a wall, an owner or a book that cannot be read refuses
    every row and writes nothing."""
    import cockpit_helpers as ch

    root = Path(str(workspace_root))
    now = ch._now(now_iso)
    rows = [c for c in (choices or []) if isinstance(c, dict)]
    actor = str(resolved_by or "").strip() or ch._owner_id(root)
    label = _label(now)
    if not actor:
        return {"ok": False, "reason": "no_actor", "line": ch.NO_ACTOR_LINE, "results": [],
                "n_applied": 0, "n_errors": len(rows)}
    results: Dict[int, Dict[str, Any]] = {}
    undo_rows: List[Tuple[int, Dict[str, Any]]] = []
    press_rows: List[Tuple[int, Dict[str, Any]]] = []
    refresh_rows: List[int] = []
    for i, c in enumerate(rows):
        verb = str(c.get("action") or "").strip().lower()
        if verb not in VERBS:
            results[i] = {"id": c.get("id"), "commitment_id": str(c.get("commitment_id") or ""), "action": verb,
                          "outcome": "refused", "line": BAD_VERB_LINE}
        elif verb == "refresh":
            refresh_rows.append(i)
        elif verb == "undo":
            undo_rows.append((i, c))
        else:
            press_rows.append((i, c))
    if undo_rows:
        results.update(_undo_phase(root, undo_rows, actor, now, label))
    if press_rows:
        results.update(_press_phase(root, press_rows, actor, now, label))
    # The review's own document is rebuilt after any write, and on the page's
    # `refresh` press (the review opened), so the page shows the book as it is.
    wrote = any(r.get("outcome") == "applied" for r in results.values())
    if wrote or refresh_rows:
        try:
            build_now_file(root, _iso(now))
            built = True
        except Exception:  # noqa: BLE001 - the presses landed; the page keeps its last copy
            built = False
        for i in refresh_rows:
            results[i] = {"id": rows[i].get("id"), "action": "refresh",
                          "outcome": "applied" if built else "error",
                          "line": REFRESH_LINE if built else UNREADABLE_LINE}
    ordered = [results[i] for i in range(len(rows))]
    n_applied = sum(1 for r in ordered if r.get("outcome") == "applied")
    n_errors = sum(1 for r in ordered if r.get("outcome") in ("error", "refused"))
    return {"ok": True, "results": ordered, "n_applied": n_applied, "n_errors": n_errors}


def _press_phase(root: Path, press_rows: List[Tuple[int, Dict[str, Any]]], actor: str, now: _dt.datetime,
                 label: str) -> Dict[int, Dict[str, Any]]:
    """Keep, Done, Drop and the project press of one Apply, against one read
    of the review. All closes go in one call to the Hub's writer."""
    import cockpit_helpers as ch
    import commitment_state as cs

    out: Dict[int, Dict[str, Any]] = {}
    view, held_ids = _review_full(root, _iso(now))
    if not view.get("ok"):
        for i, c in press_rows:
            out[i] = {"id": c.get("id"), "commitment_id": str(c.get("commitment_id") or ""),
                      "action": str(c.get("action") or "").strip().lower(), "outcome": "refused",
                      "reason": "unreadable", "line": UNREADABLE_LINE}
        return out
    listed = {r["id"] for r in view["rows"]}
    projects = {p["project_id"]: p for p in view["projects"]}
    taken: set = set()
    closes: List[Dict[str, Any]] = []
    close_of: Dict[str, Tuple[int, str]] = {}
    groups: Dict[int, Dict[str, Any]] = {}
    for i, c in press_rows:
        verb = str(c.get("action") or "").strip().lower()
        cid = str(c.get("commitment_id") or "").strip()
        base = {"id": c.get("id"), "commitment_id": cid, "action": verb}
        if verb == "drop_project":
            pid = str(c.get("project_id") or "").strip()
            base.update(project_id=pid)
            proj = projects.get(pid)
            ids = [x for x in (proj or {}).get("ids", []) if x not in taken]
            if proj is None or not ids:
                out[i] = dict(base, outcome="refused", line=NO_PROJECT_LINE)
                continue
            if len(ids) > PROJECT_DROP_CAP:
                out[i] = dict(base, outcome="refused", line=TOO_MANY_LINE.format(cap=PROJECT_DROP_CAP))
                continue
            groups[i] = {"base": base, "name": proj["name"], "ids": ids, "left": proj.get("left_open") or {}}
            for x in ids:
                taken.add(x)
                rid = f"sy:{i}:{x}"
                close_of[rid] = (i, x)
                closes.append({"id": rid, "kind": "commitment", "commitment_id": x, "action": "drop"})
            continue
        if cid in held_ids:
            out[i] = dict(base, outcome="refused", line=HELD_LINE)
            continue
        if cid not in listed or cid in taken:
            out[i] = dict(base, outcome="refused", line=NOT_LISTED_LINE)
            continue
        taken.add(cid)
        if verb in ("done", "drop"):
            rid = f"sy:{i}:{cid}"
            close_of[rid] = (i, cid)
            closes.append({"id": rid, "kind": "commitment", "commitment_id": cid, "action": verb})
            out[i] = dict(base, outcome="pending")
        else:  # keep
            res = cs.add_commitment_note(str(root), cid, KEEP_NOTE, noted_by=actor, source_skill=SOURCE_SKILL)
            if res.get("status") == "noted":
                bid = str(((res.get("batch_ref") or {}).get("batch_id")) or "")
                out[i] = dict(base, outcome="applied", line=KEEP_LINE, seq=res.get("seq"),
                              undo=[{"kind": "note", "commitment_id": cid, "batch_id": bid}])
            else:
                out[i] = dict(base, outcome="error", line=str(res.get("line") or NOT_LISTED_LINE))
    answers: Dict[str, Dict[str, Any]] = {}
    if closes:
        try:
            res = ch.apply_commitment_choices(str(root), closes, resolved_by=actor, now_iso=_iso(now), batch_id=label)
            answers = {str(r.get("id") or ""): r for r in res.get("results") or []}
        except Exception:  # noqa: BLE001 - what landed is read from the log below
            answers = {}
    # MUST 1 (fix round 2): any close not answered with its seq is looked up in
    # the log, so a close that landed always counts as landed, with its Undo.
    unsure = {cid for rid, (i, cid) in close_of.items()
              if not ((answers.get(rid) or {}).get("outcome") == "applied"
                      and isinstance((answers.get(rid) or {}).get("seq"), int))}
    landed, log_read = _landed_closes_read(root, unsure, label)
    for rid, (i, cid) in close_of.items():
        ans = dict(answers.get(rid) or ({"outcome": "error", "line": NOT_WRITTEN_LINE, "reason": "no_answer"} if log_read
                                        else {"outcome": "error", "line": UNCONFIRMED_LINE, "reason": "unconfirmed"}))
        if cid in landed:
            ans.update(outcome="applied", seq=landed[cid])
        seq = ans.get("seq") if isinstance(ans.get("seq"), int) and not isinstance(ans.get("seq"), bool) else None
        if i in groups:
            g = groups[i]
            if ans.get("outcome") == "applied" and seq is not None:
                g.setdefault("undo", []).append({"kind": "reopen", "commitment_id": cid, "seq": seq})
            elif ans.get("outcome") == "applied":
                g["n_no_undo"] = g.get("n_no_undo", 0) + 1
            elif ans.get("outcome") == "already":
                g["n_already"] = g.get("n_already", 0) + 1
            elif ans.get("reason") == "unconfirmed":
                g["n_unconfirmed"] = g.get("n_unconfirmed", 0) + 1
            else:
                g["n_failed"] = g.get("n_failed", 0) + 1
                g.setdefault("whys", [])
                if _why_clause(ans) not in g["whys"]:
                    g["whys"].append(_why_clause(ans))
            continue
        row = dict(out[i])
        row.update({k: v for k, v in ans.items() if k in ("outcome", "line", "seq", "reason")})
        if ans.get("outcome") == "applied":
            row["line"] = DONE_LINE if row["action"] == "done" else DROP_LINE
            if seq is not None:
                row["undo"] = [{"kind": "reopen", "commitment_id": cid, "seq": seq}]
            else:
                row["line"] += NO_UNDO_LINE
        out[i] = row
    for i, g in groups.items():
        undo = g.get("undo") or []
        n_no_undo = g.get("n_no_undo", 0)
        n = len(undo) + n_no_undo
        n_left, why = _left_words(g["left"])
        # Never "nothing was written" while anything landed.
        n_unc = g.get("n_unconfirmed", 0)
        line = (PROJECT_LINE.format(n=n, s="" if n == 1 else "s", name=g["name"]) if n
                else PROJECT_UNCONFIRMED_LINE.format(name=g["name"]) if n_unc
                else PROJECT_NONE_LINE.format(name=g["name"]))
        if n and n_unc:
            line += PROJECT_SOME_UNCONFIRMED_LINE.format(n=n_unc)
        if n and n_no_undo:
            line += PROJECT_NO_UNDO_LINE.format(n=n_no_undo)
        if g.get("n_failed"):
            line += PROJECT_FAILED_LINE.format(n=g["n_failed"], why="; ".join(g.get("whys") or [WHY_NOT_WRITTEN]))
        if n and n_left:
            line += PROJECT_LEFT_LINE.format(n=n_left, why=why)
        out[i] = dict(g["base"], outcome="applied" if n else "refused", line=line, n_dropped=n,
                      n_already=g.get("n_already", 0), n_failed=g.get("n_failed", 0), n_no_undo=n_no_undo,
                      n_unconfirmed=n_unc,
                      left_open=dict(g["left"]), undo=undo)
    return out


def _calendar_day(root: Path, now_iso=None) -> str:
    try:
        import cockpit_helpers as ch
        from commitment_state import _now_calendar_day

        d = _now_calendar_day(_iso(ch._now(now_iso)), workspace_path=str(root))
        return d.isoformat() if d else ""
    except Exception:  # noqa: BLE001
        return ""


def build_now_file(workspace_root, now_iso=None) -> Dict[str, Any]:
    """A WRITER. The review as `feed/stillyours` carries it, written to
    `NOW_REL` (atomic, whole file): the oldest `FEED_ROWS_CAP` rows, `n` the
    whole count, `capped` when rows were left out, the projects (their ids
    capped, `n_ids` whole), `n_held`, and when it was built. A review that
    cannot be read is written as `ok: False` with nothing listed, so the
    page never shows a list the fence could not check. Answers the doc."""
    import cockpit_helpers as ch
    from atomic_write import atomic_write_json

    root = Path(str(workspace_root))
    rv = review(root, now_iso)
    rows = list(rv.get("rows") or [])
    doc = {"ok": bool(rv.get("ok")), "days": rv.get("days"), "n": int(rv.get("n") or 0),
           "rows": rows[:FEED_ROWS_CAP], "capped": len(rows) > FEED_ROWS_CAP, "n_held": int(rv.get("n_held") or 0),
           "projects": [dict(p, ids=list(p.get("ids") or [])[:FEED_PROJECT_IDS_CAP], n_ids=len(p.get("ids") or []))
                        for p in rv.get("projects") or []],
           "built_at": _iso(ch._now(now_iso)), "built_day": _calendar_day(root, now_iso),
           # fix round 4: who is on each listed item, for the read-time wall
           "parties": dict(rv.get("parties") or {})}
    atomic_write_json(root / NOW_REL, doc)
    return doc


def build_if_due(workspace_root, now_iso=None) -> Dict[str, Any]:
    """A WRITER, at most once a workspace day: builds the document when it
    is missing, unreadable or from an earlier day. Answers `{built, day}`.
    Never raises (Maintenance's open-list job calls it)."""
    root = Path(str(workspace_root))
    day = _calendar_day(root, now_iso)
    try:
        import json as _json

        old = _json.loads((root / NOW_REL).read_text(encoding="utf-8"))
        if day and old.get("built_day") == day:
            return {"built": False, "day": day}
    except (OSError, ValueError, AttributeError):
        pass
    try:
        build_now_file(root, now_iso)
        return {"built": True, "day": day}
    except Exception as exc:  # noqa: BLE001
        return {"built": False, "day": day, "error": type(exc).__name__}


def feed(workspace_root, now_iso=None) -> Dict[str, Any]:
    """READ ONLY. `feed/stillyours`: the document `build_now_file` wrote, as
    it is, plus `stale` when it was built on an earlier day. No document
    answers `ok: False, reason: "not_built"` and lists nothing. Never builds,
    never raises."""
    import json as _json

    root = Path(str(workspace_root))
    try:
        doc = _json.loads((root / NOW_REL).read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise ValueError("not a document")
    except (OSError, ValueError):
        return {"ok": False, "reason": "not_built", "n": 0, "rows": [], "projects": [], "capped": False,
                "n_held": 0, "built_at": "", "stale": True}
    day = _calendar_day(root, now_iso)
    return _walled(root, dict(doc, stale=bool(day and doc.get("built_day") != day)))


def _walled(root: Path, doc: Dict[str, Any]) -> Dict[str, Any]:
    """Fix round 4 (MUST 1): the document held to the wall AS IT IS NOW, the
    way every other Hub feed is (`hub_work.fence` at read time). A row or a
    project item whose people are personal or off limits now is left out,
    and counted in `n_held`. A wall that cannot be read, or a document with
    no people on it (built before this check), answers `ok: False` and lists
    nothing. The people never leave this function."""
    import hub_work

    empty = {"ok": False, "n": 0, "rows": [], "projects": [], "capped": False, "n_held": int(doc.get("n_held") or 0),
             "built_at": doc.get("built_at") or "", "stale": doc.get("stale", True)}
    parties = doc.get("parties")
    if not isinstance(parties, dict):
        return dict(empty, reason="not_built")
    if not doc.get("ok"):
        return dict(empty, reason=str(doc.get("reason") or "unreadable"))
    wall = hub_work.fence(root)
    if wall is None:
        return dict(empty, reason=FEED_UNREADABLE)

    def held(cid: str) -> bool:
        who = parties.get(cid)
        if not isinstance(who, dict):
            return True  # an item the document names nobody for is not shown
        return hub_work._held({"id": cid, "owner_id": who.get("owner_id"),
                               "counterparty_ids": who.get("counterparty_ids") or []}, wall)

    rows = [r for r in doc.get("rows") or [] if isinstance(r, dict) and not held(str(r.get("id") or ""))]
    dropped = len(doc.get("rows") or []) - len(rows)
    projects = []
    for pj in doc.get("projects") or []:
        if not isinstance(pj, dict):
            continue
        ids = [x for x in pj.get("ids") or [] if not held(str(x))]
        gone = len(pj.get("ids") or []) - len(ids)
        if not ids:
            continue
        projects.append(dict(pj, ids=ids, n_ids=max(0, int(pj.get("n_ids") or 0) - gone)))
    out = {k: v for k, v in doc.items() if k != "parties"}
    out.update(rows=rows, projects=projects, n=max(0, int(doc.get("n") or 0) - dropped),
               n_held=int(doc.get("n_held") or 0) + dropped)
    return out


def main(argv=None) -> int:
    import argparse
    import json

    p = argparse.ArgumentParser(description="The Still yours? review (cr1#1706). Read only.")
    p.add_argument("--workspace", required=True)
    a = p.parse_args(argv)
    print(json.dumps(review(a.workspace), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
