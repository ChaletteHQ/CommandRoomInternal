#!/usr/bin/env python3
"""
v2.14.21+ — Regression test for the ORCHESTRATOR_MAP integrity contract.

This test exists because v2.14.20 shipped a registration bug where
enable-command-room-schedules registered short stub prompts in Cowork's
scheduled-task DB instead of the canonical orchestrator-*.md file bodies.
Five tasks registered with ~28-line briefs missing every contract primitive
(no v2.13.0 OUTPUT CONTRACT, no STOP CONTRACT, no Phase scaffolding, no
render_chat_output_widget calls). Every fire bypassed the renderer +
validators + STOP CONTRACT. Bug was structural — the registration step
was unverified at install time.

This test asserts the source-side preconditions that the runtime
registration step depends on:

  1. Each taskId in ORCHESTRATOR_MAP has a corresponding orchestrator-*.md
     file in references/.
  2. Every chat-emitting orchestrator-*.md file's first 1500 chars contain
     the v2.13.0 OUTPUT CONTRACT marker.
  3. The silent task (cr-refresh-workspace-map) carries the projector
     pipeline calls (build_workspace_map_input.py + render_artifact.py).
  4. There is NO orchestrator-pulse.md file (Pulse content lives in
     orchestrator-dont-forget.md per the v2.14.10 internal-id rename).
  5. Each orchestrator file is at least 1500 chars (real content, not stub).

If this test fails, registration will fail Phase 1's read-step assertions
and the skill aborts cleanly — no stub prompts make it into Cowork's DB.
"""

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
REF_DIR = os.path.join(
    PLUGIN_ROOT, "skills", "enable-command-room-schedules", "references"
)


# Canonical mapping — must match the ORCHESTRATOR_MAP in
# skills/enable-command-room-schedules/SKILL.md Phase 1.
# v2.14.27+ — bare taskIds (no cr- prefix) so Cowork's title rendering is clean.
ORCHESTRATOR_MAP = {
    "morning-brief": "orchestrator-morning-brief.md",
    "upcoming-meetings": "orchestrator-upcoming-meetings.md",
    "inbox": "orchestrator-inbox.md",
    "commitments": "orchestrator-commitments.md",
    "pulse": "orchestrator-dont-forget.md",  # filename stays for events.jsonl source_skill back-compat
    "past-meetings": "orchestrator-past-meetings.md",
    "friday-wrap": "orchestrator-friday-wrap.md",  # v3.11.0 — first weekly-rhythm task (Fri 4 PM)
    "relationship-moves": "orchestrator-relationship-moves.md",  # REL1 — weekly Sunday outreach pack
}

CHAT_EMITTING_TASKS = {
    "morning-brief",
    "upcoming-meetings",
    "inbox",
    "commitments",
    "pulse",
    "past-meetings",
    "friday-wrap",
    "relationship-moves",
}

SILENT_TASKS = set()  # cr-refresh-workspace-map removed in v2.14.25

CONTRACT_MARKER = "OUTPUT CONTRACT (v2.13.0+ — MANDATORY)"
MIN_BODY_LEN = 1500


passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")
        if detail:
            print(f"      {detail}")


def main():
    print("=== ORCHESTRATOR_MAP integrity test ===\n")

    # Test 1: every taskId has a corresponding file
    print("[1] every taskId in ORCHESTRATOR_MAP has a file in references/")
    for task_id, fname in ORCHESTRATOR_MAP.items():
        path = os.path.join(REF_DIR, fname)
        check(
            f"{task_id} → {fname}",
            os.path.isfile(path),
            f"missing file: {path}",
        )
    print()

    # Test 2: chat-emitting files contain the OUTPUT CONTRACT marker
    print("[2] every chat-emitting orchestrator-*.md has the v2.13.0 OUTPUT CONTRACT marker in its first 1500 chars")
    for task_id in CHAT_EMITTING_TASKS:
        fname = ORCHESTRATOR_MAP[task_id]
        path = os.path.join(REF_DIR, fname)
        if not os.path.isfile(path):
            check(f"{fname} contains OUTPUT CONTRACT", False, "file missing")
            continue
        body = open(path, encoding="utf-8").read()
        head = body[:1500]
        check(
            f"{fname} contains OUTPUT CONTRACT",
            CONTRACT_MARKER in head,
            f"first 1500 chars do not contain {CONTRACT_MARKER!r}",
        )
    print()

    # Test 3: cr-refresh-workspace-map task was REMOVED in v2.14.25 — confirm it's no longer in ORCHESTRATOR_MAP
    print("[3] cr-refresh-workspace-map removed from active taskIds (v2.14.25+)")
    check(
        "cr-refresh-workspace-map NOT in ORCHESTRATOR_MAP",
        "cr-refresh-workspace-map" not in ORCHESTRATOR_MAP,
        "v2.14.25 removed the daily auto-refresh task; it should not be in active map",
    )
    print()

    # Test 4: no orchestrator-pulse.md (Pulse lives in orchestrator-dont-forget.md)
    print("[4] no orchestrator-pulse.md (Pulse content lives in orchestrator-dont-forget.md)")
    pulse_path = os.path.join(REF_DIR, "orchestrator-pulse.md")
    check(
        "orchestrator-pulse.md does NOT exist",
        not os.path.exists(pulse_path),
        f"unexpected file at {pulse_path} — would cause taskId confusion",
    )
    # And confirm dont-forget IS the Pulse file. Per v2.14.27+ rename, the
    # canonical taskId is bare `pulse`; the orchestrator filename stays as
    # `orchestrator-dont-forget.md` for events.jsonl source_skill back-compat.
    df_path = os.path.join(REF_DIR, "orchestrator-dont-forget.md")
    if os.path.isfile(df_path):
        df_body = open(df_path, encoding="utf-8").read()
        check(
            "orchestrator-dont-forget.md identifies itself as taskId pulse (v2.14.27+)",
            "pulse" in df_body[:500].lower(),
            "first 500 chars do not mention canonical bare taskId 'pulse'",
        )
        check(
            "orchestrator-dont-forget.md warns about pre-v2.14.27 legacy taskId variants",
            "cr-pulse" in df_body and "cr-dont-forget" in df_body and "v2.14.27" in df_body,
            "missing the legacy-taskId warning (must mention cr-pulse, cr-dont-forget, and v2.14.27)",
        )
    print()

    # Test 5: every orchestrator file is real content (not a stub)
    print("[5] every orchestrator-*.md is at least 1500 chars (not a stub)")
    for task_id, fname in ORCHESTRATOR_MAP.items():
        path = os.path.join(REF_DIR, fname)
        if not os.path.isfile(path):
            continue
        size = os.path.getsize(path)
        check(
            f"{fname} >= {MIN_BODY_LEN} chars",
            size >= MIN_BODY_LEN,
            f"only {size} chars — too short to be a real orchestrator",
        )
    print()

    # Test 6: ORCHESTRATOR_MAP in SKILL.md matches this file's map
    print("[6] ORCHESTRATOR_MAP in SKILL.md matches the test fixture")
    skill_path = os.path.join(
        PLUGIN_ROOT, "skills", "enable-command-room-schedules", "SKILL.md"
    )
    if os.path.isfile(skill_path):
        skill_body = open(skill_path, encoding="utf-8").read()
        for task_id, fname in ORCHESTRATOR_MAP.items():
            check(
                f"SKILL.md contains taskId {task_id}",
                task_id in skill_body,
                f"taskId {task_id} not found in SKILL.md",
            )
            check(
                f"SKILL.md contains filename {fname}",
                fname in skill_body,
                f"filename {fname} not found in SKILL.md",
            )

    # Test 7: v3.2.3+ speaker-attribution-ambiguity guard is documented in past-meetings + meeting-notes
    print("[7] v3.2.3+ Rio-Baker speaker-attribution guard is in place")
    pm_path = os.path.join(REF_DIR, "orchestrator-past-meetings.md")
    if os.path.isfile(pm_path):
        pm_body = open(pm_path, encoding="utf-8").read()
        check(
            "orchestrator-past-meetings.md has Phase 4.5d attribution-ambiguity guard",
            "4.5d" in pm_body and "attribution_ambiguous" in pm_body,
            "missing Phase 4.5d block — the v3.2.3 speaker-attribution fix per Sam's Rio Lange / Rio Sample misattribution",
        )
        check(
            "Phase 4.5d explicitly names attribution_ambiguous and attribution_candidates",
            "attribution_ambiguous" in pm_body and "attribution_candidates" in pm_body,
            "missing the data envelope fields the rule introduces",
        )
        check(
            "Phase 4.5d explicitly says 'never auto-pick on ambiguous'",
            "auto-pick" in pm_body.lower() or "auto pick" in pm_body.lower(),
            "missing the anti-improvisation rule against alphabetical / first-mentioned auto-pick",
        )
    mn_path = os.path.join(PLUGIN_ROOT, "skills", "meeting-notes", "SKILL.md")
    if os.path.isfile(mn_path):
        mn_body = open(mn_path, encoding="utf-8").read()
        check(
            "meeting-notes/SKILL.md Step 5e has the speaker-attribution ambiguity guard",
            "attribution_ambiguous" in mn_body and "v3.2.3" in mn_body,
            "missing the parallel guard in meeting-notes Step 5e",
        )
    print()

    # Test 8: v3.2.3+ cr-inbox empty-state tracked_items population is documented
    print("[8] v3.2.3+ cr-inbox empty-state tracked_items population rule is in place")
    ib_path = os.path.join(REF_DIR, "orchestrator-inbox.md")
    if os.path.isfile(ib_path):
        ib_body = open(ib_path, encoding="utf-8").read()
        check(
            "orchestrator-inbox.md mentions v3.2.3+ tracked_items population",
            "v3.2.3" in ib_body and "tracked_items" in ib_body,
            "missing the v3.2.3+ tracked_items extension to the empty-state rule",
        )
        check(
            "orchestrator-inbox.md names the three contributing classes",
            "vendor estimate" in ib_body.lower() or "financial-signal" in ib_body.lower(),
            "missing the explicit classes (vendor estimates / auto-decline / outbound)",
        )
        check(
            "orchestrator-inbox.md caps tracked_items at 7 rows",
            "7" in ib_body and "tracked_items" in ib_body,
            "missing the cap on total rows surfaced",
        )
    print()

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
