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


def _machine_sentinel() -> str:
    """ATTRIB2's `event_types.MACHINE` when that branch is in the tree, and
    its own literal value when it is not — so the merged tree and this one
    write the identical answer to "who did this" and no backfill is owed."""
    try:
        from event_types import MACHINE
        return MACHINE
    except Exception:
        return "machine"


#: The sentinel a lapse or a timeout passes as `actor`. It is a VALUE and not
#: a person id, which is the whole point (TTL1 fix round 1, reviewer F-2).
MACHINE_ACTOR = _machine_sentinel()
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


# CARD1 6.2 item 1 (2026-09-14) — WHO OWES WHOM, ON THE ROW.
#
# A card row used to read `Who is "you" in "<title>"?` and nothing else. The
# customer could not tell, without opening the row, whether the thing being
# asked about was his own promise, somebody else's, or an extraction with no
# owner at all — so three questions all read the same and the fastest answer
# was to answer none of them. The DIRECTION is already on the row (the
# commitment's `owner_id` and its counterparty, both the extractor's own
# fields); it was simply never rendered. These are the three sentences, and a
# row whose direction is genuinely unknown SAYS SO — it is the row the
# question is about.
DIRECTION_YOU_OWE = "you_owe"
DIRECTION_OWED_TO_YOU = "owed_to_you"
DIRECTION_UNKNOWN = "unknown"
#: The phrase a row with no resolvable owner carries. A likely answer is
#: appended to it (`owner unknown — likely Bo Stone`), never substituted for
#: it: the guess is not an owner and must not read as one.
OWNER_UNKNOWN_PHRASE = "owner unknown"
#: The user's own row with no counterparty on record. "you owe Bo Stone" is
#: the shape with one; this is the shape without, and it is still a direction.
YOU_OWE_NO_NAME = "you owe it"
#: FIX ROUND 1 (reviewer F-6) — the user's own row whose counterparty IS on
#: record but carries no usable name: the extractor wrote a "name" that
#: `narration_names.safe_name` refuses (an id shape), so the label it hands
#: back is `UNRESOLVED_LABEL`. The id never reached the screen — the leak gate
#: always held — but the sentence read `you owe (no name on file)`, a
#: parenthetical bolted onto a direction. This says the same true thing in
#: words: there is somebody on the other end, and the roster has no name for
#: them. Distinct from `YOU_OWE_NO_NAME`, which is the row with NOBODY on the
#: other end — a different fact and a different sentence.
YOU_OWE_UNNAMED = "you owe someone not on file"


# CARD1 6.2 item 2 (2026-09-14) — A ONE-LINER IS NEVER A QUESTION.
#
# VERIFIED AT KICKOFF (probe, logs-night11c/card1/probe_*.py), because the
# spec asked which tier holds one and the answer is BOTH, by owner:
#
#   * a bare one-line capture the user owns  -> `admit_meeting_capture`
#     returns the OBSERVED tier with `aside: True` (ATTRIB1-B D11), written
#     as `commitment_observed`. Never in the needs-review set, so never a
#     card row. INTAKE1's held tier, and it already holds.
#   * a bare one-liner somebody else plainly owns -> the observed tier too.
#   * a bare one-liner with NO resolvable owner -> the REVIEW tier, written
#     as a `pending_review` commitment carrying `floor_code:
#     FLOOR_NO_OWNER` and a `who_is_you` question with every attendee as an
#     option and NO default. That row reached the card: measured on a
#     Sample/Stone fixture, a call with three such captures rendered THREE
#     card questions, two of them one-liners with nothing depending on them,
#     each promising to be let go in two days.
#
# So the gate is on the third shape and only the third. A row is HELD when
# all four are true: the capture never cleared the floor (`floor_code`), the
# ladder found no likely answer (no default), nothing is owed to anybody
# (no counterparty) and nothing depends on a date (no due). Any ONE of those
# four missing and the row still asks — a dated row with no likely answer is
# consequential and keeps its question, which is what `run_attrib1b_test`
# and `run_quiet1_test` both pin.
#
# HELD IS NOT DROPPED. The row stays exactly where the capture put it — a
# `pending_review` commitment on the ledger, inside the needs-review count
# the plate and the thread-knowledge reader already disclose. It is off the
# CARD, not off the book, and `card_held_questions` returns the ones held so
# the count is a value in code and not a sentence in prose.
HELD_ONE_LINER_REASON = ("a one-line capture with no likely answer, no date "
                         "and nobody on the other end")


def held_count_line(n: int) -> str:
    """The STATEMENT a surface may print beside a card that held rows. It is
    a count and a fact — never a question, and never a row to tap (the lane
    adds no touch)."""
    n = max(0, int(n or 0))
    if not n:
        return ""
    return (f"{n} one-line note{'' if n == 1 else 's'} from this call "
            f"{'is' if n == 1 else 'are'} held with the rest of your "
            f"unconfirmed items — nothing to answer here.")


def _safe_name(candidate) -> str:
    """`narration_names.safe_name`, imported lazily so this module keeps its
    stdlib-only import head."""
    from narration_names import safe_name
    return safe_name(candidate)


# TTL1 (SPEC_FLOW1 Lane G) — THE NUMBER HAS ONE HOME.
#
# The three sentences above say "two days" in prose while the drain that
# actually lets these rows go reads a CONFIGURABLE window
# (`commitment_backlog_sweep._configured_review_expiry_days`, defaulting to
# `UNCONFIRMED_NAG_DAYS = 2`). On a workspace tuned to five days the card
# promised a fate five days early — the exact F-5 defect ("the card told the
# customer the opposite of what the product did") in a second spelling.
#
# The shipped constants stay exactly as they are, because they are the
# two-day sentences and two days is the shipped window; these helpers
# compose the SAME sentences from `question_ttl`, which reads the window's
# own home. A tree without `question_ttl` gets the shipped constants back,
# byte-identical.
def _lapse_days(workspace_root=None) -> int:
    try:
        from question_ttl import CLASS_CAPTURE_CARD, class_lifetime_days
        return int(class_lifetime_days(CLASS_CAPTURE_CARD, workspace_root))
    except Exception:
        return 2


def _days_phrase(workspace_root=None) -> str:
    """ONE spelling of the number (TTL1 fix round 1, reviewer F-8). It used
    to be a second copy of the word table here, and `question_ttl` rendered
    "2 days" while this rendered "two days" — one number, two spellings,
    inside one lane."""
    n = _lapse_days(workspace_root)
    try:
        from question_ttl import days_phrase
        return days_phrase(n)
    except Exception:
        return "two days" if n == 2 else f"{n} days"


def let_go_tag(workspace_root=None) -> str:
    """LET_GO_TAG, true to THIS workspace's lapse window."""
    return f"no likely answer — let go after {_days_phrase(workspace_root)} if nobody picks"


def let_go_line(workspace_root=None) -> str:
    return ("pick who you meant — if nobody does, this one is let go "
            f"after {_days_phrase(workspace_root)} (`undo` brings it back)")


def mixed_line(workspace_root=None) -> str:
    return ("pick who you meant — the ones marked likely apply on their "
            f"own if you leave them; the others are let go after "
            f"{_days_phrase(workspace_root)} (`undo` brings any of them back)")


def card_expiry_sentence(workspace_root=None) -> str:
    """THE ONE SENTENCE any surface rendering a capture-card question must
    carry — the meeting card included, whichever widget it is built by.

    B3.2 of the v5.29.0 attended test read "no two-day sentence rendered".
    The sentence was never missing from the code: `card_header` composes it
    correctly and `render_and_persist` renders it. It was missing from the
    SCREEN because it lives on door 1's SEPARATE widget, which renders only
    for a `who_is_you` counterparty question — so a meeting card whose
    questions came from anywhere else showed a question with no statement of
    what leaving it does. A promise about a question belongs to the question
    CLASS, not to one widget, so it is composed here from the class's own
    lifetime and printed beside any of them.

    FIX ROUND 1 (reviewer F-8) — it is composed from `question_ttl
    .expiry_note`, the composer CONTRACT Rule 35 names, rather than
    re-spelling that sentence a second time; and `build_card_questions_view`
    RENDERS it as the section's footer note, so the promise is a render on
    every card the builder builds and not only an instruction to a model in
    one skill's prose."""
    try:
        from question_ttl import CLASS_CAPTURE_CARD, expiry_note
        note = expiry_note(CLASS_CAPTURE_CARD, workspace_root)
    except Exception:
        note = (f"after {_days_phrase(workspace_root)} with no answer, the "
                "likely answer applies on its own — `undo` brings it back")
    return (f"Leave them and, {note}; a question with no likely answer is "
            "let go instead.")


def card_header(questions, workspace_root=None) -> Optional[str]:
    """The header sentence for a set of questions, TRUE to what the lapse
    will do with each of them (F-5). None when there is nothing to ask.

      every question has a default -> the likely-answer line
      no question has a default    -> the let-go line
      a mix                        -> both, each attached to its rows

    `workspace_root` (TTL1, optional) sources the WINDOW from
    `question_ttl`; omitted, the shipped two-day sentences are used exactly
    as before."""
    qs = list(questions or [])
    n = len(qs)
    if not n:
        return None
    with_default = sum(1 for q in qs if q.get("default"))
    if with_default == n:
        tail = LIKELY_LINE
    elif with_default == 0:
        tail = LET_GO_LINE if workspace_root is None else let_go_line(workspace_root)
    else:
        tail = MIXED_LINE if workspace_root is None else mixed_line(workspace_root)
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
    """{person id -> display name}. LEAK2 (ATTENDED_TEST_v5.29.0 B3.4): the
    old fallback was the ID, so a person with no name on record put
    `person_199` into the card's option text and into the narration beside
    it — four times in one dictation. `safe_name` returns the honest label
    instead, and refuses a "name" that is itself an id shape."""
    from meeting_capture import _load_people, _person_name
    from narration_names import safe_name
    return {p["id"]: safe_name(_person_name(p))
            for p in _load_people(workspace_root)}


def _owner_id(ev: dict) -> str:
    """The row's own `owner_id`, read exactly where `commitment_state
    .bucket_of` reads it (`cru_match._commitment_field` — top level or
    `data`). `bucket_of` itself cannot answer here: EVERY card row is
    `pending_review`, which that predicate answers `unconfirmed` for before
    it ever looks at an owner. The DIRECTION is a different question from
    the BUCKET, and this is the field both of them read."""
    from cru_match import _commitment_field
    v = _commitment_field(ev, "owner_id")
    return str(v).strip() if isinstance(v, str) else ""


def _counterparty_label(ev, names: dict, workspace_root=None) -> str:
    """The name on the other end of this row, through `safe_name`: a
    RESOLVED counterparty id first (the roster's own name), then the
    unresolved name the extractor wrote down. "" when there is nobody."""
    from commitment_parties import counterparty_ids, primary_counterparty_name
    for pid in counterparty_ids(ev):
        nm = names.get(pid)
        if nm:
            return _safe_name(nm)
    raw = primary_counterparty_name(
        ev, workspace_root=str(workspace_root) if workspace_root else None)
    return _safe_name(raw) if raw else ""


def row_direction(ev, *, user_person_id=None, default_person_id=None,
                  names=None, workspace_root=None) -> tuple:
    """CARD1 6.2 item 1 — `(direction, phrase)` for ONE card row.

      you own it                      -> `you owe Bo Stone` / `you owe it`
      somebody else owns it           -> `Bo Stone owes you`
      no owner, a likely answer       -> `owner unknown — likely Bo Stone`
      no owner, no likely answer      -> `owner unknown`

    Every name goes through `narration_names.safe_name` (LEAK2 B3.4: the old
    fallback put `person_199` on the card four times in one dictation), and a
    person the roster does not carry gets the honest label rather than the id
    it was keyed by.

    FIX ROUND 1 (reviewer F-1) — WITHOUT A "YOU" THERE IS NO DIRECTION. On a
    workspace where `primary_user.resolve_primary_user` answers None — a fresh
    or mis-provisioned seat, which is exactly where a card is most likely to be
    read cold — `user_person_id` arrives falsy, the first test can never match,
    and every row the customer owns used to fall through to the second one and
    read `<the customer's own name> owes you`. He was told he owed himself, in
    the third person. `plate_view.build_plate` REFUSES on this same shape (D8);
    the card must not render a false sentence instead. An owner with no "you"
    to compare it against is an owner that cannot be direction-resolved, so it
    is cleared here, at the root, and the honest `owner unknown` branches below
    answer — with the likely name still appended when there is one. The rule in
    one line: a name is never rendered as the counterparty while the user side
    is unresolved."""
    names = names if isinstance(names, dict) else {}
    owner = _owner_id(ev)
    if owner and user_person_id and owner == str(user_person_id).strip():
        other = _counterparty_label(ev, names, workspace_root)
        if not other:
            return DIRECTION_YOU_OWE, YOU_OWE_NO_NAME
        # F-6 — a counterparty whose name `safe_name` refused is somebody with
        # no name on record, not a parenthetical to read out loud.
        from narration_names import UNRESOLVED_LABEL
        if other == UNRESOLVED_LABEL:
            return DIRECTION_YOU_OWE, YOU_OWE_UNNAMED
        return DIRECTION_YOU_OWE, f"you owe {other}"
    if owner and not user_person_id:
        owner = ""          # F-1: direction is unknowable without a "you"
    if owner:
        who = _safe_name(names.get(owner))
        return DIRECTION_OWED_TO_YOU, f"{who} owes you"
    if default_person_id:
        likely = _safe_name(names.get(default_person_id))
        return DIRECTION_UNKNOWN, f"{OWNER_UNKNOWN_PHRASE} — likely {likely}"
    return DIRECTION_UNKNOWN, OWNER_UNKNOWN_PHRASE


def is_held_one_liner(ev, *, default_person_id=None, workspace_root=None) -> bool:
    """CARD1 6.2 item 2 — True when this needs-review row is a BARE one-line
    capture: it never cleared the capture floor, the ladder found no likely
    answer, nobody is on the other end and no date depends on it. Such a row
    is held with the rest of the unconfirmed items and never asked about.

    All four conditions, because each one alone is a different row: a dated
    row with no likely answer is consequential (it keeps its question — the
    `LET_GO_TAG` path `run_attrib1b_test` pins); a row with a counterparty
    cleared the floor's consequence test by definition; a row with a likely
    answer takes the A6 lapse default; and a row with no `floor_code` was
    never held below the floor at all."""
    from commitment_parties import counterparty_ids, counterparty_names
    d = (ev.get("data") or {}) if isinstance(ev, dict) else {}
    if default_person_id:
        return False
    if not str(d.get("floor_code") or "").strip():
        return False
    if str(d.get("due") or "").strip():
        return False
    if counterparty_ids(ev):
        return False
    if counterparty_names(ev, workspace_root=str(workspace_root)
                          if workspace_root else None):
        return False
    return True


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
    out, _held = _walk_card_rows(ws, source_ref, rows)
    out.sort(key=lambda r: (r["confidence"], r["commitment_id"]))
    out = out[:max(0, int(cap))]
    if budget and out:
        import quiet
        sub = quiet.submit_questions(ws, quiet.ASKER_MEETING_CARD, out, now_iso=now_iso)
        keep = {r["commitment_id"] for r in sub["render"]}
        out = [r for r in out if r["commitment_id"] in keep]
    return out


def card_held_questions(workspace_root, source_ref, *, rows=None) -> list:
    """CARD1 6.2 item 2 — the rows this meeting HELD instead of asking about:
    bare one-line captures (`is_held_one_liner`). Same shape as
    `card_questions`, so a caller can count them or name them without a
    second derivation. They are still `pending_review` on the ledger and
    still inside the unconfirmed count the plate discloses — this is the
    count, not a second copy of the rows."""
    _out, held = _walk_card_rows(Path(workspace_root), source_ref, rows)
    held.sort(key=lambda r: (r["confidence"], r["commitment_id"]))
    return held


def _walk_card_rows(ws, source_ref, rows) -> tuple:
    """(askable, held) for ONE meeting. The ONE walk both readers share, so
    a row can never be absent from the card and absent from the held count
    at the same time."""
    if rows is None:
        from cru_match import load_needs_review
        rows = load_needs_review(str(ws / "_hq" / "data" / "events.jsonl"),
                                 workspace_root=str(ws))
    want = _ref_keys(source_ref)
    names = _people_names(ws)
    try:
        from primary_user import resolve_primary_user
        user_person_id = resolve_primary_user(str(ws))
    except Exception:
        user_person_id = None
    out: list = []
    held: list = []
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
        # LEAK2 — an option the roster does not carry renders the honest
        # label, never the id it was keyed by (B3.4).
        options = [{"person_id": pid, "name": _safe_name(names.get(pid))}
                   for pid in q["options"] if isinstance(pid, str) and pid]
        default = q.get("default") if q.get("default") in q["options"] else None
        # The default FIRST — it is the pre-selected answer.
        options.sort(key=lambda o: 0 if o["person_id"] == default else 1)
        # CARD1 item 1 — the direction, read off the row's own fields.
        direction, owed_line = row_direction(
            ev, user_person_id=user_person_id, default_person_id=default,
            names=names, workspace_root=ws)
        bucket = held if is_held_one_liner(
            ev, default_person_id=default, workspace_root=ws) else out
        bucket.append({
            "commitment_id": _commitment_id(ev),
            "title": str(d.get("title") or "").strip(),
            "evidence": str(d.get("evidence") or "").strip(),
            "options": options,
            "default": default,
            "confidence": conf,
            # CARD1 item 1 — who owes whom, and the sentence that says it.
            "direction": direction,
            "owed_line": owed_line,
            # QUIET1 D4 — the two consequence facts the budget ranks on: a
            # likely-named party (the pre-selected answer) and a date.
            "has_counterparty": bool(default),
            "has_date": bool(d.get("due")),
            "due": d.get("due"),
            "ts": ev.get("ts") or "",
        })
    return out, held


def _card_footer(workspace_root, held_n: int = 0) -> str:
    """The card's ONE footer note: what leaving a question does, and — when
    this call left bare one-liners — how many were held instead of asked."""
    note = card_expiry_sentence(workspace_root)
    held = held_count_line(held_n)
    return f"{note} {held}" if held else note


def build_card_questions_view(questions, *, source_skill: str = DOOR_SOURCE_SKILL,
                              header: Optional[str] = None,
                              workspace_root=None, held_n: int = 0) -> dict:
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
            # CARD1 6.2 item 1 — THE ROW SAYS WHO OWES WHOM. The title and
            # the direction, in that order, so three questions on one card
            # no longer read identically. A question dict built by an older
            # caller carries no `owed_line` and keeps the original sentence
            # byte-for-byte, so nothing that renders a hand-built view
            # changes shape.
            "name": (f'{q["title"]} — {q["owed_line"]}'
                     if q.get("owed_line")
                     else f'Who is "you" in "{q["title"]}"?'),
            # F-5 — a question with no default SAYS SO on its own row, so
            # the customer never reads "likely answer" beside a row that
            # will be let go instead.
            "context_tag": ((f'you said: "{q["evidence"]}"'
                             if q.get("evidence") else
                             "the calendar names more than one person")
                            + ("" if q.get("default") else
                               f" — {LET_GO_TAG if workspace_root is None else let_go_tag(workspace_root)}")),
            "actions": [],
            "sub_items": subs,
            "commitment_id": cid,
        }
        items.append(item)
    n = len(items)
    # FIX ROUND 1 (reviewer F-2) — A CALL THAT HELD EVERYTHING STILL SAYS SO.
    # The held count rides the card's one footer note, and until now the card
    # had to have a question for that footer to have a home: a call whose rows
    # were ALL bare one-liners rendered no section, so the count the lane
    # promises to disclose was the one case where it disappeared. The rows are
    # still counted on My Plate — nothing was ever hidden — but "the count is
    # disclosed" must not be conditional on there being something to tap. With
    # no questions the section is the statement alone: an empty item list and
    # the held line, which is a fact and not a row. No expiry sentence, because
    # nothing on this card can expire — there is no question on it.
    if items:
        sections = [{"title": None, "count": None, "items": items,
                     **({} if workspace_root is None else
                        {"footer_note": _card_footer(workspace_root, held_n)})}]
    elif held_n > 0:
        sections = [{"title": None, "count": None, "items": [],
                     "footer_note": held_count_line(held_n)}]
    else:
        sections = []
    return {
        "widget_mode": "all_batch_widget",
        "source_skill": source_skill,
        # F-5 — the header is TRUE to what the lapse will do with these
        # rows, never a blanket promise that the likely answer applies.
        "header": (header if header is not None
                   else card_header(questions, workspace_root)),
        # FIX ROUND 1 (reviewer F-8) — THE EXPIRY SENTENCE IS RENDERED, not
        # instructed. B3.2's remedy was a paragraph telling the model to
        # print a helper; a promise about what happens when the customer
        # ignores a question has to be part of the card the builder builds.
        # `footer_note` is the shipped section key both render paths already
        # honour (BUG-8330 item 5), so this adds no widget-contract key.
        # Drop-empty on the no-workspace call, which stays byte-identical.
        # CARD1 item 2 — the held count rides the SAME footer note the expiry
        # sentence already renders, so the card still carries exactly one
        # footer and the disclosure is a RENDER rather than an instruction to
        # a model (the F-8 lesson). A statement, never a row and never a
        # question. Zero held rows adds nothing.
        "sections": sections,
        "save_confirmation": None,
    }


def render_card_questions(workspace_root, source_ref, *, persist_dir,
                          cap: int = CARD_QUESTION_CAP, rows=None,
                          name_hint: str = "meeting-notes-questions") -> Optional[dict]:
    """Door 1 end to end: questions → data view → `render_and_persist`.
    Returns the transport dict, or None when the meeting asks nothing AND
    held nothing."""
    # QUIET1 D4 / F-7 — the render is the one spend site.
    qs = card_questions(workspace_root, source_ref, cap=cap, rows=rows, budget=True)
    # CARD1 item 2 — how many one-liners this call held. A second walk, once
    # per meeting render, so the number on the card is derived and not
    # carried by hand from the read above.
    #
    # FIX ROUND 1 (reviewer F-2) — derived BEFORE the empty test, because a
    # call that held every one of its rows has no questions and is exactly the
    # call whose held count used to go unsaid.
    held_n = len(card_held_questions(workspace_root, source_ref, rows=rows))
    if not qs and not held_n:
        return None
    from widget_transport import render_and_persist
    # TTL1 — the header reads THIS workspace's lapse window, not the prose's.
    view = build_card_questions_view(qs, workspace_root=workspace_root,
                                     held_n=held_n)
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
                          mint_now_iso=None, actor=None) -> dict:
    """The one write pair both doors and the lapse share: the counterparty
    lands on the projection through `reassign_commitment` (confirmed — the
    door IS the adjudication), then the flag clears through
    `clear_review_flags` carrying `confirmed_by` (A10) and, when a batch id
    is given, the brain-batch fields the standing `undo` reverses on.

    TTL1 fix round 1 (reviewer F-2) — `actor` passes STRAIGHT THROUGH to
    both writers, so the two acts a door DELEGATES carry the same answer to
    "who did this" as the acts its caller writes itself. The lapse and the
    question-expiry engine pass `actor="machine"` and both events stamp
    `actor_kind: "machine"`; a person's own pick passes nothing and keeps
    their id, their kind and their credit."""
    from commitment_state import (CommitmentIdError, clear_review_flags,
                                  reassign_commitment)
    names = _people_names(workspace_root)
    # REVIEW_LEAK2 round-2 R-2 — the BELT. Every other consumer of this dict
    # renders through `_safe_name` a second time; this one does not, and it
    # is the one that PERSISTS: the value goes into the reassign event and
    # is rendered later by whatever reads it. A name is passed through, a
    # missing one stays absent (never a placeholder in the ledger), and an
    # id shape becomes the honest label instead of being written down.
    raw_name = names.get(person_id)
    try:
        # POLICY1-B F-9 — the reassign rides the SAME batch as the clear, so
        # `undo` puts the counterparty back (to the prior person, or to none)
        # and not only the question.
        r1 = reassign_commitment(
            workspace_root, commitment_id, reassigned_by=by,
            source_skill=source_skill, new_counterparty_id=person_id,
            new_counterparty_name=_safe_name(raw_name) if raw_name else None,
            reason=reason, confirmed=True, actor=actor,
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
              confirmed_by=confirmed_by, actor=actor)
    if brain_batch_id:
        kw["brain_batch_id"] = brain_batch_id
        kw["brain_change_class"] = "commitment_confirm"
    r2 = clear_review_flags(workspace_root, commitment_id, **kw)
    return {"status": "confirmed" if r2.get("status") == "cleared"
            else r2.get("status") or "refused",
            "commitment_id": r2.get("commitment_id", str(commitment_id)),
            "counterparty_id": person_id,
            "counterparty_name": _safe_name(names.get(person_id)),
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
                               row=None, mint_now_iso=None,
                               actor=MACHINE_ACTOR) -> dict:
    """A6 — the lapse applies the ladder's pre-selected default. The row must
    still be pending and must carry a question WITH a default; anything else
    is refused, writing nothing (the ordinary lapse then owns it). The clear
    carries the batch id so `undo` puts the row back in the queue.

    TTL1 fix round 1 (reviewer F-2) — NOBODY ANSWERED IS THE PREMISE, so
    this default is never the customer's gesture and `actor` defaults to the
    machine sentinel: both delegated events stamp `actor_kind: "machine"`.
    Without it the reassign and the clear carried no actor ATTRIB2
    recognises, `is_customer_act` fell through to "absent is not the
    machine" and answered True, and the morning brief and End of Day would
    have told M he had answered a question he never saw."""
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
        brain_batch_id=batch_id, mint_now_iso=mint_now_iso, actor=actor)


__all__ = [
    "PICK_VERB", "PICK_ACTION", "CARD_QUESTION_CAP",
    "CONFIRMED_BY_PICK", "CONFIRMED_BY_DEFAULT", "CONFIRMED_BY_OWN_RECAP",
    "OWN_RECAP_WINDOW_HOURS", "LIKELY_TAG", "LET_GO_TAG", "LET_GO_LINE",
    "LIKELY_LINE", "MIXED_LINE", "card_header",
    "MACHINE_ACTOR",
    "let_go_tag", "let_go_line", "mixed_line", "card_expiry_sentence",
    "pick_token", "parse_pick_token", "question_of", "question_default",
    "card_questions", "build_card_questions_view", "render_card_questions",
    "apply_counterparty_pick", "apply_counterparty_default",
    # CARD1 (2026-09-14)
    "DIRECTION_YOU_OWE", "DIRECTION_OWED_TO_YOU", "DIRECTION_UNKNOWN",
    "OWNER_UNKNOWN_PHRASE", "YOU_OWE_NO_NAME", "YOU_OWE_UNNAMED",
    "row_direction",
    "HELD_ONE_LINER_REASON", "is_held_one_liner", "card_held_questions",
    "held_count_line",
]
