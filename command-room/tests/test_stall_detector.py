#!/usr/bin/env python3
"""Tests for stall_detector — v4.5.2 C3 derive-on-read edition.

Covers:
- Reads canonical entities.threads (nested under `entities` wrapper).
- Reads legacy `entities.projects` for backward compat.
- Handles both flat top-level and nested-under-`entities` shapes.
- All 6 statuses (active/exploring/paused/blocked/dormant/archived) — including
  archived = never flagged.
- C3 baseline rule: events (top-level primary_thread_id + related_thread_ids,
  REAL substrate shape per DATA_CONTRACT v2.2) STRICTLY beat the deprecated
  thread.last_activity field; the field is a zero-event fallback only.
- FINDINGS F-54 regression: a fossil record stamp can never make a project
  with fresh events read as stalled (and the reverse — genuinely quiet
  projects flag with the honest event-derived day count).
- apply_live_check — the F-57 dormant-scan-discipline gate.
- Defensive cases (missing files, malformed JSON, no threads).
- Config override via skill_config_writer.
- v3.14.1.x read-only contract (no events written during detection).

FIXTURE SHAPE NOTE (the realdata-fixture gotcha): events here carry
`primary_thread_id` at the event's TOP LEVEL — the canonical v2.2 shape that
live substrates actually have. The pre-C3 fixture put the id under `data`,
mirroring the detector's wrong assumption, which is exactly how F-54 shipped
with a green suite. Legacy data-level shapes keep their own explicit tests.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "shared" / "scripts"))

from stall_detector import detect_stalled_projects, apply_live_check  # noqa: E402
from skill_config_writer import save_skill_config  # noqa: E402


def _days_ago_iso(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


def _days_ago_date(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).date().isoformat()


def _event(event_type: str, thread_id: str, days_ago: int, seq: int = 1, **extra) -> dict:
    """REAL substrate shape: thread ids live at the event's top level."""
    return {
        "seq": seq,
        "ts": _days_ago_iso(days_ago),
        "type": event_type,
        "source_skill": "test",
        "primary_thread_id": thread_id,
        "data": {},
        **extra,
    }


class TestStallDetector(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        (self.workspace / "_hq" / "data").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_substrate_canonical(self, threads: list[dict], events: list[dict]) -> None:
        """Canonical shape — nested under `entities` wrapper, `threads` key."""
        payload = {
            "version": 1,
            "last_updated": _days_ago_iso(0),
            "last_writer": "test",
            "entities": {"people": [], "threads": threads, "orgs": []},
        }
        (self.workspace / "_hq" / "data" / "entities.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        (self.workspace / "_hq" / "data" / "events.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
        )

    def _write_substrate_flat_projects(self, projects: list[dict], events: list[dict]) -> None:
        """Legacy shape — flat top-level, `projects` key."""
        (self.workspace / "_hq" / "data" / "entities.json").write_text(
            json.dumps({"projects": projects, "people": [], "orgs": []}), encoding="utf-8"
        )
        (self.workspace / "_hq" / "data" / "events.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
        )

    # --- Canonical shape (nested entities.threads) ---

    def test_canonical_active_thread_over_threshold_flags(self):
        self._write_substrate_canonical(
            threads=[{"id": "project_001", "status": "active", "first_seen": _days_ago_date(60),
                      "last_activity": _days_ago_date(20)}],
            events=[],
        )
        flags = detect_stalled_projects(self.workspace)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["thread_id"], "project_001")
        self.assertEqual(flags[0]["thread_status"], "active")
        self.assertGreaterEqual(flags[0]["days_since_activity"], 20)
        self.assertEqual(flags[0]["baseline_source"], "last_activity")

    def test_canonical_active_thread_under_threshold_does_not_flag(self):
        self._write_substrate_canonical(
            threads=[{"id": "project_001", "status": "active", "first_seen": _days_ago_date(60),
                      "last_activity": _days_ago_date(10)}],
            events=[],
        )
        self.assertEqual(detect_stalled_projects(self.workspace), [])

    # --- Legacy shape (flat top-level projects) ---

    def test_legacy_flat_projects_shape_still_works(self):
        """Workspaces that haven't migrated to canonical shape still detected."""
        self._write_substrate_flat_projects(
            projects=[{"id": "project_001", "status": "active",
                       "first_seen": _days_ago_date(60),
                       "last_activity": _days_ago_date(20)}],
            events=[],
        )
        flags = detect_stalled_projects(self.workspace)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["thread_id"], "project_001")

    # --- All 6 status values ---

    def test_exploring_uses_30_day_threshold(self):
        proj = {"id": "project_002", "status": "exploring", "first_seen": _days_ago_date(90)}
        # 20 days — under threshold
        self._write_substrate_canonical(
            [{**proj, "last_activity": _days_ago_date(20)}], []
        )
        self.assertEqual(detect_stalled_projects(self.workspace), [])
        # 35 days — over
        self._write_substrate_canonical(
            [{**proj, "last_activity": _days_ago_date(35)}], []
        )
        flags = detect_stalled_projects(self.workspace)
        self.assertEqual(len(flags), 1)
        self.assertIn("exploring", flags[0]["recommended_action"])

    def test_paused_uses_45_day_threshold(self):
        proj = {"id": "project_003", "status": "paused", "first_seen": _days_ago_date(180)}
        # 30 days paused — under threshold
        self._write_substrate_canonical(
            [{**proj, "last_activity": _days_ago_date(30)}], []
        )
        self.assertEqual(detect_stalled_projects(self.workspace), [])
        # 60 days paused — over
        self._write_substrate_canonical(
            [{**proj, "last_activity": _days_ago_date(60)}], []
        )
        flags = detect_stalled_projects(self.workspace)
        self.assertEqual(len(flags), 1)
        self.assertIn("paused", flags[0]["recommended_action"])

    def test_blocked_uses_14_day_threshold(self):
        """Blocked threads flag fast — staying blocked is itself a signal."""
        proj = {"id": "project_004", "status": "blocked", "first_seen": _days_ago_date(60),
                "last_activity": _days_ago_date(20)}
        self._write_substrate_canonical([proj], [])
        flags = detect_stalled_projects(self.workspace)
        self.assertEqual(len(flags), 1)
        self.assertIn("blocked", flags[0]["recommended_action"])

    def test_dormant_uses_90_day_threshold(self):
        proj = {"id": "project_005", "status": "dormant", "first_seen": _days_ago_date(200)}
        # 60 days dormant — under threshold
        self._write_substrate_canonical(
            [{**proj, "last_activity": _days_ago_date(60)}], []
        )
        self.assertEqual(detect_stalled_projects(self.workspace), [])
        # 100 days dormant — over
        self._write_substrate_canonical(
            [{**proj, "last_activity": _days_ago_date(100)}], []
        )
        flags = detect_stalled_projects(self.workspace)
        self.assertEqual(len(flags), 1)
        self.assertIn("dormant", flags[0]["recommended_action"])

    def test_archived_is_never_flagged(self):
        """Archived threads are intentionally retired — never surface as stalls."""
        proj = {"id": "project_006", "status": "archived", "first_seen": _days_ago_date(365),
                "last_activity": _days_ago_date(365)}
        self._write_substrate_canonical([proj], [])
        self.assertEqual(detect_stalled_projects(self.workspace), [])

    # --- Baseline source priority (C3: events strictly beat the fossil) ---

    def test_event_scan_overrides_stale_last_activity_field(self):
        """If a meeting event happened more recently than thread.last_activity
        field claims, the event ts wins (the field can lag)."""
        self._write_substrate_canonical(
            threads=[{"id": "project_001", "status": "active",
                      "first_seen": _days_ago_date(60),
                      "last_activity": _days_ago_date(50)}],
            events=[_event("meeting", "project_001", days_ago=10)],
        )
        flags = detect_stalled_projects(self.workspace)
        # Event 10 days ago wins over field 50 days ago → under 14-day threshold
        self.assertEqual(flags, [])

    def test_f54_regression_fossil_field_never_hides_fresh_events(self):
        """FINDINGS F-54 exact scenario: project record last_activity frozen
        weeks ago (the fossil — no code maintains the field), meetings +
        commitments written through TODAY at the REAL top-level event shape.
        The project must be structurally incapable of appearing stalled."""
        self._write_substrate_canonical(
            threads=[{"id": "project_acme", "status": "active",
                      "first_seen": _days_ago_date(120),
                      "last_activity": _days_ago_date(42)}],  # "May 27" fossil
            events=[
                _event("meeting", "project_acme", days_ago=0, seq=101),
                _event("commitment", "project_acme", days_ago=0, seq=102),
                _event("commitment", "project_acme", days_ago=1, seq=100),
            ],
        )
        self.assertEqual(detect_stalled_projects(self.workspace), [],
                         "same-day substrate activity read as stalled — F-54 regressed")

    def test_f54_reverse_genuinely_quiet_project_flags_with_honest_count(self):
        """The reverse guarantee: a project whose events really did stop
        flags with the EVENT-derived day count — even when the fossil field
        would claim it's fresher (a stale record stamp can't suppress a real
        stall either)."""
        self._write_substrate_canonical(
            threads=[{"id": "project_001", "status": "active",
                      "first_seen": _days_ago_date(90),
                      "last_activity": _days_ago_date(5)}],  # fossil claims fresh
            events=[_event("meeting", "project_001", days_ago=20, seq=7)],
        )
        flags = detect_stalled_projects(self.workspace)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["baseline_source"], "event_scan")
        self.assertEqual(flags[0]["days_since_activity"], 20)
        self.assertEqual(flags[0]["last_event_seq"], 7)

    def test_related_thread_ids_count_as_activity(self):
        """An event whose related_thread_ids[] references the project is
        activity for it (DATA_CONTRACT v2.2 multi-thread events)."""
        ev = _event("decision", "project_other", days_ago=2, seq=50,
                    related_thread_ids=["project_001"])
        self._write_substrate_canonical(
            threads=[{"id": "project_001", "status": "active",
                      "first_seen": _days_ago_date(60),
                      "last_activity": _days_ago_date(40)}],
            events=[ev],
        )
        self.assertEqual(detect_stalled_projects(self.workspace), [])

    def test_low_confidence_events_do_not_count(self):
        """classification_confidence below the documented 0.40 floor doesn't
        reset staleness (matches computed_last_activity, VIEW_GENERATION.md)."""
        ev = _event("interaction", "project_001", days_ago=1, seq=9,
                    classification_confidence=0.2)
        self._write_substrate_canonical(
            threads=[{"id": "project_001", "status": "active",
                      "first_seen": _days_ago_date(60)}],
            events=[ev],
        )
        flags = detect_stalled_projects(self.workspace)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["baseline_source"], "first_seen")

    def test_last_activity_field_used_when_no_events(self):
        """No events tied to thread — the deprecated field is the legitimate
        zero-event fallback (fresh-ingest record stamps)."""
        self._write_substrate_canonical(
            threads=[{"id": "project_001", "status": "active",
                      "first_seen": _days_ago_date(60),
                      "last_activity": _days_ago_date(20)}],
            events=[],
        )
        flags = detect_stalled_projects(self.workspace)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["baseline_source"], "last_activity")

    def test_zero_history_uses_first_seen(self):
        """No events AND no last_activity field — use first_seen."""
        self._write_substrate_canonical(
            threads=[{"id": "project_001", "status": "active",
                      "first_seen": _days_ago_date(30)}],
            events=[],
        )
        flags = detect_stalled_projects(self.workspace)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["baseline_source"], "first_seen")
        self.assertIn("no activity since", flags[0]["recommended_action"])

    # --- Legacy event shapes (parsed forever — append-only history) ---

    def test_legacy_data_level_project_id_still_recognized(self):
        """Pre-v2.2 writers put the id under data.project_id."""
        ev = {
            "seq": 1, "ts": _days_ago_iso(5), "type": "meeting", "source_skill": "test",
            "data": {"project_id": "project_001"},
        }
        self._write_substrate_canonical(
            threads=[{"id": "project_001", "status": "active",
                      "first_seen": _days_ago_date(60),
                      "last_activity": _days_ago_date(30)}],
            events=[ev],
        )
        self.assertEqual(detect_stalled_projects(self.workspace), [])

    def test_legacy_data_level_primary_thread_id_still_recognized(self):
        """Some transitional writers put primary_thread_id under data."""
        ev = {
            "seq": 1, "ts": _days_ago_iso(5), "type": "meeting", "source_skill": "test",
            "data": {"primary_thread_id": "project_001"},
        }
        self._write_substrate_canonical(
            threads=[{"id": "project_001", "status": "active",
                      "first_seen": _days_ago_date(60),
                      "last_activity": _days_ago_date(30)}],
            events=[ev],
        )
        # Event 5 days ago wins → no flag (under 14-day threshold)
        self.assertEqual(detect_stalled_projects(self.workspace), [])

    # --- apply_live_check (F-57 dormant-scan discipline) ---

    def _one_stalled_flag(self) -> list[dict]:
        self._write_substrate_canonical(
            threads=[{"id": "project_001", "status": "active",
                      "first_seen": _days_ago_date(90)}],
            events=[_event("meeting", "project_001", days_ago=30, seq=3)],
        )
        flags = detect_stalled_projects(self.workspace)
        self.assertEqual(len(flags), 1)
        return flags

    def test_live_check_drops_flag_with_reason_when_live_signal_is_fresh(self):
        """Substrate-quiet + live-active = not stalled; dropped WITH the why."""
        flags = self._one_stalled_flag()
        kept, dropped = apply_live_check(flags, {
            "project_001": {"live_last_iso": _days_ago_date(2), "source": "gmail",
                            "detail": {"subject": "re: rollout"}},
        })
        self.assertEqual(kept, [])
        self.assertEqual(len(dropped), 1)
        self.assertIn("gmail", dropped[0]["drop_reason"])
        self.assertIn("2 days ago", dropped[0]["drop_reason"])

    def test_live_check_keeps_flag_with_honest_count_when_still_over_threshold(self):
        """Live touch newer than substrate but still past threshold → kept,
        day-count corrected to the live date."""
        flags = self._one_stalled_flag()
        kept, dropped = apply_live_check(flags, {
            "project_001": {"live_last_iso": _days_ago_date(20), "source": "calendar"},
        })
        self.assertEqual(dropped, [])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["days_since_activity"], 20)
        self.assertTrue(kept[0].get("live_checked"))

    def test_live_check_no_signal_keeps_flag_unchanged(self):
        flags = self._one_stalled_flag()
        kept, dropped = apply_live_check(flags, {"project_001": {}})
        self.assertEqual(dropped, [])
        self.assertEqual(kept, flags)

    def test_live_check_older_signal_changes_nothing(self):
        """A live signal OLDER than the substrate baseline is not evidence
        of quiet — the substrate already knew better."""
        flags = self._one_stalled_flag()
        kept, dropped = apply_live_check(flags, {
            "project_001": {"live_last_iso": _days_ago_date(45), "source": "gmail"},
        })
        self.assertEqual(dropped, [])
        self.assertEqual(kept, flags)

    # --- Multiple threads, mixed statuses ---

    def test_multiple_threads_mixed_statuses(self):
        self._write_substrate_canonical(
            threads=[
                {"id": "project_001", "status": "active",
                 "first_seen": _days_ago_date(60), "last_activity": _days_ago_date(20)},  # stalled
                {"id": "project_002", "status": "active",
                 "first_seen": _days_ago_date(60), "last_activity": _days_ago_date(5)},   # fresh
                {"id": "project_003", "status": "archived",
                 "first_seen": _days_ago_date(200), "last_activity": _days_ago_date(200)},  # archived (never)
                {"id": "project_004", "status": "paused",
                 "first_seen": _days_ago_date(120), "last_activity": _days_ago_date(60)},  # paused stalled
            ],
            events=[],
        )
        flags = detect_stalled_projects(self.workspace)
        flagged_ids = sorted(f["thread_id"] for f in flags)
        self.assertEqual(flagged_ids, ["project_001", "project_004"])

    # --- Defensive cases ---

    def test_no_entities_json(self):
        self.assertEqual(detect_stalled_projects(self.workspace), [])

    def test_malformed_entities_json(self):
        (self.workspace / "_hq" / "data" / "entities.json").write_text("not valid {", encoding="utf-8")
        self.assertEqual(detect_stalled_projects(self.workspace), [])

    def test_no_threads(self):
        self._write_substrate_canonical(threads=[], events=[])
        self.assertEqual(detect_stalled_projects(self.workspace), [])

    def test_thread_with_no_id_is_skipped(self):
        self._write_substrate_canonical(
            threads=[{"status": "active", "first_seen": _days_ago_date(30)}],  # no id
            events=[],
        )
        self.assertEqual(detect_stalled_projects(self.workspace), [])

    def test_thread_with_no_first_seen_and_no_activity_is_skipped(self):
        """Truly-unknown thread is silently skipped, not crashed on."""
        self._write_substrate_canonical(
            threads=[{"id": "project_001", "status": "active"}],
            events=[],
        )
        self.assertEqual(detect_stalled_projects(self.workspace), [])

    # --- Stage field as status fallback ---

    def test_stage_field_used_when_status_missing(self):
        """Threads may use `stage` instead of `status` per ORG_AND_THREAD_MODEL line 92."""
        self._write_substrate_canonical(
            threads=[{"id": "project_001", "stage": "active",
                      "first_seen": _days_ago_date(60),
                      "last_activity": _days_ago_date(20)}],
            events=[],
        )
        flags = detect_stalled_projects(self.workspace)
        self.assertEqual(len(flags), 1)

    # --- Config override ---

    def test_custom_threshold_via_skill_config(self):
        save_skill_config(self.workspace, "stalled-projects", {
            "thresholds": {"active_days": 7},
            "activity_event_types": ["meeting", "commitment", "decision", "interaction"],
            "surface_locations": ["pulse_phase_9"],
        })
        self._write_substrate_canonical(
            threads=[{"id": "project_001", "status": "active",
                      "first_seen": _days_ago_date(60),
                      "last_activity": _days_ago_date(10)}],
            events=[],
        )
        # 10 days > custom 7-day threshold → flags
        flags = detect_stalled_projects(self.workspace)
        self.assertEqual(len(flags), 1)

    def test_partial_config_merges_with_defaults(self):
        """User saves only active_days override; other thresholds fall back to defaults."""
        save_skill_config(self.workspace, "stalled-projects", {
            "thresholds": {"active_days": 7},
            "activity_event_types": ["meeting"],
            "surface_locations": ["pulse_phase_9"],
        })
        self._write_substrate_canonical(
            threads=[{"id": "project_001", "status": "paused",
                      "first_seen": _days_ago_date(120),
                      "last_activity": _days_ago_date(50)}],
            events=[],
        )
        # paused_days defaulted to 45 → 50 > 45 → flags
        flags = detect_stalled_projects(self.workspace)
        self.assertEqual(len(flags), 1)

    # --- v3.14.1.x read-only contract ---

    def test_deal_threads_excluded(self):
        """PIPE1 fence (D7): kind='deal' threads never flag here — they
        report through the pipeline surface's per-stage rot thresholds.
        The deal thread below is 40 days quiet (well over the 14d active
        threshold) and would flag without the fence; the sibling initiative
        with the same quiet gap still flags — the fence is kind-scoped,
        not a blanket suppression. The deal row deliberately carries NO
        deal object (the pre-PIPE1 real-data shape) — the fence must not
        crash on it or require the object."""
        self._write_substrate_canonical(
            threads=[
                {"id": "project_001", "status": "active", "kind": "deal",
                 "first_seen": _days_ago_date(60)},
                {"id": "project_002", "status": "active", "kind": "initiative",
                 "first_seen": _days_ago_date(60)},
            ],
            events=[
                _event("meeting", "project_001", days_ago=40, seq=1),
                _event("meeting", "project_002", days_ago=40, seq=2),
            ],
        )
        flags = detect_stalled_projects(self.workspace)
        flagged = {f["thread_id"] for f in flags}
        self.assertNotIn("project_001", flagged,
                         "kind=deal thread must be EXCLUDED from stall flags (PIPE1 fence)")
        self.assertIn("project_002", flagged,
                      "non-deal thread with the same gap still flags")

    def test_no_events_written_during_detection(self):
        self._write_substrate_canonical(
            threads=[{"id": "project_001", "status": "active",
                      "first_seen": _days_ago_date(60),
                      "last_activity": _days_ago_date(20)}],
            events=[_event("meeting", "project_001", days_ago=20)],
        )
        ev_path = self.workspace / "_hq" / "data" / "events.jsonl"
        before = ev_path.read_text(encoding="utf-8")
        detect_stalled_projects(self.workspace)
        after = ev_path.read_text(encoding="utf-8")
        self.assertEqual(before, after, "stall_detector wrote events — v3.14.1.x must be read-only")


if __name__ == "__main__":
    unittest.main(verbosity=2)
