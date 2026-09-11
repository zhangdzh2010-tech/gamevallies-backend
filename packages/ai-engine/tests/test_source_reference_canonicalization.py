from types import SimpleNamespace

from src.engine.review_evidence import validate_review_evidence
from src.engine.source_references import locate_source_edit, source_reference_catalog


def test_bracketed_display_reference_is_canonicalized():
    source = '<script>function pause(){ state.paused = true }</script>'
    reference = next(iter(source_reference_catalog(source)))
    finding = {
        'issue': 'incorrect state',
        'dimension': 'is_complete_game',
        'source_ref': f'[`{reference}`]',
        'reason': 'The state transition is missing',
        'correction': 'Repair the transition',
        'section': 'SCRIPT',
        'repair_scope': 'local',
    }
    review = SimpleNamespace(
        issues=['incorrect state'], findings=[finding], is_complete_game=False,
        has_real_gameplay=True, difficulty_balanced=True, fun_score=8,
        visual_polish_score=8, character_quality_score=8,
    )

    assert validate_review_evidence(review, source) == []
    assert finding['source_ref'] == reference
    assert locate_source_edit(source, source_ref=f'[{reference}]') == (0, len(source))


def test_in_span_offset_of_same_revision_is_resolved_not_stale():
    from src.engine.source_references import resolve_source_reference

    source = 'const repeated=1; /* padding */\n' * 80
    catalog = source_reference_catalog(source)
    first = next(iter(catalog))
    revision = first.split(':')[0]
    resolved = resolve_source_reference(f'quoted {revision}:123 here', source, catalog)
    assert resolved == first
    finding = {
        'issue': 'incorrect state',
        'dimension': 'is_complete_game',
        'source_ref': f'{revision}:123',
        'reason': 'The state transition is missing',
        'correction': 'Repair the transition',
        'section': 'SCRIPT',
        'repair_scope': 'local',
    }
    review = SimpleNamespace(
        issues=['incorrect state'], findings=[finding], is_complete_game=False,
        has_real_gameplay=True, difficulty_balanced=True, fun_score=8,
        visual_polish_score=8, character_quality_score=8,
    )
    assert validate_review_evidence(review, source) == []
    assert finding['source_ref'] == first
    stale = resolve_source_reference('0000000000000000:123', source, catalog)
    assert stale is None


def test_source_ref_disambiguates_repeated_search():
    source = 'const score = 1; const score = 1; const lives = 3;'
    catalog = source_reference_catalog(source)
    reference = next(iter(catalog))
    start, end = locate_source_edit(
        source, search='const score = 1;', source_ref=reference,
    )
    assert source[start:end] == 'const score = 1;'
    assert start == source.index('const score = 1;')
