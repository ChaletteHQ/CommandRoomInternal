#!/usr/bin/env python3
"""Tests for thread_activity — THE thread-staleness derivation (v4.5.2 C3).

The one rule every day-count surface (stalled-projects, pulse Phase 4)
derives from. Fixture events use the REAL substrate shape — top-level
`primary_thread_id` / `related_thread_ids` per DATA_CONTRACT v2.2 — plus
explicit legacy-shape cases (data-level ids, deprecated top-level
project_id mirror), the confidence floor, ts-recency vs seq, and the
naive/aware timestamp mix (F-15 legacy writers).
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

from thread_activity import (  # noqa: E402
    derive_thread_activity,
    event_thread_ids,
    DEFAULT_ACTIVITY_TYPES,
)


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class TestThreadActivity(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        (self.workspace / "_hq" / "data").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_events(self, events: list[dict]) -> None:
        (self.workspace / "_hq" / "data" / "events.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
        )

    # --- shape coverage ---

    def test_top_level_primary_thread_id_is_recognized(self):
        """The canonical v2.2 shape — the one F-54's scan missed."""
        self._write_events([{
            "seq": 10, "ts": _iso(3), "type": "meeting", "source_skill": "meeting-notes",
            "primary_thread_id": "project_001", "data": {"title": "sync"},
        }])
        out = derive_thread_activity(self.workspace)
        self.assertIn("project_001", out)
        self.assertEqual(out["project_001"].seq, 10)
        self.assertEqual(out["project_001"].event_type, "meeting")

    def test_related_thread_ids_credit_every_referenced_thread(self):
        self._write_events([{
            "seq": 11, "ts": _iso(2), "type": "decision", "source_skill": "decision-log",
            "primary_thread_id": "project_001",
            "related_thread_ids": ["project_002", "project_003"],
            "data": {},
        }])
        out = derive_thread_activity(self.workspace)
        self.assertEqual(set(out), {"project_001", "project_002", "project_003"})

    def test_deprecated_top_level_project_id_mirror_recognized(self):
        self._write_events([{
            "seq": 12, "ts": _iso(4), "type": "commitment", "source_skill": "x",
            "project_id": "project_004", "data": {},
        }])
        self.assertIn("project_004", derive_thread_activity(self.workspace))

    def test_legacy_data_level_ids_recognized(self):
        self._write_events([
            {"seq": 13, "ts": _iso(5), "type": "meeting", "source_skill": "x",
             "data": {"project_id": "project_005"}},
            {"seq": 14, "ts": _iso(6), "type": "meeting", "source_skill": "x",
             "data": {"primary_thread_id": "project_006"}},
        ])
        out = derive_thread_activity(self.workspace)
        self.assertIn("project_005", out)
        self.assertIn("project_006", out)

    def test_event_thread_ids_dedups_and_orders_canonical_first(self):
        ev = {
            "primary_thread_id": "project_001",
            "related_thread_ids": ["project_002", "project_001"],
            "project_id": "project_001",
            "data": {"project_id": "project_003"},
        }
        self.assertEqual(event_thread_ids(ev), ["project_001", "project_002", "project_003"])

    # --- filters ---

    def test_non_activity_types_ignored_by_default(self):
        self._write_events([{
            "seq": 15, "ts": _iso(1), "type": "pack_run", "source_skill": "pulse",
            "primary_thread_id": "project_001", "data": {},
        }])
        self.assertEqual(derive_thread_activity(self.workspace), {})
        # ...but an explicit type set can include it
        out = derive_thread_activity(self.workspace, activity_types={"pack_run"})
        self.assertIn("project_001", out)

    def test_confidence_floor_skips_low_confidence_events(self):
        self._write_events([
            {"seq": 16, "ts": _iso(1), "type": "interaction", "source_skill": "x",
             "primary_thread_id": "project_001", "classification_confidence": 0.2,
             "data": {}},
            {"seq": 17, "ts": _iso(9), "type": "interaction", "source_skill": "x",
             "primary_thread_id": "project_001", "classification_confidence": 0.9,
             "data": {}},
        ])
        out = derive_thread_activity(self.workspace)
        self.assertEqual(out["project_001"].seq, 17)

    def test_missing_confidence_counts(self):
        """Events without classification_confidence are activity (infra
        writers omit the field)."""
        self._write_events([{
            "seq": 18, "ts": _iso(2), "type": "meeting", "source_skill": "x",
            "primary_thread_id": "project_001", "data": {},
        }])
        self.assertIn("project_001", derive_thread_activity(self.workspace))

    # --- recency ---

    def test_most_recent_ts_wins_not_highest_seq(self):
        """Recency is decided by ts — seq order can diverge from time order
        on real substrates (F-38 seq races, multi-machine writes)."""
        self._write_events([
            {"seq": 100, "ts": _iso(10), "type": "meeting", "source_skill": "x",
             "primary_thread_id": "project_001", "data": {}},
            {"seq": 50, "ts": _iso(1), "type": "commitment", "source_skill": "x",
             "primary_thread_id": "project_001", "data": {}},
        ])
        rec = derive_thread_activity(self.workspace)["project_001"]
        self.assertEqual(rec.seq, 50)
        self.assertEqual(rec.event_type, "commitment")

    def test_naive_and_aware_timestamps_compare_safely(self):
        """F-15: legacy writers stamped naive-local. The scan must not crash
        and must return an aware ts for downstream day-count arithmetic."""
        naive = (datetime.now(timezone.utc) - timedelta(days=8)).replace(tzinfo=None)
        self._write_events([
            {"seq": 19, "ts": naive.isoformat(), "type": "meeting", "source_skill": "x",
             "primary_thread_id": "project_001", "data": {}},
            {"seq": 20, "ts": _iso(2), "type": "meeting", "source_skill": "x",
             "primary_thread_id": "project_001", "data": {}},
        ])
        rec = derive_thread_activity(self.workspace)["project_001"]
        self.assertEqual(rec.seq, 20)
        self.assertIsNotNone(rec.ts.tzinfo)
        # arithmetic against aware now must not raise
        _ = (datetime.now(timezone.utc) - rec.ts).days

    # --- defensive ---

    def test_missing_events_file(self):
        self.assertEqual(derive_thread_activity(self.workspace), {})

    def test_malformed_lines_and_unparseable_ts_skipped(self):
        path = self.workspace / "_hq" / "data" / "events.jsonl"
        good = {"seq": 21, "ts": _iso(3), "type": "meeting", "source_skill": "x",
                "primary_thread_id": "project_001", "data": {}}
        path.write_text(
            "not json{{\n"
            + json.dumps({"seq": 22, "ts": "garbage", "type": "meeting",
                          "primary_thread_id": "project_002", "data": {}}) + "\n"
            + json.dumps(good) + "\n",
            encoding="utf-8",
        )
        out = derive_thread_activity(self.workspace)
        self.assertEqual(set(out), {"project_001"})

    def test_default_activity_types_match_stall_detector_defaults(self):
        """The cross-surface consistency anchor: both pulse Phase 4 and
        stalled-projects derive with this same default set."""
        from stall_detector import DEFAULT_CONFIG
        self.assertEqual(
            DEFAULT_ACTIVITY_TYPES,
            frozenset(DEFAULT_CONFIG["activity_event_types"]),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
