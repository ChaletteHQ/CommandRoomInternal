#!/usr/bin/env python3
"""
Tests for shared/scripts/schedule_config.py — per-workspace schedule config
+ cron-to-english helper (v2.14.10+).

Covers:
  - parse_cron (valid + invalid expressions, all field types)
  - cron_to_english (common patterns + fallback to raw)
  - load_schedule_config (defaults, overrides, missing file, malformed JSON,
    partial overrides)
  - task_display_name
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from schedule_config import (  # noqa: E402
    DEFAULT_SCHEDULES,
    DISPLAY_NAMES,
    CronParseError,
    cron_to_english,
    load_schedule_config,
    parse_cron,
    task_display_name,
)


def _check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  OK {label}")
    else:
        print(f"  FAIL {label}{(' --- ' + detail) if detail else ''}")
        raise AssertionError(label)


# -------- parse_cron --------


def test_parse_simple_field():
    print("test_parse_simple_field")
    minute, hour, dom, month, dow = parse_cron("0 7 * * 1-5")
    _check("minute = {0}", minute == {0})
    _check("hour = {7}", hour == {7})
    _check("dom = 1-31", dom == set(range(1, 32)))
    _check("month = 1-12", month == set(range(1, 13)))
    _check("dow = {1,2,3,4,5}", dow == {1, 2, 3, 4, 5})


def test_parse_list_field():
    print("test_parse_list_field")
    _, hour, _, _, _ = parse_cron("0 7,15 * * 1-5")
    _check("hour = {7, 15}", hour == {7, 15})


def test_parse_step_field():
    print("test_parse_step_field")
    _, hour, _, _, _ = parse_cron("0 */4 * * *")
    _check("every 4 hours = {0,4,8,12,16,20}",
           hour == {0, 4, 8, 12, 16, 20}, f"got {hour}")


def test_parse_invalid():
    print("test_parse_invalid")
    for bad in ["", "0 7 *", "60 7 * * *", "abc def * * *", "0 25 * * *"]:
        try:
            parse_cron(bad)
            _check(f"rejects {bad!r}", False, "should have raised")
        except CronParseError:
            _check(f"rejects {bad!r}", True)


# -------- cron_to_english --------


def test_english_weekdays():
    print("test_english_weekdays")
    _check("7 AM weekdays",
           cron_to_english("0 7 * * 1-5") == "7 AM weekdays",
           f"got {cron_to_english('0 7 * * 1-5')!r}")


def test_english_with_minutes():
    print("test_english_with_minutes")
    _check("8:30 AM weekdays",
           cron_to_english("30 8 * * 1-5") == "8:30 AM weekdays",
           f"got {cron_to_english('30 8 * * 1-5')!r}")


def test_english_pm():
    print("test_english_pm")
    _check("5 PM weekdays",
           cron_to_english("0 17 * * 1-5") == "5 PM weekdays",
           f"got {cron_to_english('0 17 * * 1-5')!r}")


def test_english_two_times():
    print("test_english_two_times")
    out = cron_to_english("0 7,15 * * 1-5")
    _check("two times", out == "7 AM and 3 PM weekdays", f"got {out!r}")


def test_english_daily():
    print("test_english_daily")
    _check("9 AM daily",
           cron_to_english("0 9 * * *") == "9 AM daily",
           f"got {cron_to_english('0 9 * * *')!r}")


def test_english_specific_day():
    print("test_english_specific_day")
    out = cron_to_english("0 9 * * 1")
    _check("9 AM Mondays", out == "9 AM Mondays", f"got {out!r}")


def test_english_weekends():
    print("test_english_weekends")
    out = cron_to_english("0 10 * * 0,6")
    _check("10 AM weekends", out == "10 AM weekends", f"got {out!r}")


def test_english_falls_back_on_complex():
    print("test_english_falls_back_on_complex")
    # Too many time slots — falls back to raw
    raw = "5,10,15 * * * *"
    _check("complex fallback", cron_to_english(raw) == raw)


def test_english_handles_invalid():
    print("test_english_handles_invalid")
    raw = "not a cron"
    _check("invalid fallback", cron_to_english(raw) == raw)


def test_english_noon():
    print("test_english_noon")
    _check("12 PM noon", cron_to_english("0 12 * * *") == "12 PM daily")


def test_english_midnight():
    print("test_english_midnight")
    _check("12 AM midnight", cron_to_english("0 0 * * *") == "12 AM daily")


# -------- load_schedule_config --------


def test_load_returns_defaults_when_no_file():
    print("test_load_returns_defaults_when_no_file")
    config = load_schedule_config("/nonexistent/path/entities.json")
    _check("contains all default tasks",
           set(config.keys()) == set(DEFAULT_SCHEDULES.keys()))
    _check("inbox cron matches default",
           config["inbox"]["cron"] == DEFAULT_SCHEDULES["inbox"]["cron"])
    _check("inbox enabled by default", config["inbox"]["enabled"] is True)


def test_load_uses_overrides():
    print("test_load_uses_overrides")
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "entities.json"
        path.write_text(json.dumps({
            "workspace": {
                "schedule_config": {
                    "inbox": {"cron": "0 8 * * 1-5", "enabled": True},
                    "pulse-disabled": {"cron": "0 9 * * 1-5", "enabled": False},
                },
            },
        }))
        config = load_schedule_config(path)
        _check("override applied to inbox",
               config["inbox"]["cron"] == "0 8 * * 1-5",
               f"got {config['inbox']['cron']}")
        _check("non-overridden tasks keep defaults",
               config["commitments"]["cron"]
               == DEFAULT_SCHEDULES["commitments"]["cron"])


def test_load_handles_disabled_flag():
    print("test_load_handles_disabled_flag")
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "entities.json"
        path.write_text(json.dumps({
            "workspace": {
                "schedule_config": {
                    "past-meetings": {"enabled": False},
                },
            },
        }))
        config = load_schedule_config(path)
        _check("disabled flag respected",
               config["past-meetings"]["enabled"] is False)
        _check("but cron still default",
               config["past-meetings"]["cron"]
               == DEFAULT_SCHEDULES["past-meetings"]["cron"])


def test_load_handles_malformed_json():
    print("test_load_handles_malformed_json")
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "entities.json"
        path.write_text("not json at all {{{{")
        config = load_schedule_config(path)
        _check("malformed → defaults", set(config.keys())
               == set(DEFAULT_SCHEDULES.keys()))


def test_load_auto_generates_label():
    print("test_load_auto_generates_label")
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "entities.json"
        path.write_text(json.dumps({
            "workspace": {
                "schedule_config": {
                    "inbox": {"cron": "0 8 * * 1-5"},  # no label provided
                },
            },
        }))
        config = load_schedule_config(path)
        _check("label auto-generated from cron",
               config["inbox"]["label"] == "8 AM weekdays",
               f"got {config['inbox']['label']!r}")


# -------- task_display_name --------


def test_display_name_known():
    print("test_display_name_known")
    _check("Pulse", task_display_name("pulse") == "Pulse")
    _check("Inbox", task_display_name("inbox") == "Inbox")
    _check("Past Meetings", task_display_name("past-meetings") == "Past Meetings")
    _check("Friday Wrap", task_display_name("friday-wrap") == "Friday Wrap")  # v3.11.0


def test_display_name_unknown_fallback():
    print("test_display_name_unknown_fallback")
    _check("unknown task → titled",
           task_display_name("cr-some-new-task") == "Some New Task")


def main():
    tests = [
        test_parse_simple_field,
        test_parse_list_field,
        test_parse_step_field,
        test_parse_invalid,
        test_english_weekdays,
        test_english_with_minutes,
        test_english_pm,
        test_english_two_times,
        test_english_daily,
        test_english_specific_day,
        test_english_weekends,
        test_english_falls_back_on_complex,
        test_english_handles_invalid,
        test_english_noon,
        test_english_midnight,
        test_load_returns_defaults_when_no_file,
        test_load_uses_overrides,
        test_load_handles_disabled_flag,
        test_load_handles_malformed_json,
        test_load_auto_generates_label,
        test_display_name_known,
        test_display_name_unknown_fallback,
    ]
    for t in tests:
        t()
    print(f"\nOK {len(tests)} schedule_config tests passed")


if __name__ == "__main__":
    main()
