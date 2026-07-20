#!/usr/bin/env python3
"""G13 — widget-transport instruction-layer guard (EW2+T, F-15).

The F-15 lesson (integration-2026-07): `widget_transport.render_and_persist`
was code-complete and tested, yet referenced by ZERO skill texts — so at fire
time the runtime obeyed the stale byte-relay instructions and hit the
transmission wall. Code-layer green, instruction-layer absent (the same class
as the description-only-routing gotcha). This guard closes the test-design
gap by checking the INSTRUCTION layer:

  1. Every .md under skills/ (plus shared/CHAT_ACTION_WIDGET.md) that
     references `render_chat_output_widget` AT ALL must also reference
     `render_and_persist` — a surface that names the renderer but not the
     transport is the F-15 shape. (Widened from a both-tokens predicate by
     the ew2t second-eyes review: two scheduled-fire orchestrators mandated
     the renderer without ever saying `show_widget` and slipped through.)
  2. Retired-delivery ban (T2, Bug #67): no skill/shared .md may carry the
     literal `transport["file_uri"]` / `transport['file_uri']` token — that
     was the "hand the file URI to show_widget" delivery, which is IMPOSSIBLE
     on the live runtime (show_widget has no file_uri param). The token only
     ever meant that delivery, so banning it catches any reversion without
     false-positiving the § Transport doc that explains why file_uri fails.
     The audit-file `file_uri` key still lives in widget_transport.py (a .py,
     not scanned here).
  3. Delivery-contract + no-silent-fallback pins (T2): the canonical
     § Transport doc must name the `widget_code` carrier, pagination (`page`),
     and the no-silent-fallback rule; STOP_CONTRACT carries the same STOP
     language. These are the F2-rework anchors.
  4. EW2+T instruction pins — cheap regression anchors for the sibling
     fixes shipped in that pass (F-07/F-08, F-12, F-02, F-10).

Allowlist: command-room-onboarding renders its Step-1 setup widget from a
static HTML file by documented design (small fixed surface, deliberate
renderer bypass with rationale in its SKILL.md) — it mentions the renderer
only to explain the bypass, so the transport reference is not required there.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Files where BOTH tokens appear but the transport reference is deliberately
# not required. Keep this list SHORT and justified.
TRANSPORT_REF_ALLOWLIST = {
    # Documented renderer bypass: static step1_widget_v2.html, small fixed
    # surface; mentions render_chat_output_widget only to explain what it
    # deliberately does NOT use.
    "skills/command-room-onboarding/SKILL.md",
}

# The retired delivery, in BOTH the code token form and the prose form. The
# ew2t sweep left three prose "file URI" references in core contract files
# (STOP_CONTRACT, EMAIL_DRAFT_PROTOCOL) that the token-only ban missed — the
# exact instruction-layer-drift class this guard exists to catch. Widened to
# flag any surviving "file URI" delivery phrasing near show_widget/transport.
_RETIRED_FILE_URI_RE = re.compile(
    r"""transport\[["']file_uri["']\]"""
    r"""|show_widget\s*\(file\s*uri\)"""
    r"""|file\s*uri\s*(?:->|→|points at|to)\s*show_widget"""
    r"""|file\s*uri\s*points at""",
    re.IGNORECASE,
)

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(f"{label}" + (f" — {detail}" if detail else ""))
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def main() -> int:
    # ---- Check 1: transport reference in every both-token file -------------
    md_files = sorted(ROOT.glob("skills/**/*.md")) + [ROOT / "shared" / "CHAT_ACTION_WIDGET.md"]
    for p in md_files:
        text = p.read_text(encoding="utf-8")
        if "render_chat_output_widget" in text:
            if rel(p) in TRANSPORT_REF_ALLOWLIST:
                continue
            check(
                f"transport reference present in {rel(p)}",
                "render_and_persist" in text,
                "file names the renderer but never names "
                "widget_transport.render_and_persist (the F-15 shape; "
                "single-token predicate per the ew2t review)",
            )

    # ---- Check 2: retired file_uri-delivery ban (T2, Bug #67) ---------------
    for p in sorted(ROOT.glob("skills/**/*.md")) + sorted(ROOT.glob("shared/*.md")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if _RETIRED_FILE_URI_RE.search(line):
                check(
                    f"no retired transport[\"file_uri\"] delivery token in {rel(p)}:{i}",
                    False,
                    line.strip()[:120],
                )

    # ---- Check 3: delivery-contract + no-silent-fallback pins (T2) ----------
    caw = (ROOT / "shared" / "CHAT_ACTION_WIDGET.md").read_text(encoding="utf-8")
    check(
        "§ Transport names the widget_code carrier",
        "widget_code" in caw and "## Transport" in caw,
    )
    check(
        "§ Transport mandates paginate-by-design",
        "paginate" in caw.lower() and "page=N" in caw,
    )
    check(
        "§ Transport carries the no-silent-fallback rule (FS-08)",
        "No silent fallback" in caw or "no-silent-fallback" in caw.lower(),
    )
    stopc = (ROOT / "shared" / "STOP_CONTRACT.md").read_text(encoding="utf-8")
    check(
        "STOP_CONTRACT delivers via transport[\"html\"] as widget_code",
        'transport["html"]' in stopc and "widget_code" in stopc,
    )

    # ---- Check 4: EW2+T instruction pins ------------------------------------
    ew = (ROOT / "skills/email-writer/SKILL.md").read_text(encoding="utf-8")
    check(
        "email-writer chained section bans address inference (F-08)",
        "Address inference is BANNED" in ew,
    )
    check(
        "email-writer chained section carries the no-queue rule (F-07)",
        "MUST NOT create any connector draft" in ew,
    )
    ac = (ROOT / "skills/apply-choices/SKILL.md").read_text(encoding="utf-8")
    check(
        "apply-choices references the drafted-provenance builder (F-12)",
        "build_email_drafted_provenance" in ac,
    )
    bridge = (ROOT / "skills/command-room-update-bridge/SKILL.md").read_text(encoding="utf-8")
    check(
        "update-bridge Phase 4.5 runs the id-leak scan over migration copy (F-02)",
        "scan_for_id_leaks" in bridge,
    )
    wm = (ROOT / "skills/workspace-manager/SKILL.md").read_text(encoding="utf-8")
    new_deal_lines = [l for l in wm.splitlines() if "new deal [deal name]" in l or ("new deal" in l and "counterparty" in l)]
    check(
        "workspace-manager new-deal suggestion names the counterparty (F-10)",
        any("counterparty" in l for l in new_deal_lines),
        "the new-prospect suggestion template must state the org is the "
        "counterparty, never the user's own org",
    )

    # ---- Check 4: LB1 instruction pins (SPEC LB1 D12 — the F-15 class,
    # mechanized for the Living Brain helpers: every shared script LB1
    # shipped must be NAMED by the skill texts that use it, or it is
    # invisible at runtime). Each pin = (file, required tokens).
    LB1_PINS = [
        # FB-20: the morning brief is READ-ONLY — it renders no card, so it
        # names no card helpers. `select_confirm_card` / `render_and_persist`
        # were REMOVED from this pin deliberately (not lost): a text naming
        # them would be teaching a retired behavior. It now owes the prose
        # helpers that replaced them. The card pins live on at `coach`, which
        # still renders one.
        ("skills/morning-briefing/SKILL.md",
         ["money_prose_lines", "load_open_proposals", "changes_since"]),
        ("skills/command-room-coach/SKILL.md",
         ["select_confirm_card", "changes_since"]),
        ("skills/weekly-recap/SKILL.md",
         ["changes_since", "card_health_counts"]),
        ("skills/system-health/SKILL.md",
         ["load_open_proposals", "changes_since",
          "compute_relationship_moves", "render_and_persist"]),
        ("skills/cleanup/SKILL.md",
         ["expire_stale", "card_health_counts"]),
        ("skills/apply-choices/SKILL.md",
         ["resolve_proposal", "brain_undo",
          # T2.2 (FS-17/FS-11b): the person-row add path is a code helper —
          # invisible unless the dispatch text names it.
          "auto_add_person",
          # T2.2 (FS-18a/b): the audit builder + the shared deal-coverage
          # predicate — both invisible unless the dispatch text names them.
          "build_apply_choices_applied_event", "org_deal_coverage"]),
        ("skills/enable-command-room-schedules/references/orchestrator-staff-meeting.md",
         ["load_open_proposals", "changes_since",
          "compute_relationship_moves", "render_and_persist"]),
        ("shared/scripts/deal_signal_detector.py",
         ["brain_proposals.propose"]),
    ]
    for path, tokens in LB1_PINS:
        text = (ROOT / path).read_text(encoding="utf-8")
        for token in tokens:
            check(
                f"LB1 instruction pin: {path} names {token}",
                token in text,
                "an LB1 helper this surface depends on is not named by its "
                "text — the F-15 instruction-layer gap",
            )

    # ---- Check 4b: PID1 instruction pins (SPEC PID1 — code helpers the
    # identity model depends on are invisible unless the skill texts that
    # must call them name them; the same F-15 class as Check 4).
    PID1_PINS = [
        # D5: the capture-side annotation builder — unnamed speakers must
        # route here, never through a worked-around person proposal.
        ("skills/meeting-notes/SKILL.md",
         ["build_unidentified_attendee_event"]),
        ("skills/enable-command-room-schedules/references/orchestrator-past-meetings.md",
         ["build_unidentified_attendee_event"]),
        # D3/D4: the cluster fan-out + the two merge-propose dispatches.
        ("skills/apply-choices/SKILL.md",
         ["cluster_seqs", "merge_person_into", "add_person_alias",
          "proposal_fingerprint"]),
        # Step 10: the pointer counts CLUSTERS via the shared projection.
        ("skills/morning-briefing/SKILL.md",
         ["count_person_rows"]),
        # §0-4: the annotations' one count line.
        ("skills/enable-command-room-schedules/references/orchestrator-staff-meeting.md",
         ["count_open_annotations"]),
    ]
    for path, tokens in PID1_PINS:
        text = (ROOT / path).read_text(encoding="utf-8")
        for token in tokens:
            check(
                f"PID1 instruction pin: {path} names {token}",
                token in text,
                "a PID1 helper this surface depends on is not named by its "
                "text — the F-15 instruction-layer gap",
            )

    # ---- Check 5: T2.2 one-command driver pins (scope 1e) -------------------
    # The drivers exist to kill the ~30-command prep latency AND the RV-3
    # double-render; a driver referenced by zero skill texts is the F-15
    # invisible-helper shape all over again. Each surface must name the
    # driver, the stdout marker contract, and the idempotent-single-call rule.
    DRIVER_PINS = [
        ("skills/commitment-triage/SKILL.md",
         ["surface_drivers.py", "CR-WIDGET-HTML-BEGIN", "ONCE per page per fire"]),
        # FB-7: both staff-meeting paths (scheduled orchestrator + manual
        # system-health Step 5) must name --fired-via — the flag that makes
        # the driver write the fire receipt inside the render call. A path
        # text that drops it regresses to the render-without-receipt fire.
        ("skills/enable-command-room-schedules/references/orchestrator-staff-meeting.md",
         ["surface_drivers.py", "CR-WIDGET-HTML-BEGIN", "ONCE per page per fire",
          "--fired-via", "CR-RECEIPT"]),
        ("skills/system-health/SKILL.md",
         ["surface_drivers.py", "--fired-via manual"]),
        ("shared/CHAT_ACTION_WIDGET.md",
         ["surface_drivers.py", "CR-WIDGET-HTML-BEGIN"]),
        # t3 FB-9: the morning brief's substrate half is a one-command pack.
        # Both path texts must name the driver symbol + the pack marker —
        # a text that drops them regresses to the per-step MUSTs that the
        # live post-update fire skipped (brain card + alarm line).
        # T3.2 FB-18 pinned the relay adjacency (banner marker + "IMMEDIATE
        # next action" + "BEFORE any prose"). FB-20 RETIRED the brief's widget
        # outright, so those three tokens are deliberately GONE from this pin:
        # there is no relay to be adjacent to, and a text still carrying them
        # would send a literal step-follower hunting for bytes the driver no
        # longer emits. What replaces them is stronger — the driver-last
        # ordering (which still protects the PROSE blocks), the explicit
        # no-widget ban, and the pack's two new blocks. "logging is not
        # posting" survives: it outlived the widget it was written for.
        # The inverse pin (a widget from this driver = RED) is enforced
        # functionally in tests/run_t32_brief_relay_test.py.
        ("skills/enable-command-room-schedules/references/orchestrator-morning-brief.md",
         ["surface_drivers.py morning-brief", "CR-BRIEF-PACK",
          "alarm_lines", "changed.lines", "watchdog_line",
          "money_lines", "queue_pointer",
          "NO WIDGET. AT ALL.", "logging is not posting"]),
        ("skills/morning-briefing/SKILL.md",
         ["build_morning_brief_pack", "surface_drivers.py morning-brief",
          "money_prose_lines"]),
    ]
    for path, tokens in DRIVER_PINS:
        text = (ROOT / path).read_text(encoding="utf-8")
        for token in tokens:
            check(
                f"T2.2 driver pin: {path} names {token!r}",
                token in text,
                "the one-command driver contract is not named by this "
                "surface's text — the F-15 instruction-layer gap",
            )

    # ---- Check 6: T2.2 bridge nits (FS-16 + FS-01 residual) -----------------
    bridge2 = (ROOT / "skills/command-room-update-bridge/SKILL.md").read_text(encoding="utf-8")
    check(
        "FS-16: staff-meeting proposal block mandates the quoted "
        "registered-set readback",
        "registered-set readback" in bridge2
        and "feature newness is not user absence" in bridge2,
    )
    check(
        "FS-01 residual: the same-version state-gated enumeration names the "
        "rebind heads-up",
        "orchestrator-rebind heads-up line" in bridge2.split(
            "up-to-date early-exit")[1].split("Stop here.")[0],
    )

    if failures:
        print(f"\nG13 FAIL — {len(failures)} of {checks} checks failed")
        return 1
    print(f"G13 widget-transport instruction guard: {checks} checks OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
