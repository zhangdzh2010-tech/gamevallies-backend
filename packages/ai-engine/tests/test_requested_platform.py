import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import GameRuntimeContract, GameSpec, RunPipelineV2Request
from src.engine.artifact_quality import infer_artifact_kind
from src.engine.code_generator import CodeGenerator
from src.engine.game_designer import GameDesigner
from src.engine.interactive_creation import (
    interactive_system_prompt,
    is_interactive_request,
    normalize_interactive_request,
)
from src.engine.pipeline_v2_runner import V2PipelineRunner
from src.engine.requested_platform import (
    normalize_requested_platform,
    requires_desktop,
    requires_mobile_portrait,
)


def _request(text: str, **updates) -> RunPipelineV2Request:
    payload = dict(game_id="g", user_id="u", raw_user_input=text, **updates)
    return RunPipelineV2Request(**payload)


def test_science_brief_is_science_artifact_not_mobile_game_contract():
    brief = "做一个牛顿第二定律演示，可调质量和力，展示 F=ma"
    assert infer_artifact_kind(brief) == "science"
    request = _request(brief)
    assert is_interactive_request(request)
    normalized = normalize_interactive_request(request)
    assert normalized.artifact_kind == "science"
    assert normalized.platform == "desktop_web"
    assert normalized.runtime_contract.runtime_profile == "interactive_experience"
    assert normalized.runtime_contract.canvas.orientation == "landscape_first"
    assert normalized.runtime_contract.gameplay.requires_scoring is False
    assert "game_over" not in normalized.runtime_contract.state.required_states
    prompt = interactive_system_prompt("science")
    assert "Mobile-first portrait" not in prompt
    assert "桌面浏览器" in prompt
    assert "不要套用问答、关卡、积分、生命或输赢机制" in prompt


def test_photosynthesis_experiment_uses_interactive_science_path():
    brief = "做一个光合作用实验，可调光照和二氧化碳浓度"
    assert infer_artifact_kind(brief) == "science"
    assert is_interactive_request(_request(brief))
    assert requires_desktop(GameSpec(game_type="casual", source_description=brief))


def test_landscape_or_desktop_keyword_selects_desktop_platform():
    for brief in (
        "做一个横屏塔防",
        "Desktop browser strategy game with mouse control",
        "用键盘和鼠标做一款电脑平台的益智游戏",
    ):
        spec = GameSpec(game_type="casual", source_description=brief)
        assert requires_desktop(spec)
        normalized = normalize_requested_platform(spec)
        assert normalized.platform_constraints.platform == "desktop_browser"
        contract = CodeGenerator._build_requested_platform_contract(normalized)
        assert "desktop browser" in contract.lower() or "DESKTOP" in contract
        assert "phone-portrait" in contract or "not touchstart alone" in contract


def test_landscape_orientation_selects_desktop_even_without_desktop_words():
    spec = GameSpec(game_type="casual", source_description="make a runner with score and restart")
    assert not requires_desktop(spec)
    assert requires_desktop(spec, orientation="landscape")
    assert requires_desktop(spec, metadata={"orientation": "landscape_first"})
    normalized = normalize_requested_platform(spec, orientation="landscape")
    assert normalized.platform_constraints.platform == "desktop_browser"
    runner = V2PipelineRunner()
    contract = runner._compose_runtime_contract(
        base_contract=GameRuntimeContract(),
        spec=normalized,
        runtime_profile="casual_lane",
        entrypoint="create",
    )
    assert contract.canvas.orientation == "landscape_first"
    assert contract.mobile_layout.orientation == "landscape_first"
    assert "pointer" in contract.input.required_modes
    canvas = GameDesigner()._build_canvas(normalized, "landscape_first")
    assert canvas.width == 1280 and canvas.height == 720


def test_mobile_portrait_game_brief_stays_on_mobile_path():
    brief = "做一个太空躲避手机竖屏小游戏。点击开始，左右滑动躲陨石，有分数和重新开始。"
    assert infer_artifact_kind(brief) == "game"
    request = _request(brief)
    assert not is_interactive_request(request)
    spec = GameSpec(game_type="casual", source_description=brief)
    assert requires_mobile_portrait(spec)
    assert not requires_desktop(spec)
    assert not requires_desktop(spec, orientation="portrait")
    assert normalize_requested_platform(spec).platform_constraints.platform == "wechat_webview"
    assert CodeGenerator._build_requested_platform_contract(spec) == ""
    canvas = GameDesigner()._build_canvas(spec, "portrait_first")
    assert canvas.width < canvas.height


def test_compose_contract_does_not_force_mobile_game_loop_for_science_spec():
    spec = normalize_requested_platform(
        GameSpec(game_type="casual", artifact_kind="science", source_description="单摆实验"),
    )
    runner = V2PipelineRunner()
    contract = runner._compose_runtime_contract(
        base_contract=GameRuntimeContract(),
        spec=spec,
        runtime_profile="casual_arcade",
        entrypoint="create",
    )
    assert spec.platform_constraints.platform == "desktop_browser"
    assert contract.canvas.orientation == "landscape_first"
    prompts = {
        "prompt.code_gen_system": "Return ONLY one complete HTML document.\n- Mobile-first portrait gameplay.\n",
        "prompt.code_gen_system_standard": "",
    }
    with patch("src.engine.code_generator.require_prompt", side_effect=lambda key: prompts.get(key, key)):
        system = CodeGenerator()._build_system_prompt(
            prompt_bundle_snapshot=None,
            spec=spec,
            runtime_contract=contract,
        )
    assert "Mobile-first portrait gameplay." not in system
    assert "desktop" in system.lower()
