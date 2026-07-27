# `.githooks/` — Pre-commit privacy guard

This folder contains git hooks that ship with the Command Room plugin. The most important one is `pre-commit`, which runs the structural privacy guard (and three sibling structural guards) BEFORE any commit lands. Per v3.12.2 (2026-05-20), this is how the plugin enforces Rule 26 of `shared/CONTRACT.md` (no real customer/partner names in plugin source) at write-time instead of months later.

## How to install (one-time per clone)

On a fresh clone of `ChaletteHQ/cr1`, run this once at the repo root:

```bash
git config core.hooksPath command-room/.githooks
```

That tells git to look in this folder instead of `.git/hooks/`. The setting is per-clone (stored in `.git/config`), so it doesn't propagate automatically — every machine that edits the plugin source needs to run it once.

## What the hook does

On every `git commit`, the hook runs:

- `tests/run_no_real_customer_names_test.py` — Rule 26 enforcement (no real customer/partner names)
- `tests/run_no_hardcoded_drive_test.py` — Rule 25 enforcement (no hardcoded Drive paths)
- `tests/run_no_md_deliverables_test.py` — Rule 27 enforcement (no `.md` deliverables)
- `tests/run_no_retired_skills_test.py` — no references to retired skills

If any of those fail, the commit is blocked. The error output points at the specific files + lines that need fixing.

## What changed in v3.12.2 to make this matter

Pre-v3.12.2, the privacy guard used a hand-curated DENYLIST of ~15 specific known-bad names. Every new release shipped new examples by sessions that didn't know the full denylist; names slipped through; manual audits found leaks weeks later. v3.12.2 converted it to an ALLOWLIST with a common-first-name dictionary as the detection mechanism — any first name in the dictionary that isn't on the 11-approved-placeholder list now fails the guard. The pre-commit hook moves enforcement to write-time so leaks get caught the moment they're authored, not weeks later.

## Bypass (NOT recommended)

If for some reason a commit needs to bypass the hook (e.g., adding a known-good name to the test file itself, where it must appear literally to be scanned for), use:

```bash
git commit --no-verify -m "..."
```

But genuinely consider whether the bypass is necessary. The structural guards have caught real leaks every time they've run. A bypass means accepting the risk that you're shipping a leak by accident.

## See also

Everything in this section lives in the canonical plugin repo (`ChaletteHQ/cr1`) and is **not** part of the client fan-out — `tests/` is excluded by the payload contract (PROMOTEFENCE) and the privacy policy is excluded as a name-scanner-exempt file (EXEMPTFENCE). If you are reading this in a client repo, these paths are absent by design; `shared/CONTRACT.md` Rule 26 is the shipped statement of the rule.

- `shared/CONTRACT.md` Rule 26 — the contract (ships; carries the approved placeholder roster inline)
- the privacy policy under `references/` — the full rule, canonical repo only
- `tests/run_no_real_customer_names_test.py` — the enforcement, canonical repo only (three layers: denylist for known sticky strings, email-domain allowlist, name-allowlist)
