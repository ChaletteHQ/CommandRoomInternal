# Privacy policy for plugin source

This repo is the Command Room plugin source. It is distributed to operators (currently via private repo access). Plugin source MUST NOT contain personally identifying information about real beta customers, partners, or other third parties.

## Rule

Examples, fixtures, CHANGELOG entries, docstrings, comments, and skill references must use placeholder names and `@example.com` domains.

### Approved placeholder names

There are exactly **11 approved first names** for use in skill examples. Pick from this list. No others:

```
Sam     Bo      Rio     Skyler   Mira    Aria
Bowie   Lyra    Quinn   Dustin   Adan
```

For full-name introductions (the first mention of a person in any example), pair an approved first name with the canonical **surname `Sample`** (preferred) or **`Stone`** (for disambiguation examples — e.g., `Sam Sample` vs `Sam Stone`). Examples:

- ✅ `Sam Sample`, `Aria Sample`, `Bo Sample`, `Mira Stone`
- ❌ `Sam Davidson` (Davidson isn't an approved surname)
- ❌ `Jordan Sample` (Jordan isn't an approved first name — even with `Sample`, the first name leaks)

Subsequent references within the same file can drop to just the first name (`Sam said...`, `with Sam`, `Sam's project`). The surname-on-first-mention is the signal that says "this is a placeholder, not a real person."

**Do NOT pick a first name that matches anyone you actually know**, including indirect acquaintances. If in doubt, pick from the 11-name list or talk to the maintainer before introducing a new placeholder.

### Approved placeholder companies

- `Acme Co`, `Acme Logistics`, `Acme Fragrances`, `Acme Restaurant`, `Acme Catering`, `Acme Bakery`
- `Northstar Partners`
- `Category Company`, `Category Food Truck`, `Category Bakery`

Any other company name in an example needs an entry in `tests/run_no_real_customer_names_test.py`'s `APPROVED_ORG_PHRASES`.

### Approved emails

- `name@example.com` (RFC 2606 reserved)
- A `.example.com` subdomain (e.g. `bo@acme.example.com`)
- Other domains require an entry in `ALLOWED_EMAIL_DOMAIN_PATTERNS` at the top of the test file.

## Exception — the maintainer's support address

`matthew@chaletteholdings.com` is the project maintainer's public support address. The `report-bug` skill drafts emails to this address by design — keep the email literal in that one context.

**The first name "Matthew" by itself is NOT an exception** — it's the maintainer's real name and shouldn't appear in skill prose. Use "the maintainer" / "the team" / "support" instead, or reference the email directly.

## Why

The plugin repo is distributed to beta operators and (eventually) public collaborators. CHANGELOG history, skill docs, and code examples become a partial transcript of who used the product and when. That's leak surface, even in a private repo, because trust scopes change. A customer reading an example "with Daniel" might recognize who Daniel is.

## How this is enforced (v3.12.2+)

Three structural layers run as part of every commit + every release:

1. **Denylist (legacy v3.6.2).** A specific list of known historical leaks at the top of `tests/run_no_real_customer_names_test.py` (`FORBIDDEN_NAME_PATTERNS`). Sticky values like specific surnames stay here.

2. **Email allowlist (v3.6.3).** Any email address whose domain isn't on `ALLOWED_EMAIL_DOMAIN_PATTERNS` fails. Catches any new email a future skill author writes without needing it on a denylist.

3. **Name allowlist (v3.12.2 — the durable fix).** Detection-side: a dictionary of ~300 common English first names (`COMMON_FIRST_NAMES`). Allowlist-side: the 11 approved placeholders above (`APPROVED_FIRST_NAMES`). Any token that's in the dictionary AND not in the allowlist fails, in three patterns:

   - **Firstname-Lastname pair:** `Daniel Spaeth` — common first name + adjacent capitalized word
   - **Person context:** `with Daniel`, `for Daniel`, `to Daniel`, `from Daniel`
   - **Possessive:** `Daniel's project`

The combination is fails-closed: a new agent writing a new example can't ship a real first name without either (a) deliberately adding it to the approved-name allowlist (and updating this doc) OR (b) the pre-commit hook catching it before the commit lands.

### Pre-commit hook

`.githooks/pre-commit` runs all four structural guards on every `git commit`. On a fresh clone, install with:

```bash
git config core.hooksPath command-room/.githooks
```

Pre-v3.12.2 the privacy guard only ran at push time (in the `ship-cr-plugin` skill); v3.12.2 moves it to write-time so leaks get caught the moment they're authored.

## How to add a new placeholder name (rare)

If a new example genuinely needs a name pattern that's not in the approved list:

1. Pick a name that doesn't match anyone in your real-world contacts.
2. Add it to `APPROVED_FIRST_NAMES` in `tests/run_no_real_customer_names_test.py`.
3. Update the approved-first-names list at the top of this doc in the same PR.
4. Re-run the guard locally to confirm it still passes.

The 11 existing placeholders cover virtually every example. Adding new ones is a last resort, not a normal extension path.

## Documenting fixed leaks (without re-leaking)

The CHANGELOG and structural-test commit messages sometimes need to describe a leak that was fixed. Do NOT include the literal real name being fixed — that defeats the guard, which scans CHANGELOG.md too. Describe leaks by class:

- ✅ "Sanitized a maintainer-name reference in skill-X."
- ✅ "Replaced a beta-customer surname with `Sample` in skill-Y."
- ❌ "Removed all 14 instances of Matthew Davidov from skill-X."
- ❌ "Fixed Daniel Spaeth → Aria Sample in calendar-writer."

If the leak details need to be preserved for audit, that goes in your private workspace, not the public-ish repo.
