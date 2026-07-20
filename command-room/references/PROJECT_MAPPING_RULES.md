# Project mapping rules (v2.8.1+)

When an orchestrator stages a meeting prep, follow-up pack, or other entity-routed deliverable, it needs to decide WHICH project's deliverables folder gets the file. This doc specifies the deterministic rule set used by all v2.8.1+ orchestrators (daily-morning-pack, follow-up-ritual, state-watcher, etc.).

**No semantic LLM routing.** Every rule is deterministic regex/lookup. The fallback explicitly punts to the user instead of guessing.

## Inputs

For each calendar event or work item to route, the orchestrator has access to:

- **Event title** — string, may include emojis, separators, etc.
- **Event attendees** — list of `{name, email}` (M is filtered out before evaluation)
- **`_hq/data/entities.json`** — canonical record of orgs, projects, people, with aliases
- **`_hq/data/aliases.json`** — alias → canonical_id map for fuzzy resolution

## Rule order — first match wins

### Rule A — Title alias match

Calendar event title contains a thread-type or org-type alias from `aliases.json` (case-insensitive, word-boundary regex `\b<alias>\b`).

If hits:
- Resolve alias → canonical_id
- If canonical_id is `org_*`, take that org's primary active thread (the project whose `is_primary: true` flag is set; if none, the most-recently-touched active project)
- If canonical_id is `project_*`, use directly
- **Result: stage to that project's deliverables folder**

Examples:
- Event title `"Acme Phase 1 sync"` → alias `Acme` matches → `org_006` → primary active thread `project_007` → stage to `Acme Fragrances/deliverables/`
- Event title `"CR Plugin standup"` → alias `CR Plugin` matches → `project_016` → stage to `Command Room - Plugin/deliverables/`

### Rule B — Attendee email-domain consensus

Strip M's email from attendees. Collect remaining attendees' email domains.

If ≥1 non-M attendee AND **all** non-M attendees share a domain that matches an entry in any `org.domains[]` array in entities.json:
- Resolve domain → org → primary active thread
- **Result: stage to that project's deliverables folder**

Examples:
- 3 attendees: M, aria@example.com, bowie@example.com → all-non-M share `summit.example.com` → matches `org_005.domains` → primary thread `project_002`
- 4 attendees: M, aria@example.com, bowie@external.example.com → mixed domains → Rule B doesn't fire (falls through to Rule C)

### Rule C — Single-attendee person match

Single non-M attendee whose email matches a `person.emails[]` entry in entities.json. That person resolves to a single org (`primary_org_id`, else a sole `affiliation_ids[]` entry; the deprecated flat `org_id` is read only as back-compat).

- Resolve email → person → person's org (`primary_org_id`) → org → primary active thread
- **Result: stage to that project's deliverables folder**

Examples:
- 2 attendees: M, sam@example.com → sam matches person_005 → org_005 → project_002
- 2 attendees: M, quinn@unknown.example.com → quinn has no entity record → Rule C doesn't fire (falls through to Rule D)

### Rule D — Title-token person match

Event title contains a `person.canonical_name` or any `person.nicknames[]` entry (word-boundary). That person resolves to a single org (`primary_org_id` / sole `affiliation_ids[]`).

- Resolve token → person → person's org (`primary_org_id`) → org → primary active thread
- **Result: stage to that project's deliverables folder**

Examples:
- Event title `"Catch up with Bo"` → token `Bo` matches person.nicknames → person_003 → org_003 → primary thread
- Event title `"Catch up with Cal"` (where multiple Steves exist in entities.json) → ambiguous → Rule D doesn't fire (falls through)

## Ambiguity handling

If multiple rules fire with **conflicting** answers (e.g., title alias points to Acme Phase 1 but attendee domain points to Summit Company), DO NOT pick. Stage to the unrouted folder with a plain-English banner at the top of the file. The banner does NOT name internal rules (Rule A/B/C/D, "deterministic", "domain consensus" etc.); it describes the conflict in plain English the user can act on:

```markdown
> **Couldn't pick a project — two reasonable matches.**
>
> Best guess from the title: **Acme Phase 1**
> Best guess from who's on the invite: **Summit Company**
>
> Pick one or move the file manually. Replying with the project name in chat will route it.
```

The deliverables-in-flight artifact (Phase 2) surfaces ambiguous items in a separate row state with a "resolve route" action.

## No match at all

If no rule fires, stage to the unrouted folder with a plain-English banner that includes a heuristic suggestion (the best guess from a fuzzy title-token scan against active projects). The banner shows what was checked in plain language and offers a one-click route:

```markdown
> **Couldn't auto-route this prep.**
>
> Title: "Q3 review"
> Who's on it: unknown@external.example.com
>
> Nothing in the title or attendee list matched a known project, person, or org domain.
>
> Best guess (fuzzy match — confirm before using):
>   - **Summit Company / Q3 strategy** (last touched 4 days ago, has "Q3" in recent notes)
>   - **Aspen Hardware / Quarterly review** (matches "review")
>
> If this is a new client or contact, add them in chat ("add Q3 to Summit Company") and the next run will route automatically.
```

The heuristic suggestion is generated by a simple title-token + project-name overlap scan, ranked by token overlap and project recency. Cap at top 3. If the scan returns no candidates above a low confidence floor, omit the "Best guess" block and just surface the no-match banner.

**Better than dropping the prep entirely** — M sees an unrouted prep, decides to either route it manually or to add the entity. Either action improves the system.

## Pseudocode

```python
def _single_org(person):
    """Canonical single-org affiliation for routing: primary_org_id, else the
    deprecated flat org_id, else the SOLE affiliation_ids entry. Returns None
    if absent or ambiguous (multiple affiliations). The legacy plural `org_ids`
    field does not exist in any schema (deep-audit 2026-05-29, findings #5/#27)."""
    if person.get('primary_org_id'):
        return person['primary_org_id']
    if person.get('org_id'):
        return person['org_id']
    aff = person.get('affiliation_ids') or []
    return aff[0] if len(aff) == 1 else None


def map_event_to_project(event_title, attendees, entities, aliases, m_email):
    # Rule A — Title alias match
    for alias, canonical_id in aliases.items():
        if re.search(rf'\b{re.escape(alias)}\b', event_title, re.IGNORECASE):
            project_id = resolve_to_active_thread(canonical_id, entities)
            if project_id:
                return ('matched', project_id, 'rule_A_title_alias', alias)
    
    non_m = [a for a in attendees if a['email'].lower() != m_email.lower()]
    
    # Rule B — Domain consensus
    if non_m:
        domains = {a['email'].split('@')[1].lower() for a in non_m}
        if len(domains) == 1:
            single_domain = domains.pop()
            for org in entities.get('orgs', []):
                if single_domain in org.get('domains', []):
                    project_id = primary_active_thread(org, entities)
                    if project_id:
                        return ('matched', project_id, 'rule_B_domain', single_domain)
    
    # Rule C — Single non-M attendee email match
    if len(non_m) == 1:
        email = non_m[0]['email'].lower()
        for person in entities.get('people', []):
            if email in [e.lower() for e in person.get('emails', [])]:
                org_id = _single_org(person)
                if org_id:
                    project_id = primary_active_thread_for_org(org_id, entities)
                    if project_id:
                        return ('matched', project_id, 'rule_C_attendee', email)
    
    # Rule D — Title-token person match
    for person in entities.get('people', []):
        tokens = [person['canonical_name']] + person.get('nicknames', [])
        for token in tokens:
            if re.search(rf'\b{re.escape(token)}\b', event_title, re.IGNORECASE):
                org_id = _single_org(person)
                if org_id:
                    project_id = primary_active_thread_for_org(org_id, entities)
                    if project_id:
                        return ('matched', project_id, 'rule_D_title_token', token)
    
    return ('unrouted', None, None, None)


def fuzzy_route_suggestions(event_title, entities, top_n=3):
    """Best-guess project candidates when all deterministic rules miss.
    Returns up to top_n (project_id, project_name, score, reason) tuples.
    Only used to populate the plain-English 'Best guess' block in the
    unrouted banner — never auto-routes."""
    title_tokens = {t.lower() for t in re.findall(r'\w{3,}', event_title)}
    if not title_tokens:
        return []
    candidates = []
    for project in active_projects(entities):
        name_tokens = {t.lower() for t in re.findall(r'\w{3,}', project['name'])}
        overlap = title_tokens & name_tokens
        if not overlap:
            continue
        recency_boost = 1.0 if days_since_touched(project) <= 7 else 0.5
        score = len(overlap) * recency_boost
        reason = f"matches: {', '.join(sorted(overlap))}"
        candidates.append((project['id'], project['name'], score, reason))
    candidates.sort(key=lambda c: -c[2])
    return candidates[:top_n]
```

## Edge cases worth flagging

- **Person with multiple affiliations** — Rules C and D require the person to resolve to a single org (`primary_org_id`, or a sole `affiliation_ids[]` entry). If a person works across multiple clients (e.g., Cal advises both Acme Co and Northstar via multiple `affiliation_ids`), neither rule fires for them. M decides which org to route to manually. Don't try to be clever.
- **Internal-only meetings** — meetings between M and team members of his own org typically don't need prep docs. Orchestrators should filter these out BEFORE running the rule set (e.g., `if all attendees are in your-own-org, skip prep entirely`).
- **External-personal meetings** (e.g., M's friend, doctor, family) — these will fall through all rules and land in `_unrouted/`. The orchestrator's "external = needs prep" filter should be smart enough to exclude personal contacts; if not, M dismisses the unrouted item, which logs the dismissal to `staging_outcomes.jsonl`, and the system learns over time which patterns are personal.
- **Multi-org meetings** (e.g., Q3 review with Acme + Summit attendees) — Rule B fails (mixed domains), Rule A may fire if title contains the right alias. If Rule A doesn't fire, item lands in `_unrouted/` with a banner showing the mixed-domain detection. M routes manually.

## Forbidden behaviors

- **DON'T use semantic LLM routing.** Tempting, but unreliable. We're not paying tokens for "is this meeting probably about X?" guesses.
- **DON'T fall back to the most-recently-touched project on no-match.** Wrong fallback creates clutter in the wrong project's folder. Unrouted is better.
- **DON'T silently drop the prep on no-match.** Always stage the work, just to `_unrouted/` with a banner. Dropping work is the worst outcome — M loses the prep AND doesn't know it was attempted.
- **DON'T modify entities.json or aliases.json from inside the orchestrator** even when an obvious entity is missing. Orchestrators are read-only on the entity store; data layer ownership stays with `people-crm` and `workspace-manager`.
- **DON'T create a `[Project]/` folder for a project that has no thread record.** Writing a file to `[Project]/…` brings that folder into existence on disk; doing so for an unregistered project produces an orphan folder the substrate never learns about. If routing lands on a project name with no thread, stage to `_unrouted/` and let the CEO decide whether to register it via `workspace-manager` — never let a side-effect copy register a project for you. (See workspace-ingest Phase 6.5. `integrity_check.py` C10 is the backstop.)
