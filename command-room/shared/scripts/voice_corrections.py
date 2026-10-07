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


def append_correction(
    workspace_root, *, skill: str, domain: str, recipient_id: Optional[str],
    original: str, corrected: str, correction_type: str, notes: str = "",
    draft_event_seq=None,
) -> bool:
    """Append one correction row (VOICE_CALIBRATION schema, exact keys) to
    `_hq/voice/corrections-<skill>.jsonl`. Deduped against the log tail by
    (skill, original, corrected). Returns True if written, False if a duplicate.
    Never raises.

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
                if _fingerprint(skill, row.get("original_draft", ""), row.get("corrected_by_user", "")) == fp:
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
        }])
    except Exception:
        pass


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

# Ops a correction can express. The op — not the literal text — is the
# "same pattern" the >=3 floor counts, because someone who deletes twelve
# DIFFERENT filler phrases from their documents has said one thing twelve
# times, not twelve things once.
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

# D5, unchanged: >=3 same-pattern corrections per skill before anything moves.
VOICE_THRESHOLD = 3
# The amendments' worst-case paragraph is the authority on the cap: "the
# system can ban at most three phrases per writing skill it has seen the
# client rewrite three or more times". Three PHRASES PER SKILL per run.
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
    correction_type, notes}` or None for a row that is not a dict."""
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
    """Group raw correction rows by `(skill, op)` — the pattern key the
    >=3 floor counts. Pure. Values are `{"op_arg", "row"}` entries in the
    order they were read."""
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
    return "vbu_" + hashlib.sha256(
        f"{(skill or '').lower()}\x00{op}".encode("utf-8")).hexdigest()[:16]


def _quoted_list(phrases: List[str]) -> str:
    quoted = ['"' + p + '"' for p in phrases]
    if not quoted:
        return ""
    if len(quoted) == 1:
        return quoted[0]
    return ", ".join(quoted[:-1]) + " and " + quoted[-1]


def _phrase_already_banned(block_markdown: str, phrase: str) -> bool:
    return phrase.lower() in (block_markdown or "").lower()


def propose_voice_updates(
    groups: Dict[tuple, List[dict]],
    *,
    existing_overrides: Optional[Dict[str, str]] = None,
    cooldown_fingerprints: Optional[set] = None,
    cap: int = VOICE_CAP,
    threshold: int = VOICE_THRESHOLD,
) -> List[dict]:
    """Turn `(skill, op)` groups into at most one voice-block delta per
    `(skill, op)`. Pure — no reads, no writes, no clock.

    A group proposes when it holds >= `threshold` corrections AND the op has
    a section to land in (`dash_default` never does — the ban is already the
    product default). Phrases already present in the skill's current block
    are dropped, and what remains is capped at `cap` PER SKILL.

    Each proposal: `{skill, op, section, phrases, target_blocks, count,
    fingerprint, surface_noun, plain, evidence_rows}`."""
    existing_overrides = existing_overrides or {}
    cooling = cooldown_fingerprints or set()
    out: List[dict] = []
    for (skill, op) in sorted(groups, key=lambda k: (-len(groups[k]), k[0], k[1])):
        entries = groups[(skill, op)]
        if len(entries) < threshold:
            continue
        section = VOICE_OP_SECTIONS.get(op)
        if not section:
            continue
        fingerprint = voice_proposal_fingerprint(skill, op)
        if fingerprint in cooling:
            continue
        block = existing_overrides.get(skill) or ""
        phrases: List[str] = []
        target_blocks = None
        if op == OP_BAN_PHRASE:
            seen = set()
            for entry in entries:
                phrase = entry.get("op_arg")
                if not phrase or phrase in seen:
                    continue
                seen.add(phrase)
                if _phrase_already_banned(block, phrase):
                    continue
                phrases.append(phrase)
            phrases = phrases[:max(0, cap)]
            if not phrases:
                continue
            plain = ("Stopped using " + _quoted_list(phrases) + " "
                     + voice_surface_noun(skill) + " — you rewrote "
                     + ("that" if len(phrases) == 1 else "those") + " "
                     + str(len(entries)) + " times.")
        else:
            targets = [e["op_arg"] for e in entries
                       if isinstance(e.get("op_arg"), int)]
            target_blocks = min(targets) if targets else None
            if target_blocks is None:
                plain = ("Started writing shorter " + voice_surface_noun(skill)
                         + " — you cut " + str(len(entries)) + " drafts down.")
            else:
                plain = ("Kept it to " + str(target_blocks) + " "
                         + ("paragraph" if target_blocks == 1 else "paragraphs")
                         + " " + voice_surface_noun(skill)
                         + " — you cut longer drafts to that "
                         + str(len(entries)) + " times.")
        out.append({
            "skill": skill,
            "op": op,
            "section": section,
            "phrases": phrases,
            "target_blocks": target_blocks,
            "count": len(entries),
            "fingerprint": fingerprint,
            "surface_noun": voice_surface_noun(skill),
            "plain": plain,
            "evidence_rows": len(entries),
        })
    return out


_SECTION_HEAD_RE = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)


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
    date = (learned_on or "")[:10]
    phrases = list(proposal.get("phrases") or [])
    if proposal.get("op") == OP_BAN_PHRASE:
        bullet = ("- Learned " + date + ": never " + _quoted_list(phrases)
                  + " (you rewrote " + str(proposal.get("evidence_rows", 0))
                  + " drafts to drop "
                  + ("it" if len(phrases) == 1 else "them") + ")")
    else:
        target = proposal.get("target_blocks")
        if target is None:
            bullet = ("- Learned " + date + ": keep it short — you cut "
                      + str(proposal.get("evidence_rows", 0)) + " drafts down")
        else:
            bullet = ("- Learned " + date + ": paragraph count — keep it to "
                      + str(target) + " ("
                      + str(proposal.get("evidence_rows", 0))
                      + " drafts cut to that)")
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
    "VOICE_THRESHOLD", "VOICE_CAP", "VOICE_SURFACE_NOUNS",
    "VOICE_SURFACE_NOUN_DEFAULT", "voice_surface_noun",
    "normalize_correction_row", "classify_correction_op", "group_op_patterns",
    "voice_proposal_fingerprint", "propose_voice_updates", "apply_voice_delta",
    "count_instruction_dashes",
]
