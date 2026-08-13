#!/usr/bin/env python3
"""
Loop 5 — extraction-miss learning (Phase 6, Round 3).

Two miss classes are individually visible and never collected:
  (a) the CEO manually logs a decision/commitment within ~24h of a processed
      transcript involving the same people — meeting-notes missed the extraction;
  (b) a chase email gets a reply of "already done / sent last week" —
      reconcile-sent's CRU pass missed a resolution.
Phase 5's session-sweep recovering a commitment/decision out of a chat that a
meeting SHOULD have caught is the same signal. This module collects all three,
clusters them monthly, and writes confirmed patterns as few-shot exemplars to
`_hq/data/extraction-hints.md` — read by meeting-notes at extraction time and by
cru_match for resolution language, so the substrate's front door improves from
its own documented failures.

  CAPTURE (writers tag; this module supplies the detectors)
    - decision-log / commitment writers call `find_recent_meeting(new_event,
      meeting_events)` and, on a hit, tag the event `data.extraction_miss=True`
      with the source meeting ref.
    - reconcile-sent classifies a reply via `is_resolution_miss(text)` and marks
      `data.resolution_miss=True` on the outcome event.
    - the session-sweep's recoveries (`source_ref = "session:<id>"`) that overlap
      a processed meeting are consumed here as extraction-miss signal too.

  LEARN (insight-generator Loop 5 pass, monthly)
    `load_misses` → `cluster_misses` → `propose_hints` → on approval,
    `append_extraction_hint` appends an exemplar to `_hq/data/extraction-hints.md`.

Pure detectors + clustering; one small append helper. stdlib only.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Dict, List, Optional

try:
    from events_io import iter_events
    from event_time import event_time, event_dt
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from events_io import iter_events  # type: ignore
    from event_time import event_time, event_dt  # type: ignore

WINDOW_HOURS = 24
MIN_CLUSTER = 3          # ≥3 misses share a pattern before it becomes a hint
CAP = 3
PASS_NAME = "loop5_extraction_hints"

# "already done" resolution-miss phrases (a chase reply that says it was already
# handled — the CRU pass should have caught the completion).
_RESOLUTION_MISS_PATTERNS = [
    r"\balready (done|sent|handled|took care|taken care|shipped|delivered)\b",
    r"\b(sent|did|handled|finished|closed) (it|this|that|those)?\s*(last|earlier)\b",
    r"\bthat'?s?\s+(already )?(done|handled|sorted|taken care of)\b",
    r"\bwe (already|did) (sent|send|do|did|handle[d]?)\b",
    r"\bsent (it|that|this) (over|out)\b.*\b(last|earlier|already)\b",
]
_RESOLUTION_MISS_RE = re.compile("|".join(_RESOLUTION_MISS_PATTERNS), re.IGNORECASE)


def is_resolution_miss(text: str) -> bool:
    """True when a chase reply says the thing was already done — a resolution the
    CRU pass missed. Pure."""
    return bool(_RESOLUTION_MISS_RE.search(text or ""))


def _person_ids(ev: dict) -> set:
    # BUG-8244: fold every meeting-binding variant — the two-field read made
    # extraction-miss telemetry under-report, which is how the missing meeting
    # binding stayed invisible to the quality loop that exists to catch it.
    try:
        from event_refs import meeting_person_ids
        return meeting_person_ids(ev)
    except Exception:
        data = ev.get("data") or {}
        return set(ev.get("person_ids") or []) | set(data.get("person_ids") or [])


def find_recent_meeting(
    new_event: dict, meeting_events: List[dict], *, window_hours: int = WINDOW_HOURS
) -> Optional[dict]:
    """If `new_event` (a manually-logged decision/commitment) was created within
    `window_hours` AFTER a processed meeting sharing ≥1 attendee, return that
    meeting's ref `{meeting_id, source_ref}` — the extraction miss. Else None.
    Pure."""
    new_dt = event_dt(new_event)
    if new_dt is None:
        return None
    new_people = _person_ids(new_event)
    if not new_people:
        return None
    best = None
    for m in meeting_events:
        if m.get("type") not in ("meeting_processed", "meeting"):
            continue
        m_dt = event_dt(m)
        if m_dt is None or m_dt > new_dt:
            continue
        if (new_dt - m_dt).total_seconds() > window_hours * 3600:
            continue
        if not (_person_ids(m) & new_people):
            continue
        if best is None or event_dt(best) is None or m_dt > event_dt(best):
            best = m
    if best is None:
        return None
    bd = best.get("data") or {}
    return {"meeting_id": bd.get("meeting_id") or bd.get("source_ref") or "",
            "source_ref": bd.get("source_ref") or ""}


def load_misses(workspace_root, *, since_iso: Optional[str] = None) -> List[dict]:
    """Collect all miss signals: events tagged `data.extraction_miss`, outcome
    events tagged `data.resolution_miss`, and session-sweep recoveries
    (`data.source_ref` starting `session:`) that overlap a processed meeting.
    Returns normalized rows {kind, summary, meeting_type, ts}. Never raises."""
    try:
        events = list(iter_events(Path(workspace_root) / "_hq" / "data", since_ts=since_iso))
    except Exception:
        return []
    meetings = [e for e in events if e.get("type") in ("meeting_processed", "meeting")]
    out: List[dict] = []
    for ev in events:
        data = ev.get("data") or {}
        ts = event_time(ev)
        if since_iso and ts and str(ts) < str(since_iso):
            continue
        if data.get("extraction_miss"):
            out.append({"kind": "extraction", "summary": data.get("title")
                        or data.get("summary") or "", "meeting_type":
                        data.get("meeting_type") or "", "ts": ts})
        elif data.get("resolution_miss"):
            out.append({"kind": "resolution", "summary": data.get("summary")
                        or data.get("recipient") or "", "meeting_type": "", "ts": ts})
        elif (isinstance(data.get("source_ref"), str)
              and data["source_ref"].startswith("session:")
              and ev.get("type") in ("commitment", "decision")):
            # A sweep recovery that overlaps a processed meeting = extraction miss.
            if find_recent_meeting(ev, meetings):
                out.append({"kind": "extraction", "summary": data.get("title")
                            or data.get("summary") or "", "meeting_type": "",
                            "ts": ts, "via": "session_sweep"})
    return out


def _signature(summary: str) -> str:
    """A coarse phrasing signature: the two most salient content tokens."""
    toks = [t for t in re.findall(r"[a-z0-9']+", (summary or "").lower())
            if len(t) >= 4]
    toks = sorted(set(toks))[:2]
    return " ".join(toks) or "unclassified"


def cluster_misses(rows: List[dict], *, min_cluster: int = MIN_CLUSTER) -> Dict[str, dict]:
    """Group misses by (kind, meeting_type, phrasing signature); keep clusters at
    or above the floor. Returns {cluster_key: {kind, meeting_type, count,
    examples}}. Pure."""
    agg: Dict[tuple, dict] = {}
    for r in rows:
        sig = _signature(r.get("summary", ""))
        key = (r.get("kind"), r.get("meeting_type") or "", sig)
        slot = agg.setdefault(key, {"kind": r.get("kind"),
                                    "meeting_type": r.get("meeting_type") or "",
                                    "signature": sig, "count": 0, "examples": []})
        slot["count"] += 1
        if len(slot["examples"]) < 3 and r.get("summary"):
            slot["examples"].append(r["summary"][:160])
    return {f"{k[0]}|{k[1]}|{k[2]}": v for k, v in agg.items()
            if v["count"] >= min_cluster}


def _fp(cluster_key: str) -> str:
    return "exh_" + hashlib.sha256(cluster_key.encode("utf-8")).hexdigest()[:16]


def propose_hints(
    clusters: Dict[str, dict],
    *,
    existing_hints: Optional[List[str]] = None,
    cooldown_fingerprints: Optional[set] = None,
    cap: int = CAP,
) -> List[dict]:
    """Turn confirmed miss clusters into extraction-hint proposals. Pure. Returns
    up to `cap`: {fingerprint, cluster_key, kind, count, hint, plain}."""
    existing = {(h or "").strip().lower() for h in (existing_hints or [])}
    cooling = cooldown_fingerprints or set()
    out: List[dict] = []
    for key, c in sorted(clusters.items(), key=lambda kv: -kv[1]["count"]):
        example = c["examples"][0] if c["examples"] else c["signature"]
        if c["kind"] == "resolution":
            hint = (f"Treat replies like \"{example}\" as completion of the open "
                    f"item — resolve, don't re-chase.")
        else:
            mt = f" in {c['meeting_type']} meetings" if c["meeting_type"] else ""
            hint = (f"Capture items phrased like \"{example}\"{mt} — these were "
                    f"missed and logged manually {c['count']} times.")
        if hint.strip().lower() in existing:
            continue
        fp = _fp(key)
        if fp in cooling:
            continue
        out.append({
            "fingerprint": fp, "cluster_key": key, "kind": c["kind"],
            "count": c["count"], "hint": hint,
            "plain": (f"I've missed {c['count']} similar items you had to log by "
                      f"hand — want me to learn to catch that phrasing?"),
        })
        if len(out) >= cap:
            break
    return out


# ---------------------------------------------------------------------------
# Store — _hq/data/extraction-hints.md (read by meeting-notes + cru_match)
# ---------------------------------------------------------------------------

def _hints_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "extraction-hints.md"


def load_extraction_hints(workspace_root) -> List[str]:
    """The learned hint lines (bulleted). Never raises. meeting-notes / cru_match
    read this alongside their baked-in prompt."""
    path = _hints_path(workspace_root)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [ln.strip().lstrip("-* ").strip() for ln in lines
            if ln.strip().startswith(("-", "*"))]


def append_extraction_hint(workspace_root, hint: str) -> bool:
    """Append one approved exemplar to `_hq/data/extraction-hints.md` (additive,
    deduped; creates with a header if absent). Returns True on write. Never
    raises."""
    hint = (hint or "").strip()
    if not hint:
        return False
    if hint.strip().lower() in {h.strip().lower() for h in load_extraction_hints(workspace_root)}:
        return False
    path = _hints_path(workspace_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if not existing.strip():
            existing = ("# Extraction hints (learned)\n\n"
                        "Few-shot exemplars from documented extraction/resolution "
                        "misses. meeting-notes reads these at extraction time; "
                        "cru_match reads them for resolution language.\n\n")
        if not existing.endswith("\n"):
            existing += "\n"
        try:
            from atomic_write import atomic_write_text
            atomic_write_text(path, existing + f"- {hint}\n", holder="extraction_hints")
        except Exception:
            path.write_text(existing + f"- {hint}\n", encoding="utf-8")
        return True
    except Exception:
        return False


__all__ = [
    "WINDOW_HOURS", "MIN_CLUSTER", "CAP", "PASS_NAME",
    "is_resolution_miss", "find_recent_meeting", "load_misses",
    "cluster_misses", "propose_hints",
    "load_extraction_hints", "append_extraction_hint",
]
