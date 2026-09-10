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
