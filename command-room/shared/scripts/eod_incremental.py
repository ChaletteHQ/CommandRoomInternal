#!/usr/bin/env python3
"""
SPEC EODSPEED1 — the close reconciles; it does not fetch (2026-08-26).

THE MEASUREMENT THIS SPENDS
---------------------------
EODPHASE1's live phase records (six fires, 2026-08-24 → 08-26) put the
instrumented pack build at 7–10 SECONDS while `duration_ms` ran 9–27 minutes.
The other ~99% of wall clock is outside the pack builder: connector fetches
(transcripts, mail), the session's own composition, and redundant re-scans —
one fire skipped 237 stale-evidence rows it still re-walked, and `rerun_of`
fires re-fetch what the prior fire already read. The reconciliation code is
not slow. The close was doing the day's FETCHING at 5 PM.

WHAT THIS MODULE IS
-------------------
The support layer for running the End of Day's capture leg INCREMENTALLY —
during the day, silently, through the exact same canonical writers and gates
the 5 PM close runs (`meeting_capture.route_meeting_captures`,
`build_meeting_event`, `brief_writer`, the CRU passes) — so the close finds
the day's material already on disk and re-verifies instead of fetching.
Three pieces, none of which is a new write path:

  1. THE CAPTURE-PASS RECEIPT. The incremental pass is the `meeting-capture`
     job inside the already-authorized `maintenance` scheduled task
     (`maintenance_dispatcher.MAINTENANCE_JOBS`) — it piggybacks the 6:45 /
     12:45 / 17:45 slots that already exist on every machine and registers
     ZERO new scheduled tasks. `log_capture_pass_receipt` is its receipt
     writer: a `pack_run` under task id `meeting-capture`, which is what the
     dispatcher's due-ness rule reads and what `catchup_window
     ("meeting-capture", ...)` resumes from. It is NEVER a receipt under
     `past-meetings`: a pack_run on that series would arm `skip_render`
     against the real 5 PM close and split the day-close history — the one
     thing EOD2 exists to prevent.

  2. THE CRU WALK LEDGER (the 237-row class). Layer 3 of the circularity
     fence (`cru_match.match_transcript_to_commitments`) drops candidates
     captured after the meeting happened — deterministically, because a
     commitment event's `ts` never changes once appended and the meeting's
     own start never moves. A re-fire therefore re-derives the exact same
     skip verdicts over the exact same rows, after re-fetching the same
     transcript to do it. The ledger records that a transcript was walked
     COMPLETELY (evidence ref + evidence ts + when + how many stale rows it
     refused), and `already_walked` lets the next fire inside the evidence
     window honor that record instead of re-walking. Honoring is SOUND, not
     just cheap: every commitment appended after the recorded walk carries a
     `ts` newer than the walk, and the walk postdates the meeting, so layer 3
     would drop every one of them anyway — a re-walk can only reproduce the
     recorded verdicts plus more stale refusals. Any doubt (a different
     evidence ts, a walk older than the evidence window, an entry that never
     said `complete`) and the answer is None: the fire walks normally.
     NOTHING here relaxes a floor — the matcher is untouched, and a ledger
     miss behaves byte-identically to the pre-EODSPEED1 build.

  3. THE PRIOR-CAPTURE BRIEFS READ. Incremental passes are SILENT — writes
     and receipts only, no chat surface; the close remains the one narrator.
     That makes the close responsible for narrating what the passes captured:
     `prior_briefed_refs` returns the briefs written by the background pass
     since the last day-close, so the close's Meeting briefs section and its
     coverage sentence (via `end_of_day.MEETING_BRIEFED_PRIOR`) can carry
     them. Without this the day's briefs would reach nobody — the pass does
     not post and the close would fold them into a silent
     `already_processed` reduction.

THE FENCES (binding, from the spec)
-----------------------------------
* EQUIVALENCE — the same synthetic day through incremental vs bulk paths
  yields a byte-identical verdict set (closures / slipped / confirm /
  synthesis grounding). Pinned by tests/run_eodspeed1_test.py.
* SILENCE — an incremental pass posts nothing, notifies nothing. Its whole
  output is substrate writes plus its own receipt.
* DEGRADE — a day where incremental passes never ran (machine off) degrades
  to fetch-at-close exactly. The close's own window computation
  (`catchup_window("past-meetings", floor_hours=24)`) is untouched, an empty
  walk ledger changes no verdict, and an absent `meeting-capture` receipt
  narrows nothing. Slower, complete, never a thinner close.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


# The maintenance job id the incremental capture pass runs under, and the
# receipt task id its passes are logged against. A job id, not a scheduled
# task: it rides the already-authorized `maintenance` task and registers
# nothing (MAINT1's whole point).
CAPTURE_JOB_ID = "meeting-capture"

# How long a recorded CRU walk stays honorable. Aligned with the capture
# leg's own 30-day fetch ceiling (catchup.DEFAULT_CAP_DAYS): evidence older
# than the window can no longer be re-walked by any fire, so a record about
# it serves nobody and is pruned.
EVIDENCE_WINDOW_DAYS = 30

# Where the walk ledger lives. `.system` is the workspace's own diagnostic
# sidecar home (widgets, briefs audit copies) — this is bookkeeping about
# work already receipted through canonical writers, never substrate.
WALK_LEDGER_RELPATH = "_hq/.system/cru_walk_ledger.json"


# ---------------------------------------------------------------------------
# Time plumbing (kept identical in posture to catchup.py: best-effort,
# machine-agnostic, unparseable input degrades to "no answer", never raises)
# ---------------------------------------------------------------------------

def _parse_utc(value) -> Optional[_dt.datetime]:
    """ISO string / datetime → aware UTC datetime, or None."""
    if isinstance(value, _dt.datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        raw = value.strip().replace("Z", "+00:00")
        try:
            dt = _dt.datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc)


def _now_utc(now=None) -> _dt.datetime:
    parsed = _parse_utc(now)
    if parsed is not None:
        return parsed
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt: Optional[_dt.datetime]) -> Optional[str]:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


# ---------------------------------------------------------------------------
# The CRU walk ledger (the 237-row class)
# ---------------------------------------------------------------------------

def _ledger_path(workspace_root) -> Path:
    return Path(workspace_root) / WALK_LEDGER_RELPATH


def load_walk_ledger(workspace_root) -> dict:
    """The ledger, `{evidence_ref: entry}` — `{}` on any failure. A corrupt
    or missing ledger degrades to "never walked", which re-walks: the safe
    direction, and byte-identical to the pre-EODSPEED1 build."""
    try:
        raw = _ledger_path(workspace_root).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def already_walked(workspace_root, *, evidence_ref: str,
                   evidence_ts, now=None) -> Optional[dict]:
    """The recorded walk this fire may honor, or None (walk normally).

    Honored ONLY when every one of these holds — any doubt re-walks:

      * an entry exists for this exact `evidence_ref`;
      * the entry says `complete` — a walk that did not run to its appends
        is not a walk;
      * the stored `evidence_ts` and the caller's normalize to the SAME
        instant (a different evidence window is a different question), and
        BOTH are present — evidence with no time is evidence layer 3 could
        never judge, so there is no skip verdict to honor;
      * the recorded `walked_at` is not older than `EVIDENCE_WINDOW_DAYS`;
      * `walked_at` postdates `evidence_ts` — the soundness condition: it is
        what guarantees every commitment appended since the walk is stale
        under layer 3's strict ordering, so a re-walk could only reproduce
        the record.

    Returns the entry dict (`evidence_ts`, `walked_at`, `n_stale`,
    `n_results`, `complete`) so the caller can carry `n_stale` onto its
    receipt as the honored count.
    """
    ref = str(evidence_ref or "").strip()
    if not ref:
        return None
    entry = load_walk_ledger(workspace_root).get(ref)
    if not _honorable(entry, evidence_ts, now):
        return None
    return dict(entry)


def _honorable(entry, evidence_ts, now=None) -> bool:
    """THE HONOR FENCE — every condition `already_walked` requires, in one
    named function so the mutation suite can remove it whole and prove the
    tests red (`tests/run_eodspeed1_test.py` MUT-LEDGER). Returns True only
    when the recorded walk provably stands for a re-walk of THIS evidence."""
    if not isinstance(entry, dict) or entry.get("complete") is not True:
        return False
    want = _parse_utc(evidence_ts)
    have = _parse_utc(entry.get("evidence_ts"))
    if want is None or have is None or want != have:
        return False
    walked_at = _parse_utc(entry.get("walked_at"))
    if walked_at is None or walked_at < have:
        return False
    if _now_utc(now) - walked_at > _dt.timedelta(days=EVIDENCE_WINDOW_DAYS):
        return False
    return True


def record_walk(workspace_root, *, evidence_ref: str, evidence_ts,
                n_stale: int = 0, n_results: int = 0, now=None) -> dict:
    """Record that one transcript's CRU walk ran TO COMPLETION — call only
    AFTER the walk's own appends landed. Merges, prunes entries older than
    the evidence window, writes atomically. Best-effort: a ledger that
    cannot be written costs a re-walk, never the fire — the return says
    whether it landed (`{"recorded": bool, "n_entries": int}`)."""
    ref = str(evidence_ref or "").strip()
    ev_at = _parse_utc(evidence_ts)
    now_dt = _now_utc(now)
    out = {"recorded": False, "n_entries": 0}
    if not ref or ev_at is None:
        return out  # nothing honorable could ever match this entry
    ledger = load_walk_ledger(workspace_root)
    ledger[ref] = {
        "evidence_ts": _iso(ev_at),
        "walked_at": _iso(now_dt),
        "n_stale": int(n_stale) if isinstance(n_stale, int) else 0,
        "n_results": int(n_results) if isinstance(n_results, int) else 0,
        "complete": True,
    }
    horizon = now_dt - _dt.timedelta(days=EVIDENCE_WINDOW_DAYS)
    pruned = {}
    for key, entry in ledger.items():
        if not isinstance(entry, dict):
            continue
        walked_at = _parse_utc(entry.get("walked_at"))
        if walked_at is None or walked_at < horizon:
            continue
        pruned[key] = entry
    try:
        from atomic_write import atomic_write_json

        path = _ledger_path(workspace_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, pruned)
        out["recorded"] = True
        out["n_entries"] = len(pruned)
    except Exception:  # noqa: BLE001
        pass
    return out


# ---------------------------------------------------------------------------
# The capture-pass receipt (dispatcher due-ness + catchup resume point)
# ---------------------------------------------------------------------------

def log_capture_pass_receipt(workspace_root, *,
                             fired_via: str = "scheduled",
                             duration_ms: Optional[int] = None,
                             window: Optional[dict] = None,
                             n_meetings: int = 0,
                             n_processed: int = 0,
                             n_skipped: int = 0,
                             window_incomplete_before: Optional[str] = None,
                             extra_data: Optional[dict] = None) -> dict:
    """THE receipt for one incremental capture pass. A `pack_run` under task
    id `meeting-capture` — never under `past-meetings` (see the module
    docstring for why that would break `skip_render` and split the
    day-close series).

    Carries the same window vocabulary the close's own receipt carries —
    `window_start` / `window_end` / `window_incomplete_before`
    (`catchup.WINDOW_INCOMPLETE_FIELD`, the ONE spelling) — so
    `catchup_window("meeting-capture", ...)` resumes from exactly where this
    pass reached, including a batch-capped pass that left meetings owed.
    Counts only, never a title: the standard receipt discipline.
    """
    from catchup import WINDOW_INCOMPLETE_FIELD
    from receipts import log_receipt, normalize_fired_via

    data: dict = {
        "surface": "incremental-capture",
        "n_meetings": int(n_meetings or 0),
        "n_processed": int(n_processed or 0),
        "n_skipped": int(n_skipped or 0),
    }
    if isinstance(window, dict):
        if window.get("start") is not None:
            data["window_start"] = window.get("start")
        if window.get("end") is not None:
            data["window_end"] = window.get("end")
    if window_incomplete_before:
        data[WINDOW_INCOMPLETE_FIELD] = window_incomplete_before
    for k, v in (extra_data or {}).items():
        data.setdefault(k, v)
    return log_receipt(workspace_root, CAPTURE_JOB_ID,
                       receipt_type="pack_run",
                       fired_via=normalize_fired_via(fired_via) or "scheduled",
                       duration_ms=duration_ms,
                       extra_data=data)


def last_capture_pass(workspace_root, *, now=None) -> dict:
    """When the incremental capture pass last ran — the close's before/after
    instrument. `{"last_pass": iso|None, "ran_today": bool}`, where "today"
    is the machine-local calendar day (the clock the scheduler fires in).
    Read-only, never raises."""
    out = {"last_pass": None, "ran_today": False}
    try:
        from receipts import iter_receipts

        newest = None
        for r in iter_receipts(workspace_root, task_ids=[CAPTURE_JOB_ID]):
            dt = r.get("dt")
            if dt is None:
                continue
            if newest is None or dt > newest:
                newest = dt
    except Exception:  # noqa: BLE001
        return out
    if newest is None:
        return out
    local = newest.astimezone() if newest.tzinfo else newest
    out["last_pass"] = newest.isoformat()
    ref = _now_utc(now).astimezone()
    out["ran_today"] = local.date() == ref.date()
    return out


# ---------------------------------------------------------------------------
# The prior-capture briefs read (the close narrates what the passes wrote)
# ---------------------------------------------------------------------------

def prior_briefed_refs(workspace_root, *, now=None) -> list:
    """Briefs the background pass wrote since the last day-close, so the
    close can render them (`end_of_day.MEETING_BRIEFED_PRIOR`).

    Rows: `[{"source_ref", "brief_path", "title", "processed_at"}]`, oldest
    first. A row qualifies when ALL hold:

      * a `meeting_processed` event with `source_skill == "past-meetings"`
        landed AFTER the newest `past-meetings` pack_run receipt (the last
        close; `end-of-day` receipts bridge via TASK_PREDECESSORS). The
        skill filter is the seam that keeps the manual `process the call`
        path out: that path posts its own surface at the time, so re-linking
        it here would double-narrate. A background pass writes under
        `past-meetings` because it executes the close's own Phase D
        verbatim, and its silence is exactly what makes the close owe the
        link.
      * the matching `meeting` event carries a non-empty `data.brief_path`.

    No prior close on record (fresh install) bounds the read at 24 hours —
    the close's own nominal window. Everything is additionally capped at
    `EVIDENCE_WINDOW_DAYS`. Empty on any failure and empty on a machine-off
    day (no pass ran, nothing was written) — which is what keeps the
    no-incremental day byte-identical to today's close.
    """
    now_dt = _now_utc(now)
    floor = now_dt - _dt.timedelta(days=EVIDENCE_WINDOW_DAYS)
    last_close = None
    try:
        from receipts import iter_receipts

        for r in iter_receipts(workspace_root,
                               task_ids=["past-meetings", "end-of-day"]):
            if r.get("type") != "pack_run":
                continue
            dt = r.get("dt")
            if dt is None:
                continue
            dt = dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)
            if last_close is None or dt > last_close:
                last_close = dt
    except Exception:  # noqa: BLE001
        return []
    if last_close is not None:
        bound = max(last_close, floor)
    else:
        bound = max(now_dt - _dt.timedelta(hours=24), floor)

    rows: list = []
    briefs: dict = {}
    try:
        import events_io
        from connector_adapters.provenance import dedup_key_of
        from event_time import event_dt

        for ev in events_io.iter_events(workspace_root):
            if not isinstance(ev, dict):
                continue
            etype = ev.get("type")
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if etype == "meeting":
                ref = str(data.get("source_ref") or "").strip()
                path = str(data.get("brief_path") or "").strip()
                if ref and path:
                    # G30: stored refs join by DERIVED identity, never raw —
                    # a legacy lowercased row and a case-preserved one are
                    # one meeting.
                    briefs[dedup_key_of(ref) or ref] = {
                        "brief_path": path,
                        "title": str(data.get("title") or "")}
            elif etype == "meeting_processed":
                if ev.get("source_skill") != "past-meetings":
                    continue
                dt = event_dt(ev)
                if dt is None:
                    continue
                dt = dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)
                if dt <= bound:
                    continue
                ref = str(data.get("source_ref")
                          or data.get("meeting_id") or "").strip()
                if not ref:
                    continue
                row = {"source_ref": ref, "processed_at": dt.isoformat()}
                # The receipt may carry its own brief_path (the builder
                # stamps one when handed it); prefer it, fall back to the
                # meeting event's.
                path = str(data.get("brief_path") or "").strip()
                if path:
                    row["brief_path"] = path
                    row["title"] = str(data.get("title") or "")
                rows.append(row)
    except Exception:  # noqa: BLE001
        return []
    try:
        from connector_adapters.provenance import dedup_key_of
    except Exception:  # noqa: BLE001
        return []
    out = []
    seen = set()
    for row in sorted(rows, key=lambda r: r["processed_at"]):
        ref = row["source_ref"]
        # G30: dedup and the briefs join both run on the DERIVED identity —
        # never a raw stored-ref comparison across the PROV2 boundary.
        key = dedup_key_of(ref) or ref
        if key in seen:
            continue
        info = ({"brief_path": row["brief_path"],
                 "title": row.get("title") or ""}
                if row.get("brief_path") else briefs.get(key))
        if not info:
            continue  # no brief on record → nothing to link (never a guess)
        seen.add(key)
        out.append({"source_ref": ref,
                    "brief_path": info["brief_path"],
                    "title": info.get("title") or "",
                    "processed_at": row["processed_at"]})
    return out


__all__ = [
    "CAPTURE_JOB_ID", "EVIDENCE_WINDOW_DAYS", "WALK_LEDGER_RELPATH",
    "load_walk_ledger", "already_walked", "record_walk",
    "log_capture_pass_receipt", "last_capture_pass", "prior_briefed_refs",
]
