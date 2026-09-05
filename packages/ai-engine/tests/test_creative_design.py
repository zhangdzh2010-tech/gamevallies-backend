import asyncio
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.api.models import GameSpec, SlotState
from src.engine.creative_design import (
    creative_design_contract, gameplay_fingerprint, normalize_creative_design,
)
from src.engine.dialogue_engine import DialogueEngine, _build_game_spec, _normalize_slot_payload


def design():
    return {"candidates": [{"core_loop": "drag boxes to guide the cat", "input": "drag",
                            "goal": "reach the fish", "signature_rule": "indirect control"}],
            "selected_index": 0, "selection_reason": "preserves the requested indirect control"}


def test_optional_invalid_design_does_not_break_generation():
    assert normalize_creative_design(None) is None
    assert normalize_creative_design({**design(), "selected_index": 9}) is None
    assert normalize_creative_design({**design(), "selected_index": True}) is None
    assert normalize_creative_design({**design(), "candidates": []}) is None


def test_seed_changes_guidance_but_never_overrides_explicit_requirements():
    assert creative_design_contract("a") == creative_design_contract("a")
    assert creative_design_contract("a") != creative_design_contract("b")
    assert "faithful recreation" in creative_design_contract("a")


def test_fingerprint_ignores_palette_and_detects_rule_change():
    spec = GameSpec(game_type="casual", creative_design=design())
    same_game = spec.model_copy(deep=True)
    same_game.visual_style.theme = "space"
    same_game.visual_style.palette = ["#ffffff"]
    assert gameplay_fingerprint(spec) == gameplay_fingerprint(same_game)
    same_game.rules.win_condition = "reach an exit"
    assert gameplay_fingerprint(spec) != gameplay_fingerprint(same_game)


@pytest.mark.parametrize("game_type", ["puzzle", "educational"])
def test_puzzles_do_not_inherit_survival_and_collection_defaults(game_type):
    spec = _build_game_spec(SlotState(game_type=game_type))
    assert spec.rules.lose_condition == "none"
    assert spec.rules.scoring == "objective_progress"


def test_explicit_lives_and_scoring_survive_slot_conversion():
    data = _normalize_slot_payload({"game_type": "casual", "lives": 2,
                                    "scoring": "completed laps", "lose_condition": "two collisions"})
    spec = _build_game_spec(SlotState(**data))
    assert spec.rules.lives == 2
    assert spec.rules.scoring == "completed laps"
    assert spec.rules.lose_condition == "two collisions"
    assert "lives" not in _normalize_slot_payload({"lives": -3})


def test_existing_parse_call_persists_design_without_extra_model_request():
    engine = DialogueEngine()
    payload = {"game_type": "puzzle", "core_mechanic": "drag boxes to guide the cat",
               "theme": "cats", "input_method": "drag", "win_condition": "reach the fish",
               "difficulty": "easy", "creative_design": design()}
    with patch.object(engine._client, "is_enabled", return_value=True), patch(
        "src.engine.dialogue_engine.require_prompt", return_value="Return JSON {slot_json_schema}"
    ), patch.object(engine, "_complete_slot_request", new=AsyncMock(return_value=json.dumps(payload))) as call:
        spec = asyncio.run(engine.parse_description_to_spec("猫咪自动走，拖动箱子搭路拿鱼", variation_seed="a"))
    assert call.await_count == 1
    assert spec.creative_design["selected_index"] == 0
    assert spec.creative_design["candidates"][0]["signature_rule"] == "indirect control"


def test_creative_extension_can_be_disabled():
    engine = DialogueEngine()
    payload = {"game_type": "casual", "core_mechanic": "tap", "theme": "cats",
               "input_method": "tap", "win_condition": "score", "difficulty": "easy"}
    with patch.object(engine._client, "is_enabled", return_value=True), patch(
        "src.engine.dialogue_engine.settings.CREATIVE_DESIGN_ENABLED", False
    ), patch("src.engine.dialogue_engine.require_prompt", return_value="JSON {slot_json_schema}"), patch.object(
        engine, "_complete_slot_request", new=AsyncMock(return_value=json.dumps(payload))
    ) as call:
        spec = asyncio.run(engine.parse_description_to_spec("tap cats"))
    assert spec.creative_design is None
    assert call.await_args.kwargs["max_tokens"] == 640


def evaluator():
    path = Path(__file__).resolve().parents[3] / "scripts" / "evaluate-generation.py"
    loader = importlib.util.spec_from_file_location("generation_evaluation", path)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    return module.summarize


def test_evaluation_keeps_unassessed_separate_from_success_and_cost():
    report = evaluator()([{"task_id": "a", "status": "succeeded", "playable": True, "cost": 2},
                          {"task_id": "b", "status": "failed", "failure_stage": "logic_generate"}])
    assert report["playable"]["confirmed_rate_all_tasks"] == .5
    assert report["playable"]["unknown"] == 1
    assert report["cost_per_confirmed_playable"] is None
    assert report["failure_stages"] == {"logic_generate": 1}


@pytest.mark.parametrize("rows", [[{"task_id": "a"}, {"task_id": "a"}],
                                  [{"task_id": "a", "playable": "true"}],
                                  [{"task_id": "a", "cost": float("nan")}]] )
def test_evaluation_rejects_ambiguous_or_invalid_records(rows):
    with pytest.raises(ValueError):
        evaluator()(rows)
