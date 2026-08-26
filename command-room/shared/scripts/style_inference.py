#!/usr/bin/env python3
"""Style inference — derive a proposed per-client style profile from the
client's OWN record (SPEC STYLE1 §4 step 2, rev 2: inference-first, no
interview questions).

DIVISION OF LABOR (the sent_capture.py posture)
-----------------------------------------------
The SKILL does the live reading — it fetches sent mail through the declared
backend seam and reads transcripts through the transcript tool, then distills
what it saw into the documented `signals` dict below. THIS module does only
the exact parts: deterministic scoring of (on-disk stores + handed-in
signals) into evidence-carrying proposals. It fetches nothing, and it WRITES
NOTHING — `propose()` is a pure read (AC-2). The write happens in the
caller's confirm flow via skill_config_writer + build_style_changed_event.

SIGNAL SOURCES, in precedence order (SPEC STYLE1 DD-3):
  1. EXPLICIT — prior style_changed events with origin asked/recalibrated/
     inferred_confirmed: a knob the client already ruled on is NEVER
     re-proposed by inference (latest explicit beats learned).
  2. HANDED-IN LIVE SIGNALS — the skill's distillation of the sent-mail scan
     and transcript spoken-turns (shape documented on `propose`).
  3. ON-DISK DERIVED STORES — _hq/COMMUNICATION_PROFILE.md mechanics
     (written by onboarding's voice scan) and the per-skill voice-correction
     logs (draft-shortening ratio).

HONESTY RULES
-------------
Every proposal carries an evidence sentence and a confidence in [0,1].
Below PROPOSE_THRESHOLD the knob is SUPPRESSED (returned with a reason),
never guessed. Conflicting signals for the same knob suppress it too —
"the signals disagree" is a finding, not a tie to break silently.

stdlib only.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from chat_persona import DEFAULT_CHAT_PERSONA, get_chat_persona  # noqa: E402

PROPOSE_THRESHOLD = 0.7

# Sent-mail mechanics cut points (median words per email). Tuned to the
# COMMUNICATION_PROFILE scale: an executive who writes 40-word emails wants
# 40-word answers.
_TERSE_MAX_WORDS = 45
_GUIDED_MIN_WORDS = 140

_SPOKEN_MIN_TURNS = 8          # fewer analyzed turns than this = no signal
_SPOKEN_DOMINANCE = 2.0        # one ask style must dominate 2:1 to propose

_CORRECTIONS_MIN = 6           # fewer logged corrections = no signal
_SHORTEN_DOMINANCE = 0.7       # ≥70% of length-class corrections shorten


def _read_events(ws: Path) -> List[dict]:
    p = ws / "_hq" / "data" / "events.jsonl"
    if not p.exists():
        return []
    out: List[dict] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    except OSError:
        return []
    return out


def explicitly_ruled_knobs(workspace_root: Union[str, os.PathLike]) -> set:
    """Knobs the client already ruled on (origin != inferred_provisional).
    Inference never re-proposes these — explicit beats learned, always."""
    ruled: set = set()
    for row in _read_events(Path(workspace_root)):
        if row.get("type") != "style_changed":
            continue
        data = row.get("data") or {}
        if data.get("origin") == "inferred_provisional":
            continue
        for change in data.get("changes") or []:
            knob = change.get("knob")
            if knob:
                ruled.add(str(knob))
    return ruled


def _comm_profile_mechanics(ws: Path) -> Dict[str, Any]:
    """Tolerant scrape of _hq/COMMUNICATION_PROFILE.md for the mechanics the
    onboarding voice scan records. Absent file / unparseable = {} — never a
    guess."""
    p = ws / "_hq" / "COMMUNICATION_PROFILE.md"
    if not p.exists():
        return {}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: Dict[str, Any] = {}
    m = re.search(r"median\s+email\s+length\D{0,20}(\d+)\s*words", text, re.I)
    if m:
        out["median_words"] = int(m.group(1))
    m = re.search(r"median\s+sentence\s+length\D{0,20}(\d+)\s*words", text, re.I)
    if m:
        out["median_sentence_words"] = int(m.group(1))
    if re.search(r"no\s+greeting|skips?\s+the\s+greeting", text, re.I):
        out["greeting"] = "none"
    elif re.search(r"greeting[:\s]", text, re.I):
        out["greeting"] = "present"
    if re.search(r"emoji[:\s]+(frequent|often|regular)", text, re.I):
        out["emoji"] = "frequent"
    elif re.search(r"emoji[:\s]+(never|none|rare)", text, re.I):
        out["emoji"] = "rare"
    return out


def _shortening_ratio(ws: Path) -> Optional[float]:
    """Fraction of length-classified voice corrections that SHORTENED the
    draft, across all per-skill correction logs. None below the evidence
    floor."""
    vdir = ws / "_hq" / "voice"
    if not vdir.is_dir():
        return None
    shorten = lengthen = 0
    for p in sorted(vdir.glob("corrections-*.jsonl")):
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            cls = str(row.get("classification") or row.get("class") or "")
            if "shorten" in cls or cls == "cut":
                shorten += 1
            elif "lengthen" in cls or "expand" in cls:
                lengthen += 1
    total = shorten + lengthen
    if total < _CORRECTIONS_MIN:
        return None
    return shorten / total


def _mk(knob: str, value: Any, confidence: float, evidence: str) -> dict:
    return {"layer": "chat_persona", "knob": knob, "value": value,
            "confidence": round(confidence, 2), "evidence": evidence}


def _suppress(knob: str, reason: str) -> dict:
    return {"knob": knob, "reason": reason}


def propose(workspace_root: Union[str, os.PathLike],
            signals: Optional[dict] = None,
            window_days: int = 60) -> Dict[str, List[dict]]:
    """Pure function: (workspace stores, handed-in signals) → proposals.

    `signals` — the SKILL's distillation of live reads; every key optional:
      {
        "sent_mail": {"emails_analyzed": int, "median_words": int,
                       "greeting_rate": float,      # 0..1 share with greeting
                       "exclamation_rate": float,   # per email
                       "emoji_rate": float},        # per email
        "spoken":    {"turns_analyzed": int,
                       "bottom_line_asks": int,     # "just the number/answer"
                       "walkthrough_asks": int},    # "walk me through / why"
        "language":  {"non_english_share": float, "language": "es"},
      }

    Returns {"proposals": [...], "suppressed": [...]} — evidence-carrying,
    threshold-gated, conflict-suppressing. NEVER writes anything anywhere.
    """
    ws = Path(workspace_root)
    signals = signals or {}
    # REVIEW_STYLE1 F-2 — the signal dict comes from a SKILL's live
    # distillation, which means a model composed it: malformed shapes are a
    # WHEN, not an if. A proposer that raises on garbage takes the whole
    # style pass down with it, so the contract is: unreadable signal blocks
    # read as absent, numeric fields that are not numbers read as absent,
    # and the function never raises and never writes. Skip-not-fail — the
    # same posture as every transcript check.
    if not isinstance(signals, dict):
        signals = {}
    signals = {k: v for k, v in signals.items() if isinstance(v, dict)}

    def _num(block: dict, key: str):
        """A numeric field or None — never a string, bool, or negative."""
        v = block.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        if v < 0 or v != v or v in (float("inf"), float("-inf")):
            return None
        return v
    ruled = explicitly_ruled_knobs(ws)
    current = get_chat_persona(ws)
    proposals: List[dict] = []
    suppressed: List[dict] = []

    # ---- brevity: sent-mail median words, corroborated / contradicted by
    # spoken asks and correction-shortening. -------------------------------
    mail = signals.get("sent_mail") or {}
    mech = _comm_profile_mechanics(ws)
    median = _num(mail, "median_words")
    if median is None:
        median = mech.get("median_words")
    n_mail = _num(mail, "emails_analyzed") or (30 if mech else 0)
    spoken = signals.get("spoken") or {}
    turns = _num(spoken, "turns_analyzed") or 0
    bl = _num(spoken, "bottom_line_asks") or 0
    wt = _num(spoken, "walkthrough_asks") or 0
    shorten = _shortening_ratio(ws)

    terse_votes, guided_votes, brevity_ev = 0, 0, []
    if isinstance(median, int) and n_mail >= 5:
        if median <= _TERSE_MAX_WORDS:
            terse_votes += 1
            brevity_ev.append(
                f"your last {n_mail} sent emails run a median of "
                f"{median} words")
        elif median >= _GUIDED_MIN_WORDS:
            guided_votes += 1
            brevity_ev.append(
                f"your sent emails run long — median {median} words")
    if turns >= _SPOKEN_MIN_TURNS and (bl or wt):
        if bl >= wt * _SPOKEN_DOMINANCE:
            terse_votes += 1
            brevity_ev.append(
                f"in meetings you asked for the bottom line {bl}× vs "
                f"{wt}× for a walkthrough")
        elif wt >= bl * _SPOKEN_DOMINANCE:
            guided_votes += 1
            brevity_ev.append(
                f"in meetings you asked to be walked through {wt}× vs "
                f"{bl}× for just the answer")
    if shorten is not None:
        if shorten >= _SHORTEN_DOMINANCE:
            terse_votes += 1
            brevity_ev.append(
                f"{int(shorten * 100)}% of your draft edits made the text "
                "shorter")
        elif shorten <= 1 - _SHORTEN_DOMINANCE:
            guided_votes += 1
            brevity_ev.append(
                f"{int((1 - shorten) * 100)}% of your draft edits added "
                "detail")

    if "brevity" in ruled:
        suppressed.append(_suppress("brevity", "explicitly ruled by the "
                                    "client — inference never overrides"))
    elif terse_votes and guided_votes:
        suppressed.append(_suppress("brevity",
                                    "signals conflict: " + "; ".join(brevity_ev)))
    elif terse_votes or guided_votes:
        value = "terse" if terse_votes else "guided"
        votes = max(terse_votes, guided_votes)
        conf = min(0.95, 0.55 + 0.15 * votes)
        if conf >= PROPOSE_THRESHOLD:
            if current["brevity"] != value:
                proposals.append(_mk("brevity", value, conf,
                                     "; ".join(brevity_ev)))
        else:
            suppressed.append(_suppress(
                "brevity", f"confidence {conf:.2f} below threshold — "
                "staying on the default rather than guessing"))
    # no signal at all: silently default (nothing proposed, nothing to explain)

    # ---- explanation_depth rides the spoken evidence alone. ---------------
    if "explanation_depth" in ruled:
        suppressed.append(_suppress("explanation_depth", "explicitly ruled"))
    elif turns >= _SPOKEN_MIN_TURNS and wt >= max(1, bl) * _SPOKEN_DOMINANCE:
        conf = min(0.9, 0.6 + 0.02 * wt)
        if conf >= PROPOSE_THRESHOLD and current["explanation_depth"] != "teaching":
            proposals.append(_mk(
                "explanation_depth", "teaching", conf,
                f"you asked to be walked through the reasoning {wt}× across "
                f"{turns} analyzed speaking turns"))

    # ---- formality: greeting habits + exclamation/emoji rate. -------------
    greeting_rate = _num(mail, "greeting_rate")
    excl = _num(mail, "exclamation_rate")
    emoji = _num(mail, "emoji_rate")
    informal_votes, formal_votes, form_ev = 0, 0, []
    if greeting_rate is None and mech.get("greeting") == "none":
        greeting_rate = 0.0
    if isinstance(greeting_rate, (int, float)) and n_mail >= 5:
        if greeting_rate <= 0.2:
            informal_votes += 1
            form_ev.append("you skip greetings in "
                           f"{int((1 - greeting_rate) * 100)}% of emails")
        elif greeting_rate >= 0.9:
            formal_votes += 1
            form_ev.append("you open with a greeting in "
                           f"{int(greeting_rate * 100)}% of emails")
    if isinstance(excl, (int, float)) and excl >= 1.0:
        informal_votes += 1
        form_ev.append("exclamation points are a habit "
                       f"(~{excl:.1f} per email)")
    if isinstance(emoji, (int, float)) and emoji >= 0.5:
        informal_votes += 1
        form_ev.append("emoji show up in most emails")
    if "formality" in ruled:
        suppressed.append(_suppress("formality", "explicitly ruled"))
    elif informal_votes and formal_votes:
        suppressed.append(_suppress("formality",
                                    "signals conflict: " + "; ".join(form_ev)))
    elif informal_votes >= 2 or formal_votes >= 2:
        value = "informal" if informal_votes else "formal"
        conf = min(0.9, 0.55 + 0.15 * max(informal_votes, formal_votes))
        if conf >= PROPOSE_THRESHOLD and current["formality"] != value:
            proposals.append(_mk("formality", value, conf, "; ".join(form_ev)))

    # ---- humor_ok rides emoji + exclamation together, weakly. -------------
    if "humor_ok" not in ruled and isinstance(emoji, (int, float)) \
            and isinstance(excl, (int, float)) and emoji >= 0.5 and excl >= 1.0:
        proposals.append(_mk(
            "humor_ok", True, 0.7,
            "your own emails carry emoji and exclamation points freely"))

    # ---- language: dominant non-English share in the client's own writing.
    lang = signals.get("language") or {}
    share = _num(lang, "non_english_share")
    tag = lang.get("language")
    if "language" in ruled:
        suppressed.append(_suppress("language", "explicitly ruled"))
    elif isinstance(share, (int, float)) and share >= 0.6 and tag:
        proposals.append(_mk(
            "language", str(tag), min(0.95, 0.5 + share * 0.5),
            f"{int(share * 100)}% of your own writing is in '{tag}'"))

    return {"proposals": proposals, "suppressed": suppressed}


__all__ = ["propose", "explicitly_ruled_knobs", "PROPOSE_THRESHOLD"]
