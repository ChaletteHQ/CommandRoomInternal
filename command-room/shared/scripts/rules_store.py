#!/usr/bin/env python3
"""The rules folder: every learned and standing rule in one shape (cr1#1016,
train 7 of the memory and Hub programme; absorbs cr1#788).

WHY THIS EXISTS
---------------
A rule the owner gave lived wherever it happened to land: a bullet in the
workspace brief (`CLAUDE.md`), a learned line in one writing skill's voice
block, a correction in one skill's log. Each skill read its own corner, so a
rule said once reached a handful of skills and was missed by the rest, and
nobody could see the list.

WHAT IT IS
----------
One record shape for every rule, one folder that holds them, and one reader:

  * THE SHAPE (`RULE_FIELDS`): the rule in one sentence, why, where it
    applies (`applies_to`: skill names, or `everywhere`), its tier, its
    status, where it came from, since when, the source entry it can be
    checked against, and the corrections it came from or that were made
    against it, kept attached. `n_repeat` counts corrections made against it
    after it was confirmed: a rule that keeps being corrected is a rule that
    is not holding.
  * THE FOLDER (`RULES_DIR_REL`, `_hq/rules/`): `rules.json` holds every rule
    in that shape. It is rebuilt from the sources, whole, by `rebuild`; it is
    for a person to read and for display. Recall goes back to the source
    (ruling C3): every record names its source entry.
  * THE SOURCES: a rule the owner states is a `rule_stated` row on the log
    (a later row with the same id amends it, `rule_retired` retires it), and
    a correction recorded against something is a `rule_corrected` row. A
    learned rule is the learning job's own (`learning_pass.learned_rules`,
    cr1#784), read in place and given the same shape here. The log stays the
    record (ruling A1): nothing here edits or removes a row.
  * THE READER: `rules` folds the sources fresh on every call, so a rule
    stated this morning applies this afternoon in every skill, before any
    rebuild. Every skill reaches it through the one loader,
    `context_loader.load_context`, never directly.

THE THREE TIERS (cr1#788, ruling A2 of 2026-11-04)
  * `always`: a line in the brief itself. Fewer than ten, kept there by the
    template; not stored here.
  * `on_demand` (the default): stored here, named by one line in the index
    the brief carries, and loaded in full for each skill it applies to.
  * `check`: a rule code enforces; stored here so the index names it.

WRITES
------
`state_rule`, `retire_rule` and `record_correction` each append one row
through `event_gate` and then rebuild the folder; `rebuild` writes the one
file. All four are writers (`plan run_writer`). Nothing else writes.

Command line, from the plugin root:
    python shared/scripts/rules_store.py <workspace> list [--skill NAME] [--json]
    python shared/scripts/rules_store.py <workspace> rebuild
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

#: The one rules folder, beside the live data files (ruling A2).
RULES_DIR_REL = "_hq/rules"
RULES_FILE = "rules.json"
RULES_VERSION = 1

#: The rows this module owns on the log.
EV_STATED = "rule_stated"
EV_RETIRED = "rule_retired"
EV_CORRECTED = "rule_corrected"
RULE_EVENTS = (EV_STATED, EV_RETIRED, EV_CORRECTED)

#: The one shape. Every record leaves this module with exactly these keys.
RULE_FIELDS = ("id", "rule", "why", "applies_to", "tier", "status", "origin",
               "since", "source", "corrections", "n_repeat")

EVERYWHERE = "everywhere"
#: A stated rule's id; a learned rule's id starts `lrn_` and is never stated.
RULE_ID_RE = re.compile(r"rul_[0-9a-z]{4,32}")
TIERS = ("always", "on_demand", "check")
DEFAULT_TIER = "on_demand"
#: Where a rule came from.
ORIGIN_STATED = "stated"
ORIGIN_LEARNED = "learned"
ORIGIN_STANDING = "standing"
ORIGINS = (ORIGIN_STATED, ORIGIN_LEARNED, ORIGIN_STANDING)
STATUS_LIVE = "live"
STATUS_RETIRED = "retired"

#: What a correction can be recorded against (cr1#788 item 6).
CORRECTION_KINDS = ("wording", "term", "figure", "rule", "way_of_working")

RULE_CHARS = 300
WHY_CHARS = 600
SAID_CHARS = 200
INDEX_CHARS = 140
SOURCE_SKILL = "workspace-manager"
HOLDER = "rules-store"


class RuleError(ValueError):
    """A rule that cannot be stored as asked; the message is one sentence."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _ws(workspace_root) -> Path:
    return Path(str(workspace_root))


def _now_iso(now_iso: Optional[str] = None) -> str:
    return now_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _flat(text: Any, n: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= n else flat[: n - 1].rstrip() + "…"


def _day(ts: Any) -> Optional[str]:
    text = str(ts or "")
    return text[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", text) else None


def _events(ws: Path, problems: Optional[List[str]] = None) -> list:
    """The log's rows with a dict `data`. An unreadable log reads empty and
    says so in `problems`, so a caller never mistakes it for "no rules"."""
    try:
        # Owner-tier read through the allowlisted shard reader (personal
        # firewall D4c): the owner's own rules, no account mask needed.
        from events_io import load_events_owner_scoped
        events, skipped = load_events_owner_scoped(ws / "_hq" / "data")
        if skipped and problems is not None:
            problems.append(f"log: {len(skipped)} unreadable line(s) skipped")
        return [ev for ev in events
                if isinstance(ev, dict) and isinstance(ev.get("data"), dict)]
    except Exception as exc:  # noqa: BLE001: a reader never crashes on the log
        if problems is not None:
            problems.append(f"log: {type(exc).__name__}: {exc}"[:200])
        return []


def _seq(ev: dict) -> Optional[int]:
    try:
        from event_seq import event_seq
        return event_seq(ev)
    except Exception:  # noqa: BLE001
        raw = ev.get("seq")
        return raw if isinstance(raw, int) else None


def _ts(ev: dict) -> str:
    try:
        from event_time import event_time
        return str(event_time(ev) or "")
    except Exception:  # noqa: BLE001
        return str(ev.get("ts") or "")


def normalize_applies_to(applies_to: Any) -> List[str]:
    """Skill names, lower-case and de-duplicated; `everywhere` alone when it
    is named at all, or when nothing is named."""
    if isinstance(applies_to, str):
        items = re.split(r"[,\s]+", applies_to)
    elif isinstance(applies_to, (list, tuple, set, frozenset)):
        items = [str(x) for x in applies_to]
    else:
        items = []
    names: List[str] = []
    for raw in items:
        name = raw.strip().lower()
        if not name:
            continue
        if name in (EVERYWHERE, "all", "*", "every skill"):
            return [EVERYWHERE]
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
            raise RuleError(f"'{raw}' is not a skill name.")
        if name not in names:
            names.append(name)
    return names or [EVERYWHERE]


def rule_id_for(rule: str) -> str:
    """A stated rule's id, from its own words, so the same rule said twice is
    the same record rather than two."""
    norm = " ".join(re.findall(r"[a-z0-9]+", str(rule or "").lower()))
    return "rul_" + hashlib.sha1(norm.encode("utf-8")).hexdigest()[:10]


def scope_words(applies_to: Iterable[str]) -> str:
    names = list(applies_to)
    if not names or names == [EVERYWHERE]:
        return "everywhere"
    return "in " + ", ".join(names)


def applies(record: dict, skill: Optional[str]) -> bool:
    """Whether a live record applies to `skill`. A rule for everywhere
    applies to every skill; a rule named for skills applies to those."""
    if record.get("status") != STATUS_LIVE:
        return False
    scope = record.get("applies_to") or [EVERYWHERE]
    return EVERYWHERE in scope or (bool(skill) and str(skill).lower() in scope)


def _record(**kw) -> dict:
    """The one shape: exactly `RULE_FIELDS`, defaults filled."""
    rec = {k: kw.get(k) for k in RULE_FIELDS}
    rec["applies_to"] = list(rec["applies_to"] or [EVERYWHERE])
    rec["tier"] = rec["tier"] if rec["tier"] in TIERS else DEFAULT_TIER
    rec["status"] = rec["status"] or STATUS_LIVE
    rec["why"] = rec["why"] or ""
    rec["corrections"] = list(rec["corrections"] or [])
    rec["n_repeat"] = int(rec["n_repeat"] or 0)
    rec["source"] = dict(rec["source"] or {})
    return rec


# ---------------------------------------------------------------------------
# The fold: sources to records
# ---------------------------------------------------------------------------

def _correction_ref(ev: dict) -> dict:
    data = ev["data"]
    return {"seq": _seq(ev), "date": _day(_ts(ev)),
            "kind": data.get("kind") or "rule",
            "skill": data.get("skill") or "",
            "said": _flat(data.get("said"), SAID_CHARS)}


def _stated_records(events: list) -> Dict[str, dict]:
    """The stated rules, by id, latest row winning; corrections attached."""
    out: Dict[str, dict] = {}
    for ev in events:
        etype = ev.get("type")
        data = ev["data"]
        rid = str(data.get("rule_id") or "")
        if not rid:
            continue
        if etype == EV_STATED:
            prior = out.get(rid)
            since = (prior or {}).get("since") or _day(_ts(ev))
            first_seq = (prior or {}).get("_first_seq", _seq(ev))
            try:
                scope = normalize_applies_to(data.get("applies_to"))
            except RuleError:
                scope = [EVERYWHERE]
            out[rid] = _record(
                id=rid, rule=_flat(data.get("rule"), RULE_CHARS),
                why=_flat(data.get("why"), WHY_CHARS), applies_to=scope,
                tier=data.get("tier"), status=STATUS_LIVE,
                origin=data.get("origin") if data.get("origin") in ORIGINS
                else ORIGIN_STATED,
                since=since,
                source={"seq": _seq(ev), "id": ev.get("id"), "type": EV_STATED,
                        **({"file": data["moved_from"]}
                           if isinstance(data.get("moved_from"), str) else {})},
                corrections=(prior or {}).get("corrections"))
            out[rid]["_first_seq"] = first_seq
        elif etype == EV_RETIRED and rid in out:
            out[rid]["status"] = STATUS_RETIRED
    attach_corrections(out, events)
    for rec in out.values():
        rec.pop("_first_seq", None)
    return out


def attach_corrections(records: Dict[str, dict], events: list) -> None:
    """Attach every `rule_corrected` row naming a record's id to it, in log
    order. A correction counts as a repeat when it came after the rule was
    first on file, by the log's own order (`seq`), never by a clock: the
    rule's first statement for a stated rule, its learning row for a learned
    one. An amend does not reset the count."""
    for ev in events:
        if ev.get("type") != EV_CORRECTED:
            continue
        rec = records.get(str(ev["data"].get("rule_id") or ""))
        if rec is None:
            continue
        ref = _correction_ref(ev)
        rec["corrections"].append(ref)
        first = rec.get("_first_seq")
        if first is None:
            first = (rec.get("source") or {}).get("seq")
        if isinstance(first, int) and isinstance(ref["seq"], int) and ref["seq"] > first:
            rec["n_repeat"] += 1


def _learned_records(ws: Path, problems: Optional[List[str]] = None) -> List[dict]:
    """The learning job's live rules, in the one shape. Read in place: the
    voice block stays the place the writing skill applies them. One question
    can be written twice (an edit on the Hub writes a second row with the
    same question); the newest live row is the rule."""
    try:
        import learning_pass
        rows = learning_pass.learned_rules(ws)
    except Exception as exc:  # noqa: BLE001: a reader never fails on one source
        if problems is not None:
            problems.append(f"learned rules: {type(exc).__name__}: {exc}"[:200])
        return []
    out: List[dict] = []
    seen: set = set()
    for row in rows:
        if row.get("status") != STATUS_LIVE or not row.get("skill"):
            continue
        key = row.get("proposal_id") or f"seq{row.get('seq')}"
        if key in seen:
            continue
        seen.add(key)
        refs = []
        for c in row.get("corrections") or []:
            if isinstance(c, dict):
                refs.append({"seq": c.get("seq"), "date": _day(c.get("timestamp")),
                             "kind": "wording", "skill": row.get("skill"),
                             "said": _flat(c.get("said") or c.get("original")
                                           or c.get("notes"), SAID_CHARS)})
        out.append(_record(
            id="lrn_" + re.sub(r"[^A-Za-z0-9_]", "", str(key))[:24],
            rule=_flat(row.get("rule") or row.get("effect"), RULE_CHARS),
            why=_flat(row.get("rests_on"), WHY_CHARS),
            applies_to=[str(row.get("skill")).lower()], tier=DEFAULT_TIER,
            status=STATUS_LIVE, origin=ORIGIN_LEARNED,
            since=_day(row.get("applied_at")),
            source={"seq": row.get("seq"), "proposal_id": row.get("proposal_id"),
                    "type": "voice_block_updated"},
            corrections=refs, n_repeat=row.get("n_corrected_against")))
    return out


def rules(workspace_root, *, include_retired: bool = False,
          events: Optional[list] = None,
          problems: Optional[List[str]] = None) -> List[dict]:
    """Every rule in the one shape, folded fresh from the sources: stated
    and standing rules first, oldest first, then learned rules. Read-only. A
    source that could not be read is named in `problems` when one is given."""
    ws = _ws(workspace_root)
    evs = _events(ws, problems) if events is None else events
    stated = list(_stated_records(evs).values())
    learned = _learned_records(ws, problems)
    attach_corrections({r["id"]: r for r in learned}, evs)
    out = stated + learned
    if not include_retired:
        out = [r for r in out if r["status"] == STATUS_LIVE]
    return out


def rules_for(workspace_root, skill: Optional[str], **kw) -> List[dict]:
    """The live rules that apply to `skill`, in full."""
    return [r for r in rules(workspace_root, **kw) if applies(r, skill)]


def index_line(record: dict) -> str:
    """One line per rule, for the index the brief carries."""
    return (f"- [{record['id']}] {_flat(record['rule'], INDEX_CHARS)} "
            f"({scope_words(record['applies_to'])})")


def index_lines(records: Iterable[dict]) -> List[str]:
    return [index_line(r) for r in records if r.get("status") == STATUS_LIVE]


def repeat_corrections(workspace_root) -> List[dict]:
    """The rules corrected again after they were confirmed, most first: the
    rules that are not holding (cr1#788 item 7)."""
    rows = [{"id": r["id"], "rule": r["rule"], "n_repeat": r["n_repeat"]}
            for r in rules(workspace_root) if r["n_repeat"] > 0]
    return sorted(rows, key=lambda r: (-r["n_repeat"], r["id"]))


# ---------------------------------------------------------------------------
# The folder
# ---------------------------------------------------------------------------

def folder_path(workspace_root) -> Path:
    return _ws(workspace_root) / RULES_DIR_REL / RULES_FILE


def rebuild(workspace_root, *, now_iso: Optional[str] = None) -> dict:
    """Write `_hq/rules/rules.json`, whole, from the sources. Retired rules
    stay in it, marked, so the folder says what stopped applying."""
    from atomic_write import atomic_write_json

    ws = _ws(workspace_root)
    evs = _events(ws)
    records = rules(ws, include_retired=True, events=evs)
    seqs = [s for s in (_seq(ev) for ev in evs) if isinstance(s, int)]
    body = {"version": RULES_VERSION, "built_at": _now_iso(now_iso),
            "source_seq": max(seqs) if seqs else 0,
            "n_live": sum(1 for r in records if r["status"] == STATUS_LIVE),
            "rules": records}
    atomic_write_json(folder_path(ws), body)
    return {"status": "written", "rel": f"{RULES_DIR_REL}/{RULES_FILE}",
            "n_rules": len(records), "n_live": body["n_live"]}


def read_folder(workspace_root) -> Optional[dict]:
    """The stored folder file, or None when it is missing or unreadable."""
    try:
        data = json.loads(folder_path(workspace_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def _append(ws: Path, row: dict) -> dict:
    from event_gate import append_event
    return append_event(ws / "_hq" / "data" / "events.jsonl", [row],
                        holder=HOLDER)[0]


def _rebuild_quietly(ws: Path, now_iso: Optional[str]) -> Optional[str]:
    """Rewrite the folder, then the brief's generated rules index, so a rule
    stated now is in view now. Neither can undo the row that was written."""
    problem = None
    try:
        rebuild(ws, now_iso=now_iso)
    except Exception as exc:  # noqa: BLE001: the row stands; the view follows
        problem = f"{type(exc).__name__}: {exc}"
    try:
        import render_claude_md
        render_claude_md.regenerate_rules_block(ws)
    except Exception as exc:  # noqa: BLE001
        problem = problem or f"brief: {type(exc).__name__}: {exc}"
    return problem


def state_rule(workspace_root, rule: str, *, applies_to: Any = EVERYWHERE,
               why: str = "", tier: str = DEFAULT_TIER,
               rule_id: Optional[str] = None, said: str = "",
               origin: str = ORIGIN_STATED, moved_from: Optional[str] = None,
               source_skill: str = SOURCE_SKILL,
               now_iso: Optional[str] = None) -> dict:
    """Store a rule the owner stated (or amend one, by `rule_id`). Called only
    after the chat confirmed it once with its scope stated (ruling C3 of
    2026-11-04). Returns `{status, rule_id, scope, line}`; `line` is the one
    sentence the chat says. A rule already stored in the same words and
    scope writes nothing (`unchanged`)."""
    ws = _ws(workspace_root)
    text = _flat(rule, RULE_CHARS)
    if len(text) < 8:
        raise RuleError("A rule needs a sentence, not a word.")
    scope = normalize_applies_to(applies_to)
    if tier not in TIERS:
        raise RuleError(f"'{tier}' is not a tier.")
    if origin not in ORIGINS:
        raise RuleError(f"'{origin}' is not an origin.")
    if rule_id is not None and not RULE_ID_RE.fullmatch(str(rule_id)):
        raise RuleError("A stated rule's id starts with rul_.")
    rid = rule_id or rule_id_for(text)
    current = _stated_records(_events(ws)).get(rid)
    if (current and current["status"] == STATUS_LIVE and current["rule"] == text
            and current["applies_to"] == scope
            and current["why"] == _flat(why, WHY_CHARS)
            and current["tier"] == tier and current["origin"] == origin):
        return {"status": "unchanged", "rule_id": rid,
                "scope": scope_words(scope),
                "line": f"That rule is already on file ({rid}), "
                        f"{scope_words(scope)}."}
    data = {"rule_id": rid, "rule": text, "why": _flat(why, WHY_CHARS),
            "applies_to": scope, "tier": tier, "origin": origin,
            "said": _flat(said, SAID_CHARS)}
    if moved_from:
        data["moved_from"] = str(moved_from)
    row = _append(ws, {"type": EV_STATED, "source_skill": source_skill,
                       "ts": _now_iso(now_iso), "data": data})
    warn = _rebuild_quietly(ws, now_iso)
    out = {"status": "amended" if current else "stated", "rule_id": rid,
           "scope": scope_words(scope), "seq": row.get("seq"),
           "line": f"Rule {rid} is on file and applies {scope_words(scope)} "
                   f"from now on: {text} Say `retire rule {rid}` to take it "
                   f"back."}
    if warn:
        out["folder_warning"] = warn
    return out


def retire_rule(workspace_root, rule_id: str, *, reason: str = "",
                source_skill: str = SOURCE_SKILL,
                now_iso: Optional[str] = None) -> dict:
    """Retire a stated rule. The record stays in the folder, marked retired;
    the row that stated it stays on the log."""
    ws = _ws(workspace_root)
    current = _stated_records(_events(ws)).get(str(rule_id or ""))
    if current is None:
        return {"status": "no_rule", "rule_id": rule_id,
                "line": "There is no stated rule with that id."}
    if current["status"] == STATUS_RETIRED:
        return {"status": "already_retired", "rule_id": rule_id,
                "line": f"Rule {rule_id} was already retired."}
    _append(ws, {"type": EV_RETIRED, "source_skill": source_skill,
                 "ts": _now_iso(now_iso),
                 "data": {"rule_id": rule_id, "reason": _flat(reason, SAID_CHARS)}})
    _rebuild_quietly(ws, now_iso)
    return {"status": "retired", "rule_id": rule_id,
            "line": f"Rule {rule_id} no longer applies."}


def record_correction(workspace_root, *, kind: str, said: str, skill: str = "",
                      rule_id: Optional[str] = None, target: str = "",
                      source_skill: str = SOURCE_SKILL,
                      now_iso: Optional[str] = None) -> dict:
    """Record a correction against what it corrects: a wording, a term, a
    figure, a rule or a way of working (cr1#788 item 6). Against a rule, it
    is attached to that rule's record, and counted as a repeat when the rule
    was already confirmed. Append-only: a correction is never edited."""
    ws = _ws(workspace_root)
    if kind not in CORRECTION_KINDS:
        raise RuleError(f"'{kind}' is not something a correction is made against.")
    text = _flat(said, SAID_CHARS)
    if not text:
        raise RuleError("A correction needs the words that were said.")
    if rule_id and str(rule_id) not in {
            r["id"] for r in rules(ws, include_retired=True)}:
        raise RuleError("There is no rule on file with that id.")
    data = {"kind": kind, "said": text, "skill": str(skill or ""),
            "target": _flat(target, SAID_CHARS)}
    if rule_id:
        data["rule_id"] = str(rule_id)
    row = _append(ws, {"type": EV_CORRECTED, "source_skill": source_skill,
                       "ts": _now_iso(now_iso), "data": data})
    if rule_id:
        _rebuild_quietly(ws, now_iso)
    return {"status": "recorded", "seq": row.get("seq"), "kind": kind,
            "rule_id": rule_id}


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("workspace")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--skill")
    ls.add_argument("--json", action="store_true")
    sub.add_parser("rebuild")
    args = ap.parse_args(argv)
    if args.cmd == "rebuild":
        print(json.dumps(rebuild(args.workspace)))
        return 0
    recs = (rules_for(args.workspace, args.skill) if args.skill
            else rules(args.workspace))
    if args.json:
        print(json.dumps(recs, indent=2, ensure_ascii=False))
    else:
        print("\n".join(index_lines(recs)) or "No rules on file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
