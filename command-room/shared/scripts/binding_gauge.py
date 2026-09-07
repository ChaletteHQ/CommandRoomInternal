#!/usr/bin/env python3
"""READER1 — the binding-gauge writer (SPEC_READER1 §0.3 / R0, recalibrated §5c.1–2).

Computes per-thread binding coverage and persists it to
`_hq/data/binding_gauge.json`, in the schema
`load_thread_knowledge._load_gauge` consumes (the §5c.2 person-binding keys
are a COMPATIBLE EXTENSION — the reader tolerates and passes them through):

    {
      "generated": "<ISO timestamp>",
      "events_max_seq": <max human seq at measurement time, or null>,
      "meetings_person_bound_pct": <float 0..1 | null>,   # §5c.2, global
      "threads": {
        "<thread_id>": {
          "ready": <bool>,
          "ratio": <float 0..1, or null when unmeasurable>,
          "substance_bound": <int>,
          "person_binding": <float 0..1 | null>            # §5c.2, per thread
        },
        ...
      }
    }

WHY PERSISTED (§0.3, do not relitigate): a read-time coverage score is
structurally blind to misbinding — a reader cannot count events bound to the
WRONG thread. This writer measures ON ITS OWN SCHEDULE (audit / maintenance),
stamps the events high-water mark it measured at, and the reader only reports
the verdict plus read-time staleness (current max seq above `events_max_seq`).

METHODOLOGY — the gauge audit's, replicated EXACTLY (§5c.1: the first shipped
cut of this module diverged from the audit on the denominator and the terms
rule and returned 1/43 READY where the audit measured 14/23 active; that
near-universal not_ready would have put every surface in permanent ask-first
and drowned the loud-degrade signal. Recalibrated 2026-08-28 and verified
against the live workspace: READY set == the audit's, per-thread unbound
counts reproduce).

  substance_bound  Count of substance events (substance_events families —
                   the record of the work, never system bookkeeping) bound to
                   the thread via `event_refs.threads_of` — the §0.2 pinned
                   membership notion, never source refs.

  ratio            substance_bound / (substance_bound + name_unbound), where
                   name_unbound counts the substance events whose serialized
                   JSON carries one of the thread's DISTINCTIVE name terms
                   but are NOT bound to the thread. Low ratio = events that
                   look like this thread's work are bound elsewhere or
                   unbound — exactly the misbinding a read-time score cannot
                   see. Zero bound AND zero name hits -> ratio is null
                   (unmeasurable, never 1.0).

  DISTINCTIVE-NAME-TERMS RULE (the audit's, exactly): candidate terms are
  whole PHRASES — canonical_name, canonical_name with any parenthetical
  stripped, folder_name, and every aliases.json raw mapped to the thread —
  lowercased, minimum 4 chars, generic terms dropped (see GENERIC TERMS
  below). A term OWNED by 2+ threads is then dropped from EVERY thread's
  set, where ownership = the term matches (word-boundary) inside a thread's
  own name/alias corpus. This is what disarms both ambiguity shapes: a
  shared alias phrase ("command room" names four threads) and a generic name
  WORD ("partnership" sits inside three canonical names). Without it, one
  thread's events land in another thread's denominator and the gauge reads
  binding drift where there is none.

  GENERIC TERMS — a UNIVERSAL base plus a PER-WORKSPACE derivation
  (GAUGECAL1, closing REVIEW_READER1_DELTA N-2). The shipped list was one
  static set calibrated on the operator's own workspace, and it carried the
  vendor's brand name as a literal. On a client workspace that set is
  wrong-shaped in BOTH directions at once: THEIR company name is missing
  (so their own brand — the word sitting inside half their thread names —
  scores as a distinctive term, and every thread inherits every other
  thread's events into its denominator, reading as binding drift on a
  correctly-bound workspace), while OURS is uselessly present. Two layers
  now:

    * UNIVERSAL_GENERIC_TERMS — only what names nothing in ANY workspace:
      articles, container words ("team", "internal", "project" — they name
      a thread's SHAPE, never the thread), and generic org SUFFIXES ("inc",
      "group", "holdings" — a company form, never a company). No brand, no
      person, no client, nothing workspace-specific. The vendor brand
      literal is GONE from it and is re-derived on the operator's own
      workspace by rule (a) below, which is why the operator's READY set is
      unchanged (see handoffs/BUILD_GAUGECAL1 parity table).
    * workspace_generic_terms(entities) — rule (a): the workspace's OWN
      org/brand name(s) are generic WITHIN that workspace. Read from the
      `workspace` block's name keys, from every org whose
      `relationship_type` is "self" (the canonical self-org idiom this
      codebase already uses), and from the primary user's own org. Nobody
      lists anything and no workspace is named in code: every workspace,
      the operator's included, has its own brand disarmed by the same rule
      reading its own register.

  Rule (b) — a phrase owned by 2+ threads is dropped from every set — is
  the pre-existing ownership rule above, unchanged, and it keeps carrying
  the ambiguity cases the derivation does not reach.

  Name matching runs word-boundary over the event's serialized JSON line
  (`ensure_ascii=True` — the audit matched the raw file lines, and the
  substrate is written with ascii escapes), substance events only.

  RECLASSIFICATIONS FOLD FIRST (GAUGEJOB1 scope addition, SPEC_BACKFILL1
  §write-shape): the walk measures the CORRECTED bindings — the RECL1
  `apply_reclassifications` fold runs before any counting, so an event
  re-bound through the correction rail counts for its true thread and
  stops counting for the thread it was corrected away from. See the
  inline comment in `build_gauge`.

  person_binding (§5c.2)  Of the thread's bound `type: "meeting"` events,
  the fraction with at least one resolved person id via the canonical
  `event_refs.meeting_person_ids` reader + the entities email index; null
  when the thread has no bound meetings. `meetings_person_bound_pct` is the
  same fraction over ALL meeting events (the audit's global figure). Without
  this the artifact is blind to the unbound-attendees capture-defect class.

  READY = substance_bound >= READY_MIN_SUBSTANCE (default 20) AND ratio >=
  READY_MIN_RATIO (default 0.75). Both defaults are the gauge audit's, and
  both are now WORKSPACE KNOBS (GAUGECAL1) read through the skill_config
  layer's `binding_gauge` store — `ready_thresholds(workspace_root)`, keys
  `ready_min_substance` / `ready_min_ratio`. The audit that set 20/0.75 was
  the OPERATOR's; a workspace with a shorter ledger or a different capture
  cadence has no reason to inherit it, and before GAUGECAL1 the only way to
  move the bar was to edit shipped code. A thread failing either is
  NOT-READY and the reader's ask-first posture keys on that verdict (on
  READY threads only until R3 — the §5c.5 line-71 ruling, recorded in the
  reader), so the knob moves `load_thread_knowledge`'s `gauge.state` with
  it: the reader trusts the artifact's verdict and never re-derives, which
  is exactly what makes one knob, read once at measurement time, enough.

Privacy: this is substrate infra measuring the OWNER's whole ledger — it
reads through `events_io.load_events_owner_scoped` (the allowlisted shard
reader's owner-tier seam; an org-scoped read would hide personal-lane rows
from the denominator and overstate coverage). Its output carries NO event
content — ids, counts and ratios only.

CLI:
    python binding_gauge.py <workspace_root> [--dry-run]
    python binding_gauge.py <workspace_root> --job [--apply] [--fired-via X]

--dry-run computes and prints the artifact without writing anything.
--job is the GAUGEJOB1 maintenance-job entry (`run_gauge_refresh_job`):
without --apply it dry-runs (no write, no receipt, job stays due); with
--apply, a CHANGE run writes its `binding-gauge` pack_run receipt and then
the refreshed artifact, and a QUIET run (measurement identical to the
artifact on disk) leaves no trace at all — see the QUIET-RUN SEMANTICS
note below.
stdlib + sibling shared/scripts modules only.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import event_refs
    import events_io
    import substance_events
    from entities_io import entities_collection, unwrap_entities
    from thread_activity import apply_reclassifications
except ImportError:  # pragma: no cover — direct-path fallback
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import event_refs
    import events_io
    import substance_events
    from entities_io import entities_collection, unwrap_entities
    from thread_activity import apply_reclassifications

__all__ = [
    "GAUGE_RELPATH",
    "GAUGE_JOB_ID",
    "GAUGE_RECEIPT_TYPE",
    "GAUGE_CONFIG_STORE",
    "GAUGE_CONFIG_DEFAULTS",
    "config_defaults",
    "ready_thresholds",
    "READY_MIN_SUBSTANCE",
    "READY_MIN_RATIO",
    "UNIVERSAL_GENERIC_TERMS",
    "GENERIC_TERMS",
    "SELF_ORG_RELATIONSHIPS",
    "workspace_generic_terms",
    "generic_terms_for",
    "TERM_MIN_LEN",
    "distinctive_terms",
    "terms_regexes",
    "match_name_unbound",
    "event_match_line",
    "max_human_seq",
    "build_gauge",
    "write_gauge",
    "run_gauge_refresh_job",
]

# Must stay identical to load_thread_knowledge.GAUGE_RELPATH — producer and
# consumer address the same artifact.
GAUGE_RELPATH = ("_hq", "data", "binding_gauge.json")

# GAUGEJOB1 — the maintenance JOB identity (never a task of its own; rides the
# already-authorized `maintenance` taskId, dispatched by
# maintenance_dispatcher.MAINTENANCE_JOBS["binding-gauge"]). The receipt is
# the standard scheduled-job pack_run and it is the job's dueness signal.
GAUGE_JOB_ID = "binding-gauge"
GAUGE_RECEIPT_TYPE = "pack_run"

# READY thresholds — the gauge audit's values, and (GAUGECAL1) the DEFAULTS
# of two per-workspace knobs rather than constants of the product. The audit
# that produced 20 / 0.75 measured the OPERATOR's ledger; a workspace with a
# shorter history, a different capture cadence, or a deliberately stricter bar
# has no reason to inherit that number, and until now the only way to move it
# was to edit shipped code. Read through `ready_thresholds(workspace_root)`;
# these names stay as the fallbacks (and `backfill_bindings` still reads them
# for its k-to-READY arithmetic when it has no workspace in hand).
READY_MIN_SUBSTANCE = 20
READY_MIN_RATIO = 0.75

#: The skill_config store these knobs live in — `_hq/data/skill_config/
#: binding_gauge.json`. A snake_case store name because the gauge is substrate
#: infra, not a skill (the `output_profile` / `chat_persona` / `brain_render`
#: precedent). Registered in shared/data-schemas/skill_config.schema.json, so
#: an unknown key is refused LOUDLY at write.
GAUGE_CONFIG_STORE = "binding_gauge"
GAUGE_CONFIG_DEFAULTS = {
    "ready_min_substance": READY_MIN_SUBSTANCE,  # int >= 1
    "ready_min_ratio": READY_MIN_RATIO,          # float in (0, 1]
}

EPOCH_THRESHOLD = 10 ** 10   # legacy nano-epoch seqs are not a high-water mark

# ---------------------------------------------------------------------------
# GENERIC TERMS — a universal base + a per-workspace derivation (GAUGECAL1)
# ---------------------------------------------------------------------------
#
# See the module docstring's GENERIC TERMS section for the why. What is left
# here is only what names nothing in ANY workspace. Nothing in this set is a
# brand, a person, a client, or otherwise true of one workspace and false of
# the next — that is the whole test for membership, and the reason the vendor
# brand literal that shipped in the old list is gone (it is re-derived on the
# operator's own workspace by `workspace_generic_terms`, rule (a)).
#
# Matching is WHOLE-PHRASE: a candidate phrase is dropped only when the entire
# phrase equals one of these. "Acme Industries" keeps its term; a thread whose
# whole name is "Industries" does not. Terms below TERM_MIN_LEN are already
# dropped by the length rule and are listed only so the base reads as a
# complete statement of what "generic" means.
UNIVERSAL_GENERIC_TERMS = frozenset({
    # articles and connectives
    "the", "and", "for", "with", "app",
    # container words — they name a thread's SHAPE, never the thread
    "room", "core", "plan", "team", "work", "notes", "misc", "other",
    "admin", "general", "internal", "product", "project", "personal",
    "workspace", "initiative", "workstream",
    # generic org suffixes — a company FORM, never a company
    "inc", "llc", "ltd", "plc", "corp", "co", "company", "group",
    "holdings", "partners", "ventures", "capital", "labs", "studio",
    "agency", "enterprises", "solutions", "services", "systems",
    "technologies", "consulting",
})

#: Back-compat name. It used to BE the whole rule; it now names the universal
#: base only, and the workspace layer arrives through `generic_terms_for`.
GENERIC_TERMS = UNIVERSAL_GENERIC_TERMS

#: `orgs[].relationship_type` values that mean "this org IS the workspace".
#: The spelling this codebase already keys on (capture_gate's team roster,
#: end_of_day's arc builder). Note it is off-enum in entities.schema.json —
#: read-side tolerance, exactly as those two readers do it.
SELF_ORG_RELATIONSHIPS = frozenset({"self"})

#: `workspace`-block keys that would carry the workspace's own org/brand name
#: if a workspace has one. None is written by anything today (the self-org
#: record is where the name actually lives) — read tolerantly so a workspace
#: that DOES carry one is not silently ignored.
WORKSPACE_NAME_KEYS = ("org_name", "company", "company_name", "org",
                       "brand_name", "workspace_name")

TERM_MIN_LEN = 4

#: Compound-name separators. A name like "Sample Air / Stone Clean Group"
#: names ONE org by two names (a live convention — a rename or an acquisition
#: that kept both); both halves are that org, so both are generic in its
#: workspace.
_NAME_SPLIT_RE = re.compile(r"\s*[/|]\s*|\s+—\s+|\s+--\s+")

_PAREN_RE = re.compile(r"\s*\(.*?\)\s*")


def _term_pattern(term: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term)
                      + r"(?![A-Za-z0-9])")


def _terms_regex(terms: set[str]) -> re.Pattern:
    return re.compile("|".join(
        r"(?<![A-Za-z0-9])" + re.escape(s) + r"(?![A-Za-z0-9])"
        for s in sorted(terms, key=len, reverse=True)))


def _name_variants(raw) -> set[str]:
    """Every lowercased spelling of ONE org/brand name worth treating as that
    name: the whole phrase, the phrase with any parenthetical stripped, and
    each half of a compound ("A / B"). Pieces below TERM_MIN_LEN are dropped
    — a two-letter brand cannot be told apart from noise by this instrument,
    and dropping it errs toward measuring rather than toward a blanket."""
    out: set[str] = set()
    if not isinstance(raw, str):
        return out
    whole = raw.strip()
    if not whole:
        return out
    for form in (whole, _PAREN_RE.sub(" ", whole)):
        for piece in [form] + _NAME_SPLIT_RE.split(form):
            s = " ".join(piece.lower().split())
            if len(s) >= TERM_MIN_LEN:
                out.add(s)
    return out


def workspace_generic_terms(entities: dict | None) -> frozenset[str]:
    """GAUGECAL1 rule (a) — the names that are GENERIC *within this workspace*.

    A workspace's OWN org/brand name is not a distinctive thread term: it sits
    inside its own thread names, its own meeting titles, its own email
    subjects and its own signature block. Left distinctive it behaves exactly
    like a misbinding signal — every event that merely mentions the company
    lands in the denominator of whichever thread happens to own the brand
    word, and a correctly-bound client workspace reads as drifting. This is
    the fan-out half of REVIEW_READER1_DELTA N-2: the old static list had the
    VENDOR's brand in it, which is right for exactly one workspace on earth.

    Three sources, unioned, all optional and all defensively read (a
    workspace that answers none of them simply gets the universal base):

      1. the `workspace` block's name keys (WORKSPACE_NAME_KEYS, plus a
         `brand` that is a bare string or carries a `name`);
      2. every org whose `relationship_type` is "self" — the self-org idiom
         capture_gate and end_of_day already key on;
      3. the org of the PRIMARY USER, via the canonical `primary_user`
         resolver. On a workspace that never labelled a self-org this is
         usually the only signal; where both exist they agree.

    Accepts the raw entities.json dict or an already-unwrapped container.
    Never raises: a malformed entities file costs the derivation, never the
    measurement."""
    if not isinstance(entities, dict):
        return frozenset()
    try:
        ent = unwrap_entities(entities)
    except Exception:  # noqa: BLE001 — defensive: a weird doc is no doc
        return frozenset()
    if not isinstance(ent, dict):
        return frozenset()

    names: set[str] = set()

    # (1) the workspace block — merged across both live shapes (top-level and
    # nested), the primary_user.py precedence.
    ws: dict = {}
    for block in (entities.get("workspace"), ent.get("workspace")):
        if isinstance(block, dict):
            ws.update(block)
    for key in WORKSPACE_NAME_KEYS:
        v = ws.get(key)
        if isinstance(v, str):
            names.add(v)
    brand = ws.get("brand")
    if isinstance(brand, str):
        names.add(brand)
    elif isinstance(brand, dict) and isinstance(brand.get("name"), str):
        names.add(brand["name"])

    # (2) + (3) — the org records.
    try:
        orgs = [o for o in entities_collection(ent, "orgs")
                if isinstance(o, dict)]
    except Exception:  # noqa: BLE001
        orgs = []

    def _org_names(o: dict) -> None:
        for k in ("canonical_name", "name"):
            v = o.get(k)
            if isinstance(v, str):
                names.add(v)
        for a in o.get("aliases") or []:
            if isinstance(a, str):
                names.add(a)

    for o in orgs:
        rel = str(o.get("relationship_type") or "").strip().lower()
        if rel in SELF_ORG_RELATIONSHIPS:
            _org_names(o)

    try:
        from primary_user import resolve_primary_user_from_entities
        uid = resolve_primary_user_from_entities(ent)
    except Exception:  # noqa: BLE001 — the resolver is best-effort here
        uid = None
    if uid:
        try:
            people = [p for p in entities_collection(ent, "people")
                      if isinstance(p, dict)]
        except Exception:  # noqa: BLE001
            people = []
        oid = next((p.get("org_id") for p in people if p.get("id") == uid),
                   None)
        if oid:
            for o in orgs:
                if o.get("id") == oid:
                    _org_names(o)

    out: set[str] = set()
    for n in names:
        out |= _name_variants(n)
    return frozenset(out)


def generic_terms_for(entities: dict | None) -> frozenset[str]:
    """THE generic-term set for one workspace: the universal base plus that
    workspace's own derived names. The single place the two layers combine —
    `distinctive_terms` and every test read it through here."""
    return frozenset(UNIVERSAL_GENERIC_TERMS | workspace_generic_terms(entities))


def _alias_raws_by_thread(aliases_doc: dict | None) -> dict[str, list[str]]:
    """{thread_id: [raw alias phrase, ...]} from an aliases.json document.

    Tolerates both observed mapping shapes: the live flat list
    (`mappings: [{raw, canonical_id, ...}, ...]`) and the sectioned dict
    (`mappings: {people: [...], orgs: [...], threads: [...]}`)."""
    out: dict[str, list[str]] = {}
    if not isinstance(aliases_doc, dict):
        return out
    m = aliases_doc.get("mappings")
    if isinstance(m, dict):
        rows = [x for v in m.values() if isinstance(v, list)
                for x in v if isinstance(x, dict)]
    elif isinstance(m, list):
        rows = [x for x in m if isinstance(x, dict)]
    else:
        rows = []
    for row in rows:
        cid = row.get("canonical_id")
        raw = row.get("raw")
        if isinstance(cid, str) and isinstance(raw, str) and raw.strip():
            out.setdefault(cid, []).append(raw.strip())
    return out


def _candidate_phrases(thread: dict, alias_raws: dict[str, list[str]]) -> set[str]:
    out: set[str] = set()
    cn = str(thread.get("canonical_name") or "").strip()
    fn = str(thread.get("folder_name") or "").strip()
    for s in (cn, _PAREN_RE.sub(" ", cn).strip(), fn):
        if s:
            out.add(s)
    for raw in alias_raws.get(thread.get("id") or "", []):
        out.add(raw)
    return out


def distinctive_terms(threads: list[dict],
                      aliases_doc: dict | None = None,
                      *,
                      entities: dict | None = None,
                      generic_terms: frozenset[str] | set[str] | None = None,
                      ) -> dict[str, set[str]]:
    """{thread_id: distinctive name-term set}, per the audit's rule.

    Phrase candidates (names + aliases), lowercased, >= TERM_MIN_LEN chars,
    generic terms dropped; then any term owned by 2+ threads is dropped from
    EVERY thread's set — ownership meaning the term matches (word-boundary)
    inside a thread's own name/alias corpus, so both a shared alias phrase
    and a name WORD shared across canonical names disarm.

    `entities` (GAUGECAL1) is the workspace's entities doc, from which the
    workspace's own org/brand names are derived and treated as generic — see
    `workspace_generic_terms`. Omitting it yields the universal base alone,
    which is the OLD static behaviour minus the vendor brand literal; every
    in-repo caller that has a workspace passes it. `generic_terms` overrides
    both layers outright and exists for the mutation test that proves rule
    (a) is load-bearing."""
    generic = (frozenset(generic_terms) if generic_terms is not None
               else generic_terms_for(entities))
    alias_raws = _alias_raws_by_thread(aliases_doc)
    raw: dict[str, set[str]] = {}
    corpus: dict[str, str] = {}
    for t in threads:
        tid = t.get("id")
        if not tid:
            continue
        phrases = _candidate_phrases(t, alias_raws)
        corpus[tid] = " | ".join(sorted(p.lower() for p in phrases))
        raw[tid] = {p.lower().strip() for p in phrases
                    if len(p.strip()) >= TERM_MIN_LEN
                    and p.lower().strip() not in generic}
    ambiguous: set[str] = set()
    for term in set().union(*raw.values()) if raw else set():
        rx = _term_pattern(term)
        owners = sum(1 for c in corpus.values() if rx.search(c))
        if owners >= 2:
            ambiguous.add(term)
    return {tid: toks - ambiguous for tid, toks in raw.items()}


def terms_regexes(threads: list[dict],
                  aliases_doc: dict | None = None,
                  *,
                  entities: dict | None = None,
                  generic_terms: frozenset[str] | set[str] | None = None,
                  ) -> dict[str, re.Pattern]:
    """{thread_id: compiled distinctive-terms regex}, threads with no
    distinctive terms omitted. THE one place the term-set-to-regex step
    lives: `build_gauge` walks with these, and the R3 backfill
    (`backfill_bindings.py`) derives its candidate work list from the SAME
    call — producer and measurer can never drift on what "name-matched"
    means (SPEC_BACKFILL1 §M.1). `entities` rides through to
    `distinctive_terms` for the same reason: the workspace-derived generic
    terms MUST be identical on both sides or the backfill would propose
    exactly the rows the gauge refuses to count."""
    return {tid: _terms_regex(toks)
            for tid, toks in distinctive_terms(
                threads, aliases_doc, entities=entities,
                generic_terms=generic_terms).items()
            if toks}


def event_match_line(ev: dict) -> str:
    """The exact string the gauge name-matches against: the event serialized
    the way the substrate writes it (ascii escapes — the audit matched raw
    file lines), lowercased. Shared with the R3 backfill for the same
    no-second-implementation reason as `terms_regexes`."""
    return json.dumps(ev, ensure_ascii=True, default=str).lower()


def match_name_unbound(line: str, refs: set,
                       regex_by_tid: dict[str, re.Pattern]) -> dict[str, str]:
    """{thread_id: matched term text} for every thread whose distinctive-name
    regex hits `line` while the event is NOT bound to it (`refs` = the
    event's own thread refs). The gauge's name-matched-unbound test,
    extracted verbatim so the R3 backfill's candidate rows and the gauge's
    `name_unbound` denominator are the SAME computation (SPEC_BACKFILL1
    §M.1 — no second matching implementation). The matched term is returned
    for evidence display; the gauge itself only counts keys."""
    out: dict[str, str] = {}
    for tid, rx in regex_by_tid.items():
        if tid in refs:
            continue
        m = rx.search(line)
        if m:
            out[tid] = m.group(0)
    return out


def _max_human_seq(events: list[dict]):
    latest = None
    for ev in events:
        s = ev.get("seq")
        if isinstance(s, bool) or not isinstance(s, int):
            continue
        if s >= EPOCH_THRESHOLD:
            continue
        if latest is None or s > latest:
            latest = s
    return latest


#: Public alias — the R3 backfill stamps its propose snapshot and checks
#: apply-time drift against the SAME high-water-mark rule the gauge stamps
#: into `events_max_seq` (SPEC_BACKFILL1 §M.2 / §R.4).
max_human_seq = _max_human_seq


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def config_defaults() -> dict:
    """A fresh copy of the READY-threshold knob defaults. Never hand back the
    module dict — a caller mutating it would move the default for the whole
    process (the held_tier.config_defaults rule)."""
    return dict(GAUGE_CONFIG_DEFAULTS)


def ready_thresholds(workspace_root) -> tuple[int, float]:
    """GAUGECAL1 — `(min_substance, min_ratio)` for THIS workspace.

    Reads the `binding_gauge` skill_config store, falling back to the audit's
    20 / 0.75 on anything that is not a sane value. Every guard fails toward
    the DEFAULT rather than toward a permissive bar: a garbled config must
    never quietly declare threads READY that the audit's rule would not, and
    a READY thread is the one that turns OFF the reader's ask-first posture.

    `bool` is checked before `int` on purpose — `True` is an `int` in Python
    and would otherwise sail through as a substance floor of 1."""
    defaults = (READY_MIN_SUBSTANCE, READY_MIN_RATIO)
    try:
        from skill_config_writer import get_config
        cfg = get_config(workspace_root, GAUGE_CONFIG_STORE, config_defaults())
    except Exception:  # noqa: BLE001 — a config read never costs a measurement
        return defaults

    sub = cfg.get("ready_min_substance")
    if isinstance(sub, bool) or not isinstance(sub, int) or sub < 1:
        sub = READY_MIN_SUBSTANCE

    ratio = cfg.get("ready_min_ratio")
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) \
            or not (0 < float(ratio) <= 1):
        ratio = READY_MIN_RATIO

    return int(sub), float(ratio)


def build_gauge(workspace_root: str | Path, *,
                now_iso: str | None = None,
                side: dict | None = None) -> dict:
    """Compute the gauge artifact dict (no write). `now_iso` is a test seam
    for the `generated` stamp (and, QUIET1, for the interaction leg's
    "today"). `side`, when a dict is handed in, receives what the artifact
    itself must not carry: `{"interaction_transition": <dict|None>}` — the
    posture move the interaction leg found against the artifact on disk,
    which `run_gauge_refresh_job` receipts."""
    root = Path(workspace_root)
    ent_doc = _read_json(root / "_hq" / "data" / "entities.json")
    ent = unwrap_entities(ent_doc) if isinstance(ent_doc, dict) else {}
    threads = [t for t in entities_collection(ent, "threads")
               if isinstance(t, dict) and t.get("id")]
    aliases_doc = _read_json(root / "_hq" / "data" / "aliases.json")

    # Owner-tier read on purpose — see the module docstring's privacy note.
    events, _skipped = events_io.load_events_owner_scoped(root)

    # GAUGEJOB1 scope addition (SPEC_BACKFILL1 §write-shape finding) — honor
    # the RECL1 correction rail BEFORE measuring. A `reclassification` event
    # is the canonical append-only way a binding gets CORRECTED (the original
    # event is never edited), so a gauge that walks raw `threads_of` counts
    # every corrected event against its OLD thread forever: the re-bound
    # event never reaches its true thread's substance_bound, and it keeps
    # sitting in the old thread's numerator — the exact misbinding this
    # artifact exists to expose, baked into the instrument itself.
    # `apply_reclassifications` (thread_activity — the RECL1 canonical fold)
    # patches superseded envelopes one hop, latest correction wins, and
    # leaves reclassification rows in the stream (they are never substance,
    # so they enter no count — but their seqs DO advance the high-water
    # mark, which is right: a new correction must re-measure the gauge and
    # flag every reader stale until it does).
    try:
        from thread_activity import apply_reclassifications
    except ImportError:  # pragma: no cover - direct-path fallback
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from thread_activity import apply_reclassifications
    events = apply_reclassifications(events)

    # GAUGECAL1 — the generic-term set is this WORKSPACE's: the universal base
    # plus its own org/brand names, derived from the entities doc just read.
    regex_by_tid = terms_regexes(threads, aliases_doc, entities=ent_doc)

    # GAUGECAL1 — one config read per measurement, hoisted out of the
    # per-thread loop (the surface_drivers convention: the pure part is a
    # function of what it is handed, the I/O lives at the top).
    min_substance, min_ratio = ready_thresholds(root)

    tids = {t["id"] for t in threads}
    bound_count: dict[str, int] = {tid: 0 for tid in tids}
    name_unbound: dict[str, int] = {tid: 0 for tid in tids}
    meet_bound: dict[str, int] = {tid: 0 for tid in tids}
    meet_bound_pid: dict[str, int] = {tid: 0 for tid in tids}
    meet_total = 0
    meet_total_pid = 0

    email_idx = event_refs.email_person_index(ent)

    for ev in events:
        refs = event_refs.threads_of(ev) & tids
        if substance_events.is_substance_event(ev):
            # The audit matched the raw file line; the substrate is written
            # with ascii escapes, so serialize the same way.
            line = event_match_line(ev)
            for tid in refs:
                bound_count[tid] += 1
            for tid in match_name_unbound(line, refs, regex_by_tid):
                name_unbound[tid] += 1
        if substance_events.event_type_of(ev) == "meeting":
            has_pid = bool(event_refs.meeting_person_ids(ev, email_idx))
            meet_total += 1
            if has_pid:
                meet_total_pid += 1
            for tid in refs:
                meet_bound[tid] += 1
                if has_pid:
                    meet_bound_pid[tid] += 1

    threads_out: dict[str, dict] = {}
    for t in threads:
        tid = t["id"]
        sub = bound_count[tid]
        den = sub + name_unbound[tid]
        ratio = round(sub / den, 4) if den else None
        ready = (sub >= min_substance
                 and ratio is not None and ratio >= min_ratio)
        pb = (round(meet_bound_pid[tid] / meet_bound[tid], 4)
              if meet_bound[tid] else None)
        threads_out[tid] = {
            "ready": ready,
            "ratio": ratio,
            "substance_bound": sub,
            "person_binding": pb,
        }

    generated = now_iso or datetime.now(timezone.utc).isoformat()
    doc = {
        "generated": generated,
        "events_max_seq": _max_human_seq(events),
        "meetings_person_bound_pct": (round(meet_total_pid / meet_total, 4)
                                      if meet_total else None),
        "threads": threads_out,
    }
    # QUIET1 D2 — the INTERACTION leg, inside this job and never a job of
    # its own (SPEC_QUIET1 D2: "a daily job leg inside binding-gauge, not a
    # new job"). It reads the SAME owner-scoped events this measurement just
    # walked, and its verdict (the effective preset, stepped down after
    # fourteen silent days, never up) rides the artifact as a compatible
    # extension the reader passes through. The transition it found — the
    # step-down or the restore — goes back through `side` so the job can
    # receipt it (a posture move is an automatic act, and every automatic
    # act is receipted); the artifact never carries the transition itself,
    # so a quiet day stays a fixed point.
    try:
        import quiet as _quiet
    except ImportError:  # pragma: no cover — direct-path fallback
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import quiet as _quiet
    previous = _read_json(root.joinpath(*GAUGE_RELPATH))
    block, transition = _quiet.gauge_leg(
        root, events, now_iso=generated,
        previous_doc=previous if isinstance(previous, dict) else None)
    doc[_quiet.GAUGE_BLOCK_KEY] = block
    if isinstance(side, dict):
        side["interaction_transition"] = transition
    return doc


def write_gauge(workspace_root: str | Path, *,
                now_iso: str | None = None) -> dict:
    """Compute AND atomic-write the artifact. Returns the artifact dict."""
    try:
        from atomic_write import atomic_write_json
    except ImportError:  # pragma: no cover
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from atomic_write import atomic_write_json
    root = Path(workspace_root)
    doc = build_gauge(root, now_iso=now_iso)
    gpath = root.joinpath(*GAUGE_RELPATH)
    gpath.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(gpath, doc)
    return doc


# ---------------------------------------------------------------------------
# GAUGEJOB1 — the write cadence: the gauge as a maintenance JOB
# ---------------------------------------------------------------------------
#
# WHAT WAS MISSING (memory program R1 prerequisite). The writer above shipped
# with READER1 and nothing ran it on a schedule, so every reader saw the
# honest "unmeasured" forever — the §0.3 persisted-verdict design degenerated
# to no verdict at all. This entry wires it into the `maintenance` task the
# exact way UNCONFEXP1's review-expiry drain is wired: a JOB with an internal
# `nominal_cron`, zero new scheduled tasks, zero prompt-template changes,
# zero re-registration (see maintenance_dispatcher.MAINTENANCE_JOBS
# ["binding-gauge"] for the cadence rationale and the ordering contract).
#
# QUIET-RUN SEMANTICS (COVERQUIET1 posture, the reviewer-visible default —
# and a deliberate DEPARTURE from _log_review_expiry_receipt's
# receipt-every-fire rule, forced by a fixed-point problem that job does not
# have):
#
#   * a CHANGE run — the computed measurement (verdicts, counts, OR the
#     events high-water mark) differs from the artifact on disk, or no
#     readable artifact exists — writes the RECEIPT FIRST, then re-reads the
#     high-water mark and writes the ARTIFACT stamping it. Order is the
#     point: the receipt is itself a ledger event, so a receipt written
#     AFTER the artifact would advance the substrate one seq past the
#     measurement the artifact just claimed, and every reader would flag
#     stale_substrate over the job's own bookkeeping row — permanently,
#     since each refresh would re-plant the very row that re-flags it. The
#     receipt row is never substance and carries no thread refs, so the
#     verdicts measured one seq below it describe the post-receipt substrate
#     exactly; stamping the post-receipt mark is honest, and it is what
#     gives the artifact a FIXED POINT (a re-run with no movement since
#     computes the identical measurement and goes quiet);
#   * a QUIET run — measurement identical to the artifact — leaves NO trace:
#     no artifact write, no receipt. The receipt-every-fire rule exists so a
#     job never re-derives at every slot; here the quiet re-derive costs
#     ~2s (measured — see the registry row) and a quiet receipt would
#     destroy the fixed point above. The accepted trade: on a day with zero
#     substrate movement the dispatcher lists this job due at each of the
#     task's fires and each run quietly exits. In practice a change run
#     happens most days (any sibling job's receipt moves the mark), which
#     receipts the day and self-limits the job to daily;
#   * `receipt_line` is ALWAYS empty — the gauge is substrate infra, its
#     verdicts surface through load_thread_knowledge on the threads they
#     describe, and a daily "re-measured N threads" line in the staff meeting
#     would be exactly the filler COVERQUIET1 exists to refuse;
#   * crash containment: a crash between receipt and artifact write leaves a
#     served slot with a stale artifact; the NEXT day's run necessarily sees
#     a changed measurement and repairs it — one-day lag, self-healing. The
#     measure->receipt->stamp window is not concurrency-proof, and does not
#     need to be: the maintenance fire's ORDER contract runs jobs strictly
#     one at a time, and any event that does slip in is covered by the next
#     day's re-measure.


def _doc_measurement(doc) -> dict:
    """The comparable measurement — everything but the `generated` stamp."""
    if not isinstance(doc, dict):
        return {}
    return {k: v for k, v in doc.items() if k != "generated"}


def run_gauge_refresh_job(workspace_root, *, apply: bool = False,
                          now_iso: str | None = None,
                          fired_via: str = "scheduled") -> dict:
    """GAUGEJOB1 — compute the gauge, refresh the artifact when the
    measurement changed, and receipt the fire.

    `apply=False` is the dry run (compute + report, no write, no receipt —
    the registered prompt passes `--apply`, exactly as identity-reconcile,
    lifecycle and review-expiry do, and the flag mattering is the point: a
    dry run that wrote a receipt would go permanently un-due).

    `now_iso` is the test seam for the artifact's `generated` stamp only —
    dueness lives in the dispatcher, never here.

    Returns {ran, applied, wrote, changed, n_threads, n_ready,
    events_max_seq, duration_ms, refused, reason, receipt_line, summary}.
    """
    import time as _time

    def _refusal(refused: str, reason: str) -> dict:
        return {"ran": False, "applied": False, "wrote": False,
                "changed": False, "n_threads": 0, "n_ready": 0,
                "events_max_seq": None, "duration_ms": None,
                "refused": refused, "reason": reason,
                "receipt_line": "", "summary": ""}

    # Validate the receipt vocabulary BEFORE any work — the UNCONFEXP1
    # review N-4 posture: `log_receipt` raises on an unknown fired_via, and
    # a receipt that raises after a successful write leaves the job
    # permanently due with nothing to explain why. Refused up front, nothing
    # has happened yet.
    try:
        from receipts import FIRED_VIA, normalize_fired_via
    except ImportError:  # pragma: no cover — direct-path fallback
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from receipts import FIRED_VIA, normalize_fired_via
    via = normalize_fired_via(fired_via)
    if via not in FIRED_VIA:
        return _refusal(
            "unknown_fired_via",
            f"{fired_via!r} is not a fire provenance I can record, so the "
            f"gauge was not touched. Nothing was changed.")
    fired_via = via

    root = Path(workspace_root)
    if not (root / "_hq" / "data").is_dir():
        # Never write into a directory that is not a workspace — an artifact
        # (or a receipt, which would CREATE _hq/data) landed at a wrong root
        # is the misplaced-substrate class. No receipt on purpose: there is
        # no workspace to receipt into.
        return _refusal(
            "not_a_workspace",
            f"no _hq/data under {root} — this is not a workspace root, so "
            f"nothing was measured and nothing was written.")

    t0 = _time.perf_counter()
    side: dict = {}
    doc = build_gauge(root, now_iso=now_iso, side=side)
    duration_ms = int((_time.perf_counter() - t0) * 1000)
    transition = side.get("interaction_transition")

    gpath = root.joinpath(*GAUGE_RELPATH)
    existing = _read_json(gpath)
    changed = _doc_measurement(existing) != _doc_measurement(doc)

    n_ready = sum(1 for r in doc["threads"].values() if r.get("ready"))
    out = {
        "ran": True, "applied": bool(apply), "wrote": False,
        "changed": changed,
        "n_threads": len(doc["threads"]), "n_ready": n_ready,
        "events_max_seq": doc["events_max_seq"],
        "duration_ms": duration_ms,
        "refused": None, "reason": None,
        "receipt_line": "",  # always — see the QUIET-RUN SEMANTICS note
        "summary": (f"{len(doc['threads'])} thread(s) measured, {n_ready} "
                    f"READY, events_max_seq={doc['events_max_seq']}"
                    + ("" if changed else " (unchanged)")),
        # QUIET1 — the interaction leg's verdict and the move it found (the
        # move is receipted below on an applied CHANGE run; a dry run only
        # reports it).
        "interaction": dict(doc.get("interaction") or {}),
        "interaction_transition": transition,
    }

    if not apply or not changed:
        # Dry run, or the quiet fixed point: no trace at all (see the
        # QUIET-RUN SEMANTICS note — a quiet receipt would advance the very
        # high-water mark whose standstill made the run quiet).
        return out

    # CHANGE run. Receipt FIRST (it is a ledger event; written after the
    # artifact it would flag the artifact stale forever), then stamp the
    # post-receipt high-water mark and land the artifact.
    _log_gauge_receipt(root, out, fired_via=fired_via)
    if transition:
        # QUIET1 D3 — the posture move is an automatic act: ONE receipt row
        # (`interaction_posture`, step_down or restore), written before the
        # artifact for the same fixed-point reason as the job receipt. The
        # brief narrates a step_down exactly once off this row
        # (`quiet.step_down_narration`); a restore is silent — the person
        # just answered, and the questions coming back is the answer.
        try:
            import quiet as _quiet
            _quiet.write_posture_event(root, transition, now_iso=now_iso)
        except Exception as exc:  # noqa: BLE001 — loud, never fatal
            print(f"[binding-gauge] posture receipt FAILED: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
    try:
        events2, _sk2 = events_io.load_events_owner_scoped(root)
        post_max = _max_human_seq(events2)
        if isinstance(post_max, int) and (
                not isinstance(doc["events_max_seq"], int)
                or post_max > doc["events_max_seq"]):
            doc["events_max_seq"] = post_max
            out["events_max_seq"] = post_max
            out["summary"] = (f"{len(doc['threads'])} thread(s) measured, "
                              f"{out['n_ready']} READY, "
                              f"events_max_seq={post_max}")
    except Exception:  # noqa: BLE001 — a failed re-read never loses the
        pass           # measurement; the pre-receipt stamp is merely one
                       # bookkeeping row behind and self-heals next run
    from atomic_write import atomic_write_json  # sibling module — the
    # sys.path fallback above already ran if it was needed
    gpath.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(gpath, doc)
    out["wrote"] = True
    return out


def _log_gauge_receipt(workspace_root, out, *, fired_via) -> None:
    """ONE receipt per CHANGE run, written BEFORE the artifact — see the
    QUIET-RUN SEMANTICS note above for why quiet runs receipt nothing and
    why the order is receipt-then-artifact. The counts describe the
    MEASUREMENT (the receipt never claims the artifact landed — the artifact
    on disk is its own proof, and the next run repairs a crash between the
    two). Loud, never fatal: a receipt failure must not lose the refresh —
    the artifact still lands, merely stamped one bookkeeping row short."""
    try:
        from receipts import log_receipt
        log_receipt(
            workspace_root, GAUGE_JOB_ID,
            receipt_type=GAUGE_RECEIPT_TYPE,
            fired_via=fired_via,
            surfaced=0,  # substrate infra — nothing is ever surfaced to the CEO
            duration_ms=out.get("duration_ms"),
            extra_data={
                "n_threads": int(out.get("n_threads") or 0),
                "n_ready": int(out.get("n_ready") or 0),
                "events_max_seq_measured": out.get("events_max_seq"),
                "changed": True,
                "receipt_line": "",
            },
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[binding-gauge] refresh receipt FAILED: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)


def main(argv: list[str]) -> int:
    # Self-configure UTF-8 — Cowork invokes this from a cp1252 Windows
    # console; never rely on the caller setting PYTHONUTF8 (the
    # verify_fleet/tag_release unicode-crash class). Same pattern as
    # tests/run_all.py.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — non-console stream
            pass
    args = [a for a in argv[1:] if a]
    dry = "--dry-run" in args
    job = "--job" in args
    apply_flag = "--apply" in args
    fired_via = "scheduled"
    pos: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--fired-via":
            if i + 1 >= len(args):
                print("Usage: --fired-via needs a value", file=sys.stderr)
                return 2
            fired_via = args[i + 1]
            i += 2
            continue
        if not a.startswith("--"):
            pos.append(a)
        i += 1
    if len(pos) != 1:
        print("Usage: python binding_gauge.py <workspace_root> [--dry-run] |"
              " <workspace_root> --job [--apply] [--fired-via X]",
              file=sys.stderr)
        return 2
    root = Path(pos[0])
    if job:
        # GAUGEJOB1 — the maintenance-job entry. The refusal path prints and
        # exits non-zero so a mis-invoked fire is loud in the task log; the
        # job's own receipt (written only on --apply) is the dueness signal.
        out = run_gauge_refresh_job(root, apply=apply_flag,
                                    fired_via=fired_via)
        if out.get("refused"):
            print(f"REFUSED ({out['refused']}): {out['reason']}",
                  file=sys.stderr)
            return 2
        mode = "applied" if apply_flag else "dry-run"
        wrote = ("wrote artifact" if out.get("wrote")
                 else "artifact unchanged" if apply_flag
                 else "nothing written")
        print(f"OK — binding-gauge refresh ({mode}): {out['summary']}; "
              f"{wrote}; {out['duration_ms']}ms")
        return 0
    if not (root / "_hq" / "data").is_dir():
        print(f"ABORT: not a workspace root (no _hq/data): {root}",
              file=sys.stderr)
        return 2
    if dry:
        doc = build_gauge(root)
        print(json.dumps(doc, indent=1, ensure_ascii=False))
        print(f"\n(dry run — nothing written; {len(doc['threads'])} thread(s) "
              f"measured, events_max_seq={doc['events_max_seq']})",
              file=sys.stderr)
        return 0
    doc = write_gauge(root)
    ready = sum(1 for r in doc["threads"].values() if r["ready"])
    print(f"OK — wrote {'/'.join(GAUGE_RELPATH)}: "
          f"{len(doc['threads'])} thread(s), {ready} READY, "
          f"events_max_seq={doc['events_max_seq']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
