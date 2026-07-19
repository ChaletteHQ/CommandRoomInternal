# Exemplar Library — shipped seeds (SPEC OUT8)

One structural gold standard per STANDARD_KIND. Each `<kind>/exemplar_1.md` is
a skeleton with annotations — section order, where tiles/tables sit, target
length, header treatment — the design contract as a concrete instance, not
prose rules. Composers load their kind's exemplar via
`shared/scripts/exemplars.py` `get_exemplar(kind, workspace_root)` and anchor
STRUCTURE on it (never facts). Workspace-learned overrides live at
`_hq/exemplars/<kind>/` in the client workspace and take deep preference over
these seeds. Precedence: **contract beats exemplar beats default** — see
`shared/EXECUTIVE_OUTPUT_STANDARD.md`.

## Rules for every file in this tree

- **Synthetic content only.** These files promote to every client repo — a
  real name here is a fleet-wide leak. Person names come from the real-names
  guard's approved list (Sam Sample, Quinn Stone, …); orgs are Acme Co /
  Northstar Partners; emails use example.com. The coverage test runs the
  shared leak scan over every seed with zero-findings required.
- **Structure, never facts.** No number, name, or claim in an exemplar may
  flow into a deliverable. Every file declares its placeholder strings in a
  `<!-- tokens: a | b | c -->` comment; composers scan rendered output for
  those tokens after save (`exemplars.scan_docx_for_exemplar_tokens`).
- **One current version.** `exemplar_1.md` is canonical; `exemplar_2.md` (in
  workspace trees) is the rotated previous version. No merging of halves.

## Coverage

Seeded kinds (the full `brief_writer.STANDARD_KINDS` set at build time,
2026-07-16): call_prep, memo, board_pack, weekly_recap, one_pager,
followup_pack, decision_memo, dormant_scan, contract_review, automation_scan,
stress_test.

TODO (dated 2026-07-16): when the Wave-3 kinds land on main (OUT6 deck/kpi
kinds, OUT7 `kpi_scorecard`), add their seeds here — M's ruling is ALL
standard kinds, and `tests/run_exemplars_test.py` enforces the union
mechanically (it fails, with instructions, the moment `STANDARD_KINDS` grows
past this list).
