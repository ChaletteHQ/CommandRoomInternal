#!/usr/bin/env python3
"""ROUTEMISS1 — the routing-corrections VIEW and its Monday-note line.

Renders `_hq/views/ROUTER_MISSES.md` from the `router_miss` events the
workspace-manager verb handler writes (see `router_miss.py`). This file is the
named consumer that makes the event type registrable: `shared/EVENT_TYPES.md`
names it, cleanup's weekly pass calls `regenerate_if_changed` and
`monday_note_line`, and the operator-side reader (out of scope for this build)
reads the SAME view — nobody greps the event log.

OWNER-FACING. The view carries the CEO's own words (`said` / `meant`), so it
reads the log through `events_io.load_events_owner_scoped` — the owner-tier
seam, no account mask, no personal-lane drop — and is written only into the
owner's `_hq/views/`. Nothing here composes org-, board-, client- or
external-facing output, and nothing here may be reused to.

PLAIN ENGLISH. The rendered text names no event type, no field name, no
record number, no internal architecture word; skill folders are shown as
words ("call prep", not `call-prep`), and an unknown previous turn says
"unknown" rather than guessing (DD-2). `tests/run_no_jargon_in_rendered_views_test.py`
renders this view from a fixture and scans it; the machine markers live in
HTML comments, which that scanner exempts.

THE MONDAY LINE. When three or more corrections in the trailing 28 days
resolved to the same skill, `monday_note_line` returns ONE sentence for
cleanup's Monday note naming the skill and the phrases; below the threshold it
returns None and cleanup stays silent (COVERQUIET1 posture). No description
is ever auto-edited from this data — the line is a pointer for a human.

PUBLIC API
  _build_content(ws) -> (markdown, counts)
  regenerate(ws) -> counts            atomic-write the view
  regenerate_if_changed(ws) -> counts  changed-only (cleanup's weekly entry)
  monday_note_line(ws, now=None) -> str | None
  load_misses(ws) -> list[dict]        the parsed rows, newest first

stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import re
import sys
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from atomic_write import atomic_write_text  # noqa: E402

EVENT_TYPE = "router_miss"
VIEW_REL = Path("_hq") / "views" / "ROUTER_MISSES.md"
WINDOW_DAYS = 28
MONDAY_THRESHOLD = 3
MAX_ROWS = 200

_VOLATILE_RE = re.compile(r"^<!-- generated_at=.*-->$", re.M)


def _view_path(workspace_root) -> Path:
    return Path(workspace_root) / VIEW_REL


def _parse_ts(value: Any) -> Optional[_dt.datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d


def _local_date(ts: Any, workspace_root) -> str:
    """Workspace-local calendar date for a row; falls back to the UTC date."""
    try:
        from tz import to_local
        local = to_local(ts, workspace_path=workspace_root)
        if local is not None:
            return local.strftime("%Y-%m-%d")
    except Exception:
        pass
    d = _parse_ts(ts)
    return d.strftime("%Y-%m-%d") if d else "unknown date"


def skill_words(folder: Any) -> str:
    """`call-prep` -> `call prep`; None/'' -> 'unknown'. The view never shows
    a folder id, and never guesses one."""
    if not isinstance(folder, str) or not folder.strip():
        return "unknown"
    return folder.strip().replace("-", " ").replace("_", " ")


def _clean(text: Any) -> str:
    """One-line, pipe-safe cell text (the CEO's own words, untouched otherwise)."""
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text).strip().replace("|", "\\|")


def load_misses(workspace_root) -> list[dict]:
    """Every routing correction on the log, NEWEST FIRST, parsed for rendering.

    Reads through the owner-tier seam only. Rows missing `meant` or
    `resolved_to` are skipped: the writer refuses them, so one on disk is a
    hand-rolled row, and a view that guessed at it would be wrong twice.
    """
    from events_io import load_events_owner_scoped
    events, _skipped = load_events_owner_scoped(workspace_root)
    rows: list[dict] = []
    for ev in events:
        if not isinstance(ev, dict) or ev.get("type") != EVENT_TYPE:
            continue
        data = ev.get("data") or {}
        if not isinstance(data, dict):
            continue
        meant = data.get("meant")
        resolved_to = data.get("resolved_to")
        if not isinstance(meant, str) or not meant.strip():
            continue
        if not isinstance(resolved_to, str) or not resolved_to.strip():
            continue
        rows.append({
            "ts": ev.get("ts"),
            "when": _parse_ts(ev.get("ts")),
            "said": data.get("said") if isinstance(data.get("said"), str) else "",
            "meant": meant.strip(),
            "resolved_to": resolved_to.strip(),
            "routed_to": data.get("routed_to") if isinstance(data.get("routed_to"), str) else None,
            "session_ref": data.get("session_ref") if isinstance(data.get("session_ref"), str) else None,
        })
    rows.sort(key=lambda r: (r["when"] or _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)),
              reverse=True)
    return rows


def _now(now=None) -> _dt.datetime:
    if isinstance(now, _dt.datetime):
        return now if now.tzinfo else now.replace(tzinfo=_dt.timezone.utc)
    return _dt.datetime.now(_dt.timezone.utc)


def _in_window(rows: list[dict], now=None, days: int = WINDOW_DAYS) -> list[dict]:
    floor = _now(now) - _dt.timedelta(days=days)
    return [r for r in rows if r["when"] is not None and r["when"] >= floor]


def repeat_clusters(workspace_root, now=None, *, days: int = WINDOW_DAYS,
                    threshold: int = MONDAY_THRESHOLD,
                    rows: Optional[list[dict]] = None) -> list[dict]:
    """Skills the CEO had to redirect to `threshold`+ times in the window.

    Returns [{skill, count, phrases}] sorted by count desc, then name. This is
    the ONE derivation both the view's summary and the Monday line use, so the
    two can never disagree about what "repeated" means.
    """
    rows = load_misses(workspace_root) if rows is None else rows
    recent = _in_window(rows, now=now, days=days)
    by_skill: dict[str, list[str]] = {}
    for r in recent:
        by_skill.setdefault(r["resolved_to"], []).append(r["meant"])
    out = [{"skill": k, "count": len(v), "phrases": v}
           for k, v in by_skill.items() if len(v) >= threshold]
    out.sort(key=lambda c: (-c["count"], c["skill"]))
    return out


_COUNT_WORDS = {2: "Twice", 3: "Three times", 4: "Four times", 5: "Five times",
                6: "Six times", 7: "Seven times", 8: "Eight times",
                9: "Nine times", 10: "Ten times"}


def _times(n: int) -> str:
    return _COUNT_WORDS.get(n, f"{n} times")


def monday_note_line(workspace_root, now=None) -> Optional[str]:
    """ONE plain-English sentence for cleanup's Monday note, or None.

    Fires only when some skill was the target of `MONDAY_THRESHOLD`+
    corrections inside `WINDOW_DAYS`. Names the skill in words and quotes the
    phrases (capped at five, oldest first, with an "and N more" tail). Never
    names the event type, a field, or a record number. Below the threshold:
    None — cleanup adds nothing.
    """
    clusters = repeat_clusters(workspace_root, now=now)
    if not clusters:
        return None
    top = clusters[0]
    phrases = list(reversed(top["phrases"]))  # rows are newest-first; speak oldest-first
    shown = [f"“{_clean(p)}”" for p in phrases[:5]]
    tail = f", and {len(phrases) - 5} more" if len(phrases) > 5 else ""
    line = (f"{_times(top['count'])} this month you had to redirect me to "
            f"{skill_words(top['skill'])}; the phrases were: "
            f"{', '.join(shown)}{tail}.")
    if len(clusters) > 1:
        others = ", ".join(skill_words(c["skill"]) for c in clusters[1:3])
        line += f" (Also repeated: {others}.)"
    return line


def _build_content(workspace_root) -> tuple[str, dict[str, Any]]:
    workspace_root = Path(workspace_root)
    rows = load_misses(workspace_root)
    recent = _in_window(rows)
    clusters = repeat_clusters(workspace_root, rows=rows)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    out: list[str] = []
    out.append("<!-- AUTO-GENERATED by shared/scripts/render_router_misses.py — "
               "do not hand-edit; regenerate from the activity log. -->")
    out.append("<!-- source: _hq/data/events.jsonl (routing corrections) -->")
    out.append(f"<!-- generated_at={stamp} -->")
    out.append("")
    out.append("# Routing corrections")
    out.append("")
    out.append("Every time you had to redirect me — “wrong skill”, "
               "“that should have been…”, “no, I meant…” — "
               "lands here, in your own words. Nothing in this page changes how "
               "I route on its own; it is the evidence whoever tunes your Command "
               "Room reads. Your phrases are never copied into a test without a "
               "person rewriting them first.")
    out.append("")
    if not rows:
        out.append("No corrections on record. When you redirect me, the turn "
                   "lands here.")
        out.append("")
        content = "\n".join(out) + "\n"
        return content, {"total": 0, "recent": 0, "repeated": 0}

    out.append(f"**{len(rows)}** correction{'s' if len(rows) != 1 else ''} on "
               f"record; **{len(recent)}** in the last {WINDOW_DAYS} days.")
    out.append("")
    out.append("## Repeated redirects (last 28 days)")
    out.append("")
    if clusters:
        for c in clusters:
            phrases = ", ".join(f"“{_clean(p)}”" for p in reversed(c["phrases"][-5:]))
            out.append(f"- **{skill_words(c['skill'])}** — {c['count']} times: {phrases}")
    else:
        out.append("None — no skill needed redirecting three or more times.")
    out.append("")
    out.append("## Every correction, newest first")
    out.append("")
    out.append("| Date | You said | What had just run | You meant | Went to |")
    out.append("|---|---|---|---|---|")
    for r in rows[:MAX_ROWS]:
        out.append(
            f"| {_local_date(r['ts'], workspace_root)} "
            f"| {_clean(r['said']) or '—'} "
            f"| {skill_words(r['routed_to'])} "
            f"| {_clean(r['meant'])} "
            f"| {skill_words(r['resolved_to'])} |"
        )
    if len(rows) > MAX_ROWS:
        out.append("")
        out.append(f"…and {len(rows) - MAX_ROWS} older correction(s) not shown.")
    out.append("")
    out.append("“What had just run” reads “unknown” unless the turn "
               "before your correction was one of my scheduled chats — I do not "
               "guess it from the phrase.")
    out.append("")
    content = "\n".join(out) + "\n"
    return content, {"total": len(rows), "recent": len(recent),
                     "repeated": len(clusters)}


def _strip_volatile(text: str) -> str:
    return _VOLATILE_RE.sub("", text)


def regenerate(workspace_root) -> dict[str, Any]:
    """Build and atomic-write `_hq/views/ROUTER_MISSES.md`. Idempotent."""
    workspace_root = Path(workspace_root)
    content, counts = _build_content(workspace_root)
    view = _view_path(workspace_root)
    view.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(view, content)
    counts["changed"] = True
    counts["view_path"] = str(view)
    return counts


def regenerate_if_changed(workspace_root) -> dict[str, Any]:
    """Changed-only regeneration — cleanup's weekly entry point. A quiet
    workspace (no new corrections) is a true no-op write."""
    workspace_root = Path(workspace_root)
    content, counts = _build_content(workspace_root)
    view = _view_path(workspace_root)
    old = view.read_text(encoding="utf-8") if view.exists() else ""
    changed = _strip_volatile(old) != _strip_volatile(content)
    if changed:
        view.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(view, content)
    counts["changed"] = changed
    counts["view_path"] = str(view)
    return counts


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python3 render_router_misses.py <workspace_root>", file=sys.stderr)
        return 2
    ws = Path(argv[1])
    if not ws.exists():
        print(f"ABORT: workspace not found: {ws}", file=sys.stderr)
        return 2
    r = regenerate(ws)
    print(f"OK — regenerated ROUTER_MISSES.md ({r['total']} corrections, "
          f"{r['recent']} recent, {r['repeated']} repeated) -> {r['view_path']}")
    line = monday_note_line(ws)
    print(f"Monday note: {line or '(nothing — below threshold)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
