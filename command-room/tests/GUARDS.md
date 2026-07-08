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
jargon/leak scans, no-legacy-taskId, no-md-deliverables.

Adding a guard: name the test `run_guard_*` (auto-classified into the guard
tier), add a row here, and cite the audit finding it encodes.
| G11 | Description budget + routing visibility: every skill description ≤980 chars (1,024 spec cap headroom — strict runtimes DROP over-cap skills), no angle brackets, no version tags, at least one tested trigger stem inside the first 250 chars (listing-truncation window), catalog total ratchets down from 48k | run_guard_g11_description_budget_test.py | v4.5.1 (Evan incident) |
