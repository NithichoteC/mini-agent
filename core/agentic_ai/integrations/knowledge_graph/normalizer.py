"""Normalize entity names across extracted paths.

Phase 2 scope (controlled canonicalization, not full semantic reasoning):

1. Deterministic cleanup, applied to a *matching key* only (the
   human-readable display label is never lowercased/stripped of meaning):
   - strip whitespace, collapse repeated internal spaces
   - case-insensitive comparison
   - remove a single weak leading article ("the", "a", "an") from the
     matching key only
   - normalize safe punctuation variants for matching keys (currently:
     hyphen vs space, e.g. "data-retention migration" ~ "data retention
     migration")
2. Explicit alias maps (caller-declared equivalence) deterministically
   override the heuristics above, e.g.
   ``aliases={"Q3 outage": ["the major outage", "a major outage"]}``.
   Aliases always win, even over the heuristic's own canonical label
   choice.
3. Conservative heuristic matching: entities only merge automatically when
   their matching keys are identical after the deterministic cleanup in
   (1). This deliberately does NOT merge generic/substring relationships
   such as "audit" -> "compliance audit" or "outage" -> "Q3 outage" --
   those require an explicit alias (or a future, stronger resolution
   mechanism out of scope for this phase).

Only `source` and `target` fields are canonicalized. `evidence`,
`confidence`, `source_chunk_id`, and `relation` are passed through
unchanged, and the input paths are never mutated.
"""

from __future__ import annotations

_WEAK_ARTICLES = ("the ", "a ", "an ")


def _clean_whitespace(text: str) -> str:
    return " ".join(text.split()).strip()


def _matching_key(text: str) -> str:
    """Deterministic matching key: punctuation- and article-insensitive.

    This is used only to decide whether two mentions are "the same key" --
    it is never used as a display label.
    """
    cleaned = _clean_whitespace(text)
    # Safe punctuation normalization: hyphen vs space, e.g.
    # "data-retention migration" == "data retention migration".
    cleaned = cleaned.replace("-", " ")
    cleaned = " ".join(cleaned.split())
    lower = cleaned.lower()
    for article in _WEAK_ARTICLES:
        if lower.startswith(article):
            lower = lower[len(article):]
            break
    return lower.strip()


def normalize_entity_key(text: str) -> str:
    """Public deterministic matching-key helper (Phase 8).

    Mirrors exactly the matching key `normalize_entities()` uses internally
    (via the private `_matching_key()`) to decide whether two entity
    mentions are "the same": collapse whitespace, lowercase, treat hyphen
    and space as equivalent, and strip a single leading weak article
    ("the"/"a"/"an"). Exposed as a small, stable public helper so other
    modules (e.g. `graph_library.retriever.resolve_seed_entities`) can reuse
    the exact same deterministic key instead of importing a private
    underscore-prefixed helper or duplicating the logic. Never does
    substring/fuzzy/semantic matching -- purely the Level 1 deterministic
    cleanup described in dev_mem/04_phase2_entity_resolution_context.md.
    """
    return _matching_key(text)


def _has_leading_weak_article(text: str) -> bool:
    lower = text.lower()
    return any(lower.startswith(article) for article in _WEAK_ARTICLES)


def _strip_leading_weak_article(text: str) -> str:
    """Drop a single leading weak article from a *display* label.

    This is safe cleanup (Level 1): "the storage bottleneck" and "storage
    bottleneck" mean the same thing, so even a lone mention should display
    without the article. Only applied to heuristic-derived labels -- an
    explicit alias-map canonical label is never altered.
    """
    lower = text.lower()
    for article in _WEAK_ARTICLES:
        if lower.startswith(article):
            stripped = text[len(article):].strip()
            return stripped or text
    return text


def _prefer_canonical(candidate: str, existing: str) -> bool:
    """Return True if `candidate` should replace `existing` as canonical.

    Preference order:
    1. a label that does not start with a weak leading article,
    2. a more title-cased / proper-cased label,
    3. a longer label, as a final, stable tie-break.
    """
    candidate_article = _has_leading_weak_article(candidate)
    existing_article = _has_leading_weak_article(existing)
    if candidate_article != existing_article:
        return not candidate_article

    candidate_title = sum(1 for word in candidate.split() if word[:1].isupper())
    existing_title = sum(1 for word in existing.split() if word[:1].isupper())
    if candidate_title != existing_title:
        return candidate_title > existing_title

    return len(candidate) > len(existing)


def _collect_raw_entities(paths: list[dict]) -> list[str]:
    raw_entities: list[str] = []
    for path in paths:
        if not isinstance(path, dict):
            continue
        for field in ("source", "target"):
            value = path.get(field)
            if isinstance(value, str):
                raw_entities.append(value)
    return raw_entities


def _build_canonical_map(
    entities: list[str], aliases: dict[str, list[str]] | None
) -> dict[str, str]:
    """Map matching_key -> canonical display label.

    Step 1 groups raw entities by deterministic matching key (Level 1 +
    conservative Level 3 heuristic merge). Step 2 applies explicit aliases
    (Level 2), which always overwrite whatever the heuristic chose -- both
    for the canonical label's own key and for every declared variant's key.
    """
    canonical_map: dict[str, str] = {}

    for entity in entities:
        cleaned = _clean_whitespace(entity)
        if not cleaned:
            continue
        key = _matching_key(cleaned)
        if not key:
            continue
        if key not in canonical_map:
            canonical_map[key] = cleaned
        elif _prefer_canonical(cleaned, canonical_map[key]):
            canonical_map[key] = cleaned

    # Final safe cleanup: a heuristic-derived label should never display
    # with a leading weak article, even if every raw mention had one (e.g.
    # only "the storage bottleneck" ever appears -- it should still display
    # as "storage bottleneck").
    for key, label in list(canonical_map.items()):
        if _has_leading_weak_article(label):
            canonical_map[key] = _strip_leading_weak_article(label)

    if aliases:
        for canonical_label, variants in aliases.items():
            canonical_label = _clean_whitespace(str(canonical_label))
            if not canonical_label:
                continue
            canonical_map[_matching_key(canonical_label)] = canonical_label
            for variant in variants or []:
                variant = _clean_whitespace(str(variant))
                if variant:
                    canonical_map[_matching_key(variant)] = canonical_label

    return canonical_map


def normalize_entities(
    paths: list[dict], aliases: dict[str, list[str]] | None = None
) -> list[dict]:
    """Normalize `source`/`target` entity names across a list of paths.

    Does not mutate the input paths or any field other than `source`/
    `target`. Paths that are not well-formed dicts are passed through
    unchanged (validation is `schema.validate_path`'s job, not this
    function's).
    """
    raw_entities = _collect_raw_entities(paths)
    canonical_map = _build_canonical_map(raw_entities, aliases)

    normalized: list[dict] = []
    for path in paths:
        if not isinstance(path, dict):
            normalized.append(path)
            continue
        new_path = dict(path)
        for field in ("source", "target"):
            value = path.get(field)
            if isinstance(value, str):
                cleaned = _clean_whitespace(value)
                new_path[field] = canonical_map.get(_matching_key(cleaned), cleaned)
        normalized.append(new_path)

    return normalized


def build_entity_alias_report(
    paths: list[dict], aliases: dict[str, list[str]] | None = None
) -> dict[str, list[str]]:
    """Inspect which raw entity mentions collapsed onto which canonical label.

    Returns ``{canonical_label: [sorted distinct variant mentions]}``,
    using the exact same canonicalization logic as `normalize_entities`
    (same matching keys, same alias handling). Canonical labels with no
    merged variants are omitted -- this is a debugging/inspection helper,
    not used internally.

    Example::

        {
            "Q3 outage": ["q3 outage", "the Q3 outage"],
            "Project Phoenix": ["project phoenix"],
        }
    """
    raw_entities = _collect_raw_entities(paths)
    canonical_map = _build_canonical_map(raw_entities, aliases)

    variants_by_canonical: dict[str, set[str]] = {}
    for entity in raw_entities:
        cleaned = _clean_whitespace(entity)
        if not cleaned:
            continue
        canonical = canonical_map.get(_matching_key(cleaned), cleaned)
        if cleaned == canonical:
            continue
        variants_by_canonical.setdefault(canonical, set()).add(cleaned)

    return {
        canonical: sorted(variants)
        for canonical, variants in sorted(variants_by_canonical.items())
        if variants
    }
