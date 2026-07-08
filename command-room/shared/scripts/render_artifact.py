#!/usr/bin/env python3
"""
Pure HTML template renderer for Command Room Live Artifacts.

This script eliminates the v2.7.9 / v2.7.10 update-bridge failure mode where the
model wrote 15k+ tokens of HTML inline as a tool-call argument and (under
context pressure or a hallucinated payload limit) "compacted" the output into a
non-canonical artifact. Now the model emits a single bash call to this script,
which performs deterministic substitution against a canonical template file
that ships with the plugin. Bytes are reproducible by construction.

Inputs:
    --template <path>   Path to a canonical artifact template (HTML with
                        {{PLACEHOLDER}} markers).
    --input <path>      Path to a JSON file mapping placeholder name -> string
                        value (already pre-formatted: JSON-encoded for JS
                        contexts, HTML-escaped for HTML contexts; the
                        input-builder scripts handle that, this renderer does
                        not).

Output:
    Rendered HTML to stdout (or --output <path>).

Exit codes:
    0   render succeeded; all template placeholders satisfied.
    1   template has a placeholder not present in input (hard fail — prevents
        shipping an artifact with a literal {{FOO}} string in the bytes).
    2   I/O error (template or input file missing/unreadable).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_][A-Z0-9_]*)\}\}")


def find_placeholders(template: str) -> set[str]:
    return set(PLACEHOLDER_RE.findall(template))


def substitute(template: str, values: dict[str, str]) -> tuple[str, set[str]]:
    """Returns (rendered_html, missing_placeholders)."""
    missing: set[str] = set()

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            missing.add(key)
            return match.group(0)
        return values[key]

    rendered = PLACEHOLDER_RE.sub(repl, template)
    return rendered, missing


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a Command Room artifact template.")
    parser.add_argument("--template", required=True, type=Path, help="Path to template HTML.")
    parser.add_argument("--input", required=True, type=Path, help="Path to input JSON.")
    parser.add_argument("--output", type=Path, default=None, help="Output path (default: stdout).")
    parser.add_argument(
        "--ignore-missing",
        action="store_true",
        help="Don't exit non-zero if the template has placeholders not in input. Use only for debugging.",
    )
    args = parser.parse_args()

    try:
        template = args.template.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: could not read template {args.template}: {exc}", file=sys.stderr)
        return 2

    try:
        if str(args.input) == "-":
            raw_input = sys.stdin.read()  # `--input -` reads JSON from stdin (heredoc)
        else:
            raw_input = args.input.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: could not read input {args.input}: {exc}", file=sys.stderr)
        return 2

    try:
        values = json.loads(raw_input)
    except json.JSONDecodeError as exc:
        print(f"ERROR: input file is not valid JSON: {exc}", file=sys.stderr)
        return 2

    if not isinstance(values, dict):
        print("ERROR: input JSON must be an object mapping placeholder -> value.", file=sys.stderr)
        return 2

    string_values: dict[str, str] = {}
    for key, val in values.items():
        if not isinstance(val, str):
            print(
                f"ERROR: input key '{key}' must be a pre-formatted string, got {type(val).__name__}. "
                f"The input-builder scripts are responsible for JSON-encoding / HTML-escaping; "
                f"this renderer is dumb substitution.",
                file=sys.stderr,
            )
            return 2
        string_values[key] = val

    placeholders_in_template = find_placeholders(template)

    rendered, missing = substitute(template, string_values)

    extra_in_input = set(string_values.keys()) - placeholders_in_template
    if extra_in_input:
        print(
            f"WARN: input contained keys not present in template: {sorted(extra_in_input)}",
            file=sys.stderr,
        )

    if missing and not args.ignore_missing:
        print(
            f"ERROR: template has placeholders not satisfied by input: {sorted(missing)}",
            file=sys.stderr,
        )
        return 1

    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)

    return 0


if __name__ == "__main__":
    sys.exit(main())
