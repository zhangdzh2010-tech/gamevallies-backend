"""Unittest coverage for prompt-store integrations."""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import (
    CanvasConfig,
    CollisionConfig,
    CoreMechanic,
    GDD,
    GameEntity,
    GameRuntimeContract,
    GameRules,
    GameSpec,
    IterationType,
    NumericsConfig,
    PlatformConstraints,
    VisualStyle,
)
from src.engine.code_generator import CodeGenerator
from src.main import app

COMMON_CODEGEN_PROMPTS = {
    "prompt.intent_detail_template": (
        "CRITICAL INTENT DETAILS:\n"
        "- Core mechanic: {core_mechanic}\n"
        "- Theme: {theme}\n"
        "- Win condition: {win_condition}\n"
        "{reference_line}\n"
        "{special_rules_block}"
    ),
    "prompt.mobile_layout_guardrails": (
        "NON-NEGOTIABLE MOBILE LAYOUT RULES:\n"
        "- Treat {canvas_w}x{canvas_h} as a portrait reference playfield.\n"
        "- Compute a shared mobile UI scale from the short edge, for example `uiScale = Math.min(scaleX, scaleY)`.\n"
        "- Keep HUD text around {hud_font}px base size and clamp it to about 14-20px after scaling."
    ),
    "prompt.iteration_mobile_layout_guardrails": (
        "NON-NEGOTIABLE MOBILE LAYOUT RULES:\n"
        "- Keep the game portrait-first and fully playable on mobile touch screens."
    ),
    "prompt.generate_request_context_template": (
        "Original user request:\n{request_text}"
    ),
    "prompt.generate_alignment_reminder": (
        "Make sure the final game satisfies both the original user request and the structured design document above."
    ),
    "prompt.runtime_contract_summary": (
        "RUNTIME CONTRACT (NON-NEGOTIABLE):\n"
        "- Runtime profile: {runtime_profile}\n"
        "- Contract version: {contract_version}\n"
        "- Prompt bundle: {bundle_id}\n"
        "- Prompt layers: {layer_keys}\n"
        "- Required states: {required_states}\n"
        "- Required input modes: {input_modes}\n"
        "- Preferred gestures: {gestures}\n"
        "- Forbidden APIs: {forbidden_apis}\n"
        "- Orientation: {orientation}\n"
        "- UI scale mode: {ui_scale_mode}\n"
        "- HUD font clamp: {hud_min}-{hud_max}px\n"
        "- Title font clamp: {title_min}-{title_max}px"
    ),
}


class TestPromptIntegration(unittest.TestCase):
    def test_param_adjust_llm_fallback_uses_prompt_config(self):
        generator = CodeGenerator(llm_mode="real")

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.param_adjust":
                return "PARAM_PROMPT::{feedback}::{code}"
            if key == "prompt.code_gen_system":
                return "CODE_GEN_SYSTEM_FROM_DB"
            return COMMON_CODEGEN_PROMPTS.get(key, default)

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            generator._client,
            "complete",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            result = asyncio.run(
                generator._llm_iterate(
                    code="<!DOCTYPE html><html><body>old</body></html>",
                    feedback="make it faster",
                    conversation=[],
                    iter_type=IterationType.param_adjust,
                )
            )

        self.assertEqual(result, "<!DOCTYPE html><html></html>")
        kwargs = mock_complete.await_args.kwargs
        self.assertEqual(kwargs["system"], "CODE_GEN_SYSTEM_FROM_DB")
        self.assertIn(
            "PARAM_PROMPT::make it faster::<!DOCTYPE html><html><body>old</body></html>",
            kwargs["messages"][0]["content"],
        )

    def test_prompt_refresh_endpoint_requires_admin_token(self):
        with patch("src.api.endpoints.generate.settings.ADMIN_TOKEN", "unit-test-token"):
            with TestClient(app) as client:
                response = client.post("/api/v1/ai/prompts/refresh")
        self.assertEqual(response.status_code, 401)

    def test_prompt_refresh_endpoint_reloads_cache(self):
        with patch(
            "src.api.endpoints.generate.refresh_prompt_cache",
            return_value=10,
        ) as mock_refresh, patch(
            "src.api.endpoints.generate.cached_prompt_count",
            return_value=10,
        ), patch(
            "src.api.endpoints.generate.settings.ADMIN_TOKEN",
            "unit-test-token",
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/ai/prompts/refresh",
                    headers={"x-admin-token": "unit-test-token"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["prompt_count"], 10)
        mock_refresh.assert_called_once_with(raise_on_error=True)

    def test_game_design_prompt_supports_extended_db_placeholders(self):
        generator = CodeGenerator(llm_mode="real")

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.game_design_template":
                return (
                    "游戏类型：{game_type}\n"
                    "核心玩法：{core_mechanic}\n"
                    "背景色：{color_bg}\n"
                    "障碍物：\n{entities_yaml}\n"
                    "操控：\n{input_map_yaml}\n"
                    "特殊规则：\n{special_rules_list}"
                )
            if key == "prompt.code_gen_system":
                return "CODE_GEN_SYSTEM_FROM_DB"
            return COMMON_CODEGEN_PROMPTS.get(key, default)

        spec = GameSpec(
            game_type="dodge",
            source_description="做一个太空躲避游戏",
            intent_summary="控制飞船躲避陨石并穿过黑洞得分",
            core_mechanics=[CoreMechanic(type="dodge", input="touch")],
            entities=[
                GameEntity(name="player", role="player", shape="triangle", color="#6366f1"),
                GameEntity(name="meteor", role="obstacle", shape="square", color="#f43f5e"),
                GameEntity(name="star", role="collectible", shape="diamond", color="#22c55e"),
            ],
            rules=GameRules(win_condition="survive", lose_condition="lives_zero", lives=3),
            visual_style=VisualStyle(
                theme="space",
                art_style="neon",
                palette=["#0a0a2e", "#6366f1", "#22c55e", "#f43f5e", "#ffffff"],
                effects=["glow"],
            ),
            special_rules=["avoid black holes", "collect stars for combo"],
            reference_game="星际闪避",
            platform_constraints=PlatformConstraints(platform="wechat_webview", input_mode="touch_only"),
        )
        gdd = GDD(
            canvas=CanvasConfig(width=420, height=600, dpr_adaptive=True, target_fps=60),
            numerics=NumericsConfig(
                player_speed=8.0,
                base_obstacle_speed=3.0,
                speed_formula="base + elapsed_s * 0.02",
                spawn_interval_ms=900,
                score_per_second=1,
                score_per_collect=10,
                expected_survival_s=60,
            ),
            collision=CollisionConfig(method="AABB", hitbox_ratio=0.8, on_hit="lives_minus_1"),
            input_map={"touchmove": "player_follow_x", "touchend": "player_stop"},
            ui_layout={"score": {"x": 16, "y": 36, "font": "bold 18px Arial"}},
        )

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            generator._client,
            "complete",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            result = asyncio.run(generator._llm_generate(spec, gdd, description="做一个太空躲避游戏"))

        self.assertEqual(result, "<!DOCTYPE html><html></html>")
        kwargs = mock_complete.await_args.kwargs
        message = kwargs["messages"][0]["content"]
        self.assertEqual(kwargs["system"], "CODE_GEN_SYSTEM_FROM_DB")
        self.assertIn("核心玩法：控制飞船躲避陨石并穿过黑洞得分", message)
        self.assertIn("背景色：#0a0a2e", message)
        self.assertIn("障碍物：", message)
        self.assertIn("avoid black holes", message)
        self.assertIn("Reference game: 星际闪避", message)
        self.assertNotIn("{core_mechanic}", message)
        self.assertNotIn("{color_bg}", message)

    def test_game_design_prompt_always_appends_critical_intent_block(self):
        generator = CodeGenerator(llm_mode="real")

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.game_design_template":
                return "游戏类型：{game_type}\n主题：{theme}"
            if key == "prompt.code_gen_system":
                return "CODE_GEN_SYSTEM_FROM_DB"
            return COMMON_CODEGEN_PROMPTS.get(key, default)

        spec = GameSpec(
            game_type="runner",
            source_description="做一个动物园逃脱跑酷游戏",
            intent_summary="在动物园里奔跑，躲开管理员并救出小动物",
            core_mechanics=[CoreMechanic(type="runner", input="swipe")],
            rules=GameRules(win_condition="rescue all animals", lose_condition="caught", lives=3),
            visual_style=VisualStyle(theme="zoo", art_style="cartoon"),
            special_rules=["animals follow the player after rescue"],
            reference_game="Temple Run",
            platform_constraints=PlatformConstraints(platform="wechat_webview", input_mode="swipe"),
        )
        gdd = GDD(
            canvas=CanvasConfig(width=420, height=600, dpr_adaptive=True, target_fps=60),
            numerics=NumericsConfig(),
            collision=CollisionConfig(),
            input_map={"touchmove": "lane_switch"},
            ui_layout={"score": {"x": 16, "y": 36, "font": "bold 18px Arial"}},
        )

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            generator._client,
            "complete",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            asyncio.run(generator._llm_generate(spec, gdd, description=""))

        message = mock_complete.await_args.kwargs["messages"][0]["content"]
        self.assertIn("CRITICAL INTENT DETAILS:", message)
        self.assertIn("Core mechanic: 在动物园里奔跑，躲开管理员并救出小动物", message)
        self.assertIn("Reference game: Temple Run", message)
        self.assertIn("animals follow the player after rescue", message)


    def test_game_design_prompt_always_appends_mobile_layout_guardrails(self):
        generator = CodeGenerator(llm_mode="real")

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.game_design_template":
                return "Game Type: {game_type}\nTheme: {theme}"
            if key == "prompt.code_gen_system":
                return "CODE_GEN_SYSTEM_FROM_DB"
            return COMMON_CODEGEN_PROMPTS.get(key, default)

        spec = GameSpec(
            game_type="runner",
            source_description="做一个竖屏跑酷小游戏",
            rules=GameRules(win_condition="reach the finish line", lose_condition="hit obstacles", lives=3),
            visual_style=VisualStyle(theme="forest", art_style="cartoon"),
            platform_constraints=PlatformConstraints(platform="wechat_webview", input_mode="touch_only"),
        )
        gdd = GDD(
            canvas=CanvasConfig(width=360, height=640, dpr_adaptive=True, target_fps=60),
            numerics=NumericsConfig(),
            collision=CollisionConfig(),
            input_map={"touchmove": "lane_switch"},
            ui_layout={"score": {"x": 16, "y": 32, "font": "bold 16px Arial"}},
        )

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            generator._client,
            "complete",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            asyncio.run(generator._llm_generate(spec, gdd, description="做一个竖屏跑酷小游戏"))

        message = mock_complete.await_args.kwargs["messages"][0]["content"]
        self.assertIn("NON-NEGOTIABLE MOBILE LAYOUT RULES:", message)
        self.assertIn("portrait reference playfield", message)
        self.assertIn("Math.min(scaleX, scaleY)", message)
        self.assertIn("14-20px", message)

    def test_iterate_passes_current_code_using_code_parameter(self):
        generator = CodeGenerator(llm_mode="real")

        with patch.object(
            generator,
            "_classify_iteration",
            new=AsyncMock(return_value=IterationType.element_change),
        ), patch.object(
            generator,
            "_llm_iterate",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_iterate:
            html, iter_type = asyncio.run(
                generator.iterate(
                    current_code="<!DOCTYPE html><html><body>old</body></html>",
                    feedback="add one more enemy",
                    conversation=[{"role": "user", "content": "make it harder"}],
                )
            )

        self.assertEqual(iter_type, IterationType.element_change)
        self.assertEqual(html, "<!DOCTYPE html><html></html>")
        kwargs = mock_iterate.await_args.kwargs
        self.assertEqual(kwargs["code"], "<!DOCTYPE html><html><body>old</body></html>")
        self.assertNotIn("current_code", kwargs)

    def test_generate_uses_resolved_prompt_bundle_layers(self):
        generator = CodeGenerator(llm_mode="real")

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.game_design_template":
                return "Game Type: {game_type}\nTheme: {theme}"
            if key == "prompt.platform_standard":
                return "PLATFORM_FROM_DB"
            if key == "prompt.code_gen_system":
                return "LEGACY_SYSTEM_FROM_DB"
            return COMMON_CODEGEN_PROMPTS.get(key, default)

        spec = GameSpec(
            game_type="runner",
            source_description="make a portrait runner",
            rules=GameRules(win_condition="finish", lose_condition="hit obstacle", lives=3),
            visual_style=VisualStyle(theme="forest", art_style="cartoon"),
            platform_constraints=PlatformConstraints(platform="wechat_webview", input_mode="touch_only"),
        )
        gdd = GDD(
            canvas=CanvasConfig(width=360, height=640, dpr_adaptive=True, target_fps=60),
            numerics=NumericsConfig(),
            collision=CollisionConfig(),
            input_map={"touchstart": "jump"},
            ui_layout={"score": {"x": 16, "y": 32, "font": "bold 16px Arial"}},
        )
        prompt_bundle_snapshot = {
            "layers": {
                "resolved_prompts": {
                    "locked_contract": {"content": "LOCKED_CONTRACT_FROM_BUNDLE"},
                    "product_policy": {"content": "PRODUCT_POLICY_FROM_BUNDLE"},
                    "profile_few_shot": {"content": "PROFILE_FEW_SHOT_FROM_BUNDLE"},
                    "logic_generate": {"content": "LOGIC_GENERATE_FROM_BUNDLE"},
                }
            }
        }

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            generator._client,
            "complete",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            asyncio.run(
                generator._llm_generate(
                    spec,
                    gdd,
                    description="make a portrait runner",
                    runtime_contract=GameRuntimeContract(),
                    runtime_profile="lane_runner",
                    prompt_bundle_snapshot=prompt_bundle_snapshot,
                )
            )

        kwargs = mock_complete.await_args.kwargs
        self.assertIn("LOCKED_CONTRACT_FROM_BUNDLE", kwargs["system"])
        self.assertIn("PRODUCT_POLICY_FROM_BUNDLE", kwargs["system"])
        self.assertIn("LEGACY_SYSTEM_FROM_DB", kwargs["system"])
        message = kwargs["messages"][0]["content"]
        self.assertIn("LOGIC_GENERATE_FROM_BUNDLE", message)
        self.assertIn("PROFILE_FEW_SHOT_FROM_BUNDLE", message)
        self.assertIn("PLATFORM_FROM_DB", message)

    def test_iterate_uses_resolved_prompt_bundle_layers(self):
        generator = CodeGenerator(llm_mode="real")

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.element_change":
                return "ITERATE_PROMPT::{feedback}::{code}"
            if key == "prompt.code_gen_system":
                return "LEGACY_SYSTEM_FROM_DB"
            return COMMON_CODEGEN_PROMPTS.get(key, default)

        prompt_bundle_snapshot = {
            "layers": {
                "resolved_prompts": {
                    "locked_contract": {"content": "LOCKED_CONTRACT_FROM_BUNDLE"},
                    "product_policy": {"content": "PRODUCT_POLICY_FROM_BUNDLE"},
                    "profile_few_shot": {"content": "PROFILE_FEW_SHOT_FROM_BUNDLE"},
                    "logic_generate": {"content": "LOGIC_GENERATE_FROM_BUNDLE"},
                }
            }
        }

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            generator._client,
            "complete",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            asyncio.run(
                generator._llm_iterate(
                    code="<!DOCTYPE html><html><body>old</body></html>",
                    feedback="add coins",
                    conversation=[],
                    iter_type=IterationType.element_change,
                    runtime_contract=GameRuntimeContract(),
                    runtime_profile="lane_runner",
                    prompt_bundle_snapshot=prompt_bundle_snapshot,
                )
            )

        kwargs = mock_complete.await_args.kwargs
        self.assertIn("LOCKED_CONTRACT_FROM_BUNDLE", kwargs["system"])
        self.assertIn("PRODUCT_POLICY_FROM_BUNDLE", kwargs["system"])
        self.assertIn("LEGACY_SYSTEM_FROM_DB", kwargs["system"])
        message = kwargs["messages"][0]["content"]
        self.assertIn("LOGIC_GENERATE_FROM_BUNDLE", message)
        self.assertIn("PROFILE_FEW_SHOT_FROM_BUNDLE", message)
        self.assertIn("ITERATE_PROMPT::add coins::<!DOCTYPE html><html><body>old</body></html>", message)


if __name__ == "__main__":
    unittest.main()
