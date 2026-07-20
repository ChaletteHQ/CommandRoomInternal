#!/usr/bin/env python3
"""
Structural guard: no real beta-customer or partner names in plugin source.

Three-layer enforcement (v3.12.2+):

  1. Named-pattern denylist (legacy v3.6.2). Catches a small list of known
     historical leaks. Limited usefulness on its own — only catches names
     already known to be in the codebase at the time the pattern was added.

  2. Structural email allowlist (v3.6.3). Catches ANY email address whose
     domain is not on the allowlist. The durable defense for emails: any
     new real address a future skill author writes will fail this check at
     PR / push time without needing to know it in advance.

  3. **Structural name allowlist (v3.12.2 — the long-overdue durable fix
     for the name-leak class).** Same fails-closed model as Layer 2 applied
     to PERSON names:

     - **Two-word PascalCase capture.** Every adjacent `Foo Bar` proper-noun
       pair in skill / shared / reference / tests prose must be on an
       approved-pair allowlist (placeholder Firstname + Sample/Stone, an
       approved org, or an approved technical phrase). Anything else fails.
       This is the strong catch — surnames are the dangerous leak vector
       because they identify specific people.
     - **Solo first name in person-context.** Patterns like `with [Name]`,
       `for [Name]`, `to [Name]`, `from [Name]`, `[Name] said`, `[Name]'s`,
       `[Name] is a` flag any single capitalized word in that position
       that isn't on the approved-first-name placeholder list.

     Pre-v3.12.2 the architecture was a denylist (reactive — only caught
     names already added by a manual sweep). Every new release shipped new
     examples by sessions that didn't know the full denylist, so names
     leaked, audits found them, denylist grew, cycle repeated. The v3.12.2
     conversion makes new names FAIL CLOSED — to introduce a new name an
     author must add it deliberately to the allowlist.

Companion to references/PRIVACY_POLICY.md (the rule), CONTRACT.md Rule 26
(the contract that points at this test as the enforcement mechanism), and
.githooks/pre-commit (which runs this test on every commit so leaks get
caught at write-time instead of push-time).

Scope (v3.6.3): the scan covers skills/, shared/, references/, tests/, and
CHANGELOG.md at the repo root. Pre-v3.6.3 the CHANGELOG was exempt as
"audit trail," but that left a sizable historical-leak surface; the
v3.6.3 sweep sanitized the CHANGELOG too. Only this test file itself is
exempt — it literally has to contain the forbidden patterns to scan for
them.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# Named-pattern layer (legacy v3.6.2). Catches the specific historical
# leaks the v3.5.2/v3.6.2 sweeps removed.
FORBIDDEN_NAME_PATTERNS = [
    r"Pourbaba",
    r"Bailey Berro",
    r"Brett Nestat",
    r"Brett Baker",
    # FU-2 (2026-07-19): the retired "Category Company" / "CategoryCo"
    # placeholder was replaced fleet-wide by "Summit Company" — it read as
    # potentially real. Deny both the spaced org form and the CamelCase
    # alias, case-insensitively, so no future example can reintroduce it.
    r"(?i)categoryco",
    r"(?i)category\s+company",
    r"peaiventure",
    r"pe-ai\.io",
    r"[Mm]isuma",
    r"PE AI Venture",
    # v3.12.1 — surfaced by the 2026-05-20 broader sanitization sweep.
    # Case-insensitive since the HYG1 second-eyes review (2026-07-13): the
    # hostname "MDAVIDOV-PC" sat in RECEIPT_CONTRACT.md and a health-truth
    # fixture for weeks because the case-sensitive pattern only matched
    # "Davidov". Hostnames embed the surname in caps — match any casing.
    r"(?i)davidov",
    r"Spaeth",
    r"Dent Mechanic",
    r"Bedford",
    r"Landherr",
    r"[Pp]hilippe",
    # v3.18.9 — A-Z Bus client (denylist gap: "A-Z Bus" slipped into
    # prospect_conversion_detector.py:8 docstring in v3.18.7 because only the
    # surname "Landherr" was listed, not the org name or domain).
    r"A-Z Bus",
    r"a-zbus",
    r"Smittipatana",
    r"Bluhm",
    # HYG1 second-eyes review (2026-07-13): real collaborator/contact full
    # names had landed in test fixtures via real-dogfood replay shapes
    # (F-44/F-60). Fixture SHAPE stays real; names are placeholders.
    r"Jewett",
    r"\bBurg\b",
]

# Structural layer (v3.6.3). The allowlist of email domains a plugin-source
# file may legitimately contain. Every email outside this list is a leak —
# real customer, partner, or vendor address that PRIVACY_POLICY.md forbids.
#
# RFC 2606 reserves the example.* TLDs for placeholder use; *.example.com
# subdomains are allowed for placeholder hierarchies.
# mail.gmail.com / outlook.com appear in Gmail/Outlook message-id literals
# documented in zapier_send.py and the protocol docs — those are RFC 822
# header value examples, not personal addresses.
ALLOWED_EMAIL_DOMAIN_PATTERNS = [
    r"^example\.com$",
    r"^example\.org$",
    r"^example\.net$",
    r"\.example\.com$",
    r"\.example\.org$",
    r"\.example\.net$",
    r"^chaletteholdings\.com$",  # maintainer's intentional public support address
    r"^mail\.gmail\.com$",       # Gmail Message-ID header literals
    r"^outlook\.com$",           # Outlook Message-ID header literals
    r"^google\.com$",            # noreply@google.com — system mail example
    r"^granola\.ai$",            # connector domain reference (not personal)
    r"^anthropic\.com$",         # platform reference (not personal)
    r"^x\.com$",                 # 1-char synthetic test placeholder
    r"^y\.com$",                 # 1-char synthetic test placeholder
]

_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])"
    r"([A-Za-z0-9._%+\-]+)"
    r"@"
    r"([A-Za-z0-9.\-]+\.[A-Za-z]{2,})"
)


# ---------------------------------------------------------------------------
# Layer 3 (v3.12.2) — Structural NAME allowlist.
# ---------------------------------------------------------------------------
#
# Design choice: rather than scanning every capitalized two-word phrase (which
# false-positives on "Bug Fixes", "Initial Release", etc.), Layer 3 uses a
# **first-name dictionary** to detect when a capitalized word is likely to
# refer to a person. Any token that matches a known common first name AND
# isn't on the approved-placeholder list = leak.
#
# The COMMON_FIRST_NAMES set is the detection mechanism (what looks like a
# name?). The APPROVED_FIRST_NAMES set is the allowlist (which names are
# OK to use in examples?). Anything detected but not approved fails.

# Approved placeholder first names. Source: references/PRIVACY_POLICY.md.
# DO NOT extend without updating PRIVACY_POLICY.md in the same change.
APPROVED_FIRST_NAMES = frozenset({
    "Sam", "Bo", "Rio", "Skyler", "Mira", "Aria",
    "Bowie", "Lyra", "Quinn", "Dustin", "Adan",
})

# Approved placeholder surnames. Used in two-word "Firstname Surname"
# introductions of placeholder people.
APPROVED_SURNAMES = frozenset({
    "Sample",  # canonical surname per PRIVACY_POLICY.md
    "Stone",   # secondary surname for disambiguation examples
})

# Common first names dictionary — used DETECTION-SIDE (what looks like a
# name) not as an allowlist. If a token appears here AND isn't in
# APPROVED_FIRST_NAMES, it's flagged. Pulled from the top ~300 most common
# US English first names (SSA baby-name data, top male + female combined).
# This is the durable defense against "session-X uses name-not-in-denylist"
# because virtually every real person's first name in M's workspace falls
# in this distribution.
#
# To intentionally use a name OUTSIDE this list as a non-person reference
# (e.g. "Bedford" as an entity name in some legitimate context), the
# author either uses an approved placeholder OR adds the name to the
# Layer 1 denylist explanation comment so future-readers know it's been
# considered.
COMMON_FIRST_NAMES = frozenset({
    # Male — top ~150
    "James", "John", "Robert", "Michael", "William", "David", "Richard",
    "Joseph", "Thomas", "Charles", "Christopher", "Daniel", "Matthew",
    "Anthony", "Mark", "Donald", "Steven", "Paul", "Andrew", "Joshua",
    "Kenneth", "Kevin", "Brian", "George", "Timothy", "Ronald", "Jason",
    "Edward", "Jeffrey", "Ryan", "Jacob", "Gary", "Nicholas", "Eric",
    "Jonathan", "Stephen", "Larry", "Justin", "Scott", "Brandon", "Benjamin",
    "Samuel", "Gregory", "Frank", "Alexander", "Raymond", "Patrick", "Jack",
    "Dennis", "Jerry", "Tyler", "Aaron", "Jose", "Adam", "Henry", "Nathan",
    "Douglas", "Zachary", "Peter", "Kyle", "Walter", "Ethan", "Jeremy",
    "Harold", "Keith", "Christian", "Roger", "Noah", "Gerald", "Carl",
    "Terry", "Sean", "Austin", "Arthur", "Lawrence", "Jesse", "Dylan",
    "Bryan", "Joe", "Jordan", "Billy", "Bruce", "Albert", "Willie",
    "Gabriel", "Logan", "Alan", "Juan", "Wayne", "Roy", "Ralph", "Randy",
    "Eugene", "Vincent", "Russell", "Elijah", "Louis", "Bobby", "Philip",
    "Johnny", "Liam", "Mason", "Lucas", "Caleb", "Isaac", "Caden", "Hunter",
    "Owen", "Connor", "Jackson", "Carter", "Wyatt", "Tristan", "Cooper",
    "Landon", "Bentley", "Brayden", "Easton", "Levi", "Lincoln", "Asher",
    "Sebastian", "Aiden", "Cameron", "Hudson", "Grayson", "Eli", "Colton",
    "Parker", "Bradley", "Howard", "Glenn", "Travis", "Earl", "Carlos",
    "Tony", "Antonio", "Manuel", "Miguel", "Luis", "Jorge", "Mario",
    "Francisco", "Roberto", "Ricardo", "Eduardo", "Pedro", "Diego",
    "Felipe", "Hector", "Cesar", "Oscar",
    # Common short forms / nicknames
    "Tom", "Jim", "Bill", "Bob", "Mike", "Dave", "Dan", "Ben", "Joe",
    "Pete", "Tim", "Ted", "Phil", "Steve", "Doug", "Andy", "Greg", "Matt",
    "Nick", "Rick", "Ron", "Roy", "Will", "Wes", "Walt", "Vince",
    # Female — top ~150
    "Mary", "Patricia", "Jennifer", "Linda", "Elizabeth", "Barbara",
    "Susan", "Jessica", "Sarah", "Karen", "Nancy", "Lisa", "Margaret",
    "Sandra", "Ashley", "Kimberly", "Emily", "Donna", "Michelle", "Carol",
    "Amanda", "Melissa", "Deborah", "Stephanie", "Rebecca", "Sharon",
    "Laura", "Cynthia", "Kathleen", "Amy", "Shirley", "Angela", "Helen",
    "Anna", "Brenda", "Pamela", "Nicole", "Emma", "Samantha", "Katherine",
    "Christine", "Debra", "Rachel", "Catherine", "Carolyn", "Janet", "Ruth",
    "Maria", "Heather", "Diane", "Virginia", "Julie", "Joyce", "Victoria",
    "Olivia", "Kelly", "Christina", "Lauren", "Joan", "Evelyn", "Judith",
    "Andrea", "Hannah", "Megan", "Cheryl", "Jacqueline", "Martha",
    "Madison", "Teresa", "Gloria", "Sara", "Janice", "Kathryn", "Abigail",
    "Sophia", "Frances", "Jean", "Alice", "Judy", "Isabella", "Julia",
    "Grace", "Amber", "Denise", "Danielle", "Marilyn", "Beverly",
    "Charlotte", "Natalie", "Theresa", "Diana", "Brittany", "Doris",
    "Kayla", "Alexis", "Lori", "Marie", "Ann", "Tina", "Norma", "Crystal",
    "Megan", "Erica", "Phyllis", "Lillian", "Marjorie", "Rita", "Wanda",
    "Carrie", "Pearl", "Edna", "Yvonne", "Caroline", "Audrey", "Vera",
    "Joanne", "Jeanette", "Rosa", "Bernice", "Ellen", "Eva", "Esther",
    "Tracy", "Cindy", "Jane", "Robin", "Sue", "Sherry", "Donna", "Bonnie",
    "Tammy", "Wendy", "Lori", "Tara", "Holly", "Stacy", "Stacey", "Carla",
    "Erin", "Becky", "Pam", "Jen", "Liz", "Beth", "Kate", "Katie",
    # Names that have shown up in M's workspace / examples specifically
    # (catches the actual leak vectors observed across v3.x sweeps).
    # These are common-enough names that they belong in a broad first-name
    # dictionary anyway.
    "Bailey", "Brett", "Reed", "Pierce", "Sarah", "Philippe", "Jonathan",
    "Matthew", "Daniel", "David", "Lyn", "Janet", "Sarah",
    # HYG1 second-eyes review (2026-07-13): "Erick" (the k-spelling) slipped
    # through every prior sweep because only "Eric" was in the dictionary;
    # same for "Michele" (one l) vs "Michelle". Real workspace names — both
    # observed leaking into shipped examples.
    "Erick", "Michele",
})


# Person-context patterns: where a real person's name would naturally appear.
_PERSON_CONTEXT_RE = re.compile(
    r"\b(?:with|for|to|from|by|about|via)\s+([A-Z][a-z]+)\b"
)
# Possessive-name context: "[Name]'s thing", "[Name]'s call"
_POSSESSIVE_NAME_RE = re.compile(
    r"\b([A-Z][a-z]+)[’']s\b"
)
# Two-word "Firstname Lastname" pair where first word is a known first name
_FIRSTNAME_LASTNAME_RE = re.compile(
    r"\b([A-Z][a-z]+)\s+([A-Z][a-z]+)\b"
)


def _is_violation_first_name(name: str) -> bool:
    """True if this token is a common first name not on the approved list."""
    return name in COMMON_FIRST_NAMES and name not in APPROVED_FIRST_NAMES


def _scan_line_for_name_violations(line: str):
    """Return a list of (pattern, context) tuples for name-leak violations.

    Two checks:
      - Two-word "Firstname Lastname" where Firstname is a real first name
        but not on the placeholder allowlist. Strongest signal — surnames
        identify specific people.
      - Solo first name in person-context (with/for/to/from/possessive)
        where the name is a real first name but not on the placeholder
        allowlist. Catches single-name references.
    """
    out = []
    stripped = line.strip()
    # Skip code-fence open/close lines + heavily-indented code blocks
    if stripped.startswith("```"):
        return out
    if line.startswith("    ") and not stripped.startswith("- ") and not stripped.startswith("* "):
        # Indented prose is fine; indented code blocks aren't scanned
        return out

    # Strip backtick-wrapped tokens (code/identifier references)
    cleaned = re.sub(r"`[^`]*`", "", line)
    # Strip URLs
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    # Strip markdown link targets: ](url)
    cleaned = re.sub(r"\]\([^)]*\)", "", cleaned)

    # Check A — Firstname-Lastname pair where Firstname is a real first name.
    # If a known first name appears followed by another PascalCase word AND
    # the first name isn't an approved placeholder, flag — that's a person
    # reference. Allow the pair if the surname is "Sample" or "Stone" (then
    # both words must form an approved placeholder pair — which is only
    # possible if the first name is approved, contradicting the trigger).
    for m in _FIRSTNAME_LASTNAME_RE.finditer(cleaned):
        first, second = m.group(1), m.group(2)
        if first not in COMMON_FIRST_NAMES:
            continue
        if first in APPROVED_FIRST_NAMES:
            # Placeholder name — verify surname is also approved
            if second in APPROVED_SURNAMES:
                continue
            # An approved first name followed by a non-Sample/Stone surname
            # could be either a placeholder gone wrong (Sam Stone is fine,
            # Sam Spaeth would have been a leak) OR an approved first name
            # used in a non-person context (e.g., "Adam Phase"). Conservative:
            # only flag if the second word is also a common first name OR
            # not a common technical word. Skip otherwise to avoid noise.
            continue
        # First word IS a real first name and NOT on the placeholder list
        out.append((f"firstname-lastname-pair:{first} {second}", stripped))

    # Check B — solo first name in person-context.
    for m in _PERSON_CONTEXT_RE.finditer(cleaned):
        name = m.group(1)
        if not _is_violation_first_name(name):
            continue
        out.append((f"person-context-name:{name}", stripped))

    # Check C — possessive form.
    for m in _POSSESSIVE_NAME_RE.finditer(cleaned):
        name = m.group(1)
        if not _is_violation_first_name(name):
            continue
        out.append((f"possessive-name:{name}", stripped))

    return out


EXEMPT_FILES = {
    "run_no_real_customer_names_test.py",
    # PRIVACY_POLICY.md is the policy document — it MUST contain the forbidden
    # patterns it defines (for didactic ❌ examples and the "what NOT to put
    # in the changelog" guidance). Same exemption rationale as this test file.
    "PRIVACY_POLICY.md",
}

SCAN_EXTENSIONS = {".md", ".py", ".json", ".jsonl", ".html"}

SCAN_DIRS = ["skills", "shared", "references", "tests"]

# Top-level files at the repo root that should also be scanned.
SCAN_ROOT_FILES = ["CHANGELOG.md"]


def _domain_is_allowed(domain: str) -> bool:
    d = domain.lower()
    return any(re.search(p, d) for p in ALLOWED_EMAIL_DOMAIN_PATTERNS)


def _iter_scan_paths():
    for d in SCAN_DIRS:
        scan_root = PLUGIN_ROOT / d
        if not scan_root.exists():
            continue
        for path in scan_root.rglob("*"):
            yield path
    for name in SCAN_ROOT_FILES:
        path = PLUGIN_ROOT / name
        if path.exists():
            yield path


def scan() -> list[tuple[Path, int, str, str]]:
    violations: list[tuple[Path, int, str, str]] = []
    for path in _iter_scan_paths():
            if not path.is_file():
                continue
            if path.suffix not in SCAN_EXTENSIONS:
                continue
            if path.name in EXEMPT_FILES:
                continue
            if "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                # Layer 1: named-pattern leaks.
                for pattern in FORBIDDEN_NAME_PATTERNS:
                    if re.search(pattern, line):
                        violations.append(
                            (path.relative_to(PLUGIN_ROOT), i, pattern, line.strip())
                        )
                # Layer 2: structural email domain check.
                for match in _EMAIL_RE.finditer(line):
                    domain = match.group(2)
                    if not _domain_is_allowed(domain):
                        violations.append(
                            (
                                path.relative_to(PLUGIN_ROOT),
                                i,
                                f"email-domain:{domain}",
                                line.strip(),
                            )
                        )
                # Layer 3 (v3.12.2+): structural name allowlist.
                for pattern, ctx in _scan_line_for_name_violations(line):
                    violations.append(
                        (path.relative_to(PLUGIN_ROOT), i, pattern, ctx)
                    )
    return violations


def main() -> int:
    violations = scan()
    if violations:
        print("FAIL — real customer/partner names found in plugin source:")
        print()
        for path, line_no, pattern, line in violations:
            print(f"  {path}:{line_no}  (matched /{pattern}/)")
            print(f"    {line}")
            print()
        print(f"Total: {len(violations)} violation(s)")
        print()
        print("Rule 26 of shared/CONTRACT.md: plugin source MUST NOT contain real")
        print("beta-customer or partner names. Use the placeholders documented in")
        print("references/PRIVACY_POLICY.md (Sam Sample, Bo Sample, Acme Co,")
        print("Northstar Partners, @example.com domains, etc.).")
        print()
        print("For an email-domain violation: replace with @example.com (or a")
        print(".example.com subdomain). The allowed-domain list lives at the top")
        print("of this test file — extend only for domains that are RFC literals,")
        print("not personal addresses.")
        return 1
    print("OK — no real customer/partner names in skill/reference/script surfaces")
    return 0


if __name__ == "__main__":
    sys.exit(main())
