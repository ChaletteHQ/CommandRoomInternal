# Ship-Gate Guard Registry (G1–G10)

Phase 4 2026-07-02 — the merged guard list from both 2026-07-01 audits, ONE
registry. Every guard runs in the battery's **guard tier** (`python
tests/run_all.py --tier guard`); the battery is invoked by cr-test and
ship-readiness-gate, so a red guard blocks the ship verdict. G10 is a process
rule, not code — it lives in CONTRACT Rule 29 and the release checklist.

| # | Guard | Enforced by | Notes |
|---|-------|-------------|-------|
| G1 | `<plugin-root>` placeholder purge + discovery-preamble requirement for relative `sys.path` snippets | `run_guard_g1_plugin_root_test.py` | Rule 22 calls the grep a RELEASE BLOCKER |
| G2 | Retired-token grep (DECISIONS.md, write-shaped bare DECISION_LOG.md, BACKLOG.md, URL-encoded computer:///, plugin-source-v2, commandroom1, email_drafts/, Orgs Map) with scoped allowances | `run_guard_g2_retired_tokens_test.py` | update-bridge's commandroom1 is a deliberate stale-marker (P0.8) |
| G3 | Dead-reference gate: references/ citations resolve against the tree; `{SKILL_DIR}/../..` workspace paths banned | `run_guard_g3_dead_refs_test.py` | the intel-intake four-releases-of-phantom-refs class |
| G4 | Prescribed `"actions": [...]` arrays validate against `CANONICAL_ACTIONS` | `run_guard_g4_widget_verbs_test.py` | the P1.1 unexecutable-widget class |
| G5 | Banned-word lint over customer-verbatim blockquotes (PL.1 list + PL.5 nouns) | `run_pl_banned_words_test.py` (landed PR 3) | do NOT re-derive the list — extend it there |
| G6 | Advertised-trigger validation: every "Say X" in verbatim copy routes against the real trigger registry | `run_guard_g6_advertised_triggers_test.py` | the dead-command class — most trust-destroying bug family found |
| G7 | Hardcoded task/chat/question counts in customer-verbatim copy | `run_guard_g7_hardcoded_counts_test.py` | render from schedule_config / the widget's real count |
| G8 | Bootloader gate-vs-template composition | `run_bootloader_size_gate_test.py` (landed Phase 3) | composes from the real template, runs the Phase 3.5 checks |
| G9 | Customer-data scrub (real names via first-name-dictionary detection, approved-placeholder allowlist) | `run_no_real_customer_names_test.py` (pre-existing) | placeholder roster: references/PRIVACY_POLICY.md |
| G10 | **Process rule:** when a skill's core model changes, sweep its Writer Contract / Gotchas / What-It-Doesn't-Do / output templates IN THE SAME COMMIT, and DELETE superseded sentences — never annotate them | CONTRACT Rule 29 + reviewer discipline | both audits' #1 root cause: stale sediment next to its replacement |

Companion mechanical suites that predate this registry and stay load-bearing:
trigger collisions (`run_trigger_test.py` + `triggers.yaml`, incl. `expected:
none` hijack guards), source-of-truth convergence, event contract, voice/
jargon/leak scans, no-legacy-taskId, no-md-deliverables, deck chokepoint
(`run_deck_writer_test.py` — DECK_GRAMMAR caps, pre-save leak scan, brand
resolution; SPEC OUT6, row queued by the out6 review behind date-guard).

Adding a guard: name the test `run_guard_*` (auto-classified into the guard
tier), add a row here, and cite the audit finding it encodes.
| G11 | Description budget + routing visibility: every skill description ≤980 chars (1,024 spec cap headroom — strict runtimes DROP over-cap skills), no angle brackets, no version tags, at least one tested trigger stem inside the first 250 chars (listing-truncation window), catalog total ratchets down from 53k (48k→53k raised per M's sign-off 2026-07-15, g11-budget) | run_guard_g11_description_budget_test.py | v4.5.1 (Evan incident) |
| G12 | No build/handoff artifacts in the shipped plugin tree: no `BUILD_REPORT_*` / `HANDOFF_*` / `FABLE_REVIEW_*` files, no `handoffs/` directory — promote fans the whole tree to every client repo | run_guard_no_build_reports_test.py | HYG1 second-eyes review 2026-07-13 (EW1's build report committed under command-room/handoffs/) |
| G13 | Widget-transport instruction layer: every skill text that mandates `render_chat_output_widget` + `show_widget` must reference `widget_transport.render_and_persist`; show_widget byte-relay language banned; EW2+T instruction pins (chained email MUSTs, drafted-provenance builder, bridge leak scan, counterparty suggestion) | run_guard_g13_widget_transport_refs_test.py | EW2+T 2026-07-14, F-15 (code-complete transport referenced by zero skill texts — the instruction-layer-gap class) |
| G14 | Date-bomb gate: no hardcoded today-or-future ISO date literal in tests/ — fixture dates are computed relative to today, hardcoded past dates (historic DATA events) and >= +50y sentinels are free, every deliberate future literal carries a same-line `# DATE_GUARD_OK: <reason>` (JSON fixtures via the in-guard ALLOWLIST). Window-aging extension (2026-07-19): a PAST date literal governed by a TTL/expiry key in the same record and still inside that window (`0 <= age <= ttl`) is a bomb too — the FS-11 mirror class that ages OUT of the window and flips; fix with a computed `_ago(N)` date or annotate | run_guard_g14_date_bomb_test.py | MC3 time bomb (commit 2a12674) — hardcoded 'future' due fed to a real-clock status stamp went RED across the entire frozen v4.6.3 fleet 2026-07-15; FS-11 window-aging blind spot flagged in BUILD_REPORT_fb20 (M ruled yes) |
| G15 | Remote-marketplace validity: no byte > 0x7F in any git-INDEX path (scan `git ls-files -z` bytes, not the checkout); plugin.json + marketplace.json entry descriptions <= 500 chars; every committed SKILL.md description <= 1024 chars; no angle brackets in any description. Mirrors Anthropic's remote validator, which only fires at customer install/update | run_guard_marketplace_validity_test.py | 2026-07-09 em-dash dir broke installs fleet-wide (zip_invalid_path_characters); companion to run_guard_hooks_config_test.py (hooks-schema half of the same "local passes, server rejects" class) |
| G16 | Deliverable gate parity (SPEC OUT5): no save-time gate may fire on the .docx path and not the premium-HTML path (or vice versa) — one shared stack (brief_gates.run_pre_save_gates), enumeration of both backends' recorded gate_ran sets, structural ban on inline gate imports in either backend, and behavioral fixtures firing every gate against both | run_guard_g16_gate_parity_test.py | GATE1 (runtime-unreachable gates via a second render path); OUT5 adds the second backend this pins |
