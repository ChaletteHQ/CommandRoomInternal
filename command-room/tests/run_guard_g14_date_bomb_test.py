#!/usr/bin/env python3
"""Guard G14 — hardcoded future-date time bombs in test fixtures.

The MC3 class: a test hardcodes a "future" ISO date (due='2026-07-11' at
authoring time) and feeds it to a path that stamps status against the REAL
clock (build_slack_commitment_event has no injectable now). The day the date
passes, the expected status silently flips and the battery goes RED on every
branch — 2026-07-15 this took down the entire frozen v4.6.3 client fleet at
once. Fixed case: run_v46_mc3_slack_capture_test.py (commit 2a12674).

Structural rule enforced here: a test file may not contain a hardcoded ISO
date literal that is TODAY OR LATER at guard-run time. Such a literal is a
bomb-in-waiting the moment any status/window derivation touches the real
clock — and reviewers cannot re-audit every sink on every edit. Fixtures that
need a future date compute it (today + timedelta(...)); fixtures that need a
stable past date may hardcode it freely (a past date only moves further into
the past, so overdue-class status never flips).

Allowed without annotation:
  * past dates — the deliberate historic-DATA-event class (the bulk of every
    fixture substrate);
  * far-future sentinels >= 50 years out (2099-01-15 style "never expires"
    markers — stable across any plausible product lifetime);
  * dates inside docstrings / bare string statements — a discarded string
    literal structurally cannot reach a status path (prose like "M rulings
    2026-07-15" in a module header is documentation, not fixture data).

Allowed with annotation (each site consciously judged safe):
  * a trailing  # DATE_GUARD_OK: <reason>  comment on the SAME line — used
    where the date is pure pass-through shape data, is compared only against
    a pinned/injected clock (now_iso=NOW), or IS the injected as-of clock
    itself;
  * an ALLOWLIST entry below for .json/.jsonl fixtures (no comments in JSON).

A pragma with an empty reason fails — the reason is the point.

Known limits (documented, not enforced): near-past dates inside recency
windows (a "modified within 30d" fixture goes stale as time passes) are not
detectable without dataflow analysis — pin the clock in those suites;
datetime(2026, 9, 1)-style constructor literals are not scanned (in this
tree they are the injected clocks themselves, e.g. now=dt.datetime(...));
a date split across adjacent implicitly-concatenated literals or around an
f-string replacement field ("2026-08" "-01", f"2026-08-{d}") is not seen —
the regex runs per token, and no accidental fixture takes that shape.
A pragma on a date that has since drifted into the past is redundant but
never an error — this guard must not grow its own time bombs.

Run: PYTHONUTF8=1 python tests/run_guard_g14_date_bomb_test.py
"""
from __future__ import annotations

import datetime
import io
import re
import sys
import tokenize
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent

PRAGMA = "DATE_GUARD_OK"
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
SENTINEL_YEARS = 50

# (path relative to tests/, date) -> reason. For JSON/JSONL fixtures only —
# Python sites take the same-line pragma so the judgement lives next to the
# fixture. Entries whose file vanishes fail the guard (typo/rot protection);
# entries whose date has drifted into the past are redundant but harmless.
ALLOWLIST: dict[tuple[str, str], str] = {}

FSTRING_TOKENS = {
    getattr(tokenize, name)
    for name in ("FSTRING_MIDDLE", "FSTRING_START", "FSTRING_END")
    if hasattr(tokenize, name)
}


def classify(date_str: str, today: datetime.date) -> str:
    """'past' | 'sentinel' | 'bomb' | 'invalid' for one ISO date literal."""
    try:
        d = datetime.date.fromisoformat(date_str)
    except ValueError:
        return "invalid"
    if d < today:
        return "past"
    if d.year - today.year >= SENTINEL_YEARS:
        return "sentinel"
    return "bomb"


def scan_python(source: str) -> tuple[list[tuple[int, str]], dict[int, str]]:
    """Return ([(line, date_literal), ...] from string tokens,
    {line: pragma_reason} from comment tokens)."""
    dates: list[tuple[int, str]] = []
    pragmas: dict[int, str] = {}
    # Tokens after which a STRING is an expression statement (docstring / bare
    # string) — its value is discarded, so it cannot be fixture data. NL (a
    # line break INSIDE brackets) is deliberately absent and skipped below:
    # a date string opening a continuation line of a multi-line call/list is
    # fixture data and must be scanned.
    STMT_START = {tokenize.NEWLINE, tokenize.INDENT,
                  tokenize.DEDENT, tokenize.ENCODING}
    prev_type = tokenize.ENCODING
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.NL:
            continue  # non-semantic — transparent to statement position
        if tok.type == tokenize.COMMENT:
            if PRAGMA in tok.string:
                _, _, reason = tok.string.partition(PRAGMA)
                pragmas[tok.start[0]] = reason.lstrip(":").strip()
            continue  # comments don't change statement position
        if tok.type == tokenize.STRING or tok.type in FSTRING_TOKENS:
            if tok.type == tokenize.STRING and prev_type in STMT_START:
                prev_type = tok.type  # docstring / bare string — skip
                continue
            for i, seg in enumerate(tok.string.split("\n")):
                for m in DATE_RE.finditer(seg):
                    dates.append((tok.start[0] + i, m.group(0)))
        prev_type = tok.type
    return dates, pragmas


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    today = datetime.date.today()
    failures: list[str] = []

    # -- self-checks: the classifier itself, with computed (never literal) dates
    def check(label: str, ok: bool) -> None:
        if not ok:
            failures.append(f"self-check failed: {label}")

    day = datetime.timedelta(days=1)
    check("today is a bomb", classify(today.isoformat(), today) == "bomb")
    check("tomorrow is a bomb", classify((today + 7 * day).isoformat(), today) == "bomb")
    check("next year is a bomb",
          classify((today + 400 * day).isoformat(), today) == "bomb")
    check("yesterday is past", classify((today - day).isoformat(), today) == "past")
    check("50y out is a sentinel",
          classify(today.replace(year=today.year + SENTINEL_YEARS).isoformat(), today)
          == "sentinel")
    check("49y out is a bomb",
          classify(today.replace(year=today.year + SENTINEL_YEARS - 1).isoformat(), today)
          == "bomb")
    check("garbage month is invalid", classify("2026-13-40", today) == "invalid")
    synth = (
        'x = "%s"  # %s: pinned clock\n'
        'y = "%s"\n'
    ) % ((today + 7 * day).isoformat(), PRAGMA, (today + 7 * day).isoformat())
    sdates, spragmas = scan_python(synth)
    check("scanner finds both synthetic dates", len(sdates) == 2)
    check("scanner reads the pragma reason", spragmas.get(1) == "pinned clock")
    check("unpragma'd synthetic line has no pragma", 2 not in spragmas)
    doc = '"""header %s"""\nz = "%s"\n' % (
        (today + 7 * day).isoformat(), (today + 7 * day).isoformat())
    ddates, _ = scan_python(doc)
    check("docstring date is exempt, assigned date is not",
          [ln for ln, _ in ddates] == [2])
    cont = 'f(\n    "a",\n    "%s", ["x"]),\n' % (today + 7 * day).isoformat()
    cdates, _ = scan_python(cont)
    check("date opening a continuation line IS scanned (not a docstring)",
          [ln for ln, _ in cdates] == [3])
    cdoc = 'def f():\n    # note\n    """d %s"""\n' % (today + 7 * day).isoformat()
    check("docstring after a comment line stays exempt",
          scan_python(cdoc)[0] == [])
    try:
        scan_python('x = "unterminated\n')
        check("untokenizable source raises a caught type", False)
    except (tokenize.TokenError, SyntaxError):
        pass  # matches the handler in the sweep below — pins the except tuple

    # -- sweep every Python suite under tests/
    violations: list[str] = []
    for py in sorted(TESTS.rglob("*.py")):
        rel = py.relative_to(ROOT).as_posix()
        try:
            dates, pragmas = scan_python(py.read_text(encoding="utf-8"))
        except (tokenize.TokenError, SyntaxError) as e:
            violations.append(f"{rel}: untokenizable — {e}")
            continue
        for line, ds in dates:
            if classify(ds, today) != "bomb":
                continue
            if line in pragmas:
                if not pragmas[line]:
                    violations.append(
                        f"{rel}:{line}: {PRAGMA} pragma with an empty reason — "
                        "state why this future literal cannot flip"
                    )
                continue
            key = (py.relative_to(TESTS).as_posix(), ds)
            if key in ALLOWLIST:
                continue
            violations.append(
                f"{rel}:{line}: hardcoded today-or-future date “{ds}” — compute it "
                f"relative to today, or annotate the line with  # {PRAGMA}: <why "
                "it cannot flip>  (MC3 time-bomb class, commit 2a12674)"
            )

    # -- sweep JSON/JSONL fixtures (no pragma channel — ALLOWLIST only)
    for jf in sorted(list(TESTS.rglob("*.json")) + list(TESTS.rglob("*.jsonl"))):
        rel = jf.relative_to(ROOT).as_posix()
        text = jf.read_text(encoding="utf-8", errors="replace")
        for i, seg in enumerate(text.split("\n"), start=1):
            for m in DATE_RE.finditer(seg):
                if classify(m.group(0), today) != "bomb":
                    continue
                if (jf.relative_to(TESTS).as_posix(), m.group(0)) in ALLOWLIST:
                    continue
                violations.append(
                    f"{rel}:{i}: hardcoded today-or-future date “{m.group(0)}” in a "
                    "JSON fixture — regenerate relative to today or add an "
                    "ALLOWLIST entry with a reason"
                )

    # -- ALLOWLIST hygiene: entries must point at real files with real reasons
    for (relpath, ds), reason in ALLOWLIST.items():
        if not (TESTS / relpath).exists():
            violations.append(f"ALLOWLIST entry ({relpath!r}, {ds!r}) points at a missing file")
        if not str(reason).strip():
            violations.append(f"ALLOWLIST entry ({relpath!r}, {ds!r}) has an empty reason")

    if failures or violations:
        print(f"FAIL — G14 date-bomb guard: {len(failures) + len(violations)} problem(s)\n")
        for v in failures + violations:
            print(f"  ✗ {v}")
        return 1
    print("OK — no unannotated today-or-future date literals in tests/ "
          f"(as of {today.isoformat()}; sentinels >= +{SENTINEL_YEARS}y exempt)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
