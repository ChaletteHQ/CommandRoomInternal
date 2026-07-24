#!/usr/bin/env python3
"""T3.1 FB-13 — wire ids in data-* attributes must not refuse a clean card;
ids in VISIBLE text must still refuse.

The live 2026-07-16 morning-brief fire crashed on a leak-scanner violation
rendering the brain card: a reconcile REVIEW item whose commitment_id carried
the legacy shape `event_NNNN` tripped the internal-entity-ID pattern — from
`data-item-n`, the F2-sanctioned WIRE location, while every visible string on
the row was clean. The orchestrator fell back honestly (fail-closed worked);
the defect was scanner scope: Gate 2 scanned data-* attribute values as if
they were display text. Staff Meeting and commitment-triage share the same
queue/renderer and refused on the same row class.

Asserts:
  - a real-shaped reconcile review item with a legacy `event_NNNN`
    commitment id renders a clean brain card; the wire id stays BYTE-EXACT
    in data-item-n (apply-choices dispatch unbroken)
  - build_staff_meeting_view end-to-end on a fixture carrying such an item
    renders a validated widget (FB-20 re-point: this leg used to run through
    build_morning_brief_pack, but the brief is read-only now and renders no
    card at all — the coverage MOVED to the surface that still renders one
    rather than being dropped. The scanner-scope fix T3.1 shipped is
    unchanged and still load-bearing for staff-meeting / coach / triage.)
  - an id deliberately injected into DISPLAY text still refuses — entity
    ids (`event_NNN`) and the commitment/proposal shapes (`cmt_<ULID>`,
    `commitment_seq_N`, `bp_<hex>`) — the scanner was NOT weakened
  - a wire id as a row NAME still refuses (RV-5 visible-span check intact)
  - a wire-id-shaped sub_id renders NO visible label (data-sub-id still
    routes); a visible cr-sub-id span carrying a wire id refuses
  - (review F-1) the sub select's data-disp — the JS hold-message display
    handle — is the parent display number, never a suppressed wire id
  - (review F-2) re-cased ids (upper-cased section titles) still refuse:
    the T3.1 patterns are case-insensitive

G14: every fixture timestamp is computed relative to today. Placeholder
names only (Acme / Sam Sample).

House convention: non-zero exit = fail.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import surface_drivers as sd  # noqa: E402
from brain_proposals import build_card_view, select_confirm_card  # noqa: E402
from chat_output_renderer import (  # noqa: E402
    LeakDetectedError,
    render_chat_output_widget,
    validate_rendered_widget,
)

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(label + (f" — {detail}" if detail else ""))
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


def _iso(days_ago: float) -> str:
    return (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


# The live row's exact shape: a legacy event-shaped commitment id.
LEGACY_CID = "event_3486"


def make_workspace() -> Path:
    """A workspace whose ONLY open confirm-card item is a reconcile review
    proposal over a legacy `event_NNNN` commitment id — the FB-13 live row."""
    ws = Path(tempfile.mkdtemp(prefix="t31_ws_"))
    data_dir = ws / "_hq" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    events = [
        {"seq": 1, "ts": _iso(9), "type": "commitment",
         "source_skill": "meeting-notes",
         "data": {"id": LEGACY_CID, "title": "Send Sam Sample the draft",
                  "owner_id": "person:001", "kind": "promise"}},
        # the reconcile review row (within the 7d window, un-adjudicated)
        {"seq": 2, "ts": _iso(1), "type": "commitment_review_proposed",
         "source_skill": "reconcile-sent",
         "data": {"commitment_id": LEGACY_CID,
                  "proposed_resolution": "auto_resolve",
                  "match_score": 0.42,
                  "evidence": 'matched your sent message "Re: the draft"',
                  "ambiguous": True}},
    ]
    with open(data_dir / "events.jsonl", "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    (data_dir / "entities.json").write_text(json.dumps({
        "persons": [{"id": "person:001", "canonical_name": "Sam Sample",
                     "is_primary_user": True}],
        "orgs": [], "threads": [],
    }), encoding="utf-8")
    return ws


def _cru_item(cid: str, evidence: str) -> dict:
    """A card item shaped exactly like _adapt_commitment_reviews' output."""
    return {
        "id": f"cru:{cid}", "source_family": "commitment_review",
        "kind": "commitment_review", "shape": "hygiene", "tier": "confirm",
        "fingerprint": f"cru:{cid}", "evidence": evidence,
        "action_tuples": [], "render_line": "", "opened_at": _iso(1),
        "expires_at": "", "detector": "reconcile-sent", "seq": 2,
        "commitment_id": cid, "match_score": 0.42,
    }


def main() -> int:
    # ---- the FB-13 live row renders clean; wire id byte-exact ---------------
    ws = make_workspace()
    # FB-20: the brief renders no card, so the live FB-13 row now reaches the
    # user via the staff meeting — the same queue, the same builder, the same
    # renderer, which is why the T3.1 defect class is unchanged.
    card = select_confirm_card(ws, "staff-meeting")
    check("review item reaches the confirm card",
          any(i["id"] == f"cru:{LEGACY_CID}" for i in card["items"]),
          f"{[i['id'] for i in card['items']]}")
    view = build_card_view(card["items"], surface="staff-meeting",
                           header="Needs your eyes")
    try:
        html = render_chat_output_widget(view, wrapper="fragment")
    except LeakDetectedError as e:
        html = ""
        check("legacy event_NNN wire id renders clean", False, str(e)[:200])
    if html:
        check("legacy event_NNN wire id renders clean", True)
        check("wire id byte-exact in data-item-n (dispatch unbroken)",
              f'data-item-n="cru:{LEGACY_CID}"' in html)
        check("id never appears as visible row text",
              f">{LEGACY_CID}" not in html and f">cru:{LEGACY_CID}" not in html)

    # ---- end-to-end over the same fixture, on the surface that still cards --
    # (FB-20 re-point — was build_morning_brief_pack; the brief is read-only.)
    sm_view = sd.build_staff_meeting_view(ws)
    try:
        sm_html = render_chat_output_widget(sm_view, wrapper="fragment")
    except LeakDetectedError as e:
        sm_html = ""
        check("staff-meeting renders the FB-13 row end-to-end", False,
              str(e)[:200])
    check("staff-meeting renders the FB-13 row end-to-end", bool(sm_html))
    check("wire id byte-exact in the staff-meeting render (dispatch unbroken)",
          f'data-item-n="cru:{LEGACY_CID}"' in sm_html if sm_html else False)

    # ---- FB-20: the brief pack renders NO card over this same fixture -------
    pack = sd.build_morning_brief_pack(ws, mode="scheduled")
    check("the brief pack carries no widget transport for this row (FB-20)",
          "transport" not in pack and "confirm_card" not in pack,
          f"{sorted(pack.keys())}")

    # ---- injected ids in DISPLAY text: the scanner is NOT weakened, but the
    # RESPONSE changed under WG1-A D-A6 (M ruling 2026-07-20, big-test row
    # 10b/10c). A single row whose VISIBLE content carries a wire id no longer
    # takes the whole page down — it QUARANTINES to an honest placeholder, so
    # the offending id STILL never reaches the user (the invariant T3.1 guards)
    # while the healthy rows render. Assert: no page-raise, the id is ABSENT
    # from the rendered page, and the placeholder is present + names the defect.
    for label, bad_id, evidence in [
        ("entity id in visible context", LEGACY_CID,
         f"matched your sent message ({LEGACY_CID})"),
        ("cmt_ ULID in visible context", "cmt_01TESTFIXTUREULID000000AA",
         "matched cmt_01TESTFIXTUREULID000000AA to your sent mail"),
        ("commitment_seq_N in visible context", "commitment_seq_1533",
         "closed commitment_seq_1533 from your sent mail"),
        ("bp_ proposal id in visible context", "bp_d27b6b5244bb",
         "confirmed via bp_d27b6b5244bb"),
    ]:
        bad = build_card_view([_cru_item("cmt_clean_row", evidence)],
                              surface="morning-brief", header="Needs your eyes")
        try:
            bad_html = render_chat_output_widget(bad, wrapper="fragment")
        except LeakDetectedError as e:
            bad_html = ""
            check(f"{label} quarantines (no page-block)", False,
                  f"page-raised instead of quarantining: {str(e)[:120]}")
        if bad_html:
            check(f"{label} quarantines (no page-block)", True)
            check(f"{label}: the wire id is absent from the page",
                  bad_id not in bad_html, "id leaked despite quarantine")
            check(f"{label}: an honest placeholder replaces the row",
                  "1 row withheld" in bad_html)

    # ---- RV-5 backstop intact: a wire id as the row NAME never reaches the
    # user. Through the renderer it now QUARANTINES (placeholder, id absent);
    # the visible-span check in validate_rendered_widget still hard-raises on
    # hand-mangled HTML (covered below, line ~220), so the backstop is intact.
    named = build_card_view([_cru_item("cmt_clean_row", "matched a send")],
                            surface="morning-brief", header="Needs your eyes")
    named["sections"][0]["items"][0]["name"] = "commitment_seq_1533"
    try:
        named_html = render_chat_output_widget(named, wrapper="fragment")
    except LeakDetectedError:
        named_html = ""
        check("wire id as row name quarantines (RV-5)", True)  # a raise is also safe
    if named_html:
        check("wire id as row name quarantines (RV-5)",
              "commitment_seq_1533" not in named_html and "1 row withheld" in named_html,
              "wire-id name neither quarantined nor absent")

    # ---- sub-items: wire-id sub_id gets no visible label; span check covers
    sub_view = {
        "source_skill": "cr-brain",
        "header": "Needs your eyes — 1 open",
        "sections": [{"title": "HYGIENE (1)", "items": [{
            "n": 1, "display_n": 1, "name": "Send Sam Sample the draft",
            "context_tag": "matched a send",
            "actions": ["resolved"],
            # WG1-A D-A3: a ≥5-verb sub-item keeps its dropdown (the F-1
            # data-disp check below is a select-attribute assertion; a ≤4
            # sub-item would render buttons with no select).
            "sub_items": [{"id": LEGACY_CID,
                           "summary": "probably handled — undo if not",
                           "actions": ["resolved", "push to [date]", "drop",
                                       "not mine", "make task", "skip"]}],
        }]}],
    }
    sub_html = render_chat_output_widget(sub_view, wrapper="fragment")
    check("wire-id sub_id renders no visible label",
          '<span class="cr-sub-id">' not in sub_html)
    check("data-sub-id still routes",
          f'data-sub-id="{LEGACY_CID}"' in sub_html)
    # hand-mangled HTML with a visible wire-id sub label must refuse
    mangled = sub_html + (f'<div class="cr-sub-item"><span class="cr-sub-id">'
                          f"<strong>cmt_01TESTFIXTUREULID000000AA.</strong>"
                          f"</span></div>")
    try:
        validate_rendered_widget(mangled)
        check("visible wire id in cr-sub-id span refuses", False,
              "validated without refusing")
    except LeakDetectedError:
        check("visible wire id in cr-sub-id span refuses", True)

    # ---- T3.1 review F-1: the sub select's data-disp is the PARENT display
    # number, never a suppressed wire id — data-disp feeds the JS hold
    # message ("Apply is waiting on item N — …"), visible text composed
    # client-side that the render-time scan can never reach. data-n keeps
    # the wire id (dispatch).
    check("sub select data-disp is display-safe (parent display_n)",
          f'data-disp="{LEGACY_CID}"' not in sub_html
          and 'data-disp="1"' in sub_html)
    check("sub select data-n keeps the wire id (dispatch)",
          f'data-n="{LEGACY_CID}"' in sub_html)

    # ---- T3.1 review F-2: re-cased ids still refuse. Section titles are
    # .upper()'d at render (labels get first-letter capitalization) — a
    # case-sensitive pattern lets CMT_/BP_ ids straight through on the very
    # surfaces the T3.1 patterns were added to protect.
    for label, title in [
        ("cmt_ id in an uppercased section title refuses",
         "hygiene cmt_01TESTFIXTUREULID000000AA"),
        ("bp_ id in an uppercased section title refuses",
         "review bp_d27b6b5244bb"),
        ("commitment_seq id in an uppercased section title refuses",
         "review commitment_seq_1533"),
    ]:
        recased = build_card_view([_cru_item("cmt_clean_row", "matched a send")],
                                  surface="morning-brief",
                                  header="Needs your eyes")
        recased["sections"][0]["title"] = title
        try:
            render_chat_output_widget(recased, wrapper="fragment")
            check(label, False, "rendered without refusing")
        except LeakDetectedError:
            check(label, True)

    print(f"\n{checks} checks, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
