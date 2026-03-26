"""Stage 05: LLM-only HTML5 game code generation."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from ..api.models import (
    GDD,
    GameRuntimeContract,
    GameSpec,
    GenerateCodeResult,
    IterationType,
    SourceBundleContext,
)
from ..config.settings import settings
from ..config.timeout_store import get_int as get_timeout_int
from ..services.llm_client import LLMClient
from .prompt_store import get_active_prompt_bundle, require_prompt

logger = logging.getLogger(__name__)


class _SafePromptFormatDict(dict):
    """Preserve unknown placeholders instead of raising KeyError."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"

GAME_TYPE_CORE_MECHANIC_SUMMARY: Dict[str, str] = {
    "dodge": "Move to avoid hazards and survive as long as possible.",
    "platformer": "Jump across platforms, avoid gaps, and reach the goal.",
    "runner": "Keep moving forward, dodge obstacles, and survive the run.",
    "shooter": "Aim, shoot enemies, and stay alive under pressure.",
    "puzzle": "Solve spatial or logical puzzles to clear the objective.",
    "rhythm": "Tap in time with the beat to score points and maintain flow.",
    "tower_defense": "Place defenses and stop incoming waves before they breach.",
    "idle": "Accumulate resources automatically and upgrade progression.",
    "rpg": "Explore, battle, and grow the character through encounters.",
}

LOCALIZED_CORE_MECHANIC_SUMMARY: Dict[str, Dict[str, str]] = {
    "zh-CN": {
        "dodge": "移动并躲开危险，尽量坚持更久。",
        "platformer": "跨越平台、避开空隙，并抵达终点。",
        "runner": "持续前进、躲开障碍，并保持跑酷节奏。",
        "shooter": "瞄准并射击敌人，在压力下保持生存。",
        "puzzle": "通过空间或逻辑推理完成关卡目标。",
        "rhythm": "按节奏点击，保持连击并获得高分。",
        "tower_defense": "布置防御并挡住一波波来袭的敌人。",
        "idle": "积累资源并升级系统，推动自动成长。",
        "rpg": "探索、战斗并逐步强化角色。",
    },
}

UI_LANGUAGE_LABELS: Dict[str, str] = {
    "en-US": "English",
    "zh-CN": "Simplified Chinese",
}

EDUCATIONAL_REQUEST_MARKERS: tuple[str, ...] = (
    "classroom",
    "teacher",
    "lesson",
    "quiz",
    "worksheet",
    "practice",
    "practice question",
    "learning game",
    "teaching",
    "knowledge point",
    "课堂",
    "教学",
    "老师",
    "练习题",
    "知识点",
    "问答",
    "测验",
    "小测",
    "学习游戏",
    "教学游戏",
)

PLAYER_SIZE_BY_GAME_TYPE: Dict[str, tuple[int, int]] = {
    "dodge": (36, 36),
    "platformer": (40, 40),
    "runner": (40, 40),
    "shooter": (42, 42),
    "puzzle": (56, 56),
    "rhythm": (48, 48),
    "tower_defense": (44, 44),
    "idle": (48, 48),
    "rpg": (42, 42),
}


class CodeGenerator:
    """Stage 05: LLM-only HTML5 game code generator."""

    def __init__(self, llm_mode: str = "real") -> None:
        self.llm_mode = llm_mode
        self._client = LLMClient()

    @staticmethod
    def _long_generation_timeout_s() -> int:
        return get_timeout_int(
            "timeout.ai_engine.llm_long_generation_s",
            300,
            min_value=30,
        )

    @staticmethod
    def _select_token_budget(spec: Optional[GameSpec] = None, budget_override: Optional[str] = None) -> int:
        """Select token budget based on game complexity or explicit override."""
        if budget_override:
            mapping = {
                "simple": settings.LLM_GENERATION_TOKEN_BUDGET_SIMPLE,
                "standard": settings.LLM_GENERATION_TOKEN_BUDGET_STANDARD,
                "complex": settings.LLM_GENERATION_TOKEN_BUDGET_COMPLEX,
            }
            return max(1024, mapping.get(budget_override, settings.LLM_LONG_GENERATION_MAX_TOKENS))

        if spec is None:
            return max(1024, settings.LLM_LONG_GENERATION_MAX_TOKENS)

        special_rules_count = len(spec.special_rules or [])
        entity_count = len(spec.entities or [])
        game_type = (spec.game_type or "").lower()

        if special_rules_count >= 3 or entity_count >= 5 or game_type in ("rpg", "tower_defense"):
            return max(1024, settings.LLM_GENERATION_TOKEN_BUDGET_COMPLEX)
        if special_rules_count == 0 and entity_count <= 2 and game_type in ("dodge", "runner"):
            return max(1024, settings.LLM_GENERATION_TOKEN_BUDGET_SIMPLE)
        return max(1024, settings.LLM_GENERATION_TOKEN_BUDGET_STANDARD)

    async def generate(
        self,
        spec: GameSpec,
        gdd: GDD,
        template_id: Optional[str] = None,
        confidence: float = 0.0,
        description: str = "",
        allow_fallback: bool = True,
        runtime_contract: Optional[GameRuntimeContract] = None,
        runtime_profile: Optional[str] = None,
        prompt_bundle_snapshot: Optional[Dict[str, Any]] = None,
        budget_override: Optional[str] = None,
    ) -> GenerateCodeResult:
        del template_id, confidence, allow_fallback
        if self.llm_mode != "real" or not self._client.is_enabled():
            raise RuntimeError("Real LLM mode is required for game generation")

        start = time.time()
        html = await self._llm_generate(
            spec,
            gdd,
            description=description,
            runtime_contract=runtime_contract,
            runtime_profile=runtime_profile,
            prompt_bundle_snapshot=prompt_bundle_snapshot,
            budget_override=budget_override,
        )
        elapsed = int((time.time() - start) * 1000)
        return GenerateCodeResult(
            html_code=html,
            strategy="llm",
            template_id=None,
            generation_time_ms=elapsed,
            code_size_bytes=len(html.encode("utf-8")),
        )

    async def _llm_generate(
        self,
        spec: GameSpec,
        gdd: GDD,
        description: str = "",
        runtime_contract: Optional[GameRuntimeContract] = None,
        runtime_profile: Optional[str] = None,
        prompt_bundle_snapshot: Optional[Dict[str, Any]] = None,
        budget_override: Optional[str] = None,
    ) -> str:
        request_text = self._resolve_request_context(spec, gdd, description)
        entities_desc = "\n".join(
            f"  - {e.name} ({e.role}): shape={e.shape or 'auto'}, color={e.color or 'auto'}"
            for e in spec.entities
        )
        input_map_str = "\n".join(
            f"  {k} -> {v}" for k, v in gdd.input_map.items()
        )
        prompt_values = self._build_game_design_prompt_values(
            spec=spec,
            gdd=gdd,
            description=request_text,
            entities_desc=entities_desc,
            input_map_str=input_map_str,
        )
        prompt_template = require_prompt("prompt.game_design_template")
        structured_design = prompt_template.format_map(_SafePromptFormatDict(prompt_values))
        request_context = (
            require_prompt("prompt.generate_request_context_template").format(
                request_text=request_text,
            )
            if request_text
            else ""
        )
        logic_generate_policy = self._resolved_bundle_prompt(prompt_bundle_snapshot, "logic_generate")
        profile_few_shot = self._resolved_bundle_prompt(prompt_bundle_snapshot, "profile_few_shot")
        implementation_budget = self._build_implementation_budget_block(spec, request_text)
        full_prompt = "\n\n".join(
            part
            for part in [
                request_context.strip(),
                logic_generate_policy,
                profile_few_shot,
                structured_design,
                self._build_critical_intent_block(spec, request_text),
                self._build_ui_language_block(spec.ui_language),
                self._build_runtime_contract_block(runtime_contract, runtime_profile, prompt_bundle_snapshot),
                implementation_budget,
                self._build_mobile_layout_guardrails(gdd),
                require_prompt("prompt.platform_standard"),
            ]
            if part
        )
        if request_text:
            full_prompt += "\n\n" + require_prompt("prompt.generate_alignment_reminder")

        try:
            long_generation_timeout_s = self._long_generation_timeout_s()
            text = await self._client.complete(
                max_tokens=self._select_token_budget(spec, budget_override),
                system=self._build_system_prompt(prompt_bundle_snapshot),
                messages=[{"role": "user", "content": full_prompt}],
                step_key="code_generate.full",
                stage="code_generating",
                request_timeout_s=long_generation_timeout_s,
                overall_timeout_s=long_generation_timeout_s,
                allow_provider_fallback=True,
            )
            return _extract_html(text)
        except Exception as exc:
            logger.error("Full LLM generation failed: %s", exc)
            raise RuntimeError(f"Full LLM generation failed: {exc}") from exc

    def _build_game_design_prompt_values(
        self,
        *,
        spec: GameSpec,
        gdd: GDD,
        description: str,
        entities_desc: str,
        input_map_str: str,
    ) -> Dict[str, Any]:
        palette = spec.visual_style.palette or ["#0a0a2e", "#6366f1", "#22c55e", "#f43f5e", "#ffffff"]
        player_entity = next((entity for entity in spec.entities if entity.role == "player"), None)
        player_shape = (player_entity.shape or "circle") if player_entity else "circle"
        player_color = (
            player_entity.color
            if player_entity and player_entity.color
            else self._palette_value(palette, 1, "#6366f1")
        )
        player_w, player_h = PLAYER_SIZE_BY_GAME_TYPE.get(spec.game_type, (40, 40))
        primary_mechanic = spec.core_mechanics[0] if spec.core_mechanics else None
        core_mechanic = self._derive_core_mechanic_text(spec, description)
        input_type = primary_mechanic.input if primary_mechanic and primary_mechanic.input else spec.platform_constraints.input_mode
        spawn_interval = gdd.numerics.spawn_interval_ms
        min_spawn_interval = max(200, int(spawn_interval * 0.45)) if spawn_interval > 0 else 0
        max_speed = round(max(gdd.numerics.base_obstacle_speed * 2.5, gdd.numerics.player_speed * 1.5, 6.0), 1)
        score_layout = gdd.ui_layout.get("score", {}) if isinstance(gdd.ui_layout, dict) else {}
        score_font = str(score_layout.get("font", "bold 18px Arial"))
        font_match = re.search(r"(\d+)", score_font)
        ui_font_size = int(font_match.group(1)) if font_match else 18

        return {
            "game_type": spec.game_type,
            "core_mechanic": core_mechanic,
            "core_mechanics": core_mechanic,
            "ui_language": spec.ui_language,
            "ui_text_examples": self._build_ui_copy_examples(gdd, spec.ui_language),
            "theme": spec.visual_style.theme,
            "art_style": spec.visual_style.art_style,
            "reference_game": spec.reference_game or "none",
            "palette": ", ".join(palette),
            "canvas_w": gdd.canvas.width,
            "canvas_h": gdd.canvas.height,
            "player_speed": gdd.numerics.player_speed,
            "hitbox_ratio": gdd.collision.hitbox_ratio,
            "obstacle_speed": gdd.numerics.base_obstacle_speed,
            "spawn_interval": spawn_interval,
            "speed_formula": gdd.numerics.speed_formula,
            "score_per_second": gdd.numerics.score_per_second,
            "score_per_collect": gdd.numerics.score_per_collect,
            "lives": spec.rules.lives,
            "expected_s": gdd.numerics.expected_survival_s,
            "win_condition": spec.rules.win_condition,
            "lose_condition": spec.rules.lose_condition,
            "entities_desc": entities_desc,
            "entities_yaml": self._format_entities_yaml(
                [entity for entity in spec.entities if entity.role in ("obstacle", "enemy")],
                fallback_speed=gdd.numerics.base_obstacle_speed,
                fallback_spawn_interval=spawn_interval,
            ),
            "collectibles_yaml": self._format_entities_yaml(
                [entity for entity in spec.entities if entity.role == "collectible"],
                fallback_speed=0,
                fallback_spawn_interval=max(spawn_interval + 400, 1200) if spawn_interval > 0 else 1500,
            ),
            "input_map": input_map_str,
            "input_map_yaml": self._format_input_map_yaml(gdd.input_map),
            "input_type": input_type,
            "color_bg": self._palette_value(palette, 0, "#0a0a2e"),
            "color_primary": self._palette_value(palette, 1, "#6366f1"),
            "color_accent": self._palette_value(palette, 2, "#22c55e"),
            "color_danger": self._palette_value(palette, 3, "#f43f5e"),
            "color_text": self._palette_value(palette, 4, "#ffffff"),
            "player_visual": f"{player_shape} avatar with {player_color} fill",
            "player_draw_method": self._derive_player_draw_method(player_shape),
            "player_w": player_w,
            "player_h": player_h,
            "player_init_pos": self._derive_player_init_pos(spec.game_type, gdd.canvas.width, gdd.canvas.height),
            "difficulty_initial": spec.difficulty_curve,
            "max_speed": max_speed,
            "spawn_decay_formula": (
                f"max({min_spawn_interval}, {spawn_interval} - elapsed_s * 8)"
                if spawn_interval > 0
                else "not_applicable"
            ),
            "min_spawn_interval": min_spawn_interval,
            "combo_desc": "none",
            "invincible_frames": 45 if spec.game_type not in ("puzzle", "rhythm") else 0,
            "ui_score_x": score_layout.get("x", 16),
            "ui_score_y": score_layout.get("y", 36),
            "ui_font_size": ui_font_size,
            "state_flow": self._format_state_flow(gdd.state_machine),
            "special_rules_list": self._format_special_rules(spec),
        }

    def _derive_core_mechanic_text(self, spec: GameSpec, description: str) -> str:
        if spec.intent_summary.strip():
            return spec.intent_summary.strip()
        if description.strip():
            return re.sub(r"\s+", " ", description.strip())[:120]
        if spec.source_description.strip():
            return re.sub(r"\s+", " ", spec.source_description.strip())[:120]
        if spec.core_mechanics:
            mechanic = spec.core_mechanics[0]
            return f"{mechanic.type} gameplay using {mechanic.input} controls."
        localized_summary = LOCALIZED_CORE_MECHANIC_SUMMARY.get(spec.ui_language or "", {})
        if spec.game_type in localized_summary:
            return localized_summary[spec.game_type]
        if spec.game_type in GAME_TYPE_CORE_MECHANIC_SUMMARY:
            return GAME_TYPE_CORE_MECHANIC_SUMMARY[spec.game_type]
        if spec.ui_language == "zh-CN":
            return "适合移动端的玩法循环，目标明确，操控灵敏。"
        return "Mobile-friendly gameplay loop with clear goals and responsive controls."

    @staticmethod
    def _resolve_request_context(spec: GameSpec, gdd: GDD, description: str) -> str:
        candidates = [
            description.strip(),
            spec.source_description.strip(),
            gdd.raw_description.strip(),
        ]
        for candidate in candidates:
            if candidate:
                return re.sub(r"\s+", " ", candidate)
        return ""

    def _build_critical_intent_block(self, spec: GameSpec, request_text: str) -> str:
        reference_line = (
            f"- Reference game: {spec.reference_game}"
            if spec.reference_game
            else "- Reference game: none"
        )
        special_rules_block = (
            "- Must preserve these special rules:\n"
            + "\n".join(f"  - {rule}" for rule in spec.special_rules)
            if spec.special_rules
            else "- Special rules: none"
        )
        return require_prompt("prompt.intent_detail_template").format(
            core_mechanic=self._derive_core_mechanic_text(spec, request_text),
            theme=spec.visual_style.theme,
            win_condition=spec.rules.win_condition,
            reference_line=reference_line,
            special_rules_block=special_rules_block,
            ui_language=self._describe_ui_language(spec.ui_language),
        )

    @staticmethod
    def _describe_ui_language(ui_language: str) -> str:
        normalized = (ui_language or "en-US").strip() or "en-US"
        return f"{normalized} ({UI_LANGUAGE_LABELS.get(normalized, normalized)})"

    def _build_ui_language_block(self, ui_language: str) -> str:
        return (
            "UI LANGUAGE (NON-NEGOTIABLE):\n"
            f"- Visible UI language: {self._describe_ui_language(ui_language)}\n"
            "- All player-visible text must use this language, including title, HUD labels, buttons, overlays, tutorials, and win/lose copy.\n"
            "- Keep code identifiers, variable names, function names, and JSON keys in English.\n"
            "- Do not silently fall back to English UI copy unless the visible UI language itself is English."
        )

    @classmethod
    def _looks_like_educational_request(cls, *texts: str) -> bool:
        combined = " ".join((text or "").strip().lower() for text in texts if (text or "").strip())
        if not combined:
            return False
        return any(marker in combined for marker in EDUCATIONAL_REQUEST_MARKERS)

    @classmethod
    def _build_implementation_budget_block(
        cls,
        spec: Optional[GameSpec],
        request_text: str,
        *,
        fallback_game_type: str = "dodge",
    ) -> str:
        game_type = spec.game_type if spec else fallback_game_type
        source_description = spec.source_description if spec else ""
        intent_summary = spec.intent_summary if spec else ""
        lines = [
            "IMPLEMENTATION BUDGET (NON-NEGOTIABLE):",
            "- Use one canvas, one primary state object, and one requestAnimationFrame loop.",
            "- Keep only one main HUD and at most one overlay screen for ready/game-over or level-complete states.",
            "- Avoid scene managers, dialogue trees, worksheet generators, multi-page courseware, inventories, or parallel mini-games unless they are absolutely required for the core mechanic.",
            "- Prefer the smallest complete mechanic that satisfies the request and runtime contract before adding optional polish.",
            "- Reuse the same controls and state machine across the whole experience instead of creating separate subsystems.",
        ]
        if game_type == "runner":
            lines.extend([
                "- Keep one obstacle loop and at most one collectible loop.",
                "- Reuse the same lane/survival loop for progression instead of adding side modes.",
            ])
        if game_type == "puzzle":
            lines.extend([
                "- Keep one board or playfield and one clear solve condition.",
                "- Prefer concise tap/drag interactions over multiple modal interfaces.",
            ])
        if cls._looks_like_educational_request(request_text, source_description, intent_summary):
            lines.extend([
                "- For classroom or knowledge-check requests, convert the idea into one touch-friendly puzzle/quiz loop, not a lesson plan, worksheet, or long teaching document.",
                "- Keep the challenge set compact, such as 3-5 levels or prompts on one shared board/layout.",
                "- Use short player-visible prompts and immediate feedback instead of generating long explanatory text blocks.",
            ])
        return "\n".join(lines)

    @staticmethod
    def _build_source_bundle_context_block(source_bundle_context: Optional[SourceBundleContext]) -> str:
        if not source_bundle_context:
            return ""

        lines = ["HISTORICAL GAME CONTEXT:"]
        if source_bundle_context.title:
            lines.append(f"- Existing title: {source_bundle_context.title}")
        if source_bundle_context.latest_bundle_version is not None:
            lines.append(f"- Latest bundle version: {source_bundle_context.latest_bundle_version}")
        if source_bundle_context.latest_game_type:
            lines.append(f"- Current game type: {source_bundle_context.latest_game_type}")
        if source_bundle_context.latest_feedback:
            lines.append(f"- Latest user feedback: {source_bundle_context.latest_feedback}")
        if source_bundle_context.latest_iteration_type:
            lines.append(f"- Latest iteration type: {source_bundle_context.latest_iteration_type}")
        if source_bundle_context.summary:
            lines.append(f"- Summary: {source_bundle_context.summary}")
        for revision in (source_bundle_context.recent_revisions or [])[:4]:
            revision_bits = [
                f"v{revision.version}" if revision.version is not None else "",
                revision.feedback or "",
                revision.iteration_type or "",
                revision.summary or "",
            ]
            compact = " | ".join(bit for bit in revision_bits if bit)
            if compact:
                lines.append(f"- Recent revision: {compact}")
        return "\n".join(lines)

    @staticmethod
    def _build_ui_copy_examples(gdd: GDD, ui_language: str) -> str:
        labels = gdd.ui_layout.get("labels", {}) if isinstance(gdd.ui_layout, dict) else {}
        if not isinstance(labels, dict) or not labels:
            defaults = {
                "en-US": {
                    "score": "Score",
                    "lives": "Lives",
                    "ready": "Tap to Start",
                    "game_over": "Game Over",
                    "restart": "Restart",
                },
                "zh-CN": {
                    "score": "得分",
                    "lives": "生命",
                    "ready": "点击开始",
                    "game_over": "游戏结束",
                    "restart": "重新开始",
                },
            }
            labels = defaults.get(ui_language, defaults["en-US"])
        return ", ".join(f"{key}={value}" for key, value in labels.items())

    def _build_mobile_layout_guardrails(self, gdd: GDD) -> str:
        score_layout = gdd.ui_layout.get("score", {}) if isinstance(gdd.ui_layout, dict) else {}
        score_font = str(score_layout.get("font", "bold 16px Arial"))
        match = re.search(r"(\d+)", score_font)
        hud_font = int(match.group(1)) if match else 16
        hud_font = max(14, min(hud_font, 18))
        return require_prompt("prompt.mobile_layout_guardrails").format(
            canvas_w=gdd.canvas.width,
            canvas_h=gdd.canvas.height,
            hud_font=hud_font,
        )

    @staticmethod
    def _format_state_flow(state_machine: Dict[str, Any]) -> str:
        states = state_machine.get("states") if isinstance(state_machine, dict) else None
        if isinstance(states, list) and states:
            return " -> ".join(str(state) for state in states)
        return "boot -> ready -> playing -> game_over -> ready"

    def _build_runtime_contract_block(
        self,
        runtime_contract: Optional[GameRuntimeContract],
        runtime_profile: Optional[str],
        prompt_bundle_snapshot: Optional[Dict[str, Any]],
    ) -> str:
        if not runtime_contract:
            return require_prompt("prompt.runtime_contract_summary").format(
                runtime_profile=runtime_profile or "standard_mobile_canvas",
                contract_version="1.0",
                bundle_id=(prompt_bundle_snapshot or {}).get("bundle_id") or "unknown",
                layer_keys=", ".join(sorted((prompt_bundle_snapshot or {}).get("layers", {}).keys())) or "default",
                required_states="boot, ready, playing, game_over",
                input_modes="touch, pointer",
                gestures="tap",
                forbidden_apis="eval, Function, import, require",
                terminal_state_aliases="game_over",
                orientation="portrait_first",
                ui_scale_mode="short_edge",
                hud_min=14,
                hud_max=20,
                title_min=28,
                title_max=36,
            )

        bundle_id = (prompt_bundle_snapshot or {}).get("bundle_id")
        if not bundle_id:
            active_bundle = get_active_prompt_bundle()
            bundle_id = str(active_bundle.get("id")) if isinstance(active_bundle, dict) and active_bundle.get("id") else "unresolved_bundle"
        layer_keys = sorted((prompt_bundle_snapshot or {}).get("layers", {}).keys())
        gestures = ", ".join(runtime_contract.input.gestures) if runtime_contract.input.gestures else "tap"
        forbidden = ", ".join(runtime_contract.safety.forbidden_apis)
        required_states = ", ".join(runtime_contract.state.required_states)
        terminal_state_aliases = ", ".join(runtime_contract.gameplay.terminal_state_aliases or []) or "game_over"
        summary = require_prompt("prompt.runtime_contract_summary").format(
            runtime_profile=runtime_profile or runtime_contract.runtime_profile,
            contract_version=runtime_contract.version,
            bundle_id=bundle_id,
            layer_keys=", ".join(layer_keys) if layer_keys else "default",
            required_states=required_states,
            input_modes=", ".join(runtime_contract.input.required_modes),
            gestures=gestures,
            forbidden_apis=forbidden,
            terminal_state_aliases=terminal_state_aliases,
            orientation=runtime_contract.mobile_layout.orientation,
            ui_scale_mode=runtime_contract.mobile_layout.ui_scale_mode,
            hud_min=runtime_contract.mobile_layout.font_clamp.hud_min,
            hud_max=runtime_contract.mobile_layout.font_clamp.hud_max,
            title_min=runtime_contract.mobile_layout.font_clamp.title_min,
            title_max=runtime_contract.mobile_layout.font_clamp.title_max,
        )
        if "terminal/completion state aliases" not in summary.lower():
            summary += f"\n- Accepted terminal/completion state aliases: {terminal_state_aliases}"
        return summary

    @staticmethod
    def _resolved_bundle_prompt(
        prompt_bundle_snapshot: Optional[Dict[str, Any]],
        slot: str,
    ) -> str:
        resolved_prompts = ((prompt_bundle_snapshot or {}).get("layers") or {}).get("resolved_prompts")
        if not isinstance(resolved_prompts, dict):
            return ""

        entry = resolved_prompts.get(slot)
        if isinstance(entry, dict):
            return str(entry.get("content") or "").strip()
        if isinstance(entry, str):
            return entry.strip()
        return ""

    def _build_system_prompt(self, prompt_bundle_snapshot: Optional[Dict[str, Any]]) -> str:
        sections = [
            self._resolved_bundle_prompt(prompt_bundle_snapshot, "locked_contract"),
            self._resolved_bundle_prompt(prompt_bundle_snapshot, "product_policy"),
            require_prompt("prompt.code_gen_system"),
        ]

        deduped: List[str] = []
        seen: set[str] = set()
        for section in sections:
            normalized = (section or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return "\n\n".join(deduped)

    @staticmethod
    def _palette_value(palette: List[str], index: int, fallback: str) -> str:
        if 0 <= index < len(palette) and palette[index]:
            return palette[index]
        return fallback

    @staticmethod
    def _derive_player_draw_method(shape: str) -> str:
        shape_key = (shape or "").lower()
        if shape_key == "triangle":
            return "Canvas path triangle with filled color and subtle outline"
        if shape_key in {"square", "rectangle"}:
            return "Filled rounded rectangle drawn with Canvas 2D primitives"
        if shape_key == "diamond":
            return "Rotated square diamond drawn with Canvas path commands"
        return "Filled circle or simple geometric sprite drawn with Canvas 2D primitives"

    @staticmethod
    def _derive_player_init_pos(game_type: str, canvas_w: int, canvas_h: int) -> str:
        if game_type in {"dodge", "runner", "shooter"}:
            return f"bottom-center ({canvas_w // 2}, {canvas_h - 72})"
        if game_type == "platformer":
            return f"lower-left quarter ({canvas_w // 4}, {canvas_h - 96})"
        return f"center ({canvas_w // 2}, {canvas_h // 2})"

    def _format_entities_yaml(
        self,
        entities: List[Any],
        *,
        fallback_speed: float,
        fallback_spawn_interval: int,
    ) -> str:
        if not entities:
            return "  - none"

        lines: List[str] = []
        for entity in entities:
            shape = entity.shape or "auto"
            color = entity.color or "#ffffff"
            spawn_interval = entity.spawn_rate or fallback_spawn_interval
            lines.extend([
                f"  - name: {entity.name}",
                f"    visual: {shape} shape, color {color}",
                "    size: 32-56px",
                f"    speed: {fallback_speed} px/s",
                f"    spawn_interval: {spawn_interval} ms",
                "    spawn_position: random edge or lane depending on game flow",
                "    movement: straight with mild variance unless game rules require otherwise",
                f"    collision_effect: {'damage player' if entity.role in ('obstacle', 'enemy') else 'award score'}",
            ])
        return "\n".join(lines)

    @staticmethod
    def _format_input_map_yaml(input_map: Dict[str, str]) -> str:
        if not input_map:
            return "  touchstart: start or primary interaction"
        return "\n".join(f"  {event}: {action}" for event, action in input_map.items())

    @staticmethod
    def _format_special_rules(spec: GameSpec) -> str:
        rules: List[str] = []
        if spec.special_rules:
            rules.extend(f"- {rule}" for rule in spec.special_rules)
        if spec.reference_game:
            rules.append(f"- Reference game inspiration: {spec.reference_game}")
        if spec.visual_style.effects:
            rules.extend(f"- Visual effect: {effect}" for effect in spec.visual_style.effects)
        if spec.platform_constraints.platform:
            rules.append(f"- Target platform: {spec.platform_constraints.platform}")
        return "\n".join(rules) if rules else "- none"

    async def iterate(
        self,
        current_code: str,
        feedback: str,
        conversation: List[dict],
        allow_fallback: bool = True,
        runtime_contract: Optional[GameRuntimeContract] = None,
        runtime_profile: Optional[str] = None,
        prompt_bundle_snapshot: Optional[Dict[str, Any]] = None,
        game_spec: Optional[GameSpec] = None,
        source_bundle_context: Optional[SourceBundleContext] = None,
    ) -> Tuple[str, IterationType]:
        del allow_fallback
        if self.llm_mode != "real" or not self._client.is_enabled():
            raise RuntimeError("Real LLM mode is required for game iteration")

        iter_type = await self._classify_iteration(feedback)
        if iter_type == IterationType.param_adjust:
            updated = self._param_adjust(current_code, feedback)
            if updated != current_code:
                return updated, iter_type

        updated = await self._llm_iterate(
            code=current_code,
            feedback=feedback,
            conversation=conversation,
            iter_type=iter_type,
            runtime_contract=runtime_contract,
            runtime_profile=runtime_profile,
            prompt_bundle_snapshot=prompt_bundle_snapshot,
            game_spec=game_spec,
            source_bundle_context=source_bundle_context,
        )
        return updated, iter_type

    async def _classify_iteration(self, feedback: str) -> IterationType:
        try:
            text = await self._client.complete(
                max_tokens=512,
                messages=[{
                    "role": "user",
                    "content": require_prompt("prompt.iterate_classify").format(feedback=feedback),
                }],
                step_key="iterate.classify",
                stage="code_generating",
                prefer_fast=True,
            )
            label = text.strip().lower()
            for iter_type in IterationType:
                if iter_type.value in label:
                    return iter_type
        except Exception:
            pass
        return IterationType.element_change

    def _param_adjust(self, code: str, feedback: str) -> str:
        fb = feedback.lower()
        speed_pattern = r"(player[._]?speed\s*[:=]\s*|const\s+SPEED\s*=\s*)(\d+\.?\d*)"

        if "faster" in fb or "加速" in fb or "更快" in fb:
            code = re.sub(
                speed_pattern,
                lambda match: match.group(1) + str(round(float(match.group(2)) * 1.5, 1)),
                code,
                flags=re.IGNORECASE,
            )
        if "slower" in fb or "减速" in fb or "更慢" in fb:
            code = re.sub(
                speed_pattern,
                lambda match: match.group(1) + str(round(float(match.group(2)) * 0.7, 1)),
                code,
                flags=re.IGNORECASE,
            )

        lives_match = re.search(r"(\d+)\s*(?:lives|生命|命)", fb)
        if lives_match:
            lives = lives_match.group(1)
            code = re.sub(
                r"(lives\s*[:=]\s*)\d+",
                lambda match: match.group(1) + lives,
                code,
                flags=re.IGNORECASE,
            )

        primary_colors = r"#(?:6366f1|6e56ff|4f46e5|7c3aed)"
        if "red" in fb or "红" in fb:
            code = re.sub(primary_colors, "#ef4444", code, flags=re.IGNORECASE)
        if "green" in fb or "绿" in fb:
            code = re.sub(primary_colors, "#22c55e", code, flags=re.IGNORECASE)
        if "blue" in fb or "蓝" in fb:
            code = re.sub(primary_colors, "#3b82f6", code, flags=re.IGNORECASE)
        if "yellow" in fb or "黄" in fb:
            code = re.sub(primary_colors, "#eab308", code, flags=re.IGNORECASE)

        return code

    async def _llm_iterate(
        self,
        code: str,
        feedback: str,
        conversation: List[dict],
        iter_type: IterationType,
        runtime_contract: Optional[GameRuntimeContract] = None,
        runtime_profile: Optional[str] = None,
        prompt_bundle_snapshot: Optional[Dict[str, Any]] = None,
        game_spec: Optional[GameSpec] = None,
        source_bundle_context: Optional[SourceBundleContext] = None,
    ) -> str:
        history_text = "\n".join(
            f"{item.get('role', 'user')}: {item.get('content', '')}"
            for item in conversation[-4:]
        )
        mobile_guardrails = require_prompt("prompt.iteration_mobile_layout_guardrails")

        if iter_type == IterationType.param_adjust:
            prompt = require_prompt("prompt.param_adjust").format(
                feedback=feedback,
                code=code,
            )
            step_key = "iterate.param_adjust"
        elif iter_type == IterationType.element_change:
            prompt = require_prompt("prompt.element_change").format(
                feedback=feedback,
                code=code,
            )
            step_key = "iterate.element_change"
        else:
            prompt = require_prompt("prompt.mechanic_change").format(
                feedback=feedback,
                history=history_text,
                code=code,
            )
            step_key = "iterate.mechanic_change"
        contract_block = self._build_runtime_contract_block(
            runtime_contract,
            runtime_profile,
            prompt_bundle_snapshot,
        )
        spec_block = self._build_critical_intent_block(game_spec, feedback) if game_spec else ""
        source_context_block = self._build_source_bundle_context_block(source_bundle_context)
        logic_generate_policy = self._resolved_bundle_prompt(prompt_bundle_snapshot, "logic_generate")
        profile_few_shot = self._resolved_bundle_prompt(prompt_bundle_snapshot, "profile_few_shot")
        prompt = "\n\n".join(
            part
            for part in [
                logic_generate_policy,
                profile_few_shot,
                contract_block,
                spec_block,
                source_context_block,
                self._build_ui_language_block(game_spec.ui_language if game_spec else "en-US"),
                self._build_implementation_budget_block(game_spec, feedback),
                mobile_guardrails,
                prompt,
            ]
            if part
        )

        try:
            long_generation_timeout_s = self._long_generation_timeout_s()
            text = await self._client.complete(
                max_tokens=self._select_token_budget(game_spec),
                system=self._build_system_prompt(prompt_bundle_snapshot),
                messages=[{"role": "user", "content": prompt}],
                step_key=step_key,
                stage="code_generating",
                request_timeout_s=long_generation_timeout_s,
                overall_timeout_s=long_generation_timeout_s,
                allow_provider_fallback=True,
            )
            return _extract_html(text)
        except Exception as exc:
            logger.error("LLM iterate failed: %s", exc)
            raise RuntimeError(f"LLM iterate failed: {exc}") from exc


def _extract_html(text: str) -> str:
    """Extract clean HTML from LLM output."""
    text = text.lstrip("\ufeff")
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"```(?:html)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"```\s*(?:$|\n)", "", text, flags=re.MULTILINE)
    match = re.search(r"(<!DOCTYPE\s+html|<html)", text, re.IGNORECASE)
    if match:
        text = text[match.start():]
    return text.strip()
