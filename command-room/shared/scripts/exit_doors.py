#!/usr/bin/env python3
"""SPEC_FLOW1 Lane B (EXIT1) — the three ways a promise gets off the plate.

M's book on 2026-09-07: 317 open rows, 197 of them with nobody on the other
end. Nothing anybody mails, replies to or books can ever show those 197 done.
The only automatic exit they have is the owner's OWN WORD or SILENCE — and
neither existed. The measured outflow was ~15 automatic closes a month
against ~650 rows in.

Three routes, all automatic, all reversible, all through the one closure
door (`commitment_state.close_commitment` / `park_commitments`):

  ROUTE 2 — YOUR OWN WORD (built first: largest reach).
      A first-person completion statement in a dictation, or in the owner's
      OWN turn of a transcript, closes the matching open row the owner owns.
      "I sent Quinn the deck", "done with the quarterly deck", "paid D
      Creations". A statement that matches nothing opens nothing — it is a
      report, not a promise. It NEVER touches somebody else's promise: that
      is the transcript closer (`auto_close_from_transcript`), which stays
      OFF, is a different switch, and cannot reach this door (see
      `commitment_state`'s own-word guard and `run_exit1_test` [P1]-[P4]).

  ROUTE 1 — FACTS, for the rows with somebody on the other end.
      Every row carries `proof` — what would show it done (mail TO them with
      an attachment, their reply, a calendar event with them, a payment or
      an agreement). EVERY new source re-checks EVERY open row against its
      proof, not only the rows from its own meeting. Sent mail must have
      gone TO the counterparty (SELFMAIL1's rule, reused by name).

  ROUTE 3 — SILENCE, for what is left.
      No movement from either side for 45 days and no due date: the row
      parks itself, reason "no movement 45 days". At 60 days it is let go,
      one line in the weekly wrap, undoable as one batch. Rows overdue or
      due this week never park — importance first.

Counter-evidence reopens: their chase reply on a row one of these routes
closed puts it back, with a receipt.

Switches (per workspace, DEFAULT ON, off by a plain phrase, fail-to-ON):
`exit.own_word_closes`, `exit.silence_age_out` — the SPEC_FLOW1 family, read
through `commitment_policy.flow_switch_enabled`.

THE MACHINE IS ALWAYS THE CLOSER. Every act here is written under the rail's
own name as both `source_skill` and `resolved_by` / `parked_by`, which is
ATTRIB2's actor vocabulary (`event_types.resolve_actor`: an actor that IS the
event's source_skill is the machine). No customer surface ever says "you"
about one of these.

Every full-history read goes through `events_io` / `cru_match`.
"""
from __future__ import annotations

import re
import os
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# THE ONE ANSWER to "is the product still unsure it heard this promise" —
# the shipped predicate, never a second reading of the flag (FIX ROUND 3,
# review S-3). Named here so route 3 and route 1 ask the same question.
from cru_match import _is_pending_review  # noqa: E402


def _effective_fired_via(explicit):
    """`receipts.effective_fired_via`, behind an import that cannot break."""
    try:
        from receipts import effective_fired_via
    except Exception:  # noqa: BLE001 - a resolver that raises is worse
        return explicit if explicit is not None else "scheduled"
    try:
        return effective_fired_via(explicit)
    except Exception:  # noqa: BLE001
        return explicit if explicit is not None else "scheduled"

# ---------------------------------------------------------------------------
# The three rails, named
# ---------------------------------------------------------------------------
#
# ONE STRING IS BOTH `source_skill` AND `resolved_by` on every act a rail
# writes, and that is deliberate: ATTRIB2's `event_types.resolve_actor` reads
# "the actor IS the event's own source_skill" as the machine (POLICY1-B's
# existing convention, `resolved_by="sent_reconcile"`). So these acts are
# stamped machine acts without this module having to reach for an argument
# that does not exist on the base it is built from, and without a customer
# surface ever crediting M with one.
OWN_WORD_SOURCE_SKILL = "exit-own-word"
FACT_SOURCE_SKILL = "exit-proof"
SILENCE_SOURCE_SKILL = "exit-silence"

#: The `confirmed_by` value route 2's closes carry. It is the POLICY1-A
#: machine-confirmation door (a quote and a pointer are required beside it),
#: named for THIS rail so `commitment_state` can refuse the door to anybody
#: else — a non-prose skill string cannot forge an own-word close.
OWN_WORD_CONFIRMED_BY = "own_word"

#: Batch prefixes. Same shape as the sibling rails (`cru_`, `cal_`, `fsw_`):
#: prefix + a UTC stamp + random tail, minted once per RUN so one run is one
#: undo. LEDGERFENCE1's batch rule holds: one act, one batch, one reversal.
OWN_WORD_BATCH_PREFIX = "own_"
FACT_BATCH_PREFIX = "prf_"
SILENCE_BATCH_PREFIX = "sil_"

#: Route 3's two ages, in days. 45 = park (still yours, off the plate's
#: working blocks); 60 = let go (a `dropped` close on one batch, one wrap
#: line, one `undo`). Both are M's numbers from SPEC_FLOW1 Lane B item 3.
SILENCE_PARK_DAYS = 45
SILENCE_LET_GO_DAYS = 60
SILENCE_PARK_REASON = "no movement 45 days"
SILENCE_LET_GO_REASON = "no movement 60 days — nobody touched it"

# ---------------------------------------------------------------------------
# Route 2 — the owner's own word
# ---------------------------------------------------------------------------
#
# WHY THIS DOES NOT REUSE `cru_match.detect_completion_signal`. That detector
# answers a different question — "does this text sound like something got
# finished" — and answers it over a whole transcript, from a phrase list a
# workspace can WIDEN at runtime through `_hq/data/extraction-hints.md`. Both
# properties are wrong here. A close on the owner's own word is graded on ONE
# SENTENCE, and the sentence has to say that the OWNER did it and that it is
# ALREADY DONE. A learned phrase list that can grow without a release is not
# something to hang an automatic close on. So this is its own, fixed,
# deliberately small vocabulary, and it is pinned by the lane's suite.

#: Completion verbs in the shape a person uses about their own finished work.
#: Past tense or past participle only — no infinitives, no gerunds.
_OWN_WORD_VERBS = (
    "sent", "emailed", "mailed", "delivered", "shared", "forwarded",
    "paid", "signed", "submitted", "filed", "posted", "uploaded",
    "returned", "finished", "completed", "published", "shipped",
    "wrapped up", "knocked out", "handed off", "handed over",
    "dropped off", "took care of", "closed out", "wrote up", "sorted out",
    # LEARNFIX1 1.6 (M's ruling R-24). "I texted Bo about the quote" matched
    # nothing and closed nothing on a book whose work is mostly calls and
    # messages (record B2.2), and the line ended with an OFFER — "say the
    # word and I'll close it" — where the ruling says act. These four are
    # the same shape as the rest: past tense, first person, already done.
    # `spoke` and `talked` carry the preposition deliberately: a bare
    # "I spoke" finishes nothing and names nothing, and the object is what
    # `match_statement_to_rows` grades the row against.
    "texted", "called", "spoke to", "spoke with", "talked to", "talked with",
)
#: The same statement without a subject — how a dictation actually sounds
#: ("done with the quarterly deck", "paid Stone Supply"). Anchored to the
#: START of the sentence, and to these words only: "done" mid-sentence is
#: usually a subordinate clause ("when the deck is done…"), which is not a
#: report of a finished thing. Every verb above is a bare opener too, and
#: every one of them is past tense in English — a sentence that OPENS with
#: "paid" or "sent" is a person reporting what they did, never an
#: instruction and never a description of somebody else.
_OWN_WORD_BARE_OPENERS = (
    "done with", "all set on", "finished with",
)
#: First-person subjects. `we` is included because a founder says "we sent
#: the deck" about work he owns; `they` and a bare name are NOT — that is
#: somebody else's promise, which is the other closer's business and stays
#: off. The contracted perfect forms are spelled out because a dictation
#: transcript writes them that way, in either apostrophe.
_OWN_WORD_SUBJECTS = ("i", "we", "i've", "we've")
#: Adverbs that may sit between the subject and the verb without changing
#: the tense ("I just sent", "we finally paid").
_OWN_WORD_ADVERBS = ("just", "already", "finally", "actually", "also",
                     "then", "have", "'ve", "had", "did")

#: Anything that means it is NOT done yet. Checked over the whole sentence;
#: a hit disqualifies it outright. This is the half that keeps a PROMISE
#: ("I'll send Quinn the deck") from reading as a completion.
_NOT_YET_MARKERS = (
    "i'll", "i will", "we'll", "we will", "i am going to", "i'm going to",
    "we are going to", "we're going to", "going to", "gonna", "about to",
    "need to", "needs to", "have to", "has to", "want to", "wants to",
    "should", "must", "planning to", "plan to", "intend to", "hope to",
    "let me", "let's", "lets ", "remind me", "todo", "to-do", "action item",
    "haven't", "have not", "hasn't", "has not", "didn't", "did not",
    "don't", "do not", "never sent", "not sent", "not yet", "still need",
    "still owe", "still have", "waiting on", "waiting for", "once i",
    "when i", "after i", "as soon as", "if i", "if we", "unless",
    "supposed to", "was going to", "meant to", "tomorrow", "next week",
    "by friday", "by monday", "asap",
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|\n+")
_WS_RE = re.compile(r"\s+")


def _sentences(text) -> list:
    """The sentences of one turn, in order, whitespace-collapsed, empties
    dropped. Dictations arrive with almost no punctuation, so a newline is a
    sentence end here as well as a full stop — otherwise a whole dictated
    paragraph is one 'sentence' and every row in it matches everything."""
    out = []
    for raw in _SENTENCE_SPLIT_RE.split(str(text or "")):
        s = _WS_RE.sub(" ", str(raw or "")).strip()
        if s:
            out.append(s)
    return out


def _has_not_yet_marker(sentence_lower: str) -> bool:
    return any(m in sentence_lower for m in _NOT_YET_MARKERS)


def _own_word_verb_at(sentence_lower: str):
    """(verb, object_text) when this sentence reports the SPEAKER'S OWN
    finished work, else None.

    Two shapes, both anchored:
      1. `<i|we> [adverb…] <verb> …`  — "I just sent the deck to Quinn"
      2. `^<bare opener> …`           — "done with the quarterly deck"
    """
    s = sentence_lower
    # Shape 2 — the bare dictation opener, at the very start only. Longest
    # first, so "finished with the deck" is not read as "finished".
    for opener in sorted(_OWN_WORD_BARE_OPENERS + _OWN_WORD_VERBS,
                         key=len, reverse=True):
        if s.startswith(opener + " "):
            return opener, s[len(opener):].strip()
    # Shape 1 — a first-person subject, then optional adverbs, then a verb.
    tokens = s.replace("’", "'").split()
    if not tokens:
        return None
    for i, tok in enumerate(tokens):
        bare = tok.strip(",.;:!?\"'()")
        if bare not in _OWN_WORD_SUBJECTS:
            continue
        j = i + 1
        while j < len(tokens) and tokens[j].strip(",.;:!?\"'()") in _OWN_WORD_ADVERBS:
            j += 1
        rest = " ".join(tokens[j:])
        for verb in _OWN_WORD_VERBS:
            if rest == verb or rest.startswith(verb + " "):
                return verb, rest[len(verb):].strip()
    return None


def own_word_statements(text) -> list:
    """Every sentence in `text` that reports the speaker's OWN completed
    work: `[{"quote", "verb", "object"}]`, in order. Pure; no workspace, no
    clock, no learned phrase list.

    A sentence carrying ANY not-yet marker is dropped whole — "I'll send the
    deck once I finish it" contains a completion verb and is a promise.
    A question is dropped: "did I send the deck?" is not a report.

    CLOSETRUTH1 3.1 — the `?`-rule is `cru_match.is_question_shaped` now, so
    the sent-mail door and this one grade an interrogative the same way. On a
    single sentence the shared rule is the old test exactly (one question
    sentence, no reporting sentence beside it), so nothing here moved."""
    from cru_match import is_question_shaped
    out = []
    for sentence in _sentences(text):
        low = sentence.lower()
        if is_question_shaped(sentence):
            continue
        if _has_not_yet_marker(low):
            continue
        hit = _own_word_verb_at(low)
        if not hit:
            continue
        verb, obj = hit
        if not obj:
            continue
        out.append({"quote": sentence, "verb": verb, "object": obj})
    return out


def owner_turn_texts(transcript_text, *, user_names=()) -> list:
    """The text of the OWNER'S OWN turns, in order.

    THE FENCE THAT MAKES ROUTE 2 SAFE. `Them:` turns, `Speaker 2:` turns and
    every named speaker who is not the user are dropped here, before a single
    sentence is graded — so a statement in somebody else's mouth can never
    reach the own-word door, whatever it says. That is the transcript
    closer's territory (`auto_close_from_transcript`), it is OFF, and this
    lane does not open it.

    A transcript with NO markers at all yields nothing: an unlabelled wall of
    text has no owner, and "probably his" is not a basis for closing a
    promise. A dictation (`Me:` only) yields its turns, which is the shape
    M's own working sessions arrive in.

    `user_names` lets a NAMED transcript ("Sample Stone: I sent the deck")
    count the user's own turns — resolved by the same `_is_user_name` the
    capture classifier uses, never by substring.
    """
    from meeting_capture import _is_user_name, transcript_turns
    text = str(transcript_text or "")
    turns = transcript_turns(text)
    if not turns:
        return []
    out = []
    for idx, (label, marker_start, body_start) in enumerate(turns):
        end = turns[idx + 1][1] if idx + 1 < len(turns) else len(text)
        low = str(label or "").strip().lower()
        mine = (low == "me") or (bool(user_names) and _is_user_name(label, user_names))
        if not mine:
            continue
        body = text[body_start:end].strip()
        if body:
            out.append(body)
    return out


def _row_counterparty_tokens(ev, *, workspace_root=None, name_idx=None) -> set:
    """The name tokens of whoever is on the other end of a row — the "and the
    counterparty name if any" half of the match rule.

    BOTH halves, and both are needed. A spoken sentence says a NAME, and the
    row may carry that name either as free text (`counterparty_name`) or as a
    resolved id and nothing else. Reading only the free-text half would make
    the tie-break blind on exactly the rows identity has already tidied up.

    The roster read is THREADED (`workspace_root=`) per the F-28 rule, which
    is right on a closing path: a phantom — the same person written once as an
    id and once as their own name — must not count twice. Dropping the phantom
    costs this function nothing, because the id it collides with contributes
    the same person's canonical name through `name_idx`."""
    from commitment_parties import counterparty_ids, counterparty_names
    from cru_match import _tokenize
    d = (ev.get("data") if isinstance(ev, dict) else None) or {}
    names = list(counterparty_names(d, workspace_root=workspace_root))
    if d.get("owner_external"):
        names.append(str(d["owner_external"]))
    if name_idx:
        for pid in counterparty_ids(d):
            nm = name_idx.get(str(pid))
            if nm:
                names.append(str(nm))
    out = set()
    for n in names:
        out |= {t for t in _tokenize(n) if len(t) >= 3}
    return out


def _row_title(ev) -> str:
    from cru_match import _commitment_field
    return str(_commitment_field(ev, "title") or "")


def _row_id(ev) -> str:
    from cru_match import _commitment_id
    return str(_commitment_id(ev) or "")


#: Route 2's match bar IS the shipped auto-resolve threshold
#: (`commitment_policy.MATCH_SCORE_AUTO_RESOLVE`, 0.55). Not a new number: a
#: statement graded against a title is the same measurement every other rail
#: makes, and a lane that invents its own bar is a lane whose closes cannot
#: be compared with anybody else's.
#:
#: Two conditions sit BESIDE the score, and they are what make it safe on a
#: 317-row book:
#:   * at least `OWN_WORD_MIN_SHARED_TOKENS` content words in common — the
#:     overlap coefficient divides by the SHORTER side, so a two-word
#:     statement scores 1.0 against any title containing those two words;
#:   * exactly ONE row at or above the bar. Two rows tie: nothing closes,
#:     nothing is asked (M's ruling 6 — one question per row, and this row
#:     already has none).
OWN_WORD_MIN_SHARED_TOKENS = 2


def own_word_bar(workspace_root=None) -> float:
    from commitment_policy import thresholds
    return float(thresholds(workspace_root)[0])


def match_statement_to_rows(statement: dict, rows, *, owner_id: str,
                            workspace_root=None) -> dict:
    """Grade ONE own-word statement against the open rows.

    Returns `{"status", "commitment_id", "score", "shared", "candidates"}`:
      matched     exactly one row at or above the bar, and it is the
                  owner's own row
      ambiguous   two or more at the bar — nothing closes, nothing is asked
      no_match    nothing at the bar; the statement opens nothing (SPEC_FLOW1
                  Lane B item 2: it is a report, not a promise)

    A row whose counterparty is NAMED and whose name does not appear in the
    statement is held to the same bar as everything else, but it may not win
    a TIE-BREAK on the title alone — "and the counterparty name if any" is
    the spec's match rule, so a statement that names somebody prefers the
    row owed to that somebody.
    """
    from cru_match import _tokenize, score_match
    bar = own_word_bar(workspace_root)
    q = statement.get("quote") or ""
    q_tokens = set(_tokenize(q))
    name_idx = {}
    if workspace_root is not None:
        try:
            from narration_names import name_index
            name_idx = name_index(workspace_root)
        except Exception:  # noqa: BLE001 — an unreadable roster loses only the tie-break
            name_idx = {}
    scored = []
    for ev in rows or []:
        d = (ev.get("data") if isinstance(ev, dict) else None) or {}
        if str(d.get("owner_id") or "") != str(owner_id or ""):
            continue          # never somebody else's promise
        title = _row_title(ev)
        if not title:
            continue
        s = score_match(q, title)
        shared = len(q_tokens & set(_tokenize(title)))
        if s < bar or shared < OWN_WORD_MIN_SHARED_TOKENS:
            continue
        cp = _row_counterparty_tokens(ev, workspace_root=workspace_root,
                                      name_idx=name_idx)
        scored.append({
            "commitment_id": _row_id(ev),
            "title": title,
            "score": round(float(s), 4),
            "shared": shared,
            "names_counterparty": bool(cp and (cp & q_tokens)),
            "event": ev,
        })
    if not scored:
        return {"status": "no_match", "commitment_id": "", "score": 0.0,
                "shared": 0, "candidates": []}
    named = [c for c in scored if c["names_counterparty"]]
    pool = named if len(named) == 1 else scored
    if len(pool) != 1:
        return {"status": "ambiguous", "commitment_id": "", "score": 0.0,
                "shared": 0,
                "candidates": [{k: c[k] for k in ("commitment_id", "title", "score")}
                               for c in pool]}
    win = pool[0]
    return {"status": "matched", "commitment_id": win["commitment_id"],
            "score": win["score"], "shared": win["shared"],
            "title": win["title"], "candidates": []}


def own_word_closes_enabled(workspace_root=None) -> bool:
    """Is route 2 on for this workspace? Default ON, fail-to-ON — the act it
    gates carries a receipt and an `undo`, and the alternative to it is a
    question, which the design rule calls a defect."""
    from commitment_policy import EXIT_OWN_WORD_CLOSES_KEY, flow_switch_enabled
    return flow_switch_enabled(workspace_root, EXIT_OWN_WORD_CLOSES_KEY)


def plan_own_word_closes(workspace_root, *, transcript_text, source_ref,
                         user_names=(), open_rows=None, now_iso=None) -> dict:
    """What route 2 WOULD close from one dictation or transcript. No writes.

    Returns `{"enabled", "n_statements", "closes": [...], "ambiguous": [...],
    "no_match": [...], "n_owner_turns"}`. Every entry in `closes` carries the
    verbatim sentence, the row it matched and the score — which is exactly
    what the close then writes as its evidence.
    """
    from cru_match import load_open_commitments, partition_subitems
    from primary_user import resolve_primary_user
    out = {"enabled": own_word_closes_enabled(workspace_root),
           "n_owner_turns": 0, "n_statements": 0,
           "closes": [], "ambiguous": [], "no_match": []}
    uid = resolve_primary_user(workspace_root) or ""
    if not uid:
        out["refused"] = "no primary user on file"
        return out
    turns = owner_turn_texts(transcript_text, user_names=user_names)
    out["n_owner_turns"] = len(turns)
    statements = []
    for t in turns:
        statements.extend(own_word_statements(t))
    out["n_statements"] = len(statements)
    if not statements:
        return out
    if open_rows is None:
        events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
        open_rows = load_open_commitments(events_path,
                                          workspace_root=workspace_root)
    # NOT `cru_eligible`. That filter drops `kind: task` because no MAIL,
    # REPLY or CALENDAR event can close a self-owed row — which is true, and
    # is exactly why route 2 exists: 197 of M's 317 open rows have nobody on
    # the other end, and most of them are tasks. Your own word is the one
    # kind of evidence a self-owed row CAN have. Live sub-items are still
    # dropped (SUB1 D5: the parent is the row of record, and the writer
    # refuses a parent with open children anyway).
    #
    # AND NOT `_is_pending_review` EITHER — the deliberate asymmetry with
    # route 1, stated because the review (F-7) found it stated nowhere.
    # Route 1 refuses a row the extractor was unsure about: a fact on file
    # is the PRODUCT'S argument, and the product does not get to close a
    # guess it made with evidence it also found. Route 2 allows it: the
    # customer said out loud that they finished it, which answers the
    # question the guess was being held for. It removes a held row instead
    # of adding one, it goes through the POLICY1-A machine door carrying the
    # verbatim sentence and a real pointer, and it is one `undo` away.
    # 8 rows on M's book carry an unconfirmed guess; ruling 4 of the fix
    # round asks him whether to keep this as it is.
    rows, _subs = partition_subitems(list(open_rows or []))
    taken = set()
    for st in statements:
        m = match_statement_to_rows(st, rows, owner_id=uid,
                                    workspace_root=workspace_root)
        row = {"quote": st["quote"], "verb": st["verb"], **m}
        if m["status"] == "matched":
            if m["commitment_id"] in taken:
                # Two sentences about one row is one close, not two.
                row["status"] = "already_taken"
                out["no_match"].append(row)
                continue
            taken.add(m["commitment_id"])
            row["source_ref"] = source_ref
            out["closes"].append(row)
        elif m["status"] == "ambiguous":
            out["ambiguous"].append(row)
        else:
            out["no_match"].append(row)
    return out


def _mint_batch_id(prefix: str, now_iso=None) -> str:
    import secrets
    from datetime import datetime, timezone
    from cru_match import _parse_ts
    dt = (_parse_ts(now_iso) if now_iso else None) or datetime.now(timezone.utc)
    return f"{prefix}{dt.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"


def own_word_receipt_line(n: int) -> str:
    """The ONE line the surface says. Plain words, no id, no score, no rail
    name — and it says the machine did it, never "you"."""
    if n <= 0:
        return ""
    if n == 1:
        return ("Closed 1 thing you said you had done; say `undo` to put it "
                "back.")
    return (f"Closed {n} things you said you had done; say `undo` to put "
            "them all back.")


def own_word_no_match_line(n: int) -> str:
    """The honest one-liner when a report matched nothing.

    LEARNFIX1 1.6, routed in at fix round 1 (REVIEW_LEARNFIX1 ruling 10).
    With the speech verbs added, "I texted <name> about the quote" closes and
    `own_word_receipt_line` composes the receipt. What had no composed
    sentence was the OTHER ending — a report that matched nothing — so the
    surface improvised, and what it improvised on the day ended "say the word
    and I'll close it". That is the offer form of a question, and M ruled a
    question on this path a defect: the product either acts or says plainly
    that it did not. This line OFFERS NOTHING. It names no row, no id and no
    score, and it asks for nothing back.

    `n` counts the reports that matched NO row. A report whose row another
    sentence in the same turn already closed is not one of them (it matched;
    the close simply happened once), and neither is an ambiguous one, which
    matched several — saying "nothing matched" about either would be false.
    """
    if n <= 0:
        return ""
    if n == 1:
        return "Nothing on your list matched that one, so I left it alone."
    return (f"Nothing on your list matched {n} of those, so I left them "
            "alone.")


def _n_unmatched(plan: dict) -> int:
    """The reports that matched NO row — `already_taken` excluded, because
    that one DID match; its row was closed by an earlier sentence."""
    return len([r for r in (plan.get("no_match") or [])
                if r.get("status") != "already_taken"])


def typed_reports(text) -> list:
    """One typed turn -> the separate completion reports inside it.

    CLOSETRUTH1 3.5. People do not type one sentence per thing they did.
    The 2026-09-13 turn was "I sent Bo the deck, I sent Quinn the
    engagement agreement, and I paid the invoice" — ONE sentence, three
    reports, and route 2's sentence splitter (built for transcripts, which
    are punctuated) read it as one statement that matched three rows and so
    closed none of them. On the day, the chat itself did the splitting, which
    is exactly the behaviour that must live in code.

    The cut is made only where a NEW own-word report begins: a subject the
    person speaks with (`I`, `we`, `I've`, `we've`) followed by a completion
    verb, which is the same test the statement grader uses. So "I sent Quinn
    the deck and the agreement" stays ONE report — "and the agreement" starts
    no new subject — while "…the deck, and I paid the invoice" becomes two.
    Nothing here grades or closes; it only decides where one report ends.
    """
    import re as _re

    raw = str(text or "").strip()
    if not raw:
        return []
    cuts = [0]
    for m in _re.finditer(r"(?<![A-Za-z0-9'])(?:i|we)(?:'ve)?(?![A-Za-z0-9'])",
                          raw, flags=_re.IGNORECASE):
        if m.start() == 0:
            continue
        # Only a boundary the writer actually drew: a comma, a semicolon, a
        # sentence end, or an "and" / "then" joining clause in front of it.
        before = raw[:m.start()].rstrip()
        low_before = before.lower()
        if not (low_before.endswith((",", ";", ".", "!", "?"))
                or low_before.endswith(" and") or low_before.endswith(" then")
                or low_before.endswith(", and") or low_before.endswith(", then")):
            continue
        if not _own_word_verb_at(raw[m.start():].lower()):
            continue
        cuts.append(m.start())
    cuts.append(len(raw))
    out = []
    for a, b in zip(cuts, cuts[1:]):
        part = raw[a:b].strip()
        # Drop the joining words the cut left hanging on the END of a part.
        part = _re.sub(r"[,;]?\s*(?:and|then)\s*$", "", part,
                       flags=_re.IGNORECASE).strip()
        part = part.rstrip(",; ")
        if part:
            out.append(part)
    return out


#: CLOSETRUTH1 3.5 — the pointer a close made from a TYPED chat turn carries.
#: A chat turn has no meeting id and no message id, and the writer refuses a
#: machine-door close with no pointer at all, so the door itself is the
#: pointer: "the person said so, here, in chat". Never a minted receipt id —
#: the writer refuses that by name, and it would be circular anyway.
CHAT_SOURCE_REF = "chat:own-word"


def close_from_own_word(workspace_root, statements, *, source_ref=None,
                        user_names=(), open_rows=None, now_iso=None,
                        batch_id=None) -> dict:
    """THE own-word entry. Chat and the transcript pass both come through it.

    CLOSETRUTH1 3.5 (record B2.2). Typing "I sent Bo the deck, I sent Quinn
    the engagement agreement, and I paid the invoice" in chat DID close the
    right rows on 2026-09-13 — and minted no batch, because the chat path
    called the commitment writer directly with `user_confirmed=True`. So
    nothing listed for `undo`, the receipt offered none, and the only reason
    `undo` worked at all that day was that the same chat still remembered
    what it had closed. A close that is only reversible inside the turn that
    made it is not reversible.

    `statements` is what the person typed: one string, or several. Each is
    wrapped as the person's own turn, which is exactly what a chat message
    is, and the rest is route 2's existing rail unchanged — the same matcher,
    the same bar, the same switch, the same writer door, one `own_` batch and
    the same receipt line ending "say `undo` to put them back". The transcript
    path keeps calling `apply_own_word_closes` with a real `source_ref`; this
    is the same call with the chat's own pointer.

    Returns `apply_own_word_closes`' dict: `n_closed`, `batch_id`,
    `receipt_line`, `applied`, `ambiguous`, `no_match`, `refused_rows`.
    """
    if isinstance(statements, str):
        given = [statements]
    else:
        given = [str(x) for x in (statements or []) if str(x).strip()]
    parts: list = []
    for g in given:
        parts.extend(typed_reports(g))
    text = "\n".join(f"Me: {part}" for part in parts)
    ref = str(source_ref or "").strip() or CHAT_SOURCE_REF
    return apply_own_word_closes(
        workspace_root, transcript_text=text, source_ref=ref,
        user_names=user_names, open_rows=open_rows, now_iso=now_iso,
        batch_id=batch_id)


def apply_own_word_closes(workspace_root, *, transcript_text, source_ref,
                          user_names=(), open_rows=None, now_iso=None,
                          batch_id=None, plan=None) -> dict:
    """Route 2, applied. One run = one batch = one `undo`.

    Every close goes through `commitment_state.close_commitment` with:
      * `confirmed_by=OWN_WORD_CONFIRMED_BY` — the POLICY1-A machine door,
        which requires the verbatim sentence AND a real pointer, both of
        which this rail holds;
      * `resolved_by_match="match"` + `match_score` — the matcher's door,
        stated honestly (no surface embedded the id, no model picked it);
      * `resolved_by = source_skill = OWN_WORD_SOURCE_SKILL` — ATTRIB2: the
        machine is the closer.
    """
    out = dict(plan or plan_own_word_closes(
        workspace_root, transcript_text=transcript_text, source_ref=source_ref,
        user_names=user_names, open_rows=open_rows, now_iso=now_iso))
    out.setdefault("closes", [])
    out["applied"] = []
    out["refused_rows"] = []
    out["n_closed"] = 0
    out["batch_id"] = None
    out["receipt_line"] = ""
    # Composed HERE, on every path out of this function — the door that knows
    # nothing matched is the door that says so. A surface that has to work it
    # out from a list of dicts composes a sentence nothing scanned, which is
    # how "say the word and I'll close it" reached a customer on 09-16.
    out["no_match_line"] = own_word_no_match_line(_n_unmatched(out))
    if not out.get("enabled"):
        out["withheld"] = len(out["closes"])
        return out
    if not out["closes"]:
        return out
    from commitment_state import close_commitment
    batch_id = batch_id or _mint_batch_id(OWN_WORD_BATCH_PREFIX, now_iso)
    out["batch_id"] = batch_id
    from commitment_policy import CLOSE_CHANGE_CLASS, QUOTE_MAX_CHARS
    for row in out["closes"]:
        try:
            r = close_commitment(
                workspace_root, row["commitment_id"],
                resolved_by=OWN_WORD_SOURCE_SKILL,
                evidence=str(row["quote"])[:QUOTE_MAX_CHARS],
                source_skill=OWN_WORD_SOURCE_SKILL,
                resolution="done",
                confirmed_by=OWN_WORD_CONFIRMED_BY,
                resolved_by_match="match",
                source_ref=row.get("source_ref") or source_ref,
                mint_now_iso=now_iso,
                extra_data={"match_score": float(row.get("score") or 0.0),
                            "brain_batch_id": batch_id,
                            "brain_change_class": CLOSE_CHANGE_CLASS,
                            "exit_route": "own_word"},
            )
        except Exception as exc:   # noqa: BLE001 — one bad row never stops a run
            out["refused_rows"].append({"commitment_id": row["commitment_id"],
                                        "error": type(exc).__name__,
                                        "detail": str(exc)[:200]})
            continue
        if r.get("status") == "closed":
            out["n_closed"] += 1
            out["applied"].append({"commitment_id": r["commitment_id"],
                                   "quote": row["quote"],
                                   "score": row.get("score")})
        else:
            out["refused_rows"].append({"commitment_id": row["commitment_id"],
                                        "error": r.get("status")})
    out["receipt_line"] = own_word_receipt_line(out["n_closed"])
    return out


# ===========================================================================
# Route 1 — facts. `proof` at capture, re-checked on every new source.
# ===========================================================================
#
# WHAT `proof` IS. One word per row saying WHAT WOULD SHOW IT DONE. It is not
# a score and not a status: it is the question each rail is allowed to answer
# about that row, written down at the door instead of guessed at by four
# different matchers afterwards.
#
# WHY IT IS ALSO DERIVED, NOT ONLY STORED. A field stamped at capture helps
# only the rows captured after it ships. All 317 rows on M's book today were
# captured before it — and they are the entire problem. So `proof_for_row`
# derives the answer from the row's own words whenever the row does not carry
# one, and `stored_proof` is the ONE reader both halves go through.

PROOF_MAIL = "mail_to_counterparty"
PROOF_REPLY = "their_reply"
PROOF_CALENDAR = "calendar_event_with_counterparty"
PROOF_DEAL = "payment_or_agreement"
#: The honest one. A row with nobody on the other end cannot be proved by any
#: message, reply, meeting or payment that will ever exist. It has two exits
#: and they are routes 2 and 3. 197 of M's 317 open rows land here, and a
#: surface that can say so is a surface that can stop pretending the mail
#: rails are ever going to reach them.
PROOF_OWN_WORD = "your_own_word"
PROOF_KINDS = (PROOF_MAIL, PROOF_REPLY, PROOF_CALENDAR, PROOF_DEAL,
               PROOF_OWN_WORD)
PROOF_KEY = "proof"

#: Money / signature language — the row a `deal_won` or a promotion finishes.
#: The regression: on 2026-09-07 a client's "return signed agreement" row was
#: still open although the agreement had been signed AND paid ten days
#: earlier, and the product had both facts on file.
_DEAL_WORDS = (
    "pay", "paid", "payment", "invoice", "wire", "deposit", "retainer",
    "sign", "signed", "countersign", "signature", "agreement", "contract",
    "msa", "sow", "engagement letter", "proposal accepted",
)
#: Deliverable language — the row a SENT MESSAGE with an attachment finishes.
_DELIVERY_WORDS = (
    "send", "sent", "share", "email", "deliver", "forward", "provide",
    "get over", "get to", "circulate", "pass along", "write up", "draft",
)


def _row_has_counterparty(d: dict) -> bool:
    from commitment_parties import counterparty_ids, counterparty_names
    return bool(counterparty_ids(d) or counterparty_names(d)
                or str(d.get("owner_external") or "").strip())


def proof_for_row(ev) -> str:
    """DERIVE what would show this row done, from the row's own words. Pure.

    The order is the order of strength, and each test is the shipped detector
    where one exists rather than a second opinion:
      * nobody on the other end          -> `your_own_word`
      * the user is not the owner         -> `their_reply` (they owe it; what
        shows it done is their delivery, which the inbound rail already reads)
      * money or a signature in the title -> `payment_or_agreement`
      * scheduling intent in the title    -> `calendar_event_with_counterparty`
        (`cru_match.detect_scheduling_intent` — the SAME detector the shipped
        calendar closer's pre-filter uses; this lane does not invent a second
        idea of what a scheduling row is)
      * anything else with a counterparty -> `mail_to_counterparty`
    """
    d = (ev.get("data") if isinstance(ev, dict) else None) or {}
    if not _row_has_counterparty(d):
        return PROOF_OWN_WORD
    title = str(d.get("title") or "").lower()
    kind = str(d.get("kind") or "").strip().lower()
    if any(w in title for w in _DEAL_WORDS):
        return PROOF_DEAL
    if kind == "scheduling":
        return PROOF_CALENDAR
    try:
        from cru_match import detect_scheduling_intent
        if detect_scheduling_intent(d.get("title")):
            return PROOF_CALENDAR
    except Exception:  # noqa: BLE001 — a missing detector never blocks a row
        pass
    if any(w in title for w in _DELIVERY_WORDS):
        return PROOF_MAIL
    return PROOF_MAIL


def stored_proof(ev) -> str:
    """THE reader. The stamped value when the row carries a legal one, else
    the derivation. One reader, so a stamped row and a legacy row are never
    graded by two different ideas of the same question."""
    d = (ev.get("data") if isinstance(ev, dict) else None) or {}
    raw = str(d.get(PROOF_KEY) or "").strip()
    return raw if raw in PROOF_KINDS else proof_for_row(ev)


def proof_census(open_rows) -> dict:
    """{proof kind: n} over a set of open rows — what the plate is actually
    made of, and how many of its rows no message can ever finish."""
    out = {k: 0 for k in PROOF_KINDS}
    for ev in open_rows or []:
        out[stored_proof(ev)] = out.get(stored_proof(ev), 0) + 1
    return out


def calendar_closes_enabled(workspace_root=None) -> bool:
    """M's ruling 1 of the ten (2026-09-07): THE CALENDAR CLOSER IS ON.

    A meeting with the other side, after the row was captured, is a FACT —
    the same class of evidence as a delivered message, and unlike a
    transcript it does not depend on reading anybody's words. So the calendar
    leg of the CUT-A switch flips to ON while the transcript leg stays OFF.
    Its own key, read by identity and failing to ON, so the ruling survives a
    workspace with no settings file (which is every workspace)."""
    from commitment_policy import calendar_closes_enabled as _cce
    return _cce(workspace_root)


def silence_age_out_enabled(workspace_root=None) -> bool:
    from commitment_policy import EXIT_SILENCE_AGE_OUT_KEY, flow_switch_enabled
    return flow_switch_enabled(workspace_root, EXIT_SILENCE_AGE_OUT_KEY)


def _deal_events(events) -> list:
    """The paid-or-signed facts already on the ledger: a won deal, or an org
    promoted on one. Both carry the org and, usually, the people."""
    out = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        t = ev.get("type")
        d = ev.get("data") or {}
        if t == "deal_won":
            out.append(ev)
        elif t == "org_promoted" and (d.get("won_seq") or d.get("deal_manufactured")):
            out.append(ev)
    return out


def _reversed_deal_orgs(events) -> set:
    """Orgs whose win was later reversed — a reversed win proves nothing.
    (The 2026-09-07 book had exactly one: a promotion undone the same day,
    which the brief then reported as if it stood.)"""
    out = set()
    for ev in events or []:
        d = (ev.get("data") if isinstance(ev, dict) else None) or {}
        if ev.get("type") in ("deal_won_reversed",) or d.get("deal_won_reversed"):
            for key in ("org_id", "project_id", "thread_id"):
                if d.get(key):
                    out.add(str(d[key]))
    return out


def _people_orgs(workspace_root) -> dict:
    """{person_id: org_id} from the entity graph, read ONCE per pass. Empty
    on an unreadable graph — which leaves the deal leg on its person route
    only, an honest degrade rather than a crash."""
    try:
        import json as _json
        from entities_io import entities_collection
        raw = _json.loads((Path(workspace_root) / "_hq" / "data"
                           / "entities.json").read_text(encoding="utf-8"))
        out = {}
        for p in entities_collection(raw, "people") or []:
            org = str(p.get("org_id") or p.get("organization_id") or "")
            if p.get("id") and org:
                out[str(p["id"])] = org
        return out
    except Exception:  # noqa: BLE001
        return {}


def plan_fact_closes(workspace_root, *, now_iso=None, events=None,
                     only_person_ids=None) -> dict:
    """What route 1 WOULD close, over EVERY open row — not only the rows of
    whatever meeting or message triggered the look. No writes.

    THE LEGS, AND WHOSE THEY ARE:
      * `calendar` — a meeting with the other side after the row was
        captured. NOT a second closer: this reports `calendar_close.plan`,
        the shipped job, whose row set this lane WIDENED at its own door to
        every row whose `proof` is a meeting. One closer, one probation, one
        receipt, one undo — and `apply_fact_closes` runs THAT job rather
        than closing the same rows behind its back.
      * `deal` — a won deal or a promotion on the counterparty's org after
        the row was captured, for rows whose proof is a payment or a
        signature. A reversed win proves nothing.
      * `mail` / `reply` — NOT re-implemented here, deliberately.
        `reconcile_sent_commitments` and `reconcile_inbound_commitments`
        already score every open row on the book against every message in
        their window, and SELFMAIL1 put the recipient rule on the sent one
        (`cru_match.addressed_to_counterparty`). A second mail matcher in a
        third module is how two answers to one question start disagreeing.
        The census below states how many rows each of those rails owns so
        the number is visible rather than assumed.

    `only_person_ids` narrows the pass to the rows involving those people —
    which is what `pull up <name>` needs so it can ACT on what it noticed
    instead of offering to.
    """
    import commitment_policy as policy
    from cru_match import _is_pending_review, cru_eligible, load_open_commitments
    from primary_user import resolve_primary_user
    ep = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if events is None:
        from events_io import load_events_owner_scoped
        events, _skipped = load_events_owner_scoped(workspace_root)
    uid = resolve_primary_user(workspace_root) or ""
    opens = load_open_commitments(str(ep), events=events,
                                  workspace_root=workspace_root)
    rows = [r for r in cru_eligible(opens) if not _is_pending_review(r)]
    if only_person_ids:
        want = {str(p) for p in only_person_ids}
        rows = [r for r in rows
                if policy._row_party_ids(r) & want]
    census = proof_census(opens)
    out = {"n_open": len(opens), "n_eligible": len(rows), "proof_census": census,
           "calendar": [], "deal": [], "enabled_calendar": calendar_closes_enabled(workspace_root)}

    try:
        from calendar_close import plan as _calendar_plan
        cal = _calendar_plan(workspace_root, now_iso=now_iso)
        cands = list(cal.get("book") or []) + list(cal.get("observed") or [])
    except Exception:  # noqa: BLE001 — a missing calendar job is not a crash
        cands = []
    if only_person_ids:
        want = {str(p) for p in only_person_ids}
        cands = [c for c in cands
                 if policy._row_party_ids(c.get("row") or {}) & want]
    out["calendar"] = [{k: v for k, v in c.items() if k != "row"} for c in cands]

    deals = _deal_events(events)
    reversed_orgs = _reversed_deal_orgs(events)
    if deals:
        from event_time import event_time
        orgs_by_person = _people_orgs(workspace_root)
        for row in rows:
            if stored_proof(row) != PROOF_DEAL:
                continue
            cap = policy._parse_ts(event_time(row))
            if cap is None:
                continue
            parties = {p for p in policy._row_party_ids(row) if p and p != uid}
            if not parties:
                continue
            orgs = {orgs_by_person.get(p) for p in parties if orgs_by_person.get(p)}
            for ev in deals:
                d = ev.get("data") or {}
                when = policy._parse_ts(event_time(ev))
                if when is None or cap >= when:
                    continue
                ev_org = str(d.get("org_id") or d.get("project_id") or "")
                if ev_org and ev_org in reversed_orgs:
                    continue
                # The people on the event, read in BOTH scopes — a `deal_won`
                # carries `person_ids` on the envelope, like a `meeting` does
                # (the shipped `_meeting_party_ids` is the reader that already
                # knows that, so it is the one used).
                hit = ((ev_org and ev_org in orgs)
                       or bool(parties & policy._meeting_party_ids(ev)))
                if not hit:
                    continue
                out["deal"].append({
                    "commitment_id": (row.get("data") or {}).get("id") or "",
                    "title": (row.get("data") or {}).get("title") or "",
                    "deal_seq": ev.get("seq"),
                    "deal_ts": when.isoformat(),
                    "primary_thread_id": row.get("primary_thread_id") or "",
                })
                break
    return out


class _SkipCalendarLeg(Exception):
    """Internal: the caller runs the calendar job itself (see
    `apply_fact_closes(run_calendar=False)`). Not an error and never
    surfaced."""


def _calendar_window_days() -> int:
    try:
        from calendar_close import CALENDAR_WINDOW_DAYS
        return int(CALENDAR_WINDOW_DAYS)
    except Exception:  # noqa: BLE001
        return 45


def fact_receipt_line(n_calendar: int, n_deal: int) -> str:
    """One CHANGED line, in plain words. No id, no score, no rail name."""
    bits = []
    if n_calendar:
        bits.append(f"{n_calendar} because the meeting happened")
    if n_deal:
        bits.append(f"{n_deal} because it was signed or paid")
    if not bits:
        return ""
    n = n_calendar + n_deal
    return (f"Closed {n} item{'' if n == 1 else 's'} that the record shows "
            f"finished — {' and '.join(bits)}; say `undo` to put "
            f"{'it' if n == 1 else 'them'} back.")


def apply_fact_closes(workspace_root, *, now_iso=None, plan=None,
                      only_person_ids=None, batch_id=None,
                      run_calendar: bool = True) -> dict:
    """Route 1, applied. A fact-proved close HAPPENS wherever the product
    notices it — receipt and `undo`, never "say the word and I'll close
    them" (the 2026-09-07 regression B4.4, ruled by M the same day).

    `run_calendar=False` for the ONE caller that must not: the scheduled
    `exit-doors` job, which runs in the same maintenance fire as the
    `calendar-close` job. The calendar closer's probation counts its own
    RECEIPTS, so firing it twice in one run would spend two of a row's three
    offers in one pass — the SWEEPSCHED1 mistake, in the other direction.
    Every on-demand caller leaves it True: that is the whole point of "a
    fact-proved close happens wherever the product notices it"."""
    import commitment_policy as policy
    out = dict(plan or plan_fact_closes(workspace_root, now_iso=now_iso,
                                        only_person_ids=only_person_ids))
    out.update({"n_closed": 0, "applied": [], "refused_rows": [],
                "batch_id": None, "receipt_line": "", "n_withheld": 0})
    # THE CALENDAR LEG IS THE SHIPPED JOB'S. Running it here — rather than
    # closing its rows from this module — is what keeps ONE probation, ONE
    # batch shape and ONE undo on those rows. It is also what makes "a
    # fact-proved close happens wherever the product notices it" true: any
    # surface that calls this runs the closer, instead of offering to.
    n_cal = 0
    try:
        if not run_calendar:
            raise _SkipCalendarLeg
        from calendar_close import run_calendar_close_job
        # `fired_via` stays inside the shipped vocabulary
        # (`receipts.FIRED_VIA`): a surface calling this because the customer
        # asked about somebody is a MANUAL fire, and a value outside the
        # vocabulary makes the job's own receipt refuse to write — which
        # silently spends the per-row probation counter that receipt IS.
        # FIX3 F3-7: the 2026-09-21 prep reached this leg FOUR times
        # in eight minutes. The second, third and fourth had nothing
        # to do and did it anyway. A calendar-close receipt with the
        # same fired_via inside the window means this leg already ran
        # for this prep, so skip the work rather than repeat it.
        # FIX PASS 1 (review M-1): the key must be the mode a NO-OP run
        # actually writes. `calendar_close` sets `mode = MODE_APPLIED if
        # n_closed else MODE_PROPOSED`, so a run with nothing to close lands
        # `proposed` — asking for `applied` matched no row that could ever
        # exist and the skip was dead code. The constant is imported rather
        # than spelled, so the two cannot drift apart again.
        from calendar_close import _recent_noop_receipt as _recent
        from calendar_close import MODE_PROPOSED as _NOOP_MODE
        if _recent(workspace_root, {"mode": _NOOP_MODE,
                                    "n_planned": 0,
                                    "n_close_withheld": 0},
                   "manual"):
            out["calendar_run"] = {"skipped": "recent"}
            raise _SkipCalendarLeg
        cal_out = run_calendar_close_job(workspace_root, apply=True,
                                         now_iso=now_iso, fired_via="manual")
        n_cal = int(cal_out.get("n_closed") or 0)
        out["calendar_run"] = {k: cal_out.get(k) for k in
                               ("n_closed", "n_planned", "n_close_withheld",
                                "closes_enabled", "batch_id", "mode")}
        # IDENT1 I-2 (4): the answer NAMES which it was - `ran: true` here,
        # `skipped: "recent"` above - so a driven prep can count the leg.
        out["calendar_run"]["ran"] = True
        out["n_withheld"] += int(cal_out.get("n_close_withheld") or 0)
    except _SkipCalendarLeg:
        # Either the caller said not to run it (the scheduled fire,
        # whose own job runs it) or this prep already did, in which
        # case the skip named itself above.
        out.setdefault("calendar_run", None)
    except Exception as exc:  # noqa: BLE001
        out["refused_rows"].append({"leg": "calendar",
                                    "error": type(exc).__name__,
                                    "detail": str(exc)[:200]})
    deal = list(out.get("deal") or [])
    n_deal = 0
    if not deal:
        out["n_closed"] = n_cal
        out["n_closed_calendar"] = n_cal
        out["n_closed_deal"] = 0
        out["receipt_line"] = fact_receipt_line(n_cal, 0)
        return out
    batch_id = batch_id or _mint_batch_id(FACT_BATCH_PREFIX, now_iso)
    out["batch_id"] = batch_id
    for c in deal:
        day = str(c.get("deal_ts") or "")[:10]
        extra = {"brain_change_class": policy.CLOSE_CHANGE_CLASS,
                 "exit_route": "fact", "proof": PROOF_DEAL,
                 "deal_seq": c.get("deal_seq")}
        extra.update(policy.group_stamps(batch_id, f"deal:{c.get('deal_seq')}"))
        r = _close_one(workspace_root, c["commitment_id"],
                       evidence=f"signed or paid on {day}",
                       source_ref=None, extra=extra, now_iso=now_iso, out=out)
        if r:
            n_deal += 1
    out["n_closed"] = n_cal + n_deal
    out["n_closed_calendar"] = n_cal
    out["n_closed_deal"] = n_deal
    out["receipt_line"] = fact_receipt_line(n_cal, n_deal)
    return out


def _close_one(workspace_root, cid, *, evidence, source_ref, extra, now_iso,
               out) -> bool:
    from commitment_state import close_commitment
    try:
        res = close_commitment(
            workspace_root, cid, resolved_by=FACT_SOURCE_SKILL,
            evidence=evidence, source_skill=FACT_SOURCE_SKILL,
            source_ref=source_ref, extra_data=extra, mint_now_iso=now_iso)
    except Exception as exc:  # noqa: BLE001 — one bad row never stops a run
        out["refused_rows"].append({"commitment_id": cid,
                                    "error": type(exc).__name__,
                                    "detail": str(exc)[:200]})
        return False
    if res.get("status") == "closed":
        out["applied"].append({"commitment_id": res["commitment_id"],
                               "evidence": evidence})
        return True
    out["refused_rows"].append({"commitment_id": cid, "error": res.get("status")})
    return False


# ===========================================================================
# Route 3 — silence
# ===========================================================================
#
# "No activity from either side" is measured with the SHIPPED movement
# baseline (`commitment_backlog_sweep.last_activity_map`, which is
# `commitment_activity.derive_commitment_movement` — the one derivation every
# staleness surface passes through, seeded with each row's own capture time).
# A second definition of "quiet" is how two surfaces start disagreeing about
# the same row.
#
# FIX ROUND 1 (F-4) — that baseline used to count only OUR side: our chases,
# edits, re-dates, adjudications and reopens. Two kinds of touch were missing
# and the receipt was wrong because of it. The customer's OWN hand on a row
# (saying "later", taking it back off later, tapping a verb on it) is now
# movement, and so is the other side's own move on it (their reply and their
# part delivered, where the ledger recorded them). Both live in the one
# derivation — `commitment_activity.CUSTOMER_TOUCH_EVENT_TYPES` and
# `COUNTERPARTY_ACTIVITY_EVENT_TYPES` — so every staleness surface gets the
# same answer. On M's book four of the thirty-two rows this rail would have
# rested carried a snooze he made 33 days ago, under a receipt that said
# nobody had touched them.
#
# THE LIMIT THAT REMAINS, STATED: the baseline reads what the LEDGER records.
# An inbound message nobody turned into an event is still not on it, and a
# meeting or a calendar acceptance carries no commitment id to join on. The
# fences that make that safe are below: nothing overdue and nothing due this
# week is ever touched, every park and every let-go is one `undo` away, and
# their reply reopens a closed row through the inbound rail's own
# counter-evidence pass.


def _due_within(due_value, now_dt, days: int) -> bool:
    import datetime as _dt
    raw = str(due_value or "").strip()[:10]
    if not raw:
        return False
    try:
        due = _dt.date.fromisoformat(raw)
    except ValueError:
        return False
    return due <= (now_dt.date() + _dt.timedelta(days=days))


def rail_park_map(events) -> dict:
    """`{commitment id: {"ts", "days_quiet"}}` for the rows THIS RAIL rested
    and nothing has un-rested since (FIX ROUND 2, review R-1).

    Route 3 rests a row at 45 days and lets go at 60. On a seat whose
    maintenance fires daily the row is rested on day 45 and, from then on,
    the let-go leg skipped it for ever, so the six-week drain never drained
    and `show parked` only ever grew. A row THIS rail rested is its own
    argument, not a decision, so the rail may finish what it started; a row
    the CUSTOMER parked (a snooze, a hold, a mute) or that ANY OTHER leg
    parked — the owed-to-you quiet lane in `commitment_backlog_sweep`, and
    CLEANUP1's update item — is a decision somebody else made and stays
    parked. This map is how the difference is told: the newest
    park-or-unpark event per row, and only ours counts.

    The park's own `days_quiet` is carried forward, because the park event is
    itself `commitment_updated` — movement — so the shipped activity baseline
    reads a rested row's quiet clock as starting at the rest. The age that
    matters is the age of the SILENCE, so the row's age is
    `days_quiet at the rest + days since the rest`.

    A row this rail rested, let go, and the customer PUT BACK stays claimed
    with its clock restarted at the put-back (UNDOLAND1, M's ruling 7b) —
    back on the resting list, inside the cycle. See the comment on the
    `commitment_reopened` leg below.
    """
    from event_time import event_time
    out: dict = {}
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        # UNDOLAND1 (M's ruling 7b, 2026-09-08), amending FIX ROUND 3.
        #
        # Round 3 (review S-1b) fixed a real bug the wrong way. The park hint
        # survives both the close and the put-back, so before round 3 the
        # let-go leg measured the same sixty days the next morning and let
        # the row go again — the customer's `undo` reversed for exactly one
        # day. Round 3 stopped that by POPPING the row: after a put-back the
        # rail no longer claimed it.
        #
        # That left the row nowhere. Review round 4 (R4-1) measured the cost
        # on a real book: eight rows that would sit on the resting block for
        # ever — never let go again, never re-rested, and their own reply
        # could no longer take them off the resting list, because the
        # un-rest leg only un-rests a row THIS rail is resting. A dead spot
        # is worse than a loop.
        #
        # M ruled the third answer: the put-back row goes back on the
        # RESTING list, inside the cycle. The rail KEEPS its claim, and the
        # quiet clock RESTARTS at the undo — because the customer's `undo`
        # is movement (ruling 7), and a clock that restarts is what makes
        # this a cycle instead of either a loop or a dead end. Sixty more
        # quiet days after the undo and the row is let go again, with its own
        # receipt and its own `undo`. A reopen for a row this rail is NOT
        # resting is still nothing to do with us and is ignored.
        if ev.get("type") == "commitment_reopened":
            cid = str((ev.get("data") or {}).get("commitment_id") or "")
            prior = out.get(cid) if cid else None
            if prior is not None:
                out[cid] = {"ts": event_time(ev), "days_quiet": 0,
                            "reason": prior.get("reason") or "",
                            "parked_by": prior.get("parked_by") or ""}
            continue
        if ev.get("type") != "commitment_updated":
            continue
        d = ev.get("data") or {}
        if "status_hint" not in d and not d.get("unparked"):
            continue
        cid = str(d.get("commitment_id") or "")
        if not cid:
            continue
        if d.get("status_hint") == "parked":
            if str(d.get("parked_by") or "") == SILENCE_SOURCE_SKILL:
                dq = d.get("days_quiet")
                out[cid] = {"ts": event_time(ev),
                            "days_quiet": int(dq) if isinstance(dq, int) else None,
                            "reason": str(d.get("park_reason")
                                          or d.get("reason") or ""),
                            "parked_by": str(d.get("parked_by") or "")}
            else:
                out.pop(cid, None)     # somebody else's park is somebody else's call
        else:
            out.pop(cid, None)         # un-rested: the clock starts again
    return out


def _quiet_since_rail_park(park: dict, now_dt) -> Optional[int]:
    """How long the row has been silent, counting THROUGH our own rest."""
    if not park:
        return None
    when = _parse_now(park.get("ts")) if park.get("ts") else None
    if when is None:
        return None
    since = int((now_dt - when).total_seconds() // 86400)
    base = park.get("days_quiet")
    return since + int(base) if isinstance(base, int) else since


def silence_candidates(workspace_root, *, now_iso=None, events=None,
                       park_days: int = SILENCE_PARK_DAYS,
                       let_go_days: int = SILENCE_LET_GO_DAYS) -> dict:
    """The rows silence has finished with. No writes.

    Returns `{"park": [...], "let_go": [...], "n_open", "n_protected",
    "n_undateable"}`. A row is a candidate only when ALL of these hold:
      * it is open and not already parked;
      * it has NO due date — a dated row is a promise with a deadline and
        belongs to the overdue rules, not to silence;
      * it is not overdue and not due inside the next seven days (the
        importance rule: whatever a cap or a fold hides, it never hides an
        overdue item or one due this week — a rule about hiding is a rule
        about parking too);
      * its last movement can be DATED. Age-out is an argument from silence,
        and silence you cannot measure is not evidence — the shipped
        `age_out_candidates` says exactly this and it is right.
      * it is not a row the product is still UNSURE about (FIX ROUND 3,
        review S-3). Route 3 sides with route 1 here, not with route 2. The
        asymmetry stated at `apply_own_word_closes` turns on who is
        arguing: the customer's own sentence answers the question a held
        row is being held for, so route 2 may close one. Silence answers
        nothing — it is the product's own argument about a promise the
        product is not yet sure it heard, and "nobody has touched it" is
        not news about a row that was never on the working list. It is
        also what the writer says: `close_commitments` refuses a held row,
        so route 3 used to rest four of the rows on a real book and then
        refuse to let them go, every morning, in an error line no customer
        ever sees.
    """
    import datetime as _dt
    from commitment_backlog_sweep import last_activity_map
    from commitment_state import (STATUS_HINT_PARKED, _effective_status_hints,
                                  is_overdue)
    from cru_match import _commitment_id, load_open_commitments, partition_subitems
    ep = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if events is None:
        from events_io import load_events_owner_scoped
        events, _skipped = load_events_owner_scoped(workspace_root)
    opens = load_open_commitments(str(ep), events=events,
                                  workspace_root=workspace_root)
    rows, _subs = partition_subitems(opens)
    hints = _effective_status_hints(ep)
    activity = last_activity_map(ep)
    now_dt = _parse_now(now_iso)
    now_iso_s = now_dt.isoformat().replace("+00:00", "Z")
    rail_parks = rail_park_map(events)
    out = {"park": [], "let_go": [], "n_open": len(opens), "n_protected": 0,
           "n_undateable": 0, "n_dated": 0, "n_parked_already": 0,
           # R-1: of the let-go rows, how many were already resting. On a
           # seat that fires daily this is ALL of them after the first run.
           "n_let_go_from_rest": 0,
           # S-3: rows the product is still unsure about, left alone.
           "n_unconfirmed": 0}
    for ev in rows:
        cid = _commitment_id(ev)
        d = (ev.get("data") or {})
        if _is_pending_review(ev):
            # S-3 — a held row is not "on the plate nobody touched"; it is
            # not on the plate at all. Left alone on BOTH legs, so a row
            # already rested by an older build is not listed for a let-go
            # the writer will only refuse.
            out["n_unconfirmed"] += 1
            continue
        if hints.get(cid) == STATUS_HINT_PARKED:
            # R-1 — a row THIS rail rested is measured for quiet and let go
            # at 60; a row the customer or any other leg parked stays parked.
            park = rail_parks.get(cid)
            quiet = _quiet_since_rail_park(park, now_dt)
            due = d.get("due")
            if (quiet is None or quiet < let_go_days
                    or (str(due or "").strip()
                        and (is_overdue(due, now_iso_s)
                             or _due_within(due, now_dt, 7)))):
                out["n_parked_already"] += 1
                continue
            out["let_go"].append(
                {"commitment_id": cid, "title": str(d.get("title") or ""),
                 "primary_thread_id": ev.get("primary_thread_id") or "",
                 "days_quiet": quiet, "proof": stored_proof(ev),
                 "last_activity": str(park.get("ts") or "")})
            out["n_let_go_from_rest"] += 1
            continue
        due = d.get("due")
        if str(due or "").strip():
            out["n_dated"] += 1
            if is_overdue(due, now_iso_s) or _due_within(due, now_dt, 7):
                out["n_protected"] += 1
            continue
        seen = activity.get(cid)
        if seen is None:
            out["n_undateable"] += 1
            continue
        quiet = int((now_dt - seen).total_seconds() // 86400)
        if quiet < park_days:
            continue
        row = {"commitment_id": cid, "title": str(d.get("title") or ""),
               "primary_thread_id": ev.get("primary_thread_id") or "",
               "days_quiet": quiet, "proof": stored_proof(ev),
               "last_activity": seen.isoformat().replace("+00:00", "Z")}
        (out["let_go"] if quiet >= let_go_days else out["park"]).append(row)
    out["park"].sort(key=lambda r: r["days_quiet"], reverse=True)
    out["let_go"].sort(key=lambda r: r["days_quiet"], reverse=True)
    return out


def _parse_now(now_iso=None):
    from datetime import datetime, timezone
    from cru_match import _parse_ts
    return (_parse_ts(now_iso) if now_iso else None) or datetime.now(timezone.utc)


def let_go_wrap_line(n: int) -> str:
    """The ONE line the weekly wrap carries (SPEC_FLOW1 Lane B item 3). It
    says what happened and how to reverse it, in the customer's words.

    UNDOLAND1 (M's ruling 7b) — it also says WHERE the `undo` puts them.
    "Put them all back" read as "back on your working list", and that was
    never what happened: a put-back row goes back on the RESTING list, and
    now stays there with its quiet clock restarted. Naming the destination
    is the difference between a receipt and a promise the product does not
    keep.
    """
    if n <= 0:
        return ""
    if n == 1:
        return ("Let go 1 item you never touched; say `undo` to put it back "
                "on the resting list.")
    return (f"Let go {n} items you never touched; say `undo` to put them all "
            "back on the resting list.")


def park_receipt_line(n: int) -> str:
    if n <= 0:
        return ""
    return (f"Rested {n} item{'' if n == 1 else 's'} nobody has touched in six "
            f"weeks — {'it is' if n == 1 else 'they are'} still yours, just "
            "off the working list; say `undo` to put "
            f"{'it' if n == 1 else 'them'} back.")


def run_silence_pass(workspace_root, *, apply: bool = False, now_iso=None,
                     park_days: int = SILENCE_PARK_DAYS,
                     let_go_days: int = SILENCE_LET_GO_DAYS,
                     batch_id=None) -> dict:
    """Route 3, the job. ONE run, TWO batches — the park and the let-go are
    different acts with different reversals, and a customer who wants the
    let-go back should not have to un-rest six weeks of resting to get it."""
    import commitment_policy as policy
    from commitment_state import (PARK_CHANGE_CLASS, REASON_ORIGIN_PRODUCT,
                                  close_commitments, park_commitments)
    out = dict(silence_candidates(workspace_root, now_iso=now_iso,
                                  park_days=park_days, let_go_days=let_go_days))
    out.update({"enabled": silence_age_out_enabled(workspace_root),
                "n_parked": 0, "n_let_go": 0, "park_batch_id": None,
                "let_go_batch_id": None, "receipt_line": "", "wrap_line": ""})
    if not apply:
        return out
    if not out["enabled"]:
        out["withheld"] = len(out["park"]) + len(out["let_go"])
        return out
    if out["park"]:
        pb = (batch_id or _mint_batch_id(SILENCE_BATCH_PREFIX, now_iso)) + "-park"
        out["park_batch_id"] = pb
        res = park_commitments(
            workspace_root,
            [{"commitment_id": r["commitment_id"], "reason": SILENCE_PARK_REASON,
              "brain_batch_id": pb,
              # MF-5 (CARD1) — SILENCE_PARK_REASON is the PRODUCT's own
              # sentence ("no movement 45 days"), not a word the customer
              # typed, and it says so rather than relying on an absent
              # stamp meaning the same thing. It is scanned in full.
              "extra_data": {"exit_route": "silence",
                             "days_quiet": r["days_quiet"],
                             "reason_origin": REASON_ORIGIN_PRODUCT}}
             for r in out["park"]],
            parked_by=SILENCE_SOURCE_SKILL, source_skill=SILENCE_SOURCE_SKILL)
        out["n_parked"] = sum(1 for r in res if r.get("status") == "parked")
        out["park_results"] = res
    if out["let_go"]:
        lb = (batch_id or _mint_batch_id(SILENCE_BATCH_PREFIX, now_iso)) + "-letgo"
        out["let_go_batch_id"] = lb
        res = close_commitments(
            workspace_root,
            [{"commitment_id": r["commitment_id"],
              "resolved_by": SILENCE_SOURCE_SKILL,
              "resolution": "dropped",
              "evidence": SILENCE_LET_GO_REASON,
              "primary_thread_id": r.get("primary_thread_id") or None,
              "extra_data": {"brain_change_class": policy.CLOSE_CHANGE_CLASS,
                             "brain_batch_id": lb, "exit_route": "silence",
                             "days_quiet": r["days_quiet"]}}
             for r in out["let_go"]],
            source_skill=SILENCE_SOURCE_SKILL)
        out["n_let_go"] = sum(1 for r in res if r.get("status") == "closed")
        out["let_go_results"] = res
    out["receipt_line"] = park_receipt_line(out["n_parked"])
    out["wrap_line"] = let_go_wrap_line(out["n_let_go"])
    return out


# ===========================================================================
# Counter-evidence — their word puts a closed row back
# ===========================================================================

#: How long after one of these rails closed a row their word still reopens
#: it. Past this the row is history, not a live question.
COUNTER_EVIDENCE_DAYS = 21

#: FIX ROUND 2 (review R-4). A put-back is an ACT, and CONTRACT Rule 34's
#: fourth fence promises every act a receipt AND an undo. These give it both:
#: its own batch id, so `undo` names it; its own change class, so
#: `brain_undo` knows how to reverse it (by re-closing exactly as it was
#: closed); and its own line in the CHANGED list, so somebody sees it.
COUNTER_EVIDENCE_CHANGE_CLASS = "counter_evidence_reopen"
COUNTER_EVIDENCE_BATCH_PREFIX = "cev_"

#: FIX ROUND 2 (review R-5). The row the other side chased is a row somebody
#: HAS touched, and route 3's receipt says "nobody has touched" about the
#: rows it rests. The movement baseline names `email_received` as the other
#: side's own move; nothing in the product wrote it, so the sentence was
#: false whenever they chased and the rail matched nothing. THIS is the
#: writer. A row this rail had already rested when they came back is
#: un-rested — its own act, its own batch, its own reverser.
COUNTERPARTY_ACTIVITY_EVENT_TYPE = "email_received"
UNREST_CHANGE_CLASS = "counter_evidence_unrest"
UNREST_BATCH_PREFIX = "unr_"


#: FIX ROUND 2 (review R-7). An out-of-office echoes the subject line back,
#: so it scores against the row's own words and reopened items nobody had
#: come back about. These are the shapes the mail world already agrees on:
#: the daemon senders the outcome rail knows (`email_outcomes._DAEMON_MARKERS`
#: — bounces and postmasters), plus the subject prefixes every mail client
#: writes for the automated replies `shared/PASSIVE_CAPTURE.md` already tells
#: capture to skip.
AUTOMATED_SUBJECT_MARKERS = (
    "automatic reply", "auto-reply", "autoreply", "auto reply",
    "out of office", "out-of-office", "ooo:",
    "read:", "read receipt",
    "delivery status notification", "undeliverable", "undelivered mail",
    "mail delivery", "returned mail",
)

#: The first sentence of an out-of-office whose subject was left plain.
#:
#: FIX ROUND 3 (review L-3) — the body leg is read only in the FIRST
#: SENTENCE, and only when that sentence asks nothing. An out-of-office
#: STATES; it never asks. Without the second half, "I'll be out of the
#: office Thursday but can we meet Friday about the pricing sheet?" read as
#: a machine, and a real reply was dropped — a missed put-back and a missed
#: touch. STILL A LIMIT, STATED: a real reply that mentions being away and
#: asks nothing in its first sentence is still read as automated.
AUTOMATED_BODY_MARKERS = ("out of the office", "away from the office",
                          "on annual leave", "this is an automated")


def is_automated_message(msg) -> bool:
    """True when this inbound message is a machine talking, not the other
    side coming back. An automated message is never counter-evidence and
    never counts as their move (review R-7)."""
    try:
        from email_outcomes import _DAEMON_MARKERS
    except Exception:  # pragma: no cover
        _DAEMON_MARKERS = ("mailer-daemon", "postmaster")
    d = msg if isinstance(msg, dict) else {}
    subject = str(d.get("subject") or "").strip().lower()
    if any(subject.startswith(m) for m in AUTOMATED_SUBJECT_MARKERS):
        return True
    sender_addr = str(d.get("sender") or d.get("from") or "").lower()
    if any(m in sender_addr for m in _DAEMON_MARKERS):
        return True
    if str(d.get("auto_submitted") or "").strip().lower() not in ("", "no"):
        return True
    head = str(d.get("body") or "").strip().lower()[:200]
    first = re.split(r"(?<=[.!?])\s", head, maxsplit=1)[0] if head else ""
    if "?" in first:      # L-3: an out-of-office states; it never asks
        return False
    return any(m in first for m in AUTOMATED_BODY_MARKERS)


def exit_closed_rows(workspace_root, *, events=None, now_iso=None,
                     within_days: int = COUNTER_EVIDENCE_DAYS) -> list:
    """The rows one of the three exit rails closed recently and nobody has
    put back — the set counter-evidence is allowed to reopen. Read-only.

    Each row carries what a caller needs to decide whether an inbound message
    is THEM coming back about THIS item (FIX ROUND 1, F-1): the row's own
    `title` (from the capture event — a closure carries a pointer, not a
    restatement of the promise), its `counterparty_ids` (the closure event's
    `person_ids` is `[owner_id]` and never the other side, so a caller that
    gated on it would gate on the customer), and `capture_event`, the
    commitment event itself — so the caller can ask the SHIPPED thread
    matcher (`cru_match.commitment_matches_thread_ref`) whether a message
    arrived in this row's own conversation, rather than inventing a second
    idea of what "the same thread" means.
    """
    from closure_index import reversed_closer_positions
    from commitment_parties import counterparty_ids as _cp_ids
    from cru_match import _commitment_field, _commitment_id
    from event_time import event_time
    if events is None:
        from events_io import load_events_owner_scoped
        events, _skipped = load_events_owner_scoped(workspace_root)
    now_dt = _parse_now(now_iso)
    reversed_at = reversed_closer_positions(events)
    # FIX ROUND 2 (review R-2) — the calendar leg is route 1's other half and
    # signs its closes with its own job name, so a set of only the three
    # in-module rail names could never see the leg that closes the most rows.
    # Read the closer's own constant, never a second copy of the string.
    try:
        from calendar_close import SOURCE_SKILL as _CAL_SOURCE_SKILL
    except Exception:  # pragma: no cover — the closer is always importable
        _CAL_SOURCE_SKILL = "calendar-close"
    rails = {OWN_WORD_SOURCE_SKILL, FACT_SOURCE_SKILL, SILENCE_SOURCE_SKILL,
             _CAL_SOURCE_SKILL}
    captures: dict = {}
    for ev in events:
        if isinstance(ev, dict) and ev.get("type") == "commitment":
            captures[_commitment_id(ev)] = ev
    out = []
    for pos, ev in enumerate(events):
        if not isinstance(ev, dict) or ev.get("type") != "commitment_resolved":
            continue
        if pos in reversed_at:
            continue
        d = ev.get("data") or {}
        if str(ev.get("source_skill") or "") not in rails:
            continue
        when = _parse_now(event_time(ev))
        if (now_dt - when).days > int(within_days):
            continue
        cid = str(d.get("commitment_id") or "")
        cap = captures.get(cid)
        title = str(d.get("title") or d.get("title_snapshot") or "")
        if not title and cap is not None:
            title = str(_commitment_field(cap, "title") or "")
        out.append({"commitment_id": cid,
                    "title": title,
                    "closed_ts": event_time(ev),
                    # R-4 — what the closure said, so a put-back can be put
                    # back EXACTLY: the reverser re-closes with the original
                    # closer's own words, never a fresh guess.
                    "resolved_by": str(d.get("resolved_by") or ""),
                    "evidence": str(d.get("evidence") or ""),
                    "resolution": str(d.get("resolution") or "done"),
                    "closed_source_ref": d.get("source_ref"),
                    # WHICH rail closed it. The re-close on undo is that
                    # rail closing again, not the session lane closing on
                    # an id it picked itself (CLOSEID2).
                    "closed_source_skill": str(ev.get("source_skill") or ""),
                    "primary_thread_id": (ev.get("primary_thread_id")
                                          or (cap or {}).get("primary_thread_id")
                                          or ""),
                    "route": str(d.get("exit_route") or ""),
                    "counterparty_ids": (list(_cp_ids(cap)) if cap is not None
                                         else []),
                    "capture_event": cap,
                    "person_ids": list(ev.get("person_ids") or [])})
    return out


def counter_evidence_seen(workspace_root, *, events=None) -> set:
    """FIX ROUND 3 (review S-2) — the (row, message) pairs a put-back has
    already been made on, for ever.

    The un-rest leg has had this since fix round 2; the put-back had only a
    per-RUN guard, and the inbox rail is fed every UNREAD message from the
    last fortnight. So a chase the customer had not opened was re-read every
    morning: they said `undo`, the rail re-closed the row, and the next
    morning the same message put it back again. One message is one piece of
    counter-evidence, and it is spent the first time it is used.
    """
    if events is None:
        from events_io import load_events_owner_scoped
        events, _skipped = load_events_owner_scoped(workspace_root)
    out = set()
    for ev in events or []:
        if not isinstance(ev, dict) or ev.get("type") != "commitment_reopened":
            continue
        d = ev.get("data") or {}
        if d.get("brain_change_class") != COUNTER_EVIDENCE_CHANGE_CLASS:
            continue
        ref = str(d.get("source_ref") or "")
        if ref:
            out.add((str(d.get("commitment_id") or ""), ref))
    return out


def reopen_on_counter_evidence(workspace_root, *, matches, now_iso=None,
                               source_skill=None, batch_id=None) -> dict:
    """Their word puts it back. `matches` is `[{commitment_id, why}]` — the
    caller (the inbound mail rail, which is the only thing that reads their
    replies) hands in what it found; this writes the reopen with a receipt.

    The reopen is stamped with the RAIL, never with the customer: M did not
    reverse anything, the product did (ATTRIB2's regression, seqs
    15503-15506 on the 2026-09-07 book).
    """
    from commitment_state import reopen_commitment
    rail = source_skill or FACT_SOURCE_SKILL
    batch_id = batch_id or _mint_batch_id(COUNTER_EVIDENCE_BATCH_PREFIX, now_iso)
    out = {"n_reopened": 0, "reopened": [], "errors": [], "receipt_line": "",
           "batch_id": batch_id, "n_already_used": 0}
    # FIX ROUND 3 (review S-2) — one message, one put-back, for ever.
    spent = counter_evidence_seen(workspace_root) if matches else set()
    for m in matches or []:
        cid = str((m or {}).get("commitment_id") or "")
        if not cid:
            continue
        ref = str((m or {}).get("source_ref") or "")
        if ref and (cid, ref) in spent:
            out["n_already_used"] += 1
            continue
        why = str((m or {}).get("why") or "they came back about it")[:200]
        # R-4 — the undo's anchor: what this row's closure said, carried on
        # the reopen so `undo` can put the closure back exactly.
        extra = {"brain_change_class": COUNTER_EVIDENCE_CHANGE_CLASS,
                 "brain_batch_id": batch_id}
        for src, dst in (("resolved_by", "prior_resolved_by"),
                         ("evidence", "prior_evidence"),
                         ("resolution", "prior_resolution"),
                         ("closed_source_ref", "prior_source_ref"),
                         ("closed_source_skill", "prior_source_skill"),
                         # FIX ROUND 3 (review L-1) — and WHICH ROUTE closed
                         # it, so the re-close on `undo` is filed where the
                         # first close was rather than under "other".
                         ("exit_route", "prior_exit_route")):
            if (m or {}).get(src) not in (None, ""):
                extra[dst] = (m or {})[src]
        try:
            r = reopen_commitment(workspace_root, cid, reopened_by=rail,
                                  reason=why, source_skill=rail,
                                  source_ref=(m or {}).get("source_ref"),
                                  mint_now_iso=now_iso, extra_data=extra)
        except Exception as exc:  # noqa: BLE001
            out["errors"].append({"commitment_id": cid,
                                  "error": type(exc).__name__})
            continue
        if r.get("status") in ("reopened", "closed", "ok") or r.get("event"):
            out["n_reopened"] += 1
            out["reopened"].append({"commitment_id": cid, "why": why})
            if ref:
                spent.add((cid, ref))
    n = out["n_reopened"]
    if n:
        # R-4 — every customer line this lane writes carries the way back
        # (G61 fence 2), and this one is an act like the other three.
        out["receipt_line"] = (
            f"Put {n} item{'' if n == 1 else 's'} back — they came back about "
            f"{'it' if n == 1 else 'them'} after I had closed "
            f"{'it' if n == 1 else 'them'}; say `undo` to close "
            f"{'it' if n == 1 else 'them'} again.")
    return out


def counterparty_activity_line(n_marked: int, n_unrested: int) -> str:
    """What the customer is told when the other side came back about a row
    the rail had rested. Plain words, no id, no rail name, with the way
    back. Silent when nothing was un-rested: a marker on a working row
    changed nothing anybody can see."""
    if n_unrested <= 0:
        return ""
    one = n_unrested == 1
    return (f"Put {n_unrested} item{'' if one else 's'} back on your working "
            f"list — they came back about {'it' if one else 'them'} after I "
            f"had rested {'it' if one else 'them'}; say `undo` to rest "
            f"{'it' if one else 'them'} again.")


def record_counterparty_activity(workspace_root, *, opens, messages,
                                 user_person_id, source_skill,
                                 provider=None, now_iso=None,
                                 batch_id=None) -> dict:
    """FIX ROUND 2 (review R-5) — the other side's own move, recorded against
    the row it is about, and the rest it undoes.

    `opens` are the OPEN rows this run already loaded (never a second read),
    `messages` the inbound batch. A message counts for a row when the sender
    is on the other side of it AND either the message arrived in that row's
    own conversation or it reads as being about it at the CONFIRM bar — the
    same two doors the put-back uses, and the same shipped scorer every
    other match on this rail goes through. An automated message (an
    out-of-office, a read receipt, a delivery notice) is not the other side
    coming back and is dropped before either door.

    Writes `email_received` against the row (no close, no proposal, no
    question — a marker), and un-rests a row THIS rail had rested. Never
    raises into the mail run.
    """
    from commitment_parties import counterparty_ids as _cp_ids
    from commitment_state import (STATUS_HINT_PARKED, _effective_status_hints,
                                  unpark_commitments)
    from cru_match import (_commitment_id, _commitment_field,
                           _match_thresholds, commitment_matches_thread_ref,
                           score_match)
    from event_gate import append_event
    from events_io import load_events_owner_scoped
    from connector_adapters.provenance import primary_artifact_key
    ep = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    out = {"n_marked": 0, "n_unrested": 0, "marked": [], "unrested": [],
           "receipt_line": "", "batch_id": None}
    _, bar = _match_thresholds(workspace_root)
    events, _skipped = load_events_owner_scoped(workspace_root)
    # Idempotence: one marker per (row, message), for ever.
    already = {(str((e.get("data") or {}).get("commitment_id") or ""),
                str((e.get("data") or {}).get("message_ref") or ""))
               for e in events
               if isinstance(e, dict)
               and e.get("type") == COUNTERPARTY_ACTIVITY_EVENT_TYPE}
    rail_parks = rail_park_map(events)
    hints = _effective_status_hints(ep)
    rows = []
    for ev in opens or []:
        if str(_commitment_field(ev, "owner_id") or "") != str(user_person_id):
            continue          # a row THEY owe is the chase lane's business
        rows.append((_commitment_id(ev), ev, set(_cp_ids(ev))))
    evs, marked = [], []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        sender = str(msg.get("sender_person_id") or "").strip()
        if not sender or sender == str(user_person_id):
            continue
        if is_automated_message(msg):
            continue
        ref = primary_artifact_key(provider, msg.get("message_id"))
        thread_key = primary_artifact_key(provider, msg.get("thread_id"))
        text = " ".join(str(msg.get(f) or "")
                        for f in ("subject", "body")).strip()
        for cid, ev, cps in rows:
            if sender not in cps:
                continue
            if (cid, str(ref or "")) in already:
                continue
            on_thread = bool(thread_key) and commitment_matches_thread_ref(
                ev, thread_key)
            if not on_thread and score_match(
                    text, str(_commitment_field(ev, "title") or "")) < bar:
                continue
            already.add((cid, str(ref or "")))
            # The type is spelled out here on purpose: the event-contract
            # guard finds writers by the literal beside the append, and a
            # type read by production with no findable writer is exactly the
            # bug this leg exists to fix. `COUNTERPARTY_ACTIVITY_EVENT_TYPE`
            # is the same string, pinned equal in the suite.
            evs.append({"type": "email_received",
                        "source_skill": source_skill,
                        "primary_thread_id": ev.get("primary_thread_id") or "",
                        "person_ids": [sender],
                        "data": {"commitment_id": cid, "message_ref": ref,
                                 "sender_person_id": sender,
                                 "matched_on": "thread" if on_thread else "title"}})
            marked.append(cid)
    if evs:
        append_event(ep, evs, holder=source_skill)
        out["n_marked"] = len(evs)
        out["marked"] = marked
    # The rest they just disproved. Only OUR rest, and only for the rows
    # this batch actually moved.
    to_unrest = [cid for cid in dict.fromkeys(marked)
                 if hints.get(cid) == STATUS_HINT_PARKED
                 and cid in rail_parks]
    if to_unrest:
        bid = batch_id or _mint_batch_id(UNREST_BATCH_PREFIX, now_iso)
        out["batch_id"] = bid
        for cid in to_unrest:
            park = rail_parks.get(cid) or {}
            res = unpark_commitments(
                workspace_root, [cid], unparked_by=source_skill,
                source_skill=source_skill,
                reason="they came back about it",
                extra_data={"brain_change_class": UNREST_CHANGE_CLASS,
                            "brain_batch_id": bid,
                            "prior_park_reason": park.get("reason") or "",
                            "prior_parked_by": park.get("parked_by") or ""})
            for r in res:
                if r.get("status") == "unparked":
                    out["n_unrested"] += 1
                    out["unrested"].append(cid)
    out["receipt_line"] = counterparty_activity_line(out["n_marked"],
                                                     out["n_unrested"])
    return out


# ===========================================================================
# One question per row (M's ruling 6)
# ===========================================================================

def row_already_asked(workspace_root, commitment_id, *, marks=None) -> bool:
    """M's ruling 6 — a withheld close never writes a SECOND proposal on a
    row that is already carrying a question. Read through the shipped mark
    reader (`commitment_state.asked_commitment_marks`), never a second
    idea of what "already asked" means."""
    from commitment_state import asked_commitment_marks
    if marks is None:
        try:
            marks = asked_commitment_marks(workspace_root)
        except Exception:  # noqa: BLE001 — unreadable marks never ask twice
            return True
    return bool((marks or {}).get(str(commitment_id)))


# ===========================================================================
# The scheduled job — one fire, the two rails a schedule owns
# ===========================================================================

JOB_ID = "exit-doors"
RECEIPT_TYPE = "pack_run"


def run_exit_doors_job(workspace_root, *, apply: bool = False, now_iso=None,
                       fired_via=None) -> dict:
    """The maintenance leg. TWO of the three routes run here:

      * the paid-or-signed leg of route 1 (the calendar leg has its own job,
        `calendar-close`, in this same fire — see `run_calendar=False`);
      * route 3, silence.

    Route 2 is NOT here and cannot be: it reads a transcript, and a
    transcript arrives when a call is captured, not when a clock strikes.
    It runs at capture (`apply_own_word_closes`).

    Never raises into the dispatcher: a leg that fails is reported and the
    other one still runs."""
    # FIX3 F3-6: a literal default IS an explicit value by the time the
    # resolver sees it (the FIX2 M-3 lesson), so this signature says
    # nothing and the seat answers. A legacy or local seat still reads
    # `scheduled`, byte for byte; a merged seat with nothing forwarded
    # reads `manual`, which is what a typed brief actually is.
    fired_via = _effective_fired_via(fired_via)
    out = {"ran": True, "applied": bool(apply), "job": JOB_ID,
           "fact": None, "silence": None, "receipt_lines": [], "wrap_line": ""}
    try:
        out["fact"] = (apply_fact_closes(workspace_root, now_iso=now_iso,
                                         run_calendar=False)
                       if apply else
                       plan_fact_closes(workspace_root, now_iso=now_iso))
    except Exception as exc:  # noqa: BLE001
        out["fact"] = {"error": type(exc).__name__, "detail": str(exc)[:200]}
    try:
        out["silence"] = run_silence_pass(workspace_root, apply=apply,
                                          now_iso=now_iso)
    except Exception as exc:  # noqa: BLE001
        out["silence"] = {"error": type(exc).__name__, "detail": str(exc)[:200]}
    for leg in (out["fact"], out["silence"]):
        line = (leg or {}).get("receipt_line") if isinstance(leg, dict) else ""
        if line:
            out["receipt_lines"].append(line)
    out["wrap_line"] = (out["silence"] or {}).get("wrap_line", "") \
        if isinstance(out["silence"], dict) else ""
    if apply:
        _log_job_receipt(workspace_root, out, fired_via=fired_via)
    return out


def _log_job_receipt(workspace_root, out: dict, *, fired_via: str) -> None:
    try:
        from receipts import log_receipt
        fact = out.get("fact") if isinstance(out.get("fact"), dict) else {}
        sil = out.get("silence") if isinstance(out.get("silence"), dict) else {}
        log_receipt(
            workspace_root, JOB_ID, receipt_type=RECEIPT_TYPE,
            fired_via=fired_via,
            surfaced=int(fact.get("n_closed") or 0) + int(sil.get("n_parked") or 0)
            + int(sil.get("n_let_go") or 0),
            extra_data={
                "n_closed_deal": int(fact.get("n_closed_deal") or 0),
                "n_parked": int(sil.get("n_parked") or 0),
                "n_let_go": int(sil.get("n_let_go") or 0),
                "silence_enabled": sil.get("enabled"),
                "proof_census": fact.get("proof_census"),
                "receipt_lines": out.get("receipt_lines"),
                "wrap_line": out.get("wrap_line"),
            })
    except Exception:  # noqa: BLE001 — a receipt never fails a run
        pass


def main(argv=None) -> int:
    import argparse
    import json as _json
    parser = argparse.ArgumentParser(
        description="Command Room exit doors (SPEC_FLOW1 Lane B)")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--now", default=None)
    parser.add_argument("--fired-via", default=None,
                        help="the seat decides when nothing is said (receipts.effective_fired_via); a literal default here IS an explicit value by the time the resolver sees it")
    parser.add_argument("--triggered-by", default=None,
                        help="the surface that asked for this run")
    args = parser.parse_args(argv)
    # FIX3 F3-6: export what this run was asked by, so every composer
    # below reads it from one place instead of being threaded through
    # a dozen signatures.
    if getattr(args, "triggered_by", None):
        os.environ["CR_TRIGGERED_BY"] = str(args.triggered_by)
    print(_json.dumps(run_exit_doors_job(args.workspace, apply=args.apply,
                                         now_iso=args.now,
                                         fired_via=args.fired_via),
                      indent=2, default=str))
    return 0


__all__ = [
    "JOB_ID", "RECEIPT_TYPE", "run_exit_doors_job", "main",
    "OWN_WORD_SOURCE_SKILL", "FACT_SOURCE_SKILL", "SILENCE_SOURCE_SKILL",
    "OWN_WORD_CONFIRMED_BY", "OWN_WORD_BATCH_PREFIX", "FACT_BATCH_PREFIX",
    "SILENCE_BATCH_PREFIX", "SILENCE_PARK_DAYS", "SILENCE_LET_GO_DAYS",
    "SILENCE_PARK_REASON", "SILENCE_LET_GO_REASON",
    "OWN_WORD_MIN_SHARED_TOKENS",
    "own_word_statements", "owner_turn_texts", "match_statement_to_rows",
    "own_word_bar", "own_word_closes_enabled", "plan_own_word_closes",
    "apply_own_word_closes",
    "close_from_own_word", "CHAT_SOURCE_REF", "typed_reports", "own_word_receipt_line",
    "PROOF_MAIL", "PROOF_REPLY", "PROOF_CALENDAR", "PROOF_DEAL",
    "PROOF_OWN_WORD", "PROOF_KINDS", "PROOF_KEY",
    "proof_for_row", "stored_proof", "proof_census",
    "calendar_closes_enabled", "silence_age_out_enabled",
    "plan_fact_closes", "apply_fact_closes", "fact_receipt_line",
    "rail_park_map",
    "COUNTER_EVIDENCE_CHANGE_CLASS", "COUNTER_EVIDENCE_BATCH_PREFIX",
    "COUNTERPARTY_ACTIVITY_EVENT_TYPE", "UNREST_CHANGE_CLASS",
    "UNREST_BATCH_PREFIX", "record_counterparty_activity",
    "counterparty_activity_line", "is_automated_message",
    "AUTOMATED_SUBJECT_MARKERS",
    "silence_candidates", "run_silence_pass", "let_go_wrap_line",
    "park_receipt_line", "COUNTER_EVIDENCE_DAYS", "exit_closed_rows",
    "reopen_on_counter_evidence", "row_already_asked",
]


if __name__ == "__main__":
    sys.exit(main())
