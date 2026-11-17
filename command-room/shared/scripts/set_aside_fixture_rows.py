#!/usr/bin/env python3
"""Set aside named fixture rows from a book's events.jsonl, with a receipt (cr1#1240).

A battery run from a checkout nested inside a real book wrote test rows into
that book's live ledger (fixed at the source by `battery_isolation.py`). The
rows already written cannot simply be deleted: a hand removal leaves a seq gap
after every burst, and `seq_health` reports those as entries a chat took out.
`recover_corruption.py` sets aside only malformed lines.

This tool is the sanctioned path for well-formed rows that should never have
been written. It works on rows NAMED by seq, never on a pattern alone:

    # 1. read only: list the candidate seqs and their bursts
    python set_aside_fixture_rows.py select <book> --type gate_ran --source turn_backstop

    # 2. dry run: check every named seq exists and matches type + source
    python set_aside_fixture_rows.py apply <book> --seqs-file seqs.txt \
        --type gate_ran --source turn_backstop --expect 741 --reason "cr1#1240"

    # 3. the same with --apply writes

On --apply, under the one writer lock, it:
  - copies the whole events.jsonl to `_hq/.system/quarantine/` first (backup);
  - writes the named rows to a quarantine sidecar;
  - appends one `fixture_rows_set_aside` receipt naming every seq it takes
    (`data.quarantined_seqs`), so `seq_health` classifies each gap as a
    recorded repair, not a removal. The receipt goes in BEFORE the rows leave,
    so the log's max seq never drops below its high-water;
  - rewrites events.jsonl without the named rows.
A pass stopped between receipt and rewrite is finished by running it again.

It refuses (writes nothing) when any named seq is missing, appears twice,
or does not match `--type`/`--source`, when the count differs from
`--expect`, or when a receipt for the same seqs already exists. `.seqhw`,
`.writer.lock.info` and `writer_identity.json` are not touched.
"""

from __future__ import annotations

import argparse
import datetime
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from atomic_write import (  # noqa: E402
    atomic_append_jsonl,
    atomic_write_text,
    multi_write_context,
)

RECEIPT_TYPE = "fixture_rows_set_aside"
TOOL_VERSION = "2"


def _events_path(book: Path) -> Path:
    return book / "_hq" / "data" / "events.jsonl"


def _quarantine_dir(book: Path) -> Path:
    return book / "_hq" / ".system" / "quarantine"


def _parse(raw: str):
    try:
        ev = json.loads(raw)
    except Exception:
        return None
    return ev if isinstance(ev, dict) else None


def _seq(ev) -> int | None:
    s = ev.get("seq") if ev else None
    return s if isinstance(s, int) and not isinstance(s, bool) else None


def _lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    out = raw.splitlines(keepends=True)
    if out and not out[-1].endswith("\n"):
        out[-1] += "\n"
    return out


def _matches(ev, type_: str, source: str | None) -> bool:
    return ev.get("type") == type_ and (source is None or ev.get("source_skill") == source)


def select(book: Path, type_: str, source: str | None) -> dict:
    """Read only. Candidate seqs and their bursts (rows within 120 s of each other)."""
    seqs, times = [], []
    for raw in _lines(_events_path(book)):
        ev = _parse(raw)
        if ev and _matches(ev, type_, source) and _seq(ev) is not None:
            seqs.append(_seq(ev))
            times.append(str(ev.get("ts", "")))
    bursts, cur, last = [], [], None
    for s, t in zip(seqs, times):
        try:
            dt = datetime.datetime.fromisoformat(t.replace("Z", "+00:00"))
        except Exception:
            dt = None
        if cur and (dt is None or last is None or (dt - last).total_seconds() > 120):
            bursts.append(cur)
            cur = []
        cur.append((s, t))
        last = dt
    if cur:
        bursts.append(cur)
    return {
        "count": len(seqs),
        "seqs": seqs,
        "bursts": [{"n": len(b), "first_ts": b[0][1], "last_ts": b[-1][1],
                    "first_seq": b[0][0], "last_seq": b[-1][0]} for b in bursts],
    }


def _plan(lines: list[str], wanted: set[int], type_: str, source: str | None):
    """(taken_raw, surviving_raw, problems)."""
    taken, surviving, seen, problems = [], [], {}, []
    for raw in lines:
        ev = _parse(raw)
        s = _seq(ev)
        if s is not None and s in wanted:
            seen[s] = seen.get(s, 0) + 1
            if not _matches(ev, type_, source):
                problems.append(f"seq {s} is {ev.get('type')}/{ev.get('source_skill')}, "
                                f"not {type_}/{source}")
            taken.append(raw)
        else:
            surviving.append(raw)
    missing = sorted(wanted - set(seen))
    if missing:
        problems.append(f"{len(missing)} named seq(s) not in the log, e.g. {missing[:5]}")
    dupes = sorted(s for s, n in seen.items() if n > 1)
    if dupes:
        problems.append(f"{len(dupes)} named seq(s) appear more than once, e.g. {dupes[:5]}")
    return taken, surviving, problems


def _receipt_for(lines: list[str], wanted: set[int]):
    """(exact, overlapping): a receipt naming exactly these seqs, and whether any
    receipt names some of them."""
    exact, overlap = None, False
    for raw in lines:
        ev = _parse(raw)
        if ev and ev.get("type") == RECEIPT_TYPE:
            named = set((ev.get("data") or {}).get("quarantined_seqs") or [])
            if named == wanted:
                exact = ev
            if wanted & named:
                overlap = True
    return exact, overlap


def apply(book: Path, seqs: list[int], type_: str, source: str | None,
          expect: int, reason: str, *, write: bool = False) -> dict:
    wanted = set(seqs)
    out = {"book": str(book), "named": len(wanted), "applied": False, "problems": []}
    if len(wanted) != len(seqs):
        out["problems"].append("the seqs list names a number twice")
    if len(wanted) != expect:
        out["problems"].append(f"--expect {expect} but {len(wanted)} seqs named")
    if not reason.strip():
        out["problems"].append("--reason is required")
    path = _events_path(book)
    if not path.is_file():
        out["problems"].append(f"no events.jsonl under {book}")
        return out
    if out["problems"]:
        return out

    def check(lines):
        """(taken, surviving, problems, resume). `resume` is a receipt for exactly
        these seqs whose rows are still in the log: a pass stopped between its
        receipt and its rewrite, which a re-run finishes without a new receipt."""
        exact, overlap = _receipt_for(lines, wanted)
        if exact is not None:
            present = {_seq(_parse(r)) for r in lines} & wanted
            if not present:
                return [], lines, ["already done: a receipt names these seqs and none is left"], None
            taken, surviving, problems = _plan(lines, present, type_, source)
            return taken, surviving, problems, exact
        taken, surviving, problems = _plan(lines, wanted, type_, source)
        if overlap:
            problems.append("a fixture_rows_set_aside receipt already names some of these seqs")
        return taken, surviving, problems, None

    taken, surviving, problems, resume = check(_lines(path))
    out.update(problems=problems, would_take=len(taken), would_keep=len(surviving),
               resume=resume is not None)
    if problems or not write:
        return out

    with multi_write_context(book, holder="set_aside_fixture_rows"):
        lines = _lines(path)  # re-read inside the lock
        taken, surviving, problems, resume = check(lines)
        if problems:
            out["problems"] = problems
            return out
        qdir = _quarantine_dir(book)
        qdir.mkdir(parents=True, exist_ok=True)
        rel = lambda p: p.relative_to(book).as_posix()  # noqa: E731
        if resume is not None:
            d = resume.get("data") or {}
            backup, sidecar = book / d["backup_file"], book / d["quarantine_file"]
        else:
            label = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
            backup = qdir / f"events_backup_before_fixture_set_aside_{label}.jsonl"
            sidecar = qdir / f"events_fixture_set_aside_{label}.jsonl"
            n = 1
            while backup.exists() or sidecar.exists():
                backup = qdir / f"events_backup_before_fixture_set_aside_{label}_{n}.jsonl"
                sidecar = qdir / f"events_fixture_set_aside_{label}_{n}.jsonl"
                n += 1
            shutil.copy2(path, backup)
            atomic_write_text(sidecar, "".join(taken))
            # The receipt goes in FIRST, through the one append path, so it takes
            # the next seq while every row is still present. Rewriting first
            # would drop the file's max seq below the high-water whenever the
            # fixture rows are the newest rows, and the append path refuses that
            # as a regression (cr1#1240, found on the first real apply).
            atomic_append_jsonl(path, [{
                "ts": datetime.datetime.now(datetime.timezone.utc)
                      .isoformat(timespec="seconds").replace("+00:00", "Z"),
                "type": RECEIPT_TYPE,
                "source_skill": "set_aside_fixture_rows",
                "data": {
                    "quarantined_seqs": sorted(wanted),
                    "quarantined_count": len(taken),
                    "quarantine_file": rel(sidecar),
                    "backup_file": rel(backup),
                    "selector": {"type": type_, "source_skill": source},
                    "reason": reason.strip(),
                    "tool_version": TOOL_VERSION,
                },
            }])
        # Then the rows leave: re-read (the receipt is now the last line) and
        # write back everything but the named seqs. The receipt keeps the max.
        lines = _lines(path)
        _taken, surviving, problems = _plan(lines, {_seq(_parse(r)) for r in lines} & wanted,
                                            type_, source)
        if problems:
            out["problems"] = problems
            return out
        atomic_write_text(path, "".join(surviving))
    out.update(applied=True, quarantine_file=str(sidecar), backup_file=str(backup))
    return out


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("book")
    s.add_argument("--type", required=True)
    s.add_argument("--source")
    s.add_argument("--seqs-out")
    a = sub.add_parser("apply")
    a.add_argument("book")
    a.add_argument("--seqs-file", required=True)
    a.add_argument("--type", required=True)
    a.add_argument("--source")
    a.add_argument("--expect", type=int, required=True)
    a.add_argument("--reason", required=True)
    a.add_argument("--apply", action="store_true", help="write; without it, a dry run")
    args = ap.parse_args(argv)
    book = Path(args.book).expanduser().resolve()
    if args.cmd == "select":
        r = select(book, args.type, args.source)
        if args.seqs_out:
            Path(args.seqs_out).write_text("\n".join(map(str, r["seqs"])) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in r.items() if k != "seqs"}, indent=1))
        return 0
    seqs = [int(x) for x in Path(args.seqs_file).read_text().split() if x.strip()]
    r = apply(book, seqs, args.type, args.source, args.expect, args.reason, write=args.apply)
    print(json.dumps(r, indent=1))
    return 0 if not r["problems"] else 1


if __name__ == "__main__":
    sys.exit(_cli())
