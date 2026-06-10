# Intel Intake — Reference Guide

## Gotchas & Common Failures

### 1. X/Twitter Cannot Be WebFetched
- **Problem**: WebFetch fails on X.com or twitter.com URLs.
- **Solution**: Use WebSearch with `site:x.com` or `site:twitter.com` to find the post.
- **Workaround**: User can paste the tweet text directly; I'll analyze it.

### 2. YouTube Transcripts May Not Be Available
- **Problem**: Not all videos have transcripts (e.g., live streams, some older videos).
- **Solution**: Fall back to description + top comments for context.
- **Fallback**: Ask user if they can provide a summary or key timestamps.

### 3. Knowledge Base Bloat
- **Problem**: Logging too much low-value content dilutes the knowledge base.
- **Solution**: Be selective. Only log genuine, new, business-relevant insights.
- **Check**: Before logging, ask: "Is this materially new or genuinely useful?"

### 4. Stale Business Context
- **Problem**: BUSINESS_CONTEXT.md outdated; projects, goals, or operations have changed.
- **Solution**: Ask user to update context before analysis.
- **Flag**: Check "Last Updated" date in BUSINESS_CONTEXT.md; prompt refresh if >30 days old.

### 5. Broken Path References
- **Problem**: Hardcoded paths break if workspace moved.
- **Solution**: Always use `[WORKSPACE_ROOT]` as placeholder. Resolve at runtime.

### 6. Missing Files
- **Problem**: BUSINESS_CONTEXT.md or intel folder doesn't exist yet.
- **Solution**: Offer to scaffold the workspace or guide user through setup.

### 7. Over-Analyzing Low-Confidence Content
- **Problem**: Spending effort on unverified claims.
- **Solution**: Flag confidence level prominently. De-prioritize unverified intel for action.

### 8. Source Credibility
- **Problem**: Including intel from non-credible sources.
- **Solution**: Always note source credibility. Prefer primary sources, established publications, direct announcements.
- **Red flags**: No author, no date, no publication context.

---

## Tips for Effective Intel Intake

1. **Daily habit**: Feed me one article or note daily. Intel compounds over time.
2. **Tag it as you go**: "This is about hiring" or "This could help Project X" as you paste.
3. **Review quarterly**: Periodically search KNOWLEDGE_BASE.md to spot emerging patterns.
4. **Refresh context**: Update BUSINESS_CONTEXT.md when projects shift or goals change.
5. **Act on high-confidence, high-impact items fast**: Don't let actionable intel sit.
6. **Use "what do we know about X"**: Before starting a new initiative, ask what you've already learned.

---

## Error Handling

| Error | What to Do |
|-------|-----------|
| "Context file not found" | Offer to help set up BUSINESS_CONTEXT.md |
| "WebFetch failed on X.com" | Use WebSearch with site:x.com instead |
| "Transcript not available" | Analyze description + comments, flag as "description_only" |
| "URL returns 404" | Ask user for alternative source or pasted content |
| "Content older than 90 days" | Flag age; ask if still relevant to current projects |
| "Can't determine project relevance" | Ask user: "Which project(s) does this relate to?" |

---

## Example Workflow

**Input**: User pastes a news article about new Claude feature.

```
1. Load BUSINESS_CONTEXT.md → Understand user's business, active projects
2. Load KNOWLEDGE_BASE.md → Check if Claude feature already known
3. Extract → Title, author, date, key claims, confidence
4. Analyze → Is this new? How does it affect projects? Actionable?
5. Format → TL;DR, actionable items, good to know
6. Save → [WORKSPACE_ROOT]/_hq/intel/2026-04-08-claude-feature.md
7. Update → INDEX.md and KNOWLEDGE_BASE.md
8. Output → Show user summary + next steps
```

**Example output**:
```
Intel Processed

Title: Claude 3.5 Vision Now Available

TL;DR: Claude gains native vision capabilities. Directly unlocks image analysis for [Project X].

What's Actionable:
- Test vision on [Project X] use case | Effort: Low | Priority: High | Confidence: Verified
- Update [Project X] spec to include vision | Effort: Medium | Priority: High

What's Good to Know:
- Vision cost structure is [X] tokens per image
- Latency ~[X]ms for standard images

Already Covered:
- [Project X] roadmap already earmarked vision for Q3; this just accelerates timeline

Logged to: [WORKSPACE_ROOT]/_hq/intel/2026-04-08-claude-vision-available.md
Status: unreviewed
```

---

## When to Use This Skill

**Use intel-intake for**: New tools/frameworks relevant to your business, competitor moves, market trends, research on hiring/scaling, cost/efficiency breakthroughs, industry standard shifts, anything you read and think "this could affect us."

**Skip intel-intake for**: Generic company news with no business relevance, off-topic content, duplicate analysis (check KNOWLEDGE_BASE.md first), content already triaged and actioned.
