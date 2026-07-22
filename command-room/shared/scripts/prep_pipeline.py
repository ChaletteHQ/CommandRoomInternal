#!/usr/bin/env python3
"""
Meeting-prep v2 pipeline — the ONE generator's code layer (v4.5.2 S1).

WHY
---

The 2026-07-07/08 dogfood (FINDINGS_M_v451) found the highest-visibility
deliverable running on TWO generators:

  F-60  — the scheduled auto-prep produced a 209-word template fill while the
          on-demand 'prep me' path produced 1,683 words with a walk-out-with
          objective + CHANGED/DECIDE/NEEDED synthesis. Same surface name, two
          code paths. The auto path read no overnight email and missed the
          sweep-recovered, undated items about the exact meeting (F-44's
          blindness extended into prep).
  F-29  — the morning brief claimed "no prep brief" for a meeting whose prep
          file AND fire receipt were both on disk — the detector read a
          different signal than the writer left. Fixed by the per-brief
          receipt (`receipts.log_prep_receipt`) + this module's rule: the
          no-prep flag may only render when NO `prep_brief` receipt exists
          for that meeting id.
  F-29b — 'prep me' minted a SECOND, differently-slugged brief for the same
          meeting (`az-bus-joe-pashman-session` vs `joseph-pashman`) because
          slugs came from attendee-name phrasing. Fixed by `prep_slug`: the
          slug is a pure function of the MEETING ID, so regeneration always
          resolves to the same file (refresh-in-place, never a sibling).

This module is the shared contract BOTH paths call — the scheduled
auto-prep (orchestrator-upcoming-meetings Phase 4) and on-demand 'prep me'
(call-prep SKILL.md). The five-block layout, the visual layer's drop-empty
rule, and the sourced-talking-points floor are enforced HERE in code, not
re-improvised per fire. Depth (Standard/Deep) stays a synthesis-side concern
(the call-prep FRP1 config) — it changes how much signal the caller gathers,
never which generator runs.

THE FIVE BLOCKS (FINDINGS F-60 PROPOSAL, M-approved)
----------------------------------------------------

  1. Walk out with        -> exec_header.verdict (EXEC1; mandatory here)
  2. Changed Since Last Touch -> events + reschedules + overnight
                             attendee-scoped Gmail since the last touch
                             (replaces the prior-brief-gated "Since Your
                             Last Brief" section)
  3. Decisions Needed     -> open decisions this meeting can settle
                             (decision log), with "Decisions Already On The
                             Record" as the don't-relitigate companion
  4. Owed — Both Directions -> two-column table from
                             commitment_state.match_commitments_to_meetings
                             (counterparty OR name-mention, undated included
                             — the F-44 fix carried into prep) + parked
                             discuss-later items for these attendees
  5. Talking Points / Questions to Ask -> every line cites a source
                             (code-enforced; no ungrounded filler)

VISUAL LAYER (M directive 2026-07-08)
-------------------------------------

Stat-tile band + relationship timeline strip + OWED as a two-column table.
Substrate-derived only. A tile with no data is DROPPED here (never handed to
the renderer); `brief_writer` additionally REFUSES to render an empty tile or
a one-point timeline, so an empty frame is structurally impossible.

Stdlib only. Pure helpers except the path/receipt resolvers (os.listdir).
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from brief_path import get_brief_path, get_brief_filename, _slugify  # noqa: E402


class PrepContractError(ValueError):
    """A five-block contract violation (unsourced talking point, missing
    walk-out objective). Raised BEFORE any file is written — the caller
    rewrites the failing lines and re-assembles."""

    def __init__(self, message: str, violations: Optional[List[str]] = None):
        super().__init__(message)
        self.violations = violations or []


# ---------------------------------------------------------------------------
# Refresh-in-place identity (F-29b)
# ---------------------------------------------------------------------------

_MEETING_HASH_LEN = 8
_TITLE_SLUG_MAX = 40


def _meeting_hash(meeting_id: str) -> str:
    """Stable 8-hex digest of the meeting id — the identity token every
    Call_Prep filename for that meeting carries."""
    if not isinstance(meeting_id, str) or not meeting_id.strip():
        raise ValueError("meeting_id is required (the calendar event id)")
    return hashlib.sha1(meeting_id.strip().encode("utf-8")).hexdigest()[:_MEETING_HASH_LEN]


def prep_slug(meeting_id: str, title: Optional[str] = None) -> str:
    """THE slug for a prep brief — a pure function of the MEETING ID.

    F-29b's duplicate (`az-bus-joe-pashman-session` vs `joseph-pashman`, one
    meeting) happened because each fire improvised a slug from attendee-name
    phrasing. Here the identity lives in the meeting-id hash suffix; the
    title prefix is readability only and never part of the identity check.

    >>> prep_slug("evt_abc123", "Joe Pashman — SOD sync")[-9:] == "-" + _meeting_hash("evt_abc123")
    True
    >>> prep_slug("evt_abc123", "Joseph Pashman session") .endswith(_meeting_hash("evt_abc123"))
    True
    """
    h = _meeting_hash(meeting_id)
    prefix = _slugify(title or "")[:_TITLE_SLUG_MAX].strip("-")
    if prefix and prefix != "untitled":
        return f"{prefix}-{h}"
    return h


_EXISTING_BRIEF_RE = re.compile(
    r"^Call_Prep_(?P<slug>.+)_(?P<date>\d{4}-\d{2}-\d{2})\.docx$"
)


def find_existing_prep_brief(workspace_root, meeting_id: str) -> Optional[str]:
    """Absolute path of the existing Call_Prep_*.docx for this meeting id,
    or None. Matches by the meeting-id hash embedded in the slug, so a brief
    survives title rewording and date moves. Newest date wins if historic
    duplicates exist (the pre-fix F-29b siblings)."""
    h = _meeting_hash(meeting_id)
    meetings_dir = Path(workspace_root) / "_hq" / "meetings"
    if not meetings_dir.is_dir():
        return None
    candidates = []
    for name in os.listdir(meetings_dir):
        m = _EXISTING_BRIEF_RE.match(name)
        if not m:
            continue
        slug = m.group("slug")
        if slug == h or slug.endswith("-" + h):
            candidates.append((m.group("date"), name))
    if not candidates:
        return None
    candidates.sort()
    newest = candidates[-1][1]
    return str(meetings_dir / newest).replace("\\", "/")


def resolve_prep_brief_path(
    workspace_root,
    meeting_id: str,
    *,
    title: Optional[str] = None,
    date_iso: str,
) -> dict:
    """The ONE path a prep brief for `meeting_id` may be written to.

    Refresh-in-place contract: if a brief for this meeting id already exists
    (matched by the hash suffix, regardless of title/date drift), THAT path is
    returned and the regeneration overwrites it — no sibling is ever minted.
    Otherwise the canonical new path via brief_path.get_brief_path.

    Returns {"path": <absolute str>, "slug": <slug>, "refresh": <bool>}.
    """
    existing = find_existing_prep_brief(workspace_root, meeting_id)
    if existing:
        m = _EXISTING_BRIEF_RE.match(os.path.basename(existing))
        return {"path": existing, "slug": m.group("slug"), "refresh": True}
    slug = prep_slug(meeting_id, title)
    return {
        "path": get_brief_path(str(workspace_root), "call_prep", slug, date_iso),
        "slug": slug,
        "refresh": False,
    }


# ---------------------------------------------------------------------------
# Visual layer — stat tiles (drop-empty enforced at build time)
# ---------------------------------------------------------------------------

def build_prep_tiles(
    *,
    days_since_last_touch: Optional[int] = None,
    you_owe: Optional[int] = None,
    they_owe: Optional[int] = None,
    oldest_owed_days: Optional[int] = None,
    touch_number: Optional[int] = None,
) -> List[dict]:
    """The stat-tile band data: days since last touch · owed counts (with
    oldest age when known) · engagement touch #.

    Drop rule (M directive): a tile whose datum is unknown (None) is DROPPED,
    never rendered as an empty frame. Zero is data ("0 owed") and renders.
    Returns [] when nothing is known — the caller then omits the band section
    entirely.
    """
    tiles: List[dict] = []
    if days_since_last_touch is not None:
        val = "today" if days_since_last_touch == 0 else f"{days_since_last_touch}d"
        tiles.append({"label": "Since last touch", "value": val})
    if you_owe is not None or they_owe is not None:
        # Owed counts render as one band cell each so a single unknown side
        # doesn't fabricate a zero for the other.
        if you_owe is not None:
            v = str(you_owe)
            if you_owe and oldest_owed_days is not None:
                v += f" · oldest {oldest_owed_days}d"
            tiles.append({"label": "You owe", "value": v})
        if they_owe is not None:
            v = str(they_owe)
            if you_owe is None and they_owe and oldest_owed_days is not None:
                v += f" · oldest {oldest_owed_days}d"
            tiles.append({"label": "Owed to you", "value": v})
    if touch_number is not None and touch_number > 0:
        tiles.append({"label": "Touch", "value": f"#{touch_number}"})
    return tiles


def tiles_to_counters(tiles: List[dict]) -> List[dict]:
    """The same band for the chat widget — the canonical renderer's
    `counters` shape ({label, value}), which is already what build_prep_tiles
    emits. Explicit alias so the two surfaces provably share one source."""
    return [dict(t) for t in tiles]


# ---------------------------------------------------------------------------
# Visual layer — relationship timeline strip
# ---------------------------------------------------------------------------

_TIMELINE_MAX_POINTS = 12


def build_relationship_timeline(
    points: Iterable[dict],
    *,
    current_label: str = "this meeting",
) -> List[dict]:
    """Normalize timeline points (meetings + key emails since engagement
    start, current meeting marked) for brief_writer's `timeline` element.

    Input points: {"date": "Jun 30" (display string), "label": str,
    "current": bool?} — the caller derives them from substrate events
    (meeting / interaction / email events for these attendees). Points with
    no date or no label are dropped (substrate-derived only). Fewer than 2
    surviving points -> [] (a one-point strip is an empty frame; the caller
    omits the section).

    Caps at the newest 12 points, always keeping the current-meeting marker.
    """
    norm: List[dict] = []
    for p in points or []:
        if not isinstance(p, dict):
            continue
        date = str(p.get("date") or "").strip()
        label = str(p.get("label") or "").strip()
        if not date or not label:
            continue
        norm.append({"date": date, "label": label, "current": bool(p.get("current"))})
    if not any(p["current"] for p in norm) and norm:
        # Ensure the strip always shows where TODAY sits — append the current
        # meeting as the last point when the caller didn't mark one.
        norm.append({"date": "now", "label": current_label, "current": True})
    if len(norm) < 2:
        return []
    if len(norm) > _TIMELINE_MAX_POINTS:
        current = [p for p in norm if p["current"]]
        rest = [p for p in norm if not p["current"]]
        norm = rest[-(_TIMELINE_MAX_POINTS - len(current)):] + current
    return norm


# ---------------------------------------------------------------------------
# OWED — both directions (block 4)
# ---------------------------------------------------------------------------

def _owed_cell(row: dict, now_date: Optional[str]) -> str:
    """One table cell: title + due phrase (+ confirm tag). 'no date set'
    renders plainly, never as a blank (F-44's undated items stay visible)."""
    title = (row.get("title") or "").strip() or "(untitled item)"
    due = row.get("due")
    if due:
        phrase = f"due {due}"
        if now_date and isinstance(due, str) and due < now_date:
            phrase = f"overdue (was due {due})"
    else:
        phrase = "no date set"
    cell = f"{title} — {phrase}"
    if row.get("pending_review"):
        cell += " · needs a quick confirm"
    return cell


def build_owed_table(
    matched_rows: List[dict],
    *,
    user_person_id: Optional[str],
    now_date: Optional[str] = None,
) -> Optional[dict]:
    """The OWED block's two-column table from
    `commitment_state.match_commitments_to_meetings` rows (this meeting's
    rows only — filter by meeting_id before calling).

    Direction: owner_id == user -> "You owe"; any other owner (an attendee,
    or unowned with the user as counterparty) -> "Owed to you". Undated and
    pending_review rows are INCLUDED — the matcher already guarantees no
    due-date filter (F-44); this function must not reintroduce one.

    Returns a brief_writer `table` dict ({headers, rows, column_widths}) or
    None when there is nothing owed in either direction (the caller drops
    the section — never an empty table frame).
    """
    you_owe: List[str] = []
    owed_to_you: List[str] = []
    for row in matched_rows or []:
        cell = _owed_cell(row, now_date)
        if user_person_id and row.get("owner_id") == user_person_id:
            you_owe.append(cell)
        else:
            owed_to_you.append(cell)
    if not you_owe and not owed_to_you:
        return None
    n = max(len(you_owe), len(owed_to_you))
    rows = [
        [
            you_owe[i] if i < len(you_owe) else "",
            owed_to_you[i] if i < len(owed_to_you) else "",
        ]
        for i in range(n)
    ]
    return {
        "headers": ["You owe", "Owed to you"],
        "rows": rows,
        "column_widths": [3.0, 3.0],
    }


def discuss_later_bullets(
    discuss_events: List[dict],
    *,
    attendee_person_ids: Iterable[str],
    attendee_names: Iterable[str],
) -> List[str]:
    """Parked discuss-later items (`commitment_to_discuss` events) filtered
    to these attendees, one bullet each. Matches by person_id OR by a name
    token in the event's stored person/title fields — the same
    counterparty-or-name-mention posture as the OWED matcher.

    MLK1 (2026-07-21): the list is retired — nothing writes new
    `commitment_to_discuss` events. This reader is deliberately KEPT as a
    drain-only fossil: open items are real parked intentions and must keep
    rendering until the backlog empties (deleting reader + data together
    would strand them invisibly)."""
    ids = {i for i in (attendee_person_ids or []) if i}
    name_tokens = set()
    for n in attendee_names or []:
        for tok in re.split(r"[^a-z0-9]+", str(n).lower()):
            if len(tok) >= 3:
                name_tokens.add(tok)
    out: List[str] = []
    for ev in discuss_events or []:
        if not isinstance(ev, dict) or ev.get("type") != "commitment_to_discuss":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        pid = data.get("person_id") or data.get("counterparty_id")
        text_fields = " ".join(
            str(data.get(k) or "") for k in ("person", "person_name", "title", "summary")
        ).lower()
        matched = bool(pid and pid in ids) or any(t in text_fields for t in name_tokens)
        if matched:
            title = (data.get("title") or data.get("summary") or "").strip()
            if title:
                out.append(title)
    return out


# ---------------------------------------------------------------------------
# Block 5 — sourced talking points (no ungrounded filler, code-enforced)
# ---------------------------------------------------------------------------

# A line is "sourced" when it ends with a parenthesized cite naming where the
# claim came from — "(email, Jul 7)", "(meeting Jun 30)", "(commitment, May
# 22)", "(decision log)", "(transcript, Jul 2)". The vocabulary is the
# substrate's source families, so a decorative "(important!)" doesn't pass.
_SOURCE_CITE_RE = re.compile(
    r"\(([^()]*\b(?:email|gmail|meeting|transcript|call|commitment|decision|"
    r"calendar|session notes?|note|slack|sweep|granola)\b[^()]*)\)\s*$",
    re.IGNORECASE,
)


def unsourced_lines(lines: Iterable[str]) -> List[str]:
    """The lines that carry NO source cite. Empty list = all grounded."""
    bad: List[str] = []
    for line in lines or []:
        text = str(line).strip()
        if not text:
            continue
        if not _SOURCE_CITE_RE.search(text):
            bad.append(text)
    return bad


# ---------------------------------------------------------------------------
# Assembly — the five-block section list, one order, both paths
# ---------------------------------------------------------------------------

def assemble_prep_sections(
    *,
    walk_out_with: str,
    meeting_details: str,
    changed_lines: Optional[List[str]] = None,
    decide_lines: Optional[List[str]] = None,
    decisions_on_record: Optional[List[str]] = None,
    owed_table: Optional[dict] = None,
    discuss_bullets: Optional[List[str]] = None,
    talking_points: Optional[List[str]] = None,
    questions: Optional[List[str]] = None,
    tiles: Optional[List[dict]] = None,
    timeline: Optional[List[dict]] = None,
    supporting_sections: Optional[List[dict]] = None,
    extra_sections: Optional[List[dict]] = None,
    changed_summary: Optional[str] = None,
    decide_summary: Optional[str] = None,
    needs: Optional[str] = None,
) -> dict:
    """Compose the exec header + canonical section list for
    `brief_writer.make_brief(brief_kind="call_prep", ...)`. BOTH prep paths
    (scheduled auto-prep and on-demand 'prep me') call this — the section
    order and the block contracts live here, once.

    Contracts enforced (PrepContractError, before any render):
      - `walk_out_with` is mandatory — block 1 IS the brief's reason to exist.
      - every talking point / question carries a source cite (block 5).

    Drop rules applied (never an empty frame):
      - tiles: [] or None -> no "At a Glance" section.
      - timeline: <2 points -> no "Relationship Timeline" section.
      - owed_table None -> no "Owed — Both Directions" section.
      - any empty block list -> that section is omitted (omit-don't-pad).

    `supporting_sections` are pre-shaped brief_writer sections (Relationship
    Context, Where We Left Off, ...) inserted after the timeline;
    `extra_sections` (Cross-Project Insights, Risks / Watch-outs) append at
    the end. Depth (Standard/Deep) governs how much the CALLER gathers into
    these — never which generator runs.

    Returns {"exec_header": {...}, "sections": [...]}.
    """
    violations: List[str] = []
    if not (walk_out_with or "").strip():
        violations.append(
            "walk_out_with is required — block 1 (the one-sentence concrete win) "
            "may not be omitted"
        )
    for label, lines in (("Talking Points", talking_points), ("Questions to Ask", questions)):
        for bad in unsourced_lines(lines or []):
            violations.append(
                f"{label}: no source cite — every line names where it came from "
                f"(e.g. '(email, Jul 7)'): {bad[:100]}"
            )
    if violations:
        raise PrepContractError(
            f"{len(violations)} prep-contract violation(s); "
            "rewrite the failing lines and re-assemble",
            violations,
        )

    verdict = walk_out_with.strip()
    if not verdict.lower().startswith("walk out with"):
        verdict = f"Walk out with: {verdict}"
    changed_lines = [str(x).strip() for x in (changed_lines or []) if str(x).strip()]
    decide_lines = [str(x).strip() for x in (decide_lines or []) if str(x).strip()]
    exec_header = {
        "verdict": verdict,
        "changed": (changed_summary or "").strip()
        or (changed_lines[0] if changed_lines else "Nothing new since last touch."),
        "decide": (decide_summary or "").strip()
        or (decide_lines[0] if decide_lines else "Nothing — execution call."),
        "needs": (needs or "").strip() or "Nothing from you.",
    }

    sections: List[dict] = []
    tiles = [t for t in (tiles or []) if t]
    if tiles:
        sections.append({"heading": "At a Glance", "tiles": tiles})
    sections.append({"heading": "Meeting Details", "body": meeting_details})
    timeline = timeline or []
    if len(timeline) >= 2:
        sections.append({"heading": "Relationship Timeline", "timeline": timeline})
    for sec in supporting_sections or []:
        sections.append(sec)
    if changed_lines:
        sections.append({"heading": "Changed Since Last Touch", "bullets": changed_lines})
    on_record = [str(x).strip() for x in (decisions_on_record or []) if str(x).strip()]
    if on_record:
        sections.append({"heading": "Decisions Already On The Record", "bullets": on_record})
    if decide_lines:
        sections.append({"heading": "Decisions Needed", "bullets": decide_lines})
    if owed_table:
        sections.append({"heading": "Owed — Both Directions", "table": owed_table})
    discuss = [str(x).strip() for x in (discuss_bullets or []) if str(x).strip()]
    if discuss:
        sections.append({"heading": "Parked to Discuss", "bullets": discuss})
    tp = [str(x).strip() for x in (talking_points or []) if str(x).strip()]
    if tp:
        sections.append({"heading": "Talking Points", "bullets": tp})
    qs = [str(x).strip() for x in (questions or []) if str(x).strip()]
    if qs:
        sections.append({"heading": "Questions to Ask", "bullets": qs})
    for sec in extra_sections or []:
        sections.append(sec)

    return {"exec_header": exec_header, "sections": sections}


__all__ = [
    "PrepContractError",
    "prep_slug",
    "find_existing_prep_brief",
    "resolve_prep_brief_path",
    "build_prep_tiles",
    "tiles_to_counters",
    "build_relationship_timeline",
    "build_owed_table",
    "discuss_later_bullets",
    "unsourced_lines",
    "assemble_prep_sections",
]
