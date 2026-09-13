"""Game-path yield telemetry: every arcade generation emits route + family."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts"))

from src.api.models import GameSpec
from src.engine.game_path_telemetry import (
    GAME_ROUTE_REASON,
    GAME_TEMPLATE_ROUTE,
    family_id_for_runtime_profile,
    game_path_yield_fields,
    mechanic_id_for_profile,
    merge_game_path_telemetry,
)
from src.engine.interactive_router import route_interactive_template
from src.engine.pipeline_v2_runner import V2PipelineRunner
from run_generation_yield_batch import extract_quality_outcome, ledger_row


YIELD_GAME_BRIEFS = (
    (
        "simple_dodge_cn",
        "casual",
        "做一个太空躲避手机竖屏小游戏。玩家点击开始后，通过左右滑动躲开陨石并收集星星，坚持 30 秒获胜。",
    ),
    (
        "snake_classic_cn",
        "casual",
        "作品类型：游戏。制作键盘控制桌面贪吃蛇，含开始、暂停、食物、得分、碰撞结束和重开。",
    ),
    (
        "memory_cards_cn",
        "puzzle",
        "作品类型：游戏。制作精致的4对卡片记忆配对手机竖屏小游戏，含开始、翻牌、配对、步数、胜利提示和重新开始。",
    ),
    (
        "grid_puzzle_en",
        "puzzle",
        "Create a small portrait mobile grid puzzle. The player taps tiles to connect matching runes and clear the board.",
    ),
)
KNOWN_FAMILY_PREFIXES = (
    "casual_arcade",
    "casual_action",
    "casual_lane",
    "puzzle_grid",
    "tap_challenge",
)


class GamePathTelemetryFields(unittest.TestCase):
    def test_yield_fields_always_include_route_and_family(self):
        spec = GameSpec(
            game_type="casual",
            artifact_kind="game",
            source_description="dodge meteors",
            core_mechanics=[{"type": "dodge"}],
        )
        fields = game_path_yield_fields(runtime_profile="casual_arcade_orbit", spec=spec)
        self.assertEqual(fields["template_route"], GAME_TEMPLATE_ROUTE)
        self.assertEqual(fields["template_route"], "MISS")
        self.assertEqual(fields["family_id"], "casual_arcade_orbit")
        self.assertEqual(fields["recipe_id"], "orbit_control")
        self.assertEqual(fields["mechanic_id"], "dodge")
        self.assertEqual(fields["route_reason"], GAME_ROUTE_REASON)
        self.assertFalse(fields["short_path"])
        self.assertEqual(fields["artifact_kind"], "game")

    def test_family_id_is_canonical_runtime_profile(self):
        self.assertEqual(family_id_for_runtime_profile("casual_arcade_orbit"), "casual_arcade_orbit")
        self.assertEqual(mechanic_id_for_profile("puzzle_grid_match"), "grid_completion")
        self.assertEqual(mechanic_id_for_profile("puzzle_grid_route"), "route_completion")
        self.assertEqual(mechanic_id_for_profile("casual_lane_dash"), "lane_survival")

    def test_merge_does_not_overwrite_science_hit(self):
        labeled = merge_game_path_telemetry(
            {
                "template_route": "HIT",
                "family_id": "time_integrator_1d",
                "recipe_id": "pendulum",
                "seed_worthy": True,
            },
            runtime_profile="casual_arcade",
            spec=GameSpec(game_type="casual"),
        )
        self.assertEqual(labeled["template_route"], "HIT")
        self.assertEqual(labeled["family_id"], "time_integrator_1d")
        self.assertEqual(labeled["recipe_id"], "pendulum")
        self.assertTrue(labeled["seed_worthy"])

    def test_merge_labels_unlabeled_game_breakdown(self):
        labeled = merge_game_path_telemetry(
            {"review_fun_score": 7.2, "runtime_profile": "puzzle_grid_match"},
            runtime_profile="puzzle_grid_match",
            spec=GameSpec(game_type="puzzle", artifact_kind="game"),
        )
        self.assertEqual(labeled["template_route"], "MISS")
        self.assertEqual(labeled["family_id"], "puzzle_grid_match")
        self.assertEqual(labeled["recipe_id"], "grid_completion")
        self.assertEqual(labeled["review_fun_score"], 7.2)

    def test_science_router_still_hits_known_recipes(self):
        decision = route_interactive_template(
            "science",
            "作品类型：科学演示。制作小角度理想单摆演示，可调摆长L和重力g，周期T=2π√(L/g)。",
        )
        self.assertEqual(decision.route, "HIT")
        self.assertEqual(decision.family_id, "time_integrator_1d")
        self.assertEqual(decision.recipe_id, "pendulum")


class YieldGameFamilyMapping(unittest.TestCase):
    def test_yield_game_briefs_select_stable_family_and_emit_route(self):
        runner = V2PipelineRunner()
        for name, game_type, brief in YIELD_GAME_BRIEFS:
            spec = GameSpec(game_type=game_type, artifact_kind="game", source_description=brief)
            profile = runner._select_runtime_profile(spec, None)
            fields = game_path_yield_fields(runtime_profile=profile, spec=spec)
            self.assertTrue(
                any(profile == prefix or profile.startswith(prefix + "_") or profile.startswith(prefix)
                    for prefix in KNOWN_FAMILY_PREFIXES),
                (name, profile),
            )
            self.assertEqual(fields["template_route"], "MISS", name)
            self.assertEqual(fields["family_id"], profile, name)
            self.assertTrue(fields["recipe_id"], name)
            self.assertEqual(fields["route_reason"], GAME_ROUTE_REASON, name)


class YieldExtractionHardening(unittest.TestCase):
    def test_extracts_nested_task_meta_and_camel_case(self):
        outcome = extract_quality_outcome({
            "diagnostics": {
                "task": {
                    "data": {
                        "resultSummary": {
                            "qualityBreakdown": {"review_fun_score": 7.1, "final_score": 7.0},
                        },
                        "task_meta": {
                            "template_route": "MISS",
                            "template_family": "casual_arcade_orbit",
                            "template_recipe": "orbit_control",
                        },
                    }
                }
            }
        })
        self.assertEqual(outcome["template_route"], "MISS")
        self.assertEqual(outcome["family_id"], "casual_arcade_orbit")
        self.assertEqual(outcome["recipe_id"], "orbit_control")

    def test_extracts_generation_efficiency_and_runtime_profile_fallback(self):
        outcome = extract_quality_outcome({
            "qualityBreakdown": {
                "runtime_profile": "puzzle_grid_route",
                "generation_efficiency": {
                    "templateRoute": "MISS",
                    "recipeId": "route_completion",
                },
            }
        })
        self.assertEqual(outcome["template_route"], "MISS")
        self.assertEqual(outcome["family_id"], "puzzle_grid_route")
        self.assertEqual(outcome["recipe_id"], "route_completion")

    def test_science_efficiency_fields_still_win_over_runtime_profile(self):
        outcome = extract_quality_outcome({
            "qualityBreakdown": {
                "artifact_kind": "science",
                "runtime_profile": "interactive_experience",
                "template_route": "HIT",
                "family_id": "compartment_flow",
                "recipe_id": "population",
            }
        })
        self.assertEqual(outcome["template_route"], "HIT")
        self.assertEqual(outcome["family_id"], "compartment_flow")
        self.assertEqual(outcome["recipe_id"], "population")

    def test_ledger_row_records_game_miss_route(self):
        outcome = extract_quality_outcome({
            "qualityBreakdown": {
                "template_route": "MISS",
                "family_id": "casual_arcade_orbit",
                "recipe_id": "orbit_control",
                "route_reason": GAME_ROUTE_REASON,
                "seed_worthy": True,
            }
        })
        row = ledger_row(
            run_id="r",
            run_index=1,
            case={"name": "simple_dodge_cn", "title": "Dodge", "artifact_kind": "game"},
            result={
                "finalStatus": "succeeded",
                "qualityBreakdown": outcome["qualityBreakdown"],
                "template_route": outcome["template_route"],
                "family_id": outcome["family_id"],
                "recipe_id": outcome["recipe_id"],
            },
            base_url="https://example.test",
        )
        self.assertEqual(row["kind"], "game")
        self.assertEqual(row["template_route"], "MISS")
        self.assertEqual(row["family_id"], "casual_arcade_orbit")
        self.assertEqual(row["recipe_id"], "orbit_control")


if __name__ == "__main__":
    unittest.main()
