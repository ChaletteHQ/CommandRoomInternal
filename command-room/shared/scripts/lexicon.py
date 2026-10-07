#!/usr/bin/env python3
"""Bilingual lexicon overlay (Spanish beta) — additive, opt-in, zero-cost when off.

Principle 1 of the Spanish build (see ``references/SPANISH_BUILD_PLAN.md``):
**English ships native and unchanged.** No English install changes behavior by a
single byte. When no language config names a non-English language, every accessor
here returns the caller's English default *verbatim* after one cheap check, and
accent-folding is a no-op.

Principle 2: **bilingual, not Spanish-only.** When ``es`` is active the phrase
lists become ``English ∪ Spanish`` — a workspace processes mixed-language email
and meetings. English terms are never dropped.

Activation signal (written by ``command-room-onboarding``):

    _hq/data/skill_config/language.json
    { "version": 1, "languages": ["en", "es"], "last_writer": "command-room-onboarding" }

- Absent file  ⇒ English-only (production default; existing workspaces unaffected).
- ``languages`` lists non-``en`` packs to overlay, in priority order. ``en`` is
  always implicitly present. Adding ``fr``/``pt`` later is a new pack file, no code.

Lexicon packs are inert data, loaded by nothing unless ``language.json`` names them:

    shared/lexicons/<lang>.json

Read-only. Pure data + path logic — never touches the substrate.
"""
from __future__ import annotations

import json
import os
import unicodedata
from pathlib import Path
from typing import Iterable, Optional

try:
    from workspace_root import find_workspace_root
except Exception:  # pragma: no cover - resolver always ships alongside
    find_workspace_root = None  # type: ignore

# Inert packs live in shared/lexicons/ (this module is in shared/scripts/).
_LEXICON_DIR = Path(__file__).resolve().parent.parent / "lexicons"

# Per-resolved-root / per-lang caches.
_langs_cache: dict[str, list[str]] = {}
_pack_cache: dict[str, dict] = {}
# Self-locate result memoized by cwd, so the hot tokenizer path never re-walks
# the tree. Value is the resolved root Path or None.
_selfloc_cache: dict[str, Optional[Path]] = {}
# CB-T2B-1: a root's resolved cache key, memoized by the spelling handed in
# (see `_resolved_key`).
_resolved_key_cache: dict[str, str] = {}
# Sentinel: "no workspace/config from this cwd" so the self-locating fast path
# doesn't re-walk the tree every call.
_NO_ROOT = "\x00no-root"


def _resolve_root(workspace_root: Optional[Path | str]) -> Optional[Path]:
    """Root to read config from. Explicit arg wins; else self-locate from cwd
    via the shared resolver (memoized by cwd). Any failure ⇒ None (caller uses
    the English default)."""
    if workspace_root is not None:
        return Path(workspace_root)
    if find_workspace_root is None:
        return None
    cwd = str(Path.cwd())
    if cwd in _selfloc_cache:
        return _selfloc_cache[cwd]
    try:
        root = find_workspace_root()
    except Exception:
        root = None
    _selfloc_cache[cwd] = root
    return root


def _resolved_key(root: Path) -> str:
    """The cache key for a root: its resolved path, resolved ONCE per spelling.

    CB-T2B-1. `Path.resolve()` walks the filesystem, and the tokenizer asks for
    the active languages once per token (221,586 times in one Morning Brief
    pack build on a real book, where it was about half the build's time through
    the door). The resolved key is memoized by the spelling handed in; a
    relative spelling is memoized together with the cwd it was relative to, so
    a process that changes directory never reads another folder's answer.
    A link retargeted while the process runs keeps its first answer until
    `_clear_caches()`; no caller retargets a root inside one process.

    LOWS2 row 12 (CB1 F-3): on Windows a spelling is absolute only when it
    names a drive AND `isabs` says so; a root-relative `\\foo` (which `isabs`
    calls absolute on 3.12) and a drive-relative `D:foo` are tied to the cwd
    like any relative spelling. POSIX is unchanged."""
    raw = str(root)
    absolute = os.path.isabs(raw) and (os.name != "nt"
                                       or bool(os.path.splitdrive(raw)[0]))
    memo = raw if absolute else raw + "\x00" + os.getcwd()
    key = _resolved_key_cache.get(memo)
    if key is None:
        key = str(root.resolve())
        _resolved_key_cache[memo] = key
    return key


def _active_languages(workspace_root: Optional[Path | str] = None) -> list[str]:
    """Non-``en`` language codes active for this workspace, priority order.
    Empty ⇒ English-only fast path. Cached per resolved root."""
    root = _resolve_root(workspace_root)
    key = _NO_ROOT if root is None else _resolved_key(root)
    if key in _langs_cache:
        return _langs_cache[key]

    langs: list[str] = []
    if root is not None:
        cfg = root / "_hq" / "data" / "skill_config" / "language.json"
        if cfg.is_file():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
                for code in data.get("languages", []):
                    if code and code != "en" and code not in langs:
                        langs.append(str(code))
            except Exception:
                langs = []
    _langs_cache[key] = langs
    return langs


def _load_pack(lang: str) -> dict:
    if lang in _pack_cache:
        return _pack_cache[lang]
    pack: dict = {}
    p = _LEXICON_DIR / f"{lang}.json"
    if p.is_file():
        try:
            pack = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pack = {}
    _pack_cache[lang] = pack
    return pack


def _merge(english_default: Iterable[str], extra_lists: Iterable[Iterable[str]]):
    """English first (order preserved, deduped), then each extra list appended."""
    merged: list[str] = []
    seen: set[str] = set()
    for term in english_default:
        if term not in seen:
            seen.add(term)
            merged.append(term)
    for lst in extra_lists:
        for term in lst or []:
            if term not in seen:
                seen.add(term)
                merged.append(term)
    if isinstance(english_default, frozenset):
        return frozenset(merged)
    if isinstance(english_default, set):
        return set(merged)
    return tuple(merged)


def load_lexicon_terms(
    scanner: str,
    key: str,
    english_default: Iterable[str],
    workspace_root: Optional[Path | str] = None,
):
    """English default ∪ active-language terms for ``scanner[key]``.

    Return type mirrors ``english_default`` (``tuple``/``frozenset``/``set``) so
    ``phrase in CONST`` and set-membership call sites are unaffected. English
    terms always lead and are never dropped. No non-``en`` language active ⇒
    returns ``english_default`` unchanged (the production path)."""
    langs = _active_languages(workspace_root)
    if not langs:
        return english_default
    extra = [(_load_pack(lang).get(scanner) or {}).get(key, []) for lang in langs]
    return _merge(english_default, extra)


def stopwords(english_default, workspace_root: Optional[Path | str] = None):
    """Merged stop-word set (packs store the list at top-level ``stopwords``).
    When accent-folding is active the returned set is itself folded so folded
    tokens compare correctly against it."""
    langs = _active_languages(workspace_root)
    if not langs:
        return english_default
    merged = _merge(english_default, [_load_pack(lang).get("stopwords", []) for lang in langs])
    if accent_fold_enabled(workspace_root):
        folded = {fold_accents(w) for w in merged}
        return frozenset(folded) if isinstance(english_default, frozenset) else folded
    return merged


def accent_fold_enabled(workspace_root: Optional[Path | str] = None) -> bool:
    """True if any active pack sets ``accent_fold: true``. English ⇒ False, so
    ``fold_accents`` is never called and normalization is unchanged."""
    for lang in _active_languages(workspace_root):
        if _load_pack(lang).get("accent_fold"):
            return True
    return False


def fold_accents(s: str) -> str:
    """NFKD-decompose and strip combining marks: ``José`` → ``Jose``,
    ``Peña`` → ``Pena``. Case is preserved — callers that need
    case-insensitive comparison lowercase first (entity_resolve._normalize
    does). A no-op on ASCII."""
    if not s:
        return s
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def _clear_caches() -> None:
    """Test hook — drop memoized language/pack state between fixtures."""
    _langs_cache.clear()
    _pack_cache.clear()
    _selfloc_cache.clear()
    _resolved_key_cache.clear()
