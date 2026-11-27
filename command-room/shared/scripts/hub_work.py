#!/usr/bin/env python3
"""What the Hub's Work tab reads beyond the plate (cr1#1540).

The Work tab's Triage view shows the owner's whole plate, offers four piles
he can clear in one press each, and keeps up to five priorities in his own
order with dated updates on a thread. This module is the READ side of all of
it. It owns no rule: every number and every pile comes from the function the
product already answers that question with, called as it is.

  * the plate's counts: `open_list.query`, the open list as every surface
    counts it (confirmed, shown, not snoozed), split by the list's own
    `direction`; the aging bar groups those same rows by the list's own
    `age_days`. The bar's four edges (`AGE_EDGES`) only say where a segment
    ends; they count nothing in or out;
  * the four piles, each a rule the product already has:
      - `looks_done`: an open, confirmed item with a close question on the
        review queue (`brain_proposals.load_open_proposals`, the rows To
        confirm shows) whose words say a reply came in that looks like what
        the owner was waiting for, and which passes the screen the review
        confirm itself applies (`watch_gate.weakness_reason` with the
        completion signal and `watch_gate.temporal_warning`; an either/or
        question has no single item and is out). Its press is that review
        row's own `confirm`, so Apply closes it through
        `watch_gate.confirm_review_rows`, the one way a proposed close is
        accepted;
      - `quiet`: an item owed to the owner that the silence door would rest
        today (`exit_doors.silence_candidates`'s `park` list, at its own
        `SILENCE_PARK_DAYS`). Its press is `park`;
      - `unconfirmed`: an item still waiting for the owner's yes that the
        review-expiry door would lapse today
        (`commitment_backlog_sweep.review_expiry_plan`, at the workspace's
        own threshold, with that door's own holds left out). Its press is
        `notwork`, the Hub's existing "not a promise" on such a row;
      - `undated_late`: an item the owner owes that the open list counts
        overdue (`open_list.query`'s `overdue`, the headline rule) and whose
        date he never set: it came from a meeting, a mail or a chat message
        (`cockpit_helpers._row_origin`) and no change of his own ever moved
        it (`cockpit_helpers._owner_own_change`). Its press is `snooze` to
        the last day of this week by the Hub's one definition (`week_day`,
        `cockpit_helpers.week_end`, shared with the Week board, cr1#1657);
  * the fence every pile is held to: an item on the personal lane
    (`morning_message._personal_ids`), or with a person the owner put off
    limits on it (`account_scope_gate.off_limits_person_ids`), never enters
    a pile. A wall that cannot be read closes every pile, and so does a mail
    rules store that is there and cannot be read: the gate's own reader
    answers "nobody is off limits" for one, which is right for mail and no
    answer for a press that closes many items;
  * the owner's order for his priorities and the updates he posted on a
    thread: the `hub_edit` rows the Apply run wrote for them, newest first,
    a taken-back one left out.

READ ONLY, and it never raises into a feed: anything that cannot be read
answers `ok: False` and no pile. The writes are `hub_edit`'s (the order and
the updates) and `cockpit_helpers.apply_commitment_choices`'s (the piles).

A pile's press and its Undo use the verbs the items' own rows have: a
review `confirm` and a `notwork` are taken back by `reopen`, `park` by
`unpark`, and a move by the Record tab's own Put it back on the press's
batch (`pile_batch`).
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

PILE_DONE = "looks_done"
PILE_QUIET = "quiet"
PILE_UNCONFIRMED = "unconfirmed"
PILE_UNDATED = "undated_late"
PILES = (PILE_DONE, PILE_QUIET, PILE_UNCONFIRMED, PILE_UNDATED)
#: The one existing press each pile saves per item.
PILE_VERBS = {PILE_DONE: "confirm", PILE_QUIET: "park", PILE_UNCONFIRMED: "notwork",
              PILE_UNDATED: "snooze"}
#: The choice kind each pile's row is: Looks done is the review row's own
#: confirm, the others are the commitment row's own press.
PILE_ROW_KINDS = {PILE_DONE: "review", PILE_QUIET: "commitment", PILE_UNCONFIRMED: "commitment",
                  PILE_UNDATED: "commitment"}
#: How the inbound reconcile words a reply that looks like what the owner was
#: waiting for (`reconcile_inbound_commitments`: its promoted lede and its
#: proposed one); the page's "Looks done?" marker tests the same two.
REPLY_LEDES = ("they replied on this thread with what you were waiting for",
               "their reply looks like the thing you were waiting for")
#: Where a segment of the aging bar ends, in days open: under a week, one to
#: four weeks, one to three months, older. Display only.
AGE_EDGES = (7, 28, 90)
#: How many priorities the page shows in the owner's order; the rest sit
#: under More. A star is refused once this many are confirmed.
PRIORITY_SHOWN = 5
#: The most updates one feed carries, newest first; the count travels whole.
UPDATES_CAP = 60
#: Where a captured date came from when nobody typed it: the product read it
#: out of somebody's words (`cockpit_helpers._row_origin`'s source words).
HEARD_SOURCES = frozenset({"Meeting", "Email", "Slack"})
#: A pile's id on a choice row: `<kind>:<stamp>`, the stamp being the read
#: the pile was drawn from. Checked here, never trusted from the page.
PILE_ID_CAP = 80


def pile_kind(pile_id: Any) -> str:
    """The pile kind a choice row's `pile` names, or "" when it names none."""
    text = str(pile_id or "")
    if not text or len(text) > PILE_ID_CAP:
        return ""
    kind = text.split(":", 1)[0]
    return kind if kind in PILES else ""


def pile_batch(pile_id: Any) -> str:
    """The batch a pile's moves share (`undated_late` only: a move is the one
    pile write whose Undo is a batch's): one per pile press, so one Put it
    back takes back that press's moves and no other."""
    return "hubpile:" + "".join(ch for ch in str(pile_id or "")[:PILE_ID_CAP] if ch.isalnum() or ch in ":_-")


def _iso(now: _dt.datetime) -> str:
    return now.isoformat()


def week_day(workspace_root, now: _dt.datetime) -> str:
    """The last day of this week, on the workspace's own calendar, by the
    Hub's one definition (`cockpit_helpers.week_end`, cr1#1657): the Sunday
    that ends this Monday-to-Sunday week, or on a Saturday or a Sunday the
    last of the Week board's seven days, so a pile never moves its items to
    the day it is pressed."""
    from cockpit_helpers import week_end
    from commitment_state import _now_calendar_day

    day = _now_calendar_day(_iso(now), workspace_path=str(workspace_root)) or now.date()
    return week_end(day).isoformat()


def fence(workspace_root) -> Optional[Dict[str, set]]:
    """`{personal, off}`: the commitments on the personal lane and the people
    who are off limits. None when either cannot be told (fail closed)."""
    try:
        import connector_reads
        from account_scope_gate import off_limits_person_ids
        from morning_message import _personal_ids

        root = Path(str(workspace_root))
        # A book that cannot be read answers "nobody is off limits" from the
        # gate, which is no answer: the wall's own reader says so first.
        if not connector_reads.chat_wall(root).get("readable"):
            return None
        if not _rules_readable(root):
            return None
        # cr1#1682: a book whose `people` is there but not a list reads as
        # "nobody" to the lenient readers below, so nobody is walled: no answer.
        from personal_leak import people_records
        if people_records(root) is None:
            return None
        personal = _personal_ids(root)
        if personal is None:
            return None
        return {"personal": {str(i) for i in personal},
                "off": {str(i) for i in off_limits_person_ids(str(root))}}
    except Exception:  # noqa: BLE001 - fail closed: no pile
        return None



#: A name shorter than this is never matched on its own (an initial, a typo).
HIDDEN_NAME_MIN = 2


def _full_forms(rec: Dict[str, Any]) -> set:
    """A person's whole names only (full name and aliases), lower case."""
    out = set()
    for v in [rec.get("canonical_name"), rec.get("name")] + list(rec.get("aliases") or []):
        v = str(v or "").strip().lower()
        if len(v) >= HIDDEN_NAME_MIN:
            out.add(v)
    return out


def _name_forms(rec: Dict[str, Any]) -> set:
    """Every way a person's record is written: the full name, the first name
    and each alias, lower case.

    Fix check of #1812 at 90aed0468 (should): the first name is taken from
    the name up to any bracket ("(Example Co)" says where they work), after a
    leading title is read as `followup_draft.name_readings` says, so "Dr. Tess
    Vane" gives "tess", never "dr."."""
    try:
        from followup_draft import name_readings
    except Exception:  # noqa: BLE001 - no title rule: the first word, as before
        def name_readings(words):  # type: ignore[no-redef]
            return [list(words)]
    out = set()
    for v in [rec.get("canonical_name"), rec.get("name")] + list(rec.get("aliases") or []):
        v = str(v or "").strip().lower()
        if len(v) >= HIDDEN_NAME_MIN:
            out.add(v)
            # decision 147l: every reading's first word ("hon j. vale" gives "hon";
            # its "j." is an initial and is never held as a first name)
            for words in name_readings(v.split("(", 1)[0].split()):
                first = words[0] if words else ""
                if len(first.strip(".")) >= HIDDEN_NAME_MIN:
                    out.add(first)
    return out


def hidden_people(workspace_root) -> Dict[str, Any]:
    """The one rule for naming people on the Hub (cr1#1717, cr1#1721, cr1#1738):
    nobody off limits (the fence's `off`) and nobody tied to the personal side
    (`hub_meeting._tie_ids`) is ever named. Answers `{unread, ids, names,
    all_names}`: `ids` the hidden people, `names` every way their records are
    written (full name, first name, aliases, lower case), `all_names` the same
    for everyone on file but the owner. `unread` is True when the ties or the
    fence cannot be read: then nobody is named at all, so a caller hides every
    name it knows (`all_names`)."""
    root = Path(str(workspace_root))
    shut = {"unread": True, "ids": frozenset(), "names": frozenset(), "all_names": frozenset(),
            "full_names": frozenset(), "all_full": frozenset(), "shown_full": frozenset()}
    try:
        import hub_meeting
        from personal_leak import people_records

        people = people_records(root)
        ties = hub_meeting._tie_ids(root)
        wall = fence(root)
    except Exception:  # noqa: BLE001 - fail closed: nobody is named
        return shut
    everyone: set = set()
    # the owner, by the one resolver (G34), is never one of the hidden
    try:
        from primary_user import resolve_primary_user
        owner = str(resolve_primary_user(root) or "")
    except Exception:  # noqa: BLE001 - no owner known: nobody is set apart
        owner = ""
    everyone_full: set = set()
    for rec in people or []:
        if isinstance(rec, dict) and str(rec.get("id") or "") != owner:
            everyone |= _name_forms(rec)
            everyone_full |= _full_forms(rec)
    shut["all_names"] = frozenset(everyone)
    shut["all_full"] = frozenset(everyone_full)
    if people is None or ties is None or wall is None:
        return shut
    ids = (frozenset(str(i) for i in ties) | frozenset(str(i) for i in (wall.get("off") or ()))) - {owner}
    names: set = set()
    full: set = set()
    shown: set = set()
    for rec in people:
        if not isinstance(rec, dict) or str(rec.get("id") or "") == owner:
            continue
        if str(rec.get("id") or "") in ids:
            names |= _name_forms(rec)
            full |= _full_forms(rec)
        else:
            shown |= _full_forms(rec)
    return {"unread": False, "ids": ids, "names": frozenset(names), "all_names": frozenset(everyone),
            "full_names": frozenset(full), "all_full": frozenset(everyone_full),
            # review of #1733 round 2, item 3: someone on file who is not hidden
            "shown_full": frozenset(shown - full)}


def name_is_hidden(text, hp: Dict[str, Any]) -> bool:
    """True when `text` names someone `hidden_people` hides: the whole text,
    or any word of it, is one of their names (a first name alone counts). On
    an unread fence every known name is hidden."""
    value = str(text or "").strip().lower()
    if not value:
        return False
    # review of #1733 round 2, item 3: the whole name of someone on file who
    # is not hidden ("Bo Sample") is theirs, never a hidden person's alias
    if not hp.get("unread") and value in (hp.get("shown_full") or frozenset()):
        return False
    names = hp.get("all_names") if hp.get("unread") else hp.get("names")
    names = names or frozenset()
    if value in names:
        return True
    words = [w.strip(".,;:()'\"") for w in value.replace("-", " ").split()]
    return any(w in names for w in words if len(w) >= HIDDEN_NAME_MIN)

def _rules_readable(root: Path) -> bool:
    """Whether the mail rules store, where onboarding's off-limits answers
    live, can be told apart from an empty one: missing is empty (nobody was
    put off limits there), present and unparseable is unreadable. The shared
    reader (`mail_rules.load`) answers empty for both, as an inbox read must;
    this asks its writers' own loader, which says which."""
    # cr1#1830: one answer for every reader, the gate's; it also counts a
    # store whose rules are not a list as unreadable (the writers' loader
    # reads that as an empty list, which put nobody off limits).
    from account_scope_gate import off_limits_readable

    return off_limits_readable(root)


def looks_done_items(workspace_root, now: _dt.datetime) -> Dict[str, Dict[str, Any]]:
    """`{commitment id: the review row's fence inputs}` for each proposed
    close a Looks done press may accept: a reply-worded question that the
    review confirm's own screen would let through now. The inputs are the
    queue item's own, so the Apply run hands the fence what the book holds,
    never what a page sent."""
    import brain_proposals as bp
    import watch_gate as wg

    out: Dict[str, Dict[str, Any]] = {}
    for it in bp.load_open_proposals(str(workspace_root), "staff-meeting", now_iso=_iso(now)):
        if str(it.get("kind") or "") != "commitment_review" or "confirm" not in bp._row_actions(it):
            continue
        cid = str(it.get("commitment_id") or "")
        said = [str(it.get(k) or "").strip().lower() for k in ("evidence", "render_line")]
        if not cid or not any(t.startswith(lede) for t in said for lede in REPLY_LEDES):
            continue
        amb = it.get("ambiguous_candidates")
        if isinstance(amb, list) and len(amb) >= 2:
            continue
        warn = wg.temporal_warning(evidence_ts=it.get("evidence_ts"), promise_ts=it.get("promise_ts"),
                                   meeting_start=it.get("meeting_start"), apply_ts=_iso(now))
        if wg.weakness_reason(it.get("evidence"), completion_signal=it.get("has_completion_signal"), temporal=warn):
            continue
        if cid in out:
            continue  # one question an item; a second one is answered on To confirm
        out[cid] = {"id": str(it.get("id") or ""), "commitment_id": cid, "evidence": it.get("evidence"),
                    "match_score": it.get("match_score"), "has_completion_signal": it.get("has_completion_signal"),
                    "evidence_ts": it.get("evidence_ts"), "promise_ts": it.get("promise_ts"),
                    "meeting_start": it.get("meeting_start"), "matched_ref": it.get("matched_ref")}
    return out


def _held(row: Dict[str, Any], wall: Dict[str, set]) -> bool:
    """Whether the fence keeps this open-list row out of every pile."""
    if str(row.get("id")) in wall["personal"]:
        return True
    people = {str(row.get("owner_id") or "")} | {str(p) for p in row.get("counterparty_ids") or []}
    return bool((people - {""}) & wall["off"])


def _owner_dated(events: List[Dict[str, Any]]) -> set:
    """The commitments whose date the owner moved himself: a
    `commitment_updated` that sets a date and is his own act, and was not
    taken back with the Hub press that wrote it."""
    import cockpit_helpers as ch
    import hub_edit
    from commitment_state import _due_set
    from event_seq import event_seq

    # Review of #1606: a Hub Edit press that was taken back, and the Undo that
    # took it back, are not the owner dating the item (`hub_edit.taken_back_seqs`,
    # as the movement clock reads it), so the pair leaves the piles as they were.
    gone = hub_edit.taken_back_seqs(events)
    out = set()
    for ev in events:
        if not isinstance(ev, dict) or ev.get("type") != "commitment_updated":
            continue
        if event_seq(ev) in gone:
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if d.get("commitment_id") and _due_set(d) is not None and ch._owner_own_change(ev):
            out.add(str(d["commitment_id"]))
    return out


def _heard_ids(workspace_root, opens: List[Dict[str, Any]]) -> set:
    """The open commitments captured from somebody's words (a meeting, a
    mail, a chat message) rather than typed by the owner."""
    import cockpit_helpers as ch
    from cru_match import _commitment_id

    names = ch._person_names(workspace_root)
    owner = ch._owner_id(workspace_root)
    tzinfo = ch._workspace_tz(workspace_root)
    return {str(_commitment_id(ev)) for ev in opens
            if ch._row_origin(ev, names, owner, tzinfo).get("source") in HEARD_SOURCES}


def pile_sets(workspace_root, now_iso=None) -> Dict[str, Any]:
    """READ. `{ok, piles: {kind: set of commitment ids}, looks, week_day,
    days}`: which open items each pile may hold now (`looks`: for Looks done,
    each item's review row as the fence reads it). `ok: False` (and no pile) when
    the fence, the owner or the open list cannot be read. The feed carries
    this to the page, and the Apply run asks it again before it writes a
    pile's row, so an item that left its pile since the press is refused."""
    import cockpit_helpers as ch

    root = Path(str(workspace_root))
    now = ch._now(now_iso)
    out: Dict[str, Any] = {"ok": False, "piles": {k: set() for k in PILES}, "looks": {}, "week_day": "", "days": {}}
    try:
        owner = ch._owner_id(root)
        wall = fence(root)
        if not owner or wall is None:
            return out
        import commitment_backlog_sweep as sweep
        import exit_doors
        import open_list as ol
        from cru_match import load_open_commitments
        from events_io import load_events_owner_scoped

        events = [e for e in load_events_owner_scoped(str(root))[0] if isinstance(e, dict)]
        listed = ol.query(root, include_unconfirmed=True, now_iso=_iso(now))["rows"]
        free = {str(r["id"]): r for r in listed if not _held(r, wall)}
        confirmed = {i for i, r in free.items() if r.get("confirmed")}

        looks = {cid: it for cid, it in looks_done_items(root, now).items() if cid in confirmed}
        out["looks"] = looks
        out["piles"][PILE_DONE] = set(looks)

        rest = exit_doors.silence_candidates(str(root), now_iso=_iso(now), events=events)
        out["piles"][PILE_QUIET] = {str(r["commitment_id"]) for r in rest.get("park") or []
                                    if str(r["commitment_id"]) in confirmed
                                    and free[str(r["commitment_id"])].get("direction") == "owed_to_you"}

        plan = sweep.review_expiry_plan(str(root), user_person_id=owner, now_iso=_iso(now))
        if plan.get("ok"):
            out["piles"][PILE_UNCONFIRMED] = {str(r["commitment_id"]) for r in plan.get("rows") or []
                                              if str(r["commitment_id"]) in free
                                              and not free[str(r["commitment_id"])].get("confirmed")}
            out["days"][PILE_UNCONFIRMED] = int(plan.get("threshold_days") or 0)

        ep = root / "_hq" / "data" / "events.jsonl"
        heard = _heard_ids(root, load_open_commitments(ep) if ep.exists() else [])
        dated = _owner_dated(events)
        out["piles"][PILE_UNDATED] = {i for i in confirmed
                                      if free[i].get("direction") == "you_owe" and free[i].get("overdue")
                                      and i in heard and i not in dated}
        out["days"][PILE_QUIET] = int(exit_doors.SILENCE_PARK_DAYS)
        out["week_day"] = week_day(root, now)
        out["ok"] = True
    except Exception:  # noqa: BLE001 - fail closed: a pile that cannot be worked out is no pile
        out["piles"] = {k: set() for k in PILES}
        out["looks"] = {}
        out["ok"] = False
    return out


def due_today(rows: List[Dict[str, Any]], today: str) -> Dict[str, Any]:
    """cr1#1706: `{n, by_direction}`, the open-list rows due on `today` (the
    workspace's own calendar day, as `open_list.query` answered it). An item
    waiting on something is not counted, as the late count leaves it out.
    Pure."""
    import open_list as ol

    due = [r for r in rows if str(r.get("due") or "") == str(today or "-") and not r.get("waiting_on")]
    by_dir = {d: sum(1 for r in due if (r.get("direction") or "unowned") == d) for d in ol.DIRECTIONS}
    return {"n": len(due), "by_direction": by_dir}


def work_block(workspace_root, now_iso=None) -> Dict[str, Any]:
    """READ. The plate feed's optional `work` block: the open list's counts
    by direction and by age, and the ids each pile may hold. An older page
    never reads it; a feed without it gives the newer page no pile."""
    import cockpit_helpers as ch
    import open_list as ol

    root = Path(str(workspace_root))
    now = ch._now(now_iso)
    listed = ol.query(root, now_iso=_iso(now))
    rows = listed["rows"]
    counts = {"open": len(rows)}
    for d in ol.DIRECTIONS:
        counts[d] = sum(1 for r in rows if (r.get("direction") or "unowned") == d)
    ages = [0, 0, 0, 0]
    n_undated = 0
    for r in rows:
        age = r.get("age_days")
        if not isinstance(age, int) or age < 0:
            n_undated += 1
            continue
        ages[sum(1 for edge in AGE_EDGES if age >= edge)] += 1
    sets = pile_sets(root, _iso(now))
    return {"ok": bool(sets["ok"]), "counts": counts, "ages": ages, "n_no_age": n_undated,
            # cr1#1706: what the Work badge counts, in place of the lifetime
            # late total: the listed items due on the workspace's own today.
            "due_today": due_today(rows, listed["today"]),
            "piles": {k: sorted(sets["piles"][k]) for k in PILES} if sets["ok"] else {},
            # the review row each Looks done item is pressed through
            "looks": {cid: it["id"] for cid, it in sorted(sets["looks"].items())} if sets["ok"] else {},
            "week_day": sets["week_day"], "days": dict(sets["days"])}


# ---------------------------------------------------------------------------
# The owner's order and his updates, read from the Hub's own edit rows
# ---------------------------------------------------------------------------

def _edits(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The Hub's own edit rows, by the type their one writer gives them."""
    import hub_edit

    return [e for e in events or [] if isinstance(e, dict) and e.get("type") == hub_edit.EVENT
            and isinstance(e.get("data"), dict)]


def _undone(edits: List[Dict[str, Any]]) -> set:
    return {str(e["data"].get("undoes_choice")) for e in edits
            if e["data"].get("action") == "undo" and e["data"].get("undoes_choice")}


def stored_order(events: List[Dict[str, Any]]) -> List[str]:
    """The owner's order for his priorities as it stands: the newest order
    press that was not taken back. [] when he never set one."""
    edits = _edits(events)
    undone = _undone(edits)
    for e in reversed(edits):
        d = e["data"]
        if d.get("kind") == "priority_edit" and d.get("action") == "order" \
                and str(d.get("hub_choice")) not in undone:
            return [str(i) for i in d.get("order") or [] if i]
    return []


def ordered(ids: List[str], order: List[str]) -> List[str]:
    """`ids` in the stored order: the ones the order names first, in its
    order, then the rest as they came. An id the order names that is not
    among `ids` (a priority closed since) is skipped."""
    have = [str(i) for i in ids]
    first = [i for i in order if i in have]
    seen = set(first)
    return first + [i for i in have if i not in seen]


def starred(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    """`{priority id: {press, thread}}` for each priority a star on the Hub
    made and no Undo took back."""
    edits = _edits(events)
    undone = _undone(edits)
    out: Dict[str, Dict[str, str]] = {}
    for e in edits:
        d = e["data"]
        if d.get("kind") == "priority_edit" and d.get("action") == "star" \
                and str(d.get("hub_choice")) not in undone and d.get("target_id"):
            out[str(d["target_id"])] = {"press": str(d.get("hub_choice") or ""),
                                        "thread": str(d.get("thread_id") or "")}
    return out


def updates(events: List[Dict[str, Any]], names: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """Every update the owner posted on a thread and did not take back,
    newest first: `{id, thread_id, thread, text, at}`. `id` is the press
    that posted it, which a Take back names."""
    edits = _edits(events)
    undone = _undone(edits)
    out = []
    for e in edits:
        d = e["data"]
        if d.get("kind") != "stream_edit" or d.get("action") != "update" \
                or str(d.get("hub_choice")) in undone or not d.get("new"):
            continue
        tid = str(d.get("target_id") or "")
        out.append({"id": str(d.get("hub_choice") or ""), "thread_id": tid,
                    "thread": str((names or {}).get(tid) or d.get("old") or "")[:160],
                    "text": str(d["new"])[:240], "at": str(e.get("ts") or ""),
                    "seq": e.get("seq") if isinstance(e.get("seq"), int) else -1})
    out.sort(key=lambda u: (u["at"], u["seq"]), reverse=True)
    for u in out:
        u.pop("seq", None)
    return out


def main(argv=None) -> int:
    import json

    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: hub_work.py <workspace>")
        return 2
    print(json.dumps(work_block(args[0]), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
