#!/usr/bin/env python3
"""
Voice-calibration feedback loop (SPEC B1) — make `shared/VOICE_CALIBRATION.md`
actually run.

The protocol defined a corrections-log schema, batching, and staleness rules but
nothing ever DETECTED an edit, classified it, appended a row, or refreshed a
voice block. This module is the missing machinery:

  - snapshot_draft / draft-snapshots.jsonl  — persist the drafted body so a
    drafted-vs-sent diff is possible (bodies stay OUT of events.jsonl's hot stream).
  - diff_and_classify                        — deterministic correction typing
    (phrasing | structure | vocabulary | tone). Pure; no I/O, no clock.
  - append_correction / corrections-<skill>.jsonl  — append-only, deduped.
  - reconcile_sent_against_snapshots          — async detection at Sent-reconcile.
  - load_voice_block_override / write_…       — the CUSTOMER-SIDE voice block at
    `_hq/voice/voice-block-<skill>.md` (SKILL.md blocks are plugin-side and get
    overwritten on update, so calibration MUST live in the workspace).
  - load_corrections / unreviewed_counts / group_correction_patterns — the
    weekly `learning` job's voice-leg batching reads (Pass 11 is retired).

CLIENT SAFETY: all writes land under `_hq/voice/` in the customer workspace —
NEVER into the plugin directory. Bodies are workspace-private (same class as
meeting transcripts); cleanup prunes snapshots. Never raises into a send path.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Optional

try:
    from cru_match import _now_iso, _parse_ts, load_events_defensively
    from event_time import event_time
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cru_match import _now_iso, _parse_ts, load_events_defensively  # type: ignore
    from event_time import event_time  # type: ignore


def _voice_dir(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "voice"


# ---------------------------------------------------------------------------
# Classification (pure)
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "the", "a", "an", "to", "of", "and", "or", "for", "on", "in", "at", "is",
    "are", "be", "i", "you", "we", "it", "this", "that", "with", "as", "by",
    "our", "your", "my", "me", "us", "so", "but", "if", "from", "will", "can",
}
_TONE_MARKERS = {
    "hi", "hello", "dear", "hey", "thanks", "thank", "regards", "best",
    "cheers", "sincerely", "warmly", "wanted", "just", "maybe", "perhaps",
    "kindly", "please", "appreciate", "hope", "really", "very", "quick",
}


def _strip_quotes(text: str) -> str:
    """Drop reply blockquote lines so a quoted counterparty passage never
    self-reports as the user editing the draft (email-writer Phase 4 rule)."""
    return "\n".join(l for l in (text or "").splitlines() if not l.lstrip().startswith(">"))


def _paragraphs(text: str) -> List[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]


def _tokens(s: str) -> List[str]:
    return re.findall(r"[a-z0-9']+", (s or "").lower())


def _content_tokens(s: str) -> List[str]:
    return [t for t in _tokens(s) if t not in _STOPWORDS]


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s.strip()]


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(_tokens(a)), set(_tokens(b))
    if not sa and not sb:
        return 1.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 1.0


def _has_bullets(text: str) -> bool:
    return any(re.match(r"\s*[-*•]\s+", l) for l in (text or "").splitlines())


def _classify_pair(o: str, c: str) -> tuple:
    # structure: bullets <-> prose conversion
    if _has_bullets(o) != _has_bullets(c):
        return "structure", "bullets<->prose"
    # tone: the changed tokens are all greeting/sign-off/hedging markers
    changed = set(_tokens(o)) ^ set(_tokens(c))
    if changed and changed <= _TONE_MARKERS:
        return "tone", "greeting/sign-off/hedging change"
    # vocabulary: same sentence skeleton, <=3 content-word swaps per sentence, sim>=0.7
    so, sc = _sentences(o), _sentences(c)
    if so and len(so) == len(sc):
        subs, ok = [], True
        for a, b in zip(so, sc):
            if _jaccard(a, b) < 0.7:
                ok = False
                break
            ca, cb = _content_tokens(a), _content_tokens(b)
            diff = [t for t in ca if t not in cb] + [t for t in cb if t not in ca]
            if len(diff) > 3:
                ok = False
                break
            if ca != cb:
                subs.append(f"{' '.join(ca)} -> {' '.join(cb)}")
        if ok and subs:
            return "vocabulary", ("; ".join(subs))[:200]
    # phrasing: default — sentence rewritten, same intent
    return "phrasing", ""


def diff_and_classify(original: str, corrected: str) -> List[dict]:
    """Return a list of `{original, corrected, correction_type, notes}` per D4.
    Pure. One row per changed paragraph pair, capped at 5; a full rewrite
    collapses to a single `structure` row."""
    o = _strip_quotes(original or "").strip()
    c = _strip_quotes(corrected or "").strip()
    if o == c:
        return []
    po, pc = _paragraphs(o), _paragraphs(c)

    # Full rewrite: substantial text with almost nothing in common.
    if _jaccard(o, c) < 0.3 and (len(po) >= 2 or len(_tokens(o)) >= 40):
        return [{"original": o[:500], "corrected": c[:500],
                 "correction_type": "structure", "notes": "full rewrite"}]

    # Paragraph count shifted materially → one structure row.
    if abs(len(po) - len(pc)) >= 2:
        return [{"original": o[:500], "corrected": c[:500], "correction_type": "structure",
                 "notes": f"paragraph count {len(po)}->{len(pc)}"}]

    rows: List[dict] = []
    for i in range(max(len(po), len(pc))):
        a = po[i] if i < len(po) else ""
        b = pc[i] if i < len(pc) else ""
        if a.strip() == b.strip():
            continue
        ct, notes = _classify_pair(a, b)
        rows.append({"original": a[:500], "corrected": b[:500],
                     "correction_type": ct, "notes": notes})
        if len(rows) >= 5:
            break
    return rows[:5]


# ---------------------------------------------------------------------------
# Append (corrections log + draft snapshots) — workspace-side only
# ---------------------------------------------------------------------------

def _fingerprint(skill: str, original: str, corrected: str) -> str:
    return hashlib.sha256(f"{skill}\x00{original}\x00{corrected}".encode("utf-8")).hexdigest()


#: Two snapshots of the same words this close together are one draft retried,
#: whatever ids they carry.
DRAFT_RETRY_WINDOW = timedelta(minutes=10)


def _same_draft(a: dict, b: dict) -> bool:
    """Whether two draft snapshots are one draft. A click retried mints a new
    event seq, so the seq cannot say this: the draft's own id can, and with no
    id on either side the words it was addressed and written with can.

    A retry can also come back with a new draft id, so different ids only mean
    different drafts outside `DRAFT_RETRY_WINDOW`. The window reads when the
    drafts were MADE. A row's own timestamp cannot: reconcile writes a whole
    batch of sent mail in one go, and three drafts from three days would read
    as one retry."""
    same_words = all(a.get(k) == b.get(k)
                     for k in ("skill", "recipient_email", "subject", "body"))
    id_a, id_b = a.get("native_draft_id"), b.get("native_draft_id")
    if not (id_a and id_b):
        return same_words
    if id_a == id_b:
        return True
    made_a, made_b = _parse_ts(a.get("ts")), _parse_ts(b.get("ts"))
    return bool(same_words and made_a and made_b
                and abs(made_a - made_b) <= DRAFT_RETRY_WINDOW)


def _first_of_draft(workspace_root, seq) -> tuple:
    """`(key, drafted_at)` for the draft `seq` was logged against: the seq of
    that draft's FIRST snapshot, which every retry and the sent-mail read all
    resolve to, and when it was made. With no snapshot on file the seq stands
    alone and the date is unknown."""
    snaps = [s for s in _load_snapshots(workspace_root)
             if s.get("draft_event_seq") is not None]
    mine = next((s for s in snaps if s.get("draft_event_seq") == seq), None)
    if mine is None:
        return seq, None
    first = next(s for s in snaps if _same_draft(s, mine))
    return first["draft_event_seq"], _parse_ts(first.get("ts"))


def _could_be_from_draft(row: dict, key, drafted_at, doc_notes=None) -> bool:
    """Whether a stored row with the same words can be this draft's. A row
    with no draft key was stored before they were written down: it can only
    have come from a draft that already existed when it was written, so a
    draft made after it is a new correction. Anything unknown keeps the old
    dedupe, because counting one edit twice is the worse mistake. For a
    document the key-less row's notes name the file it came from, so they
    answer exactly (`doc_notes`)."""
    if key is None:
        return True
    stored = row.get("draft_ref")
    if stored is not None:
        return stored == key
    if doc_notes is not None:
        return row.get("notes") == doc_notes
    written = _parse_ts(row.get("timestamp"))
    return drafted_at is None or written is None or drafted_at <= written


def append_correction(
    workspace_root, *, skill: str, domain: str, recipient_id: Optional[str],
    original: str, corrected: str, correction_type: str, notes: str = "",
    draft_event_seq=None, draft_ref: Optional[str] = None,
) -> bool:
    """Append one correction row (the VOICE_CALIBRATION row format) to
    `_hq/voice/corrections-<skill>.jsonl`. Deduped against the log tail by
    (skill, original, corrected) and, when the caller names one, the draft.
    Returns True if written, False if a duplicate. Never raises.

    cr1#778: THE DRAFT IS PART OF WHAT MAKES A ROW A REPEAT. The dedupe exists
    so one edit read twice (reconcile over the same sent mail, a retried click)
    counts once. Without the draft in the key it also collapsed the same phrase
    deleted from three different drafts into one row, so what the owner kept
    deleting could never show as repeated. A retry mints a new seq, so the key
    is the seq of the draft's first snapshot (`_first_of_draft`), not the seq
    passed in. A caller whose source is not a draft passes its own stable
    `draft_ref` instead (the deliverable sweep names the document). A caller
    with neither (a said correction) dedupes exactly as it always did. The
    optional `draft_ref` key is written only when there is a source. It is not
    named `draft_event_seq` on
    purpose: cleanup's step 3g prunes a snapshot once a correction row carries
    that key, and a pruned snapshot would lose a later edit in the mailbox.

    EVGUARD (Sub-bug #14b, second half) — the dedupe scan below skips non-dict
    rows as well as unparseable ones. A top-level bare-string line (`"seq"`)
    PARSES, so it used to reach `row.get(...)`, raise AttributeError, and get
    swallowed by this function's outer `except Exception: return False`. The
    caller read that False as "duplicate", so ONE junk line in the tail-500 of
    `corrections-<skill>.jsonl` silently killed every future correction append
    for that skill — the voice-calibration loop died with no symptom anywhere.
    The outer never-raises contract is intact and deliberate; what was wrong is
    that a guard-less loop let a data problem reach it."""
    try:
        from atomic_write import atomic_append_jsonl
        path = _voice_dir(workspace_root) / f"corrections-{skill}.jsonl"
        fp = _fingerprint(skill, original, corrected)
        key, drafted_at = draft_ref, None
        doc_notes = notes if draft_ref is not None else None
        if key is None and draft_event_seq is not None:
            key, drafted_at = _first_of_draft(workspace_root, draft_event_seq)
        if path.exists():
            # LEARNFIX1 fix round 1 (REVIEW_LEARNFIX1 F-2) — A LESSON YOU TOOK
            # BACK AND SAID AGAIN IS LEARNED AGAIN. This scan used to compare
            # against the raw tail and return False on any earlier row with
            # the same fingerprint, retraction or no retraction. So: say "this
            # is way too long", `undo` it, say it again -> the writer called
            # it a duplicate, wrote nothing, and the receipt still promised
            # "Shorter from now on." The customer was ignored the second time,
            # silently, and `load_corrections`' own comment claimed the
            # opposite.
            #
            # The pass now reads FORWARDS, exactly as `load_corrections` does:
            # a correction row turns its fingerprint ON, a retraction of that
            # fingerprint turns it OFF, and only a fingerprint still ON at the
            # end of the tail is a duplicate. Same single read, same loop.
            active = False
            for line in path.read_text(encoding="utf-8").splitlines()[-500:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if not isinstance(row, dict):
                    continue
                retracts = row.get(RETRACTS_KEY)
                if isinstance(retracts, str) and retracts:
                    if retracts == fp:
                        active = False
                    continue
                if (_fingerprint(skill, row.get("original_draft", ""), row.get("corrected_by_user", "")) == fp
                        and _could_be_from_draft(row, key, drafted_at, doc_notes)):
                    active = True
            if active:
                return False
        row = {
            "timestamp": _now_iso(),
            "skill": skill,
            "domain": domain,
            "recipient_id": recipient_id,
            "original_draft": original,
            "corrected_by_user": corrected,
            "correction_type": correction_type,
            "notes": notes,
        }
        if key is not None:
            row["draft_ref"] = key
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_append_jsonl(path, [row])
        return True
    except Exception:
        return False


#: LEARNFIX1 1.1 (M's ruling R-20) — the key that marks a row as a RETRACTION
#: of an earlier row rather than a correction of its own. A retraction is an
#: APPEND. Nothing is ever removed from a `corrections-*.jsonl`: on 2026-09-16
#: a chat copied the file into the substrate's backups folder and rewrote it
#: without the line, a hand edit of an append-only record and HOLD-class.
#: `run_guard_corrections_append_only_test` is what makes that impossible to
#: repeat; this is the sanctioned way to take a lesson back.
RETRACTS_KEY = "retracts"


def retract_correction(
    workspace_root, *, skill: str, fingerprint: str, reason: str = "",
    undone_by: str = "",
) -> bool:
    """Take back ONE correction by APPENDING a retraction row. Returns True
    when a row was written, False when the fingerprint is unknown to this
    log or has already been retracted (so an `undo` of an `undo` is a
    no-op rather than a second row). Never raises.

    THE CONTRACT, stated where the writer is: this function does not open the
    log for writing, does not rewrite it, does not back it up and does not
    delete from it. It appends exactly one line through
    `atomic_write.atomic_append_jsonl`, the same door `append_correction`
    uses, and the row it retracts stays on disk byte-for-byte. `load_
    corrections` is the reader that stops serving it — see the skip there."""
    try:
        from atomic_write import atomic_append_jsonl
        fp = str(fingerprint or "").strip()
        if not skill or not fp:
            return False
        path = _voice_dir(workspace_root) / f"corrections-{skill}.jsonl"
        if not path.exists():
            return False
        known = False
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            if row.get(RETRACTS_KEY) == fp:
                return False  # already taken back; a second row says nothing
            if _fingerprint(skill, row.get("original_draft", ""),
                            row.get("corrected_by_user", "")) == fp:
                known = True
        if not known:
            return False
        atomic_append_jsonl(path, [{
            "timestamp": _now_iso(),
            "skill": skill,
            RETRACTS_KEY: fp,
            "retracted_at": _now_iso(),
            "reason": str(reason or ""),
            "undone_by": str(undone_by or ""),
        }])
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# LEARNFIX1 1.2 — ONE entry for a correction someone says in passing
# ---------------------------------------------------------------------------

#: The change class the in-passing correction rides. Registered in
#: `brain_undo.REVERSERS` in the same commit (the step-10 mandate): a class
#: that can apply without a reverser is a receipt that promises an `undo`
#: nothing can perform, which is exactly what produced the three-way menu and
#: then the hand cut on 2026-09-16.
IN_PASSING_CHANGE_CLASS = "voice_correction"
#: The batch prefix, so a batch listing can tell where the act came from
#: without reading the class name out loud.
IN_PASSING_BATCH_PREFIX = "vcp"
#: The receipt. ONE sentence, no question mark, no options — the shape
#: `correction_turn` already returns for a persona step, now guaranteed to
#: carry a batch behind it whatever the lane.
IN_PASSING_RECEIPT = "Shorter from now on. Say `undo` to put it back."
#: The receipt for everything else. ONE sentence, same shape, same undo —
#: and it claims only what this function actually does, which is to put the
#: correction on file with a way back. It does not say "I have changed it":
#: a caller whose kind is not in the table below may have changed nothing
#: yet, and a receipt that overstates is the defect one door along.
IN_PASSING_RECEIPT_DEFAULT = ("Noted, and it is on file. Say `undo` to take "
                              "it back.")
#: LEARNFIX1 fix round 2 (REVIEW_LEARNFIX1 N-2) — THE SENTENCE FOR A TURN
#: THAT WROTE NOTHING. `log_in_passing` returns early when the writer says
#: the lesson is already on file — the person said the same thing twice, or
#: said it again without having taken it back — and until now it handed that
#: turn the same receipt as a real capture: "Say `undo` to put it back."
#: Nothing had been written, no batch was minted, and there was nothing for
#: `undo` to find. Every lane that obeys `shared/EVENT_TYPES.md` and prints
#: what this function returns printed that promise. So the no-write path gets
#: its OWN sentence, here, once: it claims nothing, and it offers no way back
#: to a place nothing left. It must never contain the word undo.
IN_PASSING_NOOP_RECEIPT = "Already on file — nothing changed."
#: LEARNFIX1 fix round 3 — THE SENTENCE FOR A TURN THAT COULD NOT WRITE.
#: `log_in_passing` has a SECOND no-write exit: the `except` right after the
#: capture call. It returned the caller's receipt too — "Say `undo` to put it
#: back" — over a turn that put nothing on file and minted no batch, so the
#: same promise-over-nothing defect lived on the error path after fix round 2
#: closed it on the duplicate path. It gets its own constant because the
#: duplicate sentence would be a LIE here: a capture that threw does not know
#: whether the lesson is on file. This sentence claims nothing, offers no way
#: back to a place nothing left, and — M's rule — does not tell the person
#: what to say next: the product never asks and never hands out instructions.
#: It must never contain the word undo, a question mark, or an imperative.
IN_PASSING_FAILED_RECEIPT = "I could not put that on file just now."
#: LEARNFIX1 fix round 1 (REVIEW_LEARNFIX1 F-6) — ONE TABLE, ONE SENTENCE
#: EACH. `log_in_passing` is advertised in `shared/EVENT_TYPES.md` as the one
#: entry every in-passing correction lane calls, and it returned "Shorter
#: from now on" whatever the person had corrected — so the next lane to call
#: it about a routing complaint or a setting would have printed a sentence
#: about length. `correction_turn` only escaped that by overriding the
#: receipt per target. The mapping lives here, in code, and never in prose in
#: a skill file: a sentence a model composes for itself is the defect this
#: whole lane exists to remove.
#: The keys are the correction kinds this tree actually uses — the in-passing
#: targets `correction_turn` classifies (`persona`, `settings`) and the four
#: the passive rail classifies a rewrite into (`_classify_pair`: `structure`,
#: `tone`, `vocabulary`, `phrasing`). Nothing invented; a kind nobody writes
#: yet takes the default rather than getting a sentence written for it here.
IN_PASSING_RECEIPTS = {
    "persona": IN_PASSING_RECEIPT,
    "settings": "Changed, and it will stay that way. Say `undo` to put it "
                "back.",
    "tone": "That tone from now on. Say `undo` to put it back.",
    "structure": "I will lay it out your way from now on. Say `undo` to put "
                 "it back.",
    "vocabulary": "Your words from now on. Say `undo` to put it back.",
    "phrasing": "I will word it your way from now on. Say `undo` to put it "
                "back.",
}


def in_passing_receipt(correction_type: str = "persona") -> str:
    """The ONE sentence for this kind of correction. Never a question, never
    a menu, always the same `undo`."""
    return IN_PASSING_RECEIPTS.get(
        str(correction_type or "").strip().lower(), IN_PASSING_RECEIPT_DEFAULT)


def _mint_in_passing_batch() -> str:
    import datetime as _dt
    return (IN_PASSING_BATCH_PREFIX + "-"
            + _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f"))


def log_in_passing(
    workspace_root, *, skill: str, said: str, recipient_id: Optional[str] = None,
    domain: str = "", correction_type: str = "persona", original: str = "",
    corrected: str = "", notes: str = "", receipt: str = "",
    batch_id: Optional[str] = None,
) -> dict:
    """Record a correction the person said WHILE asking for something else,
    and leave an `undo` behind. Returns
    `{recorded, receipt, batch_id, fingerprint, event_seq}`. Never raises.

    THE DEFECT THIS CLOSES (record B6.3). "Too long", said about a draft, was
    handled by the composing skill's own lane: it wrote a correction row, made
    up its own sentence, put NOTHING on the ledger, and left `undo` with
    nothing registered. The chat then offered a three-way menu and cut the
    append-only log by hand. Three separate failures, one cause — a lane that
    records without minting a batch.

    So this is the one entry, and it does three things in one act: it appends
    the correction row through `append_correction` (the same store the passive
    rail writes, `origin: asked`), it appends ONE ledger event carrying
    `brain_batch_id` + `brain_change_class` so `brain_undo` can find it, and
    it hands back the sentence to print. The caller prints what it returns and
    composes nothing of its own.

    Pass `batch_id` when the same turn is already minting one (the persona
    step does): both writes then ride ONE batch and one `undo` reverses the
    whole gesture, which is what "one receipt, one batch" means."""
    out = {"recorded": False,
           "receipt": receipt or in_passing_receipt(correction_type),
           "batch_id": None, "fingerprint": "", "event_seq": None}
    try:
        text = original or said
        out["fingerprint"] = _fingerprint(skill, text, corrected)
        out["recorded"] = append_correction(
            workspace_root, skill=skill, domain=domain or correction_type,
            recipient_id=recipient_id, original=text, corrected=corrected,
            correction_type=correction_type,
            notes=notes or ("origin: asked — " + str(said or "")[:200]))
    except Exception:
        # LEARNFIX1 fix round 3 — THE OTHER NO-WRITE EXIT. `recorded` is
        # still False and `batch_id` still None (nothing below this line has
        # run, so the ledger is untouched), but the RECEIPT was whatever the
        # caller passed in or the sentence for its kind — every one of which
        # ends "Say `undo` to put it back". A turn that threw before the
        # capture returned has nothing to put back. One constant, and the
        # caller's own `receipt=` is overridden here for the same reason it
        # is on the duplicate path: a caller cannot know, before the call,
        # that the capture would fail. No undo, no offer, no instruction.
        out["receipt"] = IN_PASSING_FAILED_RECEIPT
        return out
    if not out["recorded"]:
        # LEARNFIX1 fix round 1 (REVIEW_LEARNFIX1 F-3) — NOTHING WRITTEN,
        # NOTHING TO UNDO. This used to mint a batch and append a ledger row
        # whatever the writer returned, so saying "too long" twice in a row
        # left ONE correction on disk and TWO reversible acts on every surface
        # that lists batches — and `undo` on the second one retracted the
        # FIRST one's row. That is a receipt promising an act that did not
        # happen, which is the exact defect this lane exists to remove, one
        # level down. `batch_id` stays None.
        #
        # FIX ROUND 2 (REVIEW_LEARNFIX1 N-2) — and the SENTENCE tells the
        # truth too. The batch was the half of the promise the product could
        # see; the receipt is the half the person reads, and it still said
        # "Say `undo` to put it back" over a turn that wrote nothing. An
        # explicit `receipt=` from the caller is overridden here on purpose:
        # a caller cannot know, before the call, whether the writer would
        # find the lesson already on file, so the honest sentence is this
        # function's to give. A caller that means to say something else says
        # it AFTER reading `recorded`, as `correction_turn` does.
        out["receipt"] = IN_PASSING_NOOP_RECEIPT
        return out
    try:
        from event_gate import append_event
        events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
        bid = batch_id or _mint_in_passing_batch()
        written = append_event(events_path, {
            "type": "voice_correction_logged",
            "source_skill": skill,
            "data": {
                "skill": skill,
                "domain": domain or correction_type,
                "recipient_id": recipient_id,
                # The person's own words, so a narration has something true
                # to say. Never a store name, never a path.
                "said": str(said or "")[:200],
                "correction_type": correction_type,
                "correction_fingerprint": out["fingerprint"],
                "brain_batch_id": bid,
                "brain_change_class": IN_PASSING_CHANGE_CLASS,
            },
        }, holder="voice_corrections")
        out["batch_id"] = bid
        if written and isinstance(written[0], dict):
            out["event_seq"] = written[0].get("seq")
    except Exception:
        # The row is on file either way; a ledger seam must never take the
        # capture down with it. `batch_id` stays None, and the caller's
        # receipt is the honest one — see `correction_turn`.
        pass
    return out


def snapshot_draft(
    workspace_root, *, skill: str, domain: str, recipient_id: Optional[str],
    recipient_email: Optional[str], subject: str, body: str, draft_event_seq,
    native_draft_id=None, native_message_id=None,
    gmail_message_id=None, gmail_draft_id=None,
    voice_block_sha256: Optional[str] = None,
) -> None:
    """Append the drafted body to `_hq/voice/draft-snapshots.jsonl` so a
    drafted-vs-sent diff is possible later. Bodies stay OUT of events.jsonl.

    FB-plumbing item 5 — the draft id column stores under `native_draft_id`
    (the value was never Gmail-specific). The legacy `gmail_draft_id` kwarg is
    still accepted so existing callers keep working, and it feeds the new
    column when the new kwarg is absent.

    MAILSEAM item 5 finishes that rename on the OTHER id: the sent-message id
    stores under `native_message_id` too. It was never Gmail-specific either —
    on a Superhuman or Outlook workspace the old column name described the
    value wrongly while the matching still worked, which is the quiet kind of
    wrong. Both kwargs and both columns are honored: `gmail_message_id` keeps
    working for callers, and it is still WRITTEN so snapshot rows stay readable
    by any older copy of this module (an append-only log is never rewritten)."""
    try:
        from atomic_write import atomic_append_jsonl
        path = _voice_dir(workspace_root) / "draft-snapshots.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_append_jsonl(path, [{
            "ts": _now_iso(), "skill": skill, "domain": domain,
            "recipient_id": recipient_id, "recipient_email": recipient_email,
            "draft_event_seq": draft_event_seq,
            "native_draft_id": native_draft_id or gmail_draft_id,
            "native_message_id": native_message_id or gmail_message_id,
            "gmail_message_id": native_message_id or gmail_message_id,
            "subject": subject, "body": body,
            # VOICELOAD1 (cr1#197): the block the composer read, or None
            "voice_block_sha256": voice_block_sha256,
        }])
    except Exception:
        pass


#: cr1#586: the one sentence a Draft click says when its book record was
#: not written. The mail draft is already made by then, so it says so.
DRAFT_NOT_RECORDED_LINE = ("The draft is in your mailbox, but it was not "
                           "recorded in your Command Room notes.")


def record_draft_click(
    workspace_root, *, recipient: str, subject: str, body: str,
    native_draft_id: Optional[str] = None, provider: Optional[str] = None,
    server_id: Optional[str] = None, address: Optional[str] = None,
    topic: str = "", recipient_id: Optional[str] = None, domain: str = "",
    edited_body: str = "", voice_block_sha256: Optional[str] = None,
    primary_thread_id: Optional[str] = None, source_skill: str = "email-writer",
    extra: Optional[dict] = None, snapshot: bool = True,
) -> dict:
    """A WRITER (on `RUN_WRITER_ALLOWLIST` only, cr1#586): a Draft click's
    book record, the writes apply-choices Step 4B and Step 3d already ask for,
    in their order, in one call a merged seat can make through the write door.

    1. the `email_drafted` row: `recipient`, `topic` and the identity and voice
       fields from `connector_adapters.provenance.build_email_drafted_provenance`
       (never hand-written), with `person_ids` and `primary_thread_id` on the
       envelope as email-writer's event shape has them;
    2. the draft snapshot of the queued `body`, keyed to that row's seq;
    3. when `edited_body` differs from `body` (an inline edit on the card),
       its corrections, keyed to the same seq;
    4. the sentence the ack owes, `draft_constraints.voice_receipt(...)["line"]`.

    `extra` rides additive keys on the row (cr1#817: the Hub's choice id).
    `snapshot=False` skips step 2, for text the owner's voice never wrote
    (cr1#219: a chase), so a later edit to it teaches nothing.
    Answers `{ok, event_seq, snapshot, corrections, voice_line}`. With no
    recipient, or when the row cannot be appended, nothing is written and it
    answers `{ok: false, refused, line}` with `DRAFT_NOT_RECORDED_LINE`. The
    snapshot, the corrections and the voice line never take the row down
    with them: each is best-effort, as Step 3d says."""
    recipient = str(recipient or "").strip()
    if not recipient:
        return {"ok": False, "refused": "no_recipient", "line": DRAFT_NOT_RECORDED_LINE}
    try:
        from connector_adapters.provenance import build_email_drafted_provenance
        from event_gate import append_event

        frag = build_email_drafted_provenance(
            draft_id=native_draft_id, provider=provider, server_id=server_id,
            address=address, workspace_root=workspace_root,
            voice_block_sha256=voice_block_sha256, skill=source_skill)
        data = {"recipient": recipient, "topic": str(topic or subject or "")}
        data.update(frag)
        # cr1#817: a record made for a press on the Hub names the page's
        # choice id, so a later run finds it and never records it twice. The
        # builder's own fields are never overridden.
        for key, value in (extra or {}).items():
            data.setdefault(str(key), value)
        event = {"type": "email_drafted", "source_skill": source_skill, "data": data}
        # The envelope keys email-writer's event shape carries: readers
        # (dormancy, balance) count the draft as a touch through them.
        if primary_thread_id:
            event["primary_thread_id"] = primary_thread_id
        if recipient_id:
            event["person_ids"] = [recipient_id]
        events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
        written = append_event(events_path, event, holder="voice_corrections")
        seq = written[0].get("seq") if written and isinstance(written[0], dict) else None
    except Exception:  # noqa: BLE001 - nothing landed; say so, never raise
        return {"ok": False, "refused": "not_appended", "line": DRAFT_NOT_RECORDED_LINE}
    if seq is None:
        return {"ok": False, "refused": "not_appended", "line": DRAFT_NOT_RECORDED_LINE}

    snaps = _voice_dir(workspace_root) / "draft-snapshots.jsonl"
    had = _count_lines(snaps)
    if snapshot:
        snapshot_draft(workspace_root, skill=source_skill, domain=domain,
                       recipient_id=recipient_id, recipient_email=recipient,
                       subject=subject, body=body, draft_event_seq=seq,
                       native_draft_id=native_draft_id,
                       voice_block_sha256=voice_block_sha256)
    snapshot = _count_lines(snaps) > had

    n = 0
    if edited_body and edited_body.strip() != (body or "").strip():
        try:
            for r in diff_and_classify(body, edited_body):
                if append_correction(workspace_root, skill=source_skill, domain=domain,
                                     recipient_id=recipient_id, original=r["original"],
                                     corrected=r["corrected"],
                                     correction_type=r["correction_type"],
                                     notes=r["notes"], draft_event_seq=seq):
                    n += 1
        except Exception:  # noqa: BLE001 - voice capture never blocks the click
            pass

    line = ""
    try:
        import draft_constraints as dc
        line = str(dc.voice_receipt(workspace_root, source_skill,
                                    voice_block_sha256).get("line") or "")
    except Exception:  # noqa: BLE001
        line = ""
    return {"ok": True, "event_seq": seq, "snapshot": snapshot,
            "corrections": n, "voice_line": line}


def _count_lines(path: Path) -> int:
    try:
        return sum(1 for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# Async detection at Sent reconcile
# ---------------------------------------------------------------------------

def _norm_subject(s: str) -> str:
    s = (s or "").strip().lower()
    while True:
        m = re.match(r"^(re|fwd|fw)\s*:\s*", s)
        if not m:
            break
        s = s[m.end():]
    return s.strip()


def _load_snapshots(workspace_root) -> List[dict]:
    """Draft snapshots, non-dict rows dropped.

    EVGUARD sibling-rail (joined by the Slot 9 sweep) — this loader ADMITTED a
    bare-string row, and `_match_snapshot` then called `.get()` on it. The
    AttributeError was swallowed by `reconcile_sent_against_snapshots`'s
    never-raises wrapper, which returned `n_matched: 0`: one junk line in
    draft-snapshots.jsonl silently switched the whole drafted-vs-sent
    correction detector off. Same silent-drop class as `append_correction`."""
    path = _voice_dir(workspace_root) / "draft-snapshots.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        out.append(row)
    return out


def reconcile_sent_against_snapshots(workspace_root, sent_messages: List[dict]) -> dict:
    """Match each sent message to a draft snapshot, classify the drafted-vs-sent
    diff, append corrections. Match: exact native message id (either snapshot
    column spelling), else recipient +
    normalized subject + sent ts within 7 days of the snapshot. Returns
    `{n_matched, n_corrections, skills: {...}}`. Tolerant of a missing snapshot
    file; never raises."""
    result = {"n_matched": 0, "n_corrections": 0, "skills": {}}
    try:
        snaps = _load_snapshots(workspace_root)
        if not snaps:
            return result
        for sent in sent_messages or []:
            snap = _match_snapshot(sent, snaps)
            if snap is None:
                continue
            result["n_matched"] += 1
            rows = diff_and_classify(snap.get("body", ""), sent.get("body", ""))
            for r in rows:
                wrote = append_correction(
                    workspace_root, skill=snap.get("skill", "email-writer"),
                    domain=snap.get("domain", ""), recipient_id=snap.get("recipient_id"),
                    original=r["original"], corrected=r["corrected"],
                    correction_type=r["correction_type"], notes=r["notes"],
                    draft_event_seq=snap.get("draft_event_seq"),
                )
                if wrote:
                    result["n_corrections"] += 1
                    sk = snap.get("skill", "email-writer")
                    result["skills"][sk] = result["skills"].get(sk, 0) + 1
    except Exception:
        return result
    return result


def _match_snapshot(sent: dict, snaps: List[dict]) -> Optional[dict]:
    mid = sent.get("message_id")
    if mid:
        for s in snaps:
            # Both column spellings — rows written before the rename carry only
            # the legacy one, and a snapshot log is append-only.
            snap_mid = s.get("native_message_id") or s.get("gmail_message_id")
            if snap_mid and snap_mid == mid:
                return s
    subj = _norm_subject(sent.get("subject", ""))
    sent_dt = _parse_ts(event_time(sent))
    recips = set(sent.get("recipient_person_ids") or [])
    matches = []
    for s in snaps:
        if _norm_subject(s.get("subject", "")) != subj or not subj:
            continue
        if s.get("recipient_id") and recips and s.get("recipient_id") not in recips:
            continue
        s_dt = _parse_ts(s.get("ts"))
        if sent_dt is not None and s_dt is not None:
            from datetime import timedelta
            if abs((sent_dt - s_dt).total_seconds()) > 7 * 86400:
                continue
        matches.append(s)
    # Ambiguity → skip (a wrong correction is worse than none).
    return matches[0] if len(matches) == 1 else None


# ---------------------------------------------------------------------------
# Batching reads (the `learning` job's voice leg)
# ---------------------------------------------------------------------------

def load_corrections(workspace_root, skill: Optional[str] = None) -> List[dict]:
    """Every correction row across the corrections logs (the `learning`
    job's voice-leg batching read).

    EVGUARD (Sub-bug #14b, second half) — non-dict rows are skipped alongside
    unparseable ones. A top-level bare-string line PARSES, so it used to reach
    `row.setdefault(...)` and raise AttributeError straight out of this
    function, taking the whole batching read with it.

    LEARNFIX1 1.1 — THIS IS THE RETRACTION CHOKEPOINT. A row a LATER row
    retracts (`retract_correction`) stops being served here, and the
    retraction rows themselves are never served at all. Both lines stay on
    disk: taking a lesson back is an append, and the history of what was
    said and then taken back is the record's, not the reader's, to lose.
    Every reader of the corrections corpus comes through this function for
    exactly that reason — a second glob is a second answer to "what have
    they told me", and the one that skipped the skip would go on acting on
    a lesson the customer already undid."""
    vd = _voice_dir(workspace_root)
    if not vd.exists():
        return []
    rows: List[dict] = []
    pattern = f"corrections-{skill}.jsonl" if skill else "corrections-*.jsonl"
    for path in sorted(vd.glob(pattern)):
        sk = path.stem[len("corrections-"):]
        per_file: List[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            retracts = row.get(RETRACTS_KEY)
            if isinstance(retracts, str) and retracts:
                # In order, so a correction re-said AFTER its retraction is
                # served again — "a later row retracts" reads forwards. The
                # WRITER reads forwards the same way since fix round 1
                # (F-2): before that, this comment was true here and false at
                # the door, because `append_correction` compared against the
                # raw tail and refused the re-said lesson as a duplicate.
                per_file = [
                    r for r in per_file
                    if _fingerprint(sk, r.get("original_draft", ""),
                                    r.get("corrected_by_user", "")) != retracts
                ]
                continue
            row.setdefault("skill", sk)
            per_file.append(row)
        rows.extend(per_file)
    return rows


def unreviewed_counts(workspace_root) -> Dict[str, int]:
    """Per-skill count of corrections with `timestamp` after the last
    `voice_calibration_review` event's `reviewed_through[skill]` (staleness rule 2)."""
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    reviewed_through: Dict[str, str] = {}
    events, _ = load_events_defensively(events_path)
    for ev in events:
        if ev.get("type") == "voice_calibration_review":
            rt = (ev.get("data") or {}).get("reviewed_through") or {}
            if isinstance(rt, dict):
                reviewed_through = rt  # append-ordered → last wins
    counts: Dict[str, int] = {}
    for row in load_corrections(workspace_root):
        # fix-round review (LC 2): a thing the person SAID in passing is not
        # an edit; three "too long"s must not read "3 of your edits" on the
        # profile page. Same two markers classify_correction_op refuses on.
        if classify_correction_op(normalize_correction_row(row)) == (None, None) \
                and _is_asked_row(row):
            continue
        sk = row.get("skill", "")
        cutoff = reviewed_through.get(sk)
        ts = row.get("timestamp")
        if cutoff is None or (ts is not None and str(ts) > str(cutoff)):
            counts[sk] = counts.get(sk, 0) + 1
    return counts


def group_correction_patterns(rows: List[dict]) -> Dict[tuple, List[dict]]:
    """Group by (skill, correction_type, normalized original) — the 3+-same-pattern
    threshold the monthly pass keys on."""
    groups: Dict[tuple, List[dict]] = {}
    for r in rows:
        norm = re.sub(r"\s+", " ", (r.get("original_draft") or "").lower()).strip()[:80]
        key = (r.get("skill", ""), r.get("correction_type", ""), norm)
        groups.setdefault(key, []).append(r)
    return groups


# ---------------------------------------------------------------------------
# Customer-side voice-block override store
# ---------------------------------------------------------------------------

def _override_path(workspace_root, skill: str) -> Path:
    return _voice_dir(workspace_root) / f"voice-block-{skill}.md"


# Machine-readable reads of the calibrated block (B2 gate wiring). The block
# follows VOICE_CALIBRATION.md's template; both parsers are tolerant of a
# missing section (→ the safe default: nothing allowed, dashes banned).

# The Taboos section's carve-out bullet ("OK despite being on universal
# list: ...") — the ONE sanctioned source of allow_phrases for the voice-tell
# gate. Phrases here are demonstrably the client's own voice.
_TABOOS_ALLOW_RE = re.compile(
    r"^\s*[-*]?\s*OK despite being on universal list:\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_QUOTED_PHRASE_RE = re.compile(r'["“]([^"”]+)["”]')
# Punctuation section's em-dash frequency line at its strongest value.
_EM_DASH_FREQUENT_RE = re.compile(
    r"^\s*[-*]?\s*Em-dashes:\s*frequent\b", re.IGNORECASE | re.MULTILINE
)
# Whole-word only: a carve-out phrase that merely CONTAINS "dash" (e.g.
# "dashboard") is not evidence the client's voice keeps dash punctuation.
_DASH_MENTION_RE = re.compile(r"\bdash(es)?\b|—|–", re.IGNORECASE)


def parse_taboos_allow(markdown: str) -> List[str]:
    """Parse the Taboos carve-out bullet into a phrase list for the voice-tell
    gate's `allow_phrases`. Quoted phrases win when present (commas inside a
    quoted phrase survive); otherwise the remainder is comma/semicolon-split
    with parenthetical justifications dropped. The uncalibrated template
    placeholder (`[list with justification]`) and none-ish values parse to []."""
    m = _TABOOS_ALLOW_RE.search(markdown or "")
    if not m:
        return []
    raw = m.group(1).strip()
    if not raw or raw.startswith("[") or raw.rstrip(".").lower() in {"none", "n/a", "-", "—"}:
        return []
    quoted = [q.strip() for q in _QUOTED_PHRASE_RE.findall(raw) if q.strip()]
    if quoted:
        return quoted
    raw = re.sub(r"\([^)]*\)", "", raw)
    return [p.strip(" .;") for p in re.split(r"[,;]", raw) if p.strip(" .;")]


def parse_ban_dashes(markdown: str, taboos_allow: Optional[List[str]] = None) -> bool:
    """FB-16 per-client read: dashes-as-punctuation stay BANNED (True) unless
    the calibrated block is explicit that this client's voice keeps them —
    Punctuation says `Em-dashes: frequent`, or a Taboos carve-out entry names
    dashes. `rare` / `occasional` is not evidence; the product ban stays on."""
    if _EM_DASH_FREQUENT_RE.search(markdown or ""):
        return False
    allow = parse_taboos_allow(markdown) if taboos_allow is None else taboos_allow
    return not any(_DASH_MENTION_RE.search(p) for p in allow)


def load_voice_block_override(workspace_root, skill: str) -> Optional[dict]:
    """Return `{markdown, last_refreshed, calibration_level, sample_count,
    taboos_allow, ban_dashes}` for the workspace override, or None if absent.
    `taboos_allow` / `ban_dashes` are the parsed gate-wiring reads above."""
    path = _override_path(workspace_root, skill)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    def _hdr(label):
        m = re.search(rf"^{re.escape(label)}:\s*(.+)$", text, re.MULTILINE)
        return m.group(1).strip() if m else None
    taboos_allow = parse_taboos_allow(text)
    return {
        "markdown": text,
        "last_refreshed": _hdr("Last refreshed"),
        "calibration_level": _hdr("Calibration level"),
        "sample_count": _hdr("Sample count"),
        "taboos_allow": taboos_allow,
        "ban_dashes": parse_ban_dashes(text, taboos_allow),
    }


def written_overrides(workspace_root) -> List[str]:
    """The skills that have a voice-block override ON DISK, sorted.

    LEARNFIX1 1.4 — this is the learning job's OWN ARTIFACT. `run_voice_leg`
    writes `voice-block-<skill>.md` for every change it applies, and it writes
    a `voice_calibration_review` marker for the same skills in the same fire.
    The profile page used to iterate the marker's leftovers
    (`unreviewed_counts`), which are zero the instant a change lands — so four
    hours after four changes the page said "nothing yet". A page that reports
    what was learned reads what the learner wrote."""
    vd = _voice_dir(workspace_root)
    if not vd.exists():
        return []
    prefix, suffix = "voice-block-", ".md"
    return sorted(p.name[len(prefix):-len(suffix)]
                  for p in vd.glob(f"{prefix}*{suffix}")
                  if len(p.name) > len(prefix) + len(suffix))


def write_voice_block_override(
    workspace_root, skill: str, block_markdown: str, *,
    calibration_level: str = "calibrated", sample_count: int = 0,
) -> Path:
    """Atomically write `_hq/voice/voice-block-<skill>.md` with a 3-line header,
    bumping `Last refreshed:`. Never touches the plugin directory."""
    from atomic_write import atomic_write_text
    path = _override_path(workspace_root, skill)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"Last refreshed: {_now_iso()}\n"
        f"Calibration level: {calibration_level}\n"
        f"Sample count: {sample_count}\n\n"
    )
    body = block_markdown if block_markdown.lstrip().startswith("## Voice Block") else f"## Voice Block\n\n{block_markdown}"
    # LEARN1 A3 — `atomic_write.atomic_write_text(path, content, encoding,
    # create_parents)` has NO `holder` parameter. The old call passed
    # `holder="insight-generator"`, raised TypeError on every single write,
    # and fell through to `path.write_text` — so the override the whole
    # calibration rail depends on was never once written atomically. The
    # fallback stays (this must never raise into a send path) but it is now
    # the fallback for a real I/O failure rather than the only path taken.
    try:
        atomic_write_text(path, header + body)
    except Exception:
        path.write_text(header + body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# LEARN1 — turning the corrections corpus into a voice-block delta
#
# The corpus that has been accumulating for months is NOT one shape. Two
# writers have appended to `corrections-<skill>.jsonl`:
#   1. the VOICE_CALIBRATION schema this module's `append_correction` writes
#      (`original_draft` / `corrected_by_user` / `correction_type` / `notes`);
#   2. an audit-written shape (`drafted` / `sent` / `field` / `note` / `ts`).
# `load_corrections` returns both verbatim, so every reader that reached for
# `original_draft` saw an EMPTY string on every row of shape 2 — part of the
# banked corpus was invisible to the pass that was supposed to learn from it.
# `normalize_correction_row` is the fold; nothing downstream reads the raw
# keys.
# ---------------------------------------------------------------------------

# Ops a correction can express. The floor counts one learned ITEM inside an op,
# the phrase or the paragraph count (the maintainer's ruling on cr1#778): one
# phrase deleted three times teaches that phrase, and twelve different phrases
# deleted once each teach nothing. A shorten that named no count is its own
# item. The job remembers the same thing it counts, so a learned item closes
# itself and leaves the kind open.
OP_BAN_PHRASE = "ban_phrase"
OP_SHORTEN = "shorten"
# A dash deletion is NOT a learned taboo: dashes-as-punctuation are banned by
# PRODUCT DEFAULT already (`parse_ban_dashes` returns True unless the block
# carves them out), so promoting one into the block would learn a rule that
# is already on. Classified so the row is consumed and counted, never
# proposed.
OP_DASH_DEFAULT = "dash_default"

# Where each op's learned line lands. These are the LIVE `## Voice Block`
# section names in the shipped SKILL.md files (`### Taboos (per-skill)`,
# `### Structure`); matched by PREFIX so the parenthetical suffix does not
# have to be spelled at every call site.
VOICE_OP_SECTIONS = {
    OP_BAN_PHRASE: "Taboos",
    OP_SHORTEN: "Structure",
}

# cr1#784, rulings C2 and C4 of 2026-11-04. The two numbers count corrections
# of ONE item in one skill, the same phrase or the same paragraph count (the
# ruling on cr1#778), and never the kind. At `VOICE_ASK_THRESHOLD` the owner is
# asked; at `VOICE_THRESHOLD` the rule applies without asking. These are the
# DEFAULTS: both are settings, read through `learning_pass.learning_thresholds`.
VOICE_ASK_THRESHOLD = 2
VOICE_THRESHOLD = 4
# The amendments' worst-case paragraph is the authority on the cap: at most
# three phrases per writing skill per run. It was written when the bar was three
# rewrites; the bar is now the two numbers above, and the cap did not move.
VOICE_CAP = 3

# A12 — the narration noun, per skill. Never a skill name on a customer line.
VOICE_SURFACE_NOUNS = {
    "email-writer": "in your emails",
    "memo-writer": "in your memos",
    "call-prep": "in your prep briefs",
    "follow-up-ritual": "in your follow-ups",
    "deliverables": "in your documents",
    "intro-to-demo": "in your intro emails",
    "intro-broker": "in your intros",
}
VOICE_SURFACE_NOUN_DEFAULT = "in what I write for you"

_DASH_ONLY_RE = re.compile(u"^[\\s\\-‐-―]+$")
_PARA_NOTE_RE = re.compile(r"paragraph count\s*(\d+)\s*->\s*(\d+)", re.IGNORECASE)
_BLOCK_COUNT_RE = re.compile(r"(\d+)\s+blocks\b", re.IGNORECASE)
# A correction that shortened the draft to at most this share of its words is
# a length correction even when the block count did not move.
_SHORTEN_WORD_RATIO = 0.6
_SHORTEN_MIN_WORDS = 8
# A banned phrase is a PHRASE — not a deleted paragraph.
_BAN_MAX_WORDS = 6
_BAN_MAX_CHARS = 60


def voice_surface_noun(skill: str) -> str:
    """A12 — what a change to `skill`'s voice is CALLED on a customer surface."""
    return VOICE_SURFACE_NOUNS.get(skill or "", VOICE_SURFACE_NOUN_DEFAULT)


def normalize_correction_row(row: dict) -> Optional[dict]:
    """Fold either on-disk correction shape into one row. Pure; None-safe.
    Returns `{skill, timestamp, recipient_id, domain, original, corrected,
    correction_type, notes, draft_ref}` or None for a row that is not a dict."""
    if not isinstance(row, dict):
        return None
    alt = ("drafted" in row) or ("sent" in row)
    if alt:
        notes = row.get("note")
        out = {
            "skill": row.get("skill") or "",
            "timestamp": row.get("ts") or row.get("timestamp"),
            "recipient_id": row.get("recipient_id") or row.get("recipient"),
            "domain": row.get("domain") or "",
            "original": row.get("drafted") or "",
            "corrected": row.get("sent") or "",
            "correction_type": row.get("field") or "",
            "draft_ref": row.get("draft_ref"),
        }
    else:
        # The banked corpus holds FIVE on-disk shapes, not two. Three of them
        # (`edits`+`register`, `topic`+list-valued `notes`, and
        # `what_changed`/`rule_learned`/`also`) carry no drafted-vs-sent PAIR
        # at all — they are somebody's prose about an edit, not the edit. They
        # fold here so they are visible and counted rather than reading as
        # empty rows, and they will still classify as nothing, because there
        # is no before-and-after to compare. Visible is not the same as
        # actionable and this rail must not pretend otherwise.
        notes = row.get("notes")
        if not notes:
            notes = [row.get(k) for k in
                     ("what_changed", "rule_learned", "also", "topic",
                      "register")
                     if row.get(k)]
            edits = row.get("edits")
            if isinstance(edits, list):
                notes = notes + [str(e) for e in edits if e]
            elif edits:
                notes = notes + [str(edits)]
            notes = notes or None
        out = {
            "skill": row.get("skill") or "",
            "timestamp": row.get("timestamp") or row.get("ts"),
            # `recipient` is the key three of the five shapes use. Reading
            # only `recipient_id` left those rows unscoped, which matters now
            # that a paragraph ceiling is learned per recipient.
            "recipient_id": row.get("recipient_id") or row.get("recipient"),
            "domain": row.get("domain") or "",
            "original": row.get("original_draft") or "",
            "corrected": row.get("corrected_by_user") or "",
            "correction_type": row.get("correction_type")
            or row.get("edit_type") or "",
            "draft_ref": row.get("draft_ref"),
        }
    if isinstance(notes, list):
        notes = " ".join(str(n) for n in notes)
    out["notes"] = notes if isinstance(notes, str) else ""
    for key in ("original", "corrected"):
        if not isinstance(out[key], str):
            out[key] = ""
    return out


def _blocks(text: str) -> int:
    return len([ln for ln in (text or "").splitlines() if ln.strip()])


def _words(text: str) -> int:
    return len((text or "").split())


#: The correction-turn handler's target types (correction_turn.TARGETS,
#: copied here as literals so this leaf never imports the handler). A row of
#: one of these types is a thing the person SAID, not an edit they made.
CORRECTION_TURN_TARGETS = ("persona", "settings", "profile", "router_miss",
                           "template")


def _is_asked_row(row: dict) -> bool:
    """A row the correction-turn handler wrote (said, not edited)."""
    if not isinstance(row, dict):
        return False
    notes = str(row.get("notes") or row.get("note") or "").lstrip().lower()
    ctype = str(row.get("correction_type") or row.get("field") or "")
    return notes.startswith("origin: asked") or ctype in CORRECTION_TURN_TARGETS


def classify_correction_op(nrow: dict):
    """The op one normalized correction expresses, as `(op, argument)`.
    Pure, no I/O, no clock. `(None, None)` when the row says nothing this
    rail can act on — an empty row, a rewrite that got LONGER, a reordering.

    `argument` is the banned phrase (casefolded) for `ban_phrase` and the
    target block count for `shorten` (None when the row proves shortening
    without naming a target)."""
    if not isinstance(nrow, dict):
        return (None, None)
    # NIGHT 11a fix round (REVIEW_NIGHT11A_MERGED_TREE N-4, L-1): a
    # correction said IN PASSING ("too long", "shorter", "wrong skill") is
    # recorded in this same store so one reader answers "what have they told
    # me" - but it is not an edit to a draft, and reading it as one banned
    # the word "shorter" from every draft to every recipient after a single
    # turn. Rows the correction-turn handler wrote carry `origin: asked` and a
    # target type; they say nothing this rail may act on.
    notes = str(nrow.get("notes") or "").lstrip().lower()
    if (notes.startswith("origin: asked")
            or str(nrow.get("correction_type") or "") in CORRECTION_TURN_TARGETS):
        return (None, None)
    original = nrow.get("original") or ""
    corrected = nrow.get("corrected") or ""
    if original.strip() and not corrected.strip():
        stripped = original.strip()
        if _DASH_ONLY_RE.match(stripped):
            return (OP_DASH_DEFAULT, None)
        if _words(stripped) <= _BAN_MAX_WORDS and len(stripped) <= _BAN_MAX_CHARS:
            return (OP_BAN_PHRASE, stripped.lower())
        return (None, None)
    m = _PARA_NOTE_RE.search(nrow.get("notes") or "")
    if m and int(m.group(2)) < int(m.group(1)):
        return (OP_SHORTEN, int(m.group(2)))
    if original.strip() and corrected.strip():
        mo = _BLOCK_COUNT_RE.search(original)
        mc = _BLOCK_COUNT_RE.search(corrected)
        if mo and mc and int(mc.group(1)) < int(mo.group(1)):
            return (OP_SHORTEN, int(mc.group(1)))
        bo, bc = _blocks(original), _blocks(corrected)
        if bc < bo:
            return (OP_SHORTEN, bc)
        if (_words(original) >= _SHORTEN_MIN_WORDS
                and _words(corrected) <= _SHORTEN_WORD_RATIO * _words(original)):
            return (OP_SHORTEN, None)
    return (None, None)


def group_op_patterns(rows: List[dict]) -> Dict[tuple, List[dict]]:
    """Group raw correction rows by `(skill, op)`. Pure. Values are
    `{"op_arg", "row"}` entries in the order they were read; `op_arg` is the
    item each row would teach, and `propose_voice_updates` counts the floor
    per item inside each group."""
    groups: Dict[tuple, List[dict]] = {}
    for raw in rows or []:
        nrow = normalize_correction_row(raw)
        if nrow is None or not nrow["skill"]:
            continue
        op, arg = classify_correction_op(nrow)
        if not op:
            continue
        groups.setdefault((nrow["skill"], op), []).append(
            {"op_arg": arg, "row": nrow})
    return groups


def voice_proposal_fingerprint(skill: str, op: str) -> str:
    """The PAIR key: the whole kind of correction in one skill. Nothing new is
    applied under it (cr1#778). It stays because deletion rows name it, where
    it closes the kind, and because books written before that fix hold it in
    the ledger, where it no longer does (`learning_pass._voice_exclusions`).
    Do not change this derivation: those rows are never rewritten."""
    return "vbu_" + hashlib.sha256(
        f"{(skill or '').lower()}\x00{op}".encode("utf-8")).hexdigest()[:16]


def voice_item_fingerprint(skill: str, op: str, item) -> str:
    """The key one learned thing is remembered by: the phrase for
    `ban_phrase`, the paragraph count for `shorten`, None for a shorten that
    named no count. Applied and undone changes exclude this and nothing wider,
    so the next phrase in the same skill stays learnable. The third field
    keeps it from ever equalling the pair key, even for None."""
    text = "" if item is None else str(item)
    return "vbu_" + hashlib.sha256(
        f"{(skill or '').lower()}\x00{op}\x00{text}".encode("utf-8")
    ).hexdigest()[:16]


def _quoted_list(phrases: List[str]) -> str:
    quoted = ['"' + p + '"' for p in phrases]
    if not quoted:
        return ""
    if len(quoted) == 1:
        return quoted[0]
    return ", ".join(quoted[:-1]) + " and " + quoted[-1]


#: What makes a line a rule against a phrase, outside a Taboos section.
_BAN_WORD_RE = re.compile(r"\b(?:never|avoids?|avoided|banned|don't|do not)\b",
                          re.IGNORECASE)


def _lines_outside_examples(block_markdown: str) -> list:
    """`(section, line)` for the block's lines, with every `### Examples` left
    out. An override starts as a copy of the baked block, so sample drafts sit
    in it, and words in a sample are not a rule about those words."""
    out: list = []
    section = ""
    for line in (block_markdown or "").split("\n"):
        head = _SECTION_HEAD_RE.match(line)
        if head:
            section = head.group(1).lower()
        elif "example" not in section:
            out.append((section, line))
    return out


#: A learned line's label and its note about how many drafts it came from.
#: Neither is the rule: "drafts" or "learned" in them bans nothing.
_LEARNED_LABEL_RE = re.compile(r"^\s*[-*]\s+Learned\b[^:\n]*:\s*")
_LEARNED_NOTE_RE = re.compile(
    u"\\s*(?:\\(you rewrote\\b.*|\\(\\d+ drafts? cut\\b.*"
    u"|[-\u2013\u2014] you cut \\d+ drafts?\\b.*)$")


def _rule_text(line: str) -> str:
    """What a block line says as a rule, without those two."""
    return _LEARNED_NOTE_RE.sub("", _LEARNED_LABEL_RE.sub("", line))


def _phrase_already_banned(block_markdown: str, phrase: str) -> bool:
    """Whether a rule line in the block already bans this phrase: a bullet
    under Taboos, or one that says never or avoid, learned or stated. A line
    saying what to use is not a ban, and neither is a sample draft, so a phrase
    that merely appears in either stays learnable (cr1#784). A learned line's
    label and its note are not part of the rule."""
    pattern = re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", re.IGNORECASE)
    for section, line in _lines_outside_examples(block_markdown):
        text = _rule_text(line)
        if (line.lstrip().startswith(("- ", "* ")) and pattern.search(text)
                and (section.startswith("taboo") or _BAN_WORD_RE.search(text))):
            return True
    return False


# The two bullets `apply_voice_delta` writes for a shorten. Anchored on the
# learned-line shape so a baked block's own wording can never read as learned.
_LEARNED_COUNT_RE = re.compile(
    r"^- Learned [^:\n]*: paragraph count \S+ keep it to (\d+)\b", re.MULTILINE)
_LEARNED_SHORT_RE = re.compile(r"^- Learned [^:\n]*: keep it short\b",
                               re.MULTILINE)


def _length_already_learned(block_markdown: str, target) -> bool:
    """A learned paragraph count covers every looser one: with "keep it to 2"
    in force, a third bullet saying 3 would only contradict it. A shorten with
    no count is its own item, covered only by its own line."""
    text = block_markdown or ""
    if target is None:
        return bool(_LEARNED_SHORT_RE.search(text))
    return any(int(n) <= target for n in _LEARNED_COUNT_RE.findall(text))


def _item_open(skill: str, op: str, item, block: str, closed: set) -> bool:
    """Whether one correction's item can still be learned: not excluded by its
    own fingerprint and not already in the skill's block."""
    if op == OP_BAN_PHRASE and not item:
        return False
    if voice_item_fingerprint(skill, op, item) in closed:
        return False
    if op == OP_BAN_PHRASE:
        return not _phrase_already_banned(block, item)
    return not _length_already_learned(block, item)


_LEGACY_COUNT_RE = re.compile(r"^Kept it to (\d+)\b")


def items_named_in_line(op: str, line: str) -> list:
    """What a past `plain` sentence learned, read back. Pure. Changes written
    before cr1#778 carry only the pair fingerprint, so this sentence on their
    event is the one record of which phrases an undone change held."""
    text = line or ""
    if op == OP_BAN_PHRASE:
        return [p.lower() for p in
                _QUOTED_PHRASE_RE.findall(text.split("you rewrote")[0])]
    if op == OP_SHORTEN:
        m = _LEGACY_COUNT_RE.match(text)
        if m:
            return [int(m.group(1))]
        return [None] if text.startswith("Started writing shorter") else []
    return []


def _paragraphs_word(count: int) -> str:
    return str(count) + " " + ("paragraph" if count == 1 else "paragraphs")


def rule_sentences(skill: str, op: str, phrases: List[str], target) -> tuple:
    """`(rule, effect)` for one learned change, in the owner's words (cr1#784):
    the rule in one sentence, and what it changes from now on. Pure."""
    noun = voice_surface_noun(skill)
    if op == OP_BAN_PHRASE:
        pronoun = "it" if len(phrases) == 1 else "them"
        return ("Never write " + _quoted_list(phrases) + " " + noun + ".",
                "From now on I leave " + pronoun + " out " + noun + ".")
    if target is None:
        return ("Write shorter " + noun + ".",
                "From now on my first drafts come out shorter " + noun + ".")
    return ("Keep it to " + _paragraphs_word(target) + " " + noun + ".",
            "From now on I stop at " + _paragraphs_word(target) + " " + noun + ".")


def plain_sentence(skill: str, op: str, phrases: List[str], target,
                   count: int) -> str:
    """The past-tense sentence a learned change is narrated with. Pure."""
    noun = voice_surface_noun(skill)
    if op == OP_BAN_PHRASE:
        return ("Stopped using " + _quoted_list(phrases) + " " + noun
                + " — you rewrote " + ("that" if len(phrases) == 1 else "those")
                + " " + str(count) + " times.")
    if target is None:
        return ("Started writing shorter " + noun + " — you cut " + str(count)
                + " drafts down.")
    return ("Kept it to " + _paragraphs_word(target) + " " + noun
            + " — you cut longer drafts to that " + str(count) + " times.")


def correction_ref(skill: str, op: str, entry: dict) -> dict:
    """What a rule keeps of one correction it came from: enough to find the
    row in the corrections log again, and no draft text, because bodies stay
    out of the event stream."""
    row = entry.get("row") or {}
    return {"skill": skill, "op": op, "item": entry.get("op_arg"),
            "timestamp": row.get("timestamp"),
            "draft_ref": row.get("draft_ref"),
            "fingerprint": _fingerprint(skill, row.get("original") or "",
                                        row.get("corrected") or "")}


def row_goes_against(op: str, item, nrow: dict) -> bool:
    """Whether one normalized correction, made after a rule landed, is the
    owner correcting AGAINST it (cr1#784, ruling C5): the banned phrase written
    back in, or a draft inside the learned length made longer. Pure. A thing
    said in passing is not an edit, and a phrase that merely sits inside a
    longer word, or was already in the draft, proves nothing.

    A length is about a WHOLE draft, and most rows hold one paragraph pair
    (`diff_and_classify`). So a length rule counts only rows that state a
    whole-draft count: the `paragraph count a->b` note, or the `N blocks`
    summary shape. That note is written only when the count moves by two or
    more, so a draft taken one paragraph over goes uncounted. A rule with no
    count has no whole draft to read at all and stays uncounted."""
    if not isinstance(nrow, dict) or _is_asked_row(nrow):
        return False
    original = nrow.get("original") or ""
    corrected = nrow.get("corrected") or ""
    if op == OP_BAN_PHRASE:
        if not item:
            return False
        pattern = re.compile(r"(?<!\w)" + re.escape(str(item)) + r"(?!\w)",
                             re.IGNORECASE)
        return bool(pattern.search(corrected)) and not pattern.search(original)
    if op != OP_SHORTEN or item is None:
        return False
    m = _PARA_NOTE_RE.search(nrow.get("notes") or "")
    if m:
        return int(m.group(1)) <= item < int(m.group(2))
    before, after = _BLOCK_COUNT_RE.search(original), _BLOCK_COUNT_RE.search(corrected)
    return bool(before and after
                and int(before.group(1)) <= item < int(after.group(1)))


def propose_voice_updates(
    groups: Dict[tuple, List[dict]],
    *,
    existing_overrides: Optional[Dict[str, str]] = None,
    cooldown_fingerprints: Optional[set] = None,
    cap: int = VOICE_CAP,
    threshold: int = VOICE_THRESHOLD,
    ceiling: Optional[int] = None,
) -> List[dict]:
    """Turn `(skill, op)` groups into at most one voice-block delta per
    `(skill, op)` per run. Pure: no reads, no writes, no clock.

    cr1#778: the floor is per learned item, by the maintainer's ruling. An item
    proposes when it alone holds >= `threshold` corrections, it is not already
    learned (in the block, or excluded by its own fingerprint), and the op has
    a section to land in (`dash_default` never does, the ban is already the
    product default). That one comparison is the whole floor. Phrases at the
    floor go most-repeated first and are capped at `cap` PER SKILL; a shorten
    learns the tightest count at the floor. `count` is the rows of the items
    named, nothing else.

    cr1#784: `ceiling`, when given, leaves out an item that has reached it.
    The job calls this twice over one set of groups: once at the apply number
    with no ceiling, and once at the ask number with the apply number as the
    ceiling, so an item is either applied or asked and never both.

    `cooldown_fingerprints` closes at either width: an item fingerprint closes
    that item, a pair fingerprint closes the kind (a deleted line).

    Each proposal: `{skill, op, section, phrases, target_blocks, count,
    fingerprint, item_fingerprints, item_counts, surface_noun, plain, rule,
    effect, corrections, evidence_rows}`. `item_fingerprints` holds one per
    learned item; `fingerprint` is the first of them, the one the proposal
    rail keys on. `item_counts` is each item's own count, and `corrections`
    the rows those counts came from."""
    existing_overrides = existing_overrides or {}
    cooling = cooldown_fingerprints or set()
    out: List[dict] = []
    for (skill, op) in sorted(groups, key=lambda k: (-len(groups[k]), k[0], k[1])):
        section = VOICE_OP_SECTIONS.get(op)
        if not section or voice_proposal_fingerprint(skill, op) in cooling:
            continue
        block = existing_overrides.get(skill) or ""
        counts = Counter(e.get("op_arg") for e in groups[(skill, op)]
                         if _item_open(skill, op, e.get("op_arg"), block, cooling))
        # THE FLOOR: one threshold against one item's own count.
        at_floor = [item for item, n in counts.items() if n >= threshold
                    and (ceiling is None or n < ceiling)]
        if not at_floor:
            continue
        phrases: List[str] = []
        target_blocks = None
        if op == OP_BAN_PHRASE:
            # sorted() is stable, so equal counts keep the order they were read.
            phrases = sorted(at_floor, key=lambda p: -counts[p])[:max(0, cap)]
            if not phrases:
                continue
            count = sum(counts[p] for p in phrases)
            items = phrases
        else:
            targets = [t for t in at_floor if isinstance(t, int)]
            target_blocks = min(targets) if targets else None
            count = counts[target_blocks]
            items = [target_blocks]
        plain = plain_sentence(skill, op, phrases, target_blocks, count)
        item_fingerprints = [voice_item_fingerprint(skill, op, i) for i in items]
        rule, effect = rule_sentences(skill, op, phrases, target_blocks)
        out.append({
            "skill": skill,
            "op": op,
            "section": section,
            "phrases": phrases,
            "target_blocks": target_blocks,
            "count": count,
            "fingerprint": item_fingerprints[0],
            "item_fingerprints": item_fingerprints,
            "item_counts": {fp: counts[i]
                            for fp, i in zip(item_fingerprints, items)},
            "surface_noun": voice_surface_noun(skill),
            "plain": plain,
            "rule": rule,
            "effect": effect,
            "corrections": [correction_ref(skill, op, e)
                            for e in groups[(skill, op)]
                            if e.get("op_arg") in items],
            "evidence_rows": count,
        })
    return out


_SECTION_HEAD_RE = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)


def learned_bullet(proposal: dict, *, learned_on: Optional[str] = None) -> str:
    """The one line a proposal adds to a voice block. Pure. The change's event
    carries it, because this line is what its Undo takes back (cr1#784)."""
    date = (learned_on or "")[:10]
    phrases = list(proposal.get("phrases") or [])
    if proposal.get("op") == OP_BAN_PHRASE:
        return ("- Learned " + date + ": never " + _quoted_list(phrases)
                + " (you rewrote " + str(proposal.get("evidence_rows", 0))
                + " drafts to drop "
                + ("it" if len(phrases) == 1 else "them") + ")")
    target = proposal.get("target_blocks")
    if target is None:
        return ("- Learned " + date + ": keep it short — you cut "
                + str(proposal.get("evidence_rows", 0)) + " drafts down")
    return ("- Learned " + date + ": paragraph count — keep it to "
            + str(target) + " (" + str(proposal.get("evidence_rows", 0))
            + " drafts cut to that)")


def _line_names(op: str, line: str, item) -> bool:
    """Whether a block line says this learned item."""
    if op == OP_BAN_PHRASE:
        return '"' + str(item) + '"' in line.lower()
    if item is None:
        return "keep it short" in line
    return "keep it to " + str(item) + " " in line


_NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
                 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}


#: Curly quotes to straight ones, and every hyphen and dash to a space.
_NORMAL_MARKS = str.maketrans({
    u"\u2018": "'", u"\u2019": "'", u"\u201a": "'", u"\u201b": "'", u"\u02bc": "'",
    u"\u201c": '"', u"\u201d": '"', u"\u201e": '"', u"\u201f": '"',
    "-": " ", u"\u2010": " ", u"\u2011": " ", u"\u2012": " ", u"\u2013": " ",
    u"\u2014": " ", u"\u2015": " "})


def _normal(text: str) -> str:
    """Lower case, one kind of quote, no hyphens, single spaces."""
    return " ".join(str(text).translate(_NORMAL_MARKS).lower().split())


def _line_mentions(op: str, line: str, item) -> bool:
    """Whether a line says this item in a shape a person is likely to leave it
    in. Looser than `_line_names` on purpose: this is what stops an Undo saying
    a rule is gone, and it never picks a line to remove. It reads known shapes,
    so a free rewording is out of its reach, and the receipt says so. A quoted
    phrase that merely contains this one is that other phrase."""
    text = _normal(_rule_text(line))
    if op == OP_BAN_PHRASE:
        want = _normal(item)
        text = re.sub(r'"([^"]*)"', lambda m: m.group(0) if m.group(1).strip() == want
                      else '""', text)
        return bool(re.search(r"(?<!\w)" + re.escape(want) + r"(?!\w)", text))
    if item is None:
        return "keep it short" in text or "shorter" in text
    count = f"(?:{item}|{_NUMBER_WORDS.get(item, item)})"
    return bool(re.search(
        rf"\b(?:keep (?:it )?to|max|maximum|at most|no more than|never more than"
        rf"|up to|stick to|limit(?:ed)? to) {count}\b"
        rf"|\b{count} (?:paragraphs?|paras?)\b", text))


def line_of_change(markdown: str, change: dict, *,
                   baseline: str = "") -> Optional[str]:
    """The line of `markdown` one learned change wrote, or None when it is not
    there. A change written since cr1#784 names its line. A line edited by hand
    since, or written by an older change, is found as the learned line that
    neither its `previous_markdown` nor `baseline` had, naming what the change
    learned, the newest such line first: one naming all of it, else one naming
    part of it."""
    lines = (markdown or "").split("\n")
    line = change.get("learned_line")
    if line and line in lines:
        return line
    op = str(change.get("op") or "")
    items = items_named_in_line(op, str(change.get("delta") or ""))
    if not items:
        return None
    before = set(((change.get("previous_markdown") or "") + "\n"
                  + baseline).split("\n"))
    mine = [c for c in reversed(lines)
            if c.startswith("- Learned ") and c not in before]
    for wanted in (all, any):
        for candidate in mine:
            if wanted(_line_names(op, candidate, item) for item in items):
                return candidate
    return None


def items_still_named(markdown: str, change: dict, *, baseline: str = "") -> list:
    """What this change learned that a rule line of the block still says.
    A rule line is a line under `## Voice Block` that is no heading and no
    sample draft, so the file's header says nothing. Lines the block had before
    the change, or holds from `baseline`, say nothing about it either. Anything
    returned with no `line_of_change` is a rule the owner reshaped or moved: it
    is in force, and an Undo must not guess at it, or call it gone."""
    op = str(change.get("op") or "")
    before = set(((change.get("previous_markdown") or "") + "\n"
                  + baseline).split("\n"))
    text = markdown or ""
    at = text.find("## Voice Block")
    lines = [line for _section, line in
             _lines_outside_examples(text[at:] if at >= 0 else text)
             if line.strip() and not line.startswith("#") and line not in before]
    return [item for item in items_named_in_line(op, str(change.get("delta") or ""))
            if any(_line_mentions(op, line, item) for line in lines)]


def has_owner_lines(markdown: str, baked: str) -> bool:
    """Whether a voice file holds anything beyond its header, its section heads
    and the baked block it was copied from."""
    text = markdown or ""
    at = text.find("## Voice Block")
    copied = {line.strip() for line in (baked or "").split("\n")}
    return any(line.strip() and not line.strip().startswith("#")
               and line.strip() not in copied
               for line in (text[at:] if at >= 0 else text).split("\n"))


def without_learned_line(markdown: str, line: str, *, section: str = "",
                         keep_heading_from: str = "") -> str:
    """`markdown` with the last copy of `line` removed. Pure. A section left
    empty by that loses its heading too, unless `keep_heading_from` has it, so
    a block that gained a section for this one line goes back to not having it."""
    lines = markdown.split("\n")
    at = len(lines) - 1 - lines[::-1].index(line)
    del lines[at]
    head_at = at - 1
    while head_at >= 0 and not lines[head_at].strip():
        head_at -= 1
    head = _SECTION_HEAD_RE.match(lines[head_at]) if head_at >= 0 else None
    follows = next((ln for ln in lines[at:] if ln.strip()), "")
    if (head and section and head.group(1).lower().startswith(section.lower())
            and (not follows or follows.startswith("#"))
            and lines[head_at] not in keep_heading_from.split("\n")):
        del lines[head_at]
    return "\n".join(lines)


def apply_voice_delta(block_markdown: str, proposal: dict, *,
                      learned_on: Optional[str] = None) -> str:
    """Merge ONE proposal into a `## Voice Block` body and return the new
    markdown. Pure. ADDITIVE: the learned line is appended to its section as
    a new bullet carrying its date and its evidence count — nothing already
    in the block is edited or removed, so `stated outranks learned` survives
    a merge and the operator can read which line the machine wrote.

    A block with no matching `###` section gains one at the end."""
    text = block_markdown or "## Voice Block\n"
    section = proposal.get("section") or ""
    bullet = learned_bullet(proposal, learned_on=learned_on)
    lines = text.split("\n")
    start = None
    for i, line in enumerate(lines):
        m = _SECTION_HEAD_RE.match(line)
        if m and m.group(1).strip().lower().startswith(section.lower()):
            start = i
            break
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("### " + section)
        lines.append(bullet)
        lines.append("")
        return "\n".join(lines)
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if _SECTION_HEAD_RE.match(lines[j]) or lines[j].startswith("## "):
            end = j
            break
    insert = end
    while insert > start + 1 and not lines[insert - 1].strip():
        insert -= 1
    lines.insert(insert, bullet)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The instruction corpus itself (intake DEBT_2026-08-17
# skill-templates-teach-em-dashes)
#
# The runtime dash gate has shipped since v5.5.0 and it operates on RENDERED
# sections only. The instruction corpus was never scanned — and a model
# mirrors its examples, so a template full of em-dashes teaches the exact
# violation the gate then flags. That is why a voice gate reported 82-94% of
# recent documents failing on `dash_as_punctuation` while the runtime block
# was working correctly: it was catching output the corpus had taught.
#
# This is the READER. It counts the pattern inside a SKILL.md's OUTPUT-SHAPING
# sections only — fenced example blocks and the `### Examples` section of a
# Voice Block — because prose that DISCUSSES dashes is not prose that teaches
# them. The suite pins the counts as a CEILING per skill, so the pattern
# cannot re-enter while the sweep itself is done in its own lane.
# ---------------------------------------------------------------------------

_EM_DASH_CHARS = ("—", "–")


def _output_shaping_spans(markdown: str) -> List[str]:
    """The parts of a SKILL.md a model MIRRORS: fenced code/example blocks,
    and the body of any `### Examples` section. Pure."""
    spans: List[str] = []
    lines = (markdown or "").split("\n")
    in_fence = False
    buf: List[str] = []
    in_examples = False
    for line in lines:
        if line.lstrip().startswith("```"):
            if in_fence:
                spans.append("\n".join(buf))
                buf = []
            in_fence = not in_fence
            continue
        if in_fence:
            buf.append(line)
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            in_examples = stripped.lower().lstrip("# ").startswith("examples")
            continue
        if in_examples:
            spans.append(line)
    if in_fence and buf:
        spans.append("\n".join(buf))
    return spans


def count_instruction_dashes(markdown: str) -> int:
    """How many lines of a SKILL.md's output-shaping sections carry a dash
    used as punctuation. Pure; the number a ceiling is pinned against."""
    n = 0
    for span in _output_shaping_spans(markdown):
        for line in span.split("\n"):
            if any(ch in line for ch in _EM_DASH_CHARS):
                n += 1
    return n


__all__ = [
    "diff_and_classify", "append_correction", "snapshot_draft",
    # LEARNFIX1 — the retraction writer, the one in-passing entry, and the
    # lister the profile page's learned layer reads.
    "retract_correction", "RETRACTS_KEY", "log_in_passing",
    "IN_PASSING_CHANGE_CLASS", "IN_PASSING_BATCH_PREFIX", "IN_PASSING_RECEIPT",
    "IN_PASSING_NOOP_RECEIPT", "IN_PASSING_FAILED_RECEIPT",
    "written_overrides",
    "reconcile_sent_against_snapshots", "load_corrections", "unreviewed_counts",
    "group_correction_patterns", "load_voice_block_override",
    "write_voice_block_override", "parse_taboos_allow", "parse_ban_dashes",
    "OP_BAN_PHRASE", "OP_SHORTEN", "OP_DASH_DEFAULT", "VOICE_OP_SECTIONS",
    "VOICE_THRESHOLD", "VOICE_ASK_THRESHOLD", "VOICE_CAP", "VOICE_SURFACE_NOUNS",
    "VOICE_SURFACE_NOUN_DEFAULT", "voice_surface_noun",
    "normalize_correction_row", "classify_correction_op", "group_op_patterns",
    "voice_proposal_fingerprint", "propose_voice_updates", "apply_voice_delta",
    "count_instruction_dashes",
]
