---
name: contract-review
description: "Review a contract or NDA — extract key terms, compare against your standard terms, flag deviations green/yellow/red, and suggest redlines. Counterparty- and history-aware: if the counterparty has pushed for the same carve-out before, the review notes the pattern. Use when the CEO says 'review this contract', 'review this NDA', 'redline this contract', 'redline this NDA', 'contract review', 'check this contract', 'analyze this contract', 'review this MSA', 'redline this MSA', 'review this agreement', 'analyze this agreement', 'compare this contract to my standard', 'flag risks in this contract'. Reads the contract PDF/docx, `_hq/contracts/standard-terms.md` (your standard), entities.json for counterparty context, events.jsonl for prior contract_reviewed events with the same counterparty or matching deviation classes. Writes contract_reviewed event with parties, deviation count, term hash, .docx artifact link. DOES NOT fire on 'write a contract' (out of scope — Command Room reviews, doesn't draft contracts), 'lawyer questions' (out of scope), or 'sign this contract' (use DocuSign / Adobe Sign MCP directly)."
---

## Skill Boundary

- **Use contract-review for:** reviewing a contract / NDA / MSA the user has received. Extracts terms, compares against your standard, flags deviations.
- **NOT for drafting** contracts from scratch.
- **NOT for signing** — use the DocuSign / Adobe Sign MCP for signature workflow.
- **NOT for legal advice** — surfaces deviations and questions; user takes them to counsel.

## Writer Contract (v3.8.0+ — substrate-native)

Before writing to any workspace file, read `shared/WORKSPACE_API.md`.

**Primary writer for:**
- `_hq/contracts/ContractReview_[Counterparty]_[YYYY-MM-DD].docx` — the structured review with key terms, flag list, suggested redlines, questions to ask. Per CONTRACT Rule 27 (no .md deliverables) the output is `.docx`.

**Appends to:**
- `_hq/data/events.jsonl` — event type `contract_reviewed` with `{counterparty_org_id, contract_title, term_summary_hash, deviation_count_by_color: {green, yellow, red}, artifact_path, source_file_path}`. The `term_summary_hash` allows detecting identical contracts across submissions. The `deviation_count_by_color` field is canonical-shape substrate that future consumers (insight-generator pattern detection, cleanup, board-pack-assembler) can aggregate over a period — no consumer reads it yet as of v3.12.0, but the event is shaped correctly for when one is built.

**Reads from:**
- The contract file (PDF or docx). Uses PDF MCP or python-docx for parsing.
- `_hq/contracts/standard-terms.md` — your standard terms reference. If absent on first use, prompt the user to create one (or skip the comparison pass).
- `_hq/data/entities.json` — counterparty org record. Pulls prior contract history, current relationship tier.
- `_hq/data/events.jsonl` — prior `contract_reviewed` events involving the same counterparty (so the review can say "they've pushed for this carve-out in 2 prior drafts") OR involving the same deviation class (so the review can say "third uncapped indemnification contract this quarter").
- `_hq/data/events.jsonl` — `type == "decision"` events about contract terms (e.g., "we won't accept uncapped indemnification" — surfaces as binding constraint in the review).

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
- If user chooses to create: walk through 6-question wizard (preferred IP ownership, term length, payment terms, termination notice, indemnification cap, governing law) → write `_hq/contracts/standard-terms.md`
- If user skips: proceed without comparison pass; flag everything as "worth a closer look — no standard on file to compare against"

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
- Read entities.json for the counterparty org record.
- Read `_hq/data/events.jsonl` for prior `contract_reviewed` events involving the counterparty OR matching deviation classes.
- Read `_hq/data/events.jsonl` for binding `decision` events about contract terms.

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

- Render the .docx via `shared/scripts/brief_writer.py` with the contract-review template (Key Terms / Flags / Redlines / Questions sections).
- Save to `_hq/contracts/ContractReview_[Counterparty]_[YYYY-MM-DD].docx`.
- Append `contract_reviewed` event.
- Surface the .docx link + 1-line summary in chat: "Reviewed the Acme Co MSA. Most of it lines up with your standard — two items to push back on, and one I'd want to negotiate before signing (uncapped indemnification). Suggested language and questions are in the brief."

## Output Structure (.docx)

```
CONTRACT REVIEW — Acme Co Master Services Agreement
Reviewed 2026-05-19 | Acme Co (Wilmington, DE) | 12 pages

KEY TERMS
  Parties:         Your company and Acme Co
  Term:            12 months, auto-renew
  Fees:            $14,500/mo, net 30
  IP ownership:    Work product goes to Acme  (worth pushing back — see below)
  Termination:     90 days notice              (worth pushing back — your standard is 30)
  Indemnification: Mutual, uncapped            (push back — see below)
  Governing law:   Delaware

HOW IT COMPARES TO YOUR STANDARD
  Matches your standard:   payment, governing law, confidentiality, term length
  Worth negotiating:       IP ownership, termination notice
  Push back before signing: uncapped indemnification — you decided on
                            2026-01-12 to cap this at 12 months of fees

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
