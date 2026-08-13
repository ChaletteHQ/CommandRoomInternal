#!/usr/bin/env python3
"""
events.jsonl corruption-recovery tool (v3.13.8+ — Sub-bug #14b Layer 1).

Companion to `cru_match.load_events_defensively()` (Layer 2 — reader-side
tolerance). This module is the WRITE-side recovery: a one-time idempotent
pass that quarantines malformed lines off events.jsonl into a sibling
quarantine file, appends a `corruption_recovery` event documenting the
salvage, and rewrites events.jsonl without the corrupted lines (atomic
rename).

USAGE
=====

From `command-room-update-bridge` migration phase:

    from recover_corruption import run_recovery_if_needed
    summary = run_recovery_if_needed(workspace_root, source_skill="update-bridge")
    if summary["ran"]:
        # v3.13.8.1 Bug #64 — friendly customer-facing message is in the
        # summary; surface it to the user before any technical detail.
        print(summary["customer_message"])

CLI:

    python recover_corruption.py <workspace_root>

    # CLI prints the customer_message first, then the JSON summary.

IDEMPOTENCY
===========

run_recovery_if_needed() short-circuits with `ran=False` if events.jsonl has
no malformed lines OR a prior `corruption_recovery` event with matching
`recovery_version` exists in the file. Running the helper twice on the same
workspace is safe.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from atomic_write import (  # noqa: E402
    atomic_append_jsonl,
    atomic_write_text,
    multi_write_context,
)
from cru_match import load_events_defensively  # noqa: E402
from next_seq import next_seq  # noqa: E402


RECOVERY_VERSION = "v3.13.8.1"


def _events_path(workspace_root: Path) -> Path:
    return workspace_root / "_hq" / "data" / "events.jsonl"


def _quarantine_dir(workspace_root: Path) -> Path:
    return workspace_root / "_hq" / ".system" / "quarantine"


def _already_ran(workspace_root: Path) -> bool:
    """Check whether a corruption_recovery event with the current
    RECOVERY_VERSION already exists in events.jsonl. Used for idempotency."""
    events, _skipped = load_events_defensively(_events_path(workspace_root))
    for ev in events:
        if ev.get("type") == "corruption_recovery":
            data = ev.get("data") or {}
            if data.get("recovery_version") == RECOVERY_VERSION:
                return True
    return False


def _gather_malformed_lines(path: Path) -> list[dict]:
    """Return list of {line, reason, value} for every line that doesn't load.

    Uses load_events_defensively + the skipped channel.
    """
    _events, skipped = load_events_defensively(path)
    return skipped


def _gather_lines_to_quarantine(
    path: Path, malformed_line_numbers: set[int]
) -> tuple[set[int], list[str], list[str]]:
    """Return (line_numbers_to_quarantine, quarantined_raw, surviving_raw).

    JSONL is line-independent — if a line parses as a JSON dict it's a valid
    event regardless of what surrounds it. Multi-line corruption manifests
    as multiple adjacent malformed lines (each one caught on its own merits
    by the parser pass), so a "window of neighbors" approach would actually
    DESTROY surviving real events that happen to sit next to a malformed
    line. We quarantine only the malformed lines themselves.

    (v3.13.8 ship-time discovery: an earlier draft used window=3 and the
    runtime-exercise pass caught that recovery destroyed seq 19, 20, 21
    when seq 22-23 were malformed. Lesson: don't extrapolate corruption
    blast radius beyond what the parser actually reports.)
    """
    to_quarantine = set(malformed_line_numbers)

    quarantined_raw: list[str] = []
    surviving: list[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for i, raw in enumerate(f, 1):
            if i in to_quarantine:
                quarantined_raw.append(raw if raw.endswith("\n") else raw + "\n")
            else:
                surviving.append(raw if raw.endswith("\n") else raw + "\n")

    return to_quarantine, quarantined_raw, surviving


def run_recovery_if_needed(
    workspace_root: str | Path,
    source_skill: str = "update-bridge",
    recurring: bool = False,
) -> dict:
    """Run corruption recovery on events.jsonl. Returns a summary dict:

        {
          "ran": bool,
          "skipped_reason": Optional[str],   # set when ran=False
          "quarantined_line_count": int,     # 0 if ran=False
          "quarantined_count": int,          # alias for quarantined_line_count
          "quarantine_file": Optional[str],
          "date_range_label": str,           # human-friendly span (best-effort)
          "recovery_version": str,
          "customer_message": str,           # friendly message (v3.13.8.1)
        }

    Idempotent — a second invocation on the same workspace short-circuits
    with `ran=False` + `skipped_reason="already_run"`.

    Holds a multi_write_context lock for the full recovery so we don't race
    other writers during the quarantine + rewrite + event-append sequence.

    `source_skill` parameter (v3.13.8.1 — Bug #66): the caller identity that
    flows through to the persisted corruption_recovery event. Defaults to
    "update-bridge" (the canonical caller per §5 migration manifest spec).
    CLI sets this to "recover-corruption" when invoked directly.

    `recurring` parameter (v3.14.8+ — recurring self-heal): when False (the
    default, used by the once-per-upgrade update-bridge path), the run
    short-circuits if a corruption_recovery event for the current
    RECOVERY_VERSION already exists — it's a one-time migration cleanup.
    When True (used by the weekly `cleanup` skill), the version gate is
    skipped: the trigger is purely "is there malformed data RIGHT NOW?". A
    clean file is still a fast no-op (no event written, no rewrite), so a
    recurring caller can run every session/week cheaply and only acts when
    there is actual new damage to quarantine. This is what makes the heal
    catch drift that accumulates BETWEEN upgrades, not just at upgrade time.
    """
    workspace_root = Path(workspace_root)
    events_path = _events_path(workspace_root)

    if not events_path.exists():
        return _no_op_summary("no_events_file")

    # Once-per-version gate applies only to the migration (non-recurring) path.
    # Recurring callers (weekly cleanup) gate on actual damage instead — see
    # the malformed check below.
    if not recurring and _already_ran(workspace_root):
        return _no_op_summary("already_run")

    malformed = _gather_malformed_lines(events_path)
    if not malformed:
        return _no_op_summary("no_corruption_found")

    with multi_write_context(workspace_root, holder="recover_corruption"):
        # Re-gather INSIDE the lock so the malformed line numbers match the exact
        # snapshot we quarantine from. A concurrent append between the pre-lock
        # scan above and this rewrite would shift line numbers, causing the
        # quarantine to remove a DIFFERENT (valid) event (deep-audit 2026-05-30,
        # finding #25 — same lock-window class as the backfill fix #8).
        malformed = _gather_malformed_lines(events_path)
        if not malformed:
            return _no_op_summary("no_corruption_found")
        malformed_line_numbers = {entry["line"] for entry in malformed}
        date_range_label = _date_range_label(malformed_line_numbers)
        to_quarantine, quarantined_raw, surviving = _gather_lines_to_quarantine(
            events_path, malformed_line_numbers
        )

        # Write quarantine sidecar
        quarantine_dir = _quarantine_dir(workspace_root)
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        ts_label = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%SZ")
        quarantine_file = quarantine_dir / f"events_quarantine_{ts_label}.jsonl"
        # Uniqueness guard: two heals within the same second (possible under the
        # recurring path) must not overwrite each other's quarantined records.
        _dupe = 1
        while quarantine_file.exists():
            quarantine_file = quarantine_dir / f"events_quarantine_{ts_label}_{_dupe}.jsonl"
            _dupe += 1
        atomic_write_text(quarantine_file, "".join(quarantined_raw))

        # Rewrite events.jsonl without the quarantined lines
        atomic_write_text(events_path, "".join(surviving))

        # Append the corruption_recovery event (atomic append). No
        # hand-stamped seq (BUG-8330 item 7) — appender allocates in-lock
        # against the file we just rewrote.
        recovery_event = {
            "ts": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "type": "corruption_recovery",
            # v3.13.8.1 Bug #66 — source_skill flows through from caller
            "source_skill": source_skill,
            "data": {
                "quarantined_lines": sorted(to_quarantine),
                "quarantined_count": len(to_quarantine),
                # v3.13.8.1 Bug #65 — carry date_range_label into the event
                # so audit tooling can reconstruct the friendly span later
                # without needing to re-derive it from malformed lines.
                "date_range_label": date_range_label,
                "quarantine_file": str(
                    quarantine_file.relative_to(workspace_root)
                ).replace(os.sep, "/"),
                "recovery_version": RECOVERY_VERSION,
            },
        }
        atomic_append_jsonl(events_path, [recovery_event])

    summary = {
        "ran": True,
        "skipped_reason": None,
        # v3.13.8.1 Bug #65 — alias both key names so consumers using either
        # naming work without breakage. quarantined_count is the canonical
        # field; quarantined_line_count retained as alias for older readers.
        "quarantined_count": len(to_quarantine),
        "quarantined_line_count": len(to_quarantine),
        "quarantine_file": str(quarantine_file),
        "date_range_label": date_range_label,
        "recovery_version": RECOVERY_VERSION,
    }
    summary["customer_message"] = format_customer_message(summary)
    return summary


def _no_op_summary(skipped_reason: str) -> dict:
    """Build a uniform no-op summary dict for the short-circuit return paths
    (no_events_file / already_run / no_corruption_found).
    """
    return {
        "ran": False,
        "skipped_reason": skipped_reason,
        "quarantined_count": 0,
        "quarantined_line_count": 0,
        "quarantine_file": None,
        "date_range_label": "",
        "recovery_version": RECOVERY_VERSION,
        "customer_message": "",
    }


def _date_range_label(line_numbers: set[int]) -> str:
    """Best-effort human-friendly span label. Since we don't reliably know
    the dates of malformed lines (they're malformed), return a generic label.
    """
    if not line_numbers:
        return ""
    return "earlier this period"


def format_customer_message(summary: dict) -> str:
    """Build the friendly customer-facing message for a recovery summary.

    v3.13.8.1 Bug #64 — the migration-manifest §5 customer_message template:

        "Your activity log was repaired — {count} incomplete entries from
        {date_range} were quarantined to a separate file for safekeeping.
        Your activity history is otherwise intact."

    Returns an empty string when the recovery was a no-op (caller should
    not display anything in that case).
    """
    if not summary.get("ran"):
        return ""
    count = summary.get("quarantined_count", 0) or summary.get(
        "quarantined_line_count", 0
    )
    date_range = summary.get("date_range_label") or "earlier this period"
    if count == 1:
        return (
            f"Your activity log was repaired — 1 incomplete entry from "
            f"{date_range} was quarantined to a separate file for "
            f"safekeeping. Your activity history is otherwise intact."
        )
    return (
        f"Your activity log was repaired — {count} incomplete entries from "
        f"{date_range} were quarantined to a separate file for safekeeping. "
        f"Your activity history is otherwise intact."
    )


__all__ = ["run_recovery_if_needed", "format_customer_message", "RECOVERY_VERSION"]


def _cli() -> int:
    # Optional --recurring flag (v3.14.8+): used by the weekly `cleanup` skill
    # to run the damage-triggered heal every week, skipping the once-per-version
    # gate. Without it, the CLI behaves as the original one-time migration path.
    args = [a for a in sys.argv[1:] if a != "--recurring"]
    recurring = "--recurring" in sys.argv[1:]
    if len(args) != 1:
        print(
            "usage: recover_corruption.py <workspace_root> [--recurring]",
            file=sys.stderr,
        )
        return 2
    # CLI is the direct-invocation path; source_skill identifies the caller
    # (the script itself, not update-bridge) so audit tooling can tell apart
    # bridge-driven recoveries from ad-hoc CLI recoveries. The cleanup skill's
    # recurring invocation tags itself "cleanup".
    summary = run_recovery_if_needed(
        args[0],
        source_skill="cleanup" if recurring else "recover-corruption",
        recurring=recurring,
    )
    # v3.13.8.1 Bug #64 — surface the friendly customer message FIRST so any
    # caller that echoes stdout gets the right surface, regardless of whether
    # they parse the JSON or just relay the bytes. Empty for no-op runs so we
    # don't print a misleading "repaired 0 entries" line.
    customer_message = summary.get("customer_message") or ""
    if customer_message:
        print(customer_message)
        print()  # blank line separator before JSON
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
