#!/usr/bin/env python3
"""Corrections in passing — the turn where someone tells you what was wrong
while asking for something else (SPEC_SURFACES2 §1).

WHAT WAS WRONG. "too long", "not the inbox", "why is this here", "no, I meant
the other one", "that should have been the memo writer" are said constantly
and land nowhere. The only door for changing behaviour was a `customize`
conversation nobody starts, so a person says the same thing every week and the
product never moves. Meanwhile the correction is the highest-quality signal in
the workspace: it is specific, it is unprompted, and it arrives already
attached to the thing it is about.

WHAT THIS IS. One recognizer and one handler, carried by every skill through a
shared contract paragraph. The recognizer is pure and the handler:

  - applies the change itself, at the smallest scope that fits;
  - writes a receipt the person can see and reverse;
  - writes the correction into the store with `origin: "asked"` — the person
    SAID it, which outranks anything the learning job infers;
  - logs a `router_miss` when the complaint was about routing;
  - NEVER ASKS. Not "should I?", not "which one?", not a confirm card. A
    correction is already an instruction; asking about it is the friction the
    correction was complaining about.

Five targets, in the order they are tested:

  template     "make it like this", "use this as the template" — the ONLY
               code capture site the exemplar rail has. Structural-correction
               capture was described in prose at thirteen composer SKILL.md
               sites and called by NOTHING, which is why the workspace
               exemplar store is empty after months and the learning job's
               exemplar leg reads zero. This door closes one of the thirteen:
               the person says the words and hands over the document, so the
               capture is an instruction rather than a guess at a diff.
  router_miss  "wrong skill", "that should have been the memo writer"
  settings     "not the inbox", "why is this here", "stop showing me X"
  profile      "no, I meant X", "actually it's X"
  persona      "too long", "shorter", "less of that"

stdlib only. Never raises into a chat turn — an unhandleable phrase returns
None and the turn proceeds exactly as it did before this module existed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# The skills that carry the shared contract paragraph in their own SKILL.md
# (`shared/CORRECTION_IN_PASSING.md`). This constant is the DERIVATION SOURCE
# for the guard: adding a skill here without adding its paragraph reds, and so
# does the reverse. The set is the five that compose for a named recipient plus
# the catch-all handler — the surfaces where a correction in passing actually
# gets said. Widening it is a citation, never a silent edit.
CONTRACT_SKILLS = ("email-writer", "follow-up-ritual", "memo-writer",
                   "intro-broker", "call-prep", "workspace-manager")
CONTRACT_DOC = "shared/CORRECTION_IN_PASSING.md"
# The token every carrying SKILL.md must name. The guard greps for THIS, so a
# paragraph that drops the module call stops counting as the contract.
CONTRACT_TOKEN = "correction_turn.handle_correction_turn"

TARGET_PERSONA = "persona"
TARGET_SETTINGS = "settings"
TARGET_PROFILE = "profile"
TARGET_ROUTER = "router_miss"
TARGET_TEMPLATE = "template"
TARGETS = (TARGET_TEMPLATE, TARGET_ROUTER, TARGET_SETTINGS, TARGET_PROFILE,
           TARGET_PERSONA)

# The correction is recorded as ASKED. That is the whole point of the
# distinction: `origin: asked` outranks `origin: observed` forever, and the
# learning job's floors never apply to something the person said out loud.
ORIGIN_ASKED = "asked"

# The change class the persona correction rides, so `undo` lists it beside
# every other automatic act. `chat_persona` already owns this class.
PERSONA_CHANGE_CLASS = "chat_persona_correction"

_ROUTER_RE = re.compile(
    r"\bwrong skill\b"
    r"|\bthat should have (been|gone to)\b"
    r"|\bshould have been (the |a )?[a-z][\w -]*\b"
    r"|\bthat'?s not what I asked for\b",
    re.IGNORECASE)
_ROUTER_TARGET_RE = re.compile(
    r"should have (?:been|gone to)\s+(?:the\s+|a\s+)?([a-z][\w' -]{2,40})",
    re.IGNORECASE)

_SETTINGS_RE = re.compile(
    r"\bnot the inbox\b"
    r"|\bwhy is this here\b"
    r"|\bwhy am I seeing this\b"
    r"|\bstop showing me\b"
    r"|\bdon'?t show me\b"
    r"|\bthis doesn'?t belong (here|on)\b",
    re.IGNORECASE)

_PROFILE_RE = re.compile(
    r"\bno,?\s*I meant\b"
    r"|\bactually,? it'?s\b"
    r"|\bI meant\s+\S",
    re.IGNORECASE)
_PROFILE_PAYLOAD_RE = re.compile(
    r"(?:no,?\s*I meant|I meant|actually,? it'?s)\s+(.{1,120})",
    re.IGNORECASE | re.DOTALL)

# The document-shape instruction. Deliberately narrow: it fires on someone
# SAYING the words, never on a diff a heuristic decided was structural. A
# capture site that guesses feeds a rail whose end is an automatic promotion
# of a document to a gold standard, and a wrong guess there is expensive.
_TEMPLATE_RE = re.compile(
    r"\bmake it like this\b"
    r"|\bmake that the standard (layout|shape|format)\b"
    r"|\buse this as the (template|standard|shape)\b"
    r"|\bthis is the (format|shape|layout) I want\b",
    re.IGNORECASE)

_PERSONA_RE = re.compile(
    r"\btoo long\b"
    r"|\bmake it shorter\b"
    r"|\bshorter\b"
    r"|\btoo wordy\b"
    r"|\bless of that\b"
    r"|\bcut it down\b",
    re.IGNORECASE)


def classify_correction_turn(text: str) -> Optional[dict]:
    """Which correction, if any, this turn carries. Pure — no I/O, no clock.

    Returns `{target, intent, payload, said}` or None. Order matters: a
    routing complaint that also says "too long" is a routing complaint, and
    fixing the persona instead would leave the real problem in place."""
    said = (text or "").strip()
    if not said:
        return None
    if _TEMPLATE_RE.search(said):
        return {"target": TARGET_TEMPLATE, "intent": "use_this_shape",
                "payload": "", "said": said}
    if _ROUTER_RE.search(said):
        m = _ROUTER_TARGET_RE.search(said)
        meant = (m.group(1).strip().rstrip(".!,") if m else "")
        return {"target": TARGET_ROUTER, "intent": "wrong_skill",
                "payload": meant, "said": said}
    if _SETTINGS_RE.search(said):
        return {"target": TARGET_SETTINGS, "intent": "wrong_surface",
                "payload": "", "said": said}
    if _PROFILE_RE.search(said):
        m = _PROFILE_PAYLOAD_RE.search(said)
        payload = (m.group(1).strip() if m else "")
        return {"target": TARGET_PROFILE, "intent": "restate_fact",
                "payload": payload, "said": said}
    if _PERSONA_RE.search(said):
        return {"target": TARGET_PERSONA, "intent": "shorter",
                "payload": "", "said": said}
    return None


def _record_correction(workspace_root, *, skill: str, said: str,
                       target: str, recipient_id=None, domain: str = "",
                       original: str = "", corrected: str = "",
                       batch_id=None) -> dict:
    """The correction goes into the SAME store the passive rail writes, so
    one reader answers "what have they told me about this skill" whether the
    person typed it or the product noticed it. `origin: asked` is what tells
    the two apart afterwards.

    LEARNFIX1 1.2 — this is now `voice_corrections.log_in_passing`, which
    also puts ONE ledger row with a batch behind the correction. It used to
    call `append_correction` alone and return a bool, so nothing on the
    ledger named the act and the `undo` the receipt promises had nothing to
    resolve. Returns `log_in_passing`'s dict; `recorded` is the old bool."""
    try:
        import voice_corrections as vc

        return vc.log_in_passing(
            workspace_root, skill=skill, said=said,
            recipient_id=recipient_id, domain=domain or target,
            correction_type=target, original=original or said,
            corrected=corrected,
            notes="origin: " + ORIGIN_ASKED + " — " + said[:200],
            batch_id=batch_id)
    except Exception:  # pragma: no cover — capture is best-effort
        return {"recorded": False, "receipt": "", "batch_id": None,
                "fingerprint": "", "event_seq": None}


def exemplar_kind_for_skill(skill: str) -> Optional[str]:
    """The document kind a composer's output belongs to, DERIVED from the
    shipped kind->skill map rather than restated here (a second copy is a
    second thing to drift). None for a skill that produces no document kind —
    an email is not a document kind, and this door stays shut for it."""
    try:
        from brief_gates import VOICE_SKILL_BY_KIND
    except Exception:  # pragma: no cover
        return None
    for kind, composer in (VOICE_SKILL_BY_KIND or {}).items():
        if composer == skill:
            return kind
    return None


def _capture_structure(workspace_root, *, skill: str, said: str,
                       document: str) -> dict:
    """Bank the document the person just held up as the shape they want.

    CAPTURE ONLY. This never writes an exemplar — the learning job's floor
    (three same-direction corrections on one kind) and its scrub gate stand
    between this row and any promotion. Never raises into a chat turn."""
    out = {"captured": False, "kind": None, "reason": ""}
    kind = exemplar_kind_for_skill(skill)
    if not kind:
        out["reason"] = "no document kind for this surface"
        return out
    out["kind"] = kind
    text = (document or "").strip()
    if not text:
        out["reason"] = "no document was handed over"
        return out
    try:
        from exemplars import DIRECTION_TEMPLATE, append_structural_correction

        out["captured"] = bool(append_structural_correction(
            workspace_root, kind=kind, direction=DIRECTION_TEMPLATE,
            section="", detail=said[:200], doc=text, source="chat_feedback"))
    except Exception:  # pragma: no cover — capture is best-effort
        out["reason"] = "capture failed"
    return out


def _shorten_persona(workspace_root, *, batch_id: Optional[str] = None) -> dict:
    """One step terser, and only one. `guided` -> `standard` -> `terse` ->
    stop. A person saying "too long" once is not asking to be stepped from
    guided to terse in a single turn.

    LEARNFIX1 1.2 — the turn's batch id is passed IN, so the persona step and
    the correction row that caused it ride ONE batch. Two batches for one
    sentence is how `undo` came to offer a menu."""
    from chat_persona import get_chat_persona
    from skill_config_writer import load_skill_config, save_skill_config

    ladder = {"guided": "standard", "standard": "terse", "terse": "terse"}
    persona = get_chat_persona(workspace_root)
    current = persona.get("brevity") or "standard"
    nxt = ladder.get(current, "standard")
    if nxt == current:
        return {"changed": False, "from": current, "to": current}
    stored = load_skill_config(workspace_root, "chat_persona") or {}
    cfg = stored.get("config") if isinstance(stored, dict) else None
    cfg = dict(cfg) if isinstance(cfg, dict) else {}
    before = dict(cfg)
    cfg["brevity"] = nxt
    # NIGHT 11a fix round (REVIEW_NIGHT11A_MERGED_TREE N-18 / L-3): the
    # receipt says "Say `undo` to put it back", so the write rides a batch
    # with the class `brain_undo` reverses (the previous persona config,
    # exactly - or the store cleared when there was none).
    import datetime as _dt
    batch_id = batch_id or ("cor-persona-"
                           + _dt.datetime.now(_dt.timezone.utc).strftime(
                               "%Y%m%dT%H%M%S%f"))
    save_skill_config(workspace_root, "chat_persona", cfg,
                      is_reconfigure=bool(stored), origin="correction_turn",
                      event_extra={"brain_batch_id": batch_id,
                                   "brain_change_class": PERSONA_CHANGE_CLASS,
                                   "prev_config_present": bool(stored),
                                   "prev_config": before})
    return {"changed": True, "from": current, "to": nxt, "batch_id": batch_id}


def handle_correction_turn(
    workspace_root,
    text: str,
    *,
    skill: str,
    recipient_id=None,
    domain: str = "",
    session_ref: Optional[str] = None,
    document: str = "",
) -> Optional[dict]:
    """Act on a correction in passing. Returns `{target, applied, receipt,
    recorded, batch_id, router_miss}` or None when the turn carries no
    correction.

    NEVER ASKS — there is no branch in this function that returns a question,
    and the suite pins that the receipt text contains no question mark.

    NEVER OFFERS A MENU EITHER (LEARNFIX1 1.2). ONE batch id is minted for
    the whole turn and handed to every write it makes, so `undo` afterwards
    resolves through `brain_undo.resolve_undo_phrase` like any other batch
    and the caller has a `batch_id` to name. The three-way fork the chat
    produced on 2026-09-16 was the absence of this, not a policy."""
    found = classify_correction_turn(text)
    if found is None:
        return None
    target = found["target"]
    import voice_corrections as _vc
    batch_id = _vc._mint_in_passing_batch()
    result = {"target": target, "intent": found["intent"], "applied": False,
              "receipt": "", "recorded": False, "batch_id": None,
              "router_miss": None}
    logged = _record_correction(
        workspace_root, skill=skill, said=found["said"], target=target,
        recipient_id=recipient_id, domain=domain, batch_id=batch_id)
    result["recorded"] = bool(logged.get("recorded"))
    result["batch_id"] = logged.get("batch_id")
    result["correction_fingerprint"] = logged.get("fingerprint")

    if target == TARGET_TEMPLATE:
        captured = _capture_structure(workspace_root, skill=skill,
                                      said=found["said"], document=document)
        result["detail"] = captured
        result["applied"] = bool(captured["captured"])
        result["receipt"] = (
            "Got it — that shape is on file, and once you have shown it to me "
            "a few times it becomes the one I start from."
            if captured["captured"] else
            "Noted in your own words. Hand me the document itself and I will "
            "keep its shape.")
        return result

    if target == TARGET_ROUTER:
        meant = found["payload"] or ""
        if meant:
            try:
                from router_miss import log_router_miss

                result["router_miss"] = log_router_miss(
                    workspace_root, found["said"], meant, meant,
                    routed_to=skill, session_ref=session_ref,
                    source_skill=skill)
                result["applied"] = True
            except Exception as exc:  # pragma: no cover
                result["error"] = f"{type(exc).__name__}: {exc}"
        result["receipt"] = (
            "Noted — that belonged with " + (meant or "another part of this")
            + ", and I have logged the miss so the routing learns it."
            if meant else
            "Noted — I have logged that this went to the wrong place.")
        return result

    if target == TARGET_PERSONA:
        try:
            moved = _shorten_persona(workspace_root, batch_id=batch_id)
            result["applied"] = bool(moved.get("changed"))
            result["detail"] = moved
            if moved.get("changed") and not result["batch_id"]:
                # Fix round 1 (F-3's other half). The correction row can be a
                # genuine same-turn duplicate — already said, never taken
                # back — and then `log_in_passing` writes nothing and hands
                # back no batch. If the persona STILL moved a rung, that
                # write is real and reversible, so the turn names ITS batch.
                # A receipt says `undo` only when something can be named.
                result["batch_id"] = moved.get("batch_id") or batch_id
            # Both endings carry the undo now, because both WROTE something:
            # the ladder step when there was one, and the lesson itself when
            # the answers were already as short as they go. A receipt that
            # promises `undo` on one branch and not the other is how a person
            # learns not to trust the word.
            result["receipt"] = (
                _vc.IN_PASSING_RECEIPT
                if moved.get("changed") else
                "Already as short as it goes, and I have noted you said so. "
                "Say `undo` to put it back."
                if result["recorded"] else
                # Fix round 1 (F-3, the same rule one step further). Nothing
                # moved and nothing was written — the lesson is already on
                # file and the answers are already as short as they go — so
                # the sentence does not offer a way back to a place nothing
                # left. A receipt that promises `undo` over an act that did
                # not happen is what taught the 09-16 chat to improvise.
                "Already as short as it goes, and you have told me so "
                "before — nothing to change.")
        except Exception as exc:  # pragma: no cover
            result["error"] = f"{type(exc).__name__}: {exc}"
            result["receipt"] = "Noted — I will keep it shorter."
        return result

    if target == TARGET_SETTINGS:
        # The smallest scope that fits: the complaint is recorded against the
        # surface that carried it, and the surface's own settings lane (CUSTOM2)
        # is what reads it. Nothing is muted here on one sentence — muting a
        # class of thing is a confirm-tier act by A1, and this door never asks.
        #
        # `applied` STAYS FALSE, and that is the point. Recording is not
        # applying, and a flag that says otherwise makes a caller believe the
        # surface changed. Nothing in this plugin reads a `settings`-target
        # correction yet: the one reader belongs in the settings lane, and
        # until it exists this door is honest about doing only half the job.
        result["applied"] = False
        result["receipt"] = (
            "Noted — that should not have been on this surface, and I have "
            "recorded it against this one.")
        return result

    # TARGET_PROFILE
    result["applied"] = result["recorded"]
    payload = found["payload"]
    result["receipt"] = (
        "Got it — " + payload + ". That is on file as your own words, so it "
        "outranks anything I work out on my own."
        if payload else
        "Got it — recorded in your own words, which outranks anything I work "
        "out on my own.")
    return result


__all__ = [
    "CONTRACT_SKILLS", "CONTRACT_DOC", "CONTRACT_TOKEN",
    "TARGET_PERSONA", "TARGET_SETTINGS", "TARGET_PROFILE", "TARGET_ROUTER",
    "TARGET_TEMPLATE", "TARGETS", "ORIGIN_ASKED", "exemplar_kind_for_skill",
    "classify_correction_turn", "handle_correction_turn",
]
