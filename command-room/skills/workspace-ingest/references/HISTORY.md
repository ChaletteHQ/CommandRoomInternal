# workspace-ingest — bug & design history

Narrative context moved out of SKILL.md (A7 diet). **Rules live in SKILL.md; stories live here.**
SKILL.md keeps each rule plus a breadcrumb like `(v2.7.5 Parser C bug — see references/HISTORY.md)`.

| ID / change | Date | One-line story | Rule it produced (SKILL.md anchor) | CHANGELOG |
|---|---|---|---|---|
| context-ingestion merge | — | `workspace-ingest` (data-substrate parser) and the retired `context-ingestion` skill (random-files-into-projects sorter) were consolidated — same user mental model: "I have stuff somewhere, pull what's useful into Command Room." | Merged-skill intro / two-layer model | v2.14.20 |
| onboarding decoupling | — | Onboarding is a fresh-install performance (scan → reveal → seed → brief); auto-firing a full ingest inside it was wrong. Customers with prior data run ingest BEFORE onboarding once, or onboard fresh and ingest later. | "Onboarding does NOT auto-invoke" | v2.7.22 |
| Parser C under-extraction | — | First production ingest lost 27 of 62 people because Parser C silently under-extracted section-nested PEOPLE.md entries — there was no completeness check. | "Phase 3.5: Parse Completeness Check (hard gate)" | v2.7.5 |
| atomic-write holdout | — | Phase 4 was the lone holdout still using a hand-rolled tmp+rename write pattern against the atomic_write contract; retired so ingest matches every other writer. | "Phase 4 uses `atomic_write.py`" | v2.14.20 |
| orphan-folder hole | — | A `[Project]/…` copy destination creates that folder on disk as a side effect even when no thread record exists — producing orphan project folders invisible to every daily flow and to `go [project]`. | "Phase 6.5: Registration gate" | v3.16 |
