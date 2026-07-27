# Known Issue Patterns — report-bug seed

Loaded by `report-bug/SKILL.md` Step 3. Each entry is a pattern that has shipped in v3.x and a fix the maintainer already knows. New patterns get added here whenever a real bug is fixed from customer feedback.

## Schema per entry

```
### [Pattern name]

**Signature:**
  - Keywords (in user's "what happened" sentence): [comma-separated]
  - Skill name (last fired): [skill name or "any"]
  - Symptom (auto-collected context check): [optional rule]

**Likely cause:** [one-line technical summary]

**User-side fix:** [plain-English instructions, or "(none — escalate immediately)"]

**Escalate?** [yes | no | try-first]
```

---

## Patterns (seeded 2026-05-13)

### Cowork shows old plugin behavior after an update

**Signature:**
  - Keywords: stale, old version, didn't update, still showing, behavior didn't change, version mismatch, update didn't work
  - Skill name: any
  - Symptom: plugin.json version differs from what user expected after running update

**Likely cause:** Cowork's VHD-cache is holding the previous plugin state — the update wrote to disk but Cowork is still serving the cached version in this session.

**User-side fix:** Fully quit Cowork (confirm the process is gone in Task Manager / Activity Monitor — not just the window). Reopen. Plugin should now show the new behavior. This is the v3.2.1 documented workaround.

**Escalate?** try-first

---

### Onboarding fires again on an already-set-up workspace

**Signature:**
  - Keywords: onboarding restarting, asking me to onboard again, scaffolding again, step 1 of 8, phase 1 of 6, already onboarded
  - Skill name: command-room-onboarding
  - Symptom: events.jsonl contains a prior `onboarding_checkpoint` with `status: "complete"` (M1 phase `"6"`, legacy onboarding-v2 phase `"5"`, or pre-v2 phase `"7"`)

**Likely cause:** Phase 0a's existing-workspace guard isn't reading the prior checkpoint correctly — usually because the user pointed Cowork at the parent folder instead of the workspace folder, OR the checkpoint event was overwritten by a workspace-ingest run.

**User-side fix:** Confirm Cowork is pointed at the workspace folder (the one containing `_hq/`), not the parent. If still firing, say `skip onboarding` to bypass and `workspace-manager` will take over.

**Escalate?** try-first

---

### Speaker attribution wrong in past-meeting briefs

**Signature:**
  - Keywords: wrong speaker, attributed to wrong person, said something they didn't say, transcript mislabeled, rio, speaker mixed up
  - Skill name: meeting-notes, enable-command-room-schedules (past-meetings orchestrator)
  - Symptom: any

**Likely cause:** Granola transcript has ambiguous speaker labels and the Rio-Sample speaker-attribution guard (v3.2.3) didn't fire. Usually happens when one attendee dominates a meeting >70% by time.

**User-side fix:** (none — escalate immediately)

**Escalate?** yes

---

### Zapier-sent email lands as a new thread instead of a reply

**Signature:**
  - Keywords: not threaded, separate email, new thread, didn't reply in thread, broke the thread
  - Skill name: email-writer, enable-command-room-schedules (inbox orchestrator)
  - Symptom: user mentions Zapier in passing OR workspace has Zapier integration set up per `entities.json`

**Likely cause:** Outbound email is missing the RFC 822 `Message-ID` reference header that threads it under the original. Fix shipped in v3.2.3 but may regress if Zapier zap was rebuilt or the customer's email provider strips the header.

**User-side fix:** (none — escalate immediately, but check the Zapier zap is using the v3.2.3 template before emailing)

**Escalate?** yes

---

### Commitments view shows empty even though I have lots of meetings

**Signature:**
  - Keywords: no commitments, commitments empty, commitments missing, nothing tracked, where are my commitments
  - Skill name: any
  - Symptom: events.jsonl has ≥3 `type: meeting` events but 0 open `type: commitment` events

**Likely cause:** The commitment producer pipeline (meeting-notes Step 5e, inbox-triage, follow-up-ritual) hasn't run on the historical meetings yet, OR a workspace-ingest run was done without firing the commitment scan.

**User-side fix:** Say `scan for commitments`. The `scan-for-commitments` skill is a one-shot bulk extraction over your existing meeting transcripts and email threads — populates the commitments view in ~1-2 minutes.

**Escalate?** no

---

### Agent improvises around a canonical path

**Signature:**
  - Keywords: wrong folder, wrote to wrong place, file in wrong location, created in random folder, can't find what it just made
  - Skill name: any (recurring across v2.14.x line)
  - Symptom: any

**Likely cause:** This is a recurring class of bug — the agent makes up a "reasonable-looking" path instead of using the documented canonical one. Fix is structural (make the canonical UX good enough that improv stops being attractive); reporting individual instances helps map the surface.

**User-side fix:** Tell me where the file should have gone (best guess is fine), and what folder it ACTUALLY landed in. The path mismatch is the data point the maintainer needs.

**Escalate?** yes

---

### Voice profile is generic / drafts don't sound like me

**Signature:**
  - Keywords: doesn't sound like me, generic, robotic, not my voice, sounds like ChatGPT, wrong tone, voice is off
  - Skill name: email-writer, meeting-notes, follow-up-ritual, memo-writer, one-pager-composer
  - Symptom: `_hq/BRAND_VOICE.md` exists AND was generated from <10 sent emails (check metadata header)

**Likely cause:** Voice profile was built from a thin sample (<10 sent emails captured during onboarding's Step 2d Branch B path). The profile gets sharper with every email the customer writes through the system, but the first week's drafts can read flat.

**User-side fix:** Write 3-5 emails through me this week (just ask `draft an email to [person] about [topic]` — review, edit if needed, send). The corrections feed back into the profile. Voice should lock in within ~10 sends.

**Escalate?** no

---

### Scheduled task fired but no output appeared

**Signature:**
  - Keywords: didn't run, no morning briefing, no inbox triage, missed the schedule, didn't fire, where's my brief
  - Skill name: enable-command-room-schedules (any orchestrator)
  - Symptom: any

**Likely cause:** Three common causes — (1) computer was asleep at fire time (scheduled tasks need the machine awake), (2) connector was deauthorized between schedule registration and fire time, (3) Cowork wasn't running when the cron triggered.

**User-side fix:** Check (a) was your computer on at the scheduled time? (b) is Cowork open right now? (c) try the manual trigger phrase for the missing task (e.g., `triage my inbox`, `morning briefing`). If the manual fire works but the schedule never fires, that's a real bug worth reporting.

**Escalate?** try-first

---

### Onboarding output looks stripped down vs. someone else's

**Signature:**
  - Keywords: missing voice contrast, no relationship card, stripped, half-fire, partial, looks different from
  - Skill name: command-room-onboarding
  - Symptom: any

**Likely cause:** Pre-v2.14.23 the spec had silent-skip paths for thin data. Fixed via three-branch lock + partial-card fallback. If the customer is on v3.x and still seeing strip-down, the data was below even partial-fallback thresholds (e.g., <3 sent emails total).

**User-side fix:** Onboarding's voice + relationship beats sharpen with use — they're explicitly designed to grow over the first week. If you've been using the system 7+ days and the voice profile is still missing, that's a real bug.

**Escalate?** try-first

---

## How to add a new pattern

When the maintainer fixes a real bug from customer feedback:

1. Add an entry under "Patterns" using the schema above.
2. Keep the **Signature → Keywords** tight (5-10 phrases the customer would actually type — not technical jargon they wouldn't use).
3. **User-side fix** in plain English. If there's no user-side fix, say "(none — escalate immediately)" and set `Escalate? yes`.
4. **Escalate?**: prefer `try-first` over `no` when in doubt — false-positive "you can fix this yourself" is worse than a false-positive email.
5. Bump the file's "seeded" date in the header so the file's freshness is auditable.

The skill loads this file at runtime — no code change needed when adding patterns. Just edit the file and push.
