#!/usr/bin/env python3
"""
Voice-calibration feedback loop (SPEC B1) — make `shared/VOICE_CALIBRATION.md`
actually run.

The protocol defined a corrections-log schema, batching, and staleness rules but
nothing ever DETECTED an edit, classified it, appended a row, or refreshed a
voice block. This module is the missing machinery:

  - snapshot_draft / draft-snapshots.jsonl  — persist the drafted body so a
    drafted-vs-sent diff is possible (bodies stay OUT of events.jsonl's hot stream).
  - diff_and_classify                        — deterministic correction typing
    (phrasing | structure | vocabulary | tone). Pure; no I/O, no clock.
  - append_correction / corrections-<skill>.jsonl  — append-only, deduped.
  - reconcile_sent_against_snapshots          — async detection at Sent-reconcile.
  - load_voice_block_override / write_…       — the CUSTOMER-SIDE voice block at
    `_hq/voice/voice-block-<skill>.md` (SKILL.md blocks are plugin-side and get
    overwritten on update, so calibration MUST live in the workspace).
  - load_corrections / unreviewed_counts / group_correction_patterns — the
    monthly insight-generator Pass 11 batching reads.

CLIENT SAFETY: all writes land under `_hq/voice/` in the customer workspace —
NEVER into the plugin directory. Bodies are workspace-private (same class as
meeting transcripts); cleanup prunes snapshots. Never raises into a send path.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

try:
    from cru_match import _now_iso, _parse_ts, load_events_defensively
    from event_time import event_time
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cru_match import _now_iso, _parse_ts, load_events_defensively  # type: ignore
    from event_time import event_time  # type: ignore


def _voice_dir(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "voice"


# ---------------------------------------------------------------------------
# Classification (pure)
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "the", "a", "an", "to", "of", "and", "or", "for", "on", "in", "at", "is",
    "are", "be", "i", "you", "we", "it", "this", "that", "with", "as", "by",
    "our", "your", "my", "me", "us", "so", "but", "if", "from", "will", "can",
}
_TONE_MARKERS = {
    "hi", "hello", "dear", "hey", "thanks", "thank", "regards", "best",
    "cheers", "sincerely", "warmly", "wanted", "just", "maybe", "perhaps",
    "kindly", "please", "appreciate", "hope", "really", "very", "quick",
}


def _strip_quotes(text: str) -> str:
    """Drop reply blockquote lines so a quoted counterparty passage never
    self-reports as the user editing the draft (email-writer Phase 4 rule)."""
    return "\n".join(l for l in (text or "").splitlines() if not l.lstrip().startswith(">"))


def _paragraphs(text: str) -> List[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]


def _tokens(s: str) -> List[str]:
    return re.findall(r"[a-z0-9']+", (s or "").lower())


def _content_tokens(s: str) -> List[str]:
    return [t for t in _tokens(s) if t not in _STOPWORDS]


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s.strip()]


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(_tokens(a)), set(_tokens(b))
    if not sa and not sb:
        return 1.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 1.0


def _has_bullets(text: str) -> bool:
    return any(re.match(r"\s*[-*•]\s+", l) for l in (text or "").splitlines())


def _classify_pair(o: str, c: str) -> tuple:
    # structure: bullets <-> prose conversion
    if _has_bullets(o) != _has_bullets(c):
        return "structure", "bullets<->prose"
    # tone: the changed tokens are all greeting/sign-off/hedging markers
    changed = set(_tokens(o)) ^ set(_tokens(c))
    if changed and changed <= _TONE_MARKERS:
        return "tone", "greeting/sign-off/hedging change"
    # vocabulary: same sentence skeleton, <=3 content-word swaps per sentence, sim>=0.7
    so, sc = _sentences(o), _sentences(c)
    if so and len(so) == len(sc):
        subs, ok = [], True
        for a, b in zip(so, sc):
            if _jaccard(a, b) < 0.7:
                ok = False
                break
            ca, cb = _content_tokens(a), _content_tokens(b)
            diff = [t for t in ca if t not in cb] + [t for t in cb if t not in ca]
            if len(diff) > 3:
                ok = False
                break
            if ca != cb:
                subs.append(f"{' '.join(ca)} -> {' '.join(cb)}")
        if ok and subs:
            return "vocabulary", ("; ".join(subs))[:200]
    # phrasing: default — sentence rewritten, same intent
    return "phrasing", ""


def diff_and_classify(original: str, corrected: str) -> List[dict]:
    """Return a list of `{original, corrected, correction_type, notes}` per D4.
    Pure. One row per changed paragraph pair, capped at 5; a full rewrite
    collapses to a single `structure` row."""
    o = _strip_quotes(original or "").strip()
    c = _strip_quotes(corrected or "").strip()
    if o == c:
        return []
    po, pc = _paragraphs(o), _paragraphs(c)

    # Full rewrite: substantial text with almost nothing in common.
    if _jaccard(o, c) < 0.3 and (len(po) >= 2 or len(_tokens(o)) >= 40):
        return [{"original": o[:500], "corrected": c[:500],
                 "correction_type": "structure", "notes": "full rewrite"}]

    # Paragraph count shifted materially → one structure row.
    if abs(len(po) - len(pc)) >= 2:
        return [{"original": o[:500], "corrected": c[:500], "correction_type": "structure",
                 "notes": f"paragraph count {len(po)}->{len(pc)}"}]

    rows: List[dict] = []
    for i in range(max(len(po), len(pc))):
        a = po[i] if i < len(po) else ""
        b = pc[i] if i < len(pc) else ""
        if a.strip() == b.strip():
            continue
        ct, notes = _classify_pair(a, b)
        rows.append({"original": a[:500], "corrected": b[:500],
                     "correction_type": ct, "notes": notes})
        if len(rows) >= 5:
            break
    return rows[:5]


# ---------------------------------------------------------------------------
# Append (corrections log + draft snapshots) — workspace-side only
# ---------------------------------------------------------------------------

def _fingerprint(skill: str, original: str, corrected: str) -> str:
    return hashlib.sha256(f"{skill}\x00{original}\x00{corrected}".encode("utf-8")).hexdigest()


def append_correction(
    workspace_root, *, skill: str, domain: str, recipient_id: Optional[str],
    original: str, corrected: str, correction_type: str, notes: str = "",
    draft_event_seq=None,
) -> bool:
    """Append one correction row (VOICE_CALIBRATION schema, exact keys) to
    `_hq/voice/corrections-<skill>.jsonl`. Deduped against the log tail by
    (skill, original, corrected). Returns True if written, False if a duplicate.
    Never raises."""
    try:
        from atomic_write import atomic_append_jsonl
        path = _voice_dir(workspace_root) / f"corrections-{skill}.jsonl"
        fp = _fingerprint(skill, original, corrected)
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines()[-500:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if _fingerprint(skill, row.get("original_draft", ""), row.get("corrected_by_user", "")) == fp:
                    return False
        row = {
            "timestamp": _now_iso(),
            "skill": skill,
            "domain": domain,
            "recipient_id": recipient_id,
            "original_draft": original,
            "corrected_by_user": corrected,
            "correction_type": correction_type,
            "notes": notes,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_append_jsonl(path, [row])
        return True
    except Exception:
        return False


def snapshot_draft(
    workspace_root, *, skill: str, domain: str, recipient_id: Optional[str],
    recipient_email: Optional[str], subject: str, body: str, draft_event_seq,
    gmail_draft_id=None, gmail_message_id=None,
) -> None:
    """Append the drafted body to `_hq/voice/draft-snapshots.jsonl` so a
    drafted-vs-sent diff is possible later. Bodies stay OUT of events.jsonl."""
    try:
        from atomic_write import atomic_append_jsonl
        path = _voice_dir(workspace_root) / "draft-snapshots.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_append_jsonl(path, [{
            "ts": _now_iso(), "skill": skill, "domain": domain,
            "recipient_id": recipient_id, "recipient_email": recipient_email,
            "draft_event_seq": draft_event_seq, "gmail_draft_id": gmail_draft_id,
            "gmail_message_id": gmail_message_id, "subject": subject, "body": body,
        }])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Async detection at Sent reconcile
# ---------------------------------------------------------------------------

def _norm_subject(s: str) -> str:
    s = (s or "").strip().lower()
    while True:
        m = re.match(r"^(re|fwd|fw)\s*:\s*", s)
        if not m:
            break
        s = s[m.end():]
    return s.strip()


def _load_snapshots(workspace_root) -> List[dict]:
    path = _voice_dir(workspace_root) / "draft-snapshots.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def reconcile_sent_against_snapshots(workspace_root, sent_messages: List[dict]) -> dict:
    """Match each sent message to a draft snapshot, classify the drafted-vs-sent
    diff, append corrections. Match: exact `gmail_message_id`, else recipient +
    normalized subject + sent ts within 7 days of the snapshot. Returns
    `{n_matched, n_corrections, skills: {...}}`. Tolerant of a missing snapshot
    file; never raises."""
    result = {"n_matched": 0, "n_corrections": 0, "skills": {}}
    try:
        snaps = _load_snapshots(workspace_root)
        if not snaps:
            return result
        for sent in sent_messages or []:
            snap = _match_snapshot(sent, snaps)
            if snap is None:
                continue
            result["n_matched"] += 1
            rows = diff_and_classify(snap.get("body", ""), sent.get("body", ""))
            for r in rows:
                wrote = append_correction(
                    workspace_root, skill=snap.get("skill", "email-writer"),
                    domain=snap.get("domain", ""), recipient_id=snap.get("recipient_id"),
                    original=r["original"], corrected=r["corrected"],
                    correction_type=r["correction_type"], notes=r["notes"],
                    draft_event_seq=snap.get("draft_event_seq"),
                )
                if wrote:
                    result["n_corrections"] += 1
                    sk = snap.get("skill", "email-writer")
                    result["skills"][sk] = result["skills"].get(sk, 0) + 1
    except Exception:
        return result
    return result


def _match_snapshot(sent: dict, snaps: List[dict]) -> Optional[dict]:
    mid = sent.get("message_id")
    if mid:
        for s in snaps:
            if s.get("gmail_message_id") and s.get("gmail_message_id") == mid:
                return s
    subj = _norm_subject(sent.get("subject", ""))
    sent_dt = _parse_ts(event_time(sent))
    recips = set(sent.get("recipient_person_ids") or [])
    matches = []
    for s in snaps:
        if _norm_subject(s.get("subject", "")) != subj or not subj:
            continue
        if s.get("recipient_id") and recips and s.get("recipient_id") not in recips:
            continue
        s_dt = _parse_ts(s.get("ts"))
        if sent_dt is not None and s_dt is not None:
            from datetime import timedelta
            if abs((sent_dt - s_dt).total_seconds()) > 7 * 86400:
                continue
        matches.append(s)
    # Ambiguity → skip (a wrong correction is worse than none).
    return matches[0] if len(matches) == 1 else None


# ---------------------------------------------------------------------------
# Batching reads (insight-generator Pass 11)
# ---------------------------------------------------------------------------

def load_corrections(workspace_root, skill: Optional[str] = None) -> List[dict]:
    vd = _voice_dir(workspace_root)
    if not vd.exists():
        return []
    rows: List[dict] = []
    pattern = f"corrections-{skill}.jsonl" if skill else "corrections-*.jsonl"
    for path in sorted(vd.glob(pattern)):
        sk = path.stem[len("corrections-"):]
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            row.setdefault("skill", sk)
            rows.append(row)
    return rows


def unreviewed_counts(workspace_root) -> Dict[str, int]:
    """Per-skill count of corrections with `timestamp` after the last
    `voice_calibration_review` event's `reviewed_through[skill]` (staleness rule 2)."""
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    reviewed_through: Dict[str, str] = {}
    events, _ = load_events_defensively(events_path)
    for ev in events:
        if ev.get("type") == "voice_calibration_review":
            rt = (ev.get("data") or {}).get("reviewed_through") or {}
            if isinstance(rt, dict):
                reviewed_through = rt  # append-ordered → last wins
    counts: Dict[str, int] = {}
    for row in load_corrections(workspace_root):
        sk = row.get("skill", "")
        cutoff = reviewed_through.get(sk)
        ts = row.get("timestamp")
        if cutoff is None or (ts is not None and str(ts) > str(cutoff)):
            counts[sk] = counts.get(sk, 0) + 1
    return counts


def group_correction_patterns(rows: List[dict]) -> Dict[tuple, List[dict]]:
    """Group by (skill, correction_type, normalized original) — the 3+-same-pattern
    threshold the monthly pass keys on."""
    groups: Dict[tuple, List[dict]] = {}
    for r in rows:
        norm = re.sub(r"\s+", " ", (r.get("original_draft") or "").lower()).strip()[:80]
        key = (r.get("skill", ""), r.get("correction_type", ""), norm)
        groups.setdefault(key, []).append(r)
    return groups


# ---------------------------------------------------------------------------
# Customer-side voice-block override store
# ---------------------------------------------------------------------------

def _override_path(workspace_root, skill: str) -> Path:
    return _voice_dir(workspace_root) / f"voice-block-{skill}.md"


# Machine-readable reads of the calibrated block (B2 gate wiring). The block
# follows VOICE_CALIBRATION.md's template; both parsers are tolerant of a
# missing section (→ the safe default: nothing allowed, dashes banned).

# The Taboos section's carve-out bullet ("OK despite being on universal
# list: ...") — the ONE sanctioned source of allow_phrases for the voice-tell
# gate. Phrases here are demonstrably the client's own voice.
_TABOOS_ALLOW_RE = re.compile(
    r"^\s*[-*]?\s*OK despite being on universal list:\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_QUOTED_PHRASE_RE = re.compile(r'["“]([^"”]+)["”]')
# Punctuation section's em-dash frequency line at its strongest value.
_EM_DASH_FREQUENT_RE = re.compile(
    r"^\s*[-*]?\s*Em-dashes:\s*frequent\b", re.IGNORECASE | re.MULTILINE
)
# Whole-word only: a carve-out phrase that merely CONTAINS "dash" (e.g.
# "dashboard") is not evidence the client's voice keeps dash punctuation.
_DASH_MENTION_RE = re.compile(r"\bdash(es)?\b|—|–", re.IGNORECASE)


def parse_taboos_allow(markdown: str) -> List[str]:
    """Parse the Taboos carve-out bullet into a phrase list for the voice-tell
    gate's `allow_phrases`. Quoted phrases win when present (commas inside a
    quoted phrase survive); otherwise the remainder is comma/semicolon-split
    with parenthetical justifications dropped. The uncalibrated template
    placeholder (`[list with justification]`) and none-ish values parse to []."""
    m = _TABOOS_ALLOW_RE.search(markdown or "")
    if not m:
        return []
    raw = m.group(1).strip()
    if not raw or raw.startswith("[") or raw.rstrip(".").lower() in {"none", "n/a", "-", "—"}:
        return []
    quoted = [q.strip() for q in _QUOTED_PHRASE_RE.findall(raw) if q.strip()]
    if quoted:
        return quoted
    raw = re.sub(r"\([^)]*\)", "", raw)
    return [p.strip(" .;") for p in re.split(r"[,;]", raw) if p.strip(" .;")]


def parse_ban_dashes(markdown: str, taboos_allow: Optional[List[str]] = None) -> bool:
    """FB-16 per-client read: dashes-as-punctuation stay BANNED (True) unless
    the calibrated block is explicit that this client's voice keeps them —
    Punctuation says `Em-dashes: frequent`, or a Taboos carve-out entry names
    dashes. `rare` / `occasional` is not evidence; the product ban stays on."""
    if _EM_DASH_FREQUENT_RE.search(markdown or ""):
        return False
    allow = parse_taboos_allow(markdown) if taboos_allow is None else taboos_allow
    return not any(_DASH_MENTION_RE.search(p) for p in allow)


def load_voice_block_override(workspace_root, skill: str) -> Optional[dict]:
    """Return `{markdown, last_refreshed, calibration_level, sample_count,
    taboos_allow, ban_dashes}` for the workspace override, or None if absent.
    `taboos_allow` / `ban_dashes` are the parsed gate-wiring reads above."""
    path = _override_path(workspace_root, skill)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    def _hdr(label):
        m = re.search(rf"^{re.escape(label)}:\s*(.+)$", text, re.MULTILINE)
        return m.group(1).strip() if m else None
    taboos_allow = parse_taboos_allow(text)
    return {
        "markdown": text,
        "last_refreshed": _hdr("Last refreshed"),
        "calibration_level": _hdr("Calibration level"),
        "sample_count": _hdr("Sample count"),
        "taboos_allow": taboos_allow,
        "ban_dashes": parse_ban_dashes(text, taboos_allow),
    }


def write_voice_block_override(
    workspace_root, skill: str, block_markdown: str, *,
    calibration_level: str = "calibrated", sample_count: int = 0,
) -> Path:
    """Atomically write `_hq/voice/voice-block-<skill>.md` with a 3-line header,
    bumping `Last refreshed:`. Never touches the plugin directory."""
    from atomic_write import atomic_write_text
    path = _override_path(workspace_root, skill)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"Last refreshed: {_now_iso()}\n"
        f"Calibration level: {calibration_level}\n"
        f"Sample count: {sample_count}\n\n"
    )
    body = block_markdown if block_markdown.lstrip().startswith("## Voice Block") else f"## Voice Block\n\n{block_markdown}"
    try:
        atomic_write_text(path, header + body, holder="insight-generator")
    except Exception:
        path.write_text(header + body, encoding="utf-8")
    return path


__all__ = [
    "diff_and_classify", "append_correction", "snapshot_draft",
    "reconcile_sent_against_snapshots", "load_corrections", "unreviewed_counts",
    "group_correction_patterns", "load_voice_block_override",
    "write_voice_block_override", "parse_taboos_allow", "parse_ban_dashes",
]
