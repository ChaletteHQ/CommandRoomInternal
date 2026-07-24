#!/usr/bin/env python3
"""
v4.5.2 S2 regression suite — verb taxonomy + widget feedback.

Guards the three dogfood findings this build fixed:

  F-59  one verb table: every widget verb resolves to a taxonomy row with a
        dispatch target; one display label per verb; mutes state their TTL
        on the button; no legacy two-name labels (Resolved/Push to/Skip/...)
        survive in any rendered widget.
  F-58  pressed-state + live selection counter are structurally enforced —
        HTML with action buttons but no visible-feedback layer is rejected
        before show_widget.
  F-17  a selected action missing its REQUIRED input (Defer without a date)
        can never silently block Apply: the row highlights, the missing
        thing is named inline, and Apply carries the reason.

Run via: python3 tests/run_verb_taxonomy_test.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from chat_output_renderer import (  # noqa: E402
    CANONICAL_ACTIONS,
    WidgetFeedbackContractError,
    _action_display_label,
    render_chat_output_widget,
    validate_rendered_widget,
)
from event_types import load_event_types  # noqa: E402
from verb_taxonomy import (  # noqa: E402
    DEPRECATED_ALIASES,
    LEGACY_DISPLAY_LABELS,
    REQUIRED_INPUT_ACTION_IDS,
    VERB_TAXONOMY,
    display_label,
    mute_ttl_days,
    required_input_thing,
    taxonomy_row,
)

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}{(' — ' + detail) if detail else ''}")


# ---------------------------------------------------------------------------
# Fixtures — one widget per verb family, rendered through the canonical path.
# ---------------------------------------------------------------------------

def _render(items, source="commitment-triage", **extra):
    data = {"header": "x", "source_skill": source,
            "sections": [{"title": None, "items": items}]}
    data.update(extra)
    return render_chat_output_widget(data, wrapper="fragment")


def render_all_fixture_widgets():
    """Render every taxonomy verb that a widget can emit, across fixture
    widgets shaped like the real surfaces. Returns list of html strings."""
    htmls = []
    # Commitment triage (full-list layout w/ tiles + needs-confirm row)
    htmls.append(_render(
        [
            {"n": 1, "name": "Send deck", "context_tag": "12d, undated",
             "actions": ["resolved", "push to [date]", "drop", "not mine",
                          "make task", "never track this", "skip"]},
            {"n": 2, "name": "Task row", "context_tag": "task (yours)",
             "actions": ["resolved", "push to [date]", "drop", "promote",
                          "never track this", "skip"]},
            {"n": 3, "name": "Needs confirm",
             "reduced_verbs_reason": "Fewer options — the owner is unconfirmed; clicking Done, Drop, or Not mine confirms it.",
             "actions": ["resolved", "drop", "not mine", "skip"]},
        ],
        counters=[{"label": "Open", "value": 3}, {"label": "You owe", "value": 2}],
        # SPEC OUT2 §2b — the docx-parity tile band (validated component
        # shape) renders on the main widget path alongside legacy counters.
        tiles=[{"label": "Oldest open", "value": "47d"}],
    ))
    # Email-shaped (inbox / commitments)
    htmls.append(_render(
        [
            {"n": 1, "icon": "✉", "name": "Sam", "subject": "Q2",
             "metadata": [("To", "sam@example.com"), ("Subject", "Re: Q2")],
             "body_lines": ["Hey —"],
             "actions": ["send", "draft", "snooze 3d"]},
            {"n": 2, "name": "Owed to you",
             "actions": ["follow-up call", "mark received", "skip"]},
            {"n": 3, "name": "Self item",
             "actions": ["prep deep work", "push to [date]", "mark done", "skip"]},
        ],
        source="commitments",
    ))
    # Pulse clusters
    htmls.append(_render(
        [
            {"n": 1, "icon": "👤", "name": "Bo Sample",
             "metadata": [
                 ("Last contact", "18 days ago — Apr 18, Slack DM"),
                 ("Why they matter", "Direct report"),
                 ("Open context", "(no open thread tracked)"),
                 ("What's at stake", "Aug 4 cutover"),
             ],
             "actions": ["investigate", "draft re-engagement",
                          "schedule catchup [when]", "resolved",
                          "snooze 3d"]},  # MLK1: `add to my list` retired
            {"n": 2, "name": "Stale project",
             "actions": ["prep deep work", "investigate", "mark paused",
                          "status check", "snooze 14d", "skip"]},
            {"n": 3, "name": "Review row",
             "actions": ["add [text]", "not relevant"]},
            {"n": 4, "name": "Dormant proposal",
             "actions": ["active", "keep paused", "archive", "skip"]},
            {"n": 5, "name": "Entity proposal",
             "actions": ["confirm [type]", "edit [type]", "not relevant", "skip"]},
            {"n": 6, "name": "Intro check",
             "actions": ["landed", "didnt land", "snooze 14d", "skip"]},
        ],
        source="dont-forget",
    ))
    # Meetings + decisions
    htmls.append(_render(
        [
            {"n": 1, "name": "Michele call",
             "actions": ["context [text]", "push meeting [date]", "skip"]},
            {"n": 2, "name": "Vague timing",
             "actions": ["set date [when]", "skip"]},
            {"n": 3, "name": "New person",
             "actions": ["add as person to [org]", "add as new org",
                          "add context [text]", "skip"]},
            {"n": 4, "name": "Decision",
             "actions": ["revisit", "still valid", "replace", "snooze 30d", "skip"]},
            {"n": 5, "name": "Memo",
             "actions": ["decide [text]", "edit [change]", "skip"]},
        ],
        source="past-meetings",
    ))
    # Reminders (brief Pinned block / show-my-reminders widget shape)
    htmls.append(_render(
        [
            {"n": 1, "name": "Pedro chase reminder",
             "context_tag": "pinned since Monday",
             "actions": ["reminder done", "reminder push [date]", "reminder keep"]},
        ],
        source="show-my-reminders",
    ))
    return htmls


# T2.2 row diet — verbs render as <option>s inside the row dropdown;
# footer buttons are unchanged. The label contract is identical.
# WG1-A D-A3 — a ≤4-option row now renders its verbs as cr-action BUTTONS
# (primary + secondary), not <option>s, so the same label contract must be
# read off the button text too.
OPTION_LABEL_RE = re.compile(
    r'<option value="[^"]+"[^>]*>([^<]*)</option>'
)
FOOTER_BUTTON_RE = re.compile(
    r'<button class="cr-btn-(?:apply|secondary)"[^>]*>([^<]*)</button>'
)
ACTION_BUTTON_RE = re.compile(
    r'<button class="cr-action(?:[^"]*)"[^>]*data-action="[^"]*"[^>]*>([^<]*)</button>'
)


def main() -> int:
    # ------------------------------------------------------------------
    print("[1] table completeness — every widget verb resolves to a row + dispatch")
    # ------------------------------------------------------------------
    known_events = load_event_types()
    check("event enum loaded (schema readable)", bool(known_events))
    for action_id in sorted(CANONICAL_ACTIONS):
        row = taxonomy_row(action_id)
        if row is None:
            check(f"{action_id!r} has a taxonomy row", False)
            continue
        ok = bool(row["verb"]) and bool(row["effect"]) and bool(row["surfaces"])
        check(f"{action_id!r} → row with verb/effect/surfaces", ok)
    for row in VERB_TAXONOMY:
        if row["event"] is not None:
            check(
                f"{row['action_id']!r} event {row['event']!r} registered in schema enum",
                row["event"] in known_events,
            )
        if row["family"] in ("commitment", "reminder", "mute"):
            check(
                f"{row['action_id']!r} ({row['family']}) names its event",
                row["event"] is not None,
            )
    for alias, repl in DEPRECATED_ALIASES.items():
        check(f"alias {alias!r} resolves", taxonomy_row(alias) is not None
              and display_label(alias) == display_label(repl))

    # ------------------------------------------------------------------
    print("[2] one label per verb; mutes state their TTL on the button (F-59)")
    # ------------------------------------------------------------------
    expected = {
        "resolved": "Done",
        "mark done": "Done",
        "push to [date]": "Later…",  # t3 FB-3 — the merged Defer/Snooze option
        "skip": "Snooze (1 day)",
        "skip all": "Snooze rest (1 day)",
        "snooze 3d": "Snooze (3 days)",
        "not relevant": "Not relevant (60 days)",
        "never track this": "Never track (permanent)",
        "make task": "Turn into a task",  # UXR1 D7b — conversion, not create-new
        "promote": "Make it a commitment",
        "reminder done": "Done",
        "reminder push [date]": "Later…",  # t3 FB-3 — lockstep with the commitment lane
        "reminder keep": "Keep",
    }
    for aid, want in expected.items():
        got = _action_display_label(aid)
        check(f"label {aid!r} → {want!r}", got == want, f"got {got!r}")
    for row in VERB_TAXONOMY:
        ttl = row["mute_ttl_days"]
        if ttl is None:
            continue
        verb = row["verb"]
        if ttl == "permanent":
            check(f"mute {row['action_id']!r} label states 'permanent'",
                  "permanent" in verb.lower(), verb)
        else:
            unit = "1 day" if ttl == 1 else f"{ttl} days"
            check(f"mute {row['action_id']!r} label states '{unit}'",
                  unit in verb, verb)
        check(f"mute_ttl_days({row['action_id']!r}) == {ttl!r}",
              mute_ttl_days(row["action_id"]) == ttl)

    # ------------------------------------------------------------------
    print("[3] rendered-widget scan — no legacy verb labels remain (F-13 P2a/F-18)")
    # ------------------------------------------------------------------
    htmls = render_all_fixture_widgets()
    check("all fixture widgets render + validate", all(
        validate_rendered_widget(h) is None for h in htmls))
    seen_labels = set()
    for h in htmls:
        seen_labels.update(m.strip() for m in OPTION_LABEL_RE.findall(h))
        seen_labels.update(m.strip() for m in FOOTER_BUTTON_RE.findall(h))
        seen_labels.update(m.strip() for m in ACTION_BUTTON_RE.findall(h))
    check("fixture coverage: labels were extracted", len(seen_labels) > 20,
          f"only {len(seen_labels)}")
    for legacy in sorted(LEGACY_DISPLAY_LABELS):
        check(f"legacy label {legacy!r} absent from every rendered verb option",
              legacy not in seen_labels)
    for want in ("Done", "Later…", "Snooze (1 day)", "Never track (permanent)",
                 "Not relevant (60 days)", "Make it a commitment", "Keep",
                 "Snooze rest (1 day)"):
        check(f"taxonomy label {want!r} rendered somewhere", want in seen_labels)

    # ------------------------------------------------------------------
    print("[4] F-58 — pressed-state + live counter enforced structurally")
    # ------------------------------------------------------------------
    html = htmls[0]
    # T2.2 — the armed-state contract, ported to dropdowns: a non-empty
    # selection is visibly distinct (.cr-select-armed), enforced structurally
    # by validate_rendered_widget exactly as .cr-selected was for buttons.
    check("armed-state CSS present", "select.cr-action-select.cr-select-armed" in html)
    check("counter reads 'of N selected'", "of 3 selected" in html)
    check("counter is live (id=cr-count)", 'id="cr-count"' in html)
    style_stripped = re.sub(r"<style>.*?</style>", "<style>.x{}</style>", html,
                            flags=re.S)
    try:
        validate_rendered_widget(style_stripped)
        check("HTML without armed-state CSS is rejected", False)
    except WidgetFeedbackContractError:
        check("HTML without armed-state CSS is rejected", True)
    try:
        validate_rendered_widget(html.replace('id="cr-count"', 'id="x"'))
        check("HTML without the counter is rejected", False)
    except WidgetFeedbackContractError:
        check("HTML without the counter is rejected", True)

    # ------------------------------------------------------------------
    print("[5] F-17 — Defer without a date: named reason, no silent block")
    # ------------------------------------------------------------------
    check("push to [date] is a required-input action",
          "push to [date]" in REQUIRED_INPUT_ACTION_IDS)
    check("reminder push [date] is a required-input action",
          "reminder push [date]" in REQUIRED_INPUT_ACTION_IDS)
    check("add email then send is a required-input action",
          "add email then send" in REQUIRED_INPUT_ACTION_IDS)
    check("required_input_thing names the date",
          required_input_thing("push to [date]") == "date")
    # The rendered triage widget: Defer option flagged required, wrapper
    # carries the baked reason element, footer carries the Apply-hold line.
    defer_opt = re.search(
        r'<option value="push to \[date\]"[^>]*>',
        html)
    check("Defer option rendered", defer_opt is not None)
    if defer_opt:
        check("Defer option carries data-input-required=1",
              'data-input-required="1"' in defer_opt.group(0))
        check("Defer option names the missing thing (date)",
              'data-input-thing="date"' in defer_opt.group(0))
    check("inline reason element baked into the wrapper",
          "Later… needs a date" in html)
    check("footer Apply-hold reason line present",
          'id="cr-apply-reason"' in html)
    check("JS validates before Apply (crValidate wired)",
          "function crValidate()" in html and "Apply is waiting on" in html)
    check("crApplyAll re-validates as a backstop",
          re.search(r'if\s*\(crValidate\(\)\.length\s*>\s*0\)', html) is not None)
    # Stripping the reason machinery must be rejected (a hand-built widget
    # with a required-input button and no feedback layer = F-17 reborn).
    try:
        validate_rendered_widget(html.replace('id="cr-apply-reason"', 'id="x"'))
        check("HTML with required inputs but no Apply-reason line is rejected", False)
    except WidgetFeedbackContractError:
        check("HTML with required inputs but no Apply-reason line is rejected", True)
    try:
        validate_rendered_widget(html.replace("cr-input-reason", "cr-x"))
        check("HTML with required inputs but no inline reason is rejected", False)
    except WidgetFeedbackContractError:
        check("HTML with required inputs but no inline reason is rejected", True)
    # add email then send renders its promised input (dead-toggle fix) when it
    # falls in a row's DROPDOWN tail. WG1-A D-A3: on a ≤4-option row every verb
    # is a button (bare-dispatch; apply-choices prompts for the address), so
    # this contract is exercised on a ≥5-option row where the recovery verb
    # stays a dropdown option carrying data-input-type="email-text".
    email_opt = re.search(
        r'<option value="add email then send"[^>]*>',
        _render([{ "n": 1, "name": "No address",
                   "actions": ["add email then send", "escalate to memo",
                               "add to my list", "snooze 3d", "skip"]}],
                source="commitments"))
    check("add email then send is no longer input-less",
          email_opt is not None
          and 'data-input-type="email-text"' in email_opt.group(0))
    check("email fixture rendered", isinstance(htmls[1], str))

    # ------------------------------------------------------------------
    print("[6] needs-confirm rows say WHY the verb set is reduced (F-59)")
    # ------------------------------------------------------------------
    check("reduced_verbs_reason renders as a visible note",
          "cr-verbset-note" in html and "owner is unconfirmed" in html)

    # ------------------------------------------------------------------
    print("[7] OUT2 — header bands render via the shared component fragment")
    # ------------------------------------------------------------------
    # Legacy counters + the OUT2 tiles key both emit the shared markup
    # (components.build_tile_band_html) on the main widget path.
    check("counters band rendered (legacy key, values verbatim)",
          '<div class="cr-counter-label">Open</div>'
          '<div class="cr-counter-value">3</div>' in html)
    check("tiles band rendered (OUT2 key, component-validated)",
          '<div class="cr-counter-label">Oldest open</div>'
          '<div class="cr-counter-value">47d</div>' in html)
    try:
        _render([{"n": 1, "name": "x", "actions": ["skip"]}],
                tiles=[{"label": "Empty", "value": "  "}])
        check("empty tile on the tiles key is refused (drop-empty rule)", False)
    except ValueError:
        check("empty tile on the tiles key is refused (drop-empty rule)", True)

    # ------------------------------------------------------------------
    print(f"\n=== Summary: {PASS} passed, {FAIL} failed ===")
    if FAIL:
        return 1
    print("OK — verb taxonomy + widget feedback battery ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
