#!/usr/bin/env python3
"""UXC1 — the 2026-07-21 plain-language rulings (widget UX review findings
5, 6, 7): "counterparty" never renders, record merges announce permanence,
snake_case deal enums never reach a picker.

Covers: (a) STAGE_DISPLAY / LOSS_REASON_DISPLAY cover the wire enums EXACTLY
(a new stage or reason without a plain label goes red here, never renders
raw) and no display label leaks a snake_case token; (b) the merge verb's
taxonomy label carries "(permanent)" and the bare "Merge records" is in
LEGACY_DISPLAY_LABELS (no new render may show it); (c) instruction-layer
pins — the my-plate orphan tag prose carries no "counterparty", the
staff-meeting undo footer carries the record-merge carve-out, the
apply-choices picker prose names both display maps, and "counterparty" is in
the banned-word guard's list; (d) the display overlay's two UXC1 copy shapes
directly (unresolved plain / resolved plain), on a real-shaped fixture.

Placeholder names only (Sam Sample); dates relative (G14)."""
import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "shared" / "scripts"))
import deal_state  # noqa: E402
import surface_drivers as sd  # noqa: E402
import verb_taxonomy as vt  # noqa: E402
from thread_writer import DEAL_LOSS_REASONS, DEAL_STAGES  # noqa: E402

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


# ===========================================================================
# (a) display maps cover the enums exactly; labels are plain
# ===========================================================================
print("[a] enum display coverage")
check(set(deal_state.STAGE_DISPLAY) == set(DEAL_STAGES),
      f"STAGE_DISPLAY covers DEAL_STAGES exactly: "
      f"{set(deal_state.STAGE_DISPLAY) ^ set(DEAL_STAGES)}")
check(set(deal_state.LOSS_REASON_DISPLAY) == set(DEAL_LOSS_REASONS),
      f"LOSS_REASON_DISPLAY covers DEAL_LOSS_REASONS exactly: "
      f"{set(deal_state.LOSS_REASON_DISPLAY) ^ set(DEAL_LOSS_REASONS)}")
snake = re.compile(r"[a-z]+_[a-z]+")
for label in (list(deal_state.STAGE_DISPLAY.values())
              + list(deal_state.LOSS_REASON_DISPLAY.values())):
    check(not snake.search(label) and label[0].isupper(),
          f"display label is plain, capitalized, never snake_case: {label!r}")

# ===========================================================================
# (b) merge label announces permanence; bare label banned on new renders
# ===========================================================================
print("[b] merge permanence label")
merge_rows = [r for r in vt.VERB_TAXONOMY
              if r.get("action_id") == "merge person records"]
check(len(merge_rows) == 1 and
      merge_rows[0].get("verb") == "Merge records (permanent)",
      f"taxonomy label carries (permanent): {merge_rows}")
check("Merge records" in vt.LEGACY_DISPLAY_LABELS,
      "bare 'Merge records' joined LEGACY_DISPLAY_LABELS")
check("Merge records (permanent)" not in vt.LEGACY_DISPLAY_LABELS,
      "the new label itself is not banned")

# ===========================================================================
# (c) instruction-layer pins
# ===========================================================================
print("[c] instruction-layer pins")
myplate = (REPO / "skills" / "enable-command-room-schedules" / "references"
           / "orchestrator-my-plate.md").read_text(encoding="utf-8")
check("(counterparty unresolved — who was this for?)" not in myplate,
      "my-plate orphan tag no longer instructs the counterparty wording")
check("tag the row `(who was this for?)`" in myplate,
      "my-plate orphan tag instructs the plain wording")
staff = (REPO / "skills" / "enable-command-room-schedules" / "references"
         / "orchestrator-staff-meeting.md").read_text(encoding="utf-8")
check("except the record merge, that one is permanent" in staff,
      "staff-meeting undo footer carries the record-merge carve-out")
apply_md = (REPO / "skills" / "apply-choices" / "SKILL.md").read_text(
    encoding="utf-8")
check("STAGE_DISPLAY" in apply_md and "LOSS_REASON_DISPLAY" in apply_md,
      "apply-choices picker prose names both display maps")
check("Doing it themselves" in apply_md,
      "loss-reason picker prose shows the plain labels")
guard = (REPO / "tests" / "run_pl_banned_words_test.py").read_text(
    encoding="utf-8")
check('"counterparty"' in guard,
      "'counterparty' is in the banned-word guard's list")

# ===========================================================================
# (d) the overlay's two UXC1 copy shapes, real-shaped fixture
# ===========================================================================
print("[d] overlay copy shapes")
ws = Path(tempfile.mkdtemp(prefix="uxc1_"))
(ws / "_hq" / "data").mkdir(parents=True)
(ws / "_hq" / "data" / "entities.json").write_text(json.dumps(
    {"version": 1, "people": [
        {"id": "person_1", "canonical_name": "Sam Sample"}],
     "orgs": [], "threads": [], "engagements": []}), encoding="utf-8")
stored_hit = "counterparty 'Sam Sample' has no person record"
stored_miss = "counterparty 'Quinn Sample' has no person record"
out_hit = sd._display_review_reason(ws, stored_hit, {})
out_miss = sd._display_review_reason(ws, stored_miss, {})
check(out_hit == "'Sam Sample' — contact added ✓",
      f"resolved name renders the plain contact-added copy: {out_hit!r}")
check(out_miss == "'Quinn Sample' isn't in your contacts yet",
      f"unresolved name renders the plain not-in-contacts copy: {out_miss!r}")
check("counterparty" not in out_hit and "counterparty" not in out_miss,
      "the banned word never survives the overlay")

print(f"OK — {PASS} checks passed")
