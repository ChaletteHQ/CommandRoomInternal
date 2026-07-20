#!/usr/bin/env python3
"""
GATE2 same-turn Stop-hook runner.

WHAT THIS IS
------------
The best-effort SAME-TURN layer of GATE2. It is wired as a `Stop` hook in
`hooks/hooks.json` so that — IF the runtime executes plugin hooks — it fires the
moment the assistant finishes a turn, sweeps the deliverables that turn just
produced, and surfaces any voice/privacy violation BEFORE the user forwards the
artifact.

FEASIBILITY CAVEAT (read this — it's the make-or-break from SPEC GATE2 D3)
-------------------------------------------------------------------------
Plugin `Stop`/`PostToolUse` hooks are CONFIRMED in the Claude Code CLI. Whether
the COWORK runtime (sandboxed/remote, plugin mounted at
`$CLAUDE_CODE_TMPDIR/.../.remote-plugins/plugin_*`) executes plugin-provided
hooks is UNCONFIRMED — no Anthropic doc states it, and nothing in this plugin
has ever relied on a hook before. So this runner is shipped as a SAFE PROBE +
best-effort layer:
  - If the runtime honors plugin hooks → real same-turn enforcement, today.
  - If it ignores them → zero harm; the cleanup weekly sweep (Phase 3f) is the
    load-bearing backstop and catches the same violations on the next run.
The LIVE Cowork re-run is what tells us which world we're in: if the hook fired,
a `gate_ran` event with `surface="turn_hook"` lands in events.jsonl that turn.

CONTRACT
--------
- NEVER blocks. Always exits 0. A Stop hook that errored or hung would break the
  user's turn; that is unacceptable for a quality gate. Everything is wrapped.
- READ + FLAG ONLY. Delegates to `deliverable_sweep`, which never mutates a user
  file.
- Bounded. Scans only files modified in the last few minutes (this turn's
  output), so it stays fast even in a large workspace.

INPUT: Stop-hook stdin JSON (`{transcript_path, cwd, hook_event_name, ...}`).
We use `cwd` (and a small upward search) to locate the workspace `_hq/`.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Files older than this (seconds) are NOT this turn's output — skip them so the
# hook stays a fast, same-turn check rather than a full-workspace sweep.
_TURN_WINDOW_SECONDS = 600  # 10 minutes — generous slack for a long turn


def _last_assistant_text(transcript_path: str | None) -> str:
    """Extract the just-finished assistant turn's text from the transcript JSONL.

    SPEC GATE2 D4 — the chat-prose path. A memo/email drafted entirely as chat
    text (no skill fired, no .docx saved) never reaches a file or a widget, so
    the file sweep + renderer backstop both miss it. The transcript is the only
    place that text exists. A Stop hook receives `transcript_path`, so when the
    hook fires we can scan the last assistant message's text directly.

    Defensive across transcript shapes: handles content as a string or a list of
    {type:text, text:...} blocks. Returns '' on any problem (never raises)."""
    if not transcript_path:
        return ""
    try:
        p = Path(transcript_path)
        if not p.is_file():
            return ""
        last_text = ""
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if not isinstance(entry, dict):
                continue
            if entry.get("type") not in ("assistant", None) and entry.get(
                "role"
            ) not in ("assistant", None):
                continue
            msg = entry.get("message") if isinstance(entry.get("message"), dict) else entry
            if (msg.get("role") or entry.get("type")) != "assistant":
                continue
            content = msg.get("content")
            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text") or "")
                    elif isinstance(block, str):
                        parts.append(block)
                text = "\n".join(parts)
            if text.strip():
                last_text = text  # keep the most recent assistant text
        return last_text
    except Exception:
        return ""


def _find_workspace_root(start: str | None) -> Path | None:
    """Locate the workspace root by walking up from `cwd` looking for `_hq/`.
    Falls back to the Cowork session-mount discovery if cwd doesn't contain one.
    Returns None if no `_hq/` is found (then the sweep is a no-op)."""
    candidates = []
    if start:
        candidates.append(Path(start))
    # Cowork: workspace is typically the mount under $CLAUDE_CODE_TMPDIR.
    tmp = os.environ.get("CLAUDE_CODE_TMPDIR")
    if tmp:
        candidates.append(Path(tmp).parent)
    # Prefer the canonical resolver (anchors on _hq/data/entities.json).
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from workspace_root import find_workspace_root  # type: ignore

        for base in candidates:
            try:
                return find_workspace_root(base)
            except Exception:
                continue
    except Exception:
        pass

    # Fallback: broader _hq/ detection (handles a sparse workspace whose
    # entities.json hasn't been created yet).
    for base in candidates:
        try:
            base = base.resolve()
        except Exception:
            continue
        cur = base
        for _ in range(8):  # bounded upward walk
            if (cur / "_hq" / "data").is_dir() or (cur / "_hq").is_dir():
                return cur
            if cur.parent == cur:
                break
            cur = cur.parent
        # Also check one level of children (cwd may sit just above the ws).
        try:
            for child in base.iterdir():
                if child.is_dir() and (child / "_hq").is_dir():
                    return child
        except Exception:
            pass
    return None


def main() -> int:
    # Force UTF-8 stdout. The surfaced summary carries ⚠️ / em-dash / bullet
    # glyphs; on a Windows console (cp1252) `print` would raise
    # UnicodeEncodeError, which the outer guard would swallow — silently
    # dropping the flag. errors="replace" keeps it lossy-but-visible if the
    # runtime still can't encode. Best-effort; never fatal.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # 1. Read the hook payload — never trust it to be well-formed.
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    cwd = payload.get("cwd") or os.getcwd()

    try:
        ws = _find_workspace_root(cwd)
        if ws is None:
            return 0  # no workspace in reach — nothing to sweep, exit clean

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from deliverable_sweep import sweep_workspace, summarize_for_user, scan_chat_text

        lines = []

        # 1. File sweep — every deliverable this turn produced (.docx, .md, and
        #    .html/.htm; incl. hand-rolled). One walker, all three formats.
        since = time.time() - _TURN_WINDOW_SECONDS
        result = sweep_workspace(ws, since_ts=since, emit=True, source="turn_hook")
        file_summary = summarize_for_user(result)
        if file_summary:
            lines.append(file_summary)

        # 2. Chat-prose scan — the memo/email drafted as chat text with no file
        #    or widget (the Test 2 gap). Scan the just-finished assistant turn.
        chat_text = _last_assistant_text(payload.get("transcript_path"))
        if chat_text:
            chat = scan_chat_text(chat_text, context="brief")
            if chat.get("has_violation"):
                leaks = sorted({x["match"] for x in chat.get("leaks", [])})
                tells = sorted(
                    {
                        x["match"]
                        for x in chat.get("voice", {}).get("findings", [])
                        if x.get("severity") == "fail"
                    }
                )
                bits = []
                if leaks:
                    bits.append("private/internal language (" + ", ".join(repr(w) for w in leaks[:4]) + ")")
                if tells:
                    bits.append("a generic-assistant phrase (" + ", ".join(repr(w) for w in tells[:3]) + ")")
                lines.append(
                    "This reply reads as drafted but contains " + " + ".join(bits)
                    + " — rewrite before forwarding."
                )

        if lines:
            # Surface to the user same-turn. A Stop hook's stdout is the channel
            # the runtime relays; the file sweep also wrote a durable findings
            # record so the flag survives if stdout is dropped.
            print("⚠️  Quality gate — " + "\n".join(lines))
    except Exception:
        # A quality gate must NEVER break a turn. Swallow everything.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
