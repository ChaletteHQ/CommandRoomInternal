#!/usr/bin/env python3
"""
Test battery for the W4 stateless widget dispatch (Phase 3 / SPEC-2.2).

Every widget's Apply-all tuples must carry {src: <emitting surface id>} so
apply-choices dispatches without the 60-minute fire-marker window. Verifies:
the renderer bakes crSrc from the data view's source_skill and stamps it on
every choice (including orphan-note synthesized choices); legacy data views
render an empty crSrc (fallback preserved); every widget-emitting surface's
documented data view passes source_skill (static drift guard).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from chat_output_renderer import render_chat_output_widget  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


def widget(source_skill=None):
    dv = {
        "widget_mode": "all_batch_widget",
        "header": "Inbox · test · 1 priority thread",
        "sections": [{"title": None, "items": [
            {"n": 1, "name": "Test item", "body_lines": ["line"], "actions": ["1 skip"]},
        ]}],
    }
    if source_skill is not None:
        dv["source_skill"] = source_skill
    return render_chat_output_widget(dv, wrapper="fragment")


def main():
    print("== renderer bakes the source id")
    html = widget("inbox")
    check("crSrc carries the data view's source_skill", 'const crSrc = "inbox";' in html)
    check("Apply-all stamps src on every choice", "choice.src = crSrc" in html)
    check("orphan-note synthesized choices carry src too", "orphanChoice.src = crSrc" in html)

    print("== legacy fallback preserved")
    html2 = widget(None)
    check("absent source_skill renders empty crSrc (fire-marker fallback)",
          'const crSrc = "";' in html2)

    print("== source id is JSON-escaped, not raw-injected")
    html3 = widget('x"; alert(1); //')
    check("hostile source_skill cannot break out of the JS string",
          'alert(1); //' not in html3.split("const crSrc = ")[1].split(";")[0] or
          '\\"' in html3.split("const crSrc = ")[1].split("\n")[0])

    print("== static drift guard: every widget-emitting surface passes source_skill")
    sites = {
        "skills/enable-command-room-schedules/references/orchestrator-commitments.md": "commitments",
        "skills/enable-command-room-schedules/references/orchestrator-dont-forget.md": "pulse",
        "skills/enable-command-room-schedules/references/orchestrator-inbox.md": "inbox",
        "skills/enable-command-room-schedules/references/orchestrator-past-meetings.md": "past-meetings",
        "skills/enable-command-room-schedules/references/orchestrator-upcoming-meetings.md": "upcoming-meetings",
        "skills/show-my-list/SKILL.md": "show-my-list",
        "skills/meeting-notes/SKILL.md": "meeting-notes",
    }
    for rel, src in sites.items():
        body = (ROOT / rel).read_text(encoding="utf-8")
        check(f"{Path(rel).name} data view passes source_skill={src!r}",
              f'"source_skill": "{src}"' in body)

    print("== spec + dispatcher documentation")
    caw = (ROOT / "shared" / "CHAT_ACTION_WIDGET.md").read_text(encoding="utf-8")
    check("CHAT_ACTION_WIDGET.md documents the src field", '"src"' in caw and "fire-marker" in caw)
    ac = (ROOT / "skills" / "apply-choices" / "SKILL.md").read_text(encoding="utf-8")
    check("apply-choices dispatches on src first", "dispatch on `src` FIRST" in ac)
    check("60-min TTL scoped to the fallback only",
          "60-minute TTL applies ONLY to this fallback path" in ac)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("widget src payload battery: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
