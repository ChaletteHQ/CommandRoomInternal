#!/usr/bin/env python3
"""Tests for skill_config_writer — the v3.14 foundation helper for per-skill
first-run questionnaire config storage.

Covers:
- load returns None when no config exists
- save writes config + emits skill_first_run_configured event
- save then save again emits skill_reconfigured (auto-detected)
- explicit is_reconfigure=False forces first-run event even if file exists
- wipe removes file + load returns None after
- malformed JSON returns None (defensive read)
- atomic write is actually used (no raw open() write to config or events files)
- schema_version preserved across save → load
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "shared" / "scripts"))

from skill_config_writer import (  # noqa: E402
    is_configured,
    load_skill_config,
    save_skill_config,
    wipe_skill_config,
)


class TestSkillConfigWriter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        (self.workspace / "_hq" / "data").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _events_path(self):
        return self.workspace / "_hq" / "data" / "events.jsonl"

    def _read_events(self):
        p = self._events_path()
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]

    # --- load ---

    def test_load_returns_none_when_no_config(self):
        self.assertIsNone(load_skill_config(self.workspace, "customer-health-scorer"))

    def test_is_configured_returns_false_when_no_config(self):
        self.assertFalse(is_configured(self.workspace, "customer-health-scorer"))

    def test_load_returns_none_for_malformed_json(self):
        config_dir = self.workspace / "_hq" / "data" / "skill_config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "broken-skill.json").write_text("not valid json {", encoding="utf-8")
        self.assertIsNone(load_skill_config(self.workspace, "broken-skill"))

    # --- save (first run) ---

    def test_save_writes_config_and_emits_first_run_event(self):
        config = {"weights": {"recency": 30, "completion": 40}, "tier_cutoffs": [40, 70]}
        save_skill_config(self.workspace, "customer-health-scorer", config)

        # Config file exists + round-trips
        loaded = load_skill_config(self.workspace, "customer-health-scorer")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["skill_name"], "customer-health-scorer")
        self.assertEqual(loaded["schema_version"], 1)
        self.assertIn("configured_at", loaded)
        self.assertEqual(loaded["config"], config)

        # Event emitted
        events = self._read_events()
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["type"], "skill_first_run_configured")
        self.assertEqual(ev["source_skill"], "customer-health-scorer")
        self.assertEqual(ev["data"]["skill_name"], "customer-health-scorer")
        self.assertEqual(ev["data"]["config_snapshot"], config)
        self.assertEqual(ev["seq"], 1)

    def test_save_emits_reconfigured_when_config_already_exists(self):
        first = {"weights": {"recency": 30}}
        second = {"weights": {"recency": 50}}

        save_skill_config(self.workspace, "skill-a", first)
        save_skill_config(self.workspace, "skill-a", second)

        events = self._read_events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["type"], "skill_first_run_configured")
        self.assertEqual(events[1]["type"], "skill_reconfigured")
        # Second event reflects new config
        self.assertEqual(events[1]["data"]["config_snapshot"], second)

        # Latest config wins
        loaded = load_skill_config(self.workspace, "skill-a")
        self.assertEqual(loaded["config"], second)

    def test_explicit_is_reconfigure_true_forces_reconfigured_event(self):
        save_skill_config(self.workspace, "skill-b", {"x": 1}, is_reconfigure=True)
        events = self._read_events()
        self.assertEqual(events[-1]["type"], "skill_reconfigured")

    def test_explicit_is_reconfigure_false_forces_first_run_even_if_file_exists(self):
        save_skill_config(self.workspace, "skill-c", {"x": 1})  # auto = first_run
        save_skill_config(self.workspace, "skill-c", {"x": 2}, is_reconfigure=False)
        events = self._read_events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["type"], "skill_first_run_configured")
        self.assertEqual(events[1]["type"], "skill_first_run_configured")  # forced

    def test_schema_version_preserved(self):
        save_skill_config(self.workspace, "skill-d", {"x": 1}, schema_version=2)
        loaded = load_skill_config(self.workspace, "skill-d")
        self.assertEqual(loaded["schema_version"], 2)

    # --- wipe ---

    def test_wipe_removes_config(self):
        save_skill_config(self.workspace, "skill-e", {"x": 1})
        self.assertTrue(is_configured(self.workspace, "skill-e"))

        result = wipe_skill_config(self.workspace, "skill-e")
        self.assertTrue(result)
        self.assertFalse(is_configured(self.workspace, "skill-e"))
        self.assertIsNone(load_skill_config(self.workspace, "skill-e"))

    def test_wipe_returns_false_when_no_config(self):
        result = wipe_skill_config(self.workspace, "nonexistent-skill")
        self.assertFalse(result)

    # --- seq ordering ---

    def test_seq_increments_across_saves(self):
        save_skill_config(self.workspace, "skill-f", {"x": 1})
        save_skill_config(self.workspace, "skill-g", {"y": 2})
        events = self._read_events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["seq"], 1)
        self.assertEqual(events[1]["seq"], 2)

    # --- atomic write enforcement (Gate 3) ---

    def test_save_uses_atomic_write_not_raw_open(self):
        """Critical: confirm config write goes through atomic_write helper,
        not raw open()/write(). Protects against Bug #81-class regressions."""
        events_path = self._events_path()
        config_dir = self.workspace / "_hq" / "data" / "skill_config"

        # Track raw writes to either events.jsonl OR config files
        original_open = open
        raw_write_calls = []

        def tracked_open(file, mode="r", *args, **kwargs):
            path_str = str(file)
            if mode in ("w", "a", "w+", "a+", "wb", "ab"):
                # Allow temp-file writes (which atomic_write uses internally)
                if ".tmp." in Path(path_str).name:
                    return original_open(file, mode, *args, **kwargs)
                # Otherwise track as raw
                if "events.jsonl" in path_str or "skill_config" in path_str:
                    raw_write_calls.append((path_str, mode))
            return original_open(file, mode, *args, **kwargs)

        import builtins
        original_builtin_open = builtins.open
        builtins.open = tracked_open
        try:
            save_skill_config(self.workspace, "skill-atomic", {"x": 1})
        finally:
            builtins.open = original_builtin_open

        self.assertEqual(raw_write_calls, [],
                         f"Raw open() write detected: {raw_write_calls}. "
                         f"All substrate writes MUST go through atomic helpers.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
