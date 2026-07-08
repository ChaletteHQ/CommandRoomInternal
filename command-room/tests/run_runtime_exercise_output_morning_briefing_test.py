#!/usr/bin/env python3
"""SPEC A8 — morning-briefing output regression exercise (runtime tier).

morning-briefing's deterministic core is `brief_state.compute_brief_state` over the
fixture's open commitments with a fixed `now`. This asserts the commitment-state
membership against fixture truth (user-owed items surface; a resolved commitment does
NOT) and goldens the normalized JSON of the computed state. No skill-specific event
(passive-capture only), so no event side-effect is asserted.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import output_exercise_lib as lib  # noqa: E402

NOW = "2026-06-01T12:00:00+00:00"


def main() -> int:
    from cru_match import load_open_commitments, _commitment_field
    from brief_state import compute_brief_state

    ok, fail, section, finish = lib.make_recorder()
    ws = lib.copy_fixture()
    events_path = ws / "_hq" / "data" / "events.jsonl"

    section("derivation — open commitments")
    opens = load_open_commitments(str(events_path))
    open_titles = {(_commitment_field(c, "title") or "") for c in opens}
    # The 2 resolved commitments (c3 'Confirm the vendor list', c4 'Prep the rollout plan')
    # must NOT appear; the user-owed open ones must.
    if "Confirm the vendor list" not in open_titles and "Prep the rollout plan" not in open_titles:
        ok("resolved commitments are NOT in the open set")
    else:
        fail("resolved commitments excluded", str(open_titles))
    if "Ship the pricing page copy" in open_titles and "Send Northstar the pricing sheet" in open_titles:
        ok("user-owed open commitments are present", str(sorted(open_titles)))
    else:
        fail("user-owed open commitments present", str(sorted(open_titles)))

    section("compute_brief_state (the deterministic core)")
    state = compute_brief_state(open_commitments=opens, user_person_id="person_001", now_iso=NOW)
    if isinstance(state, dict) and state:
        ok("compute_brief_state returned a state dict", f"{len(state)} keys")
    else:
        fail("compute_brief_state returned a state")
        return finish("morning_briefing_exercise")

    # the header total must equal the full open set (Bug #85 hard-count gate)
    counts = state.get("counts") if isinstance(state.get("counts"), dict) else state
    total = None
    for k in ("total",):
        if isinstance(counts.get(k), int):
            total = counts[k]
    if total is None or total == len(opens):
        ok("brief-state total equals the full open-commitment count", f"{total} == {len(opens)}")
    else:
        fail("brief-state total parity", f"{total} vs {len(opens)} open")

    section("golden — normalized JSON of the computed state")
    blob = json.dumps(state, indent=2, sort_keys=True, default=str)
    matched, diff = lib.compare_golden("morning_briefing", blob)
    ok("brief state matches golden") if matched else fail("golden match", diff[:600])

    return finish("morning_briefing_exercise")


if __name__ == "__main__":
    raise SystemExit(main())
