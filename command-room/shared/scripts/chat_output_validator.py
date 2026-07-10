#!/usr/bin/env python3
"""
Pre-flight chat-output validator for Command Room scheduled tasks.

Catches leaks the renderer should have prevented but might miss if an
orchestrator hand-rolls part of the output OR if the LLM bypasses the
renderer and writes prose directly.

USAGE:

    from chat_output_validator import validate_chat_output, ValidationResult
    result = validate_chat_output(rendered_text)
    if result.violations:
        # log + reject + force re-render
        ...

VIOLATION CATEGORIES (negative — flag presence of):

  - entity_id_leak       — org_NNN, person_NNN, event_NNN, project_NNN, engagement_NNN
  - phase_label_leak     — "Phase N — ..." narration of internal scaffolding
  - internal_path_leak   — events.jsonl, _hq/data/, staging_emissions, _unrouted/, _backups
  - tool_name_leak       — present_files, people-crm, scan-for-commitments, EMAIL_DRAFT_PROTOCOL
  - flag_name_leak       — --force, --debug, --dry-run, --refresh
  - empty_subject        — Subject: Re: with nothing after the colon
  - mojibake             — UTF-8 mis-decode bytes (â€œ â€" etc.)
  - telemetry_narration  — "pack_run seq", "Logged: pack_run", "(seq N-N)"
  - threshold_rationale  — "Degraded baseline mode", "obs=N", "absolute thresholds"
  - internal_vocab_leak  — the shared INTERNAL_VOCAB list (vocabulary_policy.py):
                           substrate, dispatch layer, payload, canonical
                           renderer/writer/reader/path, audit marker,
                           <run-summary> tags, bootloader, fire-marker,
                           bare "seq N" refs (v4.6.1 S3 — the F-14 pile)
  - sender_email_leak_in_first_line — email appears in item first line instead of in To: metadata

VIOLATION CATEGORIES (positive — flag absence of, v2.10.8+):

  - missing_item_number     — item icon (✉ 📅 📄 👤 📁 ⚙) at start of line without N. prefix
  - missing_pill_marker     — item block has no ▸ pill row anywhere in its lines
  - missing_italic_reply_body — ✉ email_reply block's Body: section has lines not wrapped in *...*
  - missing_item_separator  — consecutive item blocks not separated by --- horizontal rule
  - missing_bold_name       — item first line has no non-numeric bold (**Name**)
  - missing_quoted_subject  — ✉ email_reply / 📅 calendar_invite item first line has no "Quoted subject"

The validator is designed to be FAST (regex-only) and STRICT (false positives
cost a re-render; false negatives ship a broken chat turn). Tune patterns
conservatively — when in doubt, flag.
"""

from __future__ import annotations

import re
import sys as _sys
from dataclasses import dataclass, field
from pathlib import Path as _Path

try:
    from vocabulary_policy import internal_vocab_patterns as _internal_vocab_patterns
except ImportError:  # pragma: no cover — direct-path import fallback
    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from vocabulary_policy import internal_vocab_patterns as _internal_vocab_patterns


@dataclass
class Violation:
    category: str
    pattern: str
    matched: str
    line_number: int
    context: str  # the line where the match appeared


@dataclass
class ValidationResult:
    violations: list[Violation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.violations) == 0

    def summary(self) -> str:
        if self.ok:
            return "✓ chat output passes all checks"
        lines = [f"⚠ {len(self.violations)} chat-output violations:"]
        for v in self.violations:
            lines.append(
                f"  [{v.category}] line {v.line_number}: {v.matched!r} — context: {v.context.strip()[:120]}"
            )
        return "\n".join(lines)


# Pattern definitions — each tuple is (category, regex, description)
PATTERNS: list[tuple[str, str, str]] = [
    # Entity ID leaks (the most common class)
    ("entity_id_leak", r"\borg_\d{3,}\b", "org_NNN canonical-id leak"),
    ("entity_id_leak", r"\bperson_\d{3,}\b", "person_NNN canonical-id leak"),
    ("entity_id_leak", r"\bevent_\d{3,}\b", "event_NNN canonical-id leak"),
    ("entity_id_leak", r"\bproject_\d{3,}\b", "project_NNN canonical-id leak"),
    ("entity_id_leak", r"\bengagement_\d{3,}\b", "engagement_NNN canonical-id leak"),
    ("entity_id_leak", r"\bperson_[a-z0-9_]+\b", "person_<slug> canonical-id leak"),
    ("entity_id_leak", r"\borg_[a-z0-9_]+\b(?<!org_001)(?<!org_002)(?<!org_003)", "org_<slug> canonical-id leak"),
    # Phase label leaks (real fire saw "Phase 7 — silent memory updates")
    ("phase_label_leak", r"^Phase \d+\b", "Phase N — narration of internal scaffolding"),
    ("phase_label_leak", r"#\s*Phase \d+\b", "# Phase N markdown heading leak"),
    # Internal data-substrate path leaks
    ("internal_path_leak", r"\bevents\.jsonl\b", "events.jsonl path leak"),
    ("internal_path_leak", r"\bentities\.json\b", "entities.json path leak"),
    ("internal_path_leak", r"\baliases\.json\b", "aliases.json path leak"),
    ("internal_path_leak", r"\bstaging_emissions(\.jsonl)?\b", "staging_emissions path leak"),
    ("internal_path_leak", r"\bclassifier_feedback(\.jsonl)?\b", "classifier_feedback path leak"),
    ("internal_path_leak", r"_hq/data/", "_hq/data/ path leak"),
    ("internal_path_leak", r"_hq/staging/", "_hq/staging/ path leak"),
    ("internal_path_leak", r"\b_unrouted/", "_unrouted/ folder name leak"),
    ("internal_path_leak", r"_backups/", "_backups/ folder leak"),
    ("internal_path_leak", r"\bpre-engagements? backup\b", "backup-name leak"),
    # Tool name leaks
    ("tool_name_leak", r"\bpresent_files\b", "present_files tool leak"),
    ("tool_name_leak", r"\bcreate_artifact\b", "create_artifact tool leak"),
    ("tool_name_leak", r"\bcreate_draft\b", "create_draft tool leak"),
    ("tool_name_leak", r"\bsend_draft\b", "send_draft tool leak"),
    ("tool_name_leak", r"\bcreate_label\b", "create_label tool leak"),
    ("tool_name_leak", r"\bEMAIL_DRAFT_PROTOCOL\b", "EMAIL_DRAFT_PROTOCOL doc-name leak"),
    ("tool_name_leak", r"\bSHARED_CHAT_OUTPUT_PROTOCOL\b", "SHARED_CHAT_OUTPUT_PROTOCOL doc-name leak"),
    # Flag name leaks
    ("flag_name_leak", r"--force\b", "--force flag leak"),
    ("flag_name_leak", r"--debug\b", "--debug flag leak"),
    ("flag_name_leak", r"--dry-run\b", "--dry-run flag leak"),
    ("flag_name_leak", r"--refresh\b", "--refresh flag leak"),
    # Empty subject (real fire saw "Subject: Re:")
    ("empty_subject", r"^Subject:\s*$", "Subject: line with no content"),
    ("empty_subject", r"^Subject:\s*Re:\s*$", "Subject: Re: with nothing after"),
    # Telemetry narration leaks
    ("telemetry_narration", r"\bpack_run seq \d+", "pack_run seq number leak"),
    ("telemetry_narration", r"\(seq \d+(-\d+)?\)", "(seq N) parenthetical leak"),
    ("telemetry_narration", r"^Logged:\s+pack_run\b", "Logged: pack_run prefix leak"),
    ("telemetry_narration", r"\bdraft_created event\b", "draft_created event-name leak"),
    ("telemetry_narration", r"\bconnector_read event", "connector_read event-name leak"),
    # Threshold rationales
    ("threshold_rationale", r"Degraded baseline mode", "Degraded baseline mode rationale leak"),
    ("threshold_rationale", r"\bobs=\d+", "obs=N abbreviation leak"),
    ("threshold_rationale", r"absolute thresholds", "absolute thresholds rationale leak"),
    ("threshold_rationale", r"statistical baseline activates", "statistical baseline activates rationale leak"),
    # Mojibake (UTF-8 misdecode markers)
    ("mojibake", r"â€œ", "UTF-8 mojibake — left double quote"),
    ("mojibake", r"â€", "UTF-8 mojibake — right double quote"),
    ("mojibake", r"â€\"", "UTF-8 mojibake — em-dash"),
    ("mojibake", r"â€™", "UTF-8 mojibake — right apostrophe"),
    # Engineer phrasing leaks
    ("engineer_phrase_leak", r"force re-emit", "force re-emit phrasing leak"),
    ("engineer_phrase_leak", r"force re-emitted", "force re-emitted phrasing leak"),
    ("engineer_phrase_leak", r"PT-bounded", "PT-bounded phrasing leak"),
    ("engineer_phrase_leak", r"provenance footer applied", "provenance footer phrasing leak"),
    ("engineer_phrase_leak", r"pending_enrichment rows queued", "pending_enrichment phrasing leak"),
    ("engineer_phrase_leak", r"truncated mid-file at", "truncated mid-file phrasing leak"),
    ("engineer_phrase_leak", r"engagement-state should reclassify", "engagement-state phrasing leak"),
    # Internal architecture vocabulary (v4.6.1 S3 — the F-14 pile, sourced
    # from the ONE shared list in vocabulary_policy.INTERNAL_VOCAB; the
    # dogfood-quoted narrations "closing it in the substrate", "through the
    # canonical renderer", "Writing the audit marker", raw <run-summary>
    # tags, and bare seq numbers in Sources lines all match here)
    *(
        ("internal_vocab_leak", _rx, f"{_tid} internal-vocabulary leak")
        for _tid, _rx in _internal_vocab_patterns()
    ),
]

# Item icons and the type each implies
ITEM_ICONS = {
    "✉": "email_reply",
    "📅": "calendar_invite",
    "📄": "contract",
    "👤": "person",
    "📁": "project",
    "⚙": "self_commitment",
}
ITEM_ICON_CHARS = "".join(ITEM_ICONS.keys())

# First-line item icons (must be followed by N. somewhere reasonable)
ITEM_ICON_PATTERN = re.compile(rf"^[\s\-\*]*([{ITEM_ICON_CHARS}])\s")
ITEM_NUMBER_PATTERN = re.compile(r"\*\*\d+\.\*\*")  # markdown bold N. format

# Item-block start: bold-N. prefix immediately followed by an icon
ITEM_BLOCK_START_PATTERN = re.compile(rf"^\s*\*\*\d+\.\*\*\s*([{ITEM_ICON_CHARS}])")

# Per-element regexes for positive-presence checks (v2.10.8+)
PILL_MARKER_PATTERN = re.compile(r"▸")
BOLD_NON_NUMBER_PATTERN = re.compile(r"\*\*([A-Za-z][^*]*[A-Za-z0-9])\*\*")  # **Name** but not **1.**
QUOTED_SUBJECT_PATTERN = re.compile(r'"[^"]+"')
ITALIC_LINE_PATTERN = re.compile(r"^\*[^*].*\*\s*$")  # *...* whole-line italic
EMAIL_IN_FIRST_LINE_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
HORIZONTAL_RULE_PATTERN = re.compile(r"^---+\s*$")
BODY_LABEL_PATTERN = re.compile(r"^Body:\s*$")


def _iter_item_blocks(lines: list[str]):
    """Yield (start_idx, end_idx, icon_char, icon_type) for each item block.

    A block starts at a line matching ITEM_BLOCK_START_PATTERN and ends at
    the next such start OR at end of text. Indices are 0-based into lines.
    """
    starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = ITEM_BLOCK_START_PATTERN.match(line)
        if m:
            starts.append((i, m.group(1)))

    for k, (start, icon_char) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
        yield start, end, icon_char, ITEM_ICONS.get(icon_char, "unknown")


def _check_block_positive_presence(
    lines: list[str],
    start: int,
    end: int,
    icon_char: str,
    icon_type: str,
    result: ValidationResult,
) -> None:
    """Run per-block positive-presence checks for v2.10.8+.

    Adds violations to `result` for missing pill marker, missing italic
    reply body (email_reply only), missing bold name on first line,
    missing quoted subject (email_reply / calendar_invite only), and
    sender_email_leak_in_first_line.
    """
    block = lines[start:end]
    first_line = block[0] if block else ""
    block_text = "\n".join(block)
    line_num = start + 1  # 1-based for reporting

    # Pill marker (▸) must appear at least once in the block
    if not PILL_MARKER_PATTERN.search(block_text):
        result.violations.append(
            Violation(
                category="missing_pill_marker",
                pattern="▸",
                matched=first_line.strip()[:60],
                line_number=line_num,
                context=first_line,
            )
        )

    # Bold non-numeric name on first line (**Name**, not just **N.**)
    bold_matches = BOLD_NON_NUMBER_PATTERN.findall(first_line)
    if not bold_matches:
        result.violations.append(
            Violation(
                category="missing_bold_name",
                pattern="**Name**",
                matched=first_line.strip()[:60],
                line_number=line_num,
                context=first_line,
            )
        )

    # Quoted subject on first line — only required for email_reply and calendar_invite
    if icon_type in {"email_reply", "calendar_invite"}:
        if not QUOTED_SUBJECT_PATTERN.search(first_line):
            result.violations.append(
                Violation(
                    category="missing_quoted_subject",
                    pattern='"..."',
                    matched=first_line.strip()[:60],
                    line_number=line_num,
                    context=first_line,
                )
            )

    # Sender email leak in first line — addresses belong in To:/From: metadata, not in the headline
    email_in_first = EMAIL_IN_FIRST_LINE_PATTERN.search(first_line)
    if email_in_first:
        result.violations.append(
            Violation(
                category="sender_email_leak_in_first_line",
                pattern="email@domain in headline",
                matched=email_in_first.group(0),
                line_number=line_num,
                context=first_line,
            )
        )

    # Italic body wrap — only required for email_reply blocks with a Body: section
    if icon_type == "email_reply":
        body_start = None
        for offset, line in enumerate(block):
            if BODY_LABEL_PATTERN.match(line):
                body_start = offset + 1
                break
        if body_start is not None:
            # Body lines run from body_start until next blank line or end of block
            body_offset = body_start
            while body_offset < len(block) and block[body_offset].strip() != "":
                body_line = block[body_offset]
                # Skip pill rows that may have followed Body: without a blank gap (defensive)
                if PILL_MARKER_PATTERN.search(body_line):
                    break
                if not ITALIC_LINE_PATTERN.match(body_line):
                    result.violations.append(
                        Violation(
                            category="missing_italic_reply_body",
                            pattern="*...*",
                            matched=body_line.strip()[:60],
                            line_number=start + body_offset + 1,
                            context=body_line,
                        )
                    )
                body_offset += 1


def _check_item_separators(
    lines: list[str],
    blocks: list[tuple[int, int, str, str]],
    result: ValidationResult,
) -> None:
    """Between consecutive item blocks, verify a `---` horizontal rule appears
    somewhere in the gap. Skip before first block and after last block.
    """
    for k in range(len(blocks) - 1):
        _, end_a, _, _ = blocks[k]
        start_b, _, _, _ = blocks[k + 1]
        # Lines between block A's end and block B's start
        gap = lines[end_a:start_b]
        if not any(HORIZONTAL_RULE_PATTERN.match(line) for line in gap):
            # First line of block B as the locus for reporting
            line_num = start_b + 1
            result.violations.append(
                Violation(
                    category="missing_item_separator",
                    pattern="--- horizontal rule",
                    matched=lines[start_b].strip()[:60] if start_b < len(lines) else "",
                    line_number=line_num,
                    context=lines[start_b] if start_b < len(lines) else "",
                )
            )


def validate_chat_output(text: str) -> ValidationResult:
    """Run all validation patterns against the rendered chat text. Returns
    a ValidationResult with violations + warnings.

    Args:
        text: the fully rendered chat string about to be posted.

    Returns:
        ValidationResult with .ok bool and .violations list.
    """
    result = ValidationResult()
    lines = text.split("\n")

    # Pattern-based violations (negative — flag presence of)
    for line_num, line in enumerate(lines, start=1):
        for category, pattern, _description in PATTERNS:
            for match in re.finditer(pattern, line, re.IGNORECASE if category != "phase_label_leak" else 0):
                result.violations.append(
                    Violation(
                        category=category,
                        pattern=pattern,
                        matched=match.group(0),
                        line_number=line_num,
                        context=line,
                    )
                )

    # Item-numbering check: any line starting with a recognized item icon
    # MUST also contain an N. prefix somewhere (in a markdown-bold form).
    for line_num, line in enumerate(lines, start=1):
        if ITEM_ICON_PATTERN.search(line):
            window = " ".join(lines[max(0, line_num - 2): line_num])
            if not ITEM_NUMBER_PATTERN.search(window):
                result.violations.append(
                    Violation(
                        category="missing_item_number",
                        pattern="N. prefix",
                        matched=line.strip()[:60],
                        line_number=line_num,
                        context=line,
                    )
                )

    # Positive-presence per-item-block checks (v2.10.8+)
    blocks = list(_iter_item_blocks(lines))
    for start, end, icon_char, icon_type in blocks:
        _check_block_positive_presence(lines, start, end, icon_char, icon_type, result)

    # Item-separator check between consecutive blocks
    _check_item_separators(lines, blocks, result)

    return result


__all__ = ["validate_chat_output", "ValidationResult", "Violation"]


def main() -> int:
    """CLI mode: read text from stdin or --input file, print validation report."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Validate Command Room chat output for format/leak violations.")
    parser.add_argument("--input", type=str, default=None, help="Path to text file. Stdin if omitted.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if any violations.")
    args = parser.parse_args()

    if args.input:
        text = open(args.input, encoding="utf-8").read()
    else:
        text = sys.stdin.read()

    result = validate_chat_output(text)
    print(result.summary())
    if args.strict and not result.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
