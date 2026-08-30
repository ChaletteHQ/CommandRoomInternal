#!/usr/bin/env python3
"""
MORNCAP1 — the morning narrates what the background pass captured.

Origin: EODSPEED1 built `prior_briefed_refs` + `end_of_day.MEETING_BRIEFED_
PRIOR` so the CLOSE can narrate what its own silent incremental passes wrote
during the day. That mechanism has no morning twin: the morning brief reads
overnight EMAIL, never overnight CAPTURES. Today's loop without this spec:
the close says "3 deferred" -> the 6:45 AM pass captures them -> SILENCE. The
material lands on the substrate correctly and no surface ever tells the
operator what was in it. This module is the recovery leg that makes
deferral (CAPFENCE1) safe to build on — it ships BEFORE that fence exists.

RULINGS (§0, binding, kept verbatim from the spec):

  1. The morning brief gains a "captured since your last close" line-set:
     meetings the background/catch-up passes briefed after the last
     day-close receipt -- title + one-line what-came-of-it (n commitments,
     n decisions), each linking the already-written brief file. NARRATION of
     existing artifacts; zero new capture, zero new writers.
  2. Deferred-then-recovered meetings are called out by name: a meeting the
     close receipted as deferred (`window_incomplete_before` era) that a
     later pass captured renders as "caught up overnight: X" -- the close's
     honesty clause gets its answering clause. Never render a deferral as if
     it were a fresh capture.
  3. Same discipline as every brief section: capped (3 lines + "and N more,
     filed"), render-once ledger per (meeting, brief-date), counts-not-
     transcripts, omitted entirely when empty -- no "nothing was captured"
     filler (COVERQUIET1's complaint pre-honored).
  4. Reader, not owner: sources are `prior_briefed_refs` (window widened to
     a since-receipt variant, `eod_incremental.briefed_since_last_close`) +
     the deferral marker on the last close receipt. If EODSYNTH1-era render
     maps change, this section joins on refs, never re-derives.

ZERO NEW WRITERS (ruling 1). This module writes exactly one thing: its own
render-once ledger, a `.system` diagnostic sidecar, same posture as the CRU
walk ledger (`eod_incremental.py`) and the TASKALARM1 / BRIDGESIL1 announce
ledgers (`task_alarm.py`, `schedule_refresh.py`) this module's shape copies
deliberately -- same render-once posture, same cap-with-tail-line rendering,
same best-effort atomic write. It never appends a canonical event, never
touches a `meeting` / `meeting_processed` / `commitment` / `decision` row,
and reads counts back from disk (`meeting_capture.count_meeting_writes`)
rather than re-deriving them.

THE DEFERRAL JOIN (ruling 2 / ruling 4). `deferral_window` reads
`catchup.WINDOW_INCOMPLETE_FIELD` off `eod_incremental.last_close_receipt` --
the EXACT SAME receipt `briefed_since_last_close` bounds its own read
against, never a second "which receipt is the last close" computation that
could disagree with the first. A meeting is "recovered" (rendered as "caught
up overnight") when that marker is set AND the meeting's own start time
(`meeting_ts`, backdated onto the `meeting` event's `ts` by contract -- see
`meeting_capture.build_meeting_event`) falls INSIDE the window the close
left unprocessed: `marker <= meeting_ts <= close_at`. Both bounds matter --
the marker names the OLDEST meeting the close did not process, so anything
at or after it was POSSIBLY left out by that close; the close's own fire
time is the other side, because a meeting that started AFTER the close ran
is simply new and was never something that close could have deferred. Drop
the upper bound and every capture on a once-deferred day misreads as
"caught up overnight" forever -- `_is_recovered` is that two-sided fence,
named separately so the mutation suite can remove the call site whole and
prove the pin goes red.
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

# The render bound (ruling 3). A cap is a render bound, never a silence --
# every pending row is still recorded as narrated (see `_record_narrated`),
# not just the ones that render, so an overflow never replays tomorrow just
# because it overflowed the cap once (same posture as
# `schedule_refresh.announce_lines` / `task_alarm.dark_surface_lines`).
CAP = 3

# Where the render-once ledger lives -- `.system` is the workspace's own
# diagnostic sidecar home (mirrors ALARM_LEDGER_RELPATH / ANNOUNCE_LEDGER_
# RELPATH). Keyed by the meeting's DERIVED identity (dedup_key_of), never by
# brief-date -- the acceptance's "not re-narrated tomorrow" fixture is exactly
# this: the key must survive the date rolling over, or a same-meeting
# re-narration on the next calendar day would be indistinguishable from a
# genuinely new one.
LEDGER_RELPATH = "_hq/.system/morning_capture_ledger.json"

# The event types a meeting's "what came of it" sentence counts. Read back
# from disk via `meeting_capture.count_meeting_writes` -- never re-derived.
COUNT_TYPES = ("commitment", "decision")


def _now_utc(now=None) -> _dt.datetime:
    parsed = _parse_ts(now)
    if parsed is not None:
        return parsed
    return _dt.datetime.now(_dt.timezone.utc)


def _parse_ts(value) -> Optional[_dt.datetime]:
    """ISO string / datetime -> aware UTC datetime, or None. Matches
    `eod_incremental._parse_utc`'s posture (unparseable input degrades to
    "no answer", never raises) without importing a private helper across a
    module boundary."""
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


# ---------------------------------------------------------------------------
# The render-once ledger (ruling 3)
# ---------------------------------------------------------------------------

def _ledger_path(workspace_root) -> Path:
    return Path(workspace_root) / LEDGER_RELPATH


def load_narration_ledger(workspace_root) -> dict:
    """The ledger, `{dedup_key: entry}` -- `{}` on any failure. A corrupt or
    missing ledger degrades to "nothing has narrated yet", which re-narrates:
    the safe direction (a repeated line beats a silently-suppressed one),
    same posture as `task_alarm.load_alarm_ledger` /
    `schedule_refresh.load_announce_ledger`."""
    try:
        raw = _ledger_path(workspace_root).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _already_narrated(ledger: dict, key: str) -> bool:
    """THE RENDER-ONCE FENCE -- named separately (mirrors `task_alarm.
    _already_alarmed` / `schedule_refresh._already_announced`) so the
    mutation suite can remove the call site whole and prove the "same
    capture not re-narrated tomorrow" pin goes red."""
    return key in ledger


def _record_narrated(workspace_root, keys: list, *, brief_date: str,
                     now: _dt.datetime) -> None:
    if not keys:
        return
    ledger = load_narration_ledger(workspace_root)
    for k in keys:
        ledger[k] = {"brief_date": brief_date, "narrated_at": now.isoformat()}
    from eod_incremental import EVIDENCE_WINDOW_DAYS

    horizon = now - _dt.timedelta(days=EVIDENCE_WINDOW_DAYS)
    pruned = {}
    for k, entry in ledger.items():
        if not isinstance(entry, dict):
            continue
        narrated_at = _parse_ts(entry.get("narrated_at"))
        if narrated_at is None or narrated_at < horizon:
            continue
        pruned[k] = entry
    try:
        from atomic_write import atomic_write_json

        path = _ledger_path(workspace_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, pruned)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# The deferral join (ruling 2 / ruling 4)
# ---------------------------------------------------------------------------

def deferral_window(workspace_root) -> dict:
    """The last close's deferral window, as aware UTC instants --
    `{"marker": dt|None, "close_at": dt|None}`. `marker` is
    `window_incomplete_before` (the oldest meeting the close did NOT
    process); `close_at` is the close receipt's own fire time -- the upper
    bound, because a meeting that started AFTER the close ran is simply a
    new meeting the close never had a chance to see, not one it deferred.
    Both None when the last close was complete, or there is no close on
    record.

    Reads `eod_incremental.last_close_receipt` -- THE SAME receipt
    `briefed_since_last_close` bounds its own window against -- so this join
    can never disagree with the source of the refs it is joining onto.
    `catchup.to_connector_iso` does the ONE promotion (machine-local naive ->
    the machine's own offset), reused rather than reinvented: the marker was
    written by `catchup.receipt_window_marker` in that same naive-local
    vocabulary, and this is the one documented boundary crossing back out of
    it (see catchup.py's module docstring, "THE CLOCK SEAM AT THE CONNECTOR
    BOUNDARY"). `close_at` is already aware (`receipts.iter_receipts`'
    contract) and needs no such promotion.
    """
    from eod_incremental import last_close_receipt
    from catchup import WINDOW_INCOMPLETE_FIELD, to_connector_iso

    out = {"marker": None, "close_at": None}
    try:
        receipt = last_close_receipt(workspace_root)
    except Exception:  # noqa: BLE001
        return out
    if not receipt:
        return out
    out["close_at"] = _parse_ts(receipt.get("dt"))
    raw = receipt.get("raw") if isinstance(receipt.get("raw"), dict) else {}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    marker = data.get(WINDOW_INCOMPLETE_FIELD)
    if not marker:
        return out
    try:
        aware = to_connector_iso(marker)
    except Exception:  # noqa: BLE001
        return out
    out["marker"] = _parse_ts(aware)
    return out


def _is_recovered(meeting_ts_raw, marker: Optional[_dt.datetime],
                  close_at: Optional[_dt.datetime]) -> bool:
    """THE DEFERRAL FENCE (§0 Ruling 2). True only when the last close
    recorded a deferral marker AND this meeting's own start time falls
    INSIDE the window that close left unprocessed --
    `marker <= meeting_ts <= close_at`. The upper bound matters as much as
    the lower one: without it, every meeting captured after a deferred close
    (which is most of them, on any day a close ever deferred anything) would
    misrender as "caught up overnight" forever, including meetings that
    started after the close ran and so were never something IT could have
    deferred. Named separately so the mutation suite can remove the call
    site whole and prove the "caught up overnight" pin goes red -- without
    this fence every recovered meeting silently renders as a fresh capture,
    which is the exact dishonesty Ruling 2 exists to close."""
    if marker is None or close_at is None:
        return False
    ts = _parse_ts(meeting_ts_raw)
    if ts is None:
        return False
    return marker <= ts <= close_at


# ---------------------------------------------------------------------------
# Rendering (ruling 1 / ruling 3)
# ---------------------------------------------------------------------------

LABEL_CAPTURED = "[{title}]({brief_path}) — {counts}"
LABEL_RECOVERED = "caught up overnight: [{title}]({brief_path}) — {counts}"


def _counts_phrase(n_commitments: int, n_decisions: int) -> str:
    c_word = "commitment" if n_commitments == 1 else "commitments"
    d_word = "decision" if n_decisions == 1 else "decisions"
    return f"{n_commitments} {c_word}, {n_decisions} {d_word}"


def _row_counts(workspace_root, source_ref: str) -> dict:
    """`{"commitment": n, "decision": n}` for one meeting -- read back from
    disk via the existing claim-audit reader (`meeting_capture.
    count_meeting_writes`), never re-derived. Best-effort: a read failure
    renders zero counts rather than dropping the row (the link is still the
    thing that matters)."""
    try:
        from meeting_capture import count_meeting_writes

        return count_meeting_writes(workspace_root, source_ref, types=set(COUNT_TYPES))
    except Exception:  # noqa: BLE001
        return {t: 0 for t in COUNT_TYPES}


def narrated_since_close(workspace_root, *, now=None, cap: int = CAP,
                         record: bool = True,
                         brief_date: Optional[str] = None) -> dict:
    """SPEC MORNCAP1 -- the one producer for the morning's "captured since
    your last close" section.

    Returns `{"lines": [...], "n_narrated": int}`:

      lines       capped at `cap`, each linking the already-written brief
                  file, oldest-processed first, with an "...and N more,
                  filed." tail when the pending set overflows the cap. `[]`
                  when nothing is pending -- the caller renders NOTHING
                  (ruling 3: no "nothing was captured" filler).
      n_narrated  the TOTAL pending count -- capped-render or not, every
                  pending row is recorded as narrated in the same fire, so
                  this is the honest count for the receipt's
                  `n_prior_captures_narrated` (zero-written, never omitted).

    `record=False` previews the section without touching the ledger (a
    dry-run / test seam); every real fire calls with the default.
    """
    from eod_incremental import briefed_since_last_close

    now_dt = _now_utc(now)
    rows = briefed_since_last_close(workspace_root, now=now_dt)
    if not rows:
        return {"lines": [], "n_narrated": 0}

    try:
        from connector_adapters.provenance import dedup_key_of
    except Exception:  # noqa: BLE001
        return {"lines": [], "n_narrated": 0}

    ledger = load_narration_ledger(workspace_root) if record else {}
    pending = []
    for row in rows:
        ref = row.get("source_ref")
        key = dedup_key_of(ref) or ref
        if not key:
            continue
        if record and _already_narrated(ledger, key):
            continue
        pending.append((key, row))
    if not pending:
        return {"lines": [], "n_narrated": 0}

    window = deferral_window(workspace_root)

    lines = []
    for _, row in pending[:cap]:
        counts = _row_counts(workspace_root, row.get("source_ref"))
        phrase = _counts_phrase(counts.get("commitment", 0),
                                counts.get("decision", 0))
        title = row.get("title") or "Untitled meeting"
        brief_path = row.get("brief_path") or ""
        template = (LABEL_RECOVERED
                   if _is_recovered(row.get("meeting_ts"),
                                    window["marker"], window["close_at"])
                   else LABEL_CAPTURED)
        lines.append(template.format(title=title, brief_path=brief_path,
                                     counts=phrase))

    rest = len(pending) - cap
    if rest > 0:
        lines.append(f"...and {rest} more, filed.")

    if record:
        _record_narrated(
            workspace_root, [k for k, _ in pending],
            brief_date=brief_date or now_dt.date().isoformat(), now=now_dt)

    return {"lines": lines, "n_narrated": len(pending)}


__all__ = [
    "CAP", "LEDGER_RELPATH", "COUNT_TYPES",
    "load_narration_ledger", "deferral_window", "narrated_since_close",
]
