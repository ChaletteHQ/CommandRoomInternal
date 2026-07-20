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

Window-aging extension (FB bundle, 2026-07-19 — the builder-flagged blind
spot, M ruled yes): a PAST date that ages OUT of a TTL/freshness window is the
mirror image of a future bomb. The FS-11 case — `{"ts": "2026-07-14",
"ttl_days": 14}` compared against the REAL clock — read "fresh" until
today - ts exceeded the ttl, then silently flipped (would have gone RED on
2026-07-29 for reasons unrelated to the code). classify() cannot see this: a
near-past date is neither today-or-future nor a sentinel. So the guard now
catches the DETECTABLE subclass — a past date literal GOVERNED by a TTL/expiry
key in the same record, while the date is still INSIDE that window
(0 <= age <= ttl). The `age <= ttl` gate is the precision guarantee: a pair
already past its window would have flipped the suite red already, so on a green
tree such a pair proves the date is not actually TTL-governed (e.g. a
`window_days` payload describing a recap's coverage) and is left alone. Fix a
real hit the same way the FS-11 fixture was fixed — compute the date relative
to today (an `_ago(N)` helper) — or annotate the line with a pragma.

Known limits (documented, not enforced): near-past dates inside a recency
window with NO adjacent TTL literal (a "modified within 30d" fixture whose
threshold lives in code, not the fixture) remain undetectable without dataflow
analysis — pin the clock in those suites;
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


# Window-aging (FB bundle 2026-07-19). A TTL/expiry key whose value is the
# window, in days, a nearby date literal is measured against. `window_days` is
# deliberately EXCLUDED — in this tree it is descriptive payload (a recap's
# coverage span), not a freshness threshold a fixture date is compared to.
TTL_KEY_RE = re.compile(
    r"\b(?:ttl_days|ttl|expires_in_days|expires_in|stale_after_days"
    r"|max_age_days|freshness_days|within_days)\b[\"'\s:=]+(\d+)"
)
# A date and its governing ttl may straddle a wrapped record
# ({"ts": "...",\n "ttl_days": N}) — look one line up, two down.
TTL_LOOKAHEAD = 2


def governing_ttl(lines: list[str], idx: int) -> "int | None":
    """The TTL window (days) governing a date on 0-based line `idx`, if a TTL
    key sits in the same record (line above, this line, or the next two)."""
    lo = max(0, idx - 1)
    hi = min(len(lines), idx + 1 + TTL_LOOKAHEAD)
    for j in range(lo, hi):
        m = TTL_KEY_RE.search(lines[j])
        if m:
            return int(m.group(1))
    return None


def aging_out(date_str: str, ttl: "int | None", today: datetime.date) -> bool:
    """True if `date_str` is a past date STILL INSIDE a governing TTL window —
    a live window-aging bomb that flips once its age exceeds the ttl. False for
    a date with no governing ttl, a today-or-future date (classify()'s 'bomb'
    path owns those), or a pair already past its window (age > ttl: it would
    have flipped the suite red already, so a green tree proves it isn't really
    TTL-governed)."""
    if ttl is None:
        return False
    try:
        d = datetime.date.fromisoformat(date_str)
    except ValueError:
        return False
    if d >= today:
        return False
    return 0 <= (today - d).days <= ttl


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

    # -- window-aging self-checks (FB bundle): the FS-11 bomb class + its gates
    check("governing_ttl reads a same-line ttl_days",
          governing_ttl(['{"ts": "x", "ttl_days": 14}'], 0) == 14)
    check("governing_ttl reads a ttl on the next line (wrapped record)",
          governing_ttl(['{"ts": "x",', '"ttl_days": 21}'], 0) == 21)
    check("governing_ttl ignores window_days (descriptive payload, not a TTL)",
          governing_ttl(['{"ts": "x", "window_days": 7}'], 0) is None)
    check("aging: past date inside its ttl window is a bomb",
          aging_out((today - 2 * day).isoformat(), 14, today) is True)
    check("aging: past date already past its ttl is NOT flagged (would be red already)",
          aging_out((today - 40 * day).isoformat(), 14, today) is False)
    check("aging: past date with no governing ttl is NOT flagged",
          aging_out((today - 2 * day).isoformat(), None, today) is False)
    check("aging: today-or-future is classify()'s job, not aging's",
          aging_out((today + 3 * day).isoformat(), 14, today) is False)
    check("aging: on the ttl boundary (age == ttl) is still a live bomb",
          aging_out((today - 14 * day).isoformat(), 14, today) is True)

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
            src = py.read_text(encoding="utf-8")
            dates, pragmas = scan_python(src)
        except (tokenize.TokenError, SyntaxError) as e:
            violations.append(f"{rel}: untokenizable — {e}")
            continue
        lines = src.splitlines()
        for line, ds in dates:
            future_bomb = classify(ds, today) == "bomb"
            ttl = None if future_bomb else governing_ttl(lines, line - 1)
            aging = aging_out(ds, ttl, today)
            if not (future_bomb or aging):
                continue
            if line in pragmas:
                if not pragmas[line]:
                    kind = "future literal" if future_bomb else "aging date"
                    violations.append(
                        f"{rel}:{line}: {PRAGMA} pragma with an empty reason — "
                        f"state why this {kind} cannot flip"
                    )
                continue
            key = (py.relative_to(TESTS).as_posix(), ds)
            if key in ALLOWLIST:
                continue
            if future_bomb:
                violations.append(
                    f"{rel}:{line}: hardcoded today-or-future date “{ds}” — compute "
                    f"it relative to today, or annotate the line with  # {PRAGMA}: "
                    "<why it cannot flip>  (MC3 time-bomb class, commit 2a12674)"
                )
            else:
                violations.append(
                    f"{rel}:{line}: past date “{ds}” is still inside its governing "
                    f"{ttl}-day TTL window and ages OUT of it — the FS-11 "
                    f"window-aging bomb class. Compute it relative to today "
                    f"(an _ago(N) helper), or annotate the line with  # {PRAGMA}: "
                    "<why it cannot age out>"
                )

    # -- sweep JSON/JSONL fixtures (no pragma channel — ALLOWLIST only)
    for jf in sorted(list(TESTS.rglob("*.json")) + list(TESTS.rglob("*.jsonl"))):
        rel = jf.relative_to(ROOT).as_posix()
        text = jf.read_text(encoding="utf-8", errors="replace")
        jlines = text.split("\n")
        for i, seg in enumerate(jlines, start=1):
            for m in DATE_RE.finditer(seg):
                ds = m.group(0)
                future_bomb = classify(ds, today) == "bomb"
                aging = (not future_bomb
                         and aging_out(ds, governing_ttl(jlines, i - 1), today))
                if not (future_bomb or aging):
                    continue
                if (jf.relative_to(TESTS).as_posix(), ds) in ALLOWLIST:
                    continue
                if future_bomb:
                    violations.append(
                        f"{rel}:{i}: hardcoded today-or-future date “{ds}” in a "
                        "JSON fixture — regenerate relative to today or add an "
                        "ALLOWLIST entry with a reason"
                    )
                else:
                    violations.append(
                        f"{rel}:{i}: past date “{ds}” in a JSON fixture is still "
                        f"inside its governing {governing_ttl(jlines, i - 1)}-day "
                        "TTL window and ages OUT of it (FS-11 class) — regenerate "
                        "relative to today or add an ALLOWLIST entry with a reason"
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
