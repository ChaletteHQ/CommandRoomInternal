#!/usr/bin/env python3
"""Shared pre-save gate stack — one implementation, every deliverable backend
(SPEC OUT5 §3b).

WHY THIS EXISTS
---------------
Before OUT5 the save-time quality gates (input validation → output-contract
gate → voice-tell gate → exec-header requirement) lived inline in
`brief_writer.make_brief` — the .docx chokepoint. OUT5 adds a SECOND rendering
backend (`premium_html.make_premium_brief`), and a second backend with its own
gate code is exactly how the GATE1 bug class (runtime-unreachable gates)
re-enters the product. So the gate SEQUENCE moved here, verbatim, and both
backends call the same function. There is deliberately no way to render a
deliverable through either chokepoint without this stack running.

THE PARITY INVARIANT (test-pinned — tests/run_guard_g16_gate_parity_test.py)
----------------------------------------------------------------------------
**No gate that fires on the docx path may be absent on the premium-HTML path.**
A new gate belongs HERE, in `run_pre_save_gates`, never inline in one backend.
The G16 guard enumerates both backends' gate sets and fails naming the side
that lags.

WHAT LIVES HERE
---------------
  - The brief-kind registry (EYEBROW_BY_KIND / SUPPORTED_BRIEF_KINDS) and the
    EXEC1 kind sets (STANDARD_KINDS / DECISION_SHAPED_KINDS /
    EXEC_EYEBROW_EXCLUDED_KINDS) — moved from brief_writer VERBATIM;
    brief_writer re-exports them so existing imports keep working.
  - `run_pre_save_gates(...)` — the canonical pre-render gate sequence, exactly
    the code that ran inline in make_brief through v4.8.0: input validation →
    EXEC1 kwarg validation → recommendation-ordering check → output-contract
    gate (B3) → voice-tell gate (B2) → STANDARD_KINDS exec-header requirement.
  - `warn_page_cap(...)` — the WARN-ONLY page-cap estimate (SPEC OUT2 §5),
    shared so an over-cap render warns identically on both backends.
  - The audit emitters (`emit_brief_meta_audit` / `emit_gate_ran_audit`) —
    moved from brief_writer; `emit_gate_ran_audit` now takes a `surface`
    argument ("docx" / "premium_html") so the GATE1 detectable-bypass join
    works per backend.

Stdlib only — the HTML backend must never need python-docx just to run gates.
"""
from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


# ---------- Supported brief kinds (moved from brief_writer, SPEC OUT5) ----------
# Module-level so the output-contract validator's sync guard can import it (via
# brief_writer's re-export) and assert set(RULES_BY_KIND) <= set(EYEBROW_BY_KIND)
# — a renamed kind then breaks loudly instead of silently un-scoping its
# contract rules.

EYEBROW_BY_KIND = {
    "call_prep":         "CALL PREP",
    "past_meeting":      "MEETING BRIEF",
    "board_pack":        "BOARD PACK",
    "board_review":      "BOARD REVIEW",  # P1.9 2026-07-02 — boardroom deliberation memo (distinct from board_pack reporting)
    "contract_review":   "CONTRACT REVIEW",
    "decision_memo":     "DECISION MEMO",
    "operator_report":   "OPERATING LIFT",
    "value_receipt":     "VALUE RECEIPT",
    "weekly_recap":      "WEEKLY RECAP",
    "weekly_audit":      "WEEKLY AUDIT",
    "dormant_scan":      "DORMANT CUSTOMER SCAN",
    "automation_scan":   "AUTOMATION SCAN",
    "automation_recipe": "AUTOMATION SETUP RECIPE",
    "followup_pack":     "FOLLOW-UP PACK",
    "memo":              "MEMO",
    "one_pager":         "ONE-PAGER",
    "insights":          "INSIGHTS",
    "stress_test":       "STRESS TEST",
    "kpi_scorecard":     "KPI SCORECARD",  # SPEC OUT7 — the between-packs KPI view / QBR pre-read
    "chart_on_demand":   "CHART",          # SPEC OUT3B — one substrate-derived chart on demand
}

SUPPORTED_BRIEF_KINDS = frozenset(EYEBROW_BY_KIND)


# ---------- Executive Output Standard (SPEC EXEC1) ----------
# See shared/EXECUTIVE_OUTPUT_STANDARD.md. The standard is enforced HERE (the
# render chokepoint), not re-implemented per skill.

# Kinds required to carry a 30-second exec header (element 1). Missing header
# on one of these raises ValueError (SPEC OUT2 §4 — the deferred release-N+1
# flip, landed after the EXEC1 warn-only staging release). The flip shipped in
# the SAME commit as the scheduled-orchestrator compliance sweep (CONTRACT
# Rule 16 — prompts lag the plugin; the sweep verified every scheduled
# orchestrator that renders a STANDARD_KIND passes exec_header: upcoming-
# meetings passes it on the call_prep path; past-meetings renders past_meeting,
# not a STANDARD_KIND; friday-wrap delegates to weekly-recap's phases which
# pass it; no other orchestrator renders a .docx). These are exactly the §4
# docx-producing skills that carry the adoption edit in their SKILL.md.
STANDARD_KINDS = frozenset({
    "call_prep",
    "memo",
    "board_pack",
    "weekly_recap",
    "one_pager",
    "followup_pack",
    "decision_memo",
    "dormant_scan",
    # OUT2 §4 — EXEC1 completion: the three kinds EXEC1 deferred.
    "contract_review",   # verdict = the deal-breaker flag line
    "automation_scan",   # verdict = top opportunity + payback
    "stress_test",       # verdict = the kill-risk line
    # SPEC OUT7 — the KPI scorecard / QBR pre-read. Brief-family (a recurring
    # "how are we tracking" digest, sibling to weekly_recap), so it is
    # deliberately NOT in EXEC_EYEBROW_EXCLUDED_KINDS below: it renders the full
    # verdict + CHANGED/DECIDE/NEEDED header. verdict = the single most
    # decision-relevant KPI move this period (build_scorecard computes it).
    "kpi_scorecard",
    # SPEC OUT3B — the on-demand single-chart page. A STANDARD_KIND so it rides
    # the full exec-header contract (verdict = the one-sentence reading of the
    # chart); eyebrow-excluded below (a one-chart answer is a document, not a
    # since-yesterday digest — the one_pager posture). verdict is supplied by
    # the chart-on-demand skill from the series it just rendered.
    "chart_on_demand",
})
# NB: operator_report is deliberately NOT here. Per SPEC EXEC1 §4 its Section 0
# synthesis lead is "untouched (it's the prototype)" and §5 lists it as a
# synthesis-lead surface — its synthesis paragraph IS its contract/lead, so it
# does not pass a separate exec_header (that would double-lead). The only EXEC1
# change for operator-report is the quantify dollar tag on Section 1 items.

# Decision-shaped kinds get the recommendation-ordering check (element 2):
# a rec/decision/suggested-outcome-headed section that appears only LATE
# (earliest occurrence at section index > 2) → ValueError. Analysis exists to
# audit the recommendation, not to defer it.
DECISION_SHAPED_KINDS = frozenset({"decision_memo", "memo", "one_pager"})

# A section heading reads as a recommendation when it matches this. Scoped to
# DECISION_SHAPED_KINDS only, so a past_meeting "Decisions" list (decisions
# already made) never trips it.
_REC_HEADING_RE = re.compile(
    r"\b(recommendation|recommended|suggested\s+outcome|verdict|the\s+ask|decision)\b",
    re.IGNORECASE,
)

# Canonical heading for the ASK block (element 4).
ASKS_HEADING = "What I need from you"

# Max asks (element 4) — more than this and the page stops being a contract.
MAX_ASKS = 3

# Small-caps labels for the three exec-header lines (element 1). Shared render
# data — both backends emit the same three lines in the same order.
EXEC_HEADER_LINES = (("changed", "CHANGED"), ("decide", "DECIDE"), ("needs", "NEEDED"))

# FS-13 — the CHANGED / DECIDE / NEEDED eyebrow is a BRIEF-FAMILY scaffold (a
# recurring digest of what moved). Document / decision kinds lead with the
# VERDICT alone: a memo, one-pager, decision memo, board pack, or analysis is
# not a "since yesterday" digest, and the three-line eyebrow both misframes
# them and eats the one-pager's single page (FS-13, 4/4 docs bled it live).
# These kinds render the verdict lead + rule, no eyebrow lines.
EXEC_EYEBROW_EXCLUDED_KINDS = frozenset({
    "memo", "one_pager", "decision_memo", "board_pack",
    "contract_review", "automation_scan", "stress_test",
    # SPEC OUT3B — a single-chart page leads with the verdict (the reading),
    # then the chart + table twin. The 3-line CHANGED/DECIDE/NEEDED eyebrow
    # would misframe a one-answer page (same reasoning as one_pager).
    "chart_on_demand",
})

# Composer skill whose customer-side voice block (B1: `_hq/voice/voice-block-
# <skill>.md`) calibrates each outbound brief kind. The override file is keyed
# by SKILL name, not brief kind — voice-block-memo-writer.md, never
# voice-block-memo.md — so the voice-tell gate needs this lookup to load it.
# Scoped to the hard-blocking outbound kinds (voice_tell_detector.
# FAIL_BLOCKING_KINDS): those are exactly the saves a false-positive tell can
# block, so they are where the client's Taboos carve-out must reach the gate.
VOICE_SKILL_BY_KIND = {
    "memo":          "memo-writer",
    "one_pager":     "one-pager-composer",
    "decision_memo": "decision-memo-composer",
    "board_pack":    "board-pack-assembler",
    "followup_pack": "follow-up-ritual",
}


# ---------- Gate registry (SPEC OUT5 — the G16 enumeration surface) ----------
# The canonical, ordered names of the save-time gates every deliverable backend
# runs. `run_pre_save_gates` returns the subset of PRE_SAVE_GATES that actually
# fired (contract / voice are mode- and install-dependent); the always-on
# raising checks (input validation, rec-ordering, exec-header requirement) are
# listed so the G16 guard can behaviorally exercise the full set on BOTH
# backends. Post-save, each backend runs its format's leak scan ("leak") — same
# forbidden-token list (docx_leak_scanner), one scanner per file format.
PRE_SAVE_GATES = ("input_validation", "rec_ordering", "contract", "voice", "exec_header")
POST_SAVE_GATES = ("leak",)


# ---------- Audit emitters (moved from brief_writer VERBATIM; surface added) ----------

def emit_brief_meta_audit(
    brief_kind: str, reason: str, workspace_root: Optional[str],
    severity: str = "warn",
) -> None:
    """Exec-standard audit trail (SPEC EXEC1 element 1). NEVER raises itself.

    Always surfaces a `[brief_meta]` line on stderr (dev-internal, never
    user-visible — same channel the contract/voice gates use for their
    findings). When `workspace_root` is known, ALSO best-effort appends a
    `brief_meta` event to events.jsonl (mirrors compute_and_log_brief_state) so
    the finding is DETECTABLE in the verify loop. Since the OUT2 §4 flip the
    missing-header call site passes severity="error" and raises AFTER this
    returns — the event is the substrate trace of the refused save (an
    orchestrator that lagged the flip shows up in the verify loop, not just in
    a transient stderr line). The events.jsonl write is wrapped so it can
    never block or mask the caller's outcome."""
    print(
        f"[brief_meta] exec-standard {severity}: {brief_kind} — {reason}",
        file=sys.stderr,
    )
    if not workspace_root:
        return
    try:
        from pathlib import Path as _Path
        from next_seq import next_seq as _next_seq
        from atomic_write import atomic_append_jsonl as _append
        from cru_match import _now_iso as _ts
        events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
        _append(events_path, [{
            "seq": _next_seq(str(events_path)),
            "ts": _ts(),
            "type": "brief_meta",
            "source_skill": "brief_writer",
            "data": {"brief_kind": brief_kind, "reason": reason, "severity": severity},
        }])
    except Exception:
        # Never let the audit write block or mask the caller's outcome.
        pass


def emit_gate_ran_audit(
    brief_kind: str,
    gates: List[str],
    output_path: str,
    workspace_root: Optional[str],
    surface: str = "docx",
) -> None:
    """SPEC GATE1 — detectable-bypass audit. NEVER raises.

    Emitted from inside a backend chokepoint AFTER a deliverable is successfully
    rendered and saved, recording WHICH save-time gates actually ran for it
    (contract / voice / leak). This is the #99 lesson applied: we cannot FORCE
    the LLM to route deliverable production through the chokepoint, but a
    composer fire that produces a deliverable with NO matching `gate_ran` event
    that turn is a flaggable bypass (the LLM hand-rolled the file via a generic
    skill, or answered in chat). The verify loop / cleanup can join board-pack /
    one-pager / memo `*_drafted` events against `gate_ran` events to surface a
    deliverable that dodged the gates.

    `surface` names the backend ("docx" — brief_writer, the default so every
    pre-OUT5 caller is unchanged; "premium_html" — premium_html). Same event
    type, one join for the verify loop.

    Self-contained on purpose: it adds `gate_ran` as its OWN events-schema enum
    member and does NOT depend on EXEC1's unpushed `brief_meta` stack. Always
    prints a `[gate_ran]` line on stderr (dev-internal, never user-visible —
    same channel the contract/voice gates use). When `workspace_root` is known,
    ALSO best-effort appends a `gate_ran` event to events.jsonl via the locked
    writer so the signal lands in substrate; the write is wrapped so it can
    never block a save."""
    from os.path import basename
    print(
        f"[gate_ran] {brief_kind} rendered via {surface} chokepoint — gates: "
        f"{','.join(gates) if gates else 'none'} "
        f"(file: {basename(output_path)})",
        file=sys.stderr,
    )
    if not workspace_root:
        return
    try:
        from pathlib import Path as _Path
        from atomic_write import atomic_append_jsonl as _append
        from cru_match import _now_iso as _ts
        events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
        # seq + ts are auto-stamped inside atomic_append_jsonl for events.jsonl,
        # so we pass neither — the locked writer reserves the seq atomically.
        _append(events_path, [{
            "ts": _ts(),
            "type": "gate_ran",
            "source_skill": "brief_writer",
            "data": {
                "brief_kind": brief_kind,
                "gates": gates,
                "surface": surface,
                "artifact": basename(output_path),
            },
        }], holder="brief_writer.gate_ran")
    except Exception:
        # Never let the audit write block the brief — the deliverable is already
        # on disk and valid; the audit is best-effort detectability only.
        pass


# ---------- The shared pre-save gate sequence (SPEC OUT5 §3b) ----------

def run_pre_save_gates(
    *,
    brief_kind: str,
    title: str,
    subtitle: str,
    sections: List[dict],
    supported_kinds: frozenset,
    contract: str = "enforce",
    contract_profile: Optional[str] = None,
    voice_gate: str = "default",
    exec_header: Optional[Dict[str, str]] = None,
    asks: Optional[List[Dict[str, str]]] = None,
    workspace_root: Optional[str] = None,
) -> List[str]:
    """Run the canonical pre-render gate sequence and return the list of
    mode-dependent gates that actually fired (["contract", "voice"], a subset —
    the caller appends its post-save "leak" entry for the gate_ran audit).

    This is EXACTLY the sequence that ran inline in `brief_writer.make_brief`
    through v4.8.0, moved verbatim (SPEC OUT5 §3b): input validation → EXEC1
    kwarg validation → recommendation-ordering check (element 2) → output-
    contract gate (SPEC B3) → voice-tell gate (SPEC B2) → STANDARD_KINDS
    exec-header requirement (OUT2 §4 flip). Every raise happens BEFORE any
    file is written — a blocked save loses no content on either backend.

    `supported_kinds` is the calling backend's kind registry (brief_writer
    passes SUPPORTED_BRIEF_KINDS; premium_html passes its superset that adds
    "research"). Everything else is backend-agnostic.

    Raises: ValueError / OutputContractError / VoiceTellError exactly as
    make_brief always did — messages unchanged.
    """
    if brief_kind not in supported_kinds:
        raise ValueError(
            f"brief_kind must be one of {sorted(supported_kinds)}, "
            f"got {brief_kind!r}"
        )
    if not title:
        raise ValueError("title is required")
    if not subtitle:
        raise ValueError("subtitle is required")
    if not isinstance(sections, list) or not sections:
        raise ValueError("sections must be a non-empty list")
    if contract not in ("enforce", "report", "off"):
        raise ValueError(
            f"contract must be 'enforce', 'report', or 'off', got {contract!r}"
        )
    if voice_gate not in ("default", "warn", "off"):
        raise ValueError(
            f"voice_gate must be 'default', 'warn', or 'off', got {voice_gate!r}"
        )

    # SPEC GATE1 — track which save-time gates actually run for this deliverable,
    # so the post-save gate_ran audit records it (detectable-bypass signal).
    gates_ran: List[str] = []

    # SPEC EXEC1 — input validation for the exec-standard kwargs.
    if exec_header is not None and not isinstance(exec_header, dict):
        raise ValueError(f"exec_header must be a dict or None, got {type(exec_header).__name__}")
    if asks is not None:
        if not isinstance(asks, list):
            raise ValueError(f"asks must be a list or None, got {type(asks).__name__}")
        if len(asks) > MAX_ASKS:
            # element 4 cap — more than 3 and the page stops being a contract.
            raise ValueError(
                f"asks may hold at most {MAX_ASKS} items (got {len(asks)}); "
                f"a deliverable with >3 reader-actions is not a 30-second contract"
            )
        for ask in asks:
            if not isinstance(ask, dict) or not (ask.get("text") or "").strip():
                raise ValueError(
                    f"each ask must be a dict with a non-empty 'text': {ask!r}"
                )

    # SPEC EXEC1 element 2 — recommendation-ordering check for decision-shaped
    # kinds. Raised BEFORE the gates / render (a structural input error, like
    # the kind/title/sections checks above) — no partial file. Analysis exists
    # to AUDIT the recommendation, not to defer it: if a rec/decision/
    # suggested-outcome-headed section appears ONLY late (earliest occurrence at
    # section index > 2), the document is inverted.
    if brief_kind in DECISION_SHAPED_KINDS:
        rec_indices = [
            i for i, sec in enumerate(sections)
            if isinstance(sec, dict) and _REC_HEADING_RE.search(str(sec.get("heading") or ""))
        ]
        if rec_indices and min(rec_indices) > 2:
            raise ValueError(
                f"{brief_kind}: the recommendation must come before the analysis "
                f"(EXEC1 element 2). A recommendation-shaped section first appears "
                f"at index {min(rec_indices)}; move it into the first three "
                f"sections (heading: {sections[min(rec_indices)].get('heading')!r})."
            )

    # SPEC B3 — pre-save output-contract gate. Runs BEFORE the voice gate and
    # BEFORE any render, so a blocked save writes NO partial file and loses NO
    # content. Lazy import + ImportError tolerance, exactly like the voice gate
    # and the post-render leak scan: a workspace mid-update that hasn't taken
    # the output_contract_validator update still saves normally.
    if contract != "off":
        try:
            from output_contract_validator import (
                collect_contract_violations,
                OutputContractError,
            )
        except ImportError:
            collect_contract_violations = None  # validator not installed yet.

        if collect_contract_violations is not None:
            gates_ran.append("contract")
            violations = collect_contract_violations(
                brief_kind, title, subtitle, sections, profile=contract_profile
            )
            if violations:
                blocking = [
                    v for v in violations
                    if v.get("severity", "error") == "error"
                ]
                if contract == "enforce" and blocking:
                    # No file written — caller rewrites the failing sections.
                    raise OutputContractError(brief_kind, blocking)
                # report mode (any violation), or warn-only violations in
                # enforce mode: surface to stderr and let the save proceed.
                print(
                    f"[output-contract] {len(violations)} contract "
                    f"violation(s) in {brief_kind} "
                    f"({'report mode' if contract == 'report' else 'warn-only'}"
                    f", save proceeds):\n"
                    + "\n".join(
                        f"  [{v['rule']}] {v.get('section') or 'whole brief'}: "
                        f"{v['observed']} — {v['expected']}. {v['fix_hint']}"
                        for v in violations
                    ),
                    file=sys.stderr,
                )

    # SPEC B2 — pre-save voice-tell gate. Runs BEFORE any render so a blocked
    # save writes NO partial file and loses NO content (the draft is rewritten,
    # never deleted post-save). Lazy import + ImportError tolerance, exactly
    # like the post-render leak scan: a workspace mid-update that hasn't
    # taken the voice_tell_detector update still saves normally.
    if voice_gate != "off":
        try:
            from voice_tell_detector import (
                check_sections,
                summarize_findings,
                VoiceTellError,
                FAIL_BLOCKING_KINDS,
            )
        except ImportError:
            check_sections = None  # detector not installed yet — skip the gate.

        if check_sections is not None:
            gates_ran.append("voice")
            # Per-client calibrated phrases (Voice Block Taboos) are fed through
            # and NEVER hard-blocked. Sourced from B1's override loader when
            # present; B2 has no hard dependency on it (ImportError tolerated).
            allow_phrases = None
            ban_dashes = True
            _voice_skill = VOICE_SKILL_BY_KIND.get(brief_kind)
            if workspace_root and _voice_skill:
                try:
                    from voice_corrections import load_voice_block_override  # type: ignore

                    override = load_voice_block_override(workspace_root, _voice_skill)
                    if override:
                        allow_phrases = override.get("taboos_allow") or None
                        ban_dashes = override.get("ban_dashes", True)
                except Exception as exc:
                    # A malformed override must never kill a save — but say so
                    # on stderr: this branch silently swallowed a wrong-arity
                    # loader call for months, leaving allow_phrases always None.
                    print(
                        f"[voice-tell gate] voice-block override load failed for "
                        f"{_voice_skill} ({type(exc).__name__}: {exc}) — "
                        f"gate runs uncalibrated",
                        file=sys.stderr,
                    )
                    allow_phrases, ban_dashes = None, True

            # FB-16 per-client dash override: forward ban_dashes=False only when
            # the installed detector knows the kwarg — the dash ban and its
            # kwarg land together, so a pre-FB-16 detector has no rule to relax.
            _extra = {}
            if not ban_dashes and (
                "ban_dashes" in inspect.signature(check_sections).parameters
            ):
                _extra["ban_dashes"] = False
            result = check_sections(
                sections, brief_kind=brief_kind, allow_phrases=allow_phrases,
                **_extra,
            )
            fail_findings = [f for f in result["findings"] if f["severity"] == "fail"]
            blocking = voice_gate == "default" and brief_kind in FAIL_BLOCKING_KINDS

            if fail_findings and blocking:
                raise VoiceTellError(
                    f"Voice-tell gate blocked a {brief_kind} save — "
                    f"{len(fail_findings)} banned phrase(s) must be rewritten "
                    f"before this document can be written:\n"
                    + summarize_findings(result["findings"]),
                    findings=result["findings"],
                )
            if result["findings"]:
                # Warn-only path (internal kind, or voice_gate="warn"): surface
                # the findings on stderr and let the save proceed.
                print(
                    f"[voice-tell gate] {len(result['findings'])} tell(s) in "
                    f"{brief_kind} (warn-only, save proceeds):\n"
                    + summarize_findings(result["findings"]),
                    file=sys.stderr,
                )

    # SPEC EXEC1 element 1 — STANDARD_KINDS exec-header presence check.
    # HARD-REQUIRE as of SPEC OUT2 §4 (the deferred release-N+1 flip): a
    # STANDARD_KIND with no exec_header raises BEFORE any render — no
    # partial file. The warn-only staging release gave orchestrator prompts a
    # full release to catch up (CONTRACT Rule 16); the compliance sweep landed
    # in the same commit as this flip (see the STANDARD_KINDS comment above).
    if brief_kind in STANDARD_KINDS and not (
        exec_header and (exec_header.get("verdict") or "").strip()
    ):
        emit_brief_meta_audit(
            brief_kind,
            "no exec_header (30-second contract) passed — save refused (OUT2 §4 flip)",
            workspace_root,
            severity="error",
        )
        raise ValueError(
            f"brief_kind {brief_kind!r} is a STANDARD_KIND and requires an "
            f"exec_header with a non-empty 'verdict' (the 30-second contract, "
            f"shared/EXECUTIVE_OUTPUT_STANDARD.md element 1). Pass "
            f"exec_header={{'verdict': ..., 'changed': ..., 'decide': ..., "
            f"'needs': ...}} — 'Nothing' forms are legal and encouraged."
        )

    return gates_ran


# ---------- Page-cap estimate (SPEC OUT2 §5, shared for parity per OUT5) ----------

# Rough words-per-page for the WARN-ONLY page_cap estimate. Deliberately crude
# (the profile never blocks a save); tables/tiles/timelines count as word
# equivalents below.
WORDS_PER_PAGE_EST = 450


def estimate_pages(title: str, subtitle: str, sections: List[dict]) -> int:
    """Crude, deterministic page estimate for the WARN-ONLY page_cap check.
    Counts words in bodies/bullets/table/matrix cells; tiles and timeline
    points weigh a fixed word-equivalent each. (Moved from brief_writer
    verbatim — the HTML backend uses the same estimate so a configured cap
    warns identically whichever backend renders.)"""
    words = len(str(title).split()) + len(str(subtitle).split())
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        words += len(str(sec.get("heading") or "").split()) + 4
        words += len(str(sec.get("body") or "").split())
        for b in sec.get("bullets") or []:
            words += len(str(b).split()) + 2
        table = sec.get("table")
        if isinstance(table, dict):
            for row in (table.get("rows") or []):
                for cell in row:
                    words += len(str(cell).split()) + 1
            for h in (table.get("headers") or []):
                words += len(str(h).split()) + 1
        matrix = sec.get("matrix")
        if isinstance(matrix, dict):
            cells = matrix.get("cells")
            if isinstance(cells, list):
                for row in cells:
                    words += sum(len(str(c).split()) + 1 for c in row)
        words += 12 * len(sec.get("tiles") or [])
        words += 8 * len(sec.get("timeline") or [])
    return max(1, -(-words // WORDS_PER_PAGE_EST))  # ceil division


def warn_page_cap(
    resolved_profile: dict, brief_kind: str,
    title: str, subtitle: str, sections: List[dict],
) -> None:
    """page_cap (WARN-ONLY, forever): a configured cap for this kind that the
    crude estimate exceeds gets one stderr note — never a block, never a
    truncation. No cap configured (the default) = silence. Shared so both
    backends warn identically (SPEC OUT5 parity)."""
    cap = (resolved_profile.get("page_cap") or {}).get(brief_kind)
    if cap:
        est = estimate_pages(title, subtitle, sections)
        if est > cap:
            print(
                f"[output-profile] {brief_kind}: estimated ~{est} pages against "
                f"a configured page cap of {cap} (warn-only — save proceeds; "
                f"consider tightening the longest sections)",
                file=sys.stderr,
            )


__all__ = [
    "EYEBROW_BY_KIND",
    "SUPPORTED_BRIEF_KINDS",
    "STANDARD_KINDS",
    "DECISION_SHAPED_KINDS",
    "EXEC_EYEBROW_EXCLUDED_KINDS",
    "ASKS_HEADING",
    "MAX_ASKS",
    "EXEC_HEADER_LINES",
    "PRE_SAVE_GATES",
    "POST_SAVE_GATES",
    "run_pre_save_gates",
    "estimate_pages",
    "warn_page_cap",
    "emit_brief_meta_audit",
    "emit_gate_ran_audit",
]
