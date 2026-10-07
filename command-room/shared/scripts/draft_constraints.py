#!/usr/bin/env python3
"""The per-draft read — what this person already corrected, loaded BEFORE the
draft is composed and treated as a constraint (SPEC_SURFACES2 §1).

WHAT WAS WRONG. The corrections log is written at Sent-reconcile and read once
a month by a pass that never ran. Nothing read it at COMPOSE time. So the same
recipient could cut a five-paragraph draft down to one, twice, and the next
draft for that same person came back at five paragraphs — the product had the
evidence on disk and asked the person to make the same edit a third time.

WHAT THIS IS. One reader the five drafting skills call before composing:

    from draft_constraints import load_draft_constraints
    c = load_draft_constraints(workspace_root, "email-writer",
                               recipient_id=person_id, domain=domain)

It returns the last N corrections for THIS recipient (falling back to this
domain, then to the skill at large), the skill's learned voice-block override,
and the two constraints that are machine-checkable: a paragraph ceiling and a
banned-phrase list. `apply_draft_constraints` is the enforcement — a composed
draft goes through it before it is shown, so the constraint is a fact about
the output rather than a hope about the prompt.

WHAT A CEILING IS SCOPED TO. A length ceiling is a fact about a RELATIONSHIP —
this person wants one paragraph from you — and it is never inherited by
someone who has never corrected anything. The scope ladder falls back to the
skill at large so a banned phrase learned across the skill still reaches every
draft (a phrase you keep deleting is about how the product writes, not about
who it is writing to), but `max_paragraphs` is taken ONLY from the recipient
and domain rungs. Without that split, one recipient who cuts five paragraphs
to one twice sets a one-paragraph ceiling on every draft the skill ever
composes, to anyone — a cold first note included.

STATED OUTRANKS LEARNED. Everything here is LEARNED. A constraint the person
has stated (the voice block's own non-learned lines, an explicit instruction
in the request) wins; this reader never overrides one, it only adds a ceiling
where the person's own edits set one.

stdlib only. Never raises into a compose path — an unreadable log means no
constraints, which is exactly the pre-LEARN1 behaviour.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# The five skills that compose FOR a named recipient. Each carries the
# instruction paragraph naming this reader; the guard pins that.
DRAFT_SKILLS = ("email-writer", "follow-up-ritual", "memo-writer",
                "intro-broker", "call-prep")

# How many prior corrections for one recipient are enough to be a pattern
# rather than a mood. Two: the person did the same edit twice, unprompted.
DEFAULT_LOOKBACK = 5
PARAGRAPH_FLOOR = 2

# The rungs a PARAGRAPH CEILING may be learned from. `skill` is deliberately
# absent: a ceiling is a fact about one relationship, and a recipient the
# person has never corrected must get the product's own default length.
# Banned phrases keep the whole ladder — see the module docstring.
PARAGRAPH_SCOPES = ("recipient", "domain")

_PARA_NOTE_RE = re.compile(r"paragraph count\s*(\d+)\s*->\s*(\d+)", re.IGNORECASE)


def _match_scope(nrow: dict, recipient_id, domain) -> Optional[str]:
    """Which scope this correction belongs to for this draft: the recipient
    themselves, their domain, or the skill at large. Most specific wins."""
    if recipient_id and nrow.get("recipient_id") == recipient_id:
        return "recipient"
    if domain and nrow.get("domain") == domain:
        return "domain"
    return "skill"


def load_draft_constraints(
    workspace_root,
    skill: str,
    *,
    recipient_id=None,
    domain=None,
    limit: int = DEFAULT_LOOKBACK,
) -> dict:
    """The last `limit` corrections for this recipient/domain plus the skill's
    learned voice-block override, folded into constraints.

    Returns `{skill, recipient_id, domain, scope, corrections, override_block,
    max_paragraphs, banned_phrases, lines}`. `lines` is the plain-English
    form a composer is told to obey; `max_paragraphs` and `banned_phrases`
    are what `apply_draft_constraints` enforces mechanically."""
    out = {"skill": skill, "recipient_id": recipient_id, "domain": domain,
           "scope": None, "corrections": [], "override_block": None,
           "max_paragraphs": None, "banned_phrases": [], "lines": []}
    try:
        import voice_corrections as vc
    except Exception:  # pragma: no cover
        return out
    try:
        raw = vc.load_corrections(workspace_root, skill)
    except Exception:  # pragma: no cover — never raises into a compose path
        raw = []
    scoped: Dict[str, List[dict]] = {"recipient": [], "domain": [], "skill": []}
    for row in raw:
        nrow = vc.normalize_correction_row(row)
        if nrow is None:
            continue
        scoped[_match_scope(nrow, recipient_id, domain)].append(nrow)
    # The lookback counts corrections that SAY something, not raw rows. A
    # dash deletion is already the product default and a reordering carries no
    # constraint; letting those spend the window would push the phrase the
    # person actually keeps deleting out of view — the exact failure this
    # reader exists to close.
    for scope in ("recipient", "domain", "skill"):
        if not scoped[scope]:
            continue
        out["scope"] = scope
        bearing = [r for r in scoped[scope]
                   if vc.classify_correction_op(r)[0]
                   in (vc.OP_BAN_PHRASE, vc.OP_SHORTEN)]
        out["corrections"] = (bearing or scoped[scope])[-max(1, int(limit)):]
        break
    try:
        override = vc.load_voice_block_override(workspace_root, skill)
    except Exception:  # pragma: no cover
        override = None
    if override:
        out["override_block"] = override.get("markdown")

    caps: List[int] = []
    banned: List[str] = []
    for nrow in out["corrections"]:
        op, arg = vc.classify_correction_op(nrow)
        if op == vc.OP_BAN_PHRASE and arg and arg not in banned:
            banned.append(arg)
        elif op == vc.OP_SHORTEN and isinstance(arg, int):
            caps.append(arg)
        elif op == vc.OP_SHORTEN:
            m = _PARA_NOTE_RE.search(nrow.get("notes") or "")
            if m:
                caps.append(int(m.group(2)))
    # The ceiling is what the person actually cut TO, not the least they ever
    # cut to on a whim: the cap applies only where the same target repeats,
    # and only where the corrections are about THIS recipient or their domain.
    # A ceiling pooled from the skill at large would hand a one-paragraph
    # draft to someone who has never asked for one.
    repeated = [c for c in set(caps) if caps.count(c) >= PARAGRAPH_FLOOR]
    if repeated and out["scope"] in PARAGRAPH_SCOPES:
        out["max_paragraphs"] = min(repeated)
    out["banned_phrases"] = banned
    lines = []
    if out["max_paragraphs"]:
        n = out["max_paragraphs"]
        lines.append(
            "Keep this to " + str(n) + (" paragraph" if n == 1 else " paragraphs")
            + " — that is what they cut the last "
            + str(len([c for c in caps if c == n])) + " drafts down to.")
    if banned:
        lines.append("Do not use: " + ", ".join('"' + b + '"' for b in banned)
                     + " — they took "
                     + ("it" if len(banned) == 1 else "them")
                     + " out before.")
    out["lines"] = lines
    return out


def _split_paragraphs(text: str) -> List[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "")]
    return [p for p in parts if p]


def apply_draft_constraints(draft: str, constraints: dict) -> str:
    """Enforce the learned constraints on a composed draft. Pure.

    Paragraph ceiling: paragraphs past the ceiling are JOINED into the last
    kept paragraph rather than dropped — the person shortened the shape, they
    did not ask for content to disappear.

    Banned phrases: removed with their surrounding whitespace tidied. A phrase
    the person stated they want kept (a Taboos carve-out in the override) is
    never removed here — that read belongs to `voice_corrections`, and this
    function only ever removes phrases the caller put in `banned_phrases`."""
    text = draft or ""
    for phrase in (constraints or {}).get("banned_phrases") or []:
        if not phrase:
            continue
        # NIGHT 11a fix round (N-4, L-4): word boundaries - a learned "just"
        # used to turn "adjust" into "ad".
        text = re.sub(r"(?<![A-Za-z0-9])" + re.escape(phrase) + r"(?![A-Za-z0-9])[ \t]*",
                      "", text, flags=re.IGNORECASE)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r" +([,.;!?])", r"\1", text)
    cap = (constraints or {}).get("max_paragraphs")
    if isinstance(cap, int) and cap > 0:
        paras = _split_paragraphs(text)
        if len(paras) > cap:
            kept = paras[: cap - 1]
            kept.append(" ".join(paras[cap - 1:]))
            text = "\n\n".join(kept)
    return text.strip()


def constraint_block(constraints: dict) -> str:
    """The plain-English block a composer is instructed to obey verbatim.
    Empty string when nothing has been learned — a heading with nothing under
    it is padding, and the design rule says an empty section says so or says
    nothing."""
    lines = (constraints or {}).get("lines") or []
    if not lines:
        return ""
    return "\n".join("- " + line for line in lines)


__all__ = [
    "DRAFT_SKILLS", "DEFAULT_LOOKBACK", "PARAGRAPH_FLOOR", "PARAGRAPH_SCOPES",
    "load_draft_constraints", "apply_draft_constraints", "constraint_block",
]
