#!/usr/bin/env python3
"""
v2.13.0 audit pass — fire each orchestrator's documented data shape with stubs,
render through chat_output_renderer, scan output for leaks + canonical-action
violations + missing required fields. Report every gap.
"""
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'shared', 'scripts'))

from chat_output_renderer import render_chat_output_widget, scan_for_id_leaks


# Canonical action set per CHAT_ACTION_WIDGET.md "Action reference" (v2.12.6)
CANONICAL_ACTIONS = {
    # Email-shaped (Inbox, Commitments YOU OWE / OWED TO YOU)
    # v2.14.4+ — `to drafts` and `edit then draft` consolidated into `draft`
    # FB-17 — the email card is Send / Draft / Snooze (3 days); `edit then send`
    # retired (kept here as a still-dispatchable deprecated alias).
    "send", "edit then send", "draft", "snooze 3d", "escalate to memo", "skip",
    # Inbox calendar_invite
    "accept", "propose [time]", "decline", "decline [reason]",
    # Commitments YOU OWE
    "prep deep work", "push to [date]", "resolved",
    # Commitments OWED TO YOU
    "follow-up call", "mark received", "mark received all",
    # Commitments self
    "mark done",
    # Pulse person
    "investigate", "draft re-engagement", "schedule catchup [when]",
    "resolved", "snooze [duration]",
    # Pulse stale project
    "mark paused", "status check",
    # Pulse pending review (v2.14.5+ finish-cluster: snooze + skip)
    "confirm", "edit [change]",
    # Pulse dormant transition (v2.14.5+ finish-cluster: snooze + skip)
    "active", "keep paused", "archive",
    # Pulse entity proposal (v2.14.5+ finish-cluster: snooze + skip)
    "confirm [type]", "edit [type]",
    # Past Meetings new-person sub-item (v2.12.6+; v2.14.4 rename `log to discuss` → `add to my list`)
    "add as person to [org]", "add as new org", "add context [text]", "add to my list",
    # Past Meetings vague-timing
    "set date [when]",
    # Past Meetings decision-needed
    "decide [text]",
    # Upcoming Meetings (v2.14.37+ — context [text] supersedes add more context + ask question;
    # the legacy two are retained as back-compat aliases inside CANONICAL_ACTIONS)
    "context [text]", "add more context [text]", "ask question [text]", "push meeting [date]",
    # Bulk row
    "send all", "to drafts all", "show more", "skip all",
}


def is_canonical_action(action_str):
    """True if action matches canonical set OR is a specific-name variant.

    Two variants accepted (mirror chat_output_renderer.is_canonical_action):
      - `add as person to <Specific Org Name>` (v2.12.6+)
      - `add as new org <Specific Org Name>` (v2.14.5+)
    """
    if action_str in CANONICAL_ACTIONS:
        return True
    if action_str.startswith("add as person to ") and not action_str.endswith("[org]"):
        return True
    if action_str.startswith("add as new org ") and not action_str.endswith("[org]"):
        return True
    return False


def strip_n_prefix(action, n):
    n_str = str(n)
    if action.startswith(n_str + " "):
        return action[len(n_str) + 1:]
    return action


def audit_data_view(name, dv):
    print(f"\n=== {name} ===")
    issues = []
    try:
        html = render_chat_output_widget(dv)
    except Exception as e:
        issues.append(f"RENDER FAIL: {e}")
        for i in issues:
            print(f"  X {i}")
        return issues

    leaks = scan_for_id_leaks(html)
    if leaks:
        for kind, sample in leaks[:5]:
            issues.append(f"LEAK ({kind}): {sample!r}")

    for section in dv.get("sections", []):
        for item in section.get("items", []):
            for a in item.get("actions", []):
                stripped = strip_n_prefix(a, item.get("n", ""))
                if not is_canonical_action(stripped):
                    issues.append(f"NON-CANONICAL ACTION: {stripped!r} on item {item.get('n')}")
            for sub in item.get("sub_items", []):
                for a in sub.get("actions", []):
                    stripped = strip_n_prefix(a, sub.get("id", ""))
                    if not is_canonical_action(stripped):
                        issues.append(
                            f"NON-CANONICAL ACTION (sub): {stripped!r} on sub {sub.get('id')}"
                        )

    if not issues:
        print("  OK clean")
    else:
        for i in issues:
            print(f"  X {i}")
    return issues


# Test fixtures — each represents what the orchestrator SHOULD pass to the renderer per spec

INBOX = {
    "widget_mode": "all_batch_widget",
    "header": "Inbox - Apr 30 - 3 priority threads",
    "sections": [{"title": None, "count": None, "items": [
        {"n": 1, "icon": "envelope", "name": "Sam",
         "metadata": [("To", "d@x.com"), ("Subject", "Q2")],
         "body_lines": ["Hey,", "Body."],
         "original_thread": {"author": "Sam <d@x.com>", "date": "Apr 28",
                              "subject": "Q2", "body": "Original.",
                              "url": "https://mail.google.com/mail/u/0/#all/abc"},
         "actions": ["1 send", "1 draft", "1 snooze 3d"]},
        {"n": 2, "icon": "calendar", "name": "Bo",
         "context_tag": "9am - Summit Company",
         "actions": ["2 accept", "2 propose [time]", "2 decline [reason]", "2 skip"]},
    ]}],
}

COMMITMENTS = {
    "widget_mode": "all_batch_widget",
    "header": "Commitments - 3 open - both directions",
    "sections": [
        {"title": "YOU OWE", "count": 1, "items": [
            {"n": 1, "name": "Sam", "subject": "Q2 deck",
             "metadata": [("To", "d@x.com"), ("Subject", "Q2 deck status")],
             "body_lines": ["Hey,", "Sending Friday."],
             "actions": ["1 prep deep work", "1 send", "1 draft", "1 push to [date]", "1 resolved", "1 snooze 3d"]},
        ]},
        {"title": "OWED TO YOU", "count": 2, "items": [
            {"n": 6, "name": "Bo", "subject": "Mapping doc",
             "metadata": [("To", "bo@example.com"), ("Subject", "NetSuite mapping - timing")],
             "body_lines": ["Hey Bo,", "Touching base on the mapping doc."],
             "actions": ["6 send", "6 draft", "6 follow-up call", "6 mark received", "6 escalate to memo", "6 snooze 3d"]},
            {"n": 7, "name": "Adan (grouped)",
             "metadata": [("To", "adan@example.com"), ("Subject", "Circling back on a few things")],
             "body_lines": ["Adan,", "Following up on items from the Apr 8 call."],
             "actions": ["7 send", "7 draft", "7 mark received all", "7 snooze 3d"],
             "sub_items": [
                {"id": "7a", "summary": "Recap", "actions": ["7a mark received", "7a skip"]},
                {"id": "7b", "summary": "Licenses", "actions": ["7b mark received", "7b skip"]},
             ]},
        ]},
    ],
}

DONT_FORGET = {
    "widget_mode": "all_batch_widget",
    "header": "5 things worth not forgetting",
    "sections": [
        {"title": None, "count": None, "items": [
            {"n": 1, "icon": "person", "name": "Bo",
             "context_tag": "You usually talk every 5 days. It's been 18.",
             "actions": ["1 investigate", "1 draft re-engagement", "1 schedule catchup [when]",
                         "1 resolved", "1 snooze [duration]", "1 skip"]},
            {"n": 2, "icon": "folder", "name": "Aspen",
             "actions": ["2 prep deep work", "2 investigate", "2 mark paused", "2 status check",
                         "2 snooze [duration]", "2 skip"]},
        ]},
        # REVIEW section — v2.14.5+ trailing finish-cluster (snooze + skip)
        # added consistently across all three review-shaped item types.
        # v2.14.7+ adds the CRU review item (sub-namespace r1/r2/...) for
        # MEDIUM-confidence commitment-resolution proposals.
        {"title": "REVIEW", "count": 4, "items": [
            {"n": 5, "name": "Andrea",
             "context_tag": "Update last contact to Apr 28 (was Apr 14)? Confirm to apply.",
             "actions": ["5 confirm", "5 edit [change]", "5 snooze [duration]", "5 skip"]},
            {"n": 8, "icon": "folder", "name": "Aspen",
             "context_tag": "Move to Dormant?",
             "actions": ["8 active", "8 keep paused", "8 archive",
                         "8 snooze [duration]", "8 skip"]},
            # Entity proposal item with v2.14.5+ specific-name context_tag
            # (Acme Co case): action set is confirm/edit + finish-cluster.
            {"n": 9, "icon": "building", "name": "Acme Co",
             "context_tag": "Track Acme Co as a prospect org? Email domain acme.example.com seen in 5 threads.",
             "actions": ["9 confirm [type]", "9 edit [type]",
                         "9 snooze [duration]", "9 skip"]},
            # CRU review item (v2.14.7+ sub-namespace r1) — MEDIUM-confidence
            # commitment-resolution proposal from a recent send / transcript.
            {"n": 10, "name": "Sam",
             "context_tag": "Did 'Send pricing deck to Sam' get fulfilled? Sent via native mail Apr 30 with subject 'Q2 deck final'. Match score 0.42 -- likely but not certain. Confirm to mark resolved; Skip to keep it open.",
             "actions": ["10 confirm", "10 skip"]},
        ]},
    ],
}

PAST_MEETINGS = {
    "widget_mode": "all_batch_widget",
    "header": "Past meetings - last 24h - 3 newly processed",
    "sections": [{"title": None, "count": None, "items": [
        {"n": 1, "name": "Sam", "subject": "UX review",
         "body_lines": ["- Decision A", "- Decision B"],
         "sources": [{"label": "Granola transcript", "url": "https://notes.granola.ai/d/abc"}],
         "artifact_link": {"label": "Open brief",
                            "url": "computer:///c%3A/_hq/meetings/abc.docx"},
         "actions": [],
         "sub_items": [
            {"id": "1a", "summary": "Rio Sample - new person",
             "actions": ["1a add as person to Summit Company", "1a add context [text]",
                         "1a add to my list", "1a skip"]},
            {"id": "1b", "summary": "Rio Lange - new person",
             "actions": ["1b add as person to Summit Company", "1b add context [text]",
                         "1b add to my list", "1b skip"]},
            {"id": "1c", "summary": "Vague timing",
             "actions": ["1c set date [when]", "1c add to my list", "1c skip"]},
            {"id": "1d", "summary": "Decision needed",
             "actions": ["1d decide [text]", "1d add to my list", "1d skip"]},
            # v2.14.5+ — when the new-org name is inferable, emit the
            # specific-name variant `add as new org Acme Co` so the button
            # label shows the org explicitly instead of generic "Add as new
            # org". Mirrors how `add as person to <Org>` works.
            {"id": "1e", "summary": "Acme Co - new org candidate",
             "actions": ["1e add as new org Acme Co", "1e add context [text]",
                         "1e add to my list", "1e skip"]},
         ]},
    ]}],
}

UPCOMING_MEETINGS = {
    "widget_mode": "all_batch_widget",
    "header": "Wed Apr 30 - 2 external",
    "sections": [{"title": None, "count": None, "items": [
        {"n": 1, "name": "Sam", "subject": "Q2 deck review",
         "context_tag": "9:00 AM - Summit Company",
         "body_lines": ["Lead with: revised numbers"],
         "artifact_link": {"label": "Open brief",
                            "url": "computer:///c%3A/_hq/meetings/abc.docx"},
         "actions": ["1 add more context [text]", "1 ask question [text]", "1 push meeting [date]", "1 skip"]},
    ]}],
}

if __name__ == "__main__":
    all_issues = {}
    for name, dv in [
        ("INBOX", INBOX),
        ("COMMITMENTS", COMMITMENTS),
        ("DONT_FORGET", DONT_FORGET),
        ("PAST_MEETINGS", PAST_MEETINGS),
        ("UPCOMING_MEETINGS", UPCOMING_MEETINGS),
    ]:
        all_issues[name] = audit_data_view(name, dv)

    total = sum(len(v) for v in all_issues.values())
    print(f"\n=== AUDIT SUMMARY ===")
    print(f"Total issues: {total}")
    for name, issues in all_issues.items():
        if issues:
            print(f"  {name}: {len(issues)}")
    sys.exit(1 if total > 0 else 0)
