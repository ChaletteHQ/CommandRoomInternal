#!/usr/bin/env python3
"""
Output-contract validator for brief_writer (SPEC B3, v3.18.17+).

WHY THIS EXISTS
---------------

Every composer SKILL.md states a prose-quality bar — call-prep "800-1500
words, every section substantive"; one-pager "headline ≤25 words, 3-5
supporting-data bullets"; decision-memo "Framing/Options/Criteria/Comparison/
Recommendation, no blank matrix cells"; board-pack "≤6 exec-summary bullets,
no blank KPI cells". Until now NOTHING enforced those bars. A skinny brief
(1-bullet talking points, a 200-word call-prep, a blank comparison cell)
shipped just as readily as a substantive one. This converts those bars into
a checkable contract validated against the structured `sections` payload
BEFORE the .docx is rendered, so a failing brief is rewritten — never saved
substandard.

WHAT IT VALIDATES
-----------------

The structured input to `brief_writer.make_brief` (title + sections), NOT the
rendered docx (D1 — counting from docx XML is fragile, see Bug #54). Each
section dict carries body / bullets / table / matrix; word counts, item
counts, sentence floors, placeholder text and blank cells are all computable
from that payload, and diagnostics can name the exact section dict to rewrite.

CANONICAL GATE ORDER (B2 / B3)
------------------------------

input validation → CONTRACT gate (this module) → voice gate (voice_tell_
detector) → render (Document) → post-render leak scan (docx_leak_scanner).
This gate runs FIRST of the content gates and raises before Document() is
built, so a blocked save writes no partial file and loses no content.

CLIENT SAFETY
-------------

This gate can block a save on live client workspaces. The call_prep
total-word floor ships at REPORT severity (warn, save proceeds), NOT
hard-fail: a young/sparse client workspace legitimately cannot reach 800
words and must never be blocked for lack of substrate. Every blocking rule
only fires on a section that is PRESENT (the omit-don't-pad rule means absent
sections are never checked), and `contract='report'|'off'` are always-on
escape valves wired through make_brief.

Stdlib only.
"""
from __future__ import annotations

import json
import re
import sys
from typing import Dict, List, Optional, Tuple, Union


# ---------------------------------------------------------------------------
# Rule model (D2) — declarative per-kind table + generic rules.
#
# A `brief_kind` with no entry gets the generic rules only. `section_rules`
# keys match section headings case-insensitively by prefix (skills phrase
# headings with minor variation).
#
# Sync rule: these numbers MUST match the floors written into the five
# composer SKILL.mds. If you change a quality bar in a SKILL.md, change the
# matching entry here in the SAME commit (and vice-versa).
# ---------------------------------------------------------------------------

RULES_BY_KIND: Dict[str, dict] = {
    "call_prep": {
        # CLIENT SAFETY: the total-word floor is REPORT severity (warn) — a
        # sparse workspace genuinely cannot hit 800 words and must not be
        # blocked. See acceptance criterion 7.
        "total_words": (800, 1500),
        "total_words_severity": "warn",
        "section_rules": {
            "Talking Points": {"bullet_range": (4, 7)},
            "Questions to Ask": {"bullet_range": (3, 5)},
            "Progress Since You Last Met": {"bullet_range": (3, 8)},
            "Decisions Already On The Record": {"bullet_range": (2, 5)},
            "Relationship Context": {"min_sentences_per_paragraph": 3},
            "Where We Left Off": {"min_sentences": 4},
        },
        # Only the truly unconditional section — the omit-not-pad rule
        # (call-prep SKILL.md line 162) means most sections are conditional.
        "required_sections": ["Meeting Details"],
        "alt_profiles": ["call_prep_internal"],
    },
    "memo": {
        "total_words": (250, 1000),
        "max_paragraph_words": 150,
    },
    # P1.9 2026-07-02 — boardroom's deliberation memo. Sync rule: section list
    # mirrors boardroom SKILL.md "Memo structure"; edit both or neither.
    "board_review": {
        "required_sections": [
            "Subject & framing",
            "Verdicts",
            "Conflict map",
            "Per-seat detail",
            "The board's asks",
        ],
    },
    "one_pager": {
        "total_words": (120, 480),
        "headline_max_words": 25,
        "section_rules": {
            "Supporting Data": {"bullet_range": (3, 5)},
        },
        "allowed_placeholders": [r"\[Figure needed — confirm before sending\]"],
    },
    "decision_memo": {
        "required_sections": [
            "Framing",
            "Options",
            "Criteria & weights",
            "Comparison",
            "Recommendation",
        ],
        "section_rules": {
            "Options": {"bullet_range": (2, 4)},
            "Comparison": {"matrix_no_blank_cells": True},
        },
    },
    "board_pack": {
        "section_rules": {
            "Executive Summary": {"bullet_range": (1, 6)},
        },
        "table_no_blank_cells": True,
        "allowed_placeholders": [r"\[add asks here\]"],
    },
    # SPEC OUT7 — KPI scorecard / QBR pre-read. The KPI tables (the "KPIs vs
    # Targets" section and the "Scorecard" detail table) carry no blank cells —
    # scorecard.build_kpi_section renders an em dash for an absent-but-legitimate
    # cell, never empty (same posture as board_pack). Sync rule: the Needs-
    # attention cap mirrors scorecard.NEEDS_ATTENTION_CAP (3); change one, change
    # the other. The "(...)" nothing-forms scorecard.py emits for an empty block
    # are parenthetical honest-gaps, not placeholders — GENERIC_RULES's
    # placeholder patterns don't match them.
    "kpi_scorecard": {
        "section_rules": {
            "Needs attention": {"bullet_range": (1, 3)},
        },
        "table_no_blank_cells": True,
    },
    # SPEC OUT3B — the on-demand single-chart page. The table twin (the same
    # numbers the chart draws, the precision companion and the refusal
    # fallback) carries no blank cells: value_by_org / build_trend_chart /
    # stage_mix emit only observed, priced rows — a gap is never a blank cell,
    # it is an absent row. No section-count or word floor: a one-chart answer
    # is deliberately one section; padding it would violate the "title is the
    # message" rule.
    "chart_on_demand": {
        "table_no_blank_cells": True,
    },
    # SPEC TRIAGEROUTE — the daily inbox triage brief. Every number below is
    # inbox-triage's OWN stated bar, not another kind's borrowed: Step 5 "Surface
    # 3-5 items — these are the ones the CEO reads first. Everything else listed
    # in an appendix"; the five-bucket model ("Classify into one of five
    # buckets") is a CLOSED taxonomy; Step 6 "2-3 drafts max (more than that and
    # drafts become noise)". Sync rule: these mirror the floors in
    # skills/inbox-triage/SKILL.md Step 7 — change one, change the other.
    #
    # THE CAPS CARRY THE WEIGHT HERE, and the floors are deliberately 1. This
    # kind's length is a function of the INBOX, not of effort: a 9-email morning
    # and a 150-email Monday produce the same quality of brief at wildly
    # different sizes. A floor of 3 on "Top of the Pile" would force exactly the
    # padding the skill's own gotcha bans ("Err toward Reply Now + Decision
    # Needed getting smaller rather than padding the 'top 5' with weak items"),
    # so the 3 in "3-5" is guidance to the composer and the 5 is the contract.
    # For the same reason there is NO total_words rule (the chart_on_demand
    # precedent, inverted): a word bound would gate the inbox, not the brief.
    "inbox_triage": {
        # The one section that IS the artifact — this skill's stated job is
        # "5-bucket classification", and a triage brief with no bucket
        # accounting is a note about email, not a triage. Everything else is
        # conditional by the skill's own text: the ranked list presupposes
        # candidates, "Reply Drafts" is absent under default_action=brief_only,
        # and "Commitments I Caught" is omit-when-zero by instruction.
        "required_sections": ["By Bucket"],
        "section_rules": {
            # Cap 5 = Step 5's ranked list. A "top of the pile" with 12 entries
            # is not a top of the pile, it is the appendix moved up.
            "Top of the Pile": {"bullet_range": (1, 5)},
            # Cap 5 = the five buckets. A sixth bullet means a bucket was
            # invented past the closed taxonomy the classifier runs on.
            "By Bucket": {"bullet_range": (1, 5)},
            # Cap 3 = Step 6's own ceiling, and its own stated reason.
            "Reply Drafts": {"bullet_range": (1, 3)},
        },
    },
}


# Internal call-prep variant (call-prep SKILL.md lines 164-182). Selected via
# profile="call_prep_internal". Total-word floor relaxed to 500-1500 and still
# REPORT severity. External prep sections are dropped; the unconditional
# section is still Meeting Details.
PROFILE_RULES: Dict[str, dict] = {
    "call_prep_internal": {
        "total_words": (500, 1500),
        "total_words_severity": "warn",
        "section_rules": {
            "Where We Left Off": {"min_sentences": 4},
            "What to drive": {"bullet_range": (4, 7)},
            "Decisions Already On The Record": {"bullet_range": (2, 5)},
        },
        "required_sections": ["Meeting Details"],
    },
}


GENERIC_RULES = {  # apply to every kind
    "placeholder_patterns": [
        r"\bTBD\b",
        r"\bTKTK\b",
        r"\blorem ipsum\b",
        r"\[TODO",
        r"<insert\b",
        r"\bXXX\b",
        r"no information available",
        r"\[PLACEHOLDER",
    ],
    # Empty bullet entries are always wrong. Blank CELLS, by contrast, are only
    # checked per-kind (decision_memo Comparison matrix, board_pack KPI table)
    # — a blank cell can be legitimate in an arbitrary table, per the risk note
    # in SPEC §8.
    "no_empty_strings_in_bullets_or_cells": True,
}


# ---------------------------------------------------------------------------
# Failure shape (D3)
# ---------------------------------------------------------------------------

class OutputContractError(ValueError):
    """Raised by validate_brief when one or more BLOCKING contract violations
    are found. Carries machine-usable diagnostics on `.violations`:

        each: {"rule", "section" (heading or None), "observed",
               "expected", "fix_hint", "severity"}

    `str(e)` renders one line per violation and names every failing section.
    """

    def __init__(self, brief_kind: str, violations: List[dict]):
        self.brief_kind = brief_kind
        self.violations = violations
        super().__init__(self._render())

    def _render(self) -> str:
        lines = [
            f"Output-contract check failed for {self.brief_kind} "
            f"({len(self.violations)} violation(s)):"
        ]
        for v in self.violations:
            section = v.get("section") or "whole brief"
            lines.append(
                f"  [{v['rule']}] {section}: {v['observed']} — "
                f"{v['expected']}. {v['fix_hint']}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+")


def _words(text: str) -> int:
    return len(str(text).split())


def _sentences(text: str) -> int:
    """Count sentences by splitting on . ! ? runs.

    Heuristic and deliberately simple: abbreviations ("Inc.", "e.g.") inflate
    the count. That's benign here — every sentence rule is a FLOOR (minimum),
    never a cap, so over-counting only makes a rule easier to pass.
    """
    parts = [p for p in _SENTENCE_SPLIT_RE.split(str(text)) if p.strip()]
    return len(parts)


def _paragraphs(body: str) -> List[str]:
    """Split a body string into paragraphs on blank lines, matching
    brief_writer._add_body_paragraphs."""
    return [b.strip() for b in str(body).split("\n\n") if b.strip()]


def _matrix_grid(matrix: dict) -> List[List[str]]:
    """Normalize a matrix `cells` payload (2D list OR {(r,c): v} dict) to a
    dense 2D grid. Delegates to components.normalize_matrix (SPEC OUT2 —
    convergence: one normalization for both consumers, no mirrored copy) with
    this validator's historical empty-tolerance preserved: empty/missing
    `cells` returns [] here (emptiness is enforced by this module's own rules
    with per-section diagnostics), where the renderer path raises."""
    cells = matrix.get("cells")
    if not cells:
        return []
    import os as _os
    sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from components import normalize_matrix as _normalize_matrix
    try:
        rows, _n_cols = _normalize_matrix(cells)
    except ValueError:
        return []
    return rows


def _section_words(sec: dict) -> int:
    total = 0
    body = sec.get("body")
    if body:
        total += _words(body)
    for line in sec.get("bullets") or []:
        total += _words(line)
    table = sec.get("table")
    if table:
        for row in table.get("rows") or []:
            for cell in row:
                total += _words(cell)
    matrix = sec.get("matrix")
    if matrix:
        for row in _matrix_grid(matrix):
            for cell in row:
                total += _words(cell)
    return total


def _total_words(sections: List[dict]) -> int:
    return sum(_section_words(s) for s in sections)


def _bullet_items(sec: dict) -> List[str]:
    return [b for b in (sec.get("bullets") or []) if str(b).strip()]


def _heading_matches(heading: str, rule_key: str) -> bool:
    """Case-insensitive prefix/contains match (headings drift slightly)."""
    h = str(heading).strip().lower()
    k = rule_key.strip().lower()
    return h.startswith(k) or k in h


def _find_section(sections: List[dict], rule_key: str) -> Optional[dict]:
    for sec in sections:
        if _heading_matches(sec.get("heading", ""), rule_key):
            return sec
    return None


def _iter_text(sections: List[dict]):
    """Yield (section_heading, field_kind, text) over every text fragment, for
    placeholder scanning. field_kind in {"body", "bullet", "cell"}."""
    for sec in sections:
        heading = sec.get("heading")
        body = sec.get("body")
        if body:
            yield (heading, "body", str(body))
        for line in sec.get("bullets") or []:
            yield (heading, "bullet", str(line))
        table = sec.get("table")
        if table:
            for row in table.get("rows") or []:
                for cell in row:
                    yield (heading, "cell", str(cell))
        matrix = sec.get("matrix")
        if matrix:
            for row in _matrix_grid(matrix):
                for cell in row:
                    yield (heading, "cell", str(cell))


# ---------------------------------------------------------------------------
# Rule resolution
# ---------------------------------------------------------------------------

def _resolve_rules(brief_kind: str, profile: Optional[str]) -> dict:
    if profile:
        if profile in PROFILE_RULES:
            return PROFILE_RULES[profile]
        # Unknown profile — fall back to the kind rules rather than blowing up.
    return RULES_BY_KIND.get(brief_kind, {})


def _v(rule, section, observed, expected, fix_hint, severity="error") -> dict:
    return {
        "rule": rule,
        "section": section,
        "observed": observed,
        "expected": expected,
        "fix_hint": fix_hint,
        "severity": severity,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def collect_contract_violations(
    brief_kind: str,
    title: str,
    subtitle: str,
    sections: List[dict],
    *,
    profile: Optional[str] = None,
) -> List[dict]:
    """Return every contract violation (blocking AND warn) for this payload.
    Never raises (the non-raising twin of validate_brief, mirroring
    docx_leak_scanner.collect_docx_leaks)."""
    violations: List[dict] = []
    rules = _resolve_rules(brief_kind, profile)

    if not isinstance(sections, list) or not sections:
        # Shape errors are brief_writer's job; nothing to validate here.
        return violations

    # --- required_sections -------------------------------------------------
    for req in rules.get("required_sections", []):
        if _find_section(sections, req) is None:
            violations.append(
                _v(
                    "required_section",
                    req,
                    "missing",
                    f"{brief_kind} requires a '{req}' section",
                    f"Add the '{req}' section with real content.",
                )
            )

    # --- total_words -------------------------------------------------------
    if "total_words" in rules:
        lo, hi = rules["total_words"]
        severity = rules.get("total_words_severity", "error")
        count = _total_words(sections)
        if count < lo:
            if severity == "warn":
                fix = (
                    f"Expand with real signal, or pass contract='report' if "
                    f"the workspace genuinely lacks signal — never pad."
                )
            else:
                fix = (
                    f"Expand the thin sections with real substrate signal; "
                    f"do not pad with filler."
                )
            violations.append(
                _v(
                    "total_words",
                    None,
                    f"{count} words",
                    f"{brief_kind} requires {lo}-{hi} words",
                    fix,
                    severity=severity,
                )
            )
        elif count > hi:
            violations.append(
                _v(
                    "total_words",
                    None,
                    f"{count} words",
                    f"{brief_kind} caps at {lo}-{hi} words",
                    "Cut the least-load-bearing content; tighten prose.",
                    # Over-cap is a quality issue but never a client-safety
                    # block — surface it the same severity as the floor.
                    severity=severity,
                )
            )

    # --- headline_max_words (the title is the headline) --------------------
    if "headline_max_words" in rules:
        cap = rules["headline_max_words"]
        hw = _words(title)
        if hw > cap:
            violations.append(
                _v(
                    "headline_max_words",
                    "Headline",
                    f"{hw} words",
                    f"headline caps at {cap} words",
                    "Cut the headline to a single sharp conclusion.",
                )
            )

    # --- max_paragraph_words (across every section body) --------------------
    if "max_paragraph_words" in rules:
        cap = rules["max_paragraph_words"]
        for sec in sections:
            body = sec.get("body")
            if not body:
                continue
            for para in _paragraphs(body):
                pw = _words(para)
                if pw > cap:
                    violations.append(
                        _v(
                            "max_paragraph_words",
                            sec.get("heading"),
                            f"{pw}-word paragraph",
                            f"no paragraph over {cap} words",
                            "Break this paragraph into shorter ones.",
                        )
                    )

    # --- per-section rules -------------------------------------------------
    for rule_key, sec_rules in rules.get("section_rules", {}).items():
        sec = _find_section(sections, rule_key)
        if sec is None:
            # Section absent — omit-don't-pad means we never demand presence
            # here (that's required_sections' job).
            continue
        heading = sec.get("heading")

        if "bullet_range" in sec_rules:
            lo, hi = sec_rules["bullet_range"]
            n = len(_bullet_items(sec))
            if n < lo or n > hi:
                verb = "at least" if n < lo else "at most"
                bound = lo if n < lo else hi
                violations.append(
                    _v(
                        "bullet_range",
                        heading,
                        f"{n} bullet(s)",
                        f"'{heading}' needs {lo}-{hi} bullets",
                        f"Provide {verb} {bound} substantive bullets "
                        f"(expand with real signal or omit the section).",
                    )
                )

        if "min_sentences" in sec_rules:
            floor = sec_rules["min_sentences"]
            n = _sentences(sec.get("body") or "")
            if n < floor:
                violations.append(
                    _v(
                        "min_sentences",
                        heading,
                        f"{n} sentence(s)",
                        f"'{heading}' needs at least {floor} sentences",
                        "Expand with real detail from the substrate.",
                    )
                )

        if "min_sentences_per_paragraph" in sec_rules:
            floor = sec_rules["min_sentences_per_paragraph"]
            for para in _paragraphs(sec.get("body") or ""):
                n = _sentences(para)
                if n < floor:
                    violations.append(
                        _v(
                            "min_sentences_per_paragraph",
                            heading,
                            f"{n}-sentence paragraph",
                            f"each '{heading}' paragraph needs at least "
                            f"{floor} sentences",
                            "Expand each attendee/topic block with real "
                            "detail.",
                        )
                    )

        if sec_rules.get("matrix_no_blank_cells"):
            matrix = sec.get("matrix")
            if matrix:
                for r, row in enumerate(_matrix_grid(matrix)):
                    for c, cell in enumerate(row):
                        if not str(cell).strip():
                            violations.append(
                                _v(
                                    "matrix_blank_cell",
                                    heading,
                                    f"blank cell at (row {r}, col {c})",
                                    f"'{heading}' matrix must have no blank "
                                    f"cells",
                                    "Fill every cell with a score + evidence; "
                                    "use 'n/a' if a criterion truly doesn't "
                                    "apply.",
                                )
                            )

        if sec_rules.get("table_no_blank_cells"):
            _collect_table_blanks(sec, violations)

    # --- kind-level table_no_blank_cells (all tables in the brief) ----------
    if rules.get("table_no_blank_cells"):
        for sec in sections:
            if sec.get("table"):
                _collect_table_blanks(sec, violations)

    # --- generic: placeholders + empty bullets -----------------------------
    allowed = rules.get("allowed_placeholders", [])
    for heading, field_kind, text in _iter_text(sections):
        # Empty bullets are always wrong (cells are scoped per-kind above).
        if GENERIC_RULES.get("no_empty_strings_in_bullets_or_cells"):
            if field_kind == "bullet" and not text.strip():
                violations.append(
                    _v(
                        "empty_bullet",
                        heading,
                        "empty bullet",
                        "no empty bullet entries",
                        "Remove the empty bullet or give it content.",
                    )
                )
        for hit in _placeholder_hits(text, allowed):
            violations.append(
                _v(
                    "placeholder",
                    heading,
                    f"placeholder text {hit!r}",
                    "no placeholder / draft-artifact text",
                    "Replace with real content or omit the section — never "
                    "ship a placeholder.",
                )
            )

    return violations


def _collect_table_blanks(sec: dict, violations: List[dict]) -> None:
    table = sec.get("table")
    if not table:
        return
    rows = table.get("rows") or []
    headers = table.get("headers")
    n_cols = max((len(r) for r in rows), default=0)
    if headers:
        n_cols = max(n_cols, len(headers))
    heading = sec.get("heading")
    for r, row in enumerate(rows):
        for c in range(n_cols):
            cell = row[c] if c < len(row) else ""
            if not str(cell).strip():
                violations.append(
                    _v(
                        "table_blank_cell",
                        heading,
                        f"blank cell at (row {r}, col {c})",
                        f"'{heading}' table must have no blank cells",
                        "Fill every cell, or render '(nothing logged)' "
                        "rather than leaving it blank.",
                    )
                )


def _placeholder_hits(text: str, allowed_patterns: List[str]) -> List[str]:
    """Return placeholder matches in `text`, after removing any explicitly
    allowed placeholder forms (e.g. '[Figure needed — confirm before sending]',
    '[add asks here]')."""
    scrubbed = text
    for ap in allowed_patterns:
        scrubbed = re.sub(ap, " ", scrubbed)
    hits: List[str] = []
    for pat in GENERIC_RULES["placeholder_patterns"]:
        m = re.search(pat, scrubbed)
        if m:
            hits.append(m.group(0))
    return hits


def validate_brief(
    brief_kind: str,
    title: str,
    subtitle: str,
    sections: List[dict],
    *,
    profile: Optional[str] = None,
) -> List[dict]:
    """Validate the structured brief payload. Raises OutputContractError if any
    BLOCKING (severity="error") violation is found. Returns the list of
    non-blocking (warn) violations otherwise — the caller may surface those to
    stderr without blocking the save.

    `profile` selects an alternate rule set (e.g. "call_prep_internal").
    """
    violations = collect_contract_violations(
        brief_kind, title, subtitle, sections, profile=profile
    )
    blocking = [v for v in violations if v.get("severity", "error") == "error"]
    if blocking:
        raise OutputContractError(brief_kind, blocking)
    return [v for v in violations if v.get("severity") == "warn"]


__all__ = [
    "validate_brief",
    "collect_contract_violations",
    "OutputContractError",
    "RULES_BY_KIND",
    "PROFILE_RULES",
    "GENERIC_RULES",
]


# ---------------------------------------------------------------------------
# CLI — output_contract_validator.py <payload.json>  (same JSON shape as
# brief_writer.make_brief_from_json). Exit 0 clean / 1 on blocking violations.
# ---------------------------------------------------------------------------

def _main(argv: List[str]) -> int:
    if len(argv) == 2:
        with open(argv[1], "r", encoding="utf-8") as f:
            raw = f.read()
    elif len(argv) == 1 and not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        print(
            "usage: output_contract_validator.py <payload.json>  (or pipe JSON)",
            file=sys.stderr,
        )
        return 2

    payload = json.loads(raw)
    violations = collect_contract_violations(
        payload["brief_kind"],
        payload.get("title", ""),
        payload.get("subtitle", ""),
        payload.get("sections", []),
        profile=payload.get("contract_profile"),
    )
    if not violations:
        print("OK: no contract violations")
        return 0

    blocking = [v for v in violations if v.get("severity", "error") == "error"]
    warns = [v for v in violations if v.get("severity") == "warn"]
    for v in violations:
        section = v.get("section") or "whole brief"
        tag = "WARN" if v.get("severity") == "warn" else "FAIL"
        print(f"  [{tag}] [{v['rule']}] {section}: {v['observed']} — "
              f"{v['expected']}. {v['fix_hint']}")
    print(f"{len(blocking)} blocking, {len(warns)} warn")
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
