#!/usr/bin/env python3
"""
Shared entity resolution: resolve a free-text name reference to a person /
org / project record in `entities.json` via exact alias match → fuzzy
(edit-distance) → phonetic (Soundex) — in that order (v3.13.0+).

WHY THIS EXISTS:
  Per the 2026-05-20 Cowork handoffs #18 (transcript-search) and #31
  (workspace-manager): two skills hit the same problem class — a user types
  a name M's workspace knows about, but the skill's lookup misses because:

    - The name is a misspelling (`Elon` for `Elan`)
    - The name is phonetic (`Denari` for `Dynarii`)
    - The skill only does literal-token / exact-match lookup
    - The aliases.json entry doesn't exist YET for that specific variant

  Both skills now route through this helper. transcript-search calls it
  BEFORE the literal-text score so name-bearing queries resolve to the
  entity graph even when the literal token is absent from transcripts.
  workspace-manager calls it as a fuzzy fallback step BEFORE falling to
  the "Ambiguous → ask one question" branch, so misspelled names load
  context instead of triggering clarifying questions for facts already
  on disk.

  Same helper, two consumers, one place to update. Same cascade pattern
  as v3.13.0's other "fix once, many inherit" wins (email-writer widget
  cascade, org_writer.py).

PUBLIC API:
  - resolve(workspace_root, query) → ResolveResult | None
      Walks the 3-tier match ladder (exact → fuzzy → phonetic). Returns
      the matched entity record + match-signal explanation. None if no
      match crosses the confidence threshold.

  - resolve_all(workspace_root, query) → list[ResolveResult]
      Like `resolve` but returns ALL candidates above the threshold,
      sorted by confidence. Use for disambiguation flows ("did you mean
      Elan or Elise?").

EXACT VS FUZZY VS PHONETIC:
  - Exact: case-insensitive whitespace-normalized match against alias `raw`,
    person.canonical_name, person.aliases[], org.canonical_name, org.aliases[],
    project.canonical_name. Confidence 1.0.
  - Fuzzy: difflib.SequenceMatcher ratio ≥ 0.85 against the same surfaces.
    Confidence == the ratio (0.85 - 1.0). Catches typos like Mark→Marc.
  - Phonetic: Soundex code match against names that share a sound-alike key.
    Confidence 0.75 (constant — Soundex is binary match-or-not). Catches
    `Denari` ≈ `Dynarii`, `Elon` ≈ `Elan`.

CONFIDENCE THRESHOLDS (open question per #31 handoff):
  - 0.95+ → auto-load (highest confidence, no disambiguation needed)
  - 0.85-0.95 → load + surface "did you mean X?" inline
  - 0.75-0.85 → ask before loading
  - <0.75 → no match returned

Threshold tuning is at the consumer's discretion. The helper returns ALL
candidates with confidence; the consumer picks what to do with them.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


EntityType = Literal["person", "org", "project"]
MatchSignal = Literal["exact_alias", "exact_canonical", "fuzzy", "phonetic"]


@dataclass
class ResolveResult:
    """One candidate match for a free-text query.

    Fields:
      entity_type: kind of record ('person', 'org', 'project')
      record: the full entity dict from entities.json
      matched_via: which signal fired ('exact_alias', 'exact_canonical',
        'fuzzy', 'phonetic')
      matched_string: the alias/name string that matched
      confidence: 0.0-1.0 numeric (1.0 = exact, ~0.85-1.0 = fuzzy ratio,
        0.75 = phonetic match constant)
      reason: short plain-English explanation for surfacing to the user
        (e.g., "matched via alias 'Elan's company' to Dynarii")
    """
    entity_type: EntityType
    record: dict[str, Any]
    matched_via: MatchSignal
    matched_string: str
    confidence: float
    reason: str = field(default="")

    @property
    def entity_id(self) -> str:
        return self.record.get("id", "")

    @property
    def display_name(self) -> str:
        # For projects, prefer display_name → canonical_name → folder_name
        if self.entity_type == "project":
            return (
                self.record.get("display_name")
                or self.record.get("canonical_name")
                or self.record.get("folder_name")
                or self.record.get("id", "")
            )
        return self.record.get("canonical_name") or self.record.get("id", "")


# ---------- private helpers ----------


def _normalize(s: str) -> str:
    """Lowercase + collapse whitespace + strip. Use for comparison only;
    preserve original casing in `matched_string` field."""
    return re.sub(r"\s+", " ", s.strip().lower()) if s else ""


def _soundex(s: str) -> str:
    """Compute a 4-character Soundex code for `s`. Sound-alike strings produce
    the same code. Standard algorithm — no external deps.

    Operates on the FULL string (whitespace stripped). For multi-token names
    like `Elan Sample`, this computes the Soundex of the concatenated tokens —
    which means single-word queries like `Elon` won't match `Elan Sample`.
    For that case use `_soundex_tokens` (v3.13.2+) which returns per-token
    codes, and compare against ANY of them in the resolver.

    Examples:
      _soundex('Elan') == 'E450'
      _soundex('Elon') == 'E450'  → matches
      _soundex('Dynarii') == 'D560'
      _soundex('Denari') == 'D560'  → matches
    """
    if not s:
        return ""
    s = s.upper().strip()
    # Keep only A-Z
    s = re.sub(r"[^A-Z]", "", s)
    if not s:
        return ""

    # Soundex code map
    code_map = {
        **{c: "1" for c in "BFPV"},
        **{c: "2" for c in "CGJKQSXZ"},
        **{c: "3" for c in "DT"},
        "L": "4",
        **{c: "5" for c in "MN"},
        "R": "6",
    }

    first = s[0]
    encoded = [first]
    prev_code = code_map.get(first, "")
    for ch in s[1:]:
        code = code_map.get(ch, "")
        if code and code != prev_code:
            encoded.append(code)
        # H and W don't reset prev_code (per standard Soundex); vowels do.
        if ch not in "HW":
            prev_code = code

    result = "".join(encoded)[:4]
    # Right-pad with zeros to length 4
    return (result + "0000")[:4]


def _soundex_tokens(s: str) -> list[str]:
    """Compute the Soundex code for each whitespace-separated token in `s`.

    v3.13.2+ — closes the multi-token-name match gap. Without per-token
    Soundex, single-word misspellings like `Elon` never matched canonical
    names stored as `Elan Sample` (because the full-string Soundex of
    `Elan Sample` is `E452`, not `E450`).

    Examples:
      _soundex_tokens('Elan Sample')  == ['E450', 'S514']
      _soundex_tokens('Bo Stone')     == ['B000', 'S350']
      _soundex_tokens('Sam')          == ['S500']
      _soundex_tokens('')             == []
    """
    if not s:
        return []
    tokens = [t for t in s.strip().split() if t]
    return [_soundex(t) for t in tokens if _soundex(t)]


def _load_entities(workspace_root: Path) -> dict[str, Any]:
    path = workspace_root / "_hq" / "data" / "entities.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_aliases(workspace_root: Path) -> dict[str, Any]:
    path = workspace_root / "_hq" / "data" / "aliases.json"
    if not path.exists():
        return {"mappings": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"mappings": {}}


def _iter_alias_mappings(aliases: dict) -> list[dict]:
    """Yield every mapping entry from aliases.json regardless of shape.

    v3.13.6+ — closes the hard bug at entity_resolve.py:309 (pre-v3.13.6
    iterated `mappings` as if it were a flat list, but the canonical
    aliases.schema.json defines it as a `{people: [...], projects: [...],
    orgs: [...]}` dict. On any real workspace with the canonical shape,
    iterating yielded the string keys and `mapping.get('raw')` raised
    AttributeError, crashing every `resolve()` / `resolve_all()` call).

    Accepts BOTH shapes:
      - Canonical (v2.21+): `mappings: {people: [...], projects: [...], orgs: [...]}`
      - Legacy / fallback (older workspaces): `mappings: [...]` (flat list)
    """
    mappings = aliases.get("mappings")
    if isinstance(mappings, list):
        return [m for m in mappings if isinstance(m, dict)]
    if isinstance(mappings, dict):
        out = []
        for tier_key in ("people", "projects", "orgs"):
            tier = mappings.get(tier_key) or []
            if isinstance(tier, list):
                out.extend(m for m in tier if isinstance(m, dict))
        return out
    return []


def _unwrap_entities(entities: dict) -> dict:
    """Return the entity collections regardless of whether entities.json
    nests them under top-level (`{people: [...]}`) or under an `entities`
    wrapper (`{entities: {people: [...]}}`, the canonical schema shape).

    v3.13.6+ — closes the second half of the entity_resolve crash bug.
    Pre-v3.13.6, callers iterated `entities.get("people", [])` against the
    wrapper-shape file and got an empty list, so resolve() silently returned
    no matches.
    """
    if isinstance(entities.get("entities"), dict):
        return entities["entities"]
    return entities


def _find_entity_by_id(entities: dict, entity_id: str) -> tuple[EntityType, dict] | None:
    """Walk the three top-level collections to find a record by id."""
    e = _unwrap_entities(entities)
    if entity_id.startswith("person_"):
        for p in e.get("people", []):
            if p.get("id") == entity_id:
                return ("person", p)
    elif entity_id.startswith("org_"):
        for o in e.get("orgs", []):
            if o.get("id") == entity_id:
                return ("org", o)
    elif entity_id.startswith("project_"):
        # NB: project records live under `threads` in M's data shape, but
        # the canonical schema also names this `projects`. Try both.
        for collection in ("projects", "threads"):
            for proj in e.get(collection, []):
                if proj.get("id") == entity_id:
                    return ("project", proj)
    return None


def _iter_match_surfaces(entities: dict):
    """Yield (entity_type, record, name_string) for every name-like field
    across every entity in the workspace. The match ladder runs against
    these surfaces in order — exact, then fuzzy, then phonetic.

    v3.13.6+ — uses _unwrap_entities to handle both top-level + wrapped
    shapes (the canonical entities.schema.json nests under `entities`).
    """
    e = _unwrap_entities(entities)
    # People
    for p in e.get("people", []):
        canon = p.get("canonical_name")
        if isinstance(canon, str) and canon.strip():
            yield ("person", p, canon, "canonical")
        for alias in (p.get("aliases") or []):
            if isinstance(alias, str) and alias.strip():
                yield ("person", p, alias, "alias")
        for nick in (p.get("nicknames") or []):
            if isinstance(nick, str) and nick.strip():
                yield ("person", p, nick, "nickname")

    # Orgs
    for o in e.get("orgs", []):
        canon = o.get("canonical_name")
        if isinstance(canon, str) and canon.strip():
            yield ("org", o, canon, "canonical")
        for alias in (o.get("aliases") or []):
            if isinstance(alias, str) and alias.strip():
                yield ("org", o, alias, "alias")

    # Projects (under both `projects` and `threads` per data-shape variance)
    for collection in ("projects", "threads"):
        for proj in e.get(collection, []):
            for field_name in ("canonical_name", "display_name", "folder_name"):
                v = proj.get(field_name)
                if isinstance(v, str) and v.strip():
                    yield ("project", proj, v, "canonical")


# ---------- public API ----------


def resolve(
    workspace_root: str | Path,
    query: str,
    *,
    min_confidence: float = 0.75,
) -> ResolveResult | None:
    """Return the single best match for `query`, or None if no match crosses
    `min_confidence`. Walks the 3-tier ladder (exact → fuzzy → phonetic) and
    returns the first hit at each tier.

    For ambiguous cases (multiple candidates at the same tier), this returns
    just the top one. Callers that need disambiguation should use `resolve_all`.
    """
    candidates = resolve_all(workspace_root, query, min_confidence=min_confidence)
    return candidates[0] if candidates else None


def resolve_all(
    workspace_root: str | Path,
    query: str,
    *,
    min_confidence: float = 0.75,
    max_candidates: int = 10,
) -> list[ResolveResult]:
    """Return every match above `min_confidence`, sorted by confidence
    descending. Used for disambiguation flows.

    Match order: exact_alias > exact_canonical > fuzzy > phonetic. Within a
    tier, results are sorted by name length (shorter matches first — they're
    typically more specific).
    """
    workspace_root = Path(workspace_root)
    if not query or not query.strip():
        return []

    query_norm = _normalize(query)
    query_soundex = _soundex(query)

    entities = _load_entities(workspace_root)
    aliases = _load_aliases(workspace_root)

    results: list[ResolveResult] = []
    seen_ids: set[str] = set()  # dedupe — same entity may match via multiple surfaces

    # Tier 1a: exact match against aliases.json mappings (the canonical alias graph)
    # v3.13.6+ — uses _iter_alias_mappings to handle BOTH the canonical
    # dict-of-lists shape AND the legacy flat-list shape. See _iter_alias_mappings.
    for mapping in _iter_alias_mappings(aliases):
        raw = mapping.get("raw")
        canonical_id = mapping.get("canonical_id")
        if not isinstance(raw, str) or not isinstance(canonical_id, str):
            continue
        if _normalize(raw) != query_norm:
            continue
        if canonical_id in seen_ids:
            continue
        match = _find_entity_by_id(entities, canonical_id)
        if match is None:
            continue
        entity_type, record = match
        results.append(ResolveResult(
            entity_type=entity_type,
            record=record,
            matched_via="exact_alias",
            matched_string=raw,
            confidence=1.0,
            reason=f"matched alias {raw!r} → {record.get('canonical_name', canonical_id)}",
        ))
        seen_ids.add(canonical_id)

    # Tier 1b: exact match against entity canonical_name / aliases / nicknames
    for entity_type, record, name, source in _iter_match_surfaces(entities):
        if record.get("id") in seen_ids:
            continue
        if _normalize(name) != query_norm:
            continue
        results.append(ResolveResult(
            entity_type=entity_type,
            record=record,
            matched_via="exact_canonical" if source == "canonical" else "exact_alias",
            matched_string=name,
            confidence=1.0,
            reason=f"matched {source} {name!r} → {record.get('canonical_name', record.get('id', ''))}",
        ))
        seen_ids.add(record.get("id", ""))

    # If we have any tier-1 (exact) matches, we're done — no need to fuzzy/phonetic.
    # Exact match always wins.
    if results:
        return results[:max_candidates]

    # Tier 2: fuzzy match (edit-distance ratio)
    # Use difflib.SequenceMatcher.ratio() against every name surface.
    FUZZY_THRESHOLD = max(0.85, min_confidence)
    for entity_type, record, name, source in _iter_match_surfaces(entities):
        if record.get("id") in seen_ids:
            continue
        ratio = difflib.SequenceMatcher(None, query_norm, _normalize(name)).ratio()
        if ratio < FUZZY_THRESHOLD:
            continue
        results.append(ResolveResult(
            entity_type=entity_type,
            record=record,
            matched_via="fuzzy",
            matched_string=name,
            confidence=ratio,
            reason=(
                f"fuzzy match ({ratio:.0%} similar) to {source} {name!r} → "
                f"{record.get('canonical_name', record.get('id', ''))}"
            ),
        ))
        seen_ids.add(record.get("id", ""))

    # Tier 3: phonetic (Soundex) match
    # Only meaningful for word-like strings; skip for empty Soundex or numeric queries.
    # v3.13.2+ — single-word queries also match per-token Soundex on the candidate,
    # so "Elon" matches multi-token "Elan Sample" (matches first-token "Elan").
    PHONETIC_CONFIDENCE = 0.75
    query_is_single_token = query.strip() and len(query.strip().split()) == 1
    if query_soundex and PHONETIC_CONFIDENCE >= min_confidence:
        for entity_type, record, name, source in _iter_match_surfaces(entities):
            if record.get("id") in seen_ids:
                continue
            name_soundex = _soundex(name)
            full_match = name_soundex and name_soundex == query_soundex
            # Per-token match: single-word query against any token of a
            # multi-token candidate name (e.g., `Elon` vs `Elan Sample`).
            token_match = False
            matched_token = None
            if query_is_single_token and not full_match:
                for token_code, token_str in zip(_soundex_tokens(name), name.strip().split()):
                    if token_code == query_soundex:
                        token_match = True
                        matched_token = token_str
                        break
            if full_match or token_match:
                if token_match:
                    reason = (
                        f"phonetic match (sound-alike) to {source} token "
                        f"{matched_token!r} in {name!r} → "
                        f"{record.get('canonical_name', record.get('id', ''))}"
                    )
                else:
                    reason = (
                        f"phonetic match (sound-alike) to {source} {name!r} → "
                        f"{record.get('canonical_name', record.get('id', ''))}"
                    )
                results.append(ResolveResult(
                    entity_type=entity_type,
                    record=record,
                    matched_via="phonetic",
                    matched_string=name,
                    confidence=PHONETIC_CONFIDENCE,
                    reason=reason,
                ))
                seen_ids.add(record.get("id", ""))

    # Sort by confidence descending, then by length of matched string ascending
    # (shorter typically more specific).
    results.sort(key=lambda r: (-r.confidence, len(r.matched_string)))
    return results[:max_candidates]


def resolve_to_linked_project(
    workspace_root: str | Path,
    query: str,
    *,
    min_confidence: float = 0.75,
) -> ResolveResult | None:
    """Convenience: resolve `query` via `resolve()`. If the match is a person
    or org, walk to their linked project (the most recently active project
    where they're an attendee/key contact).

    Used by workspace-manager loose-input matching: when M says "go Elan",
    Elan resolves to person_072, and the helper walks to project_020
    (Dynarii — COO Partnership, the most-recently-active project where Elan
    is the key contact).

    Returns the linked PROJECT match (with `entity_type='project'`), or None
    if no project link can be resolved.
    """
    workspace_root = Path(workspace_root)
    base = resolve(workspace_root, query, min_confidence=min_confidence)
    if base is None:
        return None
    if base.entity_type == "project":
        return base

    entities = _unwrap_entities(_load_entities(workspace_root))

    # If person: find projects where this person is key_contact_id OR where
    # they're in the project's affiliated org. Pick most recently active.
    if base.entity_type == "person":
        person_id = base.record.get("id")
        primary_org_id = base.record.get("primary_org_id")
        candidates = []
        for collection in ("projects", "threads"):
            for proj in entities.get(collection, []):
                if proj.get("status") == "archived":
                    continue
                if proj.get("key_contact_id") == person_id:
                    candidates.append(proj)
                elif primary_org_id and proj.get("affiliation_id") == primary_org_id:
                    candidates.append(proj)
                elif primary_org_id and proj.get("org_id") == primary_org_id:
                    candidates.append(proj)
        if not candidates:
            return None
        # Sort by last_activity descending
        candidates.sort(key=lambda p: p.get("last_activity") or p.get("first_seen") or "", reverse=True)
        proj = candidates[0]
        return ResolveResult(
            entity_type="project",
            record=proj,
            matched_via=base.matched_via,
            matched_string=base.matched_string,
            confidence=base.confidence * 0.95,  # tiny penalty for indirect resolution
            reason=(
                f"{base.reason} (walked to project "
                f"{proj.get('canonical_name', proj.get('id', ''))} via key_contact / affiliation)"
            ),
        )

    # If org: pick most recently active project under that org
    if base.entity_type == "org":
        org_id = base.record.get("id")
        candidates = []
        for collection in ("projects", "threads"):
            for proj in entities.get(collection, []):
                if proj.get("status") == "archived":
                    continue
                if proj.get("affiliation_id") == org_id or proj.get("org_id") == org_id:
                    candidates.append(proj)
        if not candidates:
            return None
        candidates.sort(key=lambda p: p.get("last_activity") or p.get("first_seen") or "", reverse=True)
        proj = candidates[0]
        return ResolveResult(
            entity_type="project",
            record=proj,
            matched_via=base.matched_via,
            matched_string=base.matched_string,
            confidence=base.confidence * 0.95,
            reason=(
                f"{base.reason} (walked to project "
                f"{proj.get('canonical_name', proj.get('id', ''))} via affiliation)"
            ),
        )

    return None


# ---------- CLI ----------


def main() -> int:
    """CLI for bash callers and quick testing:
        python3 entity_resolve.py <workspace_root> <query>
        python3 entity_resolve.py <workspace_root> <query> --all
        python3 entity_resolve.py <workspace_root> <query> --to-project
    """
    import argparse
    parser = argparse.ArgumentParser(description="Resolve a name to an entity record.")
    parser.add_argument("workspace_root", type=Path)
    parser.add_argument("query", type=str, help="Free-text name to resolve.")
    parser.add_argument("--all", action="store_true", help="Return all candidates, not just best.")
    parser.add_argument("--to-project", action="store_true", help="Walk to linked project.")
    parser.add_argument("--min-confidence", type=float, default=0.75)
    args = parser.parse_args()

    if args.to_project:
        result = resolve_to_linked_project(args.workspace_root, args.query, min_confidence=args.min_confidence)
        if result is None:
            print(f"no match for {args.query!r}")
            return 1
        print(f"{result.entity_type} {result.entity_id} ({result.display_name})")
        print(f"  via: {result.matched_via} (conf {result.confidence:.2f})")
        print(f"  reason: {result.reason}")
        return 0

    if args.all:
        results = resolve_all(args.workspace_root, args.query, min_confidence=args.min_confidence)
    else:
        single = resolve(args.workspace_root, args.query, min_confidence=args.min_confidence)
        results = [single] if single else []

    if not results:
        print(f"no match for {args.query!r}")
        return 1

    for r in results:
        print(f"{r.entity_type} {r.entity_id} ({r.display_name})")
        print(f"  via: {r.matched_via} (conf {r.confidence:.2f})")
        print(f"  matched_string: {r.matched_string!r}")
        print(f"  reason: {r.reason}")
        print()
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main())
