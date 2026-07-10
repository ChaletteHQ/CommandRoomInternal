# Command Room — Executive Output Standard (EXEC1, v3.20.0+ canonical)

**The single inheritable base layer that makes every deliverable executive-grade.** A peer to `CONTRACT.md`: where CONTRACT governs *how outputs are written* (leak-clean, plain-English, widget-shaped), this standard governs *what an owner/executive gets from the page* — a 30-second contract at the top, recommendations before analysis, money-and-time quantification where the substrate supports it, an explicit ASK block, inline confidence honesty, and a three-rung detail ladder.

Enforced at the `brief_writer.make_brief()` chokepoint and at the chat-output validator — NOT re-implemented per skill. Every skill in the §4 adoption table inherits it; each adoption edit NAMES the section it subsumes so net document length does not increase.

This is the layer ABOVE B2 (voice gate) / B3 (contract gate) / B4 (wording pass). It coexists with them in the `make_brief` pipeline (order: contract → voice → exec-standard checks → render → leak scan) and never removes or reorders those gates.

---

## The six elements

### 1. The 30-second contract (exec header)

First block of every deliverable, before any section:

```
[Verdict — one bold sentence: the single conclusion of this document.]
CHANGED   Acme moved from handshake to paperwork; Northstar went 13 days quiet.
DECIDE    Whether to gate the sales hire on the playbook — by Jun 15 (board).
NEEDED    Approve the redline (one tap below) · nothing else.
```

**THE ANTI-WASHING FLOOR (load-bearing).** Each line ≤25 words AND contains a named entity, number, or date — OR the explicit nothing-form:

- `CHANGED   Nothing material since Tuesday's brief.`
- `DECIDE    Nothing — execution day.`
- `NEEDED    Nothing from you.`

"Nothing" being legal and *encouraged* is what makes this a contract instead of a summary-of-a-summary. Most days have no decision, and saying so is the value. Generic-summary shapes ("Several important updates," "Key developments this week," "Busy week across the board," "Lots of movement") are banned — they are listed as banned patterns in `chat_output_renderer` / `chat_output_validator` and rejected on the header lines.

**SUBSUMPTION RULE (net length must not increase).** The header REPLACES existing lead sections — weekly-recap "Headline", board-pack §1 prose, follow-up header counts, the standalone morning-briefing synthesis lead-line. It is not added on top of them. Each §4 adoption edit names the lead section it deletes. Body sections start at detail and never restate header sentences (the **no-duplication rule**).

**Render (`make_brief` kwarg `exec_header={verdict, changed, decide, needs}`):**
- `.docx`: verdict bold (13pt, navy); three small-caps labels (`CHANGED` / `DECIDE` / `NEEDED`) each followed by its line; light rule below. Rendered before the first section.
- **Chat:** the same three lines after the date line.
- **Widget:** the verdict is the widget header; the three lines render as the contract band; the ASK block is the widget's action set (see element 4 — one-ask-surface).

**Enforcement (HARD-REQUIRE — the OUT2 §4 flip, landed).** `STANDARD_KINDS` in `brief_writer` is the set of kinds required to carry an exec header. A `STANDARD_KIND` brief rendered WITHOUT `exec_header` (or with a blank verdict) **raises `ValueError` BEFORE `Document()` is built** — no partial file — and leaves a `brief_meta` severity="error" audit event as the substrate trace of the refused save. The warn-only staging release (EXEC1) gave scheduled-task orchestrator prompts a full release to catch up (CONTRACT Rule 16); the flip landed in the same commit as the orchestrator compliance sweep (upcoming-meetings passes `exec_header` on its call_prep path; past-meetings renders `past_meeting`, not a STANDARD_KIND; friday-wrap executes weekly-recap's phases, which pass it; no other scheduled orchestrator renders a `.docx`). The set now also carries the three EXEC1-deferred kinds: `contract_review` (verdict = the deal-breaker flag line), `automation_scan` (verdict = top opportunity + payback), `stress_test` (verdict = the kill-risk line).

### 2. Decision-forward ordering

Recommendation before analysis, always. Analysis exists so the reader can AUDIT the recommendation, not for suspense. For decision-shaped kinds (`decision_memo`, `memo`, `one_pager`), `brief_writer` runs an order check: a section whose heading reads as Recommendation / Decision / Suggested Outcome appearing at section index > 2 → **raise** (`ValueError`). decision-memo's interactive weight-setting FLOW is unchanged — only the document order flips.

### 3. Quantification discipline

`shared/scripts/quantify.py` `money_time_tag(commitment_or_thread, entities) -> str | None` is the ONLY sanctioned source of inline dollar tags on list items. It composes a tag like `"12d late · $40K deal"` by tracing `commitment.due` (time part) and `primary_thread_id → thread → org → revenue/deal-value field` (money part).

**It NEVER estimates.** When the substrate lacks the field, the part is omitted; when both parts are absent it returns `None` — no fabrication path exists by construction. A client without QuickBooks (or any annotated revenue field) simply gets no dollar tag, never a fabricated figure. quantify.py makes no MCP/connector call and reads only fields already persisted on the entity.

### 4. The ASK block

Every output ends with 0–3 explicit asks OF THE READER, each one-tap actionable on widget surfaces. Render via `make_brief` kwarg `asks=[{text, deadline?}]`, max 3 (>3 → `ValueError`), canonical heading **"What I need from you"**. Zero asks → render nothing (the header already said `NEEDED Nothing`).

- **ONE-ASK-SURFACE RULE:** when a widget is present, the widget IS the ask block — never a prose twin. Don't render the "What I need from you" prose section AND the widget action set for the same asks.
- Asks are reader-actions (`Approve the redline`, `Ratify by Friday`), never system-narration (`I will continue monitoring`).

### 5. Confidence honesty inline

Single-stale-source claims hedge at the claim: `~200 employees — LinkedIn, may be stale`. Any output that contains hedges closes with one plain-English confidence line naming what to confirm. The `research` Step 4 line is the verbatim template:

> *"Confidence medium on the company, low on size and on who the buyer is — confirm headcount and decision-maker on the call."*

Plain-English bands only (`high` / `medium` / `low`) — numeric confidence is already a CONTRACT Rule 4 leak pattern. This is **checklist-enforced**, not regex-enforced; don't pretend a regex can validate semantic honesty.

### 6. The detail ladder

- **Rung 1** — the verdict line / H2-link label is a *conclusion*, not a label: "Acme renewal is the only decision this week," never "Weekly Recap."
- **Rung 2** — the 30-second contract (element 1).
- **Rung 3** — full sections, starting at detail.

Falls out of elements 1 + 2; the **no-duplication rule** (body never restates the header) is the checklist item.

---

## The synthesis-lead rule (where narrative is allowed)

Synthesis narrative (operator-report Section 0 / Friday Wrap pattern — one anchor moment + theme) is deliberately NOT generalized everywhere. Spreading it thin is how it dies.

**REQUIRED** where the output aggregates multiple event types over a time window AND the operator-report anchor-detection (cluster connectivity) finds a real anchor. No anchor → one honest steady-state line, never a manufactured theme.

**FORBIDDEN** on:
1. **Queues / ranked lists** — their lead is the quantified count line; the reader's next act is triage, not interpretation.
2. **Argument documents** — the thesis IS the lead; meta-synthesis is double-leading.
3. **Single-event prep** — the lead is the walk-out objective (a goal, not a synthesis).

Net: synthesis leads live on **morning-briefing, weekly-recap / Friday Wrap, operator-report, and board-pack §1 — and nowhere else.**

---

## The visual pass (SPEC OUT2 §3 — render-then-critique)

Structural gates validate the payload; nothing before this section ever looked at the PAGE. The visual pass closes that gap: after `make_brief` saves a `STANDARD_KINDS` doc, the skill **renders the saved file and looks at it** — the error class this catches (evidence: PlotGen, WWW 2025) is exactly the one code review cannot: a tile that rendered empty, a table that wrapped into damage, a heading orphaned at a page break.

**The procedure (every STANDARD_KINDS save):**

1. Call `shared/scripts/visual_gate.py` `render_preview(docx_path)` — pages 1–2 as PNGs, rendered to a session temp dir (never the workspace). Best-effort ladder: Word COM (Windows) → `soffice --headless` (if on PATH) → `None`. It **never raises** into the skill.
2. If it returned `None`: the gate is skipped. Log the audit event with `rendered: false` + a short `skipped_reason` and proceed exactly as if the pass didn't exist. **The honest limit, stated plainly:** Cowork sandboxes may lack both renderers — the ladder returning `None` MUST leave behavior byte-identical to today. The gate upgrades machines that CAN render; it never degrades ones that can't.
3. If it returned images: LOOK at them and walk the fixed 6-item checklist (`visual_gate.CHECKLIST` is the machine copy):
   - orphaned heading at a page break
   - empty or placeholder tile
   - table overflow / wrap damage
   - cramped spacing
   - header/footer intact
   - brand palette applied
4. Findings → fix the sections payload and re-save **AT MOST ONCE**, then proceed regardless of the re-render's outcome. One extra model look per document — that cost is ratified (strategy Decision 3, M 2026-07-09). Never loop.
5. Log the audit event either way: `visual_gate.log_visual_gate(workspace_root, doc, rendered, findings, fixed)` → a `visual_gate` event `{doc, rendered, findings, fixed}`. This is what usage-report / insight-generator mine to prove the gate fires.

**WARN-ONLY FOREVER at the code layer.** The visual pass is judgment, not schema — there is no blocking mode and none is planned. A finding never refuses a save; it earns at most one fix-and-resave.

---

## The ranked report (layout contract — SPEC OUT2 §4)

The recurring shape shared by **dormant-customer-scan, automation-scanner, stalled-projects, and relationship-moves**: a scan that ranks items and hands the reader actions. One layout contract so all four read as one system:

1. **Tile summary band first** — 2–4 stat tiles derived from the SAME computation that built the ranked list (never a second pass, never a prose re-count). Drop-empty per F-60: a tile whose datum is genuinely unknown is omitted; a real zero renders. On `.docx` this is the first section's `tiles` list; on widget surfaces it's the shared tile fragment (components.py, one implementation).
2. **Scored rows** — each ranked item renders: **rank · name · quantify tag · why-now · action**. The quantify tag comes from `quantify.money_time_tag` or the skill's own computed score (shown so the reader can challenge the inputs, not the ranking) — never an estimate. The why-now is one line citing real evidence. The action is the item's one-tap next step.
3. **Widget actions last** — when a widget is the surface, the widget IS the ask block (one-ask-surface; element 4). No prose twin of the action list.

Queues/ranked lists take **no synthesis lead** (see the synthesis-lead rule above) — the lead is the quantified count line or the exec-header verdict, and the reader's next act is triage.

---

## The output profile (SPEC OUT2 §5 — cross-skill, dormant by default)

One workspace-level profile shapes HOW every `.docx` composer renders, without any skill re-implementing it. `make_brief` resolves it per render via `shared/scripts/output_profile.py` `get_output_profile(workspace_root)` — the same defaults-first posture as the brand layer (SPEC OUT1): **an absent or unconfigured profile is byte-identical to today's output.** No warning, no event, no config required.

**Storage:** `_hq/data/skill_config/output_profile.json` (the FRP1 store), written ONLY through `skill_config_writer.save_skill_config(ws, "output_profile", {...})` after `output_profile.validate_output_profile` passes.

**The knobs (default first — the default IS today's behavior):**

| Knob | Values | What it changes |
|---|---|---|
| `density` | **tight** · narrative | body-paragraph spacing only (narrative = looser line spacing for prose-preferring readers) |
| `visual_bias` | **tiles_first** · prose_first | tiles/body order within a section (prose_first renders the body above the tile band) |
| `page_cap` | **{}** · `{<kind>: N}` | WARN-ONLY: an over-cap render gets one stderr note; never blocks, never truncates |
| `default_format` | **docx** | the only value for now — premium HTML lands with Wave 3; unknown values resolve back to docx |

**Who writes it — exactly two paths, both explicit:**
1. **"tune output"** / "show output settings" / "reset output to defaults" — owned by workspace-manager ("output" is not a skill, so the bare-tune router rule can't resolve it).
2. **insight-generator proposals** — confirm-first REVIEW items (Pass 15's shape), never applied silently.

⛔ **FENCE (do not cross):** the output profile has **NO first-run block and NO onboarding mention**. It never appears in a first-fire footer, an M1 batch, or any proactive offer — it is a power-user surface until Wave 3. A future session that adds it to `FIRST_RUN_PROTOCOL`'s catalog or an onboarding widget is violating a ratified decision (strategy Decision set, M 2026-07-09).

Precedence note: the profile sits BELOW per-skill knobs and SCL1 directives in specificity — it sets the workspace-wide default; a skill's own config or a directive that says otherwise for that skill's documents wins.

---

## The four standard checklist items (every adopting skill adds these)

Per-skill, the adoption edit appends these four binary checks (the human/agent judgment layer the validator can't fully automate):

1. **Header is concrete-or-nothing** — every exec-header line carries a named entity, number, or date, OR uses the explicit nothing-form. No generic-summary shapes.
2. **Recommendation before analysis** — for decision-shaped output, the rec/decision/verdict precedes the options/comparison/criteria.
3. **Quantify tag when non-None** — list items that touch revenue/time carry the `money_time_tag` ONLY when it returns non-None; never an estimate, never a hand-typed dollar.
4. **Asks ≤3, reader-actionable, one-surface** — the ASK block holds 0–3 reader-actions; when a widget is present the widget is the ask (no prose twin).

---

## Three surface renderings (summary)

| Element | `.docx` (`make_brief`) | Chat | Widget |
|---|---|---|---|
| Exec header | bold verdict + 3 small-caps lines + rule | 3 lines after the date line | verdict = header; 3-line band |
| ASK block | "What I need from you" section, last | trailing reader-action list | the widget action set (one-ask-surface) |
| Quantify tag | inline on list items / bullets | inline | inline on item context |
| Confidence | inline hedge + closing confidence line | same | same |

---

## Risks this standard defends against

- **Executive-washing** — defended by the concreteness floor, subsumption-not-addition, the no-duplication rule, and legal-nothing-forms.
- **Fabrication pressure** — defended *by construction*: quantify.py has no estimation path.
- **Upgrade breakage** — defended by two-release staging (exec_header shipped warn-only, flipped to ValueError in OUT2 §4 only after the scheduled-orchestrator compliance sweep, in the same commit as any orchestrator sync).
- **Ask duplication on widget turns** — defended by the one-ask-surface rule.
- **decision-memo process confusion** — the interactive flow is unchanged; only the document order flips.

---

## Relationship to CONTRACT.md

CONTRACT.md remains the source of truth for leak patterns, canonical actions, widget format, plain-English voice, and structural data shapes. This standard adds the executive-altitude layer on top. Where they overlap (e.g. numeric-confidence is both a CONTRACT Rule 4 leak and an EXEC1 element-5 violation), CONTRACT's leak scanner is the blocking gate and EXEC1's checklist is the quality gate. Neither supersedes the other.
