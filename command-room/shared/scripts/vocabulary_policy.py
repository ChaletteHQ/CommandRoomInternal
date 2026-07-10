#!/usr/bin/env python3
"""
vocabulary_policy.py — THE banned-vocabulary list, one file, one owner
(v4.6.1 S3; FINDINGS F-53 P3a).

Before S3 the leak gate (docx_leak_scanner) and the voice gate
(voice_tell_detector) each carried their own hardcoded word list —
disjoint by accident. Live consequence, same day (F-53 P3a vs F-50 P3):
the word "leverage" was hard-blocked in a docx brief and LED an outbound
email draft two hours later. Same word, one surface bans it, another
ships it.

This module is the single source both gates read:

  MARKETING_WORDS   corporate vocabulary the user's calibrated voice never
                    uses. Blocked in customer-facing deliverables (leak
                    gate — hard fail at docx save) AND in outbound drafts
                    (voice gate — fail severity; the composer rewrites
                    until clean). A client whose calibrated Voice Block
                    demonstrably uses one of these words carves it out via
                    allow_phrases — the gate is a default, not a cage.

  INTERNAL_VOCAB    Command Room's internal architecture vocabulary —
                    never appears on ANY customer-visible surface: chat
                    narration, widget text, docx bodies, or the skill
                    descriptions Cowork renders in the Plugins UI (the
                    F-05 leak: "Internal dispatch layer ... payload ..."
                    shown to the customer as a skill description).
                    run_banned_terms_guard_test scans shipped prose
                    against this list so the F-05 class can't ship again;
                    chat_output_validator enforces it on rendered chat.

Editing rules: adding a word here propagates to BOTH gates and the guard
test — that is the point. Never re-add a word to a gate's local list;
if a gate needs a gate-specific pattern (paths, IDs, phase labels), that
stays in the gate. Only shared VOCABULARY lives here.
"""
from __future__ import annotations

from typing import List, Tuple

# ---------------------------------------------------------------------------
# Marketing / corporate vocabulary — both voice-facing gates read this.
# ---------------------------------------------------------------------------

MARKETING_WORDS: List[str] = [
    "ecosystem",
    "synergy",
    "leverage",
    "holistic",
    "stakeholder",
]


def marketing_patterns() -> List[Tuple[str, str]]:
    """(rule_name, word-boundary regex) rows for the leak gate. Rule names
    keep the historical `marketing_<word>` spelling so receipts, findings,
    and tests stay comparable across versions."""
    return [(f"marketing_{w}", rf"\b{w}\b") for w in MARKETING_WORDS]


def vocabulary_fail_rules() -> List[Tuple[str, str, str]]:
    """(rule_id, canonical_phrase, hint) rows for the voice gate — same
    shape as voice_tell_detector's _FAIL_PHRASES so they compile through
    the same phrase machinery (case-insensitive, word-boundary,
    allow_phrases carve-out applies)."""
    return [
        (
            f"vocab_{w}",
            w,
            "corporate vocabulary — the calibrated voice never says this; "
            "use the plain word",
        )
        for w in MARKETING_WORDS
    ]


# ---------------------------------------------------------------------------
# Internal architecture vocabulary — customer surfaces, all of them.
# ---------------------------------------------------------------------------

# (term_id, regex, where it leaked). Regexes are word-boundary-anchored and
# deliberately PRECISE: plain-English uses of nearby words stay legal
# ("orchestrator" as a role word, "canonical names" in a project list,
# "dispatched" as a verb). Each row cites the dogfood finding that put it
# here — this list is evidence-driven, not speculative.
INTERNAL_VOCAB: List[Tuple[str, str, str]] = [
    ("substrate", r"\bsubstrate\b",
     'F-14: "closing it in the substrate" on a closure ack'),
    ("dispatch_layer", r"\bdispatch(?:er\b| layer\b)",
     'F-05: "Internal dispatch layer" in the Plugins-UI skill description'),
    ("payload", r"\bpayload\b",
     "F-05: same description; also raw apply-choices narration"),
    ("canonical_machinery", r"\bcanonical (?:renderer|writer|reader|path)s?\b",
     'F-14: "through the canonical renderer" narrated in chat'),
    ("audit_marker", r"\baudit marker\b",
     'F-14: "Writing the audit marker, then posting"'),
    ("run_summary_tag", r"</?run-summary>",
     "F-47 P2c / F-50 P3: raw <run-summary> tag rendered as chat text"),
    ("bootloader", r"\bbootloader\b",
     "enable-schedules description (Plugins UI)"),
    ("fire_marker", r"\bfire-marker\b",
     "apply-choices internals; never say it to the customer"),
    ("bare_seq_ref", r"\bseq \d+\b",
     "F-14 pile: raw event seq numbers in Sources lines"),
]


def internal_vocab_patterns() -> List[Tuple[str, str]]:
    """(term_id, regex) rows for scanners that don't want the evidence
    column (chat_output_validator, the guard test)."""
    return [(tid, rx) for tid, rx, _ in INTERNAL_VOCAB]


__all__ = [
    "MARKETING_WORDS",
    "INTERNAL_VOCAB",
    "marketing_patterns",
    "vocabulary_fail_rules",
    "internal_vocab_patterns",
]
