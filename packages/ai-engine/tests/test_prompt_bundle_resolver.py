import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import PromptBundleSnapshot
from src.engine.prompt_bundle_resolver import resolve_prompt_bundle_snapshot


def test_resolver_merges_system_prompt_bundle_and_runtime_profile_sources():
    prompt_map = {
        "bundle.runtime.locked_contract": "LOCKED_FROM_SYSTEM",
        "bundle.product.policy": "PRODUCT_POLICY_FROM_SYSTEM",
        "bundle.product.intent_parse": "INTENT_PARSE_FROM_SYSTEM",
        "bundle.product.logic_generate": "LOGIC_GENERATE_FROM_SYSTEM",
        "bundle.repair.syntax_structural": "SYNTAX_REPAIR_FROM_SYSTEM",
        "bundle.runtime.profile.casual_lane": "PROFILE_FROM_SYSTEM",
    }

    bundle_row = {
        "id": "runtime-v1",
        "version": 1,
        "product_policy": "PRODUCT_POLICY_FROM_BUNDLE_ROW",
        "locked_contract_override": "LOCKED_OVERRIDE_FROM_BUNDLE_ROW",
        "repair_playbook": "Keep repairs surgical and preserve the core mechanic.",
        "profile_overrides": {
            "logic_generate": {
                "first_interaction": "start_play_immediately",
                "visible_feedback": "required",
            }
        },
    }
    runtime_profile_row = {
        "id": "casual_lane",
        "few_shot_prompt": "PROFILE_FROM_RUNTIME_PROFILE_TABLE",
    }

    with patch(
        "src.engine.prompt_bundle_resolver.get_prompt",
        side_effect=lambda key: prompt_map.get(key),
    ), patch(
        "src.engine.prompt_bundle_resolver.get_prompt_bundle",
        return_value=bundle_row,
    ), patch(
        "src.engine.prompt_bundle_resolver.get_runtime_profile",
        return_value=runtime_profile_row,
    ):
        resolved = resolve_prompt_bundle_snapshot(
            PromptBundleSnapshot(bundle_id="runtime-v1", bundle_version=1, layers={}),
            runtime_profile="casual_lane",
        )

    resolved_prompts = resolved.layers["resolved_prompts"]
    assert "PRODUCT_POLICY_FROM_SYSTEM" in resolved_prompts["product_policy"]["content"]
    assert "PRODUCT_POLICY_FROM_BUNDLE_ROW" in resolved_prompts["product_policy"]["content"]
    assert "LOCKED_FROM_SYSTEM" in resolved_prompts["locked_contract"]["content"]
    assert "LOCKED_OVERRIDE_FROM_BUNDLE_ROW" in resolved_prompts["locked_contract"]["content"]
    assert "LOGIC_GENERATE_FROM_SYSTEM" in resolved_prompts["logic_generate"]["content"]
    assert "start_play_immediately" in resolved_prompts["logic_generate"]["content"]
    assert "SYNTAX_REPAIR_FROM_SYSTEM" in resolved_prompts["repair_syntax_structural"]["content"]
    assert "BUNDLE REPAIR PLAYBOOK" in resolved_prompts["repair_syntax_structural"]["content"]
    assert "PROFILE_FROM_SYSTEM" in resolved_prompts["profile_few_shot"]["content"]
    assert "PROFILE_FROM_RUNTIME_PROFILE_TABLE" in resolved_prompts["profile_few_shot"]["content"]
