import json
from pathlib import Path
import pytest
from src.services.llm_readiness import capability_state, capability_rejections

CASES = json.loads((Path(__file__).resolve().parents[3] / "contracts/llm/readiness-cases.json").read_text())

@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_shared_routing_semantics(case):
    original = json.loads(json.dumps(case["flags"]))
    _, legacy = capability_state(case["flags"])
    assert legacy is case["legacy"]
    assert capability_rejections(case["flags"], case["step"]) == case["blocked"]
    assert case["flags"] == original

def test_legacy_signature_does_not_coerce_zero_to_false():
    flags = {"verified": 0, "supports_dialogue": False, "supports_patch_generation": False, "supports_full_html_rewrite": False}
    assert capability_state(flags)[1] is False
    assert capability_rejections(flags, "intent_parse") == ["supports_dialogue"]
