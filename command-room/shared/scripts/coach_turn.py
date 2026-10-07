#!/usr/bin/env python3
"""COACH2 — the record before the reply, and the coach that never affirms
what the record contradicts.

SPEC_SURFACES2_11c Lane 5 (5.2 items 1, 2, 4); the coaching-surfaces design
memo ("observed by default, coaching only through the earned or chosen door,
stated outranks learned, fact / observation / reading — never a score, never
a comparison, one voice").

WHY THIS MODULE EXISTS
----------------------
"Am I right that my plate is 60?" is the one question a product like this
gets wrong by being agreeable. The record is right there — one shared counter
(`plate_view.plate_numbers`) already owns every number any surface may state
about the plate — and an answer that opens with "yes" over a counter that
says something else is a product agreeing with its customer against their own
book. So the ORDER is the rule, and the order is enforced in code here:

    1. the RECORD    what the book says, with the count or the ledger ref
    2. the COUNTER   the strongest contrary fact, when the record disagrees
    3. the QUESTION  one, and only through a door the seat opened

`render_turn` refuses to compose an agreeing sentence whenever `agrees` is
False. That refusal is the lane: everything else here feeds it.

THE THREE FENCES OF 5.2 ITEM 4, IN ONE PLACE
--------------------------------------------
  DISCLOSURE  `session_preamble` returns the one-time line the first time the
              coaching layer renders in a session. The state lives on the
              session's own scratch under `_hq/.system/coach/`, never on the
              ledger — a disclosure is not an event in the customer's record
              of their own work.
  DISTRESS    `distress_cue` is a SHIPPED PHRASE LIST over what the customer
              typed in this turn. On a hit `distress_reply` renders ONE plain
              paragraph — stop, say so, name a person from the relationship
              object's stakeholders (or "someone you trust"), and the
              resources line — and nothing else that turn: no counter-case,
              no question, no pattern.
  NO READING  Nothing in this file infers a state of mind from any transcript
   OF A       text, and nothing here ever will. The cue list above is a list
   PERSON     of SENTENCES THE CUSTOMER TYPED, matched literally; it is not a
              classifier over a meeting record, and the coach reads no
              transcript at all. `tests/run_coach2_test.py` greps this file
              and `command-room-coach/SKILL.md` for that whole family of
              words and requires zero hits, so the absence is checked rather
              than promised.

ONE VOICE. There is no second persona here — every line composes in the
workspace persona the rest of the product already speaks in, and the pin
asserts no second name renders.

stdlib plus the shared readers. Every full-history read goes through
`events_io`; every number about the plate comes from `plate_view`'s one
projection; every sentence that carries a figure or an outcome is built
through `claims.make_claim`.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import claims as _claims  # noqa: E402
from atomic_write import atomic_write_json  # noqa: E402

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: The one-time line. It is a statement, never a question, and it is said
#: once per session because a disclosure repeated every turn is noise and
#: noise is how a real disclosure stops being read.
DISCLOSURE_LINE = "I am an AI working from your own records."

#: Where the once-per-session state lives. A scratch path under the system
#: folder — NOT the ledger.
SESSION_DIR_PARTS = ("_hq", ".system", "coach")
SESSION_FILE = "sessions.json"

#: THE PUSH DIAL, read (5.2 item 2). It sets HOW MANY counter-facts are
#: named. It does not set the voice, it does not soften a finding, and it
#: cannot buy agreement: when the record disagrees, the counter renders at
#: every setting including `light` (see `render_turn`).
PUSH_STEADY = "steady"
PUSH_LIGHT = "light"
PUSH_HARD = "hard"
PUSH_COUNTER_FACTS: Dict[str, int] = {PUSH_LIGHT: 0, PUSH_STEADY: 1,
                                      PUSH_HARD: 2}
PUSH_DEFAULT = PUSH_STEADY

#: The customer's words for the shared counter's keys. Every value is a key
#: of `plate_view.NUMBER_KEYS` — this table is a spelling map and never a
#: second definition of a number. Longest phrase wins.
SUBJECT_WORDS: Dict[str, str] = {
    "plate": "open",
    "my plate": "open",
    "open items": "open",
    "open commitments": "open",
    "open rows": "open",
    "list": "open",
    "overdue": "overdue",
    "past due": "overdue",
    "i owe": "you_owe",
    "owe": "you_owe",
    "owed to me": "owed_to_you",
    "waiting on": "owed_to_you",
    "unconfirmed": "unconfirmed",
    "needs attention": "attention",
}

#: How each key is said back in a sentence.
SUBJECT_NOUNS: Dict[str, str] = {
    "open": "open rows on your plate",
    "open_confirmed": "rows you have agreed to",
    "overdue": "rows past their date",
    "you_owe": "rows you owe someone",
    "owed_to_you": "rows someone owes you",
    "unconfirmed": "rows nobody has agreed to yet",
    "attention": "rows asking for attention today",
}

#: The distress cue list. SENTENCES THE CUSTOMER TYPES, matched literally and
#: case-insensitively. Deliberately short, deliberately explicit, and
#: deliberately not a classifier: a list can be read, argued with and edited
#: by a person, which is the only kind of fence that belongs on this subject.
DISTRESS_PHRASES: tuple = (
    "i want to die",
    "i want to end it",
    "i can't go on",
    "i cant go on",
    "i don't want to be here anymore",
    "i dont want to be here anymore",
    "kill myself",
    "end my life",
    "hurt myself",
    "harm myself",
    "i have nothing left",
    "i can't keep going",
    "i cant keep going",
    "i'm having a breakdown",
    "im having a breakdown",
    "i am having a breakdown",
)

#: The fallback when the relationship object names nobody.
DISTRESS_PERSON_FALLBACK = "someone you trust"

#: Where the resources paragraph is read from. EOD2 creates the file tonight;
#: COACH2 owns the `resources` section inside it (recorded as a seam). A
#: missing file is not an error — the paragraph degrades to the shipped line
#: below rather than refusing the turn, because a customer in distress is the
#: worst possible moment to hand back a failure.
KNOWLEDGE_PARTS = ("shared", "coach", "knowledge.md")
RESOURCES_HEADING = "## resources"
RESOURCES_FALLBACK = (
    "If you want someone to talk to right now, a local crisis or support "
    "line is the fastest route — in the US you can call or text 988; "
    "elsewhere, your local emergency number reaches someone who can help."
)


class CoachTurnError(RuntimeError):
    """The turn cannot be composed from what it was given."""


# ---------------------------------------------------------------------------
# Small readers
# ---------------------------------------------------------------------------

def _session_path(workspace_root) -> Path:
    return Path(workspace_root).joinpath(*SESSION_DIR_PARTS) / SESSION_FILE


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a missing or torn scratch file is "no state"
        return {}


def _shape(workspace_root) -> str:
    try:
        import coaching_doors as _doors
        return _doors.coaching_shape(workspace_root)
    except Exception:  # noqa: BLE001
        return "observed"


def _relationship(workspace_root) -> dict:
    try:
        import coaching_doors as _doors
        obj = _doors.relationship(workspace_root)
        return obj if isinstance(obj, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def normalize_push(value: Any) -> str:
    """The dial, normalized. An unknown word is `steady` — the middle — not
    the loudest setting."""
    token = str(value or "").strip().lower()
    return token if token in PUSH_COUNTER_FACTS else PUSH_DEFAULT


def counter_fact_budget(push: Any) -> int:
    """How many counter-facts this dial names. `light` is ZERO, and that zero
    is real everywhere EXCEPT over a record that disagrees — `render_turn`
    floors it at one there, because a dial that could silence the record
    would be a dial that buys agreement."""
    return PUSH_COUNTER_FACTS[normalize_push(push)]


# ---------------------------------------------------------------------------
# 5.2 item 1 — the record before the reply
# ---------------------------------------------------------------------------

_FIGURE_RE = re.compile(r"\b\d[\d,]*\b")

#: A bare four-digit number in this band is a YEAR, not a count of anything on
#: anybody's plate. Reading "the 2026 plan says my plate is 60" as a claim of
#: 2026 produced a counter-fact ("a gap of 1691") that was true of nothing —
#: fix round 1, reviewer F-1.
YEAR_MIN = 1900
YEAR_MAX = 2100

#: How far from the subject phrase a figure may sit and still be that
#: subject's number, in characters. Short on purpose: "my plate is 60" binds
#: (a connector, at any distance) and "60 open items, right?" binds (three
#: characters away), while "I have 3 meetings today, so is my plate right?"
#: does not — that 3 is somebody else's number and reading it as the claim
#: produces a counter-fact about nothing.
BIND_MAX_CHARS = 16

#: The words a claim hangs its number on. A figure directly after the subject
#: through one of these binds no matter the distance rule.
BIND_CONNECTORS = ("is", "are", "was", "were", "at", "of", "says", "reads",
                   "shows", "sits at", "stands at", "now", "currently")

#: Why a sentence carries numbers and still gets no agreement out of this
#: coach. Each is a PLAIN FACT about the sentence, said once, with no verdict
#: on the customer attached — the record line carries the book's own count
#: beside it, so the answer is still an answer.
BINDING_LINES: Dict[str, str] = {
    "many_figures": ("Your sentence carries more than one number, so I am "
                     "not going to pick one of them and call it your claim."),
    "many_subjects": ("Your sentence asks about more than one count, so I am "
                      "not going to read one number against another."),
    "unbound": ("I could not tell which number in that sentence was about "
                "it, so I am not reading one as your claim."),
}


def _figure_spans(text: str) -> List[tuple]:
    """Every figure a CLAIM could be made of, as `(value, start, end)`.

    Years are not candidates. Everything else the customer typed is."""
    out: List[tuple] = []
    for m in _FIGURE_RE.finditer(str(text or "")):
        raw = m.group(0)
        try:
            value = int(raw.replace(",", ""))
        except ValueError:  # pragma: no cover — the regex cannot produce this
            continue
        if len(raw) == 4 and "," not in raw and YEAR_MIN <= value <= YEAR_MAX:
            continue
        out.append((value, m.start(), m.end()))
    return out


def _figures_in(text: str) -> List[int]:
    return [v for v, _s, _e in _figure_spans(text)]


def _flat(text: str) -> str:
    """Lower case with every non-alphanumeric character replaced by ONE space
    — same length as the original, so a match's offsets are the offsets in
    what the customer actually typed."""
    return re.sub(r"[^a-z0-9]", " ", str(text or "").lower())


def _subject_hits(text: str) -> List[tuple]:
    """`(key, start, end, phrase_length)` for every subject phrase the
    sentence carries. Positions are into the original string."""
    flat = _flat(text)
    hits: List[tuple] = []
    for phrase, key in SUBJECT_WORDS.items():
        pattern = (r"(?<![a-z0-9])"
                   + r"\s+".join(re.escape(w) for w in phrase.split())
                   + r"(?![a-z0-9])")
        for m in re.finditer(pattern, flat):
            hits.append((key, m.start(), m.end(), len(phrase)))
    return hits


def subject_of(text: str) -> Optional[str]:
    """Which of the shared counter's keys this sentence is about, or None.

    Longest phrase first, so "waiting on" never loses to a bare "on" and
    "my plate" resolves the same key "plate" does."""
    hits = _subject_hits(text)
    if not hits:
        return None
    return max(hits, key=lambda h: h[3])[0]


def bind_figure(text: str, subject: Optional[str] = None) -> dict:
    """WHICH number in this sentence is the claim about the subject — or
    none, said out loud.

    Returns `{claimed, ambiguous, reason, candidates}`.

    THE DEFECT THIS EXISTS TO CLOSE (reviewer F-1, fix round 1): the first
    cut took `figures[0]`, so *"my plate went from 335 to 60"*, *"my plate is
    not 335, it's 60, right?"* and *"is my plate 335 or 60?"* all handed the
    coach the number that happened to match the book, and it answered *"That
    matches what I have."* to sentences whose actual claim it disagreed with.
    Positional choice cannot tell a claim from the thing it is contrasted
    with, so it is not made at all here:

      * a YEAR is never a candidate (`_figure_spans`);
      * MORE THAN ONE distinct candidate figure, or more than one subject
        asked about, is AMBIGUOUS — no figure is chosen, and the caller
        states the book's count with no agreement claim on it;
      * one candidate binds only if it is actually attached to the subject —
        straight after it through a connector word, or inside
        `BIND_MAX_CHARS` of it. Otherwise it is somebody else's number.
    """
    spans = _figure_spans(text)
    hits = _subject_hits(text)
    out = {"claimed": None, "ambiguous": False, "reason": "",
           "candidates": [v for v, _s, _e in spans]}
    if not spans:
        out["reason"] = "no_figure"
        return out
    if len({v for v, _s, _e in spans}) > 1:
        out["ambiguous"] = True
        out["reason"] = "many_figures"
        return out
    if len({k for k, _s, _e, _l in hits}) > 1:
        out["ambiguous"] = True
        out["reason"] = "many_subjects"
        return out
    value, start, _end = spans[0]
    mine = [h for h in hits if subject is None or h[0] == subject]
    if not mine:
        out["reason"] = "no_subject"
        return out
    flat = _flat(text)
    for _k, _s, end, _l in mine:
        between = flat[end:start].strip()
        if start >= end and (not between
                             or between in BIND_CONNECTORS
                             or all(w in BIND_CONNECTORS
                                    for w in between.split())):
            out["claimed"] = value
            out["reason"] = "connector"
            return out
    nearest = min(abs(start - h[1]) for h in mine)
    if nearest <= BIND_MAX_CHARS:
        out["claimed"] = value
        out["reason"] = "nearest"
        return out
    out["reason"] = "unbound"
    return out


def _counter_numbers(workspace_root, *, now_iso: Optional[str] = None) -> dict:
    """THE COUNTER. One build, the shared projection, nothing re-counted here.

    This is the call 5.2 item 1's fence targets: remove it and the 60-vs-the
    -book pin has no figure to disagree with."""
    try:
        import plate_view as _plate
        nums = _plate.surface_numbers(workspace_root, now_iso=now_iso)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return nums if isinstance(nums, dict) else {"ok": False, "error": "no numbers"}


def _events(workspace_root, *, since_ts=None,
            events: Optional[Iterable[dict]] = None) -> List[dict]:
    """The full-history read, through `events_io` and nowhere else — and
    through its ORG-SCOPED door specifically, the same one `value_receipt`
    takes. The coach composes sentences a customer reads and may screen-share,
    so it takes the reader whose privacy layers are on by default rather than
    the raw one plus a promise to be careful."""
    if events is not None:
        return [e for e in events if isinstance(e, dict)]
    try:
        from events_io import load_events_org_scoped
        rows, _skipped = load_events_org_scoped(workspace_root,
                                                since_ts=since_ts)
        return list(rows)
    except Exception:  # noqa: BLE001
        return []


_STOPWORDS = frozenset({
    "am", "i", "right", "that", "the", "a", "an", "we", "you", "my", "our",
    "is", "was", "were", "are", "to", "of", "on", "in", "it", "and", "or",
    "did", "do", "does", "not", "no", "yes", "about", "for", "with", "this",
    "decided", "decide", "decision", "still", "think", "thought", "have",
})


def _tokens(text: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", str(text or "").lower())
            if len(t) > 2 and t not in _STOPWORDS]


def _superseded_row(claim_text: str, events: List[dict]) -> Optional[dict]:
    """The strongest contrary ROW for an outcome sentence: a
    `decision_supersede_proposed` whose own title or evidence shares the
    claim's distinctive words. A proposal is not a closure — so the counter
    says a later record DISAGREES, never that the decision was reversed."""
    want = set(_tokens(claim_text))
    if not want:
        return None
    best = None
    best_overlap = 0
    for ev in events:
        if ev.get("type") != "decision_supersede_proposed":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        blob = " ".join(str(data.get(k) or "") for k in ("title", "evidence"))
        overlap = len(want & set(_tokens(blob)))
        if overlap > best_overlap:
            best, best_overlap = ev, overlap
    return best if best_overlap >= 2 else None


def _seq_of(ev: dict) -> str:
    try:
        from event_seq import event_seq
        s = event_seq(ev)
    except Exception:  # noqa: BLE001
        s = ev.get("seq")
    return str(s) if s is not None else ""


def counter_case(workspace_root, claim_text: str, *,
                 now_iso: Optional[str] = None,
                 numbers: Optional[dict] = None,
                 events: Optional[Iterable[dict]] = None,
                 stance: Optional[str] = None) -> dict:
    """The record, then the strongest contrary fact, then at most one
    question. Returns

        {agrees, record, counter, question, claims, subject, claimed,
         actual, refs, basis}

    `agrees` is THE GATE. `None` means the record has nothing to say about
    this sentence (no figure it owns, no row that contradicts it) — which is
    not agreement and is never rendered as one.

    `numbers` / `events` are injection points for a caller that already
    built them (and for the suite). Left out, the counter comes from
    `plate_view`'s one projection and the ledger through `events_io`.
    """
    text = str(claim_text or "").strip()
    if not text:
        raise CoachTurnError("a coach turn needs the sentence the customer said")

    stance = stance or str(_relationship(workspace_root).get("stance") or "")
    subject = subject_of(text)
    binding = bind_figure(text, subject)
    out: Dict[str, Any] = {
        "agrees": None, "record": "", "counter": "", "question": "",
        "claims": [], "subject": subject, "claimed": None, "actual": None,
        "refs": [], "basis": "", "text": text, "distress": False,
        "binding": binding["reason"],
    }

    # --- DISTRESS TAKES PRECEDENCE OVER EVERY LINE BELOW ------------------
    # Not a matter of instruction: a counter-case is never COMPOSED on a
    # sentence carrying a distress cue, so there is nothing for a relay to
    # accidentally render. `render_turn` asks the same gate again.
    if distress_gate(text):
        out["distress"] = True
        return out

    # --- the figure path: the shared counter owns this number -------------
    if subject and binding["candidates"]:
        nums = numbers if numbers is not None else _counter_numbers(
            workspace_root, now_iso=now_iso)
        if isinstance(nums, dict) and nums.get("ok") is not False \
                and subject in nums:
            actual = int(nums.get(subject) or 0)
            claimed = binding["claimed"]
            noun = SUBJECT_NOUNS.get(subject, subject)
            record = f"The count I can see is {actual} {noun}."
            out["actual"] = actual

            # NO FIGURE BOUND TO THE SUBJECT = NO AGREEMENT CLAIM. The record
            # still answers — the book's own count, said plainly — and
            # `agrees` stays None, which `render_turn` cannot turn into a yes.
            if claimed is None:
                said = BINDING_LINES.get(binding["reason"] or "", "")
                record = (record + " " + said).strip() if said else record
                out["record"] = record
                out["claims"].append(_claims.make_claim(
                    record, count=actual, window="your plate as it stands"))
                return _with_question(out, stance=stance)

            out["record"] = record
            out["claimed"] = claimed
            out["agrees"] = (claimed == actual)
            out["claims"].append(_claims.make_claim(
                record, count=actual, window="your plate as it stands"))
            if not out["agrees"]:
                gap = abs(actual - claimed)
                counter = (f"You said {claimed}; the book says {actual} — "
                           f"a gap of {gap}.")
                out["counter"] = counter
                out["claims"].append(_claims.make_claim(
                    counter, count=gap, window="your plate as it stands"))
            return _with_question(out, stance=stance)

    # --- the outcome path: a later row disagrees with the sentence --------
    rows = _events(workspace_root, events=events)
    row = _superseded_row(text, rows)
    if row is not None:
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        seq = _seq_of(row)
        title = str(data.get("title") or "").strip() or "that decision"
        record = f"Your record carries that decision: {title}."
        counter = (f"A later meeting record proposes superseding it — "
                   f"{title} — so the record does not agree with you yet.")
        out["record"] = record
        out["counter"] = counter
        out["agrees"] = False
        out["refs"] = [seq] if seq else []
        if seq:
            out["claims"].append(_claims.make_claim(record, refs=[seq]))
            out["claims"].append(_claims.make_claim(counter, refs=[seq]))
        return _with_question(out, stance=stance)

    # --- nothing the record owns ------------------------------------------
    out["record"] = ("I do not have a number or a row of my own for that, so "
                     "I am not going to agree with it either way.")
    return _with_question(out, stance=stance)


#: The one question, by stance. `tell_first` gets NO question — the read,
#: then the pushback, which is what that stance asked for.
_STANCE_QUESTIONS = {
    "ask_first": "What made you land on that figure?",
    "situational": "What made you land on that figure?",
}


def _with_question(out: dict, *, stance: str) -> dict:
    stance = str(stance or "").strip().lower()
    if stance in _STANCE_QUESTIONS and out.get("agrees") is not True:
        out["question"] = _STANCE_QUESTIONS[stance]
    return out


def render_turn(workspace_root, case: dict, *, shape: Optional[str] = None,
                push: Any = None) -> dict:
    """The reply, in the one order: record, then counter, then — only on a
    seat that opened a door — the question.

    THE REFUSAL: there is no branch in this function that can put an agreeing
    word in front of a `record` whose `agrees` is False. The agreeing line is
    reachable only from `agrees is True`, and it is placed AFTER the record
    line even then.

    `shape` is the seat's coaching shape. On `observed` the COACHING LAYER —
    the question — does not render; the record and the counter still do,
    because refusing to agree with a book that disagrees is not coaching, it
    is telling the truth.
    """
    shape = shape or _shape(workspace_root)
    push = push if push is not None else _relationship(workspace_root).get("push")
    budget = counter_fact_budget(push)

    # THE DISTRESS STOP, IN CODE (fix round 1, reviewer F-2). It was prose
    # only: the skill said "before composing anything else, check the cue",
    # and nothing here asked. One line, at the top of the one function that
    # composes the reply, so the paragraph is what a distress turn RETURNS
    # rather than what a relay is asked to remember.
    if distress_gate(case.get("text")):
        reply = distress_reply(workspace_root, text=case.get("text"))
        return {"text": reply["text"], "asked": False, "shape": shape,
                "push": normalize_push(push),
                "counter_fact_budget": budget, "claims": [],
                "distress": True, "coaching_suppressed": True,
                "person": reply["person"]}

    lines: List[str] = []
    record = str(case.get("record") or "").strip()
    if record:
        lines.append(record)

    counter = str(case.get("counter") or "").strip()
    if counter:
        # THE DIAL CANNOT BUY AGREEMENT. `light` is zero counter-facts
        # everywhere except here, where the record itself disagrees.
        if case.get("agrees") is False:
            lines.append(counter)
        elif budget > 0:
            lines.append(counter)

    if case.get("agrees") is True:
        lines.append("That matches what I have.")

    question = str(case.get("question") or "").strip()
    asked = False
    if question and shape != "observed":
        lines.append(question)
        asked = True

    return {"text": "\n".join(lines), "asked": asked, "shape": shape,
            "push": normalize_push(push),
            "counter_fact_budget": budget,
            "claims": list(case.get("claims") or [])}


# ---------------------------------------------------------------------------
# 5.2 item 2 — pattern breaks by count, and the push dial read
# ---------------------------------------------------------------------------

def _pack_bags(pack: dict) -> set:
    """Every identity this End-of-Day pack carries, through `eod_coach`'s own
    ref bags — never a second reading of the pack's shape."""
    try:
        import eod_coach as _ec
        return set(_ec._unmoved_ref_bag(pack)) | set(_ec._slipped_ref_bag(pack))
    except Exception:  # noqa: BLE001
        return set()


def _instances(key: str, prior_packs: Iterable[dict]) -> int:
    return sum(1 for p in (prior_packs or []) if key in _pack_bags(p))


def pattern_break(workspace_root, key: str, *,
                  prior_packs: Optional[List[dict]] = None,
                  push: Any = None,
                  label: Optional[str] = None,
                  closes: Optional[Iterable[dict]] = None) -> dict:
    """How many times the record carries this pattern, and — only above the
    floor — what the record says worked.

    Below `claims.FLOOR_INSTANCES` this is an OBSERVATION and nothing else:
    two instances are two instances, and calling them a pattern is the exact
    move the floor exists to refuse. At or above it, ONE feedforward line
    with a basis line saying what it rests on.

    `push` sets how many counter-facts ride along (1 / 0 / 2) — never the
    voice, and never whether the reading renders at all.
    """
    key = str(key or "").strip()
    if not key:
        raise CoachTurnError("a pattern break needs the pattern's identity")
    if prior_packs is None:
        try:
            import eod_coach as _ec
            prior_packs = _ec.read_prior_packs(workspace_root,
                                               limit=_ec.MAX_PRIOR_PACKS)
        except Exception:  # noqa: BLE001
            prior_packs = []
    label = str(label or key.split(":", 1)[-1])
    n = _instances(key, prior_packs)
    window = f"the last {len(list(prior_packs))} closes"

    observation = (f"{label} shows up in {n} of the closes I can see.")
    out: Dict[str, Any] = {
        "instances": n, "label": label, "window": window,
        "observation": observation, "feedforward": "", "basis": "",
        "is_reading": False, "counter_facts": [],
        "push": normalize_push(push),
    }
    out["claims"] = [_claims.make_claim(observation, count=n, window=window)]

    # THE FLOOR (5.2 item 2's fence). Remove this comparison and two
    # instances read as a pattern.
    if n < _claims.FLOOR_INSTANCES:
        return out

    move = _move_that_worked(label, closes)
    basis = f"{n} closes in {window} carry {label}."
    if move:
        feed = (f"Next time {label} comes up, the record says the move that "
                f"worked was {move}.")
    else:
        feed = (f"Next time {label} comes up, the record does not yet show a "
                f"move that worked.")
    out["feedforward"] = feed
    out["basis"] = basis
    out["is_reading"] = True
    out["claims"].append(_claims.make_claim(feed, basis=basis, instances=n))

    budget = counter_fact_budget(push)
    if budget:
        out["counter_facts"] = _counter_facts(label, closes, budget)
    return out


def _move_that_worked(label: str, closes: Optional[Iterable[dict]]) -> str:
    """The most recent close whose own words carry the pattern's label, said
    back in the record's words. Never invented: no matching close, no line."""
    want = set(_tokens(label))
    if not want:
        return ""
    best = ""
    for row in (closes or []):
        if not isinstance(row, dict):
            continue
        blob = " ".join(str(row.get(k) or "")
                        for k in ("title", "evidence", "quote", "text"))
        if want & set(_tokens(blob)):
            best = str(row.get("evidence") or row.get("quote")
                       or row.get("title") or "").strip()
    return best


def _counter_facts(label: str, closes: Optional[Iterable[dict]],
                   budget: int) -> List[str]:
    out: List[str] = []
    want = set(_tokens(label))
    for row in (closes or []):
        if len(out) >= budget:
            break
        if not isinstance(row, dict):
            continue
        blob = " ".join(str(row.get(k) or "") for k in ("title", "evidence"))
        if want & set(_tokens(blob)):
            title = str(row.get("title") or "").strip()
            if title:
                out.append(f"The record also carries {title}.")
    return out


# ---------------------------------------------------------------------------
# 5.2 item 4 — disclosure, distress, and the reading this coach never does
# ---------------------------------------------------------------------------

def session_preamble(workspace_root, session_id: str) -> str:
    """The disclosure line, ONCE per session. Every later turn in the same
    session gets the empty string.

    The state is a scratch file, not a ledger row: the customer's record of
    their own work does not need an entry saying the product introduced
    itself."""
    sid = str(session_id or "").strip()
    if not sid:
        raise CoachTurnError("a session preamble needs the session's id")
    path = _session_path(workspace_root)
    state = _read_json(path)
    seen = state.get("disclosed") if isinstance(state.get("disclosed"), dict) else {}
    if sid in seen:
        return ""
    seen = dict(seen)
    seen[sid] = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["disclosed"] = seen
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, state)
    return DISCLOSURE_LINE


def distress_cue(text: Optional[str]) -> bool:
    """True when what the customer TYPED carries one of the shipped phrases.

    A literal list over this turn's own words. Nothing is inferred, nothing
    is scored, and no meeting record is read."""
    low = re.sub(r"\s+", " ", str(text or "").lower())
    return any(p in low for p in DISTRESS_PHRASES)


def distress_gate(text: Optional[str]) -> bool:
    """THE PRECEDENCE, in one place. `counter_case` asks it before it composes
    anything, and `render_turn` asks it before it renders anything — so a
    distress turn cannot get a counter-case, a question or a pattern out of
    this module by any route, whatever a relay does or forgets.

    Removing this gate's answer is R-4's and R-10's removal proof: the
    distress sentence comes back with the plate counter-case instead of the
    paragraph."""
    return distress_cue(text)


def _resources_line(workspace_root) -> str:
    """The `resources` paragraph out of the shared coach knowledge file. A
    missing file or a missing section degrades to the shipped line — this is
    the one turn in the product that may not fail."""
    try:
        path = Path(workspace_root)
        base = path.joinpath(*KNOWLEDGE_PARTS)
        if not base.exists():
            base = _HERE.parent.joinpath("coach", "knowledge.md")
        text = base.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return RESOURCES_FALLBACK
    lines = text.split("\n")
    body: List[str] = []
    collecting = False
    for line in lines:
        if line.strip().lower().startswith(RESOURCES_HEADING):
            collecting = True
            continue
        if collecting and line.startswith("## "):
            break
        if collecting and line.strip():
            body.append(line.strip())
    return " ".join(body).strip() or RESOURCES_FALLBACK


def distress_reply(workspace_root, *, text: Optional[str] = None) -> dict:
    """ONE plain paragraph, and nothing else this turn.

    Stop coaching and say so; name a person the customer already named as
    someone who would notice a change, or "someone you trust"; give the
    resources line. No counter-case, no question, no pattern, no diagnosis
    and no name for what is happening — that is not this product's to give.
    """
    obj = _relationship(workspace_root)
    people = obj.get("stakeholders")
    person = DISTRESS_PERSON_FALLBACK
    if isinstance(people, list) and people:
        first = str(people[0]).strip()
        if first:
            try:
                from narration_names import safe_name
                person = safe_name(first) or first
            except Exception:  # noqa: BLE001
                person = first
    paragraph = (
        "I am going to stop here rather than carry on coaching — this is "
        "bigger than the record in front of me, and I am not the right thing "
        f"to work it with. Talking to {person} would be a better next step "
        f"than anything I can do. {_resources_line(workspace_root)}"
    )
    return {"renders": True, "text": paragraph, "person": person,
            "coaching_suppressed": True, "claims": []}


# ---------------------------------------------------------------------------
# WRAP2's one routed line (5.2 / lane 4 item 2 — the seam)
# ---------------------------------------------------------------------------

def next_monday(today: Any = None, *, workspace_root: Any = None,
                now: Any = None) -> str:
    """The Monday that starts NEXT week, as `YYYY-MM-DD`.

    THE DATE IS THE WORKSPACE'S, NEVER UTC (fix round 1, reviewer F-3). Called
    with no date it resolves today through `day_intent.workspace_today` →
    `tz.py`, the one date layer every other member of this family already
    uses. Handed a UTC calendar date at 9pm Pacific on a Sunday, the naive
    arithmetic answers with the Monday EIGHT days out — a whole week wrong,
    and plausible enough to go unnoticed. A caller with its own date still
    passes one.

    `day_intent.resolve_for_date` says in as many words that phrases like
    "next Tuesday" are the caller's to resolve, so `next week is about X`
    needs a caller that can count days. This is that counting, in code, so
    the SKILL.md line is narration rather than arithmetic.

    SEAM: its natural home is `day_intent.py`, which no 11c lane owns. It
    lives here so the behaviour ships with a pin tonight; the coordinator may
    rehome it in one move.

    Monday itself resolves to the FOLLOWING Monday — "next week" said on a
    Monday means the week after this one, not today.
    """
    if today is None:
        from day_intent import workspace_today
        day = workspace_today(workspace_root, now=now)
    elif isinstance(today, _dt.datetime):
        day = today.date()
    elif isinstance(today, _dt.date):
        day = today
    else:
        day = _dt.date.fromisoformat(str(today).strip())
    return (day + _dt.timedelta(days=7 - day.weekday())).isoformat()


__all__ = [
    "DISCLOSURE_LINE", "DISTRESS_PHRASES", "DISTRESS_PERSON_FALLBACK",
    "RESOURCES_FALLBACK", "PUSH_COUNTER_FACTS", "PUSH_DEFAULT",
    "SUBJECT_WORDS", "SUBJECT_NOUNS", "CoachTurnError",
    "BINDING_LINES", "BIND_MAX_CHARS", "YEAR_MIN", "YEAR_MAX",
    "normalize_push", "counter_fact_budget", "subject_of", "bind_figure",
    "counter_case", "render_turn", "pattern_break",
    "session_preamble", "distress_cue", "distress_gate", "distress_reply",
    "next_monday",
]
