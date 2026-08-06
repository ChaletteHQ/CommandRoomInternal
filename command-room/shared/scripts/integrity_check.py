"""Deterministic substrate integrity check (read-only).

Command Room's consistency rules ("every id resolves", "every active folder has
a thread record and vice-versa", "no cycles", "aliases point at live entities")
historically lived only as prose in weekly-audit/SKILL.md for the model to
execute by hand. That means drift stays invisible until someone runs the audit
*and* checks 30+ items faithfully. This script executes those checks as code so
drift fails loudly instead.

It is strictly READ-ONLY: it loads entities.json / events.jsonl / aliases.json
and the top-level folder list, and reports findings. It never writes the
substrate and never moves a file. Fixing is a separate, owner-driven step.

Shape-tolerant by design (see [[substrate-shape-drift]]): the live file stores
orgs/people/threads flat at top level and uses the `threads` key, while the
schema describes them nested under `entities` with a `projects` key. The loader
normalizes both.

Usage:
    python3 integrity_check.py <workspace_root> [--json]
    python3 integrity_check.py            # auto-resolves root from CWD

Exit code: 0 if no ERROR-severity findings, 1 otherwise (so it can gate CI or a
migration). WARN/INFO findings do not fail the run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

try:
    from workspace_root import find_workspace_root
except ImportError:  # when imported as a package member
    from .workspace_root import find_workspace_root  # type: ignore

# Folders that are never client/project threads, so "folder with no thread
# record" is expected for them, not an orphan.
_NON_PROJECT_FOLDERS = {
    "_hq", "_archive", "_people", "_exploring", "_unrouted",
    "Command Room",  # the product's own collateral folder
}

ERROR, WARN, INFO = "ERROR", "WARN", "INFO"


def _norm(s: Any) -> str:
    return (s or "").strip().lower()


class Finding:
    __slots__ = ("check", "severity", "message", "subject")

    def __init__(self, check: str, severity: str, message: str, subject: str = ""):
        self.check = check
        self.severity = severity
        self.message = message
        self.subject = subject

    def as_dict(self) -> dict:
        return {
            "check": self.check,
            "severity": self.severity,
            "subject": self.subject,
            "message": self.message,
        }


def load_entities(root: Path) -> dict:
    """Load entities.json and normalize to flat lists regardless of shape."""
    raw = json.loads((root / "_hq" / "data" / "entities.json").read_text("utf-8"))
    # Collections live either flat at top level or nested under "entities".
    container = raw.get("entities") if isinstance(raw.get("entities"), dict) else raw
    people = container.get("people") or raw.get("people") or []
    # The thread collection is "threads" (live) or "projects" (schema/legacy).
    threads = (
        container.get("threads")
        or container.get("projects")
        or raw.get("threads")
        or raw.get("projects")
        or []
    )
    orgs = container.get("orgs") or raw.get("orgs") or []
    engagements = container.get("engagements") or raw.get("engagements") or []
    return {
        "people": people,
        "threads": threads,
        "orgs": orgs,
        "engagements": engagements,
        "raw": raw,
    }


def load_events(root: Path) -> tuple[list[dict], int]:
    """Defensively read events.jsonl. Returns (events, skipped_line_count).

    EVGUARD (Sub-bug #14b, second half) — a top-level bare-string line
    (`"seq"`) PARSES, so the old loop counted it as an event and appended it.
    Nothing crashed here; every downstream reader did (`event_thread_id` et al.
    call `.get()` on the row). Non-dict rows now count in `skipped` and never
    enter `events`, so the return value matches its annotation.

    Deliberately does NOT route through `cru_match.load_events_defensively`:
    this module is dependency-light on purpose (json/sys/pathlib only) because
    a corruption diagnostic has to run when the other modules are the thing
    that is broken. The guard is three lines; the import would be a new
    failure mode.
    """
    path = root / "_hq" / "data" / "events.jsonl"
    events: list[dict] = []
    skipped = 0
    if not path.is_file():
        return events, skipped
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(ev, dict):
            skipped += 1
            continue
        events.append(ev)
    return events, skipped


def load_aliases(root: Path) -> dict:
    path = root / "_hq" / "data" / "aliases.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError:
        return {}


def event_thread_id(ev: dict) -> str:
    """Resolve a thread id from an event regardless of which of the historical
    spellings/nestings it used. Mirrors the four conventions found in the live
    log: top-level primary_thread_id / project_id / primary_project_id and the
    same keys nested under `data`."""
    data = ev.get("data") or {}
    for src in (ev, data):
        for key in ("primary_thread_id", "project_id", "primary_project_id"):
            val = src.get(key)
            if val:
                return str(val)
    return ""


def event_org_ids(ev: dict) -> list[str]:
    data = ev.get("data") or {}
    ids = data.get("org_ids") or ev.get("org_ids") or []
    single = data.get("org_id") or ev.get("org_id")
    if single:
        ids = [*ids, single]
    return [str(i) for i in ids if i]


def _detect_cycle(node_id: str, parent_of: dict[str, str | None]) -> bool:
    seen = set()
    cur: str | None = node_id
    while cur:
        if cur in seen:
            return True
        seen.add(cur)
        cur = parent_of.get(cur)
    return False


def _project_folders(root: Path) -> list[str]:
    """Top-level folders that could be project threads (excludes infra + hidden).

    Mirrors the C10/C11 skip rules: `_`-prefixed infra folders, the product's own
    collateral folder, and dotfiles are never project threads."""
    out = []
    for d in root.iterdir():
        if not d.is_dir() or d.name.startswith("."):
            continue
        if d.name in _NON_PROJECT_FOLDERS or d.name.startswith("_"):
            continue
        out.append(d.name)
    return out


def _has_session_notes(folder: Path) -> bool:
    """True if the folder carries any SESSION_NOTES file (live, archive, or index).

    Session notes live at the folder root as `SESSION_NOTES[_NAME].md` (per the
    cleanup gotcha — not a subfolder). An archive/index alone still proves notes
    existed, so it counts as present (we never re-scaffold over real history)."""
    for _ in folder.glob("SESSION_NOTES*.md"):
        return True
    return False


def _folder_structure_findings(root: Path, threads: list[dict]) -> list[Finding]:
    """C10 (orphan folder) / C11 (missing brain) / C11b (missing session notes).

    Folder <-> thread reconciliation, strictly read-only. Shared by run_checks
    and the scan_project_structure() entry point that cleanup's Phase 1 calls
    directly (SPEC CLEAN1 / D1)."""
    findings: list[Finding] = []
    folder_to_thread = {_norm(t.get("folder_name")): t for t in threads if t.get("folder_name")}
    for name in _project_folders(root):
        folder = root / name
        # C10 — disk project folder has no thread record (orphan folder).
        if _norm(name) not in folder_to_thread:
            findings.append(Finding("C10.orphan_folder", WARN,
                f"folder '{name}/' has no thread record in entities (orphan — register or archive)", name))
        has_context = (folder / "PROJECT_CONTEXT.md").is_file()
        has_brain = (folder / "PROJECT_BRAIN.md").is_file()
        has_notes = _has_session_notes(folder)
        # Only flag folders that look like real scaffolded projects (have a
        # context, brain, or session-notes file) — bare folders are orphans, not
        # incomplete projects.
        looks_like_project = has_context or has_brain or has_notes
        if not looks_like_project:
            continue
        # C11 — missing PROJECT_BRAIN.md.
        if not has_brain:
            findings.append(Finding("C11.missing_brain", WARN,
                f"project '{name}/' has scaffolding but no PROJECT_BRAIN.md (backfill from template)", name))
        # C11b — missing SESSION_NOTES (mirror of C11; backfill a scaffold —
        # never overwrite an existing notes file). SPEC CLEAN1 / D3.
        if not has_notes:
            findings.append(Finding("C11b.missing_session_notes", WARN,
                f"project '{name}/' has scaffolding but no SESSION_NOTES file (backfill a scaffold)", name))
    return findings


def scan_project_structure(workspace_root: str | Path) -> list[Finding]:
    """Deterministic Phase-1 structural scan for cleanup (SPEC CLEAN1 / D1).

    Loops top-level project folders, cross-references entities.json threads, and
    returns orphan_folder / missing_brain / missing_session_notes findings.
    Strictly READ-ONLY — the caller (cleanup) decides what to flag vs. remediate;
    orphan folders are always FLAGGED, never removed. This replaces cleanup's
    prose-only Phase 1 scan, which five real runs proved did not execute."""
    root = Path(workspace_root)
    threads = load_entities(root)["threads"]
    return _folder_structure_findings(root, threads)


def run_checks(root: Path) -> list[Finding]:
    ent = load_entities(root)
    events, skipped = load_events(root)
    aliases = load_aliases(root)
    findings: list[Finding] = []

    people = ent["people"]
    threads = ent["threads"]
    orgs = ent["orgs"]
    engagements = ent["engagements"]

    org_ids = {o.get("id") for o in orgs if o.get("id")}
    person_ids = {p.get("id") for p in people if p.get("id")}
    thread_ids = {t.get("id") for t in threads if t.get("id")}

    # C1 — id presence + shape.
    for coll, items, prefix in (
        ("orgs", orgs, "org_"),
        ("people", people, "person_"),
        ("threads", threads, "project_"),
        ("engagements", engagements, "engagement_"),
    ):
        for it in items:
            iid = it.get("id")
            if not iid:
                findings.append(Finding("C1.id_present", ERROR,
                    f"{coll} record missing 'id'", str(it.get("canonical_name") or it.get("folder_name") or "?")))
            elif not str(iid).startswith(prefix):
                findings.append(Finding("C1.id_shape", WARN,
                    f"{coll} id '{iid}' does not match expected prefix '{prefix}'", str(iid)))

    # C2 — thread.affiliation_id resolves to an org, or is the literal 'personal'.
    for t in threads:
        aff = t.get("affiliation_id")
        legacy = t.get("org_id")
        ref = aff if aff is not None else legacy
        if ref in (None, "", "personal"):
            continue
        if ref not in org_ids:
            findings.append(Finding("C2.thread_affiliation", ERROR,
                f"thread '{t.get('id')}' affiliation_id '{ref}' does not resolve to any org",
                str(t.get("id"))))

    # C3 — thread.parent_thread_id resolves + no cycles.
    parent_of_thread = {t.get("id"): t.get("parent_thread_id") for t in threads}
    for t in threads:
        p = t.get("parent_thread_id")
        if p and p not in thread_ids:
            findings.append(Finding("C3.parent_thread", ERROR,
                f"thread '{t.get('id')}' parent_thread_id '{p}' does not resolve", str(t.get("id"))))
        elif p and _detect_cycle(t.get("id"), parent_of_thread):
            findings.append(Finding("C3.thread_cycle", ERROR,
                f"thread '{t.get('id')}' is part of a parent_thread_id cycle", str(t.get("id"))))

    # C4 — org.parent_org_id resolves + no cycles.
    parent_of_org = {o.get("id"): o.get("parent_org_id") for o in orgs}
    for o in orgs:
        p = o.get("parent_org_id")
        if p and p not in org_ids:
            findings.append(Finding("C4.parent_org", ERROR,
                f"org '{o.get('id')}' parent_org_id '{p}' does not resolve", str(o.get("id"))))
        elif p and _detect_cycle(o.get("id"), parent_of_org):
            findings.append(Finding("C4.org_cycle", ERROR,
                f"org '{o.get('id')}' is part of a parent_org_id cycle", str(o.get("id"))))

    # C5 — engagement endpoints resolve.
    for e in engagements:
        for endpoint in ("from_org_id", "to_org_id"):
            ref = e.get(endpoint)
            if ref and ref not in org_ids:
                findings.append(Finding("C5.engagement_endpoint", ERROR,
                    f"engagement '{e.get('id')}' {endpoint} '{ref}' does not resolve", str(e.get("id"))))

    # C6 — person org links + thread membership resolve.
    for p in people:
        for ref in [p.get("primary_org_id"), p.get("org_id"), *(p.get("affiliation_ids") or [])]:
            if ref and ref != "personal" and ref not in org_ids:
                findings.append(Finding("C6.person_org", WARN,
                    f"person '{p.get('id')}' references org '{ref}' which does not resolve", str(p.get("id"))))
        for ref in (p.get("project_ids") or []):
            if ref and ref not in thread_ids:
                findings.append(Finding("C6.person_thread", WARN,
                    f"person '{p.get('id')}' project_ids includes '{ref}' which does not resolve", str(p.get("id"))))
    # thread -> person links
    for t in threads:
        refs = [t.get("owner_person_id"), *(t.get("stakeholder_person_ids") or [])]
        for ref in refs:
            if ref and ref not in person_ids:
                findings.append(Finding("C6.thread_person", WARN,
                    f"thread '{t.get('id')}' references person '{ref}' which does not resolve", str(t.get("id"))))

    # C7 — event references resolve (dangling-ref / test-residue detector).
    dangling_threads: dict[str, int] = {}
    dangling_orgs: dict[str, int] = {}
    for ev in events:
        tid = event_thread_id(ev)
        if tid and tid not in thread_ids:
            dangling_threads[tid] = dangling_threads.get(tid, 0) + 1
        for oid in event_org_ids(ev):
            if oid not in org_ids:
                dangling_orgs[oid] = dangling_orgs.get(oid, 0) + 1
    for tid, count in sorted(dangling_threads.items(), key=lambda x: -x[1]):
        if tid in org_ids:
            msg = (f"{count} event(s) put org id '{tid}' in a thread-id slot "
                   f"(mis-slotted reference — an org id where a project_ id belongs)")
        else:
            msg = f"{count} event(s) reference thread '{tid}' which does not exist in entities"
        findings.append(Finding("C7.dangling_event_thread", WARN, msg, tid))
    for oid, count in sorted(dangling_orgs.items(), key=lambda x: -x[1]):
        findings.append(Finding("C7.dangling_event_org", WARN,
            f"{count} event(s) reference org '{oid}' which does not exist in entities", oid))

    # C8 — alias targets resolve.
    all_ids = org_ids | person_ids | thread_ids
    alias_map = aliases.get("mappings") if isinstance(aliases.get("mappings"), list) else None
    if alias_map is not None:
        for m in alias_map:
            cid = m.get("canonical_id")
            if cid and cid not in all_ids:
                findings.append(Finding("C8.dead_alias", WARN,
                    f"alias '{m.get('raw') or m.get('alias') or '?'}' -> '{cid}' which no longer resolves", str(cid)))

    # C9 — active thread folder_name exists on disk.
    for t in threads:
        if (t.get("status") or "active") in ("archived",):
            continue
        fn = t.get("folder_name")
        if fn and fn not in ("", None) and not (root / fn).is_dir():
            findings.append(Finding("C9.thread_folder_missing", WARN,
                f"thread '{t.get('id')}' folder_name '{fn}' not found on disk (moved/renamed/archived without record update?)",
                str(t.get("id"))))
    # C9b — thread carries NO folder_name at all (HONEST1).
    #
    # C9 above guards on `fn and fn not in ("", None)`, so an EMPTY folder_name is
    # skipped by it entirely — and that is precisely what v5.4.0's resolver now
    # produces: `thread_writer` resolves the folder against what is on disk and
    # leaves the field empty when nothing matches, rather than filling it with a
    # slug guess. Honest, but invisible. FOLDERGUARD's own docstring assumed C9
    # would catch it; C9 cannot. Without this check the empty case has no home in
    # the checker at all, and a thread can sit unrenderable forever with the
    # weekly report showing clean.
    #
    # Uses the codebase terminal PAIR ('resolved','archived') rather than C9's
    # archived-only filter — a resolved deal legitimately never gets a folder, and
    # flagging it forever after close is noise. C9's narrower filter is left as-is
    # deliberately: widening it would change existing finding counts, which is a
    # separate decision from adding a new check.
    #
    # Kind filter: deal and objective threads have NO folder by design —
    # `deal_state.create_deal` and `objective_state.create_objective` never set
    # folder_name, and neither kind owns a project folder to record. Flagging
    # them fires on every open deal and every objective in the workspace with
    # advice ("record the folder it belongs to") that has nothing to point at,
    # which turns the Monday note into weekly noise. Only kinds that DO get a
    # folder are checked.
    FOLDERLESS_KINDS = ("deal", "objective")
    for t in threads:
        if (t.get("status") or "active") in ("resolved", "archived"):
            continue
        if (t.get("kind") or "") in FOLDERLESS_KINDS:
            continue
        if not t.get("folder_name"):
            findings.append(Finding("C9b.thread_folder_unset", WARN,
                f"thread '{t.get('id')}' has no folder_name — nothing can resolve its "
                f"project folder, so its brain can never render (record the folder it belongs to)",
                str(t.get("id"))))
    # C10 / C11 / C11b — folder <-> thread reconciliation (orphan folder, missing
    # brain, missing session notes). Shared with scan_project_structure() so the
    # weekly cleanup Phase 1 and the deep-clean integrity pass agree exactly.
    findings.extend(_folder_structure_findings(root, threads))

    # C12 — duplicate event seq.
    seqs: dict[Any, int] = {}
    for ev in events:
        s = ev.get("seq")
        if s is not None:
            seqs[s] = seqs.get(s, 0) + 1
    for s, count in seqs.items():
        if count > 1:
            findings.append(Finding("C12.duplicate_seq", ERROR,
                f"event seq {s} appears {count} times (concurrent-append collision — possible lost data)", str(s)))

    # C13 — corrupt event lines.
    if skipped:
        findings.append(Finding("C13.unparseable_events", WARN,
            f"{skipped} line(s) in events.jsonl could not be parsed as JSON", ""))

    # C14 — thread-bound events carry a primary_thread_id (ORG_AND_THREAD_MODEL
    # invariant #1). Every daily-flow surface (briefing, commitments, pulse)
    # FILTERS on this field, so an event missing it is silently invisible.
    # Scoped to the thread-bound types so meta/system events (org_created,
    # backfill, tier_change) don't false-positive (deep-audit 2026-05-29, #13).
    _THREAD_BOUND_TYPES = {"meeting", "interaction", "commitment", "decision",
                           "follow_up", "note", "insight"}
    missing_tid = 0
    for ev in events:
        et = ev.get("type") or ev.get("event") or ""
        if et in _THREAD_BOUND_TYPES and not event_thread_id(ev):
            missing_tid += 1
    if missing_tid:
        findings.append(Finding("C14.event_missing_thread", WARN,
            f"{missing_tid} thread-bound event(s) have no primary_thread_id — "
            f"invisible to every daily-flow surface that filters on it", ""))

    # C15 — classification_confidence within [0.0, 1.0] (invariant #5). An
    # out-of-range or non-numeric value silently breaks the numeric band
    # comparison in consumers (deep-audit 2026-05-29, #13).
    bad_conf = 0
    for ev in events:
        c = ev.get("classification_confidence")
        if c is None:
            continue
        if not isinstance(c, (int, float)) or isinstance(c, bool) or not (0.0 <= c <= 1.0):
            bad_conf += 1
    if bad_conf:
        findings.append(Finding("C15.confidence_out_of_range", WARN,
            f"{bad_conf} event(s) have classification_confidence outside [0.0, 1.0] "
            f"or non-numeric — breaks confidence-band comparisons", ""))

    # C17 — commitment write-contract violations (Phase 2 Stage D, S4: the
    # Monday-note flag for the F4 in-place-mutation class). Two symptoms of a
    # hand-rolled events.jsonl edit:
    #   (a) any event carrying a `_cleanup_*` key (the 2026-05-28 cleanup-chat
    #       sessions annotated events in place while mutating them);
    #   (b) a commitment event whose data.status is outside the schema values
    #       {open, overdue} — the closed-family values (closed/resolved/
    #       superseded/done) are READ forever (legacy 249) but no NEW write
    #       may produce one: closure is a close_commitment() tombstone append.
    # Read-only: cleanup surfaces these in the Monday note as contract
    # violations; it never rewrites the rows (F4 applies to us too).
    _closed_family = ("closed", "resolved", "superseded", "done")
    cleanup_keys = 0
    mutated_status = 0
    for ev in events:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if any(str(k).startswith("_cleanup_") for k in list(ev.keys()) + list(d.keys())):
            cleanup_keys += 1
        et = ev.get("type") or ev.get("event") or ""
        if et == "commitment":
            status = d.get("status") or d.get("state") or ev.get("status")
            if status and status not in ("open", "overdue") and status in _closed_family:
                mutated_status += 1
    if cleanup_keys:
        findings.append(Finding("C17.cleanup_keys", ERROR,
            f"{cleanup_keys} event(s) carry _cleanup_* keys — a hand-rolled "
            f"in-place edit touched events.jsonl (F4 contract violation; "
            f"closure/annotation must be an APPEND)", ""))
    if mutated_status:
        findings.append(Finding("C17.inplace_status", WARN,
            f"{mutated_status} commitment event(s) carry a closed-family "
            f"data.status — legacy rows are readable forever, but a GROWING "
            f"count means an active in-place mutation writer (F4). Compare "
            f"against last week's Monday note; growth = contract violation",
            ""))

    # C16 — Live State block staleness (brain-substrate-drift fix). A rendered
    # people block older than the newest thread-tagged event means the render
    # trigger didn't fire — the exact disease the brain-substrate work targets.
    # Read-only. Uses the human-counter seq (ignores nano-epoch legacy seqs).
    import re as _re16
    _EPOCH16 = 10 ** 10

    def _threads_of16(ev):
        out = set()
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        for k in ("primary_thread_id", "thread_id", "project_id", "primary_project_id"):
            if ev.get(k):
                out.add(ev[k])
            if d.get(k):
                out.add(d[k])
        for k in ("related_thread_ids", "thread_ids"):
            for r in (ev.get(k) or []):
                out.add(r)
            for r in (d.get(k) or []):
                out.add(r)
        return {t for t in out if isinstance(t, str) and t}

    _newest16: dict[str, int] = {}
    for ev in events:
        s = ev.get("seq")
        if not isinstance(s, (int, float)) or isinstance(s, bool) or s >= _EPOCH16:
            continue
        s = int(s)
        for tid in _threads_of16(ev):
            if s > _newest16.get(tid, 0):
                _newest16[tid] = s
    for t in threads:
        fn = t.get("folder_name")
        if not fn:
            continue
        brain = root / fn / "PROJECT_BRAIN.md"
        if not brain.is_file():
            continue
        try:
            btext = brain.read_text(encoding="utf-8")
        except OSError:
            continue
        m = _re16.search(r"<!--\s*LIVE-STATE:people\b[^>]*source_seq=(\d+)", btext)
        if not m:
            continue
        block_seq = int(m.group(1))
        newest = _newest16.get(t.get("id"))
        if newest and newest > block_seq:
            findings.append(Finding("C16.live_state_stale", WARN,
                f"thread '{t.get('id')}' Live State block (source_seq={block_seq}) is older than "
                f"its newest event (seq={newest}) — the render trigger didn't fire", str(t.get("id"))))

    # C18 — entity-history view staleness (SPEC HIST1 D7; mirrors C16). A
    # person/org history view under _hq/views/people|orgs older than the
    # newest event tagging that entity means the go-render / cleanup 3.5d3
    # refresh didn't fire. Read-only; only EXISTING views are checked (views
    # are created on `go`, so an entity with no view is not a finding).
    try:
        import render_person_history as _rph18
        import render_org_history as _roh18
        _seq_re18 = _re16.compile(r"<!--\s*source_seq=(\d+)\s*-->")
        for kind, views_dir, gather in (
            ("person", root / "_hq" / "views" / "people",
             lambda eid: _rph18._gather(root, eid)),
            ("org", root / "_hq" / "views" / "orgs",
             lambda eid: _roh18._gather(root, eid,
                                        _roh18._collections(_roh18._load_entities_doc(root)))),
        ):
            if not views_dir.is_dir():
                continue
            for f in sorted(views_dir.glob("*.md")):
                try:
                    m18 = _seq_re18.search(f.read_text(encoding="utf-8"))
                except OSError:
                    continue
                if not m18:
                    continue
                block_seq18 = int(m18.group(1))
                try:
                    _evs, _skipped, newest18 = gather(f.stem)
                except Exception:
                    continue
                if newest18 > block_seq18:
                    findings.append(Finding("C18.entity_history_stale", WARN,
                        f"{kind} history view '{f.name}' (source_seq={block_seq18}) is older than "
                        f"the newest event tagging that entity (seq={newest18}) — the go-render / "
                        f"cleanup refresh didn't fire", f.stem))
    except Exception:
        pass  # the drift check is advisory — a missing module never bricks the audit

    return findings


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    args = [a for a in argv[1:] if not a.startswith("--")]
    try:
        root = Path(args[0]).resolve() if args else find_workspace_root()
    except (FileNotFoundError, IndexError) as e:
        print(f"Could not resolve workspace root: {e}", file=sys.stderr)
        return 2

    findings = run_checks(root)
    errors = [f for f in findings if f.severity == ERROR]
    warns = [f for f in findings if f.severity == WARN]
    infos = [f for f in findings if f.severity == INFO]

    if as_json:
        print(json.dumps({
            "workspace_root": str(root),
            "summary": {"error": len(errors), "warn": len(warns), "info": len(infos)},
            "findings": [f.as_dict() for f in findings],
        }, indent=2))
    else:
        print(f"Integrity check — {root}")
        print(f"  {len(errors)} error  ·  {len(warns)} warn  ·  {len(infos)} info\n")
        for sev, group in ((ERROR, errors), (WARN, warns), (INFO, infos)):
            for f in group:
                subj = f" [{f.subject}]" if f.subject else ""
                print(f"  {sev:5s} {f.check}{subj}: {f.message}")
        if not findings:
            print("  clean — no integrity findings.")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
