#!/usr/bin/env python3
"""BACKFILL2 — R3b: the binding backfill as a one-tap Cowork widget.

BACKFILL1 (`backfill_bindings.py`) is the ENGINE and is not touched here: it
derives the candidate rows (the gauge's own walk), refuses name-only and
contested rows, writes one `reclassification` per accepted row in one
`bkf_` batch, records every rejection with its evidence tier in the
`binding_backfill_run` receipt, rebuilds the gauge on apply, and reverses
through `brain_undo`. This module is the SURFACE: it turns the propose
report into a `render_and_persist` data view (rows grouped by project,
three verbs per row — `bind` / `not this project` / `skip`), and turns the
apply-choices tuples that come back into ONE `backfill_bindings.apply` call.

THE NO-AUTO FENCE, RESTATED FOR THE WIDGET PATH. Nothing here decides
anything. A row reaches `apply` ONLY as an explicit `bind` or
`not this project` tuple the user tapped; `skip` writes a one-day mute and
adjudicates nothing; an unknown verb on a binding row is refused, never
coerced into a bind. "Apply all" applies the taps, not the page.

THE SNAPSHOT FENCE RIDES THE PAYLOAD. The wire id of every row is
`bind:<seq>:<snapshot_max_seq>` — the propose snapshot the row was derived
from is baked into the id the button carries, so a click that lands hours
later (Cowork chats are persistent) hands `apply` the snapshot the user
actually READ, and `backfill_bindings.apply` refuses if the substrate has
moved past it (its own exact-match fence). Rows from two different renders
in one batch are refused wholesale — one adjudication, one snapshot.

DISPATCH HOME. The rows render with `source_skill: "needs-your-call"` (the
skill that owns "the operator adjudicates rows") and a `bind:` wire-id
prefix, exactly as the `pcand:` person-candidate rows do on the same source
— apply-choices routes on the prefix, and everything else on that source is
untouched.

PRECISION DATASET. `bind` → an accepted seq; `not this project` → a rejected
seq. Both reach `backfill_bindings.apply`, whose receipt records each with
its evidence tier — the same record the CLI sitting produces. The widget
adds no second ledger.

stdlib + sibling shared/scripts modules only.
"""
from __future__ import annotations

import datetime as _dt
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import backfill_bindings as bb  # noqa: E402

SOURCE_SKILL = "needs-your-call"
WIRE_PREFIX = "bind:"
# The mute ledger's `surface` / `item_class` for a skipped row — what
# `show muted` shows and what the upstream filter in `render_backfill_page`
# reads back through `active_dismissal_target_ids`.
MUTE_SURFACE = "binding-review"
MUTE_ITEM_CLASS = "binding_candidate"

#: The wire-id prefix of a write-time binding QUESTION row (REVIEW_BIND1
#: F4a). Deliberately NOT the `bind:` prefix: these rows are a different
#: population with a different resolution path, and `parse_wire_id` refuses
#: them so `apply_choices` can never coerce one into a bind.
QUESTION_PREFIX = "ask:"
#: The section a question row renders under — its own, above the derived
#: rows, so the two populations are never read as one list.
QUESTION_SECTION_TITLE = "Which project does this belong to?"
#: The DISPLAY-number prefix of a question row (RE-VERIFY 1 N1). The two
#: populations on this page must not share a numbering space. They did for
#: one round, and the cost was exact: a question consumed display number 1,
#: every past record below it shifted by one, and the documented typed
#: answer ("bind 1") filed a record the customer had not named — with a
#: success ack and an `undo` offer, so it read as having worked. The past
#: records keep 1, 2, 3 … (what `report["rows"]` order has always meant)
#: and a question is Q1, Q2 …, which belongs to no other list on the page.
QUESTION_DISPLAY_PREFIX = "Q"

BIND_ACTION = "bind"
REJECT_ACTION = "not this project"
SKIP_ACTION = "skip"
# ONE list, read by the data-view builder — the same discipline as
# `needs_review_queue.QUEUE_ROW_ACTIONS`.
ROW_ACTIONS = [BIND_ACTION, REJECT_ACTION, SKIP_ACTION]

_WIRE_RE = re.compile(r"^bind:(\d+):(\d+)$")

# Plain words for the engine's corroborator names — the evidence the user
# weighs. Never the machine tokens.
_EVIDENCE_WORDS = {
    "person_overlap": "same people",
    "org_link": "same organization",
    "source_ref_adjacency": "same meeting or thread",
    "temporal_cluster": "same week as its other work",
}


def wire_id(seq: int, snapshot_max_seq: int) -> str:
    return f"{WIRE_PREFIX}{int(seq)}:{int(snapshot_max_seq)}"


def parse_wire_id(n) -> Optional[tuple]:
    """`(seq, snapshot_max_seq)` for a binding row's wire id, None for
    anything else (a different source's id, or a malformed one)."""
    m = _WIRE_RE.match(str(n or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def mute_target(seq: int) -> str:
    """The mute ledger's `target_id` for a skipped row: `bind:<seq>` — the
    record's stable identity WITHOUT the snapshot. Every append (a skip's own
    dismissal included) advances the substrate's high-water mark, so a mute
    keyed on the full wire id would stop matching the very next render."""
    return f"{WIRE_PREFIX}{int(seq)}"


# ---------------------------------------------------------------------------
# Row shaping (the ONLY place binding rows are shaped for a widget)
# ---------------------------------------------------------------------------


def _date_words(ts: str) -> str:
    if not ts:
        return "undated"
    try:
        d = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts[:10]
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def _kind_words(event_type: str) -> str:
    t = (event_type or "").replace("_", " ").strip()
    return t or "record"


def _evidence_words(row: dict) -> str:
    corr = [_EVIDENCE_WORDS.get(c, c.replace("_", " "))
            for c in row.get("corroborators") or []]
    if not corr:
        return "evidence: the name alone"
    return "evidence: the name + " + ", ".join(corr)


def _binding_words(row: dict, name_by_tid: dict) -> str:
    refs = row.get("current_refs") or []
    if not refs:
        return "not filed under any project yet"
    names = [name_by_tid.get(t, "another project") for t in refs]
    return ("already filed under " + ", ".join(names)
            + " — binding ADDS this project alongside")


def _k_line(t: dict) -> str:
    """The per-project footer: what this project's taps buy (spec §M.2 —
    k-to-READY, in words). Never the gauge's own vocabulary."""
    k = int(t.get("k_to_ready") or 0)
    n = int(t.get("proposable_rows") or 0)
    if k == 0:
        return "This project's history already holds up on its own."
    if t.get("ready_reachable"):
        noun = "binding" if k == 1 else "bindings"
        return (f"{k} more confirmed {noun} and this project's history "
                f"holds up on its own — {n} on offer here.")
    return (f"Binding alone can't get this project there ({k} needed, {n} "
            f"on offer) — each bind still makes its history truer.")


def _row_item(row: dict, snapshot: int, display_n: int,
              name_by_tid: dict) -> dict:
    preview = (row.get("preview") or "").strip()
    kind = _kind_words(row.get("event_type") or "")
    name = preview or f"a {kind} with no readable text"
    term = row.get("matched_term") or ""
    tag = " · ".join(b for b in (
        _date_words(row.get("ts") or ""),
        kind,
        f"names “{term}”" if term else "",
        _evidence_words(row),
        _binding_words(row, name_by_tid),
    ) if b)
    return {
        "n": wire_id(row["seq"], snapshot),   # wire id — snapshot rides it
        "display_n": display_n,
        "name": name,
        "context_tag": tag,
        "actions": list(ROW_ACTIONS),
    }


def _thread_names(workspace_root) -> dict:
    """thread id -> display name off entities.json, for the "already filed
    under …" words on bound-elsewhere rows (the report names only in-scope
    threads). Any failure degrades to an empty map."""
    try:
        import json

        from entities_io import entities_collection, unwrap_entities

        doc = json.loads((Path(workspace_root) / "_hq" / "data"
                          / "entities.json").read_text(encoding="utf-8"))
        ent = unwrap_entities(doc) if isinstance(doc, dict) else {}
        return {t["id"]: str(t.get("canonical_name") or t.get("display_name")
                              or t["id"])
                for t in entities_collection(ent, "threads")
                if isinstance(t, dict) and t.get("id")}
    except Exception:  # pragma: no cover — names are a nicety, never a gate
        return {}


def _question_item(q: dict, display_n: str) -> dict:
    """One write-time binding question as a page row. READ-ONLY: it carries
    no verbs, because on this tree a `thread_binding` proposal has no
    confirm-side consumer in `apply-choices` (EVENT_TYPES.md's own
    Thread-binding lane says so, and `thread_subjects.propose_split` cites
    it as the gap it declined to repeat). Offering a verb that writes
    nothing would be a dead tap; showing the question where every artifact
    says it lands is the fix M ruled for. The handler is a seam.

    `display_n` arrives ALREADY formatted (`Q1`, `Q2` …) and is never an
    integer: RE-VERIFY 1 N1. A typed answer resolves against the number the
    page printed, so a question must never print a number a past record
    could also be printing."""
    line = q.get("render_line") or (
        f"{q.get('title') or 'a new row'} could belong to more than one "
        "project.")
    bits = [b for b in (q.get("evidence") or "",
                        "nothing is filed either way until you say")
            if b]
    return {
        "n": f"{QUESTION_PREFIX}{q.get('proposal_id') or ''}",
        "display_n": display_n,
        "name": line,
        "context_tag": " \u00b7 ".join(bits),
        "actions": [],
    }


def build_backfill_data_view(report: dict, *, muted_ids: Iterable[str] = (),
                             header: str | None = None,
                             thread_names: dict | None = None) -> dict:
    """The propose report as a `render_and_persist` data view — one SECTION
    per project, rows numbered across the page, the k-to-READY line as the
    section footer. `muted_ids` (live `skip` mutes, read upstream by the
    caller) are dropped BEFORE shaping — the mute filter runs in the view
    build, never in the transport."""
    # `snapshot_max_seq` is None on a ledger with no human-written row yet
    # (a brand-new workspace). The page must render its all-clear there, not
    # raise: `int(None)` is a TypeError, and 0 is the honest snapshot for a
    # substrate nothing has been written to.
    snapshot = int(report.get("snapshot_max_seq") or 0)
    muted = {str(m) for m in muted_ids}
    name_by_tid = dict(thread_names or {})
    name_by_tid.update({t["thread_id"]: t["thread_name"]
                        for t in report.get("per_thread") or []})
    for r in report.get("rows") or []:
        name_by_tid.setdefault(r["thread_id"], r.get("thread_name") or "")
        for ref in r.get("current_refs") or []:
            name_by_tid.setdefault(ref, "another project")

    rows_by_tid: dict = {}
    for r in report.get("rows") or []:
        if mute_target(r["seq"]) in muted:
            continue
        rows_by_tid.setdefault(r["thread_id"], []).append(r)

    sections = []
    display_n = 0
    # REVIEW_BIND1 F4a — the write-time questions first, in their own
    # section: they are the newest thing on the door and the shortest to
    # answer, and keeping them out of the per-project sections keeps the
    # two populations (a question about a row written today; a past record
    # the gauge matched by name) from reading as one list.
    #
    # AND IN THEIR OWN NUMBERING (RE-VERIFY 1 N1). `display_n` counts the
    # PAST RECORDS only, so row N is still `report["rows"]`'s Nth row and
    # the bottom row is still reachable by the number beside it; a question
    # is `Q1`, `Q2` …. Sharing one counter is what let a typed "bind 1"
    # file a record the customer never named.
    questions = report.get("binding_questions") or []
    if questions:
        items = []
        for q_n, q in enumerate(questions, start=1):
            items.append(
                _question_item(q, f"{QUESTION_DISPLAY_PREFIX}{q_n}"))
        sections.append({
            "title": QUESTION_SECTION_TITLE,
            "count": len(items),
            "items": items,
            "footer_note": (
                "These came up as rows were written — Command Room could "
                "see the company but not which of its projects. Nothing is "
                "filed either way. Answering them is not wired yet; for "
                "now this is the record of what could not be filed."),
        })
    for t in report.get("per_thread") or []:
        rows = rows_by_tid.get(t["thread_id"]) or []
        if not rows:
            continue
        items = []
        for r in rows:
            display_n += 1
            items.append(_row_item(r, snapshot, display_n, name_by_tid))
        sections.append({
            "title": t["thread_name"],
            "count": len(items),
            "items": items,
            "footer_note": _k_line(t),
        })
    n_past = display_n
    n_q = len(questions)
    n_rows = n_past + n_q
    if n_q and not n_past:
        default_header = (
            f"Project bindings — {n_q} new "
            f"{'row needs' if n_q == 1 else 'rows need'} a project. "
            "Nothing is filed either way until you say which one.")
    else:
        default_header = (
            f"Project bindings — {n_past} past "
            f"{'record names' if n_past == 1 else 'records name'} a project "
            "they were never filed under. Bind it, or say it's not that "
            "project; nothing moves until you tap.")
        if n_q:
            default_header += (
                f" Plus {n_q} new "
                f"{'row' if n_q == 1 else 'rows'} waiting on which project "
                "they belong to.")
    return {
        "source_skill": SOURCE_SKILL,
        "header": header or default_header,
        "sections": sections,
    }


def _live_muted_ids(workspace_root, now_iso: Optional[str]) -> set:
    """Wire ids with a live `skip` mute — the standing liveness filter,
    scoped to this lane's prefix. Any failure degrades to no filter."""
    try:
        import events_io
        from mute_ledger import active_dismissal_target_ids

        events, _skipped = events_io.load_events_owner_scoped(workspace_root)
        now = now_iso or _dt.datetime.now(_dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        return {t for t in active_dismissal_target_ids(events, now)
                if str(t).startswith(WIRE_PREFIX)}
    except Exception:  # pragma: no cover — the page must still render
        return set()


def render_backfill_page(workspace_root, *, page: int = 1, persist_dir=None,
                         now_iso: Optional[str] = None, extra_thread_ids=(),
                         max_rows: Optional[int] = None) -> dict:
    """Propose (read-only), drop live-muted rows, shape, page by WHOLE
    project, render ONE page through the canonical transport. The skill's
    single call.

    Returns the transport dict plus `group_pagination`, `report` (the
    propose report — `snapshot_max_seq` is what every row's wire id
    carries), `n_rows`, and `empty` (True with a one-line `line` when there
    is nothing to adjudicate — nothing is rendered then)."""
    from needs_review_queue import resolve_page
    from widget_transport import render_and_persist

    ws = Path(workspace_root)
    report = bb.propose(ws, extra_thread_ids=extra_thread_ids, now_iso=now_iso)
    muted = _live_muted_ids(ws, now_iso)
    data_view = build_backfill_data_view(report, muted_ids=muted,
                                         thread_names=_thread_names(ws))
    n_rows = sum(len(s["items"]) for s in data_view["sections"])
    if not n_rows:
        r = report["refused"]
        held = sum(1 for x in (report.get("rows") or [])
                   if mute_target(x["seq"]) in muted)
        line = ("Nothing to file — every past record that names a project is "
                "either already filed under it or too thin to propose")
        if held:
            line += f" ({held} snoozed until tomorrow)"
        line += "."
        return {"empty": True, "line": line, "report": report, "n_rows": 0,
                "refused": r}
    # PLATENUM1 4.5 — same pattern, same fix: the caller's page reaches the
    # transport, so a group the byte fit re-slices is still walkable.
    paged = resolve_page(data_view, page=page, max_rows=max_rows)
    gp = paged["group_pagination"]
    transport = render_and_persist(
        data_view=paged["page_view"], wrapper="fragment",
        persist_dir=str(persist_dir or (ws / "_hq" / ".system" / "widgets")),
        name_hint="binding-review", page=paged["sub_page"],
        page_size=paged["page_size"])
    if gp["sub_pages"] > 1:
        gp["group_split_by_budget"] = True
    transport["group_pagination"] = gp
    transport["report"] = report
    transport["n_rows"] = n_rows
    # RE-VERIFY 1 N1 — the items THIS page rendered, in page order, each
    # carrying the display number it PRINTED and its own wire id. A typed
    # answer resolves against this list (`typed_answer_choices`) and never
    # against `report["rows"]` by position: `report` is the whole propose,
    # this page is one project-slice of it, and the binding questions are a
    # second population with their own numbering.
    # MF-13-2 (RE-VERIFY 2 O1): `page_view` is the GROUP page; the byte fit
    # rendered ONE sub-page of it. `items` is the rendered window only, so a
    # number the page did not print is refused, never dispatched.
    _flat = [it for sec in (paged["page_view"].get("sections") or [])
             for it in (sec.get("items") or []) if isinstance(it, dict)]
    _start = (int(paged["sub_page"]) - 1) * int(paged["page_size"])
    transport["items"] = _flat[_start:_start + int(paged["page_size"])]
    transport["empty"] = False
    return transport


def _page_items(transport) -> list:
    """The rendered items of one page, in page order. Accepts the transport
    `render_backfill_page` returned, or a raw data view (`sections`)."""
    if not isinstance(transport, dict):
        return []
    items = transport.get("items")
    if isinstance(items, list):
        return [it for it in items if isinstance(it, dict)]
    secs = transport.get("sections")
    if isinstance(secs, list):
        return [it for sec in secs if isinstance(sec, dict)
                for it in (sec.get("items") or []) if isinstance(it, dict)]
    return []


def _display_key(value) -> str:
    """One spelling for a display number, typed or printed: `1`, `1.`, `Q1`
    and `q1` are the same row."""
    return str(value if value is not None else "").strip().rstrip(".").lower()


def typed_answer_choices(transport, answers) -> dict:
    """Turn what the user TYPED ("bind 1 and 3, not 2") into apply-choices
    tuples, resolved BY THE ROW'S OWN ID.

    `answers` = `[{"number": <what they said>, "action": <verb>,
    "context": <optional note>}, …]`, in the order they said them. Each
    number is looked up against the number THIS PAGE PRINTED
    (`item["display_n"]`) and the tuple carries that item's own `n` — the
    wire id — so a row can only ever be adjudicated as itself.

    NEVER index `report["rows"]` by position (RE-VERIFY 1 N1). Two things
    break that arithmetic: the binding questions are a separate population
    rendered above the records, and `report` is the whole propose while a
    page is one project-slice of it. Either one silently files a record the
    customer did not name, with a success ack and an `undo` offer.

    Returns `{"choices": [...], "refused": [{number, action, detail}],
    "lines": [one plain sentence per refusal]}`. `choices` goes straight to
    `apply_choices`; the lines are said out loud — a number that resolved to
    nothing is never quietly dropped, and never guessed at."""
    by_display: dict = {}
    for it in _page_items(transport):
        key = _display_key(it.get("display_n"))
        if key and key not in by_display:
            by_display[key] = it

    choices: list = []
    refused: list = []
    lines: list = []
    for a in answers or []:
        if not isinstance(a, dict):
            continue
        typed = str(a.get("number") if a.get("number") is not None
                    else "").strip()
        action = str(a.get("action") or "").strip().lower()
        item = by_display.get(_display_key(typed))
        if item is None:
            detail = (f"there's no {typed or 'row'} on this page — nothing "
                      "was filed for it")
        elif not (item.get("actions") or []):
            # A question row. It has no seq and no snapshot, so there is
            # nothing for the engine to adjudicate against; saying so is
            # the honest answer and dispatching the row below it is not.
            detail = (f"{typed} is a question about a row written today, "
                      "not one of the past records this list files — "
                      "nothing to file for it yet")
        else:
            choice = {"n": item["n"], "action": action}
            note = str(a.get("context") or "").strip()
            if note:
                choice["context"] = note
            choices.append(choice)
            continue
        refused.append({"number": typed, "action": action, "detail": detail})
        lines.append(detail)
    return {"choices": choices, "refused": refused, "lines": lines}


# ---------------------------------------------------------------------------
# Apply — the rail's one call for `bind:` rows
# ---------------------------------------------------------------------------


def skip_rows(workspace_root, wire_ids: Iterable[str], *,
              source_skill: str = SOURCE_SKILL,
              now_iso: Optional[str] = None) -> list:
    """`skip` on binding rows: one standard `chat_dismissal` per wire id —
    1 day (the taxonomy's TTL for `skip`, the number the button states),
    `data.target_id` = the wire id verbatim (what the upstream filter reads
    back), the Loop-2 fingerprint stamped. Adjudicates NOTHING: the row is
    not accepted, not rejected, not recorded in the precision dataset — it
    simply stops rendering until tomorrow."""
    from event_gate import append_event
    from surface_preferences import dismissal_fingerprint
    from verb_taxonomy import mute_ttl_days

    parsed = [(str(w), parse_wire_id(w)) for w in wire_ids]
    ids = [(w, p[0]) for w, p in parsed if p]
    if not ids:
        return []
    ttl = mute_ttl_days(SKIP_ACTION) or 1
    if now_iso:
        now = _dt.datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        if now.tzinfo is None:
            now = now.replace(tzinfo=_dt.timezone.utc)
    else:
        now = _dt.datetime.now(_dt.timezone.utc)
    until = (now + _dt.timedelta(days=int(ttl))).strftime("%Y-%m-%dT%H:%M:%SZ")
    fp = dismissal_fingerprint(MUTE_SURFACE, MUTE_ITEM_CLASS, None)
    events = [{
        "type": "chat_dismissal",
        "source_skill": source_skill,
        "primary_thread_id": "",
        "data": {
            "target_id": mute_target(seq),
            "surface": MUTE_SURFACE,
            "item_class": MUTE_ITEM_CLASS,
            "fingerprint": fp,
            "snooze_until": until,
            "via": "backfill_widget.skip_rows",
        },
    } for _w, seq in ids]
    append_event(Path(workspace_root) / "_hq" / "data" / "events.jsonl",
                 events, holder=source_skill)
    return [{"n": w, "action": SKIP_ACTION, "status": "dismissed",
             "target_id": mute_target(seq), "snooze_until": until}
            for w, seq in ids]


def apply_choices(workspace_root, choices: list, *, applied_by: str,
                  now_iso: Optional[str] = None,
                  source_skill: str = SOURCE_SKILL) -> dict:
    """Dispatch the `bind:` tuples of one Apply — the rail's ONE call.

    `choices` = the apply-choices tuples (`{n, action, ...}`); tuples whose
    `n` is not a binding wire id are ignored here (they belong to another
    handler on the same source). `applied_by` = the operator identity the
    rail already resolved (`primary_user.resolve_primary_user`); an EMPTY
    one refuses the whole call — an unattributed adjudication is not a
    precision record.

      bind              -> accepted seq   (one `bkf_` batch per Apply)
      not this project  -> rejected seq   (recorded with its tier, never
                                           re-proposed)
      skip              -> one-day mute, nothing adjudicated
      anything else     -> refused for that row, nothing written

    All bind / not-this-project rows in one Apply must carry the SAME
    snapshot (one render = one adjudication); `backfill_bindings.apply`
    then refuses if the substrate moved past it. Returns
    {status, results:[{n, action, status, ...}], batch_id, batch_ref,
    n_applied, n_rejected, n_skipped, n_refused, receipt, summary} — each
    `results` entry is that tuple's `handler_result` for the audit event."""
    results: list = []
    accepts: dict = {}
    rejects: dict = {}
    skips: list = []
    snapshots: set = set()

    for ch in choices or []:
        if not isinstance(ch, dict):
            continue
        n = ch.get("n")
        parsed = parse_wire_id(n)
        if parsed is None:
            if str(n or "").startswith(QUESTION_PREFIX):
                # A write-time binding question, not a backfill row. It has
                # no verbs on the page and it is NOT adjudicated through
                # this rail: `backfill_bindings.apply` writes a
                # `reclassification` against a SEQ and a snapshot, and a
                # question has neither. Refused out loud rather than
                # silently dropped.
                results.append({
                    "n": str(n), "action": str(ch.get("action") or ""),
                    "status": "refused",
                    "detail": "that row is a question about a new item, not "
                              "one of the past records this list files — "
                              "nothing was written for it"})
            continue
        seq, snap = parsed
        action = str(ch.get("action") or "").strip().lower()
        if action == BIND_ACTION:
            accepts[seq] = str(n)
            snapshots.add(snap)
        elif action == REJECT_ACTION:
            rejects[seq] = str(n)
            snapshots.add(snap)
        elif action == SKIP_ACTION:
            skips.append(str(n))
        else:
            # The no-auto fence's dispatch half: an unfamiliar verb on a
            # binding row never becomes a bind by default.
            results.append({"n": str(n), "action": action, "status": "refused",
                            "detail": "not a verb this row offers — nothing "
                                      "was written for it"})

    if not accepts and not rejects and not skips:
        # Refusals only (unfamiliar verbs) → the batch IS refused; no
        # binding rows at all → an honest no-op. Nothing written either way.
        return {"status": "refused" if results else "noop",
                "results": results, "batch_id": None,
                "batch_ref": None, "n_applied": 0, "n_rejected": 0,
                "n_skipped": 0, "n_refused": len(results),
                "summary": ("Those aren't answers this list takes — nothing "
                            "was written." if results else
                            "No binding rows in this batch — nothing "
                            "written.")}

    if (accepts or rejects) and not (applied_by or "").strip():
        for seq, n in list(accepts.items()) + list(rejects.items()):
            results.append({
                "n": n, "action": BIND_ACTION if seq in accepts
                else REJECT_ACTION, "status": "refused",
                "detail": "no operator identity on file — an unattributed "
                          "adjudication is not a precision record"})
        return {"status": "refused", "results": results, "batch_id": None,
                "batch_ref": None, "n_applied": 0, "n_rejected": 0,
                "n_skipped": 0, "n_refused": len(results),
                "summary": ("I can't record who decided these until this "
                            "workspace knows who you are — nothing was "
                            "written. Snoozing still works.")}

    if len(snapshots) > 1:
        for seq, n in list(accepts.items()) + list(rejects.items()):
            results.append({
                "n": n, "action": BIND_ACTION if seq in accepts
                else REJECT_ACTION, "status": "refused",
                "detail": "rows from two different renders in one batch — "
                          "one adjudication, one snapshot"})
        return {"status": "refused", "results": results, "batch_id": None,
                "batch_ref": None, "n_applied": 0, "n_rejected": 0,
                "n_skipped": 0, "n_refused": len(results),
                "summary": ("Those rows came from two different pages of "
                            "this list — say `review project bindings` for "
                            "a fresh one and answer from that. Nothing was "
                            "written.")}

    overlap = sorted(set(accepts) & set(rejects))
    if overlap:
        for seq in overlap:
            results.append({"n": accepts[seq], "action": BIND_ACTION,
                            "status": "refused",
                            "detail": "the same row was both bound and "
                                      "refused in one batch"})
            accepts.pop(seq, None)
            rejects.pop(seq, None)

    # ORDER MATTERS: the engine's apply runs FIRST. A skip's own dismissal is
    # an append, and every append advances the substrate's high-water mark —
    # writing the mutes first would make the same Apply's binds read as
    # stale against the snapshot the user just tapped on.
    batch_id = None
    batch_ref = None
    receipt = None
    n_applied = n_rejected = 0
    engine_status = None
    if accepts or rejects:
        (snapshot,) = tuple(snapshots)
        res = bb.apply(workspace_root,
                       accept_seqs=sorted(accepts), reject_seqs=sorted(rejects),
                       snapshot_max_seq=snapshot, applied_by=applied_by,
                       source_skill=source_skill, now_iso=now_iso)
        engine_status = res.get("status")
        if engine_status == "stale_snapshot":
            for seq, n in list(accepts.items()) + list(rejects.items()):
                results.append({
                    "n": n, "action": BIND_ACTION if seq in accepts
                    else REJECT_ACTION, "status": "stale_snapshot",
                    "detail": res.get("summary")})
        else:
            applied_seqs = {r["seq"] for r in
                            (res.get("receipt") or {}).get("applied_rows") or []}
            rejected_seqs = {r["seq"] for r in
                             (res.get("receipt") or {}).get("rejected") or []}
            for seq, n in accepts.items():
                results.append({"n": n, "action": BIND_ACTION,
                                "status": "applied" if seq in applied_seqs
                                else "unchanged"})
            for seq, n in rejects.items():
                results.append({"n": n, "action": REJECT_ACTION,
                                "status": "recorded" if seq in rejected_seqs
                                else "unchanged"})
            batch_id = res.get("batch_id")
            batch_ref = res.get("batch_ref")
            receipt = res.get("receipt")
            n_applied = int(res.get("n_applied") or 0)
            n_rejected = int(res.get("n_rejected") or 0)

    # skip — a mute, not an adjudication; written AFTER the engine's apply.
    skip_results = skip_rows(workspace_root, skips, source_skill=source_skill,
                             now_iso=now_iso) if skips else []
    results.extend(skip_results)

    n_refused = sum(1 for r in results if r["status"] == "refused")
    n_skipped = len(skip_results)
    if engine_status == "stale_snapshot":
        status = "stale_snapshot"
        summary = ("This list is out of date — the workspace changed since "
                   "it was drawn. Say `review project bindings` for a fresh "
                   "one; nothing was written.")
    elif n_applied:
        status = "applied"
        summary = _summary(n_applied, n_rejected, n_skipped, n_refused,
                           receipt, batch_id)
    elif n_rejected:
        status = "recorded"
        summary = _summary(0, n_rejected, n_skipped, n_refused, receipt, None)
    elif n_skipped:
        status = "dismissed"
        summary = _summary(0, 0, n_skipped, n_refused, None, None)
    else:
        status = "refused" if n_refused else "unchanged"
        summary = _summary(0, 0, 0, n_refused, None, None)
    return {
        "status": status,
        "results": results,
        "batch_id": batch_id,
        "batch_ref": batch_ref,
        "n_applied": n_applied,
        "n_rejected": n_rejected,
        "n_skipped": n_skipped,
        "n_refused": n_refused,
        "receipt": receipt,
        "summary": summary,
    }


def _summary(n_applied, n_rejected, n_skipped, n_refused, receipt,
             batch_id) -> str:
    bits = []
    if n_applied:
        bits.append(f"Filed {n_applied} under "
                    f"{'its' if n_applied == 1 else 'their'} project")
    if n_rejected:
        bits.append(f"marked {n_rejected} as not that project (I won't "
                    f"suggest {'it' if n_rejected == 1 else 'them'} again)")
    if n_skipped:
        bits.append(f"snoozed {n_skipped} until tomorrow")
    if n_refused:
        bits.append(f"left {n_refused} untouched")
    s = "; ".join(bits) if bits else "Nothing changed"
    s = s[0].upper() + s[1:] + "."
    if receipt and n_applied:
        before, after = receipt.get("ready_before"), receipt.get("ready_after")
        if after is not None and before is not None and after != before:
            s += (f" {after - before} more "
                  f"{'project' if after - before == 1 else 'projects'} now "
                  "have a history that holds up on its own.")
    if batch_id:
        s += " Say `undo` to reverse the sitting."
    return s


# ---------------------------------------------------------------------------
# BIND1 4.2 - the condition of the plumbing, on the maintenance surface only
# ---------------------------------------------------------------------------
#
# Ruling 8: bind at the WRITE this night; the backfill of the rows already
# open is a bulk write over the customer's live book and is a separate pass
# on M's word. So the open rows stay as they are, and the 2026-09-07
# three-surfaces rule puts the condition of the plumbing on the MAINTENANCE
# report - one line, nowhere else. Not the brief, not the day-close, not the
# wrap: this is a number about the product, not about the customer's work.
#
# Counted through the PRODUCT'S OWN PROJECTION (`plate_view.build_plate`),
# never a naive ledger roll-up: the planner's roll-up of the same copy said
# 130 of 637 because it counted every commitment row the ledger ever carried
# rather than the confirmed-open rows the plate actually shows. The plate's
# own unbound signal is `NO_PROJECT_KEY` - the key `project_group` returns
# for a row whose thread has no name on file - which is exactly the set
# `brief_settings.group_rows` drops into `GROUP_FALLBACK`.

#: The one sentence. No count in it but the two numbers, no verb the reader
#: has to act on - the maintenance report states conditions, it does not ask.
UNBOUND_LINE_NONE = "Every open row on the plate knows which project it belongs to."


def unbound_open_rows(workspace_root, *, preset: str = "engaged",
                      now_iso=None) -> dict:
    """`{"open": n, "unbound": n, "by_project_label": {label: n}}` - open
    plate rows that carry no project binding, through the product's own
    projection. `by_project_label` counts EVERY open row by the group label
    the plate itself renders it under, unbound rows included (they land
    under `plate_view.NO_PROJECT_LABEL`).

    A refused plate (an unresolvable user, D8) returns `{"open": 0,
    "unbound": 0, "error": <the plate's own line>}` rather than a zero that
    reads as good news. PURE READ."""
    from plate_view import build_plate, NO_PROJECT_KEY, NO_PROJECT_LABEL

    view = build_plate(workspace_root, preset=preset, now_iso=now_iso)
    if view.get("error"):
        return {"open": 0, "unbound": 0, "by_project_label": {},
                "error": view["error"]}

    seen: dict = {}

    def _walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "rows" and isinstance(value, list):
                    for row in value:
                        if isinstance(row, dict) and row.get("id"):
                            seen[row["id"]] = row
                else:
                    _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)

    _walk(view.get("blocks") or {})
    unbound = [r for r in seen.values()
               if str(r.get("project_key") or "") == NO_PROJECT_KEY]
    # REVIEW_BIND1 F9 - this key was documented and returned EMPTY, every
    # time. It is populated now, off the plate's own group label (the same
    # `plate_view.project_group` label every grouped surface renders), so
    # "which projects are these rows in?" is answerable from the census
    # rather than being a promise the return shape never kept. The
    # maintenance sentence does not name a project and does not read it;
    # it is here for the operator report that asks the follow-up question.
    by_label: dict = {}
    for row in seen.values():
        label = str(row.get("project_label") or "").strip()
        if not label:
            label = (NO_PROJECT_LABEL
                     if str(row.get("project_key") or "") == NO_PROJECT_KEY
                     else "")
        if not label:
            continue
        by_label[label] = by_label.get(label, 0) + 1
    return {"open": len(seen), "unbound": len(unbound),
            "by_project_label": by_label}


def unbound_rows_maintenance_line(workspace_root, *, preset: str = "engaged",
                                  now_iso=None) -> str:
    """ONE plain sentence for the weekly maintenance report, or
    `UNBOUND_LINE_NONE` when nothing is unbound. Never a question, never an
    offer, and never rendered on a customer surface - the maintenance report
    is the only place this sentence is allowed to appear (HEALTH1's ruling,
    applied to this number)."""
    counts = unbound_open_rows(workspace_root, preset=preset, now_iso=now_iso)
    if counts.get("error"):
        return ("The plate could not be read, so the project-binding count is "
                "not available this run.")
    n, total = int(counts["unbound"]), int(counts["open"])
    if not n:
        return UNBOUND_LINE_NONE
    return ("%d of %d open rows on the plate carry no project binding, so they "
            "group under \"Everything else\". New rows bind as they are "
            "written; the rows already open are a separate backfill pass."
            % (n, total))


def undo_sitting(workspace_root, batch_id: str, *, undone_by: str,
                 source_skill: str = SOURCE_SKILL) -> dict:
    """Reverse one Apply's `bkf_` batch through the standing undo lane and
    rebuild the gauge once — `backfill_bindings.undo`, unchanged."""
    return bb.undo(workspace_root, batch_id, undone_by=undone_by,
                   source_skill=source_skill)


__all__ = [
    "SOURCE_SKILL",
    "WIRE_PREFIX",
    "MUTE_SURFACE",
    "MUTE_ITEM_CLASS",
    "BIND_ACTION",
    "REJECT_ACTION",
    "SKIP_ACTION",
    "ROW_ACTIONS",
    "wire_id",
    "parse_wire_id",
    # RE-VERIFY 1 N1
    "QUESTION_PREFIX",
    "QUESTION_SECTION_TITLE",
    "QUESTION_DISPLAY_PREFIX",
    "typed_answer_choices",
    "build_backfill_data_view",
    "render_backfill_page",
    "skip_rows",
    "apply_choices",
    "undo_sitting",
    # BIND1 4.2
    "UNBOUND_LINE_NONE",
    "unbound_open_rows",
    "unbound_rows_maintenance_line",
]
