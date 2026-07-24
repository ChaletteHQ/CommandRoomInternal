#!/usr/bin/env python3
"""
SPEC SYNC1 A2 — the ONE author of substrate alert .md files, with a lifecycle.

WHY THIS MODULE EXISTS (row 17's second lesson)
------------------------------------------------
The 2026-07-19 maintenance fire hit a stale sandbox mount (a view of
events.jsonl at max seq 3591 while the real file was at 4643 — a copy 1,015+
seqs behind a pre-recovery lineage). FS-04 caught it and failed closed. But the
fire ALSO hand-authored a prose alert, `SUBSTRATE_REGRESSION_ALERT_2026-07-19.md`,
that asserted "seqs 3592-4606 are missing" and prescribed Drive-version-history
recovery — WRONG by read-time (nothing was missing; the mount was stale). Two
things had no lifecycle:

  * the `.seqregression.json` marker: `check_substrate_regression` returned it
    without re-verifying (fixed in atomic_write.check_substrate_regression —
    it now truth-checks + self-archives);
  * the alert .md: nobody owned it, so a false, live-looking recovery
    instruction sat in `_hq/` indefinitely.

This module closes the .md half. `write_alert` renders the alert FROM the
marker (never free prose), stating BOTH hypotheses and never asserting data
loss as fact. `sweep_alerts` re-checks every live alert's predicate on each
brief / health / preflight fire and, when the condition has resolved, annotates
+ archives it (D-3) so M never executes stale recovery steps from a live alert.

Alarms must not outlive their truth.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

_ALERT_GLOB = "SUBSTRATE_REGRESSION_ALERT_*.md"
_ALERT_PREFIX = "SUBSTRATE_REGRESSION_ALERT_"
_RESOLVED_DIRNAME = "_resolved_alerts"

# The machine-readable header sweep_alerts parses back out. An HTML comment so it
# renders invisibly if the .md is ever opened in a viewer, but is trivially
# grep/parse-able. NEVER free prose — the row-17 alert was free prose and lied.
_HEADER_OPEN = "<!-- CR-SUBSTRATE-ALARM"
_HEADER_CLOSE = "-->"
_HEADER_RE = re.compile(
    re.escape(_HEADER_OPEN) + r"\s*(\{.*?\})\s*" + re.escape(_HEADER_CLOSE),
    re.DOTALL,
)


def _hq_dir(workspace_root) -> Path:
    return Path(workspace_root) / "_hq"


def _events_path(workspace_root) -> Path:
    """The active events.jsonl, honoring the (dormant) data_root resolver so an
    alert self-checks against the same file the writer appends to."""
    try:
        from data_root import resolve
        return resolve(workspace_root) / "events.jsonl"
    except Exception:
        return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _date_from_marker(marker: dict) -> str:
    """The YYYY-MM-DD the alert filename carries. Derived from the marker's own
    `detected` timestamp (data-driven, never a literal — G14 clean) so an alert
    is stably named for the incident it describes; falls back to the current
    UTC date if the marker has no readable detected ts."""
    detected = marker.get("detected") if isinstance(marker, dict) else None
    if isinstance(detected, str) and len(detected) >= 10:
        head = detected[:10]
        if re.match(r"^\d{4}-\d{2}-\d{2}$", head):
            return head
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def alert_path(workspace_root, marker: dict) -> Path:
    return _hq_dir(workspace_root) / f"{_ALERT_PREFIX}{_date_from_marker(marker)}.md"


def _seqs_from_marker(marker: dict) -> tuple[Optional[int], Optional[int]]:
    """(file_max_seq, sidecar_max_seq) as ints, or (None, None) parts when
    absent/non-numeric."""
    def _int(v):
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    return _int(marker.get("file_max_seq")), _int(marker.get("sidecar_max_seq"))


def write_alert(workspace_root, marker: dict) -> Path:
    """Render the substrate-regression alert .md FROM the marker (SPEC SYNC1 A2).

    The template requirements, learned from the row-17 false alert:
      (i)   a machine-readable header block sweep_alerts re-parses;
      (ii)  BOTH hypotheses stated — stale mount (likely transient; the real
            file may be fine on the primary machine) vs real clobber (laptop
            Drive sync) — with an explicit "verify which is true before any
            recovery step";
      (iii) NEVER asserts data loss as fact — "N entries are not visible from
            this view", never "missing" / "lost";
      (iv)  the FS-04 doctrine line — nothing was force-written; the quarantine
            files hold the blocked batches intact;
      (v)   a footer stating the file self-clears when the condition resolves.

    Returns the alert Path. Overwrites an existing same-date alert in place
    (re-stamps last_verified) — one incident, one file."""
    if not isinstance(marker, dict):
        marker = {}
    path = alert_path(workspace_root, marker)
    file_max, sidecar_max = _seqs_from_marker(marker)
    created = str(marker.get("detected") or _iso_now())
    now = _iso_now()
    n_quar = marker.get("n_quarantined")
    quar_path = marker.get("quarantine_path")

    events_path = _events_path(workspace_root)
    try:
        marker_rel = str(Path(marker.get("quarantine_path") or "").name) if quar_path else ""
    except Exception:
        marker_rel = ""

    header = {
        "marker_path": str(events_path.name + ".seqregression.json"),
        "events_rel": str(events_path),
        "predicate": (f"file_max_seq >= {sidecar_max}" if sidecar_max is not None
                      else "seqhw high-water reached"),
        "sidecar_max_seq": sidecar_max,
        "file_max_seq_at_creation": file_max,
        "created": created,
        "last_verified": now,
    }

    gap = (sidecar_max - file_max) if (sidecar_max is not None and file_max is not None) else None
    gap_line = (
        f"**{gap} entr{'y is' if gap == 1 else 'ies are'} not visible from this view.**"
        if gap is not None and gap > 0 else
        "**Some recent entries are not visible from this view.**"
    )

    body = f"""{_HEADER_OPEN}
{json.dumps(header)}
{_HEADER_CLOSE}

# Substrate view is behind its high-water mark

_Auto-generated from the FS-04 regression marker — not hand-written. Do not
edit; this file is owned by `alarm_artifacts` and self-clears when the
condition resolves._

- **Detected:** {created}
- **Last re-verified:** {now}
- **On-disk max entry number (this view):** {file_max if file_max is not None else "unknown"}
- **Recorded high-water mark:** {sidecar_max if sidecar_max is not None else "unknown"}

{gap_line} That does not mean anything was dropped — it means the copy of the
activity log this process can see is behind the high-water mark the workspace
has already reached.

## Before doing anything: figure out which of these is true

There are two possible causes, and the recovery step is different for each.
**Verify which one is true before taking any recovery action.**

1. **Stale view (most likely, usually transient).** A Cowork sandbox mount or a
   mid-sync Drive cache is serving an older copy while the real file on the
   primary machine is already whole. If so, nothing is wrong with your data —
   the view just needs to catch up. Fully quit and reopen Cowork (quit the app
   completely — closing the window is not enough) and re-check; the alert will
   clear itself on the next healthy check.

2. **Real clobber (multi-machine Drive last-writer-wins).** A trailing machine
   (usually a laptop that came online) flushed an out-of-date `events.jsonl`
   over the live one. If — and only if — the primary machine also shows the low
   number after a full Cowork restart, recover the log from Drive version
   history and merge, then let the reconciler replay the quarantined batches.

If you are not sure which case you are in, ask me to walk you through checking —
do not run a recovery step on a guess.

## Nothing was force-written

The write guard refused every in-place append while this condition held and set
the blocked batches aside intact{f' ({n_quar} batch file, `{marker_rel}`)' if (n_quar and marker_rel) else ''}.
No data was overwritten and no batch was dropped; the quarantined batches are
replayed automatically once a healthy view is confirmed.

---

_This file self-clears when the condition resolves. If you are reading it, the
condition was still true as of the "Last re-verified" time above._
"""
    from atomic_write import atomic_write_text
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, body)
    return path


def _parse_header(text: str) -> Optional[dict]:
    m = _HEADER_RE.search(text)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _predicate_resolved(workspace_root, header: dict) -> bool:
    """Re-evaluate the alert's predicate against the live file. Resolved iff the
    on-disk max seq now meets or exceeds the recorded high-water. A header we
    cannot read a sidecar_max from is treated as UNRESOLVED (leave it up — an
    alert we can't re-verify must not silently clear)."""
    sidecar_max = header.get("sidecar_max_seq")
    if not (isinstance(sidecar_max, (int, float)) and not isinstance(sidecar_max, bool)):
        return False
    events_rel = header.get("events_rel")
    events_path = Path(events_rel) if isinstance(events_rel, str) and events_rel else _events_path(workspace_root)
    if not events_path.exists():
        # The active file resolved from the header no longer exists here (e.g. a
        # relocated data_root on another machine) — fall back to the live one.
        events_path = _events_path(workspace_root)
    try:
        from atomic_write import _file_max_seq
        return _file_max_seq(events_path) >= int(sidecar_max)
    except Exception:
        return False


def _bump_last_verified(path: Path, text: str) -> None:
    header = _parse_header(text)
    if header is None:
        return
    header["last_verified"] = _iso_now()
    new_block = f"{_HEADER_OPEN}\n{json.dumps(header)}\n{_HEADER_CLOSE}"
    new_text = _HEADER_RE.sub(lambda _m: new_block, text, count=1)
    try:
        from atomic_write import atomic_write_text
        atomic_write_text(path, new_text)
    except Exception:
        pass


def _append_alarm_cleared(workspace_root, alert_name: str, header: dict) -> None:
    """Best-effort `substrate_alarm_cleared` audit event. Gate-valid (registered
    type). NEVER raises — a health read that could crash on a telemetry append
    would be a worse bug than the stale alarm it clears."""
    try:
        from event_gate import append_event
        ev = {
            "type": "substrate_alarm_cleared",
            "source_skill": "alarm_artifacts",
            "data": {
                "alert": alert_name,
                "marker": header.get("marker_path"),
                "sidecar_max_seq": header.get("sidecar_max_seq"),
                "resolved_at": _iso_now(),
            },
        }
        append_event(_events_path(workspace_root), ev, holder="alarm_artifacts.sweep")
    except Exception:
        pass


def _archive_marker_if_present(workspace_root) -> None:
    """If a live regression marker is still on disk, route it through the
    truth-check (which self-archives a resolved marker). Best-effort."""
    try:
        from atomic_write import check_substrate_regression
        check_substrate_regression(_events_path(workspace_root))
    except Exception:
        pass


def sweep_alerts(workspace_root) -> list[dict]:
    """Re-evaluate every live substrate-regression alert; resolve the ones whose
    condition no longer holds (SPEC SYNC1 A2 + D-3).

    Resolved → prepend a `✅ RESOLVED <ts> — condition re-checked and false; no
    action needed` banner, move the .md to `_hq/_resolved_alerts/` (annotate +
    archive, NEVER delete — the audit trail survives), archive the marker, and
    best-effort append `substrate_alarm_cleared`.
    Still true → bump `last_verified` in place.

    Returns a list of {alert, action} for each alert touched. Called from
    substrate_alarm_lines (every brief / health fire), from the A4 preflight,
    and from reconcile_forward after a healthy replay — so no alert survives a
    single healthy fire. Best-effort throughout; never raises."""
    out: list[dict] = []
    hq = _hq_dir(workspace_root)
    try:
        alerts = sorted(hq.glob(_ALERT_GLOB)) if hq.is_dir() else []
    except OSError:
        return out
    resolved_dir = hq / _RESOLVED_DIRNAME
    for ap in alerts:
        try:
            text = ap.read_text(encoding="utf-8")
        except OSError:
            continue
        header = _parse_header(text)
        if header is None:
            # An alert with no machine-readable header can't be re-verified;
            # leave it (don't guess). Recorded so the caller can see it.
            out.append({"alert": ap.name, "action": "unparseable-left"})
            continue
        if not _predicate_resolved(workspace_root, header):
            _bump_last_verified(ap, text)
            out.append({"alert": ap.name, "action": "still-true-bumped"})
            continue
        # Resolved — annotate + archive (D-3).
        banner = (
            f"> ✅ RESOLVED {_iso_now()} — condition re-checked and false; no "
            f"action needed. The activity log this process can see has caught up "
            f"to (or passed) the recorded high-water mark; nothing was dropped "
            f"and no recovery step is required. Archived for the audit trail.\n\n"
        )
        try:
            resolved_dir.mkdir(parents=True, exist_ok=True)
            from atomic_write import atomic_write_text
            dest = resolved_dir / ap.name
            atomic_write_text(dest, banner + text)
            try:
                ap.unlink()
            except OSError:
                try:
                    import time as _time
                    ap.rename(ap.with_suffix(ap.suffix + f".resolved.{int(_time.time())}"))
                except OSError:
                    pass
            _archive_marker_if_present(workspace_root)
            _append_alarm_cleared(workspace_root, ap.name, header)
            out.append({"alert": ap.name, "action": "resolved-archived"})
        except Exception:
            out.append({"alert": ap.name, "action": "archive-failed"})
    return out


__all__ = ["write_alert", "sweep_alerts", "alert_path"]
