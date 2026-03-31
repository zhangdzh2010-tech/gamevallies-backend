import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import (
    CanvasConfig,
    CollisionConfig,
    GDD,
    GameRuntimeContract,
    NumericsConfig,
    QACheckError,
    SlotState,
)
from src.engine.code_generator import CodeGenerator
from src.engine.dialogue_engine import _build_game_spec
from src.engine.game_designer import GameDesigner
from src.engine.qa_pipeline import QAPipeline


CODEGEN_PROMPTS = {
    "prompt.game_design_template": (
        "GAME DESIGN DOCUMENT\n"
        "Game Type: {game_type}\n"
        "UI Language: {ui_language}\n"
        "Visible UI Copy Examples: {ui_text_examples}\n"
        "Theme: {theme}\n"
        "Core Mechanic: {core_mechanic}"
    ),
    "prompt.generate_request_context_template": "Original user request:\n{request_text}",
    "prompt.intent_detail_template": (
        "CRITICAL INTENT DETAILS:\n"
        "- Core mechanic: {core_mechanic}\n"
        "- UI language: {ui_language}\n"
        "- Theme: {theme}\n"
        "- Win condition: {win_condition}\n"
        "{reference_line}\n"
        "{special_rules_block}"
    ),
    "prompt.mobile_layout_guardrails": (
        "NON-NEGOTIABLE MOBILE LAYOUT RULES:\n"
        "- Treat {canvas_w}x{canvas_h} as a portrait reference playfield.\n"
        "- Keep HUD text around {hud_font}px."
    ),
    "prompt.platform_standard": "PLATFORM: mobile H5 browser / WebView",
    "prompt.generate_alignment_reminder": "Keep the UI language intact.",
    "prompt.code_gen_system": "Return HTML only.",
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

QA_PROMPTS = {
    "prompt.qa_fix": (
        "Fix ALL listed issues.\n"
        "Game type: {game_type}\n"
        "{runtime_contract_block}\n"
        "Issues:\n{error_list}\n"
        "Current code:\n{code}"
    ),
    "prompt.qa_runtime_contract_block": (
        "Runtime contract (must still hold after the repair):\n"
        "- Contract version: {contract_version}\n"
        "- Runtime profile: {runtime_profile}\n"
        "- Required states: {required_states}\n"
        "- Required input modes: {input_modes}\n"
        "- Forbidden APIs: {forbidden_apis}"
    ),
    "prompt.qa_instruction_generic": "- Fix only the listed QA issues and preserve the original game behavior.",
}


def _fake_codegen_prompt(key: str, default=None):
    return CODEGEN_PROMPTS.get(key, default)


def _fake_qa_prompt(key: str, default=None):
    return QA_PROMPTS.get(key, default)


def test_build_game_spec_infers_chinese_ui_language_and_non_space_visual_defaults():
    spec = _build_game_spec(
        SlotState(game_type="casual"),
        source_description="做一个轻松的跑酷小游戏，画面温暖一点",
    )

    assert spec.ui_language == "zh-CN"
    assert spec.intent_summary == "用一个直观的休闲玩法循环，保持反馈快、目标清晰。"
    assert spec.rules.win_condition == "达成目标分数或完成一轮短挑战。"
    assert spec.visual_style.theme != "space"
    assert spec.visual_style.palette != ["#0a0a2e", "#6366f1", "#22c55e", "#f43f5e", "#ffffff"]


def test_game_designer_emits_localized_ui_labels():
    spec = _build_game_spec(
        SlotState(game_type="casual", theme="forest"),
        source_description="做一个森林主题的躲避游戏",
    )

    gdd = asyncio.run(GameDesigner().design(spec))

    assert gdd.ui_layout["labels"]["score"] == "得分"
    assert gdd.ui_layout["labels"]["restart"] == "重新开始"
    assert gdd.ui_layout["game_over_overlay"]["title"]["label"] == "游戏结束"


def test_code_generator_prompt_includes_ui_language_contract_and_examples():
    generator = CodeGenerator(llm_mode="real")
    spec = _build_game_spec(
        SlotState(game_type="casual", theme="city"),
        source_description="做一个城市跑酷小游戏",
    )
    gdd = GDD(
        canvas=CanvasConfig(width=360, height=640, dpr_adaptive=True, target_fps=60),
        numerics=NumericsConfig(),
        collision=CollisionConfig(),
        input_map={"touchstart": "start"},
        ui_layout={
            "labels": {
                "score": "得分",
                "lives": "生命",
                "ready": "点击开始",
                "game_over": "游戏结束",
                "restart": "重新开始",
            },
            "score": {"x": 16, "y": 32, "font": "bold 16px Arial"},
        },
    )

    with patch(
        "src.engine.code_generator.require_prompt",
        side_effect=_fake_codegen_prompt,
    ), patch.object(
        generator._client,
        "complete",
        new=AsyncMock(return_value="<!DOCTYPE html><html><body></body></html>"),
    ) as mock_complete:
        result = asyncio.run(generator._llm_generate(spec, gdd, description="做一个城市跑酷小游戏"))

    assert result == "<!DOCTYPE html><html><body></body></html>"
    prompt = mock_complete.await_args.kwargs["messages"][0]["content"]
    assert "Visible UI language: zh-CN (Simplified Chinese)" in prompt
    assert "score=得分" in prompt
    assert "点击开始" in prompt


def test_qa_pipeline_repair_prompt_preserves_ui_language():
    pipeline = QAPipeline()
    spec = _build_game_spec(
        SlotState(game_type="casual"),
        source_description="做一个中文界面的躲避游戏",
    )
    errors = [QACheckError(type="runtime_qa", message="overlay text is incorrect", severity="error")]

    with patch(
        "src.engine.qa_pipeline.require_prompt",
        side_effect=_fake_qa_prompt,
    ), patch.object(
        pipeline._client,
        "complete",
        new=AsyncMock(return_value="<!DOCTYPE html><html><body>fixed</body></html>"),
    ) as mock_complete:
        repaired = asyncio.run(
            pipeline._fix_with_llm(
                code="<!DOCTYPE html><html><body>broken</body></html>",
                errors=errors,
                game_spec=spec,
                runtime_contract=GameRuntimeContract(),
                max_tokens=1024,
                prefer_fast=False,
            )
        )

    assert repaired == "<!DOCTYPE html><html><body>fixed</body></html>"
    prompt = mock_complete.await_args.kwargs["messages"][0]["content"]
    assert "Visible UI language: zh-CN (Simplified Chinese)" in prompt
    assert "Do not rewrite visible UI copy into English" in prompt
