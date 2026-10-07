#!/usr/bin/env python3
"""SPEC INSIGHT-RULE1 — the three-way classification every rendered number or
outcome must carry, and the scan that catches the ones that don't.

M's ruling, 2026-09-07 (SPEC_SURFACES2 Standing rules, restated by M as the
rule this lane exists to enforce): *"a number you have not observed is not a
number."* Every figure or completed-outcome sentence a surface renders has to
resolve to one of three kinds:

  FACT         a ledger event backs it. Carries `refs` (event seqs) that
               `validate_claim` can check against an index built from the
               events actually on disk.
  OBSERVATION  a projection over a window — a count that is true of the record
               as read, not asserted from taste. Carries `count` + `window`.
  READING      an interpretation. Needs a FLOOR of >= 3 instances over weeks
               (never one or two data points dressed up as a pattern) PLUS a
               basis line saying what it rests on — SPEC_SURFACES2's own
               words, restated here as the one place that enforces them in
               code rather than prose.

Never a score (the fence is `eod_synthesis.assert_no_score` / `score_tokens_in`
— reused here, not re-derived: ONE score fence for the whole plugin, exactly
the F-53 P3a discipline `docx_leak_scanner.py` already follows for marketing
words). Never a comparison to another person (`comparison_tokens_in`, new
here — nothing upstream owned this ban yet).

WHY THIS IS A SEPARATE MODULE AND NOT MORE LINES IN `eod_synthesis.py`
-----------------------------------------------------------------------
`eod_synthesis.sentence()` grounds ONE surface (End of Day) against its OWN
rows. This module is the shared checker SPEC_SURFACES2 §8 asks for — brief,
End of Day, the wrap, the coach, the profile all render numbers and readings,
and a rule that lives inside one of their modules is a rule the other four
never see. `claims.py` takes no dependency on any surface; every surface that
composes a number-or-outcome sentence is expected to build it through
`make_claim` (the write-time gate: a claim that cannot earn its declared kind
raises `ClaimError` before it ever reaches a render), and every surface that
persists rendered text alongside its claims can be checked post-render with
`assert_surface_resolved` (the read-time gate — SPEC_SURFACES2's "a scan over
every rendered surface on the fixtures").

THE FENCES (proven by REMOVAL, tests/run_guard_insightrule1_test.py [9])
-----------------------------------------------------------------------------
  1. THE FLOOR OF THREE. `_classify` refuses a `basis`-carrying claim with
     `instances < FLOOR_INSTANCES` — remove that guard and a one-instance
     claim renders as a READING.
  2. THE OUTCOME HALF OF THE LEDGER-RESOLUTION SCAN. `scan_surface` calls
     `unresolved_outcomes` over the raw rendered text — remove that call and
     an outcome sentence with no claim behind it passes the scan clean.
  3-6. FIX ROUND 1 (REVIEW_INSIGHTRULE1 F-1, 2026-09-08) — the review's
     vacuity sweep found the OTHER four defect producers `scan_surface`
     wires in were pinned as functions but not as wiring: remove the
     `unresolved_figures` call, the `score_tokens_in` call, the
     `comparison_tokens_in` call, or the `validate_claim` loop
     (the `invalid_claim` producer), and each one individually still passed
     the suite green, because no fixture could ONLY pass through that one
     call. Four fixtures now close that (see section [6]) and each removal
     reds by name.

Extends the leak scan's harness (`docx_leak_scanner.py`'s pattern-list +
word-boundary-regex shape): `comparison_tokens_in` here is the same kind of
compiled-pattern list as `connector_id_patterns.py` / `surface_leak_patterns.py`
feed into `_FORBIDDEN_PATTERNS`, kept in its own module for the same reason
those are — the list is the thing that changes, the scanner shouldn't have to.

stdlib only.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    from eod_synthesis import score_tokens_in  # the ONE score fence, reused
except ImportError:  # pragma: no cover — direct-path import fallback
    sys.path.insert(0, str(_HERE))
    from eod_synthesis import score_tokens_in


# --- the three kinds --------------------------------------------------------

FACT = "fact"
OBSERVATION = "observation"
READING = "reading"
_KINDS = frozenset({FACT, OBSERVATION, READING})

# A reading needs a floor of THREE instances over weeks (SPEC_SURFACES2
# Standing rules + SPEC §8). This is the number the removal fence targets.
FLOOR_INSTANCES = 3


class ClaimError(RuntimeError):
    """A claim's own fields cannot support the kind it is declared (or
    inferred) as. Raised AT THE WRITE, in the composer that built the
    sentence — never downstream, and never caught inside this module."""


class UnresolvedClaimError(RuntimeError):
    """A rendered surface carries a figure, an outcome, a score, or a
    comparison with nothing behind it. Raised by `assert_surface_resolved`,
    the read-time gate a renderer calls before it shows or persists a
    surface. Lists every defect `scan_surface` found."""


def _classify(ledger_refs: list[str], count: Optional[int], window: Optional[str],
              basis: Optional[str], instances: Optional[int]) -> str:
    """Which of the three kinds these fields can support — or ClaimError."""
    if ledger_refs:
        return FACT
    if count is not None and window:
        return OBSERVATION
    if basis and instances is not None:
        # THE FLOOR (fence 1 — see module docstring). A one- or two-instance
        # claim is not a pattern; refusing it here is what stops a reading
        # from being minted out of a single data point anywhere upstream.
        if instances < FLOOR_INSTANCES:
            raise ClaimError(
                f"a reading needs a floor of {FLOOR_INSTANCES} instances "
                f"over weeks; this one carries {instances}")
        return READING
    raise ClaimError(
        "a claim needs a ledger ref (fact), a projection count + window "
        "(observation), or a basis line + a floor of "
        f"{FLOOR_INSTANCES} instances (reading) — none of those were given")


def make_claim(text: str, *, kind: Optional[str] = None,
                refs: Optional[Iterable[Any]] = None,
                count: Optional[int] = None, window: Optional[str] = None,
                basis: Optional[str] = None,
                instances: Optional[int] = None) -> dict:
    """Build one classified claim: `{text, kind, refs, count, window, basis,
    instances}`. `kind` is INFERRED from whichever fields are populated; if
    the caller also names a `kind`, it must match what the fields actually
    support — a caller cannot declare a reading and skip the floor by also
    passing a fact ref, and cannot declare a fact by naming it without a ref.

    This is the ONE constructor every surface is expected to call before it
    renders a sentence with a number or a completed outcome in it — "code at
    the write, never prose only" (SPEC_SURFACES2, the cadence rule)."""
    text = str(text or "").strip()
    if not text:
        raise ClaimError("a claim needs rendered text")
    ledger_refs = [str(r).strip() for r in (refs or []) if str(r or "").strip()]
    inferred = _classify(ledger_refs, count, window, basis, instances)
    if kind is not None and kind != inferred:
        raise ClaimError(
            f"claim declared as {kind!r} but its fields only support "
            f"{inferred!r}: {text!r}")
    return {
        "text": text,
        "kind": inferred,
        "refs": ledger_refs,
        "count": count,
        "window": (str(window).strip() if window else ""),
        "basis": (str(basis).strip() if basis else ""),
        "instances": instances,
    }


def validate_claim(claim: dict, *, ledger_index: Optional[set] = None) -> list[str]:
    """Defects in one already-built claim dict. Empty list == clean.

    Re-validates from the STORED fields (never trusts a persisted `kind`) so
    a claim record that was hand-edited after `make_claim` built it, or that
    arrived from a store `make_claim` never touched, gets the same scrutiny.
    When `ledger_index` is given, a fact's refs are checked against it —
    the ledger-resolution half of the rule, not just the shape of the claim.
    """
    if not isinstance(claim, dict):
        return ["claim is not a dict"]
    defects: list[str] = []
    if not str(claim.get("text") or "").strip():
        defects.append("claim has no rendered text")
    refs = [str(r) for r in (claim.get("refs") or [])]
    try:
        recomputed = _classify(refs, claim.get("count"), claim.get("window"),
                                claim.get("basis"), claim.get("instances"))
    except ClaimError as exc:
        defects.append(str(exc))
        return defects
    declared = claim.get("kind")
    if declared not in _KINDS:
        defects.append(f"unknown claim kind {declared!r}")
    elif declared != recomputed:
        defects.append(
            f"claim declares kind {declared!r} but its fields support "
            f"{recomputed!r} — a stale or hand-edited record")
    if recomputed == FACT and ledger_index is not None:
        missing = [r for r in refs if r not in ledger_index]
        if missing:
            defects.append(f"fact ref(s) not found on the ledger: {missing!r}")
    return defects


def ledger_index_from_events(events: Iterable[dict]) -> set:
    """The resolving set `validate_claim`'s `ledger_index` wants: every
    event's `seq`, as strings. Goes through `event_seq.event_seq` (SPEC
    UNDOGUARD) rather than a bare `ev.get("seq")`, so a str-typed or
    backwards seq on disk still resolves a fact ref that names it — the
    same normalization every other reader in this plugin already gets."""
    try:
        from event_seq import event_seq
    except ImportError:  # pragma: no cover — direct-path fallback
        sys.path.insert(0, str(_HERE))
        from event_seq import event_seq
    idx: set = set()
    for ev in events or []:
        s = event_seq(ev)
        if s is not None:
            idx.add(str(s))
    return idx


# --- score + comparison bans (extends the leak scan's harness) -------------

# "Never a comparison to another person" (SPEC_SURFACES2 Standing rules +
# SPEC §8). No prior module owned this ban — `eod_synthesis.SCORE_PATTERNS`
# is the sibling fence for grades; this is its comparison-shaped counterpart,
# kept as its own compiled-pattern list for the same reason
# `connector_id_patterns.py` / `surface_leak_patterns.py` are their own
# files: the LIST is what changes, never the scanner that walks it.
COMPARISON_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\bcompared\s+to\s+(?:other|another|your\s+peers?)\b", re.IGNORECASE),
    re.compile(r"\b(?:vs\.?|versus)\s+(?:other|another|your\s+peers?|"
               r"the\s+average)\b", re.IGNORECASE),
    re.compile(r"\bthan\s+(?:your\s+)?(?:peers?|other\s+(?:clients?|"
               r"customers?|users?|owners?|founders?|ceos?)|the\s+average|"
               r"an?\s+average\s+\w+)\b", re.IGNORECASE),
    re.compile(r"\bpercentile\b", re.IGNORECASE),
    re.compile(r"\btop\s+\d{1,3}\s*%\s+of\b", re.IGNORECASE),
    re.compile(r"\brelative\s+to\s+(?:other|another)\b", re.IGNORECASE),
    re.compile(r"\bbetter\s+than\s+(?:most|other)\b", re.IGNORECASE),
    re.compile(r"\bbeats?\s+(?:the\s+)?average\b", re.IGNORECASE),
    # a named-person comparison — "3 more deals than Stone" — case-sensitive
    # on the name half so ordinary lowercase prose ("more than a day") never
    # matches. Up to a few words of noun phrase may sit between the
    # comparative and "than" ("more deals than", not just "more than").
    re.compile(r"\b(?:more|less|fewer|better|worse)\b(?:\s+\w+){0,3}?"
               r"\s+than\s+[A-Z][a-z]+\b"),
)


def comparison_tokens_in(text: Optional[str]) -> list[str]:
    """Every rendered comparison-to-another-person phrase in `text`, as the
    matched substrings. Empty list means the text names no such comparison."""
    if not text:
        return []
    hits: list[str] = []
    for pattern in COMPARISON_PATTERNS:
        for m in pattern.finditer(str(text)):
            hits.append(m.group(0))
    return hits


# --- unresolved figures + unresolved outcomes -------------------------------

_NUMBER_RE = re.compile(r"\$?\b\d[\d,]*(?:\.\d+)?%?\b")

# Outcome-shaped verbs: a sentence asserting something COMPLETED. A reader
# treats "you signed X" as a fact whether or not the product ever observed
# it — SPEC §8's own accept line names an outcome-plus-name sentence with no
# ledger event behind it. This list stays deliberately short and literal (it
# drives a fixture-scan gate, not a general-purpose NLP classifier).
_OUTCOME_RE = re.compile(
    r"\b(?:signed|closed\s+the\s+deal|closed\s+the\s+(?:account|contract)|"
    r"won\s+the|shipped|renewed|finalized|cancelled|churned)\b",
    re.IGNORECASE)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text or "") if s.strip()]


def unresolved_figures(text: str, claims: Optional[Iterable[dict]] = None) -> list[str]:
    """Every numeral in `text` that no claim's own text contains — a figure
    the renderer put on the surface with nothing behind it. This is the
    ledger-resolution scan's numeric half (fence 2 — see module docstring).

    Coverage is TOKEN-based (F-2 fix, 2026-09-08): a claim covers a figure
    only when the same numeral token appears among the claim text's own
    numerals — not whenever the figure's digits happen to occur as a
    substring of some other numeral in the claim text (a real "5" is not
    covered by a claim that only mentions "15")."""
    covered: set[str] = set()
    for c in (claims or []):
        for m in _NUMBER_RE.finditer(str(c.get("text") or "")):
            covered.add(m.group(0))
    out: list[str] = []
    for m in _NUMBER_RE.finditer(text or ""):
        token = m.group(0)
        if token not in covered:
            out.append(token)
    return out


def unresolved_outcomes(text: str, claims: Optional[Iterable[dict]] = None) -> list[str]:
    """Every outcome-shaped sentence in `text` not covered by a FACT claim
    that itself validates clean IN SHAPE — `validate_claim(c)` is called
    here with no `ledger_index`, so this checks that the claim's declared
    kind matches its own fields, not that its ref actually resolves on the
    ledger (a bogus ref is still caught separately, as `invalid_claim`, by
    the `validate_claim(..., ledger_index=...)` loop in `scan_surface`).
    The SPEC §8 accept-line class: an outcome asserted in prose with no
    event behind it.

    Coverage requires SENTENCE IDENTITY (F-3 fix, 2026-09-08): a fact claim
    covers an outcome sentence only when its own `text` IS that sentence —
    not whenever one is a substring of the other, which let a short,
    unrelated fact claim ("this week") silently mask an outcome sentence
    that happens to contain the same words. A composer building a claim
    through `make_claim` for a rendered outcome sentence is expected to
    pass that exact sentence as `text` — this is the discipline the SPEC
    §8 fixture already follows."""
    claims = list(claims or [])
    fact_texts = [c.get("text", "") for c in claims
                  if c.get("kind") == FACT and not validate_claim(c)]
    out: list[str] = []
    for sent in _split_sentences(text):
        if _OUTCOME_RE.search(sent):
            if sent not in fact_texts:
                out.append(sent)
    return out


def scan_surface(surface: str, text: str, claims: Optional[Iterable[dict]] = None,
                  *, ledger_index: Optional[set] = None) -> list[dict]:
    """Every defect on one rendered surface: a claim that doesn't earn its
    kind, a rendered score, a rendered comparison, an unresolved figure, an
    unresolved outcome. Empty list == the surface is clean.

    `claims` is the manifest the composer is expected to have built via
    `make_claim` alongside the text (the same discipline
    `surface_drivers.build_end_of_day_pack` already uses for
    `eod_synthesis.sentence()` — structure persisted, not just prose)."""
    claims = list(claims or [])
    defects: list[dict] = []
    for c in claims:
        for d in validate_claim(c, ledger_index=ledger_index):
            defects.append({"surface": surface, "kind": "invalid_claim",
                             "detail": d, "claim": c.get("text")})
    for tok in score_tokens_in(text):
        defects.append({"surface": surface, "kind": "score", "detail": tok})
    for tok in comparison_tokens_in(text):
        defects.append({"surface": surface, "kind": "comparison", "detail": tok})
    for tok in unresolved_figures(text, claims):
        defects.append({"surface": surface, "kind": "unresolved_figure",
                         "detail": tok})
    for sent in unresolved_outcomes(text, claims):
        defects.append({"surface": surface, "kind": "unresolved_outcome",
                         "detail": sent})
    return defects


def assert_surface_resolved(surface: str, text: str,
                             claims: Optional[Iterable[dict]] = None, *,
                             ledger_index: Optional[set] = None) -> None:
    """The write-time gate: raise `UnresolvedClaimError` if `scan_surface`
    finds anything. Every renderer that composes a number-or-outcome surface
    is expected to call this before it shows or persists the result — the
    same shape as `eod_synthesis.assert_no_score`, extended to the full
    fact/observation/reading rule rather than just the score half of it."""
    defects = scan_surface(surface, text, claims, ledger_index=ledger_index)
    if defects:
        raise UnresolvedClaimError(
            f"{surface}: {len(defects)} unresolved claim(s) — {defects!r}")


__all__ = [
    "FACT", "OBSERVATION", "READING", "FLOOR_INSTANCES",
    "ClaimError", "UnresolvedClaimError",
    "make_claim", "validate_claim", "ledger_index_from_events",
    "COMPARISON_PATTERNS", "comparison_tokens_in",
    "unresolved_figures", "unresolved_outcomes",
    "scan_surface", "assert_surface_resolved",
]
