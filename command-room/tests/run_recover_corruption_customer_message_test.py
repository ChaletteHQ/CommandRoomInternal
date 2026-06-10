#!/usr/bin/env python3
"""
Test for v3.13.8.1 recover_corruption customer_message + field harmonization.

Covers three v3.13.8 bugs surfaced during verification (Test 0.1 confirm prompt):

  Bug #64 — customer-friendly message template was never surfaced. The script
            emitted raw JSON only; the §5 migration-manifest customer_message
            template (`"Your activity log was repaired — N incomplete entries
            from {date_range}..."`) was never displayed.

  Bug #65 — field drift: summary used `quarantined_line_count`, event used
            `quarantined_count`, `date_range_label` dropped entirely from
            persisted event.

  Bug #66 — source_skill spec drift: event used "recover-corruption" (script
            name) but §5 manifest spec expected "update-bridge" when called
            from the bridge.

Verifies:
  1. format_customer_message() returns the exact §5 template with count +
     date_range substituted (multiple-entry plural form).
  2. format_customer_message() returns the singular form when count=1.
  3. format_customer_message() returns empty string for a no-op summary.
  4. run_recovery_if_needed() returns customer_message in the summary dict.
  5. The persisted corruption_recovery event includes date_range_label
     (was dropped in v3.13.8).
  6. The persisted event uses both quarantined_count + date_range_label
     (Bug #65 harmonization).
  7. source_skill defaults to "update-bridge" (Bug #66 spec conformance).
  8. source_skill="recover-corruption" honored when caller is the CLI.
  9. Idempotency holds — second invocation returns no_op (regression).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from recover_corruption import (  # noqa: E402
    RECOVERY_VERSION,
    format_customer_message,
    run_recovery_if_needed,
)


def _setup_workspace_with_malformed_lines(num_malformed: int = 3) -> Path:
    """Build a synthetic workspace with `num_malformed` malformed events.jsonl
    lines surrounded by valid events."""
    tmp = Path(tempfile.mkdtemp(prefix="cr_recover_test_"))
    data_dir = tmp / "_hq" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    events_path = data_dir / "events.jsonl"

    lines: list[str] = []
    # 5 valid events
    for i in range(1, 6):
        ev = {
            "seq": i,
            "ts": f"2026-05-{15 + i:02d}T10:00:00Z",
            "type": "noop",
            "data": {"i": i},
        }
        lines.append(json.dumps(ev))
    # N malformed lines (bare-dict-key style — exactly the Bug #68 corruption
    # class, which Sub-bug #14b recovery should quarantine)
    for _ in range(num_malformed):
        lines.append('"ts"')
        lines.append('"type"')
    # 2 more valid events after the corruption
    for i in range(6, 8):
        ev = {
            "seq": i,
            "ts": f"2026-05-{15 + i:02d}T10:00:00Z",
            "type": "noop",
            "data": {"i": i},
        }
        lines.append(json.dumps(ev))

    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp


def _read_events(workspace_root: Path) -> list[dict]:
    path = workspace_root / "_hq" / "data" / "events.jsonl"
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            # Skip malformed lines (test fixture)
            continue
    return out


# ---------- Test 1: format_customer_message template — plural ----------

def test_format_customer_message_plural():
    summary = {
        "ran": True,
        "quarantined_count": 8,
        "date_range_label": "earlier this period",
    }
    msg = format_customer_message(summary)
    # The §5 migration-manifest template (verbatim phrasing)
    assert "Your activity log was repaired" in msg
    assert "8 incomplete entries" in msg
    assert "earlier this period" in msg
    assert "quarantined to a separate file" in msg
    assert "for safekeeping" in msg
    assert "Your activity history is otherwise intact" in msg
    # Confirm NOT leaking technical jargon
    assert "events.jsonl" not in msg
    assert "JSONDecodeError" not in msg
    assert "quarantine" in msg  # noun in the friendly phrasing — OK
    print("PASS test_format_customer_message_plural")


# ---------- Test 2: format_customer_message — singular ----------

def test_format_customer_message_singular():
    summary = {
        "ran": True,
        "quarantined_count": 1,
        "date_range_label": "earlier this month",
    }
    msg = format_customer_message(summary)
    assert "1 incomplete entry" in msg, f"expected singular, got: {msg}"
    assert "was quarantined" in msg, f"expected singular verb, got: {msg}"
    print("PASS test_format_customer_message_singular")


# ---------- Test 3: format_customer_message — no-op returns empty ----------

def test_format_customer_message_no_op_empty():
    no_op_summary = {
        "ran": False,
        "skipped_reason": "already_run",
        "quarantined_count": 0,
    }
    msg = format_customer_message(no_op_summary)
    assert msg == "", f"expected empty for no-op, got: {msg!r}"
    print("PASS test_format_customer_message_no_op_empty")


# ---------- Test 4: summary dict includes customer_message ----------

def test_summary_includes_customer_message():
    workspace = _setup_workspace_with_malformed_lines(num_malformed=2)
    summary = run_recovery_if_needed(workspace)

    assert summary["ran"] is True
    assert "customer_message" in summary
    assert summary["customer_message"].startswith("Your activity log was repaired")
    print("PASS test_summary_includes_customer_message")


# ---------- Test 5: persisted event includes date_range_label (Bug #65) ----------

def test_event_includes_date_range_label():
    workspace = _setup_workspace_with_malformed_lines(num_malformed=2)
    run_recovery_if_needed(workspace)

    written = _read_events(workspace)
    recovery_event = next(e for e in written if e.get("type") == "corruption_recovery")
    assert "date_range_label" in recovery_event["data"], (
        "Bug #65: date_range_label must be carried into event payload"
    )
    # Should match the friendly span label, not empty
    assert recovery_event["data"]["date_range_label"] != ""
    print("PASS test_event_includes_date_range_label")


# ---------- Test 6: event field harmonization (Bug #65) ----------

def test_event_field_harmonization():
    workspace = _setup_workspace_with_malformed_lines(num_malformed=2)
    run_recovery_if_needed(workspace)

    written = _read_events(workspace)
    recovery_event = next(e for e in written if e.get("type") == "corruption_recovery")
    # Event uses quarantined_count (canonical)
    assert "quarantined_count" in recovery_event["data"]
    # Event includes date_range_label
    assert "date_range_label" in recovery_event["data"]
    # Event includes recovery_version + quarantine_file (regression check)
    assert recovery_event["data"]["recovery_version"] == RECOVERY_VERSION
    assert "quarantine_file" in recovery_event["data"]
    assert "quarantined_lines" in recovery_event["data"]
    print("PASS test_event_field_harmonization")


# ---------- Test 7: source_skill defaults to update-bridge (Bug #66) ----------

def test_source_skill_defaults_to_update_bridge():
    workspace = _setup_workspace_with_malformed_lines(num_malformed=2)
    # Use default source_skill
    run_recovery_if_needed(workspace)

    written = _read_events(workspace)
    recovery_event = next(e for e in written if e.get("type") == "corruption_recovery")
    assert recovery_event["source_skill"] == "update-bridge", (
        f"Bug #66: default source_skill must be 'update-bridge' (§5 manifest), "
        f"got {recovery_event['source_skill']!r}"
    )
    print("PASS test_source_skill_defaults_to_update_bridge")


# ---------- Test 8: source_skill caller-override honored (Bug #66) ----------

def test_source_skill_caller_override():
    workspace = _setup_workspace_with_malformed_lines(num_malformed=2)
    # CLI invocation passes source_skill explicitly
    run_recovery_if_needed(workspace, source_skill="recover-corruption")

    written = _read_events(workspace)
    recovery_event = next(e for e in written if e.get("type") == "corruption_recovery")
    assert recovery_event["source_skill"] == "recover-corruption", (
        f"caller-override on source_skill must be honored; got "
        f"{recovery_event['source_skill']!r}"
    )
    print("PASS test_source_skill_caller_override")


# ---------- Test 9: idempotency holds (regression) ----------

def test_idempotency_regression():
    workspace = _setup_workspace_with_malformed_lines(num_malformed=2)
    first = run_recovery_if_needed(workspace)
    second = run_recovery_if_needed(workspace)
    assert first["ran"] is True
    assert second["ran"] is False
    assert second["skipped_reason"] == "already_run"
    # No-op summary also includes customer_message field (empty)
    assert "customer_message" in second
    assert second["customer_message"] == ""
    print("PASS test_idempotency_regression")


def main():
    test_format_customer_message_plural()
    test_format_customer_message_singular()
    test_format_customer_message_no_op_empty()
    test_summary_includes_customer_message()
    test_event_includes_date_range_label()
    test_event_field_harmonization()
    test_source_skill_defaults_to_update_bridge()
    test_source_skill_caller_override()
    test_idempotency_regression()
    print()
    print("OK — all 9 recover_corruption customer_message tests passed.")


if __name__ == "__main__":
    main()
