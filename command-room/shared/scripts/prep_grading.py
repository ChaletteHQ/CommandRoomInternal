#!/usr/bin/env python3
"""
Loop 3 — prep-brief accuracy grading (Phase 6, Round 3).

call-prep writes a brief BEFORE a meeting; hours later past-meetings processes
the transcript of the SAME meeting. The two are never compared — the product
holds both halves of a graded exam and never grades it. This module grades it:

  CAPTURE (past-meetings, after meeting-notes runs)
    join transcript → prep brief by calendar event id (both live in
    `_hq/meetings/`), then `grade_brief(predicted_sections, transcript_topics)`
    scores which predicted talking-points / risks / questions actually came up,
    which topics came up unpredicted, and which sections were rendered but never
    relevant. `build_prep_feedback_event(...)` writes a `prep_feedback` event.

  LEARN (insight-generator Pass 15, monthly)
    aggregate per meeting-type; `propose_section_weights(stats, ...)` proposes
    section-weight changes to call-prep's config ("risks section has been
    empty-but-rendered in 8 of 9 internal 1:1s — drop it for internal
    meetings?"). Feeds the EXISTING show-then-tune config store
    (`skill_config_writer` for `call-prep`), so no new store.

The semantic match (did this predicted point come up in the transcript?) is
supplied by the caller as `topics` + a `matcher`; the deterministic bookkeeping,
aggregation, and proposal machinery live here (pure, testable). stdlib only.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

try:
    from events_io import iter_events
    from event_time import event_time
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from events_io import iter_events  # type: ignore
    from event_time import event_time  # type: ignore

# Sections whose ITEMS are predictions gradable against the transcript.
GRADABLE_SECTIONS = ("Talking Points", "Risks / Watch-outs", "Questions to Ask",
                     "Decisions Needed")
MIN_MEETINGS = 6          # small-n floor before proposing a weight change
EMPTY_RATE = 0.8          # section empty-but-rendered ≥80% of the time → drop
CAP = 3
PASS_NAME = "pass15_prep_grading"


def _tokens(s: str) -> set:
    return {t for t in re.findall(r"[a-z0-9']+", (s or "").lower()) if len(t) >= 3}


def default_matcher(item: str, topics: List[str], *, min_overlap: int = 2) -> bool:
    """A predicted item 'came up' if it shares ≥min_overlap content tokens with
    any transcript topic. Deterministic fallback; the pass can pass a smarter
    (LLM-backed) matcher."""
    it = _tokens(item)
    if not it:
        return False
    for topic in topics or []:
        if len(it & _tokens(topic)) >= min_overlap:
            return True
    return False


def grade_brief(
    predicted_sections: Dict[str, List[str]],
    transcript_topics: List[str],
    *,
    matcher: Callable[[str, List[str]], bool] = default_matcher,
) -> dict:
    """Score a prep brief against its meeting transcript. Pure. Returns:
      {sections_hit: {section: n_hit}, sections_rendered: {section: n_items},
       sections_empty: [sections rendered with 0 hits],
       unpredicted_topics: [topics no predicted item covered]}"""
    hit: Dict[str, int] = {}
    rendered: Dict[str, int] = {}
    covered_topics = set()
    for section, items in (predicted_sections or {}).items():
        items = [i for i in (items or []) if i and i.strip()]
        rendered[section] = len(items)
        n_hit = 0
        for it in items:
            if matcher(it, transcript_topics):
                n_hit += 1
                for topic in transcript_topics or []:
                    if matcher(it, [topic]):
                        covered_topics.add(topic)
        hit[section] = n_hit
    empty = [s for s, n in rendered.items() if n > 0 and hit.get(s, 0) == 0]
    unpredicted = [t for t in (transcript_topics or []) if t not in covered_topics]
    return {"sections_hit": hit, "sections_rendered": rendered,
            "sections_empty": empty, "unpredicted_topics": unpredicted}


def build_prep_feedback_event(
    *, meeting_id: str, meeting_type: str, grade: dict,
    person_ids: Optional[List[str]] = None, source_skill: str = "past-meetings",
) -> dict:
    """A `prep_feedback` event (no seq/ts — append_event stamps)."""
    return {
        "type": "prep_feedback",
        "source_skill": source_skill,
        "person_ids": person_ids or [],
        "data": {
            "meeting_id": meeting_id,
            "meeting_type": meeting_type,
            "sections_hit": grade.get("sections_hit", {}),
            "sections_rendered": grade.get("sections_rendered", {}),
            "sections_missed": grade.get("sections_empty", []),
            "unpredicted_topics": grade.get("unpredicted_topics", []),
        },
    }


def load_prep_feedback(workspace_root, *, since_iso: Optional[str] = None) -> List[dict]:
    """prep_feedback rows in the window. Never raises."""
    out: List[dict] = []
    try:
        events = iter_events(Path(workspace_root) / "_hq" / "data", since_ts=since_iso)
    except Exception:
        return out
    for ev in events:
        if ev.get("type") != "prep_feedback":
            continue
        ts = event_time(ev)
        if since_iso and ts and str(ts) < str(since_iso):
            continue
        out.append(ev.get("data") or {})
    return out


def aggregate_section_stats(rows: List[dict]) -> Dict[tuple, dict]:
    """Per (meeting_type, section): {rendered, empty}. Pure."""
    agg: Dict[tuple, dict] = {}
    for r in rows:
        mtype = r.get("meeting_type") or "other"
        rendered = r.get("sections_rendered") or {}
        empty = set(r.get("sections_missed") or [])
        for section, n in rendered.items():
            if n <= 0:
                continue
            slot = agg.setdefault((mtype, section), {"rendered": 0, "empty": 0})
            slot["rendered"] += 1
            if section in empty:
                slot["empty"] += 1
    return agg


def _fp(meeting_type: str, section: str) -> str:
    raw = f"{(meeting_type or '').lower()}\x00{(section or '').lower()}"
    return "pwt_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def propose_section_weights(
    stats: Dict[tuple, dict],
    *,
    existing_weights: Optional[dict] = None,
    cooldown_fingerprints: Optional[set] = None,
    cap: int = CAP,
    min_meetings: int = MIN_MEETINGS,
    empty_rate: float = EMPTY_RATE,
) -> List[dict]:
    """Propose dropping a section for a meeting-type where it's consistently
    rendered-but-empty. Pure. Returns up to `cap`:
      {fingerprint, meeting_type, section, weight, rendered, empty, empty_rate, plain}"""
    existing = existing_weights or {}
    cooling = cooldown_fingerprints or set()
    out: List[dict] = []
    for (mtype, section), s in sorted(stats.items(), key=lambda kv: -kv[1]["empty"]):
        if s["rendered"] < min_meetings:
            continue
        rate = s["empty"] / s["rendered"] if s["rendered"] else 0.0
        if rate < empty_rate:
            continue
        # already dropped for this meeting-type?
        if existing.get(mtype, {}).get(section) == 0:
            continue
        fp = _fp(mtype, section)
        if fp in cooling:
            continue
        out.append({
            "fingerprint": fp, "meeting_type": mtype, "section": section,
            "weight": 0, "rendered": s["rendered"], "empty": s["empty"],
            "empty_rate": round(rate, 2),
            "plain": (f"The {section} section came up empty in {s['empty']} of your "
                      f"last {s['rendered']} {mtype} meetings — drop it for those?"),
        })
        if len(out) >= cap:
            break
    return out


# ---------------------------------------------------------------------------
# call-prep config extension (per-meeting-type section weights)
# ---------------------------------------------------------------------------

def section_weight(config: dict, meeting_type: str, section: str,
                   default: float = 1.0) -> float:
    """The learned weight for a section in a meeting-type (1.0 = render normally,
    0 = drop). Read by call-prep before rendering. Pure; None-safe."""
    sw = (config or {}).get("section_weights") or {}
    return sw.get(meeting_type, {}).get(section, default)


def set_section_weight(config: dict, meeting_type: str, section: str, weight: float) -> dict:
    """Return config with a section weight set (does not persist — the caller
    saves via skill_config_writer.save_skill_config)."""
    config = dict(config or {})
    sw = dict(config.get("section_weights") or {})
    mt = dict(sw.get(meeting_type) or {})
    mt[section] = weight
    sw[meeting_type] = mt
    config["section_weights"] = sw
    return config


__all__ = [
    "GRADABLE_SECTIONS", "MIN_MEETINGS", "EMPTY_RATE", "CAP", "PASS_NAME",
    "default_matcher", "grade_brief", "build_prep_feedback_event",
    "load_prep_feedback", "aggregate_section_stats", "propose_section_weights",
    "section_weight", "set_section_weight",
]
