#!/usr/bin/env python3
"""Exemplar library test (SPEC OUT8) — resolution, coverage, scrub gate,
learning loop, precedence, and the exemplar-token leak check.

Covers:
  1. Resolution order: workspace > seed > None; never raises; no caching.
  2. Coverage: a seed exemplar EXISTS for every brief_writer.STANDARD_KINDS
     member (M's "all kinds" ruling made mechanical), and the pinned
     build-time kind list matches the live set — divergence fails with
     add-the-seed instructions (the dated-TODO contract in
     shared/exemplars/README.md).
  3. Seed hygiene: every seed passes the shared leak scan with ZERO findings,
     carries the skeleton sentinel, and declares a non-empty token list.
  4. Precedence (contract > exemplar > default): a workspace exemplar that
     omits the exec header does NOT produce a header-less doc — make_brief
     still raises. There is no exemplar code path into the chokepoint.
  5. Learning loop: correction append shape + dedup, >=3 same-direction
     threshold proposes (<3 doesn't), cooldown fingerprints suppress
     (round-tripped through proposal_ledger), confirmed update rotates
     versions, the scrub gate strips a poisoned name before write and
     REFUSES unscrubable leak content.
  6. Exemplar-token leak check: a marker planted in a fixture exemplar never
     appears in rendered output unflagged — text and .docx scans both catch
     it; clean output scans clean.

House convention: check()/_failures harness (run_brand_test.py posture);
missing deps fail LOUDLY (exit 2), never SKIP-but-PASS.
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import exemplars  # noqa: E402
from exemplars import (  # noqa: E402
    ExemplarScrubError,
    append_structural_correction,
    exemplar_marker_tokens,
    get_exemplar,
    group_correction_patterns,
    load_structural_corrections,
    promote_workspace_exemplar,
    propose_exemplar_updates,
    residual_name_candidates,
    scan_docx_for_exemplar_tokens,
    scan_text_for_exemplar_tokens,
    scrub_exemplar_text,
    seed_kinds,
)

try:
    from brief_writer import STANDARD_KINDS, make_brief
except ImportError as exc:  # pragma: no cover
    print(f"ERROR: cannot import brief_writer ({exc}) — this suite requires "
          f"it (same fail-loud class as the G11 SKIP-but-PASS).")
    sys.exit(2)
try:
    from docx_leak_scanner import scan_text_for_leaks
except ImportError as exc:  # pragma: no cover
    print(f"ERROR: cannot import docx_leak_scanner ({exc}).")
    sys.exit(2)
import proposal_ledger  # noqa: E402

_failures = []


def check(name, cond, extra=""):
    status = "OK  " if cond else "FAIL"
    print(f"{status} {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        _failures.append(name)


# The STANDARD_KINDS set at build time (2026-07-16). TODO (dated 2026-07-16):
# when the Wave-3 kinds merge (OUT6 deck/kpi kinds, OUT7 kpi_scorecard), add
# a seed under shared/exemplars/<kind>/ AND extend this pin — the equality
# check below is DESIGNED to fail the moment STANDARD_KINDS grows, so the
# "all kinds" ruling stays mechanically enforced.
PINNED_KINDS = frozenset({
    "call_prep", "memo", "board_pack", "weekly_recap", "one_pager",
    "followup_pack", "decision_memo", "dormant_scan", "contract_review",
    "automation_scan", "stress_test",
})


def main():
    # ------------------------------------------------------------------ 1
    # Resolution: seed tier
    ex = get_exemplar("board_pack")
    check("seed resolves with no workspace", ex is not None
          and ex["source"] == "seed" and "exemplar_1.md" in ex["path"]
          and ex["text"].strip() != "")
    check("unknown kind resolves None", get_exemplar("no_such_kind") is None)
    check("never raises on garbage workspace_root",
          get_exemplar("memo", 12345) is not None)  # falls through to seed
    check("never raises on path-traversal kind",
          get_exemplar("../../etc", ".") is None)

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        # Workspace tier wins (deep preference, not merge)
        ws_dir = ws / "_hq" / "exemplars" / "board_pack"
        ws_dir.mkdir(parents=True)
        (ws_dir / "exemplar_1.md").write_text(
            "<!-- exemplar-skeleton test -->\n<!-- tokens: Acme Co -->\n"
            "# workspace override\n", encoding="utf-8")
        got = get_exemplar("board_pack", ws)
        check("workspace exemplar beats seed", got is not None
              and got["source"] == "workspace"
              and "workspace override" in got["text"])
        # Delete → clean fallback to seed (acceptance #3), fresh read (no cache)
        (ws_dir / "exemplar_1.md").unlink()
        got = get_exemplar("board_pack", ws)
        check("deleted workspace exemplar falls back to seed",
              got is not None and got["source"] == "seed")
        # Half-finished rotation: only exemplar_2 present still resolves
        (ws_dir / "exemplar_2.md").write_text("# rotated only\n",
                                              encoding="utf-8")
        got = get_exemplar("board_pack", ws)
        check("exemplar_2 honored when _1 absent (workspace)",
              got is not None and got["source"] == "workspace"
              and "rotated only" in got["text"])

    # ------------------------------------------------------------------ 2
    # Coverage — the "all kinds" ruling, mechanical.
    seeded = seed_kinds()
    missing = set(STANDARD_KINDS) - seeded
    check("every STANDARD_KINDS member has a seed exemplar", not missing,
          extra=f"missing seeds: {sorted(missing)} — add "
                f"shared/exemplars/<kind>/exemplar_1.md")
    drift = set(STANDARD_KINDS) ^ PINNED_KINDS
    check("pinned kind list matches live STANDARD_KINDS", not drift,
          extra=(f"STANDARD_KINDS changed (delta: {sorted(drift)}). Per the "
                 f"2026-07-16 TODO: add/remove the seed under "  # DATE_GUARD_OK: prose date labeling the TODO inside a diagnostic message — never compared to any clock
                 f"shared/exemplars/ and update PINNED_KINDS in this test."))
    extra_seeds = seeded - set(STANDARD_KINDS)
    check("no orphan seed dirs for non-standard kinds", not extra_seeds,
          extra=f"orphans: {sorted(extra_seeds)}")

    # ------------------------------------------------------------------ 3
    # Seed hygiene: leak scan zero findings + sentinel + tokens.
    for kind in sorted(seeded):
        ex = get_exemplar(kind)
        findings = scan_text_for_leaks(ex["text"])
        check(f"seed {kind} leak-scan clean", not findings,
              extra=str(findings[:2]))
        check(f"seed {kind} carries skeleton sentinel",
              "exemplar-skeleton" in ex["text"])
        check(f"seed {kind} declares tokens",
              len(exemplar_marker_tokens(ex["text"])) >= 1)

    # ------------------------------------------------------------------ 4
    # Precedence: contract beats exemplar. A workspace exemplar with NO
    # header guidance cannot produce a header-less STANDARD_KIND doc —
    # make_brief raises regardless (no exemplar code path into enforcement).
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        memo_dir = ws / "_hq" / "exemplars" / "memo"
        memo_dir.mkdir(parents=True)
        (memo_dir / "exemplar_1.md").write_text(
            "<!-- exemplar-skeleton fixture -->\n"
            "<!-- tokens: ZZEXEMPLARMARKERZZ -->\n"
            "# memo skeleton WITHOUT any exec header slot\n"
            "## Context first\nZZEXEMPLARMARKERZZ sample line\n",
            encoding="utf-8")
        fixture = get_exemplar("memo", ws)
        check("precedence fixture resolves from workspace",
              fixture is not None and fixture["source"] == "workspace")
        out = ws / "t.docx"
        sections = [
            {"heading": "Decision", "body": "Take the pilot."},
            {"heading": "Context", "body": "Background paragraph."},
        ]
        raised = False
        try:
            make_brief(out, brief_kind="memo", title="T", subtitle="s", sections=sections, contract="off")
        except ValueError:
            raised = True
        check("STANDARD_KIND without exec_header still raises "
              "(exemplar cannot waive the contract)", raised and not out.exists())
        make_brief(out, brief_kind="memo", title="T", subtitle="s", sections=sections, contract="off",
                   exec_header={"verdict": "Take the pilot.",
                                "changed": "Nothing material.",
                                "decide": "The pilot.",
                                "needs": "Nothing from you."})
        check("with exec_header the render succeeds", out.exists())

        # -------------------------------------------------------------- 6
        # Exemplar-token leak check on the rendered .docx: the fixture's
        # marker is NOT in this doc -> clean; a doc that embeds it -> caught.
        clean = scan_docx_for_exemplar_tokens(out, fixture["text"])
        check("clean render has no exemplar tokens", clean == [])
        out2 = ws / "t2.docx"
        make_brief(out2, brief_kind="memo", title="T", subtitle="s",
                   contract="off",
                   sections=[{"heading": "Decision",
                              "body": "ZZEXEMPLARMARKERZZ leaked here."}],
                   exec_header={"verdict": "V.", "changed": "Nothing.",
                                "decide": "Nothing.", "needs": "Nothing."})
        caught = scan_docx_for_exemplar_tokens(out2, fixture["text"])
        check("planted marker in rendered output is caught",
              len(caught) == 1 and caught[0]["token"] == "ZZEXEMPLARMARKERZZ")
        check("text-scan variant catches the same marker",
              scan_text_for_exemplar_tokens(
                  "body ZZEXEMPLARMARKERZZ tail", fixture["text"]) != [])
        check("text-scan clean on clean text",
              scan_text_for_exemplar_tokens(
                  "an ordinary sentence", fixture["text"]) == [])

    # ------------------------------------------------------------------ 5
    # Learning loop.
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        row = dict(kind="board_pack", direction="tiles_first",
                   section="KPIs", detail="moved tile band up",
                   doc="Pack_A.docx", source="reconcile_sent")
        check("correction append returns True",
              append_structural_correction(ws, **row))
        check("duplicate correction dedups to False",
              not append_structural_correction(ws, **row))
        rows = load_structural_corrections(ws)
        check("correction row shape", len(rows) == 1 and rows[0]["kind"] ==
              "board_pack" and rows[0]["direction"] == "tiles_first"
              and rows[0]["section"] == "KPIs"
              and rows[0]["source"] == "reconcile_sent"
              and rows[0].get("timestamp"))
        check("append never raises on unwritable root",
              append_structural_correction(
                  Path(tmp) / "t.docx", kind="memo", direction="shorten")
              in (True, False))

        # Threshold: 2 same-direction -> no proposal; 3 -> proposes.
        append_structural_correction(ws, kind="board_pack",
                                     direction="tiles_first", section="KPIs",
                                     detail="again", doc="Pack_B.docx")
        two = propose_exemplar_updates(load_structural_corrections(ws))
        check("2 same-direction corrections do NOT propose", two == [])
        append_structural_correction(ws, kind="board_pack",
                                     direction="tiles_first", section="KPIs",
                                     detail="third time", doc="Pack_C.docx")
        three = propose_exemplar_updates(load_structural_corrections(ws))
        check("3 same-direction corrections propose exactly one",
              len(three) == 1 and three[0]["kind"] == "board_pack"
              and three[0]["count"] == 3)
        p = three[0]
        check("proposal is confirm-first shape (fingerprint + plain)",
              p.get("fingerprint") and isinstance(p.get("plain"), str)
              and p["plain"].endswith("?")
              and "board pack" in p["plain"])
        check("plain line carries no file tokens",
              ".jsonl" not in p["plain"] and "_hq" not in p["plain"])

        # Decline -> proposal_ledger cooldown suppresses the re-propose.
        proposal_ledger.append_decision(
            ws, pass_name=exemplars.PASS_NAME, fingerprint=p["fingerprint"],
            user_action="declined", summary="test decline")
        cooldowns = proposal_ledger.active_cooldowns(
            ws, exemplars.PASS_NAME,
            now_iso=rows[0]["timestamp"])
        check("declined fingerprint is in active cooldown",
              p["fingerprint"] in cooldowns)
        check("cooldown suppresses re-propose",
              propose_exemplar_updates(load_structural_corrections(ws),
                                       cooldown_fingerprints=cooldowns) == [])

        # Cap: 4 distinct >=3 patterns -> at most 3 proposals, strongest first.
        for i, d in enumerate(["drop_section", "shorten", "prose_first",
                               "move_section_up"]):
            for j in range(3 + i):
                append_structural_correction(
                    ws, kind="memo", direction=d, section=f"S{i}",
                    detail=f"edit {j}", doc=f"D{i}{j}.docx")
        capped = propose_exemplar_updates(load_structural_corrections(ws, "memo"))
        check("proposal cap holds at 3, strongest evidence first",
              len(capped) == 3 and capped[0]["count"] >= capped[-1]["count"])

        # Scrub gate: poisoned entity name is stripped before write.
        entities = {"workspace": {"user_name": "Mercer Whitlock"},
                    "entities": {"orgs": [
                        {"id": "org_1", "canonical_name": "Meridian Ventures",
                         "aliases": ["Meridian"]}],
                        "persons": [
                        {"id": "p_1", "canonical_name": "Mercer Whitlock"}]}}
        # The poisoned fixture needs a NON-example email domain so the scrub
        # gate has something to rewrite — but tests/ is a G9-scanned surface,
        # so the literal can't sit in this file. Assemble it at runtime (the
        # docx_leak_scanner test uses the same split-token technique for its
        # deliberately-dirty fixtures).
        dirty_email = "mercer@" + "meridian" + "vc.exam" + "ple.io"
        poisoned = ("# Pack skeleton\nMeridian Ventures leads; "
                    f"contact Mercer Whitlock at {dirty_email}\n")
        scrubbed, repl = scrub_exemplar_text(poisoned, entities)
        check("scrub replaces org + person + email",
              "Meridian" not in scrubbed and "Mercer" not in scrubbed
              and "meridianvc" not in scrubbed and len(repl) >= 3)
        result = promote_workspace_exemplar(ws, "board_pack", poisoned,
                                            entities=entities)
        promoted = Path(result["path"]).read_text(encoding="utf-8")
        check("promote writes scrubbed exemplar_1",
              result["path"].endswith("exemplar_1.md")
              and "Meridian" not in promoted and "Mercer" not in promoted)
        check("promote adds sentinel + tokens header",
              "exemplar-skeleton" in promoted
              and exemplar_marker_tokens(promoted))
        check("first promote does not rotate", result["rotated"] is False)
        # Confirmed update rotates: second promote moves v1 -> exemplar_2.
        result2 = promote_workspace_exemplar(
            ws, "board_pack", "# Pack skeleton v2\nplain structure\n",
            entities=entities)
        v2 = (ws / "_hq" / "exemplars" / "board_pack" / "exemplar_2.md")
        check("second promote rotates previous to exemplar_2",
              result2["rotated"] is True and v2.exists()
              and "Pack skeleton" in v2.read_text(encoding="utf-8"))
        check("resolution now returns the new exemplar_1",
              "v2" in get_exemplar("board_pack", ws)["text"])

        # Unscrubable leak content is REFUSED (fail-closed write gate).
        refused = False
        try:
            promote_workspace_exemplar(ws, "memo",
                                       "# skeleton\nsee events.jsonl for it\n",
                                       entities=entities)
        except ExemplarScrubError as e:
            refused = bool(e.findings)
        check("leak-pattern exemplar text is refused, with findings", refused)
        check("refused write left no memo exemplar",
              get_exemplar("memo", ws)["source"] == "seed")

        # Review F-1 (2026-07-16): a name in NEITHER entities.json NOR the
        # static leak vocabulary must not silently survive the scrub gate.
        # Layer 3 = residual_name_candidates + the confirmed_residuals
        # contract on promote.
        untracked = ("# Pack skeleton\n"
                     "Meridian Ventures closed the Hollowbrook Capital "
                     "acquisition; Tobias Renwick signs Friday. "
                     "Deal size $2.3M. Sam Sample owns the follow-up.\n"
                     "## Meeting Details\n")
        cand = residual_name_candidates(untracked)
        check("residual detector flags untracked org/person/figure",
              "Hollowbrook Capital" in cand and "Tobias Renwick" in cand
              and "$2.3M" in cand)
        check("residual detector excludes approved placeholders",
              "Sam Sample" not in cand and "Acme Co" not in cand)
        check("tokens-declared strings are not residual candidates",
              "$480K" not in residual_name_candidates(
                  "<!-- tokens: $480K -->\ncosts $480K to run"))
        refused_names = []
        try:
            promote_workspace_exemplar(ws, "one_pager", untracked,
                                       entities=entities)
        except ExemplarScrubError as e:
            refused_names = [f["match"] for f in e.findings
                             if f.get("name") == "residual_candidate"]
        check("promote refuses unconfirmed residual names, naming them",
              "Hollowbrook Capital" in refused_names
              and "Tobias Renwick" in refused_names)
        check("refused residual write left no one_pager exemplar",
              get_exemplar("one_pager", ws)["source"] == "seed")
        confirmed = promote_workspace_exemplar(
            ws, "one_pager", untracked, entities=entities,
            confirmed_residuals=residual_name_candidates(untracked))
        check("user-confirmed residuals unblock the promote",
              confirmed["path"].endswith("exemplar_1.md")
              and confirmed["residuals"])

        # The docx token scan leans on two PRIVATE docx_leak_scanner helpers
        # (deliberate reuse of the run-boundary-collapse extraction). Pin the
        # coupling explicitly so a scanner refactor fails HERE with a name,
        # not as a silent [] from the fail-open scan.
        import docx_leak_scanner as _dls
        check("docx_leak_scanner private extraction helpers exist "
              "(scan_docx_for_exemplar_tokens depends on them — if renamed, "
              "update exemplars.scan_docx_for_exemplar_tokens)",
              hasattr(_dls, "_read_document_xml")
              and hasattr(_dls, "_docx_paragraph_text"))

        # group_correction_patterns is pure + tolerant.
        check("grouping tolerates malformed rows",
              group_correction_patterns([{"kind": "memo"}, "junk",
                                         {"direction": "shorten"}]) == {})

    print()
    if _failures:
        print(f"FAILURES ({len(_failures)}): {_failures}")
        return 1
    print("ALL exemplars tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
