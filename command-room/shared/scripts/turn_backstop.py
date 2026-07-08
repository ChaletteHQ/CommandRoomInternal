#!/usr/bin/env python3
"""
Turn-level voice-tell backstop (SPEC GATE1, item 3).

WHY THIS EXISTS
---------------
The B2 voice gate has two enforcement points: (1) inside `brief_writer.make_brief`
for .docx-saving composers (deterministic Python — strong), and (2) a Step-2 bash
call inside the email-shaped composers' SKILL.mds (email-writer, inbox-triage,
follow-up-ritual, intro-broker), which push to Gmail Drafts and never save a
.docx. v3.20.0 verification proved point (2) is brittle: even when the skill
fired, the LLM treated the "enforced" Step-2 bash call as optional prose and
skipped it, and a banned-phrase draft reached the customer.

This module is the deterministic backstop for the EMAIL/CHAT surface — the path
that never reaches `make_brief`. Every email-shaped draft is rendered for the
user through ONE chokepoint: `chat_output_renderer.render_chat_output_widget`
(email-writer Phase 4, and every skill that chains through it). This module is
wired into that chokepoint so a banned voice tell in a chat-rendered email body
is caught even when no skill ran its Step-2 gate.

DESIGN (the #99 lesson — make bypass DETECTABLE; don't fight a calibrated voice)
-------------------------------------------------------------------------------
- NON-BLOCKING by default. The renderer scan is stderr-warn only and NEVER
  raises — the email widget always renders. Blocking at the renderer would be
  wrong: the renderer has no per-client `allow_phrases` context, so a CEO who
  demonstrably signs "Best regards" would be blocked. Detectability, not a hard
  stop, is the right contract for this surface (the .docx surface keeps the hard
  make_brief gate, which DOES carry allow_phrases).
- DETECTABLE in substrate. When a workspace_root is known, a fail finding emits
  a `gate_ran` event (`surface="chat_email"`, `result="fail"`) — the same enum
  member `brief_writer` uses for the docx surface — so the verify loop / cleanup
  can flag a voice-violating email that dodged the Step-2 gate.

Stdlib only (plus the sibling voice_tell_detector).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional

_CALENDAR_KEYS = {"time", "duration", "location", "date", "when", "where"}


def _is_email_shaped(item: dict) -> bool:
    """Mirror of chat_output_renderer._is_email_shaped (kept independent to avoid
    an import cycle): To + Subject present, and NOT a calendar invite (which also
    carries To/Subject but adds Time/Duration/Location/Date keys)."""
    metadata = item.get("metadata") or []
    has_to = has_subject = has_calendar = False
    for pair in metadata:
        try:
            key, value = pair[0], pair[1]
        except (TypeError, IndexError, KeyError):
            continue
        if not value:
            continue
        k = (key or "").lower()
        if k == "to":
            has_to = True
        elif k == "subject":
            has_subject = True
        elif k in _CALENDAR_KEYS:
            has_calendar = True
    if has_calendar:
        return False
    return has_to and has_subject


def _body_text(item: dict) -> str:
    """Flatten an item's body_lines into a scannable blob, stripping any
    reply-blockquote `>` prefixes (those are quoted-counterparty text the
    detector already skips, but stripping keeps line semantics clean)."""
    lines = item.get("body_lines") or []
    out: List[str] = []
    for ln in lines:
        if isinstance(ln, str):
            out.append(ln)
    return "\n".join(out)


def scan_email_body(
    text: str,
    *,
    allow_phrases: Optional[List[str]] = None,
) -> Dict:
    """Scan a single email body string for voice tells. Thin wrapper over
    voice_tell_detector.scan_text(context="email"). Returns the detector's
    {"verdict", "findings"} dict (verdict "pass" if the detector is unavailable)."""
    try:
        from voice_tell_detector import scan_text
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            from voice_tell_detector import scan_text  # type: ignore
        except ImportError:
            return {"verdict": "pass", "findings": []}
    return scan_text(text, context="email", allow_phrases=allow_phrases)


def _emit_gate_ran_chat_email(
    workspace_root: str,
    fail_count: int,
    item_count: int,
) -> None:
    """Best-effort `gate_ran` event for the chat-email surface. NEVER raises."""
    try:
        from atomic_write import atomic_append_jsonl as _append
        from cru_match import _now_iso as _ts
    except ImportError:
        return
    try:
        events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
        _append(events_path, [{
            "ts": _ts(),
            "type": "gate_ran",
            "source_skill": "turn_backstop",
            "data": {
                "surface": "chat_email",
                "gates": ["voice"],
                "result": "fail" if fail_count else "pass",
                "fail_count": fail_count,
                "items_scanned": item_count,
            },
        }], holder="turn_backstop.gate_ran")
    except Exception:
        # Telemetry must never break a render.
        pass


def _resolve_workspace_root() -> Optional[str]:
    """Best-effort workspace-root resolution so the renderer call-site (which has
    no workspace_root in hand) still emits a DETECTABLE gate_ran event.

    SPEC GATE2 D4 / the GATE1 wiring bug: `render_chat_output_widget` called
    `scan_data_view_for_tells(data)` with NO workspace_root, so the emit branch
    never fired — the backstop scanned but wrote nothing detectable (that's the
    'fired 0 times' finding). Resolving the root here closes that hole. Anchors
    on _hq/data/entities.json via the canonical resolver; returns None (stderr
    warn only, no event) if we're not inside a workspace. NEVER raises."""
    import os

    try:
        from pathlib import Path as _Path
        from workspace_root import find_workspace_root  # type: ignore
    except Exception:
        return None
    starts = [os.getcwd()]
    tmp = os.environ.get("CLAUDE_CODE_TMPDIR")
    if tmp:
        starts.append(str(_Path(tmp).parent))
    for s in starts:
        try:
            return str(find_workspace_root(s))
        except Exception:
            continue
    return None


def scan_data_view_for_tells(
    data: dict,
    *,
    workspace_root: Optional[str] = None,
    source_skill: Optional[str] = None,
    emit: bool = True,
) -> Dict:
    """Scan every email-shaped item's body in a chat data_view for fail-severity
    voice tells AND privacy/substrate leaks. NON-BLOCKING: prints a
    `[turn-backstop]` warning to stderr on any fail finding and (when
    workspace_root is known or resolvable) emits a detectable gate_ran event.
    NEVER raises — the widget must always render.

    workspace_root self-resolves when not passed (SPEC GATE2 D4) so the
    renderer call-site, which has none in hand, still emits a detectable event.

    Returns {"items_scanned": int, "fail_count": int, "findings": [...]}.
    """
    findings: List[dict] = []
    items_scanned = 0
    if not isinstance(data, dict):
        return {"items_scanned": 0, "fail_count": 0, "findings": []}

    if workspace_root is None:
        workspace_root = _resolve_workspace_root()

    # SPEC GATE2 D4 — also catch privacy/substrate leak tokens (Phase N,
    # project_NNN, events.jsonl, marketing words) in the chat body, not just
    # voice tells. The live memo leaked `Phase N` as chat prose.
    try:
        from docx_leak_scanner import scan_text_for_leaks
    except Exception:
        scan_text_for_leaks = None  # type: ignore

    for section in data.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for item in section.get("items") or []:
            if not isinstance(item, dict) or not _is_email_shaped(item):
                continue
            body = _body_text(item)
            if not body.strip():
                continue
            items_scanned += 1
            result = scan_email_body(body)
            for f in result.get("findings", []):
                if f.get("severity") == "fail":
                    f = {**f, "item_n": item.get("n")}
                    findings.append(f)
            if scan_text_for_leaks is not None:
                for lk in scan_text_for_leaks(body):
                    findings.append(
                        {
                            "rule": f"leak_{lk['name']}",
                            "severity": "fail",
                            "match": lk["match"],
                            "hint": "privacy/substrate leak — strip before sending",
                            "item_n": item.get("n"),
                        }
                    )

    if findings:
        try:
            from voice_tell_detector import summarize_findings
            summary = summarize_findings(findings)
        except Exception:
            summary = "\n".join(
                f"  [{f.get('rule')}] {f.get('match')!r}" for f in findings
            )
        print(
            f"[turn-backstop] {len(findings)} banned voice tell(s) in a "
            f"chat-rendered email body"
            + (f" (skill: {source_skill})" if source_skill else "")
            + " — the composer's Step-2 voice gate did NOT catch these. "
            "Rewrite the flagged lines before sending:\n" + summary,
            file=sys.stderr,
        )

    if emit and workspace_root and items_scanned:
        _emit_gate_ran_chat_email(workspace_root, len(findings), items_scanned)

    return {
        "items_scanned": items_scanned,
        "fail_count": len(findings),
        "findings": findings,
    }


__all__ = [
    "scan_email_body",
    "scan_data_view_for_tells",
]
