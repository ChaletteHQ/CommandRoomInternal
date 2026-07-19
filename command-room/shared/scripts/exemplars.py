#!/usr/bin/env python3
"""Exemplar library — structural gold standards per deliverable kind (SPEC OUT8).

WHY THIS EXISTS
---------------
Voice calibration taught the system WORDS from corrections; this module teaches
it STRUCTURE and DESIGN the same way. One gold-standard exemplar per
STANDARD_KIND lives beside the composer; every composer loads its kind's
exemplar as a few-shot anchor before composing; client edits and user feedback
update the exemplars through insight-generator's existing confirm-first
proposal rail (Pass 16) — never a silent write.

TWO TIERS, MIRRORING brand.py RESOLUTION
----------------------------------------
- **Shipped seeds** — `shared/exemplars/<kind>/exemplar_1.md`: the plugin's own
  gold standard per kind. SYNTHETIC placeholder content only — these files
  promote to every client repo, so every byte is treated as public.
- **Workspace exemplars** — `_hq/exemplars/<kind>/exemplar_1.md` in the client
  workspace: learned overrides, same format.

Resolution is a DEEP PREFERENCE, not a merge: workspace exemplar if present,
else shipped seed, else None — an exemplar is an exemplar, halves don't
combine. `get_exemplar` mirrors `brand.get_brand`'s posture: read at render
time, never cached across workspaces, always safe to call, never raises for a
client. Absent everywhere = None and the composer proceeds on defaults (the
pre-OUT8 world, byte-identical).

PRECEDENCE (pinned in shared/EXECUTIVE_OUTPUT_STANDARD.md)
----------------------------------------------------------
**Contract beats exemplar beats default.** An exemplar can never license
skipping the exec header, the ask cap, or any gate — it shapes what the gates
leave free. This module has NO code path into `make_brief`'s enforcement; the
chokepoint raises on a missing exec header regardless of what any exemplar
shows. Voice Blocks keep owning WORDS; exemplars own LAYOUT (the two rails
stay disjoint — see shared/VOICE_CALIBRATION.md).

STRUCTURE, NEVER FACTS
----------------------
No number, name, or claim may flow from an exemplar into a deliverable. Every
exemplar declares its placeholder tokens in a `<!-- tokens: a | b | c -->`
comment; `scan_docx_for_exemplar_tokens` / `scan_text_for_exemplar_tokens`
check rendered output for them (the composer runs this after save, warn-only,
one fix-and-resave max — the visual-pass posture).

THE LEARNING LOOP (extend, don't invent)
----------------------------------------
Structural corrections append to `_hq/exemplars/corrections-<kind>.jsonl`
(observed by reconcile-sent for sent docs, or explicit "make it like this"
feedback in chat). insight-generator Pass 16 batches them; >=3 same-direction
corrections on one kind propose a workspace-exemplar update — confirm-first,
the Pass-15 proposal shape. On confirm, `promote_workspace_exemplar` runs the
scrub gate (entity names -> placeholders, then the leak scan; residual
findings REFUSE the write) and rotates the previous exemplar_1 to exemplar_2.

Stdlib only. Read paths never raise; the promote path raises
`ExemplarScrubError` rather than ever writing a poisoned exemplar.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

_HERE = Path(__file__).resolve().parent
SEED_ROOT = _HERE.parent / "exemplars"

# Ledger pass name for proposal_ledger cooldowns (insight-generator Pass 16).
PASS_NAME = "pass16_exemplar_structure"

# >=3 same-direction corrections on one kind before a proposal fires.
PROPOSAL_THRESHOLD = 3
PROPOSAL_CAP = 3

# The canonical direction vocabulary for a structural correction. Free strings
# are accepted (the grouping key is the string itself), but capture sites
# should prefer these so repeats actually group.
KNOWN_DIRECTIONS = frozenset({
    "move_section_up",
    "move_section_down",
    "drop_section",
    "add_section",
    "merge_sections",
    "shorten",
    "lengthen",
    "tiles_first",
    "prose_first",
    "table_over_prose",
    "prose_over_table",
})

_SENTINEL_COMMENT = (
    "<!-- exemplar-skeleton (SPEC OUT8): structure only. Nothing in this file "
    "is content — no name, number, or claim below may appear in a "
    "deliverable. -->"
)

_TOKENS_RE = re.compile(r"<!--\s*tokens:\s*(.*?)\s*-->", re.DOTALL)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")

# Placeholder pools for the scrub gate. Orgs lead with the two placeholders
# the real-names guard's own failure message approves; person names come from
# its APPROVED_FIRST_NAMES / APPROVED_SURNAMES allowlist.
_ORG_PLACEHOLDERS = ["Acme Co", "Northstar Partners"]
_PERSON_PLACEHOLDERS = [
    "Sam Sample", "Quinn Stone", "Rio Sample", "Mira Stone",
    "Bo Sample", "Skyler Stone", "Aria Sample", "Lyra Stone",
]


class ExemplarScrubError(RuntimeError):
    """A candidate exemplar failed the scrub gate — the write was refused.
    Carries `findings` (leak-scan finding dicts) when the leak scan tripped."""

    def __init__(self, message: str, findings: Optional[List[dict]] = None):
        super().__init__(message)
        self.findings = findings or []


# ---------------------------------------------------------------------------
# Resolution (read path — never raises)
# ---------------------------------------------------------------------------

def _read_exemplar_file(path: Path) -> Optional[str]:
    """Defensive read: missing / unreadable / undecodable => None."""
    try:
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return text if text.strip() else None


def _kind_dir_exemplar(base: Path, kind: str) -> Optional[Path]:
    """The current exemplar file for `kind` under `base`, or None.
    exemplar_1.md is canonical; exemplar_2.md (the rotated previous version)
    is honored only when _1 is absent/unreadable, so a half-finished rotation
    never leaves a kind exemplar-less."""
    if not kind or not isinstance(kind, str) or "/" in kind or "\\" in kind:
        return None
    for name in ("exemplar_1.md", "exemplar_2.md"):
        candidate = base / kind / name
        if _read_exemplar_file(candidate) is not None:
            return candidate
    return None


def get_exemplar(
    kind: str,
    workspace_root: Union[str, "Path", None] = None,
) -> Optional[dict]:
    """Resolve the structural exemplar for a render.

    Resolution: workspace `_hq/exemplars/<kind>/exemplar_1.md` if present,
    else shipped seed `shared/exemplars/<kind>/exemplar_1.md`, else None.
    Deep preference — never a merge.

    Returns {"path": str, "text": str, "source": "workspace"|"seed"} or None.
    brand.get_brand posture verbatim: read at render time, never cached across
    workspaces, never raises for a client. None => the composer proceeds on
    its defaults, byte-identical to the pre-OUT8 world.
    """
    try:
        if workspace_root is not None:
            try:
                ws_base = Path(workspace_root) / "_hq" / "exemplars"
            except TypeError:
                ws_base = None
            if ws_base is not None:
                found = _kind_dir_exemplar(ws_base, kind)
                if found is not None:
                    text = _read_exemplar_file(found)
                    if text is not None:
                        return {"path": str(found), "text": text,
                                "source": "workspace"}
        found = _kind_dir_exemplar(SEED_ROOT, kind)
        if found is not None:
            text = _read_exemplar_file(found)
            if text is not None:
                return {"path": str(found), "text": text, "source": "seed"}
    except Exception:
        # Never raises for a client — an exemplar failure must never block a
        # render. Absent = defaults.
        return None
    return None


def seed_kinds() -> frozenset:
    """The kinds that ship a seed exemplar (dirs under shared/exemplars/ with
    a readable exemplar_1.md). The coverage test asserts this against
    brief_writer.STANDARD_KINDS. Never raises."""
    kinds = set()
    try:
        if SEED_ROOT.is_dir():
            for child in SEED_ROOT.iterdir():
                if child.is_dir() and _read_exemplar_file(
                        child / "exemplar_1.md") is not None:
                    kinds.add(child.name)
    except OSError:
        pass
    return frozenset(kinds)


# ---------------------------------------------------------------------------
# Structural corrections (the learning loop's capture side)
# ---------------------------------------------------------------------------

def corrections_path(workspace_root: Union[str, "Path"], kind: str) -> Path:
    return Path(workspace_root) / "_hq" / "exemplars" / f"corrections-{kind}.jsonl"


def append_structural_correction(
    workspace_root: Union[str, "Path"],
    *,
    kind: str,
    direction: str,
    section: str = "",
    detail: str = "",
    doc: str = "",
    source: str = "chat_feedback",
    timestamp: Optional[str] = None,
) -> bool:
    """Append one structural-correction row to
    `_hq/exemplars/corrections-<kind>.jsonl`. Returns True if written, False
    on duplicate or any failure — capture is best-effort and never raises
    (voice_corrections.append_correction posture).

    Row: {timestamp, kind, direction, section, detail, doc, source}.
    `direction` should come from KNOWN_DIRECTIONS so repeats group;
    `source` is "reconcile_sent" or "chat_feedback".
    """
    try:
        if not (kind and direction):
            return False
        path = corrections_path(workspace_root, kind)
        row = {
            "timestamp": timestamp or _utc_now_iso(),
            "kind": kind,
            "direction": direction,
            "section": section or "",
            "detail": detail or "",
            "doc": doc or "",
            "source": source or "chat_feedback",
        }
        # Dedup against the existing tail by content (ignoring timestamp).
        existing = _load_jsonl(path)[-500:]
        key = (row["kind"], row["direction"], row["section"],
               row["detail"], row["doc"])
        for prior in existing:
            if (prior.get("kind"), prior.get("direction"),
                    prior.get("section"), prior.get("detail"),
                    prior.get("doc")) == key:
                return False
        from atomic_write import atomic_append_jsonl
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_append_jsonl(path, row)
        return True
    except Exception:
        return False


def load_structural_corrections(
    workspace_root: Union[str, "Path"],
    kind: Optional[str] = None,
) -> List[dict]:
    """All structural-correction rows (optionally one kind). Tolerant of a
    missing dir / malformed lines; never raises."""
    rows: List[dict] = []
    try:
        base = Path(workspace_root) / "_hq" / "exemplars"
        if not base.is_dir():
            return rows
        if kind:
            files = [corrections_path(workspace_root, kind)]
        else:
            files = sorted(base.glob("corrections-*.jsonl"))
        for f in files:
            for row in _load_jsonl(f):
                if not row.get("kind"):
                    row["kind"] = f.name[len("corrections-"):-len(".jsonl")]
                rows.append(row)
    except OSError:
        pass
    return rows


def group_correction_patterns(
    rows: List[dict],
) -> Dict[tuple, List[dict]]:
    """Group correction rows by (kind, direction, normalized section) — the
    'same-direction' key. Pure."""
    groups: Dict[tuple, List[dict]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        kind = row.get("kind") or ""
        direction = row.get("direction") or ""
        if not (kind and direction):
            continue
        key = (kind, direction, _norm(row.get("section") or ""))
        groups.setdefault(key, []).append(row)
    return groups


def proposal_fingerprint(kind: str, direction: str, section: str = "") -> str:
    return f"{kind}:{direction}:{_norm(section)}"


def propose_exemplar_updates(
    rows: List[dict],
    *,
    cooldown_fingerprints: Optional[Set[str]] = None,
    cap: int = PROPOSAL_CAP,
    threshold: int = PROPOSAL_THRESHOLD,
) -> List[dict]:
    """Batch structural corrections into confirm-first proposals (the Pass-15
    shape). A pattern proposes when >= `threshold` corrections share the same
    (kind, direction, section) key and its fingerprint is not in cooldown.
    Pure; returns at most `cap` proposals, strongest evidence first, each:

        {fingerprint, kind, direction, section, count, sources, plain}

    `plain` is the only user-facing line — plain English, no scores, no file
    tokens (the proposal-rail contract).
    """
    cooldowns = cooldown_fingerprints or set()
    proposals: List[dict] = []
    for (kind, direction, section), group in group_correction_patterns(rows).items():
        if len(group) < threshold:
            continue
        fp = proposal_fingerprint(kind, direction, section)
        if fp in cooldowns:
            continue
        proposals.append({
            "fingerprint": fp,
            "kind": kind,
            "direction": direction,
            "section": section,
            "count": len(group),
            "sources": sorted({g.get("source") or "" for g in group} - {""}),
            "plain": _plain_line(kind, direction, section, len(group)),
        })
    proposals.sort(key=lambda p: (-p["count"], p["fingerprint"]))
    return proposals[: max(0, cap)]


_DIRECTION_PHRASES = {
    "move_section_up": "moved the {section} section earlier",
    "move_section_down": "moved the {section} section later",
    "drop_section": "removed the {section} section",
    "add_section": "added a {section} section",
    "merge_sections": "merged the {section} sections",
    "shorten": "shortened the document",
    "lengthen": "expanded the document",
    "tiles_first": "put the stat tiles above the prose",
    "prose_first": "put the prose above the stat tiles",
    "table_over_prose": "turned the {section} prose into a table",
    "prose_over_table": "turned the {section} table into prose",
}


def _plain_line(kind: str, direction: str, section: str, count: int) -> str:
    kind_label = kind.replace("_", " ")
    template = _DIRECTION_PHRASES.get(
        direction, "restructured the {section} layout".format(
            section=section or "document").replace("the  layout", "the layout")
    )
    did = template.format(section=section or "same")
    return (
        f"You've {did} in {count} recent {kind_label} documents — "
        f"make that the standard layout?"
    )


# ---------------------------------------------------------------------------
# Structure-never-facts: exemplar-token scan of rendered output
# ---------------------------------------------------------------------------

def exemplar_marker_tokens(exemplar_text: str) -> Set[str]:
    """The placeholder tokens an exemplar declares in its
    `<!-- tokens: a | b | c -->` comment(s). These are the strings that must
    NEVER appear in a rendered deliverable (structure, never facts). Pure;
    tolerant of a missing comment (=> empty set)."""
    tokens: Set[str] = set()
    if not isinstance(exemplar_text, str):
        return tokens
    for match in _TOKENS_RE.finditer(exemplar_text):
        for raw in match.group(1).split("|"):
            tok = raw.strip()
            if len(tok) >= 3:
                tokens.add(tok)
    return tokens


def scan_text_for_exemplar_tokens(
    output_text: str,
    exemplar_text: str,
) -> List[dict]:
    """Findings for every declared exemplar token that leaked into
    `output_text` (case-insensitive). [] when clean. Never raises."""
    findings: List[dict] = []
    if not isinstance(output_text, str) or not output_text:
        return findings
    lowered = output_text.lower()
    for token in sorted(exemplar_marker_tokens(exemplar_text)):
        idx = lowered.find(token.lower())
        if idx == -1:
            continue
        start, end = max(0, idx - 20), min(len(output_text), idx + len(token) + 20)
        findings.append({
            "name": "exemplar_token",
            "token": token,
            "context": output_text[start:end],
        })
    return findings


def scan_docx_for_exemplar_tokens(
    docx_path: Union[str, "Path"],
    exemplar_text: str,
) -> List[dict]:
    """Extract the .docx's paragraph text and scan it for exemplar tokens.
    Reuses docx_leak_scanner's extraction (run-boundary collapse included).
    Skips silently ([] + no error) when the scanner or file is unavailable —
    the same fail-open posture as brief_writer's post-render leak-scan import
    (this is a warn-only quality check, not a blocking gate)."""
    try:
        from docx_leak_scanner import _read_document_xml, _docx_paragraph_text
        text = _docx_paragraph_text(_read_document_xml(Path(docx_path)))
    except Exception:
        return []
    return scan_text_for_exemplar_tokens(text, exemplar_text)


# ---------------------------------------------------------------------------
# The scrub gate + promote (write path — refuses rather than poisons)
# ---------------------------------------------------------------------------

def scrub_exemplar_text(
    text: str,
    entities: Union[dict, str, "Path", None] = None,
) -> Tuple[str, List[dict]]:
    """Replace workspace entity names (orgs, persons, threads/deals) and
    non-example email addresses in `text` with synthetic placeholders.
    Returns (scrubbed_text, replacements) where each replacement is
    {"original": ..., "placeholder": ...}. Pure over its inputs; a missing /
    unreadable entities source scrubs emails only. Deterministic: names are
    assigned placeholders in sorted order, longest name replaced first."""
    if not isinstance(text, str):
        return "", []
    replacements: List[dict] = []
    ents = _load_entities(entities)

    org_names: Set[str] = set()
    person_names: Set[str] = set()
    if ents:
        inner = ents.get("entities") if isinstance(ents.get("entities"), dict) else {}
        for org in inner.get("orgs") or []:
            if isinstance(org, dict):
                for field in ("canonical_name", "legal_name"):
                    if isinstance(org.get(field), str):
                        org_names.add(org[field])
                org_names.update(_str_list(org.get("aliases")))
        for person in inner.get("persons") or []:
            if isinstance(person, dict):
                if isinstance(person.get("canonical_name"), str):
                    person_names.add(person["canonical_name"])
                person_names.update(_str_list(person.get("aliases")))
                person_names.update(_str_list(person.get("nicknames")))
        for thread in inner.get("threads") or []:
            if isinstance(thread, dict) and isinstance(
                    thread.get("canonical_name"), str):
                org_names.add(thread["canonical_name"])
        workspace = ents.get("workspace")
        if isinstance(workspace, dict) and isinstance(
                workspace.get("user_name"), str):
            person_names.add(workspace["user_name"])

    # Assign placeholders deterministically; replace longest-first so
    # "Acme Corp International" never leaves a dangling "International".
    scrubbed = text
    assigned: List[Tuple[str, str]] = []
    for i, name in enumerate(sorted(org_names, key=lambda n: (n.lower()))):
        pool = _ORG_PLACEHOLDERS
        placeholder = pool[i] if i < len(pool) else f"Sample Org {i + 1}"
        assigned.append((name, placeholder))
    for i, name in enumerate(sorted(person_names, key=lambda n: (n.lower()))):
        pool = _PERSON_PLACEHOLDERS
        placeholder = pool[i % len(pool)] if i < len(pool) \
            else f"Sam Sample {i + 1}"
        assigned.append((name, placeholder))
    for name, placeholder in sorted(assigned, key=lambda p: -len(p[0])):
        if len(name.strip()) < 3:
            continue  # a 1-2 char alias would shred unrelated words
        pattern = re.compile(
            r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
        if pattern.search(scrubbed):
            scrubbed = pattern.sub(placeholder, scrubbed)
            replacements.append({"original": name, "placeholder": placeholder})

    # Emails: anything not on an example/test domain becomes the sample.
    def _email_sub(match: "re.Match") -> str:
        domain = match.group(1).lower()
        if domain == "example.com" or domain.endswith(".example.com") \
                or domain in ("example.org", "example.net"):
            return match.group(0)
        replacements.append({"original": match.group(0),
                             "placeholder": "sam.sample@example.com"})
        return "sam.sample@example.com"

    scrubbed = _EMAIL_RE.sub(_email_sub, scrubbed)
    return scrubbed, replacements


_CAND_WORD = r"[A-Z][A-Za-z'’\-]*[a-z][A-Za-z'’\-]*"
_TITLECASE_SEQ_RE = re.compile(
    r"\b" + _CAND_WORD + r"(?: " + _CAND_WORD + r")+\b")
_MONEY_RE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?\s?(?:[KMB]\b|million\b|billion\b)?")
# Words so common a sequence made ONLY of them is never a name.
_CAND_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "not", "for", "with", "from",
    "this", "that", "these", "those", "what", "when", "where", "who", "why",
    "how", "you", "your", "it", "its", "is", "are", "was", "be", "to", "of",
    "in", "on", "at", "by", "no", "one", "two", "three",
})


def residual_name_candidates(text: str) -> List[str]:
    """Name-shaped tokens in a candidate exemplar that the scrub gate could
    NOT vouch for: capitalized multi-word sequences (Title Case runs of 2+
    words) and dollar figures, minus the known synthetic placeholders and
    anything the text's own `<!-- tokens: … -->` comment already declares.

    This is the third scrub layer (review F-1, 2026-07-16): `scrub_exemplar_text`
    only knows the workspace entity list, and the leak scan only knows the
    static forbidden-token vocabulary — a counterparty name that is in
    NEITHER (untracked org, unlogged person, a real deal figure) passes both.
    These candidates must be confirm-listed to the user on the Pass 16 card;
    `promote_workspace_exemplar` refuses to write while any of them is
    unconfirmed. Deliberately noisy-but-cheap: section headings like
    "Meeting Details" will appear — promotes are rare and user-reviewed, and
    a false listing costs one glance where a miss is a standing leak.

    Pure; sorted + deduped; never raises. ALL-CAPS annotation vocabulary
    (CHANGED, DECIDE) and single capitalized words are out of scope — the
    entity scrub and the user's own read cover those.
    """
    if not isinstance(text, str) or not text:
        return []
    known = {p.lower() for p in _ORG_PLACEHOLDERS + _PERSON_PLACEHOLDERS}
    known |= {t.lower() for t in exemplar_marker_tokens(text)}
    generated = re.compile(r"(?i)^(sample org|sam sample)( \d+)?$")
    out: Set[str] = set()
    for m in _TITLECASE_SEQ_RE.finditer(text):
        seq = m.group(0)
        low = seq.lower()
        if low in known or generated.match(seq):
            continue
        if all(w in _CAND_STOPWORDS for w in low.split()):
            continue
        out.add(seq)
    for m in _MONEY_RE.finditer(text):
        fig = m.group(0).strip()
        if fig.lower() not in known:
            out.add(fig)
    return sorted(out)


def promote_workspace_exemplar(
    workspace_root: Union[str, "Path"],
    kind: str,
    new_text: str,
    *,
    entities: Union[dict, str, "Path", None] = None,
    confirmed_residuals: Optional[List[str]] = None,
) -> dict:
    """Write a confirmed exemplar update to
    `_hq/exemplars/<kind>/exemplar_1.md`, rotating the previous version to
    `exemplar_2.md`. CONFIRM-FIRST ONLY: the sole legitimate caller is
    insight-generator Pass 16 after an explicit user confirm (or the user
    asking for it in so many words) — a silently mutating gold standard is
    drift with a title.

    The scrub gate is three layers, all fail-closed:
      1. entity names -> placeholders (`scrub_exemplar_text` — knows only the
         workspace entity list);
      2. the shared leak scan over the scrubbed text (knows only the static
         forbidden-token vocabulary);
      3. `residual_name_candidates` over the scrubbed text — the name-shaped
         tokens NEITHER layer can vouch for (untracked orgs/persons, dollar
         figures). Every candidate must appear in `confirmed_residuals` (the
         user-confirmed list from the Pass 16 card) or the write is REFUSED.
         A real name belongs replaced with a placeholder, never confirmed
         through.
    Residual findings raise ExemplarScrubError — a poisoned name is REFUSED,
    never written. The leak scanner being unavailable also refuses (this is a
    write gate; it fails closed, unlike the read-path scans above).

    Returns {"path", "rotated", "replacements", "residuals"}.
    """
    if not (isinstance(kind, str) and kind and "/" not in kind
            and "\\" not in kind):
        raise ValueError(f"invalid exemplar kind: {kind!r}")
    if not (isinstance(new_text, str) and new_text.strip()):
        raise ValueError("empty exemplar text")

    scrubbed, replacements = scrub_exemplar_text(
        new_text, entities if entities is not None else workspace_root)

    try:
        from docx_leak_scanner import scan_text_for_leaks
    except ImportError as exc:
        raise ExemplarScrubError(
            "leak scanner unavailable — refusing to promote an unscanned "
            "exemplar") from exc
    findings = scan_text_for_leaks(scrubbed)
    if findings:
        raise ExemplarScrubError(
            f"candidate exemplar for {kind!r} failed the leak scan "
            f"({len(findings)} finding(s)) — write refused",
            findings=findings,
        )

    # Layer 3 (review F-1): name-shaped tokens neither the entity scrub nor
    # the leak vocabulary can vouch for must be user-confirmed, or we refuse.
    candidates = residual_name_candidates(scrubbed)
    confirmed = {_norm(c) for c in (confirmed_residuals or [])}
    unconfirmed = [c for c in candidates if _norm(c) not in confirmed]
    if unconfirmed:
        raise ExemplarScrubError(
            f"candidate exemplar for {kind!r} carries {len(unconfirmed)} "
            f"name-shaped token(s) the workspace entity list cannot vouch "
            f"for: {unconfirmed[:8]} — list each on the Pass 16 confirm "
            f"card and pass the user-confirmed set as confirmed_residuals=; "
            f"a REAL name or figure gets replaced with a placeholder, never "
            f"confirmed through",
            findings=[{"name": "residual_candidate", "match": c}
                      for c in unconfirmed],
        )

    # Ensure the standing header: sentinel + a tokens declaration covering the
    # placeholders now present, so the rendered-output token scan has teeth.
    if "exemplar-skeleton" not in scrubbed:
        scrubbed = _SENTINEL_COMMENT + "\n" + scrubbed
    if not _TOKENS_RE.search(scrubbed):
        present = sorted(
            {r["placeholder"] for r in replacements}
            | {p for p in _ORG_PLACEHOLDERS + _PERSON_PLACEHOLDERS
               if p.lower() in scrubbed.lower()}
        )
        if present:
            head, sep, tail = scrubbed.partition("\n")
            scrubbed = head + sep + \
                "<!-- tokens: " + " | ".join(present) + " -->\n" + tail

    from atomic_write import atomic_write_text
    target_dir = Path(workspace_root) / "_hq" / "exemplars" / kind
    target_dir.mkdir(parents=True, exist_ok=True)
    current = target_dir / "exemplar_1.md"
    rotated = False
    prior = _read_exemplar_file(current)
    if prior is not None:
        atomic_write_text(target_dir / "exemplar_2.md", prior)
        rotated = True
    atomic_write_text(current, scrubbed)
    return {"path": str(current), "rotated": rotated,
            "replacements": replacements, "residuals": candidates}


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _str_list(val) -> Set[str]:
    if not isinstance(val, list):
        return set()
    return {v for v in val if isinstance(v, str) and v.strip()}


def _load_jsonl(path: Path) -> List[dict]:
    rows: List[dict] = []
    try:
        if not path.is_file():
            return rows
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    except (OSError, UnicodeDecodeError):
        pass
    return rows


def _load_entities(
    entities: Union[dict, str, "Path", None],
) -> Optional[dict]:
    """brand._load_entities posture: dict passes through; a path reads
    workspace_root/_hq/data/entities.json (or a direct file path)
    defensively. Never raises."""
    if entities is None:
        return None
    if isinstance(entities, dict):
        return entities
    try:
        root = Path(entities)
    except TypeError:
        return None
    path = root / "_hq" / "data" / "entities.json" if root.is_dir() else root
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


__all__ = [
    "PASS_NAME",
    "PROPOSAL_THRESHOLD",
    "PROPOSAL_CAP",
    "KNOWN_DIRECTIONS",
    "ExemplarScrubError",
    "get_exemplar",
    "seed_kinds",
    "corrections_path",
    "append_structural_correction",
    "load_structural_corrections",
    "group_correction_patterns",
    "proposal_fingerprint",
    "propose_exemplar_updates",
    "exemplar_marker_tokens",
    "scan_text_for_exemplar_tokens",
    "scan_docx_for_exemplar_tokens",
    "scrub_exemplar_text",
    "residual_name_candidates",
    "promote_workspace_exemplar",
]
