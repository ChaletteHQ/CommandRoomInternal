#!/usr/bin/env python3
"""SPEC A8 — email-writer output regression exercise (runtime tier).

email-writer produces a Gmail Draft, NOT a file (CONTRACT Rule 27). Its deterministic
part is the commitment_refs[]/decision_refs[] derivation for a recipient. The golden is
the normalized JSON of {recipient_id, commitment_refs, decision_refs, subject}; the
side-effect is email_drafted then email_sent with draft_event_seq linkage.
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
RECIPIENT = "person_002"  # Rio Sample


def main() -> int:
    from cru_match import load_events_defensively, load_open_commitments, event_references_person
    from next_seq import next_seq
    from atomic_write import atomic_append_jsonl

    ok, fail, section, finish = lib.make_recorder()
    ws = lib.copy_fixture()
    events_path = ws / "_hq" / "data" / "events.jsonl"
    events, _ = load_events_defensively(str(events_path))

    section("derivation — commitment_refs + decision_refs for the recipient")
    opens = load_open_commitments(str(events_path))
    commitment_refs = sorted(c["seq"] for c in opens if event_references_person(c, RECIPIENT))
    # decision_refs: decisions on the recipient's primary project (project_001).
    decision_refs = sorted(e["seq"] for e in events
                           if e.get("type") == "decision" and e.get("primary_thread_id") == "project_001")
    if commitment_refs == [4]:
        ok("commitment_refs derived (Rio owns c1 @ seq 4)", str(commitment_refs))
    else:
        fail("commitment_refs", str(commitment_refs))
    if decision_refs == [8, 12, 14, 20]:
        ok("decision_refs derived (project_001 decisions)", str(decision_refs))
    else:
        fail("decision_refs", str(decision_refs))

    section("golden — normalized JSON skeleton")
    skeleton = {"recipient_id": RECIPIENT, "commitment_refs": commitment_refs,
                "decision_refs": decision_refs, "subject": "Sourcing spec + pricing sheet"}
    matched, diff = lib.compare_golden("email_writer", json.dumps(skeleton, indent=2, sort_keys=True))
    ok("draft skeleton matches golden") if matched else fail("golden match", diff[:600])

    section("substrate side-effect (email_drafted -> email_sent linkage)")
    seq_d = next_seq(str(events_path))
    drafted = {"seq": seq_d, "ts": NOW, "type": "email_drafted", "source_skill": "email-writer",
               "primary_thread_id": "project_001", "person_ids": [RECIPIENT],
               "data": {"recipient": "rio@example.com", "topic": "sourcing spec + pricing sheet",
                        "draft_event_seq": 0, "commitment_refs": commitment_refs,
                        "decision_refs": decision_refs}}
    atomic_append_jsonl(events_path, [drafted])
    v = lib.validate_event(json.loads(events_path.read_text(encoding="utf-8").splitlines()[-1]))
    ok("email_drafted validates") if not v else fail("email_drafted valid", str(v))

    seq_s = next_seq(str(events_path))
    sent = {"seq": seq_s, "ts": NOW, "type": "email_sent", "source_skill": "email-writer",
            "primary_thread_id": "project_001", "person_ids": [RECIPIENT],
            "data": {"recipient": "rio@example.com", "topic": "sourcing spec + pricing sheet",
                     "gmail_message_id": "<probe-1>", "draft_event_seq": seq_d}}
    atomic_append_jsonl(events_path, [sent])
    appended = json.loads(events_path.read_text(encoding="utf-8").splitlines()[-1])
    v = lib.validate_event(appended)
    ok("email_sent validates") if not v else fail("email_sent valid", str(v))
    if appended["data"]["draft_event_seq"] == seq_d:
        ok("email_sent.draft_event_seq links back to the email_drafted seq")
    else:
        fail("draft_event_seq linkage", str(appended["data"]["draft_event_seq"]))

    return finish("email_writer_exercise")


if __name__ == "__main__":
    raise SystemExit(main())
