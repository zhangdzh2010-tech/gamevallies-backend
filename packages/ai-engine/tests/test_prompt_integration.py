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
from src.engine.game_designer import GameDesigner
from src.engine.section_patch import ensure_structured_section_markers
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
    "prompt.visual_quality_bar": (
        "VISUAL / CHARACTER QUALITY BAR:\n"
        "- Presentation must feel polished and premium for mobile H5: cohesive {theme} world-building, {art_style} direction, readable depth, and strong moment-to-moment feedback.\n"
        "{palette_line}\n"
        "- Character quality must feel intentional and premium."
    ),
    "prompt.mobile_layout_guardrails": (
        "NON-NEGOTIABLE MOBILE LAYOUT RULES:\n"
        "- Treat {canvas_w}x{canvas_h} as a portrait reference playfield.\n"
        "- Compute a shared mobile UI scale from the short edge, for example `uiScale = Math.min(scaleX, scaleY)`.\n"
        "- Keep HUD text around {hud_font}px base size and clamp it to about 14-20px after scaling."
    ),
    "prompt.generate_request_context_template": (
        "Original user request:\n{request_text}"
    ),
    "prompt.generate_alignment_reminder": (
        "Make sure the final game satisfies both the original user request and the structured design document above."
    ),
    "prompt.runtime_contract_summary": (
        "RUNTIME CONTRACT (MUST STAY FUNCTIONAL):\n"
        "- Runtime profile: {runtime_profile} (contract v{contract_version})\n"
        "- Core state flow must support {required_states} with a restart path back into active play.\n"
        "- Input must work through {input_modes}; expected gestures: {gestures}.\n"
        "- Forbidden APIs: {forbidden_apis}.\n"
        "- Mobile layout: {orientation}, {ui_scale_mode} scaling, HUD {hud_min}-{hud_max}px, title {title_min}-{title_max}px.\n"
        "- Platform target: mobile H5 browser / WebView with a single main canvas.\n"
        "- Prevent accidental page scrolling during play and keep gameplay local with no external network or asset requests.\n"
        "- Accepted terminal/completion state aliases: {terminal_state_aliases}\n"
        "- Prompt bundle: {bundle_id}\n"
        "- Prompt layers: {layer_keys}\n"
        "- The final code must respect every contract rule explicitly, not implicitly."
    ),
    "prompt.generation_tier_safe": (
        "GENERATION TIER: SAFE\n"
        "- Prioritize stability, clarity, and QA-friendly structure."
    ),
    "prompt.generation_tier_standard": (
        "GENERATION TIER: STANDARD\n"
        "- Balance stability with delight.\n"
        "- Build a more polished and distinctive result than the minimal safe baseline."
    ),
    "prompt.generation_tier_showcase": (
        "GENERATION TIER: SHOWCASE\n"
        "- Aim for a premium-feeling result with stronger presentation and a more distinctive loop."
    ),
    "prompt.code_gen_system_standard": (
        "STANDARD OVERRIDE:\n"
        "- Favor clearer progression, stronger feedback, and a more intentional presentation."
    ),
    "prompt.code_gen_system_showcase": (
        "SHOWCASE OVERRIDE:\n"
        "- A premium-feeling result is preferred over the smallest generic implementation."
    ),
}


class TestPromptIntegration(unittest.TestCase):
    def test_preflight_safety_block_requires_safe_grid_accessor_for_puzzle_profiles(self):
        generator = CodeGenerator(llm_mode="real")
        spec = GameSpec(
            game_type="puzzle",
            source_description="build a fruit merge puzzle",
            core_mechanics=[CoreMechanic(type="merge", input="tap")],
            rules=GameRules(win_condition="clear_board", lose_condition="board_full", lives=1),
            visual_style=VisualStyle(theme="fruit", art_style="flat"),
        )
        runtime_contract = GameRuntimeContract(runtime_profile="puzzle_grid_merge")

        block = generator._build_preflight_safety_block(
            spec,
            runtime_contract,
            "puzzle_grid_merge",
        )

        self.assertIn("function getCell(grid, row, col)", block)
        self.assertIn("zero raw `grid[row][col].*` reads", block)
        self.assertIn("const cell = getCell(grid, row, col); if (!cell) continue;", block)
        self.assertIn("`cell.fruit`", block)
        self.assertIn("XMLHttpRequest", block)
        self.assertIn("WebSocket", block)

    def test_full_generation_keeps_provider_failover_disabled_but_allows_single_cancel_retry(self):
        generator = CodeGenerator(llm_mode="real")
        spec = GameSpec(
            game_type="casual",
            source_description="make a simple dodge game",
            core_mechanics=[CoreMechanic(type="tap_dodge", input="tap")],
            rules=GameRules(win_condition="survive", lose_condition="hit", lives=3),
            visual_style=VisualStyle(theme="arcade", art_style="flat"),
        )
        gdd = GDD()
        runtime_contract = GameRuntimeContract(runtime_profile="casual_arcade")

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.code_gen_system":
                return "CODE_GEN_SYSTEM_FROM_DB"
            if key == "prompt.game_design_template":
                return "GAME DESIGN DOCUMENT:\n- Core mechanic: {core_mechanic}\n- Theme: {theme}"
            if key == "prompt.logic_generate_policy":
                return "LOGIC GENERATE POLICY"
            if key == "prompt.implementation_budget":
                return "IMPLEMENTATION BUDGET"
            if key == "prompt.critical_intent_block":
                return "CRITICAL INTENT DETAILS:\n- Core mechanic: {core_mechanic}"
            return COMMON_CODEGEN_PROMPTS.get(key, default)

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            generator._client,
            "complete_with_truncation_retry",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            asyncio.run(
                generator._llm_generate(
                    spec,
                    gdd,
                    description="make a simple dodge game",
                    runtime_contract=runtime_contract,
                    runtime_profile="casual_arcade",
                    prompt_bundle_snapshot={},
                    budget_override="complex",
                )
            )

        kwargs = mock_complete.await_args.kwargs
        self.assertIs(kwargs["allow_provider_fallback"], True)
        self.assertEqual(kwargs["hedge_provider_fallback_after_s"], 45)
        self.assertEqual(kwargs["timeout_retry_attempts"], 0)
        self.assertEqual(kwargs["provider_retry_attempts"], 1)
        self.assertIs(kwargs["provider_retry_on_timeout_errors"], False)
        self.assertEqual(kwargs["truncation_retry_attempts"], 2)
        self.assertIn("OUTPUT SIZE CONSTRAINT", kwargs["truncation_retry_guidance"])
        self.assertIn("XMLHttpRequest", kwargs["truncation_retry_guidance"])

    def test_truncated_full_generation_continues_partial_html_instead_of_hard_fail(self):
        from src.services.llm_client import LLMResponseTruncatedError

        generator = CodeGenerator(llm_mode="real")
        spec = GameSpec(
            game_type="casual",
            source_description="make a simple dodge game",
            core_mechanics=[CoreMechanic(type="tap_dodge", input="tap")],
            rules=GameRules(win_condition="survive", lose_condition="hit", lives=3),
            visual_style=VisualStyle(theme="arcade", art_style="flat"),
        )
        gdd = GDD()
        runtime_contract = GameRuntimeContract(runtime_profile="casual_arcade")
        prefix = "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>const x=1;"
        suffix = "</script></body></html>"

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.code_gen_system":
                return "CODE_GEN_SYSTEM_FROM_DB"
            if key == "prompt.game_design_template":
                return "GAME DESIGN DOCUMENT:\n- Core mechanic: {core_mechanic}\n- Theme: {theme}"
            if key == "prompt.logic_generate_policy":
                return "LOGIC GENERATE POLICY"
            if key == "prompt.implementation_budget":
                return "IMPLEMENTATION BUDGET"
            if key == "prompt.critical_intent_block":
                return "CRITICAL INTENT DETAILS:\n- Core mechanic: {core_mechanic}"
            return COMMON_CODEGEN_PROMPTS.get(key, default)

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            generator._client,
            "complete_with_truncation_retry",
            new=AsyncMock(side_effect=[
                LLMResponseTruncatedError(
                    "OpenAI-compatible response hit the output length limit and may be truncated",
                    partial_text=prefix,
                    stop_reason="length",
                ),
                suffix,
            ]),
        ) as mock_complete:
            html, _ = asyncio.run(
                generator._llm_generate(
                    spec,
                    gdd,
                    description="make a simple dodge game",
                    runtime_contract=runtime_contract,
                    runtime_profile="casual_arcade",
                    prompt_bundle_snapshot={},
                    budget_override="simple",
                )
            )

        self.assertIn("</html>", html)
        self.assertIn("gameCanvas", html)
        self.assertEqual(mock_complete.await_count, 2)
        self.assertEqual(mock_complete.await_args_list[1].kwargs["step_key"], "code_generate.continue")

    def test_generation_timeout_budget_follows_single_admin_budget(self):
        simple_spec = GameSpec(
            game_type="casual",
            source_description="make a simple dodge game",
            core_mechanics=[CoreMechanic(type="tap_dodge", input="tap")],
            rules=GameRules(win_condition="survive", lose_condition="hit", lives=3),
            visual_style=VisualStyle(theme="arcade", art_style="flat"),
            entities=[
                GameEntity(name="player", role="player", shape="circle"),
                GameEntity(name="enemy", role="obstacle", shape="square"),
            ],
        )
        standard_spec = GameSpec(
            game_type="casual",
            source_description="build a lively office prank runner with a few obstacles and a clear restart loop",
            core_mechanics=[CoreMechanic(type="runner", input="swipe")],
            rules=GameRules(win_condition="survive the shift", lose_condition="caught", lives=3),
            visual_style=VisualStyle(theme="office", art_style="flat"),
            entities=[
                GameEntity(name="worker", role="player", shape="circle"),
                GameEntity(name="manager", role="enemy", shape="square"),
                GameEntity(name="paper", role="collectible", shape="diamond"),
                GameEntity(name="cart", role="obstacle", shape="rectangle"),
            ],
            special_rules=[
                "Keep the first interaction immediate and visible.",
                "Add a short booster route that changes obstacle timing.",
            ],
        )
        complex_spec = GameSpec(
            game_type="educational",
            source_description="build a classroom runner with three waves, mini boss, quizzes, and route planning",
            core_mechanics=[
                CoreMechanic(type="runner", input="swipe"),
                CoreMechanic(type="quiz", input="tap"),
            ],
            rules=GameRules(win_condition="finish the lesson", lose_condition="energy_zero", lives=3),
            visual_style=VisualStyle(theme="classroom", art_style="playful"),
            entities=[
                GameEntity(name="student", role="player", shape="circle"),
                GameEntity(name="question_gate", role="obstacle", shape="square"),
                GameEntity(name="energy_orb", role="collectible", shape="diamond"),
                GameEntity(name="teacher", role="boss", shape="rectangle"),
                GameEntity(name="robot", role="enemy", shape="triangle"),
            ],
            special_rules=[
                "Three waves before the mini boss appears",
                "Energy is used to attack and defend",
                "Quiz answers change the route plan",
                "Escort classmates between stations",
            ],
        )

        self.assertEqual(CodeGenerator._generation_request_timeout_budget_s(simple_spec), 240)
        self.assertEqual(CodeGenerator._generation_overall_timeout_budget_s(simple_spec), 240)
        self.assertIsNone(CodeGenerator._generation_provider_hedge_delay_s(simple_spec))
        self.assertEqual(CodeGenerator._generation_request_timeout_budget_s(standard_spec), 240)
        self.assertEqual(CodeGenerator._generation_overall_timeout_budget_s(standard_spec), 240)
        self.assertEqual(CodeGenerator._generation_provider_hedge_delay_s(standard_spec), 45)
        self.assertEqual(CodeGenerator._generation_request_timeout_budget_s(complex_spec), 240)
        self.assertEqual(CodeGenerator._generation_overall_timeout_budget_s(complex_spec), 240)
        self.assertEqual(CodeGenerator._generation_provider_hedge_delay_s(complex_spec), 45)

    def test_iterate_step_timeout_overrides_use_step_specific_keys(self):
        def fake_get_timeout_int(key: str, default: int, *, min_value=None, max_value=None):
            overrides = {
                "timeout.ai_engine.iterate.classify_request_s": 35,
                "timeout.ai_engine.iterate.classify_overall_s": 75,
                "timeout.ai_engine.iterate.element_change_request_s": 150,
                "timeout.ai_engine.iterate.element_change_overall_s": 300,
            }
            value = overrides.get(key, default)
            if min_value is not None:
                value = max(min_value, value)
            if max_value is not None:
                value = min(max_value, value)
            return value

        with patch("src.engine.code_generator.get_timeout_int", side_effect=fake_get_timeout_int):
            self.assertEqual(
                CodeGenerator._resolve_step_request_timeout_s("iterate.classify", default_timeout_s=30),
                35,
            )
            self.assertEqual(
                CodeGenerator._resolve_step_overall_timeout_s(
                    "iterate.classify",
                    request_timeout_s=35,
                    default_timeout_s=60,
                ),
                75,
            )
            self.assertEqual(
                CodeGenerator._resolve_step_request_timeout_s("iterate.element_change", default_timeout_s=120),
                150,
            )
            self.assertEqual(
                CodeGenerator._resolve_step_overall_timeout_s(
                    "iterate.element_change",
                    request_timeout_s=150,
                    default_timeout_s=240,
                ),
                300,
            )

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
            "complete_with_truncation_retry",
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

        self.assertEqual(result, "<!DOCTYPE html><html><body>old</body></html>")
        kwargs = mock_complete.await_args.kwargs
        self.assertIn("CODE_GEN_SYSTEM_FROM_DB", kwargs["system"])
        self.assertIn(
            "PARAM_PROMPT::make it faster::CURRENT PATCHABLE SECTIONS:",
            kwargs["messages"][0]["content"],
        )
        self.assertIn("PATCH-FIRST ITERATION OUTPUT CONTRACT", kwargs["messages"][0]["content"])
        self.assertIn("=== SECTION:SCRIPT START ===", kwargs["messages"][0]["content"])

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
            game_type="casual",
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
            "complete_with_truncation_retry",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            result = asyncio.run(generator._llm_generate(spec, gdd, description="做一个太空躲避游戏"))

        self.assertEqual(result[0], "<!DOCTYPE html><html></html>")
        self.assertIsNone(result[1])
        kwargs = mock_complete.await_args.kwargs
        message = kwargs["messages"][0]["content"]
        self.assertIn("CODE_GEN_SYSTEM_FROM_DB", kwargs["system"])
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
            game_type="casual",
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
            "complete_with_truncation_retry",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            asyncio.run(generator._llm_generate(spec, gdd, description=""))

        message = mock_complete.await_args.kwargs["messages"][0]["content"]
        self.assertIn("CRITICAL INTENT DETAILS:", message)
        self.assertIn("Core mechanic: 在动物园里奔跑，躲开管理员并救出小动物", message)
        self.assertIn("Reference game: Temple Run", message)
        self.assertIn("animals follow the player after rescue", message)
        self.assertIn("Original brief anchor:", message)

    def test_generate_dedupes_reference_and_special_rules_when_gdd_already_includes_them(self):
        generator = CodeGenerator(llm_mode="real")

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.game_design_template":
                return (
                    "GAME DESIGN DOCUMENT\n"
                    "Game Type: {game_type}\n"
                    "Reference Game: {reference_game}\n"
                    "Special Rules:\n{special_rules_list}"
                )
            if key == "prompt.code_gen_system":
                return "SYSTEM"
            if key == "prompt.platform_standard":
                return "PLATFORM"
            return COMMON_CODEGEN_PROMPTS.get(key, default)

        spec = GameSpec(
            game_type="casual",
            source_description="build a rescue runner",
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
            "complete_with_truncation_retry",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            asyncio.run(generator._llm_generate(spec, gdd, description=spec.source_description))

        message = mock_complete.await_args.kwargs["messages"][0]["content"]
        self.assertEqual(message.count("Reference Game: Temple Run"), 1)
        self.assertNotIn("- Reference game: Temple Run", message)
        self.assertEqual(message.count("animals follow the player after rescue"), 1)
        self.assertNotIn("Must preserve these special rules", message)


    def test_generate_omits_empty_design_program_and_request_scaffold_for_compact_prompt(self):
        generator = CodeGenerator(llm_mode="real")

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.game_design_template":
                return "Game Type: {game_type}\nTheme: {theme}"
            if key == "prompt.generate_request_context_template":
                return "Original user request:\n{request_text}"
            if key == "prompt.generate_alignment_reminder":
                return "ALIGN"
            if key == "prompt.code_gen_system":
                return "SYSTEM"
            if key == "prompt.platform_standard":
                return "PLATFORM"
            return COMMON_CODEGEN_PROMPTS.get(key, default)

        spec = GameSpec(
            game_type="casual",
            source_description="make a tap dodger",
            core_mechanics=[CoreMechanic(type="dodge", input="tap")],
            rules=GameRules(win_condition="survive", lose_condition="hit", lives=3),
            visual_style=VisualStyle(theme="arcade", art_style="minimal"),
            platform_constraints=PlatformConstraints(platform="wechat_webview", input_mode="touch"),
        )
        gdd = GDD(
            canvas=CanvasConfig(width=360, height=640, dpr_adaptive=True, target_fps=60),
            numerics=NumericsConfig(),
            collision=CollisionConfig(),
            input_map={"touchstart": "start"},
            ui_layout={},
        )

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            generator._client,
            "complete_with_truncation_retry",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            asyncio.run(generator._llm_generate(spec, gdd, description=spec.source_description))

        message = mock_complete.await_args.kwargs["messages"][0]["content"]
        self.assertNotIn("DESIGN PROGRAM (HIGH PRIORITY):", message)
        self.assertNotIn("Original user request:", message)
        self.assertNotIn("ALIGN", message)
        self.assertIn("Original brief anchor:", message)


    def test_game_design_prompt_always_appends_mobile_layout_guardrails(self):
        generator = CodeGenerator(llm_mode="real")

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.game_design_template":
                return "Game Type: {game_type}\nTheme: {theme}"
            if key == "prompt.code_gen_system":
                return "CODE_GEN_SYSTEM_FROM_DB"
            if key == "prompt.platform_standard":
                return "PLATFORM"
            return COMMON_CODEGEN_PROMPTS.get(key, default)

        spec = GameSpec(
            game_type="casual",
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
            "complete_with_truncation_retry",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            asyncio.run(generator._llm_generate(spec, gdd, description="做一个竖屏跑酷小游戏"))

        message = mock_complete.await_args.kwargs["messages"][0]["content"]
        self.assertIn("MOBILE LAYOUT IMPLEMENTATION RECIPE:", message)
        self.assertIn("portrait-first reference size of 360x640", message)
        self.assertIn("Math.min(scaleX, scaleY)", message)
        self.assertIn("14-20px", message)
        self.assertIn("CODE SAFETY CHECKLIST (FIRST PRIORITY):", message)
        self.assertIn("Centralize touch extraction", message)
        self.assertIn("CONTRACT IMPLEMENTATION CHECKLIST (CODE SHAPE, NOT JUST INTENT):", message)
        self.assertIn("gameplay state variable", message)
        self.assertIn("ctx.roundRect(...).fill()", message)
        self.assertIn("touchstart, touchmove, and touchend", message)
        self.assertIn("changedTouches[0]", message)
        self.assertIn("const dot = dots[i]; if (!dot) continue;", message)
        self.assertNotIn("NON-NEGOTIABLE MOBILE LAYOUT RULES:", message)
        self.assertNotIn("\nPLATFORM", message)

    def test_generate_omits_standalone_ui_language_block_when_gdd_already_declares_it(self):
        generator = CodeGenerator(llm_mode="real")

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.game_design_template":
                return (
                    "GAME DESIGN DOCUMENT\n"
                    "UI Language: {ui_language}\n"
                    "Visible UI Copy Examples: {ui_text_examples}\n"
                    "Theme: {theme}"
                )
            if key == "prompt.code_gen_system":
                return "SYSTEM"
            if key == "prompt.platform_standard":
                return "PLATFORM"
            return COMMON_CODEGEN_PROMPTS.get(key, default)

        spec = GameSpec(
            game_type="casual",
            source_description="做一个中文城市跑酷游戏",
            rules=GameRules(win_condition="reach the finish line", lose_condition="hit obstacles", lives=3),
            visual_style=VisualStyle(theme="city", art_style="cartoon"),
            ui_language="zh-CN",
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
            "complete_with_truncation_retry",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            asyncio.run(generator._llm_generate(spec, gdd, description=spec.source_description))

        message = mock_complete.await_args.kwargs["messages"][0]["content"]
        self.assertIn("UI Language: zh-CN", message)
        self.assertIn("Visible UI Copy Examples:", message)
        self.assertNotIn("UI LANGUAGE (NON-NEGOTIABLE):", message)

    def test_runtime_contract_block_uses_compact_default_summary(self):
        generator = CodeGenerator(llm_mode="real")

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=lambda key, default=None: COMMON_CODEGEN_PROMPTS.get(key, default),
        ):
            block = generator._build_runtime_contract_block(
                runtime_contract=None,
                runtime_profile="casual_arcade",
                prompt_bundle_snapshot={"bundle_id": "arcade_v3", "layers": {"resolved_prompts": {}}},
            )

        self.assertIn("Runtime profile: casual_arcade (contract v1.0)", block)
        self.assertIn("Core state flow must support boot, ready, playing, game_over", block)
        self.assertIn("Input must work through pointer, touch; expected gestures: tap.", block)
        self.assertIn("Forbidden APIs: eval, Function, import, require.", block)
        self.assertIn("Mobile layout: portrait_first, short_edge scaling, HUD 14-20px, title 28-36px.", block)
        self.assertIn("Platform target: mobile H5 browser / WebView with a single main canvas.", block)
        self.assertIn("Prompt bundle: arcade_v3", block)
        self.assertNotIn("Prompt layers:", block)

    def test_runtime_contract_block_compacts_long_lists_and_conditional_aliases(self):
        generator = CodeGenerator(llm_mode="real")
        contract = GameRuntimeContract(
            runtime_profile="casual_rescue",
            input={"required_modes": ["touch", "pointer", "keyboard", "gamepad", "voice"], "gestures": ["tap", "drag", "swipe", "hold", "double_tap"]},
            safety={"forbidden_apis": ["eval", "Function", "fetch", "XMLHttpRequest", "WebSocket", "localStorage", "sessionStorage", "document.write"]},
            gameplay={"terminal_state_aliases": ["game_over", "victory", "complete", "failed", "won", "lost", "clear", "solved", "finished"]},
        )

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=lambda key, default=None: COMMON_CODEGEN_PROMPTS.get(key, default),
        ):
            block = generator._build_runtime_contract_block(
                runtime_contract=contract,
                runtime_profile=None,
                prompt_bundle_snapshot=None,
            )

        self.assertIn("touch, pointer, keyboard, gamepad, +1 more", block)
        self.assertIn("tap, drag, swipe, hold, +1 more", block)
        self.assertIn("eval, Function, fetch, XMLHttpRequest, WebSocket, localStorage, +2 more", block)
        self.assertIn("Accepted terminal/completion state aliases: game_over, victory, complete, failed, won, lost, clear, solved, +1 more", block)

    def test_contract_implementation_checklist_spells_out_mobile_input_and_restart_requirements(self):
        generator = CodeGenerator(llm_mode="real")

        block = generator._build_contract_implementation_checklist(
            runtime_contract=GameRuntimeContract(),
            runtime_profile="casual_lane",
        )

        self.assertIn("scaleX and scaleY", block)
        self.assertIn("uiScale = Math.min(scaleX, scaleY)", block)
        self.assertIn("canvas.width and canvas.height", block)
        self.assertIn("touchstart, touchmove, and touchend", block)
        self.assertIn("changedTouches[0]", block)
        self.assertIn("pointerdown, pointermove, and pointerup", block)
        self.assertIn("restartGame(), resetGame(), or restart()", block)
        self.assertIn("player.laneX()", block)
        self.assertIn("length check", block)
        self.assertIn("cell.anim or particle.anim", block)
        self.assertIn("function getInputPoint(e)", block)

        puzzle_block = generator._build_contract_implementation_checklist(
            runtime_contract=GameRuntimeContract(),
            runtime_profile="puzzle_grid",
        )
        self.assertIn("visible board-state change", puzzle_block)
        self.assertIn("no-op selection toggles", puzzle_block)
        self.assertIn("const rowBucket = grid[row]; const cell = rowBucket && rowBucket[col];", puzzle_block)
        self.assertIn("function getCell(grid, row, col)", puzzle_block)

    def test_preflight_safety_block_reads_nested_runtime_contract_input_and_orientation(self):
        generator = CodeGenerator(llm_mode="real")

        block = generator._build_preflight_safety_block(
            spec=None,
            runtime_contract=GameRuntimeContract(
                input={"required_modes": ["touch"], "gestures": ["tap", "drag"]},
                mobile_layout={"orientation": "landscape_first"},
            ),
            runtime_profile="casual_arcade",
        )

        self.assertIn("Centralize touch extraction", block)
        self.assertIn("const point = (e.touches", block)
        self.assertIn("landscape reference constants", block)
        self.assertIn("const REF_W = 640; const REF_H = 360;", block)
        self.assertIn("function resizeCanvas()", block)
        self.assertIn("function getInputPoint(e)", block)

    def test_platform_standard_falls_back_only_for_legacy_runtime_contract_templates(self):
        generator = CodeGenerator(llm_mode="real")

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=lambda key, default=None: (
                "RUNTIME CONTRACT:\n- Runtime profile: {runtime_profile}\n- Orientation: {orientation}"
                if key == "prompt.runtime_contract_summary"
                else "PLATFORM"
                if key == "prompt.platform_standard"
                else COMMON_CODEGEN_PROMPTS.get(key, default)
            ),
        ):
            fallback = generator._build_platform_standard_fallback()

        self.assertEqual(fallback, "PLATFORM")

    def test_mobile_layout_guardrails_switch_to_landscape_when_contract_requests_it(self):
        generator = CodeGenerator(llm_mode="real")
        gdd = GDD(
            canvas=CanvasConfig(width=640, height=360, dpr_adaptive=True, target_fps=60),
            numerics=NumericsConfig(),
            collision=CollisionConfig(),
            input_map={},
            ui_layout={},
        )

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=lambda key: COMMON_CODEGEN_PROMPTS[key],
        ):
            guardrails = generator._build_mobile_layout_guardrails(
                gdd,
                GameRuntimeContract(mobile_layout={"orientation": "landscape_first"}),
            )

        self.assertIn("landscape-first", guardrails)
        self.assertNotIn("portrait-first", guardrails)

    def test_profile_few_shot_switches_to_landscape_when_contract_requests_it(self):
        with patch(
            "src.engine.code_generator.get_runtime_profile",
            return_value={
                "few_shot_prompt": (
                    "Use a centered portrait canvas, compact HUD, and one obvious primary interaction."
                ),
            },
        ):
            profile_prompt = CodeGenerator._resolve_profile_few_shot(
                None,
                "casual_arcade",
                runtime_contract=GameRuntimeContract(
                    mobile_layout={"orientation": "landscape_first"},
                ),
            )

        self.assertIn("centered landscape canvas", profile_prompt)
        self.assertNotIn("portrait canvas", profile_prompt)

    def test_profile_few_shot_compacts_long_multiline_reference(self):
        long_prompt = "\n".join(
            [
                "Use a centered portrait canvas with a compact HUD.",
                "Keep one obvious primary interaction and readable feedback.",
                "Prefer short rounds with restart clarity and visible scoring.",
                "Use side-safe buttons and avoid modal UI sprawl.",
                "Extra line that should be trimmed in compact mode.",
            ]
        )

        with patch(
            "src.engine.code_generator.get_runtime_profile",
            return_value={"few_shot_prompt": long_prompt},
        ):
            profile_prompt = CodeGenerator._resolve_profile_few_shot(
                None,
                "casual_arcade",
                runtime_contract=None,
            )

        self.assertIn("Use a centered portrait canvas with a compact HUD.", profile_prompt)
        self.assertIn("Keep one obvious primary interaction and readable feedback.", profile_prompt)
        self.assertIn("Prefer short rounds with restart clarity and visible scoring.", profile_prompt)
        self.assertIn("Extra line that should be trimmed in compact mode.", profile_prompt)
        self.assertNotIn("Use side-safe buttons and avoid modal UI sprawl.", profile_prompt)
        self.assertLessEqual(len(profile_prompt), 300)

    def test_build_game_design_prompt_values_compact_extended_fields(self):
        generator = CodeGenerator(llm_mode="real")
        spec = GameSpec(
            game_type="casual",
            source_description="build a crowded arcade rescue game",
            core_mechanics=[CoreMechanic(type="rescue", input="drag")],
            entities=[
                GameEntity(name="pilot", role="player", shape="triangle", color="#6366f1"),
                GameEntity(name="meteor", role="obstacle", shape="square", color="#f43f5e"),
                GameEntity(name="drone", role="enemy", shape="hex", color="#f97316"),
                GameEntity(name="laser_gate", role="obstacle", shape="rectangle", color="#ef4444"),
                GameEntity(name="mine", role="obstacle", shape="circle", color="#dc2626"),
                GameEntity(name="satellite", role="collectible", shape="diamond", color="#22c55e"),
                GameEntity(name="battery", role="collectible", shape="diamond", color="#84cc16"),
                GameEntity(name="beacon", role="collectible", shape="diamond", color="#14b8a6"),
            ],
            rules=GameRules(win_condition="rescue the crew", lose_condition="lose all shields", lives=3),
            visual_style=VisualStyle(
                theme="space",
                art_style="neon",
                effects=["glow trails", "screen shake", "parallax stars"],
            ),
            design_goals=[
                "Keep rounds under 45 seconds.",
                "Show the next rescue target clearly.",
                "Reward near-miss recoveries.",
                "Make combo pickups visually obvious.",
                "Add a memorable extraction finish.",
            ],
            special_rules=[
                "Rescued crew members trail behind the ship.",
                "Black holes bend nearby movement paths.",
                "Charge gates temporarily disable lasers.",
                "Shield pickups stack into a short grace window.",
                "Extraction beacon appears after the final rescue.",
            ],
            reference_game="Rescue Raiders",
            platform_constraints=PlatformConstraints(platform="wechat_webview", input_mode="drag"),
        )
        gdd = GDD(
            canvas=CanvasConfig(width=420, height=720, dpr_adaptive=True, target_fps=60),
            numerics=NumericsConfig(
                player_speed=8.0,
                base_obstacle_speed=3.5,
                spawn_interval_ms=850,
                score_per_second=1,
                score_per_collect=10,
            ),
            collision=CollisionConfig(),
            input_map={
                "touchstart": "engage_thrusters",
                "touchmove": "drag_ship",
                "touchend": "stabilize",
                "pointerdown": "engage_thrusters",
                "pointermove": "drag_ship",
                "pointerup": "stabilize",
            },
            level_structure=[
                {"id": "sector_1", "goal": "rescue two crew members", "hazards": ["meteor", "mine"]},
                {"id": "sector_2", "goal": "cross the laser corridor", "hazards": ["laser_gate"]},
                {"id": "sector_3", "goal": "draft around a black hole", "hazards": ["black_hole"]},
                {"id": "sector_4", "goal": "escort the convoy", "hazards": ["drone", "mine"]},
                {"id": "sector_5", "goal": "reach the extraction beacon", "hazards": ["laser_gate", "meteor"]},
            ],
            reward_plan={
                "combo": {"window_ms": 2500, "multiplier_cap": 5, "bonus_fx": "aurora streak"},
                "rescue_chain": {"milestones": [2, 4, 6, 8, 10], "reward": "shield refill"},
                "finish": {"beacon_bonus": 500, "flawless_bonus": 800},
                "secret": {"rare_pickup": "distress cache", "value": 999},
                "overflow_rule": "grant one extra life if all rescues are flawless",
            },
            tutorial_beats=[
                "Drag anywhere to steer the ship.",
                "Pass over crew members to attach them.",
                "Avoid hazards until the extraction beacon appears.",
                "Use charge gates to disable laser walls.",
                "Bank near misses to build combo energy.",
            ],
        )

        values = generator._build_game_design_prompt_values(
            spec=spec,
            gdd=gdd,
            description=spec.source_description,
        )

        self.assertIn("+2 more entities", values["entities_desc"])
        self.assertIn("note: +1 more obstacle entities", values["entities_yaml"])
        self.assertIn("note: +2 more inputs", values["input_map_yaml"])
        self.assertIn("+1 more", values["design_goals"])
        self.assertIn("_omitted_keys", values["reward_plan_json"])
        self.assertLessEqual(len(values["level_structure_json"]), 320)
        self.assertNotIn("sector_5", values["level_structure_json"])

    def test_generate_uses_compact_structured_design_values(self):
        generator = CodeGenerator(llm_mode="real")

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.game_design_template":
                return (
                    "Game Type: {game_type}\n"
                    "Entities:\n{entities_desc}\n"
                    "Obstacles:\n{entities_yaml}\n"
                    "Input:\n{input_map_yaml}\n"
                    "Goals: {design_goals}\n"
                    "Levels: {level_structure_json}\n"
                    "Special Rules:\n{special_rules_list}"
                )
            if key == "prompt.code_gen_system":
                return "SYSTEM"
            if key == "prompt.platform_standard":
                return "PLATFORM"
            return COMMON_CODEGEN_PROMPTS.get(key, default)

        spec = GameSpec(
            game_type="casual",
            source_description="build a crowded arcade rescue game",
            core_mechanics=[CoreMechanic(type="rescue", input="drag")],
            entities=[
                GameEntity(name="pilot", role="player"),
                GameEntity(name="meteor", role="obstacle"),
                GameEntity(name="drone", role="enemy"),
                GameEntity(name="laser_gate", role="obstacle"),
                GameEntity(name="mine", role="obstacle"),
                GameEntity(name="satellite", role="collectible"),
                GameEntity(name="battery", role="collectible"),
            ],
            rules=GameRules(win_condition="rescue the crew", lose_condition="lose all shields", lives=3),
            visual_style=VisualStyle(theme="space", art_style="neon", effects=["glow trails", "screen shake"]),
            design_goals=[
                "Keep rounds under 45 seconds.",
                "Show the next rescue target clearly.",
                "Reward near-miss recoveries.",
                "Make combo pickups visually obvious.",
                "Add a memorable extraction finish.",
            ],
            special_rules=[
                "Rescued crew members trail behind the ship.",
                "Black holes bend nearby movement paths.",
                "Charge gates temporarily disable lasers.",
                "Shield pickups stack into a short grace window.",
                "Extraction beacon appears after the final rescue.",
            ],
            reference_game="Rescue Raiders",
            platform_constraints=PlatformConstraints(platform="wechat_webview", input_mode="drag"),
        )
        gdd = GDD(
            canvas=CanvasConfig(width=420, height=720, dpr_adaptive=True, target_fps=60),
            numerics=NumericsConfig(base_obstacle_speed=3.5, spawn_interval_ms=850),
            collision=CollisionConfig(),
            input_map={
                "touchstart": "engage_thrusters",
                "touchmove": "drag_ship",
                "touchend": "stabilize",
                "pointerdown": "engage_thrusters",
                "pointermove": "drag_ship",
                "pointerup": "stabilize",
            },
            level_structure=[
                {"id": "sector_1", "goal": "rescue two crew members"},
                {"id": "sector_2", "goal": "cross the laser corridor"},
                {"id": "sector_3", "goal": "draft around a black hole"},
                {"id": "sector_4", "goal": "escort the convoy"},
                {"id": "sector_5", "goal": "reach the extraction beacon"},
            ],
        )

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            generator._client,
            "complete_with_truncation_retry",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            asyncio.run(generator._llm_generate(spec, gdd, description=spec.source_description))

        message = mock_complete.await_args.kwargs["messages"][0]["content"]
        self.assertIn("+1 more entities", message)
        self.assertIn("note: +1 more obstacle entities", message)
        self.assertIn("note: +2 more inputs", message)
        self.assertIn("+1 more", message)
        self.assertNotIn("sector_5", message)
        self.assertNotIn("pointerup: stabilize", message)

    def test_generate_rewrites_profile_few_shot_for_landscape_contracts(self):
        generator = CodeGenerator(llm_mode="real")
        spec = GameSpec(
            game_type="casual",
            core_mechanics=[CoreMechanic(type="runner", input="swipe")],
            entities=[GameEntity(name="runner", role="player")],
            rules=GameRules(),
            visual_style=VisualStyle(theme="arcade", art_style="minimal"),
            platform_constraints=PlatformConstraints(),
        )
        gdd = GDD(
            canvas=CanvasConfig(width=640, height=360, dpr_adaptive=True, target_fps=60),
            numerics=NumericsConfig(),
            collision=CollisionConfig(),
            input_map={},
            ui_layout={},
        )

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.game_design_template":
                return "Game Type: {game_type}\nTheme: {theme}\nCanvas: {canvas_w}x{canvas_h}"
            if key == "prompt.generate_request_context_template":
                return "Original user request:\n{request_text}"
            if key == "prompt.generate_alignment_reminder":
                return "ALIGN"
            if key == "prompt.platform_standard":
                return "PLATFORM"
            if key == "prompt.code_gen_system":
                return "SYSTEM"
            return COMMON_CODEGEN_PROMPTS.get(key, default)

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=fake_get_prompt,
        ), patch(
            "src.engine.code_generator.get_runtime_profile",
            return_value={
                "few_shot_prompt": (
                    "Use a centered portrait canvas, compact HUD, and one obvious primary interaction."
                ),
            },
        ), patch.object(
            generator._client,
            "complete_with_truncation_retry",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            asyncio.run(
                generator._llm_generate(
                    spec,
                    gdd,
                    description="make a landscape arcade runner",
                    runtime_contract=GameRuntimeContract(
                        mobile_layout={"orientation": "landscape_first"},
                        canvas={"orientation": "landscape_first"},
                    ),
                    runtime_profile="casual_arcade",
                )
            )

        message = mock_complete.await_args.kwargs["messages"][0]["content"]
        self.assertIn("centered landscape canvas", message)
        self.assertNotIn("centered portrait canvas", message)

    def test_generate_omits_large_reference_skeleton_for_rich_prompt(self):
        generator = CodeGenerator(llm_mode="real")
        spec = GameSpec(
            game_type="casual",
            generation_tier="showcase",
            source_description="build a flashy arcade rescue loop with combo scoring and layered progression",
            core_mechanics=[CoreMechanic(type="rescue", input="drag")],
            rules=GameRules(win_condition="rescue everyone", lose_condition="timer", lives=3),
            visual_style=VisualStyle(theme="city", art_style="comic"),
            design_goals=["Keep a strong rescue loop."],
            platform_constraints=PlatformConstraints(platform="wechat_webview", input_mode="drag"),
        )
        gdd = GDD(
            canvas=CanvasConfig(width=420, height=600, dpr_adaptive=True, target_fps=60),
            numerics=NumericsConfig(),
            collision=CollisionConfig(),
            input_map={"touchmove": "drag"},
            level_structure=[{"id": 1, "goal": "rescue"}],
            feedback_moments=["big combo flash"],
        )

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=lambda key, default=None: (
                "Game Type: {game_type}\nTheme: {theme}" if key == "prompt.game_design_template"
                else "SYSTEM" if key == "prompt.code_gen_system"
                else "PLATFORM" if key == "prompt.platform_standard"
                else COMMON_CODEGEN_PROMPTS.get(key, default)
            ),
        ), patch.object(
            generator.template_cache,
            "get_skeleton",
            return_value="<html>" + ("x" * 1800) + "</html>",
        ), patch.object(
            generator._client,
            "complete_with_truncation_retry",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            asyncio.run(generator._llm_generate(spec, gdd, description=spec.source_description))

        message = mock_complete.await_args.kwargs["messages"][0]["content"]
        self.assertNotIn("REFERENCE SKELETON", message)

    def test_generate_keeps_small_reference_skeleton_for_safe_sparse_brief(self):
        generator = CodeGenerator(llm_mode="real")
        spec = GameSpec(
            game_type="casual",
            generation_tier="safe",
            source_description="tap game",
            core_mechanics=[CoreMechanic(type="tap", input="tap")],
            rules=GameRules(win_condition="score", lose_condition="miss", lives=3),
            visual_style=VisualStyle(theme="arcade", art_style="minimal"),
            platform_constraints=PlatformConstraints(platform="wechat_webview", input_mode="touch"),
        )
        gdd = GDD(
            canvas=CanvasConfig(width=360, height=640, dpr_adaptive=True, target_fps=60),
            numerics=NumericsConfig(),
            collision=CollisionConfig(),
            input_map={"touchstart": "tap"},
            ui_layout={},
        )

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=lambda key, default=None: (
                "Game Type: {game_type}\nTheme: {theme}" if key == "prompt.game_design_template"
                else "SYSTEM" if key == "prompt.code_gen_system"
                else "PLATFORM" if key == "prompt.platform_standard"
                else COMMON_CODEGEN_PROMPTS.get(key, default)
            ),
        ), patch.object(
            generator.template_cache,
            "get_skeleton",
            return_value="<!DOCTYPE html><html><body><canvas></canvas><script>function loop(){}</script></body></html>",
        ), patch.object(
            generator._client,
            "complete_with_truncation_retry",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            asyncio.run(generator._llm_generate(spec, gdd, description=spec.source_description))

        message = mock_complete.await_args.kwargs["messages"][0]["content"]
        self.assertIn("REFERENCE SKELETON", message)

    def test_iterate_passes_current_code_using_code_parameter(self):
        generator = CodeGenerator(llm_mode="real")

        with patch.object(generator._client, "is_enabled", return_value=True), patch.object(
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
        self.assertEqual(html, ensure_structured_section_markers("<!DOCTYPE html><html></html>"))
        kwargs = mock_iterate.await_args.kwargs
        self.assertEqual(
            kwargs["code"],
            ensure_structured_section_markers("<!DOCTYPE html><html><body>old</body></html>"),
        )
        self.assertNotIn("current_code", kwargs)

    def test_generate_public_api_injects_structured_section_markers(self):
        generator = CodeGenerator(llm_mode="real")
        spec = GameSpec(
            game_type="casual",
            source_description="make a quick dodge game",
            intent_summary="dodge falling objects",
            core_mechanics=[CoreMechanic(type="dodge", input="touchmove")],
            rules=GameRules(win_condition="survive", lose_condition="hit obstacle", lives=3),
            visual_style=VisualStyle(theme="arcade", art_style="minimal"),
            platform_constraints=PlatformConstraints(platform="wechat_webview", input_mode="touch"),
        )
        gdd = GDD()

        with patch.object(
            generator._client,
            "is_enabled",
            return_value=True,
        ), patch.object(
            generator,
            "_llm_generate",
            new=AsyncMock(return_value="<!DOCTYPE html><html><head><style>body{margin:0;}</style></head><body><canvas id='gameCanvas'></canvas><script>const ready = true;</script></body></html>"),
        ):
            result = asyncio.run(generator.generate(spec, gdd))

        assert "<!-- SECTION:HTML_SHELL START -->" in result.html_code
        assert "<!-- SECTION:STYLE START -->" in result.html_code
        assert "<!-- SECTION:HUD START -->" in result.html_code
        assert "/* SECTION:CONFIG START */" in result.html_code

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
            game_type="casual",
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
            "complete_with_truncation_retry",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            asyncio.run(
                generator._llm_generate(
                    spec,
                    gdd,
                    description="make a portrait runner",
                    runtime_contract=GameRuntimeContract(),
                    runtime_profile="casual_lane",
                    prompt_bundle_snapshot=prompt_bundle_snapshot,
                )
            )

        kwargs = mock_complete.await_args.kwargs
        self.assertIn("LOCKED_CONTRACT_FROM_BUNDLE", kwargs["system"])
        self.assertIn("PRODUCT_POLICY_FROM_BUNDLE", kwargs["system"])
        self.assertIn("LEGACY_SYSTEM_FROM_DB", kwargs["system"])
        self.assertEqual(kwargs["context_scope"], "request")
        message = kwargs["messages"][0]["content"]
        self.assertIn("LOGIC_GENERATE_FROM_BUNDLE", message)
        self.assertIn("PROFILE_FEW_SHOT_FROM_BUNDLE", message)
        self.assertIn("CODE SAFETY CHECKLIST (FIRST PRIORITY):", message)
        self.assertIn("function laneX(index)", message)
        self.assertNotIn("PLATFORM_FROM_DB", message)

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
            "complete_with_truncation_retry",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            asyncio.run(
                generator._llm_iterate(
                    code="<!DOCTYPE html><html><body>old</body></html>",
                    feedback="add coins",
                    conversation=[],
                    iter_type=IterationType.element_change,
                    runtime_contract=GameRuntimeContract(),
                    runtime_profile="casual_lane",
                    prompt_bundle_snapshot=prompt_bundle_snapshot,
                )
            )

        kwargs = mock_complete.await_args.kwargs
        self.assertIn("LOCKED_CONTRACT_FROM_BUNDLE", kwargs["system"])
        self.assertIn("PRODUCT_POLICY_FROM_BUNDLE", kwargs["system"])
        self.assertIn("LEGACY_SYSTEM_FROM_DB", kwargs["system"])
        self.assertEqual(kwargs["context_scope"], "request")
        message = kwargs["messages"][0]["content"]
        self.assertIn("CONTRACT IMPLEMENTATION CHECKLIST (CODE SHAPE, NOT JUST INTENT):", message)
        self.assertNotIn("LOGIC_GENERATE_FROM_BUNDLE", message)
        self.assertNotIn("PROFILE_FEW_SHOT_FROM_BUNDLE", message)
        self.assertIn("ITERATE_PROMPT::add coins::CURRENT PATCHABLE SECTIONS:", message)
        self.assertIn("PATCH-FIRST ITERATION OUTPUT CONTRACT", message)
        self.assertIn("=== SECTION:SCRIPT START ===", message)

    def test_iterate_applies_json_script_patch_response(self):
        generator = CodeGenerator(llm_mode="real")
        code = (
            "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas>"
            "<script>const coins = 1; function loop(){ return coins; }</script></body></html>"
        )

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=lambda key, default=None: (
                "ITERATE_PROMPT::{feedback}::{code}" if key == "prompt.element_change"
                else "MECHANIC_PROMPT::{feedback}::{history}::{code}" if key == "prompt.mechanic_change"
                else "LEGACY_SYSTEM_FROM_DB" if key == "prompt.code_gen_system"
                else COMMON_CODEGEN_PROMPTS.get(key, default)
            ),
        ), patch.object(
            generator._client,
            "complete_with_truncation_retry",
            new=AsyncMock(
                return_value='{"patches":[{"section":"SCRIPT","content":"const coins = 2; function loop(){ return coins; }"}]}'
            ),
        ):
            result = asyncio.run(
                generator._llm_iterate(
                    code=code,
                    feedback="add coins",
                    conversation=[],
                    iter_type=IterationType.element_change,
                )
            )

        self.assertIn("const coins = 2", result)
        self.assertIn("<canvas id='gameCanvas'></canvas>", result)

    def test_iterate_applies_anchor_targeted_patch_response(self):
        generator = CodeGenerator(llm_mode="real")
        code = ensure_structured_section_markers(
            "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas>"
            "<script>const canvas = document.getElementById('gameCanvas'); let coins = 1; "
            "/* SECTION:INPUT START */ canvas.addEventListener('pointerdown', () => {}); /* SECTION:INPUT END */ "
            "function loop(){ return coins; }</script></body></html>"
        )

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=lambda key, default=None: (
                "ITERATE_PROMPT::{feedback}::{code}" if key == "prompt.element_change"
                else "LEGACY_SYSTEM_FROM_DB" if key == "prompt.code_gen_system"
                else COMMON_CODEGEN_PROMPTS.get(key, default)
            ),
        ), patch.object(
            generator._client,
            "complete_with_truncation_retry",
            new=AsyncMock(
                return_value='{"patches":[{"section":"INPUT","operation":"replace_block","content":"canvas.addEventListener(\'pointerdown\', () => { coins += 1; });"}]}'
            ),
        ):
            result = asyncio.run(
                generator._llm_iterate(
                    code=code,
                    feedback="add a tap control",
                    conversation=[],
                    iter_type=IterationType.element_change,
                )
            )

        self.assertIn("canvas.addEventListener('pointerdown'", result)
        self.assertIn("/* SECTION:INPUT START */", result)

    def test_iterate_rolls_back_invalid_patch_candidate(self):
        generator = CodeGenerator(llm_mode="real")
        code = (
            "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas>"
            "<script>const coins = 1; function loop(){ return coins; }</script></body></html>"
        )

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=lambda key, default=None: (
                "ITERATE_PROMPT::{feedback}::{code}" if key == "prompt.element_change"
                else "MECHANIC_PROMPT::{feedback}::{history}::{code}" if key == "prompt.mechanic_change"
                else "LEGACY_SYSTEM_FROM_DB" if key == "prompt.code_gen_system"
                else COMMON_CODEGEN_PROMPTS.get(key, default)
            ),
        ), patch.object(
            generator._client,
            "complete_with_truncation_retry",
            new=AsyncMock(
                return_value='{"patches":[{"section":"BODY","content":"<div>coins only</div><script>const coins = 2;</script>"}]}'
            ),
        ):
            result = asyncio.run(
                generator._llm_iterate(
                    code=code,
                    feedback="change the layout",
                    conversation=[],
                    iter_type=IterationType.mechanic_change,
                )
            )

        self.assertEqual(result, code)

    def test_select_token_budget_uses_generation_tier(self):
        safe_spec = GameSpec(game_type="casual", generation_tier="safe")
        standard_spec = GameSpec(game_type="casual", generation_tier="standard")
        showcase_spec = GameSpec(game_type="casual", generation_tier="showcase")

        safe_budget = CodeGenerator._select_token_budget(safe_spec)
        standard_budget = CodeGenerator._select_token_budget(standard_spec)
        showcase_budget = CodeGenerator._select_token_budget(showcase_spec)

        self.assertLess(safe_budget, standard_budget)
        self.assertGreater(showcase_budget, standard_budget)

    def test_showcase_budget_block_allows_richer_structure(self):
        showcase_spec = GameSpec(
            game_type="casual",
            generation_tier="showcase",
            source_description="build a flashy arcade rescue game",
        )

        block = CodeGenerator._build_implementation_budget_block(showcase_spec, "flashy rescue gameplay")

        self.assertIn("2-3 linked subsystems", block)
        self.assertIn("signature mechanic", block)
        self.assertNotIn("smallest complete mechanic", block)

    def test_showcase_system_prompt_appends_db_override_without_code_fallback(self):
        generator = CodeGenerator(llm_mode="real")
        showcase_spec = GameSpec(game_type="casual", generation_tier="showcase")

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=lambda key, default=None: (
                "Choose the smallest implementation that fully satisfies the brief.\n"
                "Prefer one clear gameplay loop.\n"
                "Avoid optional polish before core loop stability."
                if key == "prompt.code_gen_system"
                else COMMON_CODEGEN_PROMPTS.get(key, default)
            ),
        ):
            system_prompt = generator._build_system_prompt(
                prompt_bundle_snapshot=None,
                spec=showcase_spec,
            )

        self.assertIn("Choose the smallest implementation that fully satisfies the brief.", system_prompt)
        self.assertIn("SHOWCASE OVERRIDE", system_prompt)
        self.assertIn("premium-feeling result", system_prompt)

    def test_generate_prompt_includes_visual_pack_direction(self):
        generator = CodeGenerator(llm_mode="real")
        spec = GameSpec(
            game_type="casual",
            generation_tier="showcase",
            source_description="make a premium neon arcade game",
            rules=GameRules(),
            visual_style=VisualStyle(
                theme="space",
                art_style="neon",
                visual_pack="neon_glass",
                render_style_intensity="high",
            ),
            platform_constraints=PlatformConstraints(),
        )
        gdd = GDD(
            canvas=CanvasConfig(width=360, height=640, dpr_adaptive=True, target_fps=60),
            numerics=NumericsConfig(),
            collision=CollisionConfig(),
            input_map={},
            ui_layout={},
        )

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.game_design_template":
                return "Game Type: {game_type}\nTheme: {theme}"
            if key == "prompt.code_gen_system":
                return "SYSTEM"
            if key == "prompt.platform_standard":
                return "PLATFORM"
            return COMMON_CODEGEN_PROMPTS.get(key, default)

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            generator._client,
            "complete_with_truncation_retry",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            asyncio.run(generator._llm_generate(spec, gdd, description="make a premium neon arcade game"))

        message = mock_complete.await_args.kwargs["messages"][0]["content"]
        self.assertIn("VISUAL PACK DIRECTION:", message)
        self.assertIn("Pack id: neon_glass", message)
        self.assertIn("HUD style: glass_panel", message)
        self.assertIn("Button style: pill_glow", message)

    def test_game_designer_embeds_visual_pack_style_metadata(self):
        designer = GameDesigner()
        spec = GameSpec(
            game_type="puzzle",
            generation_tier="showcase",
            visual_style=VisualStyle(
                theme="ocean",
                art_style="clean",
                visual_pack="clean_edu",
                render_style_intensity="high",
            ),
        )

        gdd = asyncio.run(designer.design(spec))

        self.assertEqual(gdd.ui_layout["style"]["visualPack"], "clean_edu")
        self.assertEqual(gdd.ui_layout["style"]["renderStyleIntensity"], "high")
        self.assertEqual(gdd.ui_layout["style"]["hudStyle"], "clean_cards")
        self.assertIn("Helvetica Neue", gdd.ui_layout["style"]["fontFamily"])

    def test_showcase_design_program_flows_into_prompt(self):
        designer = GameDesigner()
        generator = CodeGenerator(llm_mode="real")
        spec = GameSpec(
            game_type="funny",
            generation_tier="showcase",
            source_description="做一个办公室摸鱼游戏，老板会突然巡查。",
            intent_summary="tap to hide from the boss",
            rules=GameRules(win_condition="stay undiscovered", lose_condition="caught_by_boss"),
            visual_style=VisualStyle(
                theme="office",
                art_style="comic",
                visual_pack="comic_bounce",
                render_style_intensity="high",
            ),
            platform_constraints=PlatformConstraints(input_mode="tap"),
            session_length="rapid_sketch_rounds",
            progression_shape="escalating_gags",
            reward_loop="Land bigger workplace jokes and survive the inspection streak.",
            signature_moment="A fake spreadsheet flips back into a meme wall just before the boss catches you.",
            target_audience="office meme players",
            tone="absurd and punchy",
            reference_style="comic",
            complexity_budget="showcase",
            comedy_device="near_miss_reversal",
            design_goals=["Teach the joke loop fast.", "Escalate the inspection pressure.", "End with a strong gag payoff."],
        )

        gdd = asyncio.run(designer.design(spec))

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.game_design_template":
                return "Game Type: {game_type}\nTheme: {theme}"
            if key == "prompt.code_gen_system":
                return "SYSTEM"
            if key == "prompt.platform_standard":
                return "PLATFORM"
            return COMMON_CODEGEN_PROMPTS.get(key, default)

        with patch(
            "src.engine.code_generator.require_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            generator._client,
            "complete_with_truncation_retry",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            asyncio.run(generator._llm_generate(spec, gdd, description=spec.source_description))

        message = mock_complete.await_args.kwargs["messages"][0]["content"]
        self.assertIn("DESIGN PROGRAM (HIGH PRIORITY):", message)
        self.assertIn("Complexity budget: showcase", message)
        self.assertIn("Signature moment: A fake spreadsheet flips back into a meme wall just before the boss catches you.", message)
        self.assertIn("Level structure:", message)
        self.assertIn("Failure recovery plan:", message)
        self.assertTrue(gdd.level_structure)
        self.assertTrue(gdd.phase_plan)
        self.assertTrue(gdd.feedback_moments)


if __name__ == "__main__":
    unittest.main()
