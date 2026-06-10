# Meeting Notes Detail Reference

Supplementary reference material for the meeting-notes skill. This file contains extended guidance, detailed templates, and comprehensive gotcha analysis.

---

## Business Lens Questions

When applying the business lens in Step 7, ask the user these detailed questions:

### Scope & Delivery
- Did scope creep in? New features, users, locations, or integrations?
- Is the timeline still realistic? Do you have the capacity?
- Who owns this now? Are expectations aligned?

### Financial Health
- Is budget being consumed faster than expected?
- Any new revenue, cost reduction, or margin shift?
- Do you have visibility on P&L impact?

### Relationships & Trust
- How's the relationship with this person/team? Warming, cooling, stressed, strong?
- Did any misalignment surface? (conflicting priorities, unspoken concerns)
- Is there a trust issue to address proactively?

### Risks & Execution
- What could derail this? (dependency, resource gap, external blocker)
- Is there pressure to accelerate beyond team capacity?
- What's the most likely failure mode?

### Opportunities
- Is there an opportunity to expand, cross-sell, or deepen the relationship?
- Could a small shift in approach unlock value?
- What's the play here?

---

## Gotchas Section

### 1. Meeting notes filed but not acted on

**Risk:** Notes sit in SESSION_NOTES, action items stay pending for weeks. Team loses momentum, owner is unaware of their commitments.

**Prevention:** Master Tracker update triggers task creation or calendar block; follow up on owner 3 days before deadline.

**Check:** Is the Next Action dated and assigned to a real person who knows about it?

---

### 2. Scope creep accepted without financial adjustment

**Risk:** Team absorbs new work, project burns budget, margin erodes. You ship more than you priced for.

**Prevention:** Flag every scope change → ref file; explicitly ask in follow-up if timeline/budget needs adjustment.

**Check:** Does scope change have a corresponding budget or timeline impact in Master Tracker?

---

### 3. Financial numbers mentioned but never tracked

**Risk:** You think you're tracking P&L, but meeting spend doesn't make it to financials.md. Your accounting is weeks behind reality.

**Prevention:** Extract every number (budget, spend, revenue, margin) → ref file with meeting date.

**Check:** Can you trace every $ mentioned in a meeting to a line item in your financials tracking?

---

### 4. New contacts mentioned but not recorded

**Risk:** You don't follow up, relationship stalls, decision-maker is lost. Six months later you can't remember who to call.

**Prevention:** Every named person → contacts.md with role, company, email if mentioned.

**Check:** Can you pull up a contact from a meeting 6 months ago and follow up?

---

### 5. Relationship shift missed until it's too late

**Risk:** Client is unhappy, internal friction builds, you miss the warning signs. Relationship erodes silently.

**Prevention:** Business lens explicitly asks about relationship dynamics; Master Tracker logs relationship status.

**Check:** Is relationship status (strong/fragile/cooling/warming) updated after every meeting?

---

### 6. Action item owner doesn't know they own it

**Risk:** Nothing happens because the person wasn't in the room or email didn't land. Critical path stalls.

**Prevention:** ACTION: Send owner a message (Slack, email) summarizing their action item + deadline.

**Check:** Does the owner have visibility and confirm they'll execute?

---

### 7. Master Tracker not updated, becomes stale

**Risk:** Your source of truth is weeks behind reality. Strategic decisions are based on outdated context.

**Prevention:** Update Master Tracker **in this skill run**, not later.

**Check:** Is Master Tracker timestamp recent? (Within 1 day of last meeting)

---

### 8. Granola transcript incomplete or auto-pull fails silently

**Risk:** You process partial notes, miss critical details, make bad decisions. Later, you discover you misunderstood a key point.

**Prevention:** Confirm transcript completeness; ask user if anything was cut off.

**Check:** Does the transcript span the full meeting duration? Any sections marked [audio unclear]?

---

### 9. Follow-up questions are generic or rhetorical

**Risk:** User doesn't answer, you don't surface implications. The meeting stays a filing exercise, not a strategic conversation.

**Prevention:** Ask specific, answerable questions that pull context (see Step 8 examples).

**Check:** Are your follow-ups actionable or just "how did it go?"

---

### 10. Assume attendees understood the same thing

**Risk:** Misalignment festers, only surfaces when execution stalls. You discover weeks later that Aria thought X and Bowie thought Y.

**Prevention:** If attendees had different roles, ask user: "Did everyone leave with the same understanding of X?"

**Check:** Are there any notes about disagreement or caveats in the decisions?

---

## Full SESSION_NOTES Template

Use this template for detailed SESSION_NOTES files. The compact version in SKILL.md Step 4 is sufficient for most meetings; use this for complex projects, high-stakes decisions, or financial implications.

```markdown
# Session Notes

## [Meeting Title / Context]
**Date:** YYYY-MM-DD  
**Duration:** Xm  
**Attendees:** [List with roles if known]  
**Project:** [Project name]

---

### Decisions
- [Decision 1]: [Who, rationale, any caveats or dissent]
- [Decision 2]: ...

### Action Items
| Owner | Task | Deadline | Owner Confirmed? |
|-------|------|----------|------------------|
| [Name] | [Specific, measurable task] | YYYY-MM-DD | Y/N |

### Financial Impact
- [Item or category]: $[Amount] | [Scope or rationale]
- **Total impact:** $[X] ↑ or ↓ [from budget/baseline]

### Scope Changes
- [What changed]: From [baseline] → To [new state]
- **Impact:** [Effort, timeline, resource, risk implication]

### Relationship & Dynamics
- **Attendee sentiment:** [Overall tone — aligned, tense, misaligned, uncertain]
- **Key unspoken concern (if any):** [What you picked up but wasn't directly stated]
- **Trust level:** [Strong, warming, cooling, fragile]

### Business Lens
**Risks**
- [Risk 1]: [Trigger, impact if happens, likelihood]
- [Risk 2]: ...

**Opportunities**
- [Opportunity 1]: [What we could do, potential upside]
- [Opportunity 2]: ...

**Timeline**
- [What's the pressure point? Accelerated delivery, external deadline, internal commitment?]

**Next Critical Action**
- [The one thing that must happen next to keep momentum]

### Questions to Follow Up On
- [Assumption that needs validation]
- [Gap in understanding or misalignment to surface]
```

---

## Tips for Best Results

- **Bring transcripts, not recordings** — Processing is faster and more accurate with text
- **Name the project/client early** — Saves time routing; if new, we create it
- **Flag what's uncertain** — If you're not sure about a decision or owner, say so; we'll dig in
- **Use follow-ups** — The 2-3 questions are designed to surface implications; answer them honestly
- **Update ref files regularly** — The more recent your contacts, scope, financials, risks, the better the follow-ups and context
- **Check Master Tracker weekly** — Make sure Next Action is still accurate and timely
