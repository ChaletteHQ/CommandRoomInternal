#!/usr/bin/env python3
"""
Progress narration on the inbound-mail lane (INCAP2, cr1#192).

WHY THIS EXISTS
---------------
A counterparty's status line about work that is already tracked ("the
invoices are still in progress, we will talk it through sometime next week")
passed the commitment-language test and was written as a fresh, dated
waiting-on item. The restatement dedup (`capture_gate.matches_open_commitment`)
did not merge it, because a status line shares one topic word with the
tracked row, and that test asks for far more than one. So the lane minted
a second promise out of a progress report on the first one.

THE RULE (SPEC_INCAP2 DD-1)
---------------------------
A waiting-on candidate whose words match the progress-narration class below,
about a counterparty who already has tracked work on the same topic, goes to
the REVIEW tier: it is still written, as a `pending_review` row carrying
`NARRATION_REVIEW_REASON`, and the operator adjudicates it. It is never
dropped. The same words with no tracked topic are captured exactly as before.

What this module does NOT do: it does not move the relevance gate or any
threshold (`capture_gate.classify_capture` is untouched and runs first), and
it only ever moves a row from open to review, never the other way.

THE PHRASE CLASS IS CONSERVATIVE ON PURPOSE. A junk row that slips through is
recoverable noise; a real promise sent to review is the costlier mistake. So
the class is two narrow shapes:

  1. A STATUS cue: "still in progress", "still working on", "a work in
     progress", "will keep you posted", "making good progress on".
  2. A DEFERRED DISCUSSION: a talk-it-through verb ("talk it through",
     "walk you through", "discuss", "catch up on", "circle back", "touch
     base") with a vague forward time ("sometime", "at some point", "next
     week", "soon") in the same sentence.

A vague date alone ("sometime next week I'll send the contract") is a real
promise and is not narration. And text is NEVER narration when it also
carries a concrete date ("Friday", "Oct 30", "the 30th", "tomorrow") or a
first-person send or deliver promise ("I will send the model next week"):
a status line with a real promise inside it is a promise.

THE TOPIC TEST IS NARROW TOO. A shared word only counts when it names a
THING: common action verbs and generic descriptors ("send", "review",
"call", "revised", "signed") and the parties' own name words are taken out
of both sides first, so "send revised contract" never matches "send the
invoice" from the same person.

Callers: `inbound_capture.capture_inbound_items` and
`inbox_helpers.plan_inbound_capture`, for waiting-on items only. A
reply-owed item is always held for review already.

stdlib only. One read: `party_name_words` opens `_hq/data/entities.json`
for the parties' names when the caller passes the workspace root; any
failure gives no names, never an error.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from capture_gate import (  # noqa: E402
    _MIN_NAME_TOKEN,
    _content_tokens,
    _party_tokens,
)

#: The one sentence a downgraded row carries as its `review_reason`.
NARRATION_REVIEW_REASON = (
    "reads as a progress update on something already tracked with this "
    "person, not a new promise"
)

_STATUS_RE = re.compile(
    r"\b(?:still|currently) (?:in progress|underway|under way|in the works|"
    r"being worked on|working on|working through|on it)\b"
    r"|\bwork in progress\b"
    r"|\bkeep (?:you|you all|everyone) (?:posted|updated|in the loop)\b"
    r"|\bmaking (?:good |some |steady )?progress (?:on|with)\b"
)

_DISCUSS_RE = re.compile(
    r"\b(?:talk|talking|walk|walking) (?:it |this |that |them |you "
    r"|everyone |things )?(?:all )?through\b"
    r"|\bdiscuss(?:ing)?\b"
    r"|\bcatch(?:ing)? up (?:on|about)\b"
    r"|\bcircl(?:e|ing) back\b"
    r"|\btouch(?:ing)? base\b"
)

_VAGUE_FORWARD_RE = re.compile(
    r"\b(?:sometime|some time|at some point|later this week|next week|"
    r"next month|in the coming (?:days|weeks)|soon)\b"
)

_SENTENCE_SPLIT_RE = re.compile(r"[.!?;\n]+")

#: A concrete date anywhere in the text means a real promise may be inside
#: it, so the text is never narration.
_CONCRETE_DATE_RE = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.? \d{1,2}\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)\b"
    r"|\b\d{1,2}/\d{1,2}\b|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b(?:today|tonight|tomorrow|eod|eow)\b"
    r"|\bend of (?:the )?(?:day|week|month)\b"
)

#: A first-person (or modal) send or deliver promise anywhere in the text.
_PROMISE_RE = re.compile(
    r"\b(?:i|we|i'll|we'll|i'm|we're|i'd|we'd|will|shall|going to|plan to)\b"
    r"[^.;!?\n]{0,40}?"
    r"\b(?:send|sending|deliver|delivering|share|sharing|provide|providing|"
    r"forward|forwarding|submit|submitting|return|owe|get (?:it|them|this|"
    r"that|these|those|you))\b"
)

#: Words that say HOW or WHEN, never WHAT. They are taken out before the
#: topic test, so a status line cannot match tracked work on "next week".
_NARRATION_VOCAB = frozenset(
    "still currently progress underway works worked working work keep posted "
    "updated update updates loop making made good steady talk talking walk "
    "walking through discuss discussing catch catching circle circling back "
    "touch touching base sometime some time point later next week weeks month "
    "months coming days soon all things everyone going go".split()
)

#: Words that say what someone DOES or what shape a thing is in, never which
#: thing it is. Taken out before the topic test.
_GENERIC_VOCAB = frozenset(
    "send sending sent review reviewing reviewed call calling called email "
    "emailing emailed follow following followup share sharing shared get "
    "getting got give giving provide providing deliver delivering delivered "
    "check checking confirm confirming schedule scheduling set setting meet "
    "meeting reply replying respond responding look looking finish "
    "finishing finalize finalizing prepare preparing owe owes owed need "
    "needs have has make making put take taking write writing return "
    "returning submit submitting forward forwarding revise revised revising "
    "corrected correct final new latest signed sign updated draft drafts "
    "item items thing stuff note notes question questions".split()
)

_MIN_TOPIC_TOKEN = 3


def _normalize(text) -> str:
    return str(text or "").replace("’", "'").replace("‘", "'").lower()


def is_progress_narration(text) -> bool:
    """True when `text` reads as a progress update rather than a promise.
    Pure; see the module docstring for the two shapes it accepts."""
    norm = _normalize(text)
    if not norm.strip():
        return False
    if _CONCRETE_DATE_RE.search(norm) or _PROMISE_RE.search(norm):
        return False
    if _STATUS_RE.search(norm):
        return True
    for sentence in _SENTENCE_SPLIT_RE.split(norm):
        if _DISCUSS_RE.search(sentence) and _VAGUE_FORWARD_RE.search(sentence):
            return True
    return False


def row_narrates_progress(data: dict) -> bool:
    """True when a built row's title or evidence reads as narration. The
    cheap first question, so a caller can skip loading the open set for
    every row that is not narration at all."""
    data = data or {}
    return is_progress_narration(
        f"{data.get('title') or ''}. {data.get('evidence') or ''}")


def _topic_tokens(text, drop=frozenset()) -> set:
    return {t for t in _content_tokens(text)
            if len(t) >= _MIN_TOPIC_TOKEN and t not in _NARRATION_VOCAB
            and t not in _GENERIC_VOCAB and t not in drop}


def _name_words(names) -> set:
    out = set()
    for n in names or ():
        for tok in re.findall(r"[a-z0-9']+", str(n or "").lower()):
            if len(tok) >= _MIN_NAME_TOKEN:
                out.add(tok)
    return out


def party_name_words(workspace_root, person_ids) -> set:
    """The name words (canonical name and aliases) of `person_ids`, from the
    workspace's entities file, so a title that names the person never
    matches tracked work on the person's name alone. Any read failure gives
    an empty set."""
    want = {str(p) for p in (person_ids or ()) if p}
    if not workspace_root or not want:
        return set()
    try:
        p = Path(workspace_root) / "_hq" / "data" / "entities.json"
        raw = json.loads(p.read_text(encoding="utf-8"))
        ent = raw["entities"] if isinstance(raw.get("entities"), dict) else raw
        names = []
        for person in ent.get("people") or []:
            if isinstance(person, dict) and str(person.get("id") or "") in want:
                names.append(person.get("canonical_name"))
                names.extend(person.get("aliases") or [])
        return _name_words(names)
    except Exception:  # noqa: BLE001 - no names is today's behaviour
        return set()


def tracked_topic_match(
    data: dict,
    open_events,
    *,
    person_ids=(),
    exclude_party_ids=frozenset(),
    exclude_party_names=(),
    party_names=(),
) -> Optional[dict]:
    """The first open commitment that shares a counterparty with `data` and
    at least one topic word with the candidate's title, else None. Pure.

    A topic word names a thing: narration and time words, common action
    verbs and generic descriptors, and every party's name words
    (`party_names`, the user's `exclude_party_names`, and any free-text
    party name on either row) are taken out of both sides first.

    The party test is `matches_open_commitment`'s: a person id or a name
    token, with the workspace owner excluded, because the owner is a party to
    every row on this lane and would otherwise link everything. The topic
    test is deliberately looser than that function's restatement bar (one
    shared topic word, after the narration and time words are taken out):
    it is only ever asked about a candidate that already reads as narration.
    """
    data = data or {}
    excl_ids = {str(i) for i in (exclude_party_ids or ()) if i}
    excl_names = set()
    for n in exclude_party_names or ():
        for tok in str(n or "").lower().split():
            if len(tok) >= _MIN_NAME_TOKEN:
                excl_names.add(tok)

    new_ids = {str(p) for p in (person_ids or ()) if p}
    for k in ("owner_id", "counterparty_id"):
        if data.get(k):
            new_ids.add(str(data[k]))
    new_ids -= excl_ids
    new_names = set()
    for k in ("owner_external", "counterparty_name"):
        for tok in str(data.get(k) or "").lower().split():
            if len(tok) >= _MIN_NAME_TOKEN:
                new_names.add(tok)
    new_names -= excl_names
    if not new_ids and not new_names:
        return None

    # The candidate's TITLE only: its evidence is a whole sentence of mail,
    # and its incidental words would match far more than its topic does.
    drop = (_name_words(party_names) | _name_words(exclude_party_names)
            | _name_words([data.get("owner_external"),
                           data.get("counterparty_name")]))
    new_topic = _topic_tokens(data.get("title"), drop)
    if not new_topic:
        return None

    for ev in open_events or []:
        if not isinstance(ev, dict) or ev.get("type") != "commitment":
            continue
        ev_ids, ev_names = _party_tokens(ev)
        ev_ids = {str(i) for i in ev_ids} - excl_ids
        ev_names = ev_names - excl_names
        if not ((new_ids & ev_ids) or (new_names & ev_names)):
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        ev_topic = _topic_tokens(
            f"{d.get('title') or ''} {d.get('summary') or ''} "
            f"{d.get('evidence') or ''}", drop | ev_names)
        if new_topic & ev_topic:
            return ev
    return None


def hold_if_narration(
    event: dict,
    open_events,
    *,
    user_person_id,
    user_names=(),
    workspace_root=None,
) -> Optional[dict]:
    """Move a built waiting-on `event` to the review tier when it narrates
    progress on tracked work. Returns the tracked event it matched (and
    stamps `pending_review` plus the reason on `event["data"]`), else None and
    `event` is left exactly as it was.

    A reason already on the row is kept and this one is appended, the way
    the due-before-source check does it, so no earlier reason is lost.

    `workspace_root` lets the topic test drop the parties' name words read
    from the entities file (`party_name_words`); without it only free-text
    names on the rows are dropped.
    """
    data = (event or {}).get("data")
    if not isinstance(data, dict) or not row_narrates_progress(data):
        return None
    ids = set(event.get("person_ids") or ())
    for k in ("owner_id", "counterparty_id"):
        if data.get(k):
            ids.add(data[k])
    for ev in open_events or []:
        if isinstance(ev, dict):
            ids.update(p for p in (ev.get("person_ids") or ()) if p)
    match = tracked_topic_match(
        data, open_events,
        person_ids=event.get("person_ids") or (),
        exclude_party_ids={user_person_id} if user_person_id else frozenset(),
        exclude_party_names=user_names or (),
        party_names=sorted(party_name_words(workspace_root, ids)),
    )
    if match is None:
        return None
    data["pending_review"] = True
    prior = str(data.get("review_reason") or "").strip()
    data["review_reason"] = (f"{prior}; {NARRATION_REVIEW_REASON}" if prior
                             else NARRATION_REVIEW_REASON)
    return match


__all__ = [
    "NARRATION_REVIEW_REASON",
    "is_progress_narration",
    "row_narrates_progress",
    "tracked_topic_match",
    "party_name_words",
    "hold_if_narration",
]
