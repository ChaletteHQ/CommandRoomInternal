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

**Enforcement (CLIENT-SAFE — warn-only this release).** `STANDARD_KINDS` in `brief_writer` is the set of kinds expected to carry an exec header. A `STANDARD_KIND` brief rendered WITHOUT `exec_header` emits a **`brief_meta` audit event (warn-only) and NEVER raises** this release. The ValueError is a FUTURE release (N+1), after scheduled-task orchestrator prompts catch up (CONTRACT Rule 16 — prompts lag the plugin; a hard-require now would break every scheduled brief on every live client workspace on upgrade day).

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
- **Upgrade breakage** — defended by two-release staging (exec_header warn-only now, ValueError N+1).
- **Ask duplication on widget turns** — defended by the one-ask-surface rule.
- **decision-memo process confusion** — the interactive flow is unchanged; only the document order flips.

---

## Relationship to CONTRACT.md

CONTRACT.md remains the source of truth for leak patterns, canonical actions, widget format, plain-English voice, and structural data shapes. This standard adds the executive-altitude layer on top. Where they overlap (e.g. numeric-confidence is both a CONTRACT Rule 4 leak and an EXEC1 element-5 violation), CONTRACT's leak scanner is the blocking gate and EXEC1's checklist is the quality gate. Neither supersedes the other.
