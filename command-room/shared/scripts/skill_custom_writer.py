#!/usr/bin/env python3
"""Skill customization writer — atomic read/write for per-skill freeform directives (SPEC SCL1).

Each output-producing skill that adopts the Skill Customization Layer stores its
customer's standing behavioral preferences ("directives") at:

    _hq/custom/<skill-name>.md          (one file per adopting skill; lazily created)

This helper is the canonical writer for those files — the customization sibling
of `skill_config_writer.py` (which owns the enumerated FRP1 knobs at
`_hq/data/skill_config/<skill>.json`). The split is deliberate: config JSON holds
enumerated decisions the code deep-merges; custom MD holds freeform prose the
model applies. See shared/SKILL_CUSTOMIZATION.md and shared/FIRST_RUN_PROTOCOL.md.

DESIGN (SPEC SCL1 §6.3):
  - Mirrors skill_config_writer.py in shape: canonical API, atomic writes only,
    one event per mutation, never raises to the caller.
  - Atomic writes are MANDATORY per shared/WORKSPACE_API.md §5 (the atomic-write
    mandate, MANDATORY v2.10.5+) — every write lands through
    `atomic_write.atomic_write_text` (the .md file) / `atomic_append_jsonl`
    (the events.jsonl mutation event). Direct `path.write_text` is FORBIDDEN.
    (Correction registry 2026-07-01: the atomic-write mandate is WORKSPACE_API §5,
    NOT "CONTRACT Rule 25" — Rule 25 is the runtime-resolved $WORKSPACE path rule.)
  - Every mutation emits exactly one substrate event
    (skill_customization_added / _removed / _updated / _reset). Those five types
    are registered in shared/data-schemas/events.schema.json with named consumers
    (usage-report, coach, cleanup) per the source-of-truth Writes-checklist
    item 5 — no consumer-less writes. (Correction registry 2026-07-01: the
    named-consumer requirement is Writes-checklist item 5, not a literal
    "CHECK 4".)

WRITE-TIME VALIDATION (the rejection list, §6.3/§6.6):
  add_directive rejects, with a plain-English reason the caller surfaces
  conversationally, any text that:
    - authorizes an outbound action  (send / auto-send / auto-queue / schedule
      without asking / skip confirmation / don't ask)
    - tampers with a gate            (ignore the rule / bypass / override the
      contract / disable a check)
    - grabs cross-skill scope        ("for all skills …")
    - exceeds 280 characters         (one rule per directive)
  Enforcement is layered — write-time rejection here, read-time subordination in
  the SKILL.md paragraph, and test-time adoption lint. A hostile hand edit that
  slips past this writer is still caught by the read-time contract.

CUSTOMER-FACING LANGUAGE: never surface the word "directive", the file path, or
"SCL1" to the customer. The calling skill acknowledges in plain English
("Got it — I'll always pair revenue with margin.").
"""

from __future__ import annotations

import hashlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from atomic_write import atomic_append_jsonl, atomic_write_text  # noqa: E402

CUSTOM_DIR_SUBPATH = ("_hq", "custom")
EVENTS_PATH_SUBPATH = ("_hq", "data", "events.jsonl")

SCHEMA_VERSION = 1
MAX_DIRECTIVES = 30
MAX_FILE_BYTES = 4000
MAX_DIRECTIVE_CHARS = 280

VALID_ORIGINS = {"explicit", "calibration", "learned", "org_seed"}

# --- Write-time rejection patterns (§6.3). Conservative, high-confidence. -----
# Each entry is (compiled pattern, plain-English reason surfaced to the customer).
_REJECT_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b(auto[-\s]?send|auto[-\s]?queue|send it|send them|send without|"
            r"skip (?:the )?confirm(?:ation)?|don'?t ask|without asking|"
            r"no confirmation|schedule without asking)\b",
            re.IGNORECASE,
        ),
        "That one changes what I'm allowed to send on your behalf — it lives in "
        "your email settings instead; say \"tune email-writer\".",
    ),
    (
        re.compile(
            r"(\bignore\b[^.]*?\b(?:rule|contract|gate|check|confirmation)\b|"
            r"\bbypass\b|\boverride the contract\b|"
            r"\bdisable\b[^.]*?\b(?:rule|gate|check|confirmation|safety)\b|"
            r"\bturn off\b[^.]*?\b(?:confirmation|check|gate|safety)\b|"
            r"\bskip\b[^.]*?\b(?:check|gate|safety|confirmation)\b)",
            re.IGNORECASE,
        ),
        "I can't set a preference that turns off a safety or confirmation step — "
        "those stay on so nothing goes out or changes without you.",
    ),
    (
        re.compile(
            r"\b(for all skills|across all skills|in every skill|everywhere in "
            r"command room|every skill)\b",
            re.IGNORECASE,
        ),
        "That's a rule for more than one thing at once — tell me which one it "
        "applies to and I'll set it there (I keep each preference with the thing "
        "it shapes).",
    ),
]


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _custom_dir(workspace_root: Path) -> Path:
    return workspace_root.joinpath(*CUSTOM_DIR_SUBPATH)


def _custom_path(workspace_root: Path, skill_name: str) -> Path:
    return _custom_dir(workspace_root) / f"{skill_name}.md"


def _events_path(workspace_root: Path) -> Path:
    return workspace_root.joinpath(*EVENTS_PATH_SUBPATH)


# ---------------------------------------------------------------------------
# Directive identity + validation
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Normalize directive text for stable id derivation: lowercase, collapse
    whitespace, strip. Stable across edits of *unrelated* lines (§6.3)."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def directive_id(skill: str, text: str) -> str:
    """d-<sha256[:8]>(skill|normalized_text) — used by remove/update, cooldowns,
    drift flags. Same text under the same skill always yields the same id, which
    is what makes add_directive idempotent (and org_seed re-install a no-op)."""
    digest = hashlib.sha256(f"{skill}|{_normalize(text)}".encode("utf-8")).hexdigest()
    return f"d-{digest[:8]}"


def validate_directive_text(text: str) -> tuple[bool, str | None]:
    """Return (ok, reason). Reason is a plain-English, customer-surfaceable line
    when ok is False; None when ok. Mirrors the §6.3 rejection list."""
    t = (text or "").strip()
    if not t:
        return False, "I didn't catch what you'd like me to do differently — say it once more?"
    if len(t) > MAX_DIRECTIVE_CHARS:
        return (
            False,
            "That's a few rules in one — give me one at a time and I'll keep each "
            "as its own standing preference.",
        )
    for pat, reason in _REJECT_RULES:
        if pat.search(t):
            return False, reason
    return True, None


# ---------------------------------------------------------------------------
# File format (Appendix B) — parse + serialize
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_PROVENANCE_RE = re.compile(
    r"<!--\s*id:\s*(?P<id>d-[0-9a-f]{8})?\s*\|\s*origin:\s*(?P<origin>[a-z_]+)"
    r"\s*\|\s*(?P<date>\d{4}-\d{2}-\d{2})(?:\s*\|\s*ev:\s*(?P<ev>[0-9,\s]*))?\s*-->"
)


def _parse_provenance(comment: str) -> dict[str, Any]:
    m = _PROVENANCE_RE.search(comment or "")
    if not m:
        return {}
    ev_raw = (m.group("ev") or "").strip()
    ev = [int(x) for x in re.findall(r"\d+", ev_raw)] if ev_raw else []
    return {
        "id": m.group("id"),
        "origin": m.group("origin"),
        "date": m.group("date"),
        "evidence_seqs": ev,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _parse_file(path: Path, skill: str) -> list[dict[str, Any]]:
    """Parse an existing custom file into a list of directive dicts. Never raises;
    a malformed file returns []. Bullets without a provenance comment are treated
    as hand-added explicit directives (id backfilled from text) — hand editing is
    first-class (Appendix B parse tolerance)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    # Strip frontmatter
    body = text
    fm = _FRONTMATTER_RE.match(text)
    if fm:
        body = text[fm.end():]
    # Only the Directives section is parsed; anything else is customer notes.
    idx = body.find("## Directives")
    if idx == -1:
        return []
    section = body[idx + len("## Directives"):]
    lines = section.splitlines()

    directives: list[dict[str, Any]] = []
    cur_text: list[str] | None = None
    cur_prov: dict[str, Any] = {}

    def _flush() -> None:
        nonlocal cur_text, cur_prov
        if cur_text is None:
            return
        body_text = " ".join(s.strip() for s in cur_text).strip()
        if body_text:
            did = cur_prov.get("id") or directive_id(skill, body_text)
            directives.append(
                {
                    "id": did,
                    "text": body_text,
                    "origin": cur_prov.get("origin", "explicit"),
                    "date": cur_prov.get("date", _today()),
                    "evidence_seqs": cur_prov.get("evidence_seqs", []),
                }
            )
        cur_text, cur_prov = None, {}

    for line in lines:
        stripped = line.strip()
        # A provenance comment closes the current bullet.
        if stripped.startswith("<!--"):
            cur_prov = _parse_provenance(stripped)
            continue
        if stripped.startswith("- "):
            _flush()
            cur_text = [stripped[2:].strip()]
        elif cur_text is not None and stripped and not stripped.startswith("#"):
            # Indented continuation line belongs to the same directive.
            cur_text.append(stripped)
        elif stripped.startswith("#"):
            # A new heading ends the Directives section.
            _flush()
            break
    _flush()
    return directives


def _serialize(skill: str, directives: list[dict[str, Any]], calibration_level: str) -> str:
    updated = _now_iso()
    fm = [
        "---",
        f"skill: {skill}",
        f"schema_version: {SCHEMA_VERSION}",
        f"updated_at: {updated}",
        f"directive_count: {len(directives)}",
        f"calibration_level: {calibration_level}",
        "---",
        "",
        "## Directives",
        "",
    ]
    lines = fm
    for d in directives:
        prov = f"<!-- id: {d['id']} | origin: {d['origin']} | {d['date']}"
        if d.get("evidence_seqs"):
            prov += " | ev: " + ",".join(str(s) for s in d["evidence_seqs"])
        prov += " -->"
        lines.append(f"- {d['text']}")
        lines.append(f"  {prov}")
    return "\n".join(lines) + "\n"


def _calibration_level(directives: list[dict[str, Any]]) -> str:
    origins = {d.get("origin") for d in directives}
    if "calibration" in origins:
        return "calibrated"
    if "org_seed" in origins:
        return "seeded"
    return "none"


def _emit(workspace_root: Path, skill: str, source_skill: str, event_type: str, data: dict[str, Any]) -> None:
    """Emit one mutation event via the canonical append path (gates + auto-stamps
    seq/ts). Never raises to the caller — a substrate write failure must not block
    the customer's preference from being saved to the (already-written) file."""
    event = {
        "ts": _now_iso(),
        "type": event_type,
        "source_skill": source_skill,
        "data": {"skill_name": skill, **data},
    }
    try:
        atomic_append_jsonl(_events_path(workspace_root), event)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public API (§6.3)
# ---------------------------------------------------------------------------


def add_directive(
    workspace_root: str | Path,
    skill: str,
    text: str,
    *,
    origin: str,
    evidence_seqs: list[int] | None = None,
    source_skill: str | None = None,
) -> dict[str, Any]:
    """Validate + append one directive. Returns {ok, directive_id, reason?}.

    origin ∈ {'explicit','calibration','learned','org_seed'}. Idempotent by id:
    re-adding the same text under the same skill is a no-op that returns the
    existing id (this is what makes org_seed re-install safe, §10 Table 7).

    Never raises. Caps (30 directives / 4,000 bytes) and the rejection list are
    enforced here; a rejection returns ok=False with a plain-English reason.
    """
    workspace_root = Path(workspace_root)
    if origin not in VALID_ORIGINS:
        return {"ok": False, "directive_id": None, "reason": f"invalid origin '{origin}'"}

    ok, reason = validate_directive_text(text)
    if not ok:
        return {"ok": False, "directive_id": None, "reason": reason}

    path = _custom_path(workspace_root, skill)
    directives = _parse_file(path, skill)

    new_id = directive_id(skill, text)
    # Idempotent add — already present (same normalized text).
    if any(d["id"] == new_id for d in directives):
        return {"ok": True, "directive_id": new_id, "reason": "already saved"}

    if len(directives) >= MAX_DIRECTIVES:
        return {
            "ok": False,
            "directive_id": None,
            "reason": "You've taught me a lot here already — let's fold a couple "
            "of the older ones together before I add another.",
        }

    directives.append(
        {
            "id": new_id,
            "text": text.strip(),
            "origin": origin,
            "date": _today(),
            "evidence_seqs": list(evidence_seqs or []),
        }
    )
    content = _serialize(skill, directives, _calibration_level(directives))
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        return {
            "ok": False,
            "directive_id": None,
            "reason": "You've taught me a lot here already — let's fold a couple "
            "of the older ones together before I add another.",
        }

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, content)

    data: dict[str, Any] = {
        "directive_id": new_id,
        "origin": origin,
        "directive_count": len(directives),
        "file_bytes": len(content.encode("utf-8")),
    }
    if origin == "learned" and evidence_seqs:
        data["evidence_seqs"] = list(evidence_seqs)
    _emit(workspace_root, skill, source_skill or "skill-custom-writer",
          "skill_customization_added", data)
    return {"ok": True, "directive_id": new_id, "reason": None}


def remove_directive(
    workspace_root: str | Path,
    skill: str,
    directive_id_: str,
    *,
    source_skill: str | None = None,
) -> bool:
    """Drop one directive by id. Returns True if it existed and was removed."""
    workspace_root = Path(workspace_root)
    path = _custom_path(workspace_root, skill)
    directives = _parse_file(path, skill)
    kept = [d for d in directives if d["id"] != directive_id_]
    if len(kept) == len(directives):
        return False
    if kept:
        atomic_write_text(path, _serialize(skill, kept, _calibration_level(kept)))
    else:
        # Empty file: keep a valid, hand-editable skeleton rather than leaving a
        # dangling doc. (Never delete — additive-only doctrine, §3.1.)
        atomic_write_text(path, _serialize(skill, [], "none"))
    _emit(workspace_root, skill, source_skill or "skill-custom-writer",
          "skill_customization_removed",
          {"directive_id": directive_id_, "directive_count": len(kept)})
    return True


def update_directive(
    workspace_root: str | Path,
    skill: str,
    directive_id_: str,
    text: str,
    *,
    source_skill: str | None = None,
) -> bool:
    """Replace the text of one directive (keeps its origin + date). Returns True
    if the id existed and the new text passed validation."""
    workspace_root = Path(workspace_root)
    ok, _ = validate_directive_text(text)
    if not ok:
        return False
    path = _custom_path(workspace_root, skill)
    directives = _parse_file(path, skill)
    found = False
    new_id = directive_id(skill, text)
    for d in directives:
        if d["id"] == directive_id_:
            d["text"] = text.strip()
            d["id"] = new_id  # id is content-derived; it moves with the text
            d["date"] = _today()
            found = True
            break
    if not found:
        return False
    atomic_write_text(path, _serialize(skill, directives, _calibration_level(directives)))
    _emit(workspace_root, skill, source_skill or "skill-custom-writer",
          "skill_customization_updated",
          {"directive_id": new_id, "directive_count": len(directives)})
    return True


def load_directives(workspace_root: str | Path, skill: str) -> list[dict[str, Any]]:
    """Return the current directives for a skill, or [] if absent/malformed.
    Never raises — a missing or unparseable file degrades to defaults (§6.6)."""
    return _parse_file(_custom_path(Path(workspace_root), skill), skill)


def wipe_customizations(
    workspace_root: str | Path,
    skill: str,
    *,
    source_skill: str | None = None,
) -> bool:
    """Reset a skill's customizations (the 'reset <skill> customizations' path).
    Returns True if a non-empty file existed. Leaves an empty, valid skeleton
    behind rather than deleting (additive-only, §3.1)."""
    workspace_root = Path(workspace_root)
    path = _custom_path(workspace_root, skill)
    directives = _parse_file(path, skill)
    if not directives:
        return False
    atomic_write_text(path, _serialize(skill, [], "none"))
    _emit(workspace_root, skill, source_skill or "skill-custom-writer",
          "skill_customization_reset", {"removed_count": len(directives)})
    return True


def directive_counts(workspace_root: str | Path) -> dict[str, int]:
    """Per-skill directive counts for cleanup / coach / usage-report. Reads every
    `_hq/custom/*.md`; empty dict if the directory doesn't exist yet."""
    workspace_root = Path(workspace_root)
    d = _custom_dir(workspace_root)
    out: dict[str, int] = {}
    if not d.exists():
        return out
    for p in sorted(d.glob("*.md")):
        skill = p.stem
        out[skill] = len(_parse_file(p, skill))
    return out


__all__ = [
    "add_directive",
    "remove_directive",
    "update_directive",
    "load_directives",
    "wipe_customizations",
    "directive_counts",
    "directive_id",
    "validate_directive_text",
]
