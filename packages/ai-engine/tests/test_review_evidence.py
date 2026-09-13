from types import SimpleNamespace

from src.engine.review_evidence import (
    FINDINGS_EVIDENCE_REQUIRED,
    coerce_review_findings,
    coerce_review_issues,
    normalize_finding_source_fields,
    promote_issue_objects_to_findings,
    validate_review_evidence,
)
from src.engine.review_recovery import (
    _build_review_correction,
    _citation_only,
    _recoverable_evidence_errors,
)
from src.engine.source_references import source_reference_catalog


SOURCE = '<html><body><script>function pause(){state="running";}</script></body></html>'
FINDING = dict(
    issue='pause keeps the simulation running',
    dimension='is_complete_game',
    code_excerpt='function pause(){state="running";}',
    reason='pause assigns the active state',
    correction='set state to paused',
    section='SCRIPT',
    repair_scope='local',
)


def _review(**overrides):
    values = dict(
        issues=[],
        findings=[],
        is_complete_game=True,
        has_real_gameplay=True,
        difficulty_balanced=True,
        fun_score=8,
        visual_polish_score=8,
        character_quality_score=8,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_empty_findings_with_issues_is_the_post85_review_evidence_error():
    errors = validate_review_evidence(
        _review(is_complete_game=False, issues=['click bound missing'], findings=[]),
        SOURCE,
    )
    assert errors == [FINDINGS_EVIDENCE_REQUIRED]


def test_null_findings_with_issues_is_the_same_evidence_gap():
    errors = validate_review_evidence(
        _review(is_complete_game=False, issues=['click bound missing'], findings=None),
        SOURCE,
    )
    assert errors == [FINDINGS_EVIDENCE_REQUIRED]


def test_valid_finding_is_accepted_and_projects_issues():
    review = _review(
        is_complete_game=False,
        issues=['short summary'],
        findings=[dict(FINDING)],
    )
    assert validate_review_evidence(review, SOURCE) == []
    assert review.issues == [FINDING['issue']]


def test_source_ref_alias_is_normalized_before_citation_check():
    reference = next(iter(source_reference_catalog(SOURCE)))
    finding = dict(FINDING)
    finding.pop('code_excerpt')
    finding['sourceRef'] = reference
    review = _review(is_complete_game=False, issues=[finding['issue']], findings=[finding])
    assert validate_review_evidence(review, SOURCE) == []
    assert finding['source_ref'] == reference
    assert finding['code_excerpt'] == source_reference_catalog(SOURCE)[reference]


def test_single_finding_object_is_wrapped_for_validation():
    review = _review(is_complete_game=False, issues=[FINDING['issue']], findings=dict(FINDING))
    assert validate_review_evidence(review, SOURCE) == []
    assert review.findings == [FINDING]


def test_missing_finding_evidence_is_a_recoverable_citation_error():
    errors = [FINDINGS_EVIDENCE_REQUIRED]
    assert _citation_only(errors)
    assert _recoverable_evidence_errors(errors)
    correction = _build_review_correction(errors, '{"issues":["click bound missing"],"findings":[]}')
    assert FINDINGS_EVIDENCE_REQUIRED in correction
    assert 'issues and findings must have the same length' in correction
    assert 'source_ref' in correction
    assert 'Do not change the artifact' in correction
    assert 'Never invent or inflate scores' in correction
    assert 'findings=[]' in correction


def test_coerce_helpers_do_not_invent_issue_or_citation_text():
    assert coerce_review_findings(None) == []
    assert coerce_review_findings({'issue': 'x'}) == [{'issue': 'x'}]
    assert coerce_review_findings('not-a-list') is None
    assert coerce_review_issues(['ok']) == ['ok']
    assert coerce_review_issues([{'issue': 'object issue'}]) == ['object issue']
    assert coerce_review_issues([{}]) is None
    assert coerce_review_issues('bad') is None
    finding = {'sourceRef': 'abc', 'excerpt': 'fn()'}
    normalize_finding_source_fields(finding)
    assert finding['source_ref'] == 'abc'
    assert finding['code_excerpt'] == 'fn()'
    assert promote_issue_objects_to_findings([FINDING], []) == [FINDING]
    assert promote_issue_objects_to_findings(['plain'], []) == []
