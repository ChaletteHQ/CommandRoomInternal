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

  LEARN (the `learning` job's prep leg, weekly — Pass 15 is retired)
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

_MISSING = object()


def section_weight(config: dict, meeting_type: str, section: str,
                   default: float = 1.0) -> float:
    """The learned weight for a section in a meeting-type (1.0 = render normally,
    0 = drop). Read by call-prep before rendering. Pure; None-safe.

    WRITER AND READER AGREE ON ONE SPELLING. call-prep passes the section
    names it renders — `Risks / Watch-outs`, `Questions to Ask` — while the
    learning job writes the normalized join key (`risks watch outs`), because
    the same section has appeared in the graded feedback under three dash
    characters and two cases. A lookup that only tried the caller's literal
    string found NOTHING the job wrote: a store with a reader that cannot see
    it is a store with no reader. So this tries the caller's exact spelling
    first (any weight an older interactive confirm wrote under a raw name
    still answers), then the normalized key."""
    sw = (config or {}).get("section_weights") or {}
    if not isinstance(sw, dict):
        return default
    exact = sw.get(meeting_type)
    if isinstance(exact, dict):
        found = exact.get(section, _MISSING)
        if found is not _MISSING:
            return found
    norm_mt = normalize_meeting_type(meeting_type)
    norm_sec = normalize_section_name(section)
    folded = sw.get(norm_mt)
    if isinstance(folded, dict):
        found = folded.get(norm_sec, _MISSING)
        if found is not _MISSING:
            return found
    return default


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


# ---------------------------------------------------------------------------
# LEARN1 — the normalized aggregate, the de-emphasis tier, and the writer
#
# THE DEFECT THIS CLOSES: `aggregate_section_stats` keys on the section string
# EXACTLY as the renderer spelled it. Across the banked feedback the SAME
# section appears as "Questions to Ask" and "questions to ask", and the risks
# section appears under three different dash characters — so one section's
# evidence sits in three buckets, none of which reaches the floor. Section
# names are join keys; a re-spelling silently splits the history.
# ---------------------------------------------------------------------------

# THE ONLY ACT ON THIS RAIL IS THE SHIPPED DROP. There is no second, gentler
# tier here. A "render this section shorter at half the evidence" act was
# built and taken back out before merge: it is a NEW act at a NEW evidence
# bar, SPEC_LEARN1 §6 puts changes to floors and caps out of scope, and the
# worst-case paragraph the customer was shown when he approved automatic
# applying says the system can hide a section that was empty in four of five
# briefs. An act that fires at one in two makes that sentence false. It needs
# his word and a rewritten worst-case paragraph before it exists, not a
# builder's judgement — so the floor stands at `EMPTY_RATE`, and on a book
# that carries no evidence at that floor this leg correctly does nothing.

_DASHES_RE = re.compile("[‐-―]")
_NONWORD_RE = re.compile(r"[^a-z0-9]+")
# Meeting-type spellings that name the same kind of meeting. PREFIX STRIPS
# ONLY. Two meeting types that a person would describe differently must never
# be folded together: a section empty in a one-to-one is not evidence about a
# hiring interview, and a weight learned on a pooled bucket would apply to
# both. `internal_1_1` and `internal_hiring` therefore stay their own keys —
# folding them into `internal` is a pooling decision, not a normalization.
_MEETING_TYPE_ALIASES = {
    "client_support": "support",
    "client_onboarding": "onboarding",
    "client_working_session": "working_session",
}


def normalize_section_name(section) -> str:
    """The join key for a prep-brief section. Case-folded, dash-variants
    unified, punctuation collapsed to single spaces. Pure."""
    text = _DASHES_RE.sub("-", str(section or "")).lower()
    return _NONWORD_RE.sub(" ", text).strip()


def normalize_meeting_type(meeting_type) -> str:
    """The join key for a meeting type. Pure. Unknown spellings pass through
    normalized rather than being forced into a known bucket."""
    text = _DASHES_RE.sub("-", str(meeting_type or "")).lower()
    key = _NONWORD_RE.sub(" ", text).strip().replace(" ", "_")
    return _MEETING_TYPE_ALIASES.get(key, key) or "other"


def aggregate_section_stats_normalized(rows: List[dict]) -> Dict[tuple, dict]:
    """Per (normalized meeting_type, normalized section): {rendered, empty}.
    Pure. Sits BESIDE `aggregate_section_stats` rather than replacing it:
    the raw form is what the shipped Pass-15 prose and its tests read, and
    displacing it would rewrite history's meaning.

    `empty` is derived from `sections_hit` when the row carries it (a section
    rendered with zero hits), falling back to the `sections_missed` list —
    the two agree on every row the grader wrote, and the derivation survives
    a section re-spelling between the two fields."""
    agg: Dict[tuple, dict] = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        mtype = normalize_meeting_type(r.get("meeting_type") or "other")
        rendered = r.get("sections_rendered") or {}
        hits = r.get("sections_hit") or {}
        missed = {normalize_section_name(s)
                  for s in (r.get("sections_missed") or [])}
        if not isinstance(rendered, dict):
            continue
        for section, n in rendered.items():
            try:
                if int(n) <= 0:
                    continue
            except (TypeError, ValueError):
                continue
            key = normalize_section_name(section)
            slot = agg.setdefault((mtype, key), {"rendered": 0, "empty": 0})
            slot["rendered"] += 1
            if isinstance(hits, dict) and section in hits:
                if not hits.get(section):
                    slot["empty"] += 1
            elif key in missed:
                slot["empty"] += 1
    return agg


def weight_change_line(change: dict, *, rendered=None, empty=None) -> str:
    """The customer sentence for a weight change that ACTUALLY HAPPENED.

    Built from the WRITTEN change — `{meeting_type, section, from, to}` as
    the writer returned it — never from the proposal. A proposal is a thing
    the job wanted to do; the write is the thing it did, and a surface that
    announces the proposal announces effects the write may not have produced.
    Returns "" for a change that moved nothing, so the caller has no sentence
    to narrate when nothing happened.

    The evidence numbers are optional and only sharpen the sentence; without
    them it still says exactly what changed."""
    if not isinstance(change, dict):
        return ""
    mtype = str(change.get("meeting_type") or "").replace("_", " ").strip()
    section = str(change.get("section") or "").strip()
    if not mtype or not section:
        return ""
    to = change.get("to")
    if change.get("from") == to:
        return ""
    if to != 0:
        # The only act on this rail is the drop. A weight this function does
        # not have a sentence for is a weight the customer should not be told
        # a story about.
        return ""
    because = ""
    try:
        if int(empty) > 0 and int(rendered) > 0:
            because = (" — it came up empty in " + str(int(empty))
                       + " of the last " + str(int(rendered)))
    except (TypeError, ValueError):
        because = ""
    return ("Stopped putting the " + section + " section in your " + mtype
            + " prep briefs" + because + ".")


def set_section_weights(workspace_root, updates: List[dict], *,
                        origin: str = "learning",
                        event_extra: Optional[dict] = None) -> dict:
    """Persist a batch of section weights into call-prep's skill config.

    The wrapper the proposers never had: `set_section_weight` is pure and
    `save_skill_config` is the store, and nothing in the tree joined them —
    which is why a workspace with seventy graded briefs carried no weights.

    Returns {"changes": [{meeting_type, section, from, to}], "config"}.
    `from` is the weight the config held before (1.0 when it held none), so
    the reverser has its snapshot without re-reading anything."""
    from skill_config_writer import load_skill_config, save_skill_config

    stored = load_skill_config(workspace_root, "call-prep") or {}
    cfg = stored.get("config") if isinstance(stored, dict) else None
    cfg = dict(cfg) if isinstance(cfg, dict) else {}
    had_config = bool(stored)
    changes: List[dict] = []
    for update in updates or []:
        mtype = update.get("meeting_type")
        section = update.get("section")
        if not mtype or not section:
            continue
        new_weight = update.get("weight", 0)
        prior = section_weight(cfg, mtype, section)
        if prior == new_weight:
            continue
        cfg = set_section_weight(cfg, mtype, section, new_weight)
        changes.append({"meeting_type": mtype, "section": section,
                        "from": prior, "to": new_weight})
    if not changes:
        return {"changes": [], "config": cfg}
    save_skill_config(workspace_root, "call-prep", cfg,
                      is_reconfigure=had_config, origin=origin,
                      event_extra=event_extra or None)
    return {"changes": changes, "config": cfg}


__all__ = [
    "GRADABLE_SECTIONS", "MIN_MEETINGS", "EMPTY_RATE", "CAP", "PASS_NAME",
    "default_matcher", "grade_brief", "build_prep_feedback_event",
    "load_prep_feedback", "aggregate_section_stats", "propose_section_weights",
    "section_weight", "set_section_weight",
    "normalize_section_name", "normalize_meeting_type",
    "aggregate_section_stats_normalized", "weight_change_line",
    "set_section_weights",
]
