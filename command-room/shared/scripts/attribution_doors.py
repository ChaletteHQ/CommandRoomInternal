#!/usr/bin/env python3
"""ATTRIB1-B — the two confirmation DOORS for a capture whose counterparty the
ladder could not resolve, and the lapse that applies the ladder's default.

WHY THIS MODULE EXISTS. ATTRIB1-A made the review flag a DERIVED fact and
ATTRIB1-B's ladder (`meeting_capture.attribute_counterparty`) writes ONE
question on the row when it must ask — `attribution.question = {kind:
who_is_you, options: [person ids], default: person id | None}`. Before this
module the only place that question could be answered was the needs-your-call
queue, which asks "is this real?" and never "who is 'you'?". Two doors open
first:

  DOOR 1 — the meeting card (D8). `card_questions` picks at most
           `CARD_QUESTION_CAP` rows of THIS meeting that carry a question,
           lowest confidence first; `build_card_questions_view` shapes them
           for the Step 9 widget (rendered ONLY through
           `widget_transport.render_and_persist` — never hand-composed);
           the pick arrives as an apply-choices tuple and lands through
           `apply_counterparty_pick`, whose verb spelling is
           `confirm_counterparty:<commitment id>:<person id>`.
  DOOR 2 — the user's own recap (D9). Lives in
           `reconcile_sent_commitments.confirm_pending_captures`: a sent
           message to a meeting attendee within 48h whose text matches a
           pending capture CONFIRMS it (`confirmed_by: own_recap`), never
           closes it.
  LAPSE  — A6 (ruled 2026-09-02): no answer inside the nag window → the
           pre-selected default is APPLIED (`counterparty_basis:
           default_applied`, flag cleared, undo on the batch) — never
           `dropped` for a row that had a best guess. Wired from
           `commitment_backlog_sweep.apply_review_expiry`.

EVERY WRITE GOES THROUGH THE SHIPPED WRITERS. A pick or a default is
`commitment_state.reassign_commitment` (the counterparty lands on the
projection, `commitment_reassigned`) followed by
`commitment_state.clear_review_flags` (the flag clears, `confirmed_by`
says which door — A10). The clear carries the brain-batch fields so the
standing `undo` reverses it through the registered `commitment_confirm`
reverser. Nothing here appends a hand-built event.

D12 — every door-1 pick appends ONE hint line through
`extraction_hints.append_attribution_hint` (LEARN1 owns consumption).

Fixtures use the Sample/Stone placeholder roster only.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# The verb the door-1 pick dispatches on (SPEC D8 / A6 wording). Parsed by
# `parse_pick_token`; composed by `pick_token`.
PICK_VERB = "confirm_counterparty"
# The widget's per-option action — the canonical `confirm` verb on a
# sub-item whose id IS the pick token's tail (`<commitment id>:<person id>`).
PICK_ACTION = "confirm"
# At most this many questions on one meeting card (D8).
CARD_QUESTION_CAP = 3
# A10 — the door names on the clear event's `confirmed_by`.
CONFIRMED_BY_PICK = "user_pick"
CONFIRMED_BY_DEFAULT = "default_applied"
CONFIRMED_BY_OWN_RECAP = "own_recap"
# Hours after the meeting inside which the user's own recap confirms (D9).
OWN_RECAP_WINDOW_HOURS = 48

DOOR_SOURCE_SKILL = "meeting-notes"
LIKELY_TAG = "likely — applies on its own if you don't answer"
# REVIEW_MERGED_v5280 F-5 — a question with NO likely answer says what
# actually happens to it, because the card used to promise "the likely
# answer applies" on rows that had none and were DROPPED after two days.
# The customer was told the opposite of what the product did. The wording
# names the outcome and the way back.
LET_GO_TAG = "no likely answer — let go after two days if nobody picks"
LET_GO_LINE = ("pick who you meant — if nobody does, this one is let go "
               "after two days (`undo` brings it back)")
LIKELY_LINE = "pick who you meant, or leave it and the likely answer applies"
MIXED_LINE = ("pick who you meant — the ones marked likely apply on their "
              "own if you leave them; the others are let go after two days "
              "(`undo` brings any of them back)")


def card_header(questions) -> Optional[str]:
    """The header sentence for a set of questions, TRUE to what the lapse
    will do with each of them (F-5). None when there is nothing to ask.

      every question has a default -> the likely-answer line
      no question has a default    -> the let-go line
      a mix                        -> both, each attached to its rows"""
    qs = list(questions or [])
    n = len(qs)
    if not n:
        return None
    with_default = sum(1 for q in qs if q.get("default"))
    if with_default == n:
        tail = LIKELY_LINE
    elif with_default == 0:
        tail = LET_GO_LINE
    else:
        tail = MIXED_LINE
    return f"{n} quick question{'' if n == 1 else 's'} from this call — {tail}"


def pick_token(commitment_id: str, person_id: str) -> str:
    return f"{PICK_VERB}:{commitment_id}:{person_id}"


def parse_pick_token(token) -> Optional[tuple]:
    """`confirm_counterparty:<cid>:<pid>` → `(cid, pid)`, or None. Also
    accepts the widget's own tuple spelling (`<cid>:<pid>` with the
    `confirm` action) — the sub-item id is the token's tail."""
    s = str(token or "").strip()
    if not s:
        return None
    if s.startswith(PICK_VERB + ":"):
        s = s[len(PICK_VERB) + 1:]
    parts = s.split(":")
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        return None
    return (parts[0].strip(), parts[1].strip())


def question_of(ev: dict) -> Optional[dict]:
    """The `who_is_you` question a projected row carries, or None."""
    d = (ev.get("data") or {}) if isinstance(ev, dict) else {}
    attr = d.get("attribution") if isinstance(d.get("attribution"), dict) else {}
    q = attr.get("question")
    if not isinstance(q, dict) or not isinstance(q.get("options"), list):
        return None
    return q


def question_default(ev: dict) -> str:
    """The pre-selected default on a row's question, or "" (A6 keys on
    this: a row with no best guess follows the ordinary lapse)."""
    q = question_of(ev) or {}
    d = q.get("default")
    return d if isinstance(d, str) and d in (q.get("options") or []) else ""


def _people_names(workspace_root) -> dict:
    from meeting_capture import _load_people, _person_name
    return {p["id"]: _person_name(p) or p["id"]
            for p in _load_people(workspace_root)}


def _commitment_id(ev: dict) -> str:
    from cru_match import _commitment_id as _cid
    return _cid(ev)


def _ref_keys(ref) -> set:
    from meeting_capture import meeting_ref_keys
    return meeting_ref_keys(ref)


def card_questions(workspace_root, source_ref, *, cap: int = CARD_QUESTION_CAP,
                   rows=None, budget: bool = False, now_iso: Optional[str] = None) -> list:
    """DD-7 — the questions ONE meeting's card renders: pending rows of this
    meeting that carry a `who_is_you` question, lowest confidence first, at
    most `cap`. Each entry: `{commitment_id, title, evidence, options:
    [{person_id, name}], default, confidence}`.

    Reads the projected needs-review set through the canonical loader
    (`cru_match.load_needs_review`, adjudication folds applied) — a row
    already answered on another surface is correctly absent. `rows` lets a
    caller hand the projection in (tests, or a surface that already holds
    it).

    QUIET1 D4 — `budget=True` submits the candidates through
    `quiet.submit_questions` as the `meeting_card` asker AFTER the card
    cap: under `light` five questions a week reach the person across every
    asker, ranked by consequence; a question cut by the budget is simply
    not asked and its row takes the A6 lapse default exactly as an
    unanswered one would. A question already asked this week renders
    without spending again. The DEFAULT is the pure per-meeting read
    (REVIEW_QUIET1 F-7: a read must not spend the week as a side effect);
    the ONE place that renders the card, `render_card_questions`, passes
    `budget=True` explicitly."""
    ws = Path(workspace_root)
    if rows is None:
        from cru_match import load_needs_review
        rows = load_needs_review(str(ws / "_hq" / "data" / "events.jsonl"),
                                 workspace_root=str(ws))
    want = _ref_keys(source_ref)
    names = _people_names(workspace_root)
    out: list = []
    for ev in rows or []:
        d = ev.get("data") or {}
        if want and not (_ref_keys(d.get("source_ref")) & want):
            continue
        q = question_of(ev)
        if not q or not q.get("options"):
            continue
        # A2 — the ONE confidence vocabulary is stamped at the event's top
        # level by `stamp_confidence`; the data-level spelling is a legacy
        # fallback. Unscored reads as 1.0 so a scored row asks first.
        conf = ev.get("classification_confidence")
        if not isinstance(conf, (int, float)) or isinstance(conf, bool):
            conf = d.get("classification_confidence")
        conf = float(conf) if isinstance(conf, (int, float)) \
            and not isinstance(conf, bool) else 1.0
        options = [{"person_id": pid, "name": names.get(pid, pid)}
                   for pid in q["options"] if isinstance(pid, str) and pid]
        default = q.get("default") if q.get("default") in q["options"] else None
        # The default FIRST — it is the pre-selected answer.
        options.sort(key=lambda o: 0 if o["person_id"] == default else 1)
        out.append({
            "commitment_id": _commitment_id(ev),
            "title": str(d.get("title") or "").strip(),
            "evidence": str(d.get("evidence") or "").strip(),
            "options": options,
            "default": default,
            "confidence": conf,
            # QUIET1 D4 — the two consequence facts the budget ranks on: a
            # likely-named party (the pre-selected answer) and a date.
            "has_counterparty": bool(default),
            "has_date": bool(d.get("due")),
            "due": d.get("due"),
            "ts": ev.get("ts") or "",
        })
    out.sort(key=lambda r: (r["confidence"], r["commitment_id"]))
    out = out[:max(0, int(cap))]
    if budget and out:
        import quiet
        sub = quiet.submit_questions(ws, quiet.ASKER_MEETING_CARD, out, now_iso=now_iso)
        keep = {r["commitment_id"] for r in sub["render"]}
        out = [r for r in out if r["commitment_id"] in keep]
    return out


def build_card_questions_view(questions, *, source_skill: str = DOOR_SOURCE_SKILL,
                              header: Optional[str] = None) -> dict:
    """The data view for door 1 — one item per question, one sub-item per
    option, the default first and tagged. The widget's Apply-all tuple for a
    pick is `{n: "<commitment id>:<person id>", action: "confirm", src:
    "meeting-notes"}`, which `parse_pick_token` reads. Rendered ONLY through
    `widget_transport.render_and_persist` (the transport runs every gate).

    Returns the `all_batch_widget` data view. Empty `questions` → an empty
    sections list (a caller renders nothing rather than a card that asks
    nothing)."""
    items: list = []
    for i, q in enumerate(questions or [], start=1):
        cid = q["commitment_id"]
        subs: list = []
        for o in q.get("options") or []:
            tag = f" — {LIKELY_TAG}" if o["person_id"] == q.get("default") \
                else ""
            subs.append({"id": f"{cid}:{o['person_id']}",
                         "summary": f"{o['name']}{tag}",
                         "actions": [PICK_ACTION]})
        item = {
            "n": i,
            "icon": "",
            # PLAIN STRINGS. The row's title and the quote are the user's
            # own words, and marking them is the RENDERER's job through its
            # own chokepoint (`chat_output_renderer.mark_field`) — an
            # emitter that assembles the provenance delimiters itself is
            # exactly what the user-text provenance guard forbids.
            "name": f'Who is "you" in "{q["title"]}"?',
            # F-5 — a question with no default SAYS SO on its own row, so
            # the customer never reads "likely answer" beside a row that
            # will be let go instead.
            "context_tag": ((f'you said: "{q["evidence"]}"'
                             if q.get("evidence") else
                             "the calendar names more than one person")
                            + ("" if q.get("default") else f" — {LET_GO_TAG}")),
            "actions": [],
            "sub_items": subs,
            "commitment_id": cid,
        }
        items.append(item)
    n = len(items)
    return {
        "widget_mode": "all_batch_widget",
        "source_skill": source_skill,
        # F-5 — the header is TRUE to what the lapse will do with these
        # rows, never a blanket promise that the likely answer applies.
        "header": header if header is not None else card_header(questions),
        "sections": ([{"title": None, "count": None, "items": items}]
                     if items else []),
        "save_confirmation": None,
    }


def render_card_questions(workspace_root, source_ref, *, persist_dir,
                          cap: int = CARD_QUESTION_CAP, rows=None,
                          name_hint: str = "meeting-notes-questions") -> Optional[dict]:
    """Door 1 end to end: questions → data view → `render_and_persist`.
    Returns the transport dict, or None when the meeting asks nothing."""
    # QUIET1 D4 / F-7 — the render is the one spend site.
    qs = card_questions(workspace_root, source_ref, cap=cap, rows=rows, budget=True)
    if not qs:
        return None
    from widget_transport import render_and_persist
    view = build_card_questions_view(qs)
    return render_and_persist(data_view=view, wrapper="fragment",
                              persist_dir=persist_dir, name_hint=name_hint)


def _pending_row(workspace_root, commitment_id):
    from cru_match import load_needs_review
    ws = Path(workspace_root)
    for ev in load_needs_review(str(ws / "_hq" / "data" / "events.jsonl"),
                                workspace_root=str(ws)):
        if _commitment_id(ev) == str(commitment_id):
            return ev
    return None


def _confirm_counterparty(workspace_root, commitment_id, person_id, *,
                          by, source_skill, confirmed_by, reason, note,
                          brain_batch_id=None, source_ref=None,
                          mint_now_iso=None) -> dict:
    """The one write pair both doors and the lapse share: the counterparty
    lands on the projection through `reassign_commitment` (confirmed — the
    door IS the adjudication), then the flag clears through
    `clear_review_flags` carrying `confirmed_by` (A10) and, when a batch id
    is given, the brain-batch fields the standing `undo` reverses on."""
    from commitment_state import (CommitmentIdError, clear_review_flags,
                                  reassign_commitment)
    names = _people_names(workspace_root)
    try:
        # POLICY1-B F-9 — the reassign rides the SAME batch as the clear, so
        # `undo` puts the counterparty back (to the prior person, or to none)
        # and not only the question.
        r1 = reassign_commitment(
            workspace_root, commitment_id, reassigned_by=by,
            source_skill=source_skill, new_counterparty_id=person_id,
            new_counterparty_name=names.get(person_id) or None,
            reason=reason, confirmed=True,
            brain_batch_id=brain_batch_id or None,
            brain_change_class="commitment_reassign" if brain_batch_id else None)
    except CommitmentIdError as exc:
        return {"status": "not_found", "commitment_id": str(commitment_id),
                "detail": str(exc)}
    if r1.get("status") != "reassigned":
        return {"status": r1.get("status") or "refused",
                "commitment_id": str(commitment_id)}
    kw = dict(cleared_by=by, source_skill=source_skill, note=note,
              source_ref=source_ref, mint_now_iso=mint_now_iso,
              confirmed_by=confirmed_by)
    if brain_batch_id:
        kw["brain_batch_id"] = brain_batch_id
        kw["brain_change_class"] = "commitment_confirm"
    r2 = clear_review_flags(workspace_root, commitment_id, **kw)
    return {"status": "confirmed" if r2.get("status") == "cleared"
            else r2.get("status") or "refused",
            "commitment_id": r2.get("commitment_id", str(commitment_id)),
            "counterparty_id": person_id,
            "counterparty_name": names.get(person_id) or person_id,
            "confirmed_by": confirmed_by}


def apply_counterparty_pick(workspace_root, token, *, picked_by: str,
                            source_skill: str = "apply-choices",
                            source_ref=None) -> dict:
    """DOOR 1 — the user's pick. `token` is `confirm_counterparty:<cid>:<pid>`
    (or the widget's `<cid>:<pid>`). Refuses, writing nothing, when the row
    is not pending, carries no question, or the person is not one of its
    options — a pick outside the offered set is a reassignment, and that is
    the `reassign to [name]` verb's contract, not this one's.

    A pick is a USER action: the flag clears with `confirmed_by: user_pick`,
    the counterparty is confirmed, and ONE hint line is appended (D12)."""
    parsed = parse_pick_token(token)
    if not parsed:
        return {"status": "malformed", "detail":
                f"a pick reads {PICK_VERB}:<commitment id>:<person id>; got "
                f"{token!r}"}
    cid, pid = parsed
    row = _pending_row(workspace_root, cid)
    if row is None:
        return {"status": "not_pending", "commitment_id": cid}
    q = question_of(row)
    if not q:
        return {"status": "no_question", "commitment_id": cid}
    if pid not in (q.get("options") or []):
        return {"status": "not_an_option", "commitment_id": cid,
                "detail": f"{pid} is not one of the people this question "
                          f"offered; use `reassign to [name]` to route it "
                          f"elsewhere"}
    out = _confirm_counterparty(
        workspace_root, cid, pid, by=picked_by, source_skill=source_skill,
        confirmed_by=CONFIRMED_BY_PICK,
        reason="counterparty picked from the meeting card",
        note="you picked who this was for on the meeting card",
        source_ref=source_ref)
    if out.get("status") == "confirmed":
        d = row.get("data") or {}
        attr = d.get("attribution") if isinstance(d.get("attribution"), dict) \
            else {}
        try:
            from extraction_hints import append_attribution_hint
            out["hint_written"] = append_attribution_hint(
                workspace_root, verdict="counterparty",
                title=str(d.get("title") or ""),
                transcript_class=str(attr.get("transcript_class") or ""),
                counterparty_name=out.get("counterparty_name"),
                kind=str(d.get("kind") or ""))
        except Exception:  # never let the hint fail the pick
            out["hint_written"] = False
    return out


def apply_counterparty_default(workspace_root, commitment_id, *, applied_by: str,
                               batch_id: str, source_skill: str,
                               row=None, mint_now_iso=None) -> dict:
    """A6 — the lapse applies the ladder's pre-selected default. The row must
    still be pending and must carry a question WITH a default; anything else
    is refused, writing nothing (the ordinary lapse then owns it). The clear
    carries the batch id so `undo` puts the row back in the queue."""
    row = row if row is not None else _pending_row(workspace_root, commitment_id)
    if row is None:
        return {"status": "not_pending", "commitment_id": str(commitment_id)}
    default = question_default(row)
    if not default:
        return {"status": "no_default", "commitment_id": str(commitment_id)}
    return _confirm_counterparty(
        workspace_root, commitment_id, default, by=applied_by,
        source_skill=source_skill, confirmed_by=CONFIRMED_BY_DEFAULT,
        reason="nobody answered inside the review window — the likely "
               "answer was applied",
        note="the likely answer was applied when nobody answered — say "
             "`undo` to put it back",
        brain_batch_id=batch_id, mint_now_iso=mint_now_iso)


__all__ = [
    "PICK_VERB", "PICK_ACTION", "CARD_QUESTION_CAP",
    "CONFIRMED_BY_PICK", "CONFIRMED_BY_DEFAULT", "CONFIRMED_BY_OWN_RECAP",
    "OWN_RECAP_WINDOW_HOURS", "LIKELY_TAG", "LET_GO_TAG", "LET_GO_LINE",
    "LIKELY_LINE", "MIXED_LINE", "card_header",
    "pick_token", "parse_pick_token", "question_of", "question_default",
    "card_questions", "build_card_questions_view", "render_card_questions",
    "apply_counterparty_pick", "apply_counterparty_default",
]
