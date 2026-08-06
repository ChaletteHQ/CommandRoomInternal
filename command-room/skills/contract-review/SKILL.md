---
name: contract-review
surfaces: both
description: "Review a contract or NDA — extract key terms, compare against your standard terms, flag deviations green/yellow/red, and suggest redlines. Fires on: 'review this contract', 'review this NDA', 'redline this contract / NDA / MSA', 'contract review', 'check this contract', 'analyze this agreement', 'compare this contract to my standard', 'flag risks in this contract'. Counterparty- and history-aware: repeated carve-out pushes from the same counterparty get noted as a pattern; every review is logged with parties and deviation classes. Does NOT fire on 'write a contract' (out of scope — Command Room reviews, never drafts contracts), legal-advice questions (out of scope), or e-signature sending (the connected signing tool). Deviation taxonomy and standard-terms contract: Routing section in the body."
---

## Skill Boundary (v2.1)

- **Use contract-review for:** reviewing a contract / NDA / MSA the user has received. Extracts terms, compares against your standard, flags deviations.
- **NOT for drafting** contracts from scratch.
- **NOT for signing** — use the DocuSign / Adobe Sign MCP for signature workflow.
- **NOT for legal advice** — surfaces deviations and questions; user takes them to counsel.

## Writer Contract (v3.8.0+ — substrate-native)

Before writing to any workspace file, read `shared/WORKSPACE_API.md`.

**Primary writer for:**
- `_hq/contracts/ContractReview_[Counterparty]_[YYYY-MM-DD].docx` — the structured review with key terms, flag list, suggested redlines, questions to ask. Per CONTRACT Rule 27 (no .md deliverables) the output is `.docx`.

**Appends to:**
- `_hq/data/events.jsonl` — event type `contract_reviewed` with `{counterparty_org_id, contract_title, term_summary_hash, deviation_count_by_color: {green, yellow, red}, artifact_path, source_file_path}`. The `term_summary_hash` allows detecting identical contracts across submissions — computed by the exact recipe in "The `term_summary_hash` recipe" below (deterministic: two runs over the same contract MUST produce the same hash, or the Phase 2 dedup never matches). The `deviation_count_by_color` field is canonical-shape substrate that future consumers (insight-generator pattern detection, cleanup, board-pack-assembler) can aggregate over a period — no consumer reads it yet as of v3.12.0, but the event is shaped correctly for when one is built.

  **Append through the locked writer (SPEC GATE1 / A1):** write this event via `atomic_append_jsonl(events_path, [event], holder="contract-review")`, NOT a hand-rolled `next_seq`+`open('a')` or a raw `>>`. The helper reserves the seq and writes inside the cross-process writer lock so a concurrent append can't lose the event or duplicate a seq. Omit `seq`/`ts` — auto-stamped. See `shared/WORKSPACE_API.md` → Append Protocol §3.

**Reads from:**
- The contract file (PDF or docx). Uses PDF MCP or python-docx for parsing.
- `_hq/contracts/standard-terms.md` — your standard terms reference. If absent on first use, prompt the user to create one (or skip the comparison pass).
- `_hq/data/entities.json` — counterparty org record. Pulls prior contract history, current relationship tier.
- `_hq/data/events.jsonl` — prior `contract_reviewed` events involving the same counterparty (so the review can say "they've pushed for this carve-out in 2 prior drafts") OR involving the same deviation class (so the review can say "third uncapped indemnification contract this quarter"). **Read via the org-scoped reader, never a raw load** (PGUARD2 — the review artifact and redlines travel counterparty-adjacent): `from events_io import load_events_org_scoped; org_events, skipped = load_events_org_scoped(workspace_root)`, then filter `type == "contract_reviewed"`. The reader applies the account-scope mask and drops personal-lane rows by design.
- `_hq/data/events.jsonl` — `type == "decision"` events about contract terms (e.g., "we won't accept uncapped indemnification" — surfaces as binding constraint in the review). Same org-scoped read as above — one `load_events_org_scoped` call serves both.

**Conflict boundary:** sole writer of `contract_reviewed` events. Does NOT write to entities.json (counterparty org enrichment is people-crm's domain — surface suggestions to that skill instead).

---

# contract-review

CEOs review contracts at a rate that doesn't justify dedicated counsel for every read but high enough that drift in personal standards creates real exposure. This skill makes the comparison substrate-aware: not just "here are the key terms" (commodity AI can do that) but "here's how this deviates from YOUR standard + what this counterparty has pushed for before + which red flags violate decisions you've already made."

## What It Does

For a contract input (PDF or docx attached, or path referenced):

1. Parse the document — extract parties, term length, payment terms, IP ownership, termination, indemnification, governing law, and other key clauses.
2. Load `_hq/contracts/standard-terms.md` — your standard.
3. Load counterparty history from entities.json + prior contract_reviewed events.
4. Compare each key term against your standard. Classify as green (matches), yellow (deviates but acceptable), red (violates a binding constraint).
5. Surface suggested redlines for yellow + red items.
6. Generate "questions to ask before signing" — usually 2-4 questions probing the counterparty's actual concern behind the deviation.
7. Write the .docx review + append the contract_reviewed event.

## How to Use

```
"review this contract"   (attached file)
"review this NDA"
"redline this MSA"
"check this contract for risks"
"compare this contract to my standard"
"analyze this contract from Acme Co"
```

Trigger can reference a file path explicitly:
```
"review the contract at ~/Downloads/AcmeCo_MSA_v3.pdf"
```

## How It Works

### Phase 0 — First-use bootstrap

If `_hq/contracts/standard-terms.md` doesn't exist:
- Surface a brief explainer: "I review contracts against your standard terms — but I don't have yours on file yet. Want to set them up now (about 5 min), or should I just review this one and skip that comparison?"

**The 6-question wizard (run once, ~5 min):**
1. Preferred term length for client engagements? (e.g., "12 months")
2. Indemnification cap? (e.g., "12 months of fees")
3. Standard payment terms? (e.g., "net 30")
4. Termination notice required? (e.g., "30 days")
5. IP ownership default? (e.g., "work product transfers on payment")
6. Governing law? (e.g., "Texas")

Write the answers to `_hq/contracts/standard-terms.md` as a YAML block (one key per answer) plus a one-paragraph narrative summary.

### Settings verbs (SPEC OUT2 §5 — aliases onto this wizard, NOT a second store)

The standard FRP1 verbs map onto the existing standard-terms file — storage unchanged, no
`skill_config` JSON for this skill, no migration:

| CEO says | Behavior |
|---|---|
| "tune contract-review" / "update my standard terms" | re-run the 6-question wizard with the CURRENT `standard-terms.md` answers pre-filled → rewrite the file. This is the "separate explicit edit" the DOES NOT section requires — never mid-review. |
| "show contract-review settings" / "show my standard terms" | render the current `standard-terms.md` answers in plain English, read-only. If the file doesn't exist yet: offer the wizard. |
| "reset contract-review to defaults" | confirm first (this deletes your captured standard — AF posture, the wizard answers are the review's spine), then remove `standard-terms.md`; reviews fall back to the market-standard defaults below until the wizard runs again. |

These verbs are ALIASES into the Phase 0 wizard — they exist so the whole composer family answers
the same tune/show/reset vocabulary. The file stays the single source of truth; do NOT create
`_hq/data/skill_config/contract-review.json`.

**No-standard fallback (user skipped the wizard):** still assign colors against market-standard defaults — don't punt to "everything's yellow."
- **RED:** uncapped or one-way indemnification; unlimited liability; IP assignment of pre-existing/background work.
- **YELLOW:** term > 24 months; auto-renewal with no notice window; payment terms > net 45; unilateral change clauses.
- **GREEN:** mutual, market-standard terms.
Close every no-standard review with one line offering the wizard ("Want me to capture your standard terms so next time I compare against yours, not the market?").

### Phase 1 — Parse contract

Read the document. Use:
- For PDF: PDF MCP's `read_pdf_content` + structural-extraction prompt
- For .docx: python-docx parsing

Extract:
- Parties (names, addresses, signature blocks)
- Effective date + term length + auto-renew clause
- Fees / payment terms
- IP ownership + work-product clauses
- Confidentiality scope
- Indemnification (one-way vs mutual, caps if any)
- Termination notice + survival
- Governing law / dispute resolution
- Non-compete / non-solicit if present

### Phase 2 — Load standard + history

- Read `_hq/contracts/standard-terms.md` — your standard for each clause.
- **Resolve the counterparty via the canonical resolver — never a hand-rolled name match.** The party name extracted in Phase 1 ("Acme Co", "ACME CORPORATION") must resolve through `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)` (aliases.json → entities.json, per `shared/ENTITY_RESOLVE_PROTOCOL.md`) to a canonical `org_id`:

  ```python
  import sys
  # Rule 22 preamble REQUIRED before this runs: cd "$PLUGIN_ROOT" (SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}")
  sys.path.insert(0, "shared/scripts")
  from entity_resolve import resolve_all
  matches = resolve_all(workspace_root, counterparty_name_from_contract)
  # Single org match → use its org_id as counterparty_org_id (event + history reads).
  # Ambiguous → surface disambiguation before proceeding.
  # No match → counterparty_org_id = null; note in the review that this is a
  #   first-time counterparty (and history sections are empty by construction).
  ```

- Read entities.json for the resolved counterparty org record (prior contract history, relationship tier).
- Read `_hq/data/events.jsonl` for prior `contract_reviewed` events involving the counterparty OR matching deviation classes — from the Reads section's org-scoped load (`load_events_org_scoped`), never a raw read.
- **Duplicate-contract check (ADV1).** Compute this contract's `term_summary_hash` (the same hash written on the `contract_reviewed` event) early, then scan prior `contract_reviewed` events for a matching `data.term_summary_hash`. On a hit, surface one line up front: *"Heads up — you reviewed this exact contract on [date of the prior event]. Want the diff against last time, or a fresh full review?"* This is what makes the otherwise-write-only `term_summary_hash` field earn its place.

  **The `term_summary_hash` recipe (exact — do not improvise):** SHA-256 over the lowercased, whitespace-collapsed concatenation of the extracted key-term VALUES (the Phase 1 extraction results as short strings, never the raw contract text) in this FIXED field order, first 16 hex chars:

  ```python
  import hashlib, re

  TERM_HASH_FIELDS = [
      "parties", "term_length", "auto_renew", "fees", "payment_terms",
      "ip_ownership", "confidentiality", "indemnification",
      "termination", "governing_law", "non_compete",
  ]  # FIXED order — reordering, adding, or removing a field breaks every prior hash

  def term_summary_hash(terms: dict) -> str:
      parts = []
      for field in TERM_HASH_FIELDS:
          value = str(terms.get(field) or "")          # absent term → empty string
          parts.append(re.sub(r"\s+", " ", value).strip().lower())
      return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
  ```

  Determinism rules: each value is the extracted term summary rendered the same way every run (e.g. `"12 months"`, `"net 30"`, `"mutual, uncapped"`); absent terms contribute `""`; never include dates-of-review, file paths, or page counts. Two runs over the same contract MUST produce the same 16-hex-char hash — that identity is the entire point of the field.
- Read `_hq/data/events.jsonl` for binding `decision` events about contract terms — same org-scoped load, filter `type == "decision"`.

### Phase 3 — Flag each term

For each key term, classify:
- 🟢 Green — matches your standard
- 🟡 Yellow — deviates but within normal negotiation range
- 🔴 Red — violates a binding decision OR creates material exposure (e.g., uncapped indemnification when your standard caps it)

For each yellow / red, compose:
- Suggested redline (specific clause language to propose back)
- Pattern note if applicable ("third uncapped indemnification contract this quarter")
- History note if applicable ("Acme pushed for the same IP carve-out in their Sep 2025 NDA")

### Phase 4 — Generate questions to ask

For each red flag, generate 1-2 questions probing the counterparty's underlying concern. Often the concrete deviation hides a soft requirement that can be resolved with narrower language. Surface as a "Questions to Ask Before Signing" section.

### Phase 5 — Write artifact + event

- Render the .docx via `shared/scripts/brief_writer.py` with the contract-review template (Key Terms / How it compares / Redlines / Questions sections).
- **NEVER hand-roll the review** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship a substandard or leaking review (the v3.20.0 failure mode) — and this document carries counterparty names, negotiated terms and the user's own standard side by side, which is exactly what the leak scan exists to catch.
- **NEVER create, render, copy, upload, or update the review — or any part, derivative, or restatement of it ("the redlines", "the flag list", "a summary") — through Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not one vendor's API quirk). It fails twice at once: the connector path bypasses every gate above, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root, not `_hq/contracts/` (the 2026-07-24 root-drop incident). Not exceptions: "for mobile", "so I can share it with my lawyer", "as a copy alongside the canonical file" — **nor a direct instruction**: "put the review in a Google Doc so counsel can comment" is a request this gate refuses, not an override. Sending a redline analysis of a live negotiation to a connector's default drive is the worst version of this bypass — hand back the `.docx` link and let the user route it deliberately.
- **Executive Output Standard (SPEC OUT2 §4 — `contract_review` is now a STANDARD_KIND; `make_brief` REFUSES the render without this).** Pass `exec_header`:
  - **verdict = the deal-breaker flag line** — the single red flag that must move before signing: *"Don't sign as-is — uncapped indemnification violates your Jan 12 cap decision."* When nothing is red: *"Safe to sign — two yellow terms worth a push, no deal-breakers."*
  - **changed** = what's new vs the counterparty's prior paper (the pattern/history note: "third uncapped-indemnification contract this quarter"), or the nothing-form.
  - **decide** = the negotiate-vs-accept call in front of the user (with the date if one is live). **needs** = the one action ("approve the §6.1 redline below"), or "Nothing from you."
  - **Subsumption (net length must not increase):** the verdict REPLACES the former top summary line of "How it compares to your standard" (the "Push back before signing: …" lead) — the matrix carries the detail; the header carries the conclusion. Body sections never restate the header.
- **Visual pass (SPEC OUT2 §3, after the save):** run the render-then-critique pass per `shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The visual pass" — call `shared/scripts/visual_gate.py` `render_preview(<saved path>)`, LOOK at the returned page images against the 7-item checklist (orphaned heading at a page break · empty/placeholder tile · table overflow/wrap damage · cramped spacing · header/footer intact · brand palette applied — the flag matrix's tinted cells are exactly what this catches when they wrap or render blank · chart unreadable / overplotted), fix + re-save AT MOST ONCE, then log `visual_gate.log_visual_gate(WORKSPACE_ROOT, doc, rendered, findings, fixed)` either way. `None` from the ladder = no renderer on this machine — log `rendered: false` with a `skipped_reason` and proceed exactly as before (warn-only forever).
- **Exemplar anchor (SPEC OUT8).** Before composing, load the kind's structural exemplar — `exemplars.get_exemplar("contract_review", workspace_root)` (`shared/scripts/exemplars.py`) — and anchor STRUCTURE on it: section order, visual placement, proportions (the flag-matrix contract below stays authoritative; the exemplar anchors layout within it). Workspace exemplar (`_hq/exemplars/contract_review/`) beats the shipped seed; `None` = compose on the template above, unchanged. **Contract beats exemplar beats default** — an exemplar never licenses skipping the exec header or any gate, and it anchors structure, never facts: no name, number, or claim from the exemplar may appear in the review. After saving, run `exemplars.scan_docx_for_exemplar_tokens(docx_path, exemplar["text"])`; a finding means exemplar placeholder content leaked — fix the sections payload and re-save AT MOST ONCE (the visual-pass posture, warn-only). When the user gives structural feedback on a delivered review ("make it like this", reorder/drop a section), capture it with `exemplars.append_structural_correction(workspace_root, kind="contract_review", direction=..., section=...)` — capture only; the exemplar itself updates exclusively through insight-generator's confirm-first proposals (`shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The exemplar anchor").
- **"How it compares" is a `matrix` (SPEC OUT1 §4), not prose labels.** Pass a section whose `matrix` has `headers_row = ["Your standard", "This contract", "Flag"]`, `headers_col` = the term names (leftmost column), the cell grid = `[your-standard-summary, this-contract-summary, flag-word]` per row, and **`flag_col_idx = 2`** so the renderer shades each Flag cell with the brand tint that matches its word. Use the plain flag WORD in the flag column — `Standard` / `OK` (green tint), `Review` / `Watch` (amber tint), or `Flag` / `Risk` (red tint) — NOT the emoji dots and NOT a raw color name; the shading carries the color, the word carries the meaning for a reader who prints in grayscale or is colorblind. The per-term redline/pattern/history prose analysis stays BELOW the matrix in the Redlines section — the matrix is the scan-in-5-seconds layer, the prose is the depth.
- Save to `_hq/contracts/ContractReview_[Counterparty]_[YYYY-MM-DD].docx`.
- Append `contract_reviewed` event.
- Surface a 1-line summary in chat: "Reviewed the Acme Co MSA. Most of it lines up with your standard — two items to push back on, and one I'd want to negotiate before signing (uncapped indemnification). Suggested language and questions are in the brief."
- **Then end the chat turn with the H2 doc link (CONTRACT Rule 3 — link LAST in the turn, never inline).** Build it with the canonical helpers — never hand-encode a `computer:///` URL:

  ```python
  import sys
  sys.path.insert(0, "shared/scripts")
  from chat_output_renderer import doc_headline_link
  from brief_path import get_brief_artifact_url
  print(doc_headline_link("Contract review — Acme Co MSA", get_brief_artifact_url(output_path)))
  ```

## Output Structure (.docx)

```
CONTRACT REVIEW — Acme Co Master Services Agreement
Reviewed 2026-05-19 | Acme Co (Wilmington, DE) | 12 pages

[Exec header (OUT2 §4) — the deal-breaker flag line leads:]
**Don't sign as-is — uncapped indemnification violates your Jan 12 cap decision.**
CHANGED   Third uncapped-indemnification contract this quarter; Acme re-ran their Sep 2025 IP carve-out.
DECIDE    Negotiate §6.1 + §9 before signing, or accept the 90-day notice as-is.
NEEDED    Approve the two suggested redlines below.

KEY TERMS
  Parties:         Your company and Acme Co
  Term:            12 months, auto-renew
  Fees:            $14,500/mo, net 30
  IP ownership:    Work product goes to Acme  (worth pushing back — see below)
  Termination:     90 days notice              (worth pushing back — your standard is 30)
  Indemnification: Mutual, uncapped            (push back — see below)
  Governing law:   Delaware

HOW IT COMPARES TO YOUR STANDARD
  [The flag matrix (Phase 5) — Your standard | This contract | Flag, one row
   per term, flag cells tinted. The former "Push back before signing:" summary
   line is SUBSUMED by the exec-header verdict above (no-duplication rule);
   the indemnification detail lives in its matrix row + the Redlines section.]
  Matches your standard:   payment, governing law, confidentiality, term length
  Worth negotiating:       IP ownership, termination notice

WHAT TO KNOW
  - Acme asked for the same IP carve-out in their NDA back in Sep 2025.
  - This is the third contract this quarter with uncapped indemnification.

SUGGESTED LANGUAGE
  §6.1 IP Ownership — instead of:
    "All Work Product shall be the sole property of Acme"
  try:
    "Work Product is jointly owned; Acme receives a perpetual,
     royalty-free license for internal business use."

  [...]

QUESTIONS TO ASK BEFORE SIGNING
  • What's the concern behind the IP carve-out? If they're worried
    about competitive use, a narrower non-compete might solve it.
  • Why 90-day notice? Industry standard is 30 — sometimes longer
    notice means they got burned before.
```

## DOES NOT

- Provide legal advice. Surfaces deviations + questions; the user takes them to counsel.
- Auto-redline the contract file. Suggested redlines are in the review .docx; the user copies/edits into the contract via their own tooling.
- Sign or send. Use DocuSign / Adobe Sign MCP for signature workflow.
- Modify `_hq/contracts/standard-terms.md` mid-review. If a review reveals you want to update your standard, that's a separate explicit edit.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Review a contract or NDA — extract key terms, compare against your standard terms, flag deviations green/yellow/red, and suggest redlines. Counterparty- and history-aware: if the counterparty has pushed for the same carve-out before, the review notes the pattern. Use when the CEO says 'review this contract', 'review this NDA', 'redline this contract', 'redline this NDA', 'contract review', 'check this contract', 'analyze this contract', 'review this MSA', 'redline this MSA', 'review this agreement', 'analyze this agreement', 'compare this contract to my standard', 'flag risks in this contract'. Reads the contract PDF/docx, `_hq/contracts/standard-terms.md` (your standard), entities.json for counterparty context, events.jsonl for prior contract_reviewed events with the same counterparty or matching deviation classes. Writes contract_reviewed event with parties, deviation count, term hash, .docx artifact link. DOES NOT fire on 'write a contract' (out of scope — Command Room reviews, doesn't draft contracts), 'lawyer questions' (out of scope), or 'sign this contract' (use DocuSign / Adobe Sign MCP directly).

> Also handles standard-terms settings (SPEC OUT2 §5 — aliases onto the Phase 0 wizard; storage stays `_hq/contracts/standard-terms.md`, never a second store) — use when the CEO says 'tune contract-review', 'show contract-review settings', 'reset contract-review to defaults', 'update my standard terms', 'show my standard terms'. (These verbs live here rather than in the description because the description budget is capped — G11; the runtime router and the trigger tests read the description and this Routing corpus together.)
