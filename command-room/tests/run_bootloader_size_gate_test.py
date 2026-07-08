#!/usr/bin/env python3
"""
Bootloader gate-vs-template composition test (Phase 3 / P0.2 — guard G8).

Composes a bootloader for EVERY task in orchestrator-map.json from the REAL
template (same substitutions Phase 1.B performs, including the W4
plugin-version stamp), then runs the Phase 3.5 checks against it:
required markers, substitution completeness, no frontmatter, and the
runtime-computed 0.9x-1.5x size bounds. This is the test that makes
gate-vs-template drift impossible to re-ship — the pre-Phase-3 gate
hardcoded three inconsistent ranges while the template grew past them, so
every healthy install reported a failed setup.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "skills" / "enable-command-room-schedules" / "references"

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


REQUIRED_MARKERS = [
    "# Scheduled task bootloader",
    "Resolve the plugin path",
    "Read the orchestrator and execute it verbatim",
    "Anti-improvisation contract",
]


def compose(task_id: str, fname: str, basename="Penelopes Brain", version="4.4.0") -> str:
    template = (REF / "scheduled-task-bootloader.md").read_text(encoding="utf-8")
    marker = "## The bootloader template (everything below this heading is the registered prompt body)"
    assert marker in template
    body = template.split(marker, 1)[1].lstrip("\n").lstrip()
    return (body
            .replace("<TASK_ID>", task_id)
            .replace("<ORCHESTRATOR_FILENAME>", fname)
            .replace("<WORKSPACE_BASENAME>", basename)
            .replace("<PLUGIN_VERSION>", version))


def gate_check(task_id, fname, prompt, expected_len):
    """The Phase 3.5 verification, as code — mirrors the SKILL.md pseudocode."""
    problems = []
    if prompt.lstrip().startswith("---"):
        problems.append("frontmatter")
    for m in REQUIRED_MARKERS:
        if m not in prompt:
            problems.append(f"missing marker {m!r}")
    if f"`{task_id}`" not in prompt:
        problems.append("task-id substitution")
    if fname not in prompt:
        problems.append("orchestrator filename")
    for ph in ("<TASK_ID>", "<ORCHESTRATOR_FILENAME>", "<WORKSPACE_BASENAME>", "<PLUGIN_VERSION>"):
        if ph in prompt:
            problems.append(f"unsubstituted {ph}")
    if len(prompt) > expected_len * 1.5:
        problems.append("too large")
    if len(prompt) < expected_len * 0.9:
        problems.append("too small")
    return problems


def main():
    omap = {k: v for k, v in json.loads((REF / "orchestrator-map.json").read_text(encoding="utf-8")).items()
            if not k.startswith("_")}
    check("orchestrator map non-empty", len(omap) >= 7, repr(omap))

    print("== every real composed bootloader passes the gate")
    for task_id, fname in omap.items():
        prompt = compose(task_id, fname)
        problems = gate_check(task_id, fname, prompt, len(prompt))
        check(f"{task_id}: composed bootloader passes ({len(prompt)} chars)",
              not problems, repr(problems))
        check(f"{task_id}: carries the version stamp",
              "plugin-version: 4.4.0" in prompt)

    print("== the gate still catches the two regression classes")
    tid, fname = next(iter(omap.items()))
    good = compose(tid, fname)
    stub = f"# Scheduled task bootloader — {tid}\nRun the {tid} task."
    check("stub-improvisation caught",
          "too small" in gate_check(tid, fname, stub, len(good))
          or any("missing marker" in p for p in gate_check(tid, fname, stub, len(good))))
    bloated = good + ("\n# padding" * 400)
    check("full-orchestrator-body-registered caught",
          "too large" in gate_check(tid, fname, bloated, len(good)))
    unsub = compose(tid, fname).replace("4.4.0", "<PLUGIN_VERSION>")
    check("unsubstituted version stamp caught",
          any("unsubstituted <PLUGIN_VERSION>" in p for p in gate_check(tid, fname, unsub, len(good))))

    print("== the retired hardcoded ranges are gone from the gate")
    skill = (ROOT / "skills" / "enable-command-room-schedules" / "SKILL.md").read_text(encoding="utf-8")
    check("no 'between 1500 and 3500' claim", "between 1500 and 3500" not in skill)
    check("no '~5500 chars' claim survives as a bound", "are ~5500 chars" not in skill)
    check("no hardcoded 7000-char abort", "> 7000" not in skill)
    check("gate computes bounds from the composed bootloader",
          "expected * 1.5" in skill and "expected * 0.9" in skill)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("bootloader size-gate battery: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
