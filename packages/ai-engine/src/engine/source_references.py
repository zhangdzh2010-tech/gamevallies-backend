"""Revision-bound source addressing shared by reviews and atomic local edits."""
from __future__ import annotations
import hashlib
import re


_SOURCE_REFERENCE_RE = re.compile(r'^[0-9a-f]{16}:\d+$')
_SOURCE_REFERENCE_FIND_RE = re.compile(r'([0-9a-f]{16}):(\d+)')


def canonical_source_reference(reference: object) -> str | None:
    """Accept the catalog key and common reviewer wrappers around it."""
    if not isinstance(reference, str):
        return None
    normalized = reference.strip().strip('`"\'').strip()
    if normalized.startswith('[') and normalized.endswith(']'):
        normalized = normalized[1:-1].strip().strip('`"\'').strip()
    if _SOURCE_REFERENCE_RE.fullmatch(normalized):
        return normalized
    match = _SOURCE_REFERENCE_FIND_RE.search(reference)
    return f'{match.group(1)}:{match.group(2)}' if match else None


def source_reference_catalog(source: str) -> dict[str, str]:
    """Server-issued, revision-bound references; never fuzzy-match model text.

    Offsets disambiguate repeated source. Short spans also bound repair context
    for minified HTML without requiring the reviewer to retype escaped code.
    """
    revision = hashlib.sha256(source.encode()).hexdigest()[:16]
    return {f'{revision}:{offset}':source[offset:offset+600]
            for offset in range(0, len(source), 600)}


def indexed_review_source(source: str) -> str:
    return '\n'.join(f'[{reference}]\n{excerpt}'
        for reference, excerpt in source_reference_catalog(source).items())


def resolve_source_reference(
    reference: object,
    source: str,
    catalog: dict[str, str] | None = None,
) -> str | None:
    """Map a citation onto this revision's catalog without inventing a span.

    Exact catalog keys win. An in-span offset of the SAME revision (for example
    citing byte 123 when the printed label is :0) is the same source window,
    not a new claim. A different hash is stale and stays rejected.
    """
    catalog = source_reference_catalog(source) if catalog is None else catalog
    canonical = canonical_source_reference(reference)
    if canonical is None:
        return None
    if canonical in catalog:
        return canonical
    try:
        revision, offset_text = canonical.rsplit(':', 1)
        offset = int(offset_text)
    except ValueError:
        return None
    expected = hashlib.sha256(source.encode()).hexdigest()[:16]
    if revision != expected:
        return None
    for key, excerpt in catalog.items():
        start = int(key.rsplit(':', 1)[1])
        if start <= offset < start + max(len(excerpt), 1):
            return key
    if catalog and 0 <= offset <= len(source):
        last_key = next(reversed(catalog))
        last_start = int(last_key.rsplit(':', 1)[1])
        if last_start <= offset:
            return last_key
    return None


def locate_unique_search(source: str, search: str, *, region: tuple[int, int] | None = None) -> tuple[int, int]:
    """Require a single occurrence; distinguish missing from ambiguous."""
    if not isinstance(search, str) or not search:
        raise ValueError('search_not_found: search must be a non-empty string')
    haystack = source if region is None else source[region[0]:region[1]]
    origin = 0 if region is None else region[0]
    count = haystack.count(search)
    if count == 1:
        start = origin + haystack.index(search)
        return start, start + len(search)
    stripped = search.strip()
    if stripped and stripped != search and haystack.count(stripped) == 1:
        start = origin + haystack.index(stripped)
        return start, start + len(stripped)
    if count == 0:
        raise ValueError('search_not_found: search does not occur in the original source')
    raise ValueError('search_not_unique: search must match exactly once in the original source')


def locate_source_edit(source: str, *, search=None, source_ref=None) -> tuple[int, int]:
    has_search = isinstance(search, str) and bool(search)
    if not has_search and source_ref is None:
        raise ValueError('exactly one search or source_ref is required')
    if source_ref is not None:
        catalog = source_reference_catalog(source)
        resolved = resolve_source_reference(source_ref, source, catalog)
        if resolved is None:
            raise ValueError('unknown or stale source reference')
        start = int(resolved.rsplit(':', 1)[1])
        span_end = start + len(catalog[resolved])
        if has_search:
            try:
                return locate_unique_search(source, search)
            except ValueError:
                region = source[start:span_end]
                if search in region:
                    found = start + region.index(search)
                    return found, found + len(search)
                return locate_unique_search(source, search, region=(start, span_end))
        return start, span_end
    return locate_unique_search(source, search)


def apply_source_edits(source: str, edits: list[tuple[int, int, str]]) -> str:
    edits = sorted(edits)
    if any(left[1]>right[0] for left,right in zip(edits,edits[1:])):
        raise ValueError('overlapping patches')
    result = source
    for start,end,replacement in reversed(edits):
        result = result[:start]+replacement+result[end:]
    return result
