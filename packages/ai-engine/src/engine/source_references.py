"""Revision-bound source addressing shared by reviews and atomic local edits."""
from __future__ import annotations
import hashlib


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


def locate_source_edit(source: str, *, search=None, source_ref=None) -> tuple[int, int]:
    if (search is None) == (source_ref is None):
        raise ValueError('exactly one search or source_ref is required')
    if source_ref is not None:
        catalog = source_reference_catalog(source)
        if not isinstance(source_ref, str) or source_ref not in catalog:
            raise ValueError('unknown or stale source reference')
        start = int(source_ref.rsplit(':',1)[1])
        return start, start+len(catalog[source_ref])
    if not isinstance(search,str) or not search or source.count(search) != 1:
        raise ValueError('search_not_unique: search must match exactly once in the original source')
    start = source.index(search)
    return start, start+len(search)


def apply_source_edits(source: str, edits: list[tuple[int, int, str]]) -> str:
    edits = sorted(edits)
    if any(left[1]>right[0] for left,right in zip(edits,edits[1:])):
        raise ValueError('overlapping patches')
    result = source
    for start,end,replacement in reversed(edits):
        result = result[:start]+replacement+result[end:]
    return result
