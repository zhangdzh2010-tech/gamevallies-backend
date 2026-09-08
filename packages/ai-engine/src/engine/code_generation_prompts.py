"""Prompts extracted from code_generator.py."""

from __future__ import annotations
import json
import re
from typing import Any, Dict, List, Optional
from ..api.models import GDD, GameRuntimeContract, GameSpec
from .runtime_profile_ids import normalize_runtime_profile_id
from .visual_pack_catalog import get_visual_pack, visual_pack_direction_lines
from .code_generation_support import GAME_TYPE_CORE_MECHANIC_SUMMARY, LOCALIZED_CORE_MECHANIC_SUMMARY, UI_LANGUAGE_LABELS, EDUCATIONAL_REQUEST_MARKERS, PLAYER_SIZE_BY_GAME_TYPE

class CodeGenerationPromptsMixin:
    """Prompts behavior; state remains owned by CodeGenerator."""

    @staticmethod
    def _build_generation_guidance_block(generation_guidance: Optional[str]) -> str:
        normalized = str(generation_guidance or "").strip()
        if not normalized:
            return ""
        return normalized


    @staticmethod
    def _build_preflight_safety_block(
        spec: Optional[GameSpec],
        runtime_contract: Optional[GameRuntimeContract],
        runtime_profile: Optional[str],
    ) -> str:
        lines = [
            "CODE SAFETY CHECKLIST (FIRST PRIORITY):",
            "- Declare every live helper, alias, and loop variable before use; never reference undeclared short aliases such as `line`, `cell`, `dot`, `nr`, `nc`, `viewWidth`, or `viewHeight`.",
            "- Set explicit non-zero `canvas.width` and `canvas.height` during init/resize before the first render.",
            "- Acquire `ctx = canvas.getContext('2d')` immediately after the canvas is created, and never call `ctx.setTransform(...)`, `ctx.clearRect(...)`, or similar APIs before that initialization succeeds.",
            "- Do not initialize `ctx`, `player`, `touchState`, `dragState`, `dragStart`, or other live runtime objects to `null` if the main loop can run before they are assigned; prefer safe default objects or guard every property read until initialization completes.",
            "- Keep collection item aliases scoped to the loop or callback that declares them; if code reads `star.x`, `particle.alpha`, or similar live members, declare `const star = stars[i]` / `const particle = particles[i]` in the same block before use.",
            "- For decorative loops such as `dots`, `stars`, `particles`, or `lines`, use a concrete loop shape like `for (let i = 0; i < dots.length; i += 1) { const dot = dots[i]; if (!dot) continue; ... }` and never read `dot.*` outside that declaring block.",
            "- Declare `update()`, `render()`, and any loop helper before the first direct call or `requestAnimationFrame(loop)` callback that invokes them; never rely on undeclared function expressions being available earlier in the file.",
            "- If resize or layout logic uses `scaleX`, `scaleY`, `uiScale`, `viewWidth`, or `viewHeight`, define those aliases inside the same resize/init block before the first HUD or canvas draw that reads them.",
            "- Do not begin a statement with a bare `.` or split property chains across lines; every canvas or object call must be a complete JavaScript statement on its own line.",
            "- The first canvas/document interaction must be able to start gameplay from `boot` / `ready`; never write a primary input handler that only says `if (state !== 'playing') return` unless that same handler can call `startGame()` first.",
            "- When reading input coordinates, prefer `const point = touch || e;` and only read `point.clientX` / `point.clientY` after guarding touch arrays and null cases.",
            "- Do not build translucent gradient or fill colors by concatenating alpha suffixes onto dynamic color strings such as `light.color + '80'`; use explicit `rgba(...)` / `hsla(...)` values or full `#RRGGBBAA` literals.",
        ]

        profile = str(runtime_profile or "").strip().lower()
        input_contract = getattr(runtime_contract, "input", None)
        input_modes = [
            str(mode or "").strip().lower()
            for mode in (getattr(input_contract, "required_modes", None) or [])
        ]
        gestures = [
            str(gesture or "").strip().lower()
            for gesture in (getattr(input_contract, "gestures", None) or [])
        ]
        platform_input_mode = str(getattr(getattr(spec, "platform_constraints", None), "input_mode", "") or "").strip().lower()
        if platform_input_mode:
            input_modes.append(platform_input_mode)
        input_modes = [mode for mode in dict.fromkeys(input_modes) if mode]
        gestures = [gesture for gesture in dict.fromkeys(gestures) if gesture]
        orientation = (
            runtime_contract.mobile_layout.orientation
            if runtime_contract and runtime_contract.mobile_layout
            else "portrait_first"
        )

        touch_input_present = any("touch" in mode for mode in input_modes)
        if touch_input_present or any("drag" in gesture or "tap" in gesture or "swipe" in gesture for gesture in gestures):
            lines.append(
                "- Centralize touch extraction in one guarded helper, for example "
                "`const touch = (e.touches && e.touches.length ? e.touches[0] : (e.changedTouches && e.changedTouches.length ? e.changedTouches[0] : null)); if (!touch) return;`."
            )
            lines.append(
                "- Never read `e.touches[0]` or `e.changedTouches[0]` directly inside gameplay handlers; define one helper such as "
                "`function getInputPoint(e) { const point = (e.touches && e.touches.length ? e.touches[0] : (e.changedTouches && e.changedTouches.length ? e.changedTouches[0] : e)); "
                "if (!point || point.clientX == null || point.clientY == null) return null; return { x: point.clientX, y: point.clientY }; }` and reuse that exact shape."
            )
            lines.append(
                "- If gameplay stores live touch or drag state, initialize it to a safe object such as "
                "`let touchState = { active: false, x: 0, y: 0, startX: 0, startY: 0 };` instead of `null`, "
                "or guard every `touchState.*` / `touch.*` read before the first animation frame."
            )
            lines.append(
                "- Prefer one shared coordinate helper such as "
                "`const point = (e.touches && e.touches.length ? e.touches[0] : (e.changedTouches && e.changedTouches.length ? e.changedTouches[0] : e)); "
                "if (!point || point.clientX == null || point.clientY == null) return;` and reuse it in start/move/end handlers."
            )
        if orientation == "landscape_first":
            lines.append(
                "- For landscape-first layouts, declare explicit landscape reference constants such as "
                "`const REF_W = 640; const REF_H = 360; const scaleX = canvas.width / REF_W; const scaleY = canvas.height / REF_H; const uiScale = Math.min(scaleX, scaleY);` "
                "before laying out HUD or gameplay."
            )
            lines.append(
                "- Keep a resize scaffold equivalent to "
                "`function resizeCanvas() { canvas.width = Math.max(1, Math.round(window.innerWidth || REF_W)); canvas.height = Math.max(1, Math.round(window.innerHeight || REF_H)); "
                "const scaleX = canvas.width / REF_W; const scaleY = canvas.height / REF_H; const uiScale = Math.min(scaleX, scaleY); const viewWidth = canvas.width; const viewHeight = canvas.height; return { scaleX, scaleY, uiScale, viewWidth, viewHeight }; }`."
            )
        else:
            lines.append(
                "- For portrait-first layouts, declare explicit portrait reference constants such as "
                "`const REF_W = 360; const REF_H = 640; const scaleX = canvas.width / REF_W; const scaleY = canvas.height / REF_H; const uiScale = Math.min(scaleX, scaleY);` "
                "before laying out HUD or gameplay."
            )
            lines.append(
                "- Keep a resize scaffold equivalent to "
                "`function resizeCanvas() { canvas.width = Math.max(1, Math.round(window.innerWidth || REF_W)); canvas.height = Math.max(1, Math.round(window.innerHeight || REF_H)); "
                "const scaleX = canvas.width / REF_W; const scaleY = canvas.height / REF_H; const uiScale = Math.min(scaleX, scaleY); const viewWidth = canvas.width; const viewHeight = canvas.height; return { scaleX, scaleY, uiScale, viewWidth, viewHeight }; }`."
            )
        if "puzzle_grid" in profile or "grid" in profile:
            lines.append(
                "- Never read `grid[row][col].prop` directly; define `getCell(row, col)` or local guards like "
                "`const rowBucket = grid[row]; const cell = rowBucket && rowBucket[col]; if (!cell) return;` before every `cell.type`, `cell.anim`, or neighbor read."
            )
            lines.append(
                "- Start every puzzle-grid implementation with a concrete safe accessor such as "
                "`function getCell(grid, row, col) { const rowBucket = grid[row]; return rowBucket ? rowBucket[col] : null; }` "
                "and reuse it everywhere instead of ad-hoc indexing."
            )
            lines.append(
                "- In match-finding, gravity, hint, and neighbor scans, first read "
                "`const cell = getCell(grid, row, col); if (!cell) continue;` and only then access `cell.type`, "
                "`cell.fruit`, `cell.targetY`, `cell.anim`, `cell.animProgress`, or adjacent cells."
            )
            lines.append(
                "- The final HTML must contain zero raw `grid[row][col].*` reads; route every grid lookup through "
                "`getCell(grid, row, col)` or an equivalent guarded `rowBucket/cell` pattern."
            )
        if "lane" in profile:
            lines.append(
                "- If lane helpers are used, declare them explicitly, for example `function laneX(index) { ... }`, before the render or input loop references them."
            )

        lines.append("- If any draft pattern conflicts with these rules, rewrite it before finalizing the HTML output.")
        return "\n".join(lines)


    def _build_game_design_prompt_values(
        self,
        *,
        spec: GameSpec,
        gdd: GDD,
        description: str,
    ) -> Dict[str, Any]:
        palette = spec.visual_style.palette or ["#0a0a2e", "#6366f1", "#22c55e", "#f43f5e", "#ffffff"]
        visual_pack = get_visual_pack(spec.visual_style.visual_pack) or {}
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
            "visual_pack": spec.visual_style.visual_pack or "none",
            "render_style_intensity": spec.visual_style.render_style_intensity or "balanced",
            "font_family": visual_pack.get("fontFamily", "system-ui, sans-serif"),
            "hud_style": visual_pack.get("hudStyle", "clean_cards"),
            "button_style": visual_pack.get("buttonStyle", "rounded_button"),
            "background_style": visual_pack.get("backgroundStyle", spec.visual_style.background),
            "motion_style": visual_pack.get("motionStyle", "responsive"),
            "particle_style": visual_pack.get("particleStyle", "minimal"),
            "accent_shapes": ", ".join(visual_pack.get("accentShapes", [])) or "none",
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
            "entities_desc": self._build_entities_desc(spec.entities),
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
            "input_map": self._format_input_map_lines(gdd.input_map),
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
            "session_length": spec.session_length or "short_bursts",
            "progression_shape": spec.progression_shape or "score_chase",
            "reward_loop": spec.reward_loop or "none",
            "signature_moment": spec.signature_moment or "none",
            "target_audience": spec.target_audience or "general mobile players",
            "tone": spec.tone or "readable and playful",
            "reference_style": spec.reference_style or "none",
            "complexity_budget": spec.complexity_budget or self._resolve_generation_tier(spec),
            "teaching_mode": spec.teaching_mode or "none",
            "comedy_device": spec.comedy_device or "none",
            "design_goals": self._format_compact_list(spec.design_goals, max_items=4, max_chars=64),
            "level_structure_json": self._compact_json_block(gdd.level_structure),
            "phase_plan_json": self._compact_json_block(gdd.phase_plan),
            "reward_plan_json": self._compact_json_block(gdd.reward_plan),
            "tutorial_beats_json": self._compact_json_block(gdd.tutorial_beats),
            "signature_interactions_json": self._compact_json_block(gdd.signature_interactions),
            "feedback_moments_json": self._compact_json_block(gdd.feedback_moments),
            "failure_recovery_json": self._compact_json_block(gdd.failure_recovery_plan),
            "max_speed": max_speed,
            "spawn_decay_formula": (
                f"max({min_spawn_interval}, {spawn_interval} - elapsed_s * 8)"
                if spawn_interval > 0
                else "not_applicable"
            ),
            "min_spawn_interval": min_spawn_interval,
            "combo_desc": "none",
            "invincible_frames": 45 if spec.game_type not in ("puzzle", "educational") else 0,
            "ui_score_x": score_layout.get("x", 16),
            "ui_score_y": score_layout.get("y", 36),
            "ui_font_size": ui_font_size,
            "state_flow": self._format_state_flow(gdd.state_machine),
            "special_rules_list": self._format_special_rules(spec),
        }


    @staticmethod
    def _json_block(value: Any) -> str:
        if value is None or value == "":
            return "[]"
        if value == {}:
            return "{}"
        if value == []:
            return "[]"
        return json.dumps(value, ensure_ascii=False, indent=2)


    @classmethod
    def _compact_text_items(
        cls,
        items: List[str],
        *,
        max_items: int,
        max_chars: int,
    ) -> tuple[List[str], int]:
        normalized = [
            cls._truncate_prompt_snippet(str(item), max_chars=max_chars)
            for item in items
            if str(item or "").strip()
        ]
        visible = normalized[:max_items]
        overflow = max(0, len(normalized) - len(visible))
        return visible, overflow


    @classmethod
    def _format_compact_list(
        cls,
        items: List[str],
        *,
        max_items: int = 4,
        max_chars: int = 72,
    ) -> str:
        visible, overflow = cls._compact_text_items(items, max_items=max_items, max_chars=max_chars)
        if not visible:
            return "none"
        if overflow:
            visible.append(f"+{overflow} more")
        return "; ".join(visible)


    @classmethod
    def _compact_json_value(
        cls,
        value: Any,
        *,
        max_items: int = 4,
        max_chars: int = 64,
        depth: int = 0,
        max_depth: int = 2,
    ) -> Any:
        if isinstance(value, dict):
            if depth >= max_depth:
                keys = [str(key) for key in value.keys()]
                return cls._format_compact_list(keys, max_items=max_items, max_chars=max_chars)
            items = list(value.items())
            compacted = {
                str(key): cls._compact_json_value(
                    child,
                    max_items=max_items,
                    max_chars=max_chars,
                    depth=depth + 1,
                    max_depth=max_depth,
                )
                for key, child in items[:max_items]
            }
            overflow = len(items) - min(len(items), max_items)
            if overflow:
                compacted["_omitted_keys"] = f"+{overflow} more"
            return compacted
        if isinstance(value, list):
            visible = [
                cls._compact_json_value(
                    item,
                    max_items=max_items,
                    max_chars=max_chars,
                    depth=depth + 1,
                    max_depth=max_depth,
                )
                for item in value[:max_items]
            ]
            overflow = len(value) - len(visible)
            if overflow:
                visible.append(f"+{overflow} more")
            return visible
        if isinstance(value, str):
            return cls._truncate_prompt_snippet(value, max_chars=max_chars)
        return value


    @classmethod
    def _compact_json_block(
        cls,
        value: Any,
        *,
        max_items: int = 4,
        max_chars: int = 320,
    ) -> str:
        if value is None or value == "":
            return "[]"
        if value == {}:
            return "{}"
        if value == []:
            return "[]"
        compacted = cls._compact_json_value(value, max_items=max_items)
        compact_text = json.dumps(compacted, ensure_ascii=False, indent=2)
        if len(compact_text) <= max_chars:
            return compact_text
        return cls._truncate_prompt_snippet(
            json.dumps(compacted, ensure_ascii=False, separators=(",", ": ")),
            max_chars=max_chars,
        )


    def _build_entities_desc(
        self,
        entities: List[Any],
        *,
        max_items: int = 6,
    ) -> str:
        if not entities:
            return "  - none"
        visible: List[str] = []
        for entity in entities[:max_items]:
            extras: List[str] = [f"shape={entity.shape or 'auto'}", f"color={entity.color or 'auto'}"]
            if entity.spawn_rate:
                extras.append(f"spawn={entity.spawn_rate}ms")
            visible.append(f"  - {entity.name} ({entity.role}): {', '.join(extras)}")
        overflow = len(entities) - len(visible)
        if overflow:
            visible.append(f"  - +{overflow} more entities")
        return "\n".join(visible)


    @classmethod
    def _format_input_map_lines(
        cls,
        input_map: Dict[str, str],
        *,
        max_items: int = 4,
    ) -> str:
        if not input_map:
            return "  touchstart -> start or primary interaction"
        pairs = [f"{event} -> {action}" for event, action in input_map.items()]
        visible, overflow = cls._compact_text_items(pairs, max_items=max_items, max_chars=72)
        lines = [f"  {item}" for item in visible]
        if overflow:
            lines.append(f"  +{overflow} more inputs")
        return "\n".join(lines)


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


    @staticmethod
    def _truncate_prompt_snippet(text: str, *, max_chars: int = 180) -> str:
        normalized = re.sub(r"\s+", " ", (text or "").strip())
        if len(normalized) <= max_chars:
            return normalized
        return normalized[: max_chars - 3].rstrip() + "..."


    @classmethod
    def _build_distinctive_loop_hint(
        cls,
        spec: GameSpec,
        request_text: str,
        runtime_profile: Optional[str],
    ) -> str:
        runtime_profile = normalize_runtime_profile_id(runtime_profile)
        generation_tier = cls._resolve_generation_tier(spec)
        diversity_rules = [
            rule for rule in (spec.special_rules or [])
            if "distinctive gameplay loop" in rule.lower() or "avoid the stock" in rule.lower()
        ]
        wants_distinctive_loop = generation_tier == "showcase" or bool(diversity_rules)
        if runtime_profile in {
            "casual_arcade",
            "casual_action",
            "casual_arcade_burst",
            "casual_arcade_orbit",
            "casual_arcade_rescue",
            "casual_action_arena",
            "casual_action_survival",
        }:
            wants_distinctive_loop = True

        lines: List[str] = []
        if request_text.strip():
            lines.append(f"- Original brief anchor: {cls._truncate_prompt_snippet(request_text, max_chars=160)}")
        if wants_distinctive_loop:
            lines.append("- Prefer a distinctive loop framing instead of the stock default when it still matches the brief.")
        if generation_tier == "showcase":
            lines.append("- Showcase tier: spend extra budget on one signature mechanic framing, not generic polish alone.")
        for rule in diversity_rules[:2]:
            lines.append(f"- {rule}")
        return "\n".join(lines)


    @staticmethod
    def _strip_ui_language_line(prompt: str) -> str:
        lines = [
            line for line in (prompt or "").splitlines()
            if not line.strip().lower().startswith("- ui language:")
        ]
        return "\n".join(lines).strip()


    @staticmethod
    def _structured_design_has_ui_language(prompt: str) -> bool:
        normalized = (prompt or "").lower()
        return "ui language:" in normalized or "visible ui copy examples:" in normalized


    @classmethod
    def _strip_reference_and_special_rules(cls, prompt: str, structured_design: str) -> str:
        normalized_design = (structured_design or "").lower()
        has_reference = "reference game:" in normalized_design
        has_special_rules = "special rules:" in normalized_design

        lines = (prompt or "").splitlines()
        stripped_lines: List[str] = []
        skip_special_rule_children = False
        for line in lines:
            normalized_line = line.strip().lower()
            if has_reference and normalized_line.startswith("- reference game:"):
                continue
            if has_special_rules and normalized_line.startswith("- must preserve these special rules:"):
                skip_special_rule_children = True
                continue
            if has_special_rules and normalized_line.startswith("- special rules:"):
                continue
            if skip_special_rule_children:
                if line.startswith("  - "):
                    continue
                skip_special_rule_children = False
            stripped_lines.append(line)

        return cls._strip_empty_prompt_lines("\n".join(stripped_lines))


    def _build_design_program_block(self, spec: GameSpec, gdd: GDD) -> str:
        lines: List[str] = []
        defaultish_fields = (
            ("Session length", spec.session_length, "short_bursts"),
            ("Progression shape", spec.progression_shape, "score_chase"),
            ("Reward loop", spec.reward_loop, "none"),
            ("Signature moment", spec.signature_moment, "none"),
            ("Target audience", spec.target_audience, "general mobile players"),
            ("Tone", spec.tone, "readable and playful"),
            ("Reference style", spec.reference_style, "none"),
            ("Teaching mode", spec.teaching_mode, "none"),
            ("Comedy device", spec.comedy_device, "none"),
        )
        for label, value, default_value in defaultish_fields:
            normalized = (value or "").strip()
            if not normalized or normalized == default_value:
                continue
            lines.append(f"- {label}: {normalized}")

        complexity_budget = (spec.complexity_budget or "").strip() or self._resolve_generation_tier(spec)
        if complexity_budget == "showcase" or (spec.complexity_budget or "").strip():
            lines.append(f"- Complexity budget: {complexity_budget}")

        design_goals, goal_overflow = self._compact_text_items(
            spec.design_goals or [],
            max_items=4,
            max_chars=84,
        )
        if design_goals:
            lines.extend(["- Design goals:", *(f"  - {goal}" for goal in design_goals)])
            if goal_overflow:
                lines.append(f"  - +{goal_overflow} more goals")

        structured_sections = [
            ("Level structure", gdd.level_structure),
            ("Phase plan", gdd.phase_plan),
            ("Reward plan", gdd.reward_plan),
            ("Tutorial beats", gdd.tutorial_beats),
            ("Signature interactions", gdd.signature_interactions),
            ("Feedback moments", gdd.feedback_moments),
            ("Failure recovery plan", gdd.failure_recovery_plan),
        ]
        for label, value in structured_sections:
            if not value:
                continue
            lines.extend([f"- {label}:", self._compact_json_block(value, max_chars=360)])

        if not lines:
            return ""
        return "\n".join(
            [
                "DESIGN PROGRAM (HIGH PRIORITY):",
                *lines,
                "- Preserve this design program unless a requirement directly conflicts with it.",
            ]
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


    @staticmethod
    def _build_visual_pack_block(spec: Optional[GameSpec]) -> str:
        if spec is None:
            return ""
        pack = get_visual_pack(spec.visual_style.visual_pack)
        lines = visual_pack_direction_lines(pack)
        if not lines:
            return ""
        intensity = spec.visual_style.render_style_intensity or "balanced"
        pack_id = spec.visual_style.visual_pack or "none"
        return "\n".join([
            "VISUAL PACK DIRECTION:",
            f"- Pack id: {pack_id}",
            f"- Render style intensity: {intensity}",
            *lines,
            "- Follow this direction across HUD, buttons, overlays, backgrounds, and feedback animation.",
            "- Keep the playfield readable, but avoid generic default UI if the selected pack suggests a stronger style.",
        ])


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
        fallback_game_type: str = "casual",
    ) -> str:
        game_type = spec.game_type if spec else fallback_game_type
        source_description = spec.source_description if spec else ""
        intent_summary = spec.intent_summary if spec else ""
        generation_tier = cls._resolve_generation_tier(spec)
        lines = [
            "IMPLEMENTATION SHAPE:",
            "- Explicit subjects, colors and scenery in the original brief override ALL generic entity names, geometric shape hints, palettes and background defaults in the generated spec.",
            "- Draw requested actors and props as recognizable multi-part procedural illustrations: silhouette, interior detail, highlight/shadow and reactive motion. Primitive shapes are construction parts, never the final placeholder hero.",
            "- Deliver coherent foreground/background depth, legible HUD hierarchy, and immediate motion/particle feedback for success and damage. Keep every requested rule and control intact; visual polish must not replace functionality.",
            "- Use one canvas and one primary requestAnimationFrame loop.",
            "- Keep the whole experience coherent inside one HTML file and one shared state model.",
            "- Reuse the same controls and state machine across the whole experience instead of creating disconnected subsystems.",
        ]
        if generation_tier == "safe":
            lines.extend([
                "- Keep one main HUD and at most one overlay screen for ready/game-over or level-complete states.",
                "- Avoid scene managers, dialogue trees, worksheet generators, inventories, or parallel mini-games unless absolutely required for the core mechanic.",
                "- Choose the most engaging compact mechanic that satisfies the request and runtime contract before layering extra polish.",
            ])
        elif generation_tier == "showcase":
            lines.extend([
                "- Allow a richer presentation layer, a stronger HUD, and 2-3 linked subsystems as long as they all plug into the same loop.",
                "- Favor one signature mechanic plus one support system such as combos, waves, rescue targets, route goals, risk-reward pickups, or finale beats.",
                "- Showcase briefs may use 5-8 active entities or families when they stay legible and share the same core loop.",
                "- Spend budget on clarity, juice, pacing, progression, and memorable payoff once boot, input, restart, and visible feedback are secure.",
            ])
        else:
            lines.extend([
                "- Allow 2-4 supporting subsystems and a more expressive HUD when they improve the brief.",
                "- Standard briefs may use roughly 4-6 active entities or families when they reinforce the same mechanic.",
                "- Build beyond the minimal safe demo when the brief supports it, while keeping the loop readable and QA-friendly.",
            ])

        if game_type == "casual":
            lines.extend([
                "- Keep the round structure readable and avoid spawning multiple unrelated subsystems.",
                "- Use one main action loop, but supporting pickups, rescue targets, combo chains, chase goals, or escort targets are allowed when they share the same controls.",
            ])
        if game_type in {"puzzle", "educational"}:
            lines.extend([
                "- Keep one board or playfield, but it may support layered goals such as route building, merge progression, or timed challenge beats.",
                "- Prefer concise tap/drag interactions over modal UI sprawl.",
            ])
        if game_type == "funny":
            lines.extend([
                "- Build one memorable comic payoff or absurd loop, and allow one supporting gag system if it improves the pacing.",
                "- Keep the humor readable through gameplay, feedback, and staging rather than long text setup.",
            ])
        if cls._looks_like_educational_request(request_text, source_description, intent_summary):
            lines.extend([
                "- For classroom or knowledge-check requests, build one touch-friendly challenge flow instead of a lesson plan or long teaching document.",
                "- Keep the challenge set compact, such as 3-5 levels or prompts on one shared board/layout.",
                "- Use short player-visible prompts and immediate feedback instead of long explanatory text blocks.",
            ])
        return "\n".join(lines)


    @classmethod
    def _should_include_reference_skeleton(
        cls,
        spec: GameSpec,
        *,
        request_text: str,
        skeleton: Optional[str],
        design_program_block: str,
    ) -> bool:
        normalized_skeleton = (skeleton or "").strip()
        if not normalized_skeleton:
            return False
        generation_tier = cls._resolve_generation_tier(spec)
        if generation_tier != "safe":
            return False
        if len(normalized_skeleton) > 2400:
            return False
        return True


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


    @staticmethod
    def _resolve_layout_orientation(runtime_contract: Optional[GameRuntimeContract]) -> str:
        orientation = (
            runtime_contract.mobile_layout.orientation
            if runtime_contract and runtime_contract.mobile_layout
            else "portrait_first"
        )
        return "landscape_first" if orientation == "landscape_first" else "portrait_first"


    @staticmethod
    def _layout_reference_label(orientation: str) -> str:
        return "landscape-first" if orientation == "landscape_first" else "portrait-first"


    @staticmethod
    def _rewrite_layout_prompt_for_orientation(prompt: str, orientation: str) -> str:
        if orientation != "landscape_first":
            return prompt
        replacements = (
            ("portrait-first", "landscape-first"),
            ("portrait first", "landscape first"),
            ("portrait reference playfield", "landscape reference playfield"),
            ("portrait reference size", "landscape reference size"),
            ("portrait reference", "landscape reference"),
            ("portrait layout", "landscape layout"),
            ("portrait sizing", "landscape sizing"),
        )
        updated = prompt
        for source, target in replacements:
            updated = updated.replace(source, target)
        return updated


    @classmethod
    def _compact_mobile_layout_guardrails(
        cls,
        prompt: str,
        *,
        reference_label: str,
        canvas_w: int,
        canvas_h: int,
    ) -> str:
        keep_markers = (
            "side padding",
            "hud text",
            "overlay title",
            "restart/help text",
        )
        kept_lines: List[str] = []
        for raw_line in (prompt or "").splitlines():
            stripped = raw_line.strip()
            if not stripped.startswith("- "):
                continue
            lowered = stripped.lower()
            if any(marker in lowered for marker in keep_markers):
                kept_lines.append(stripped)

        recipe_lines = [
            "MOBILE LAYOUT IMPLEMENTATION RECIPE:",
            f"- Use a {reference_label} reference size of {canvas_w}x{canvas_h} during resize calculations.",
            "- Read both viewport width and height before deriving scale.",
            "- Compute scaleX/scaleY once, derive uiScale from Math.min(scaleX, scaleY) or an equivalent short-edge fit, then center the canvas and HUD.",
            *kept_lines,
        ]
        return cls._compose_prompt_sections(["\n".join(recipe_lines)])


    @staticmethod
    def _format_state_flow(state_machine: Dict[str, Any]) -> str:
        states = state_machine.get("states") if isinstance(state_machine, dict) else None
        if isinstance(states, list) and states:
            return " -> ".join(str(state) for state in states)
        return "boot -> ready -> playing -> game_over -> ready"


    def _build_contract_implementation_checklist(
        self,
        runtime_contract: Optional[GameRuntimeContract],
        runtime_profile: Optional[str],
    ) -> str:
        profile_value = normalize_runtime_profile_id(
            runtime_profile
            or (
                runtime_contract.runtime_profile
                if runtime_contract
                else ""
            )
        )
        input_modes = [
            str(mode).strip().lower()
            for mode in (
                runtime_contract.input.required_modes
                if runtime_contract and runtime_contract.input and runtime_contract.input.required_modes
                else ["pointer", "touch"]
            )
            if str(mode).strip()
        ]
        orientation = self._resolve_layout_orientation(runtime_contract)
        restart_required = (
            runtime_contract.gameplay.requires_restart_entry
            if runtime_contract and runtime_contract.gameplay
            else True
        )
        orientation_label = "portrait-first" if orientation == "portrait_first" else "landscape-first"
        lines: List[str] = [
            "CONTRACT IMPLEMENTATION CHECKLIST (CODE SHAPE, NOT JUST INTENT):",
            (
                "- Declare named scaleX and scaleY variables from viewport-to-reference dimensions, "
                "then compute uiScale = Math.min(scaleX, scaleY) before laying out gameplay or HUD."
            ),
            (
                f"- Keep the canvas and HUD {orientation_label}; do not rely on one unnamed `scale` value "
                "without separate width and height factors."
            ),
            "- Obtain a 2D context from the main canvas up front and drive visible gameplay through that single canvas.",
            "- During init and resize, set canvas.width and canvas.height to explicit non-zero values before the first render frame.",
            (
                "- When drawing rounded UI cards or buttons, do not chain `ctx.roundRect(...).fill()` or "
                "`ctx.roundRect(...).stroke()`; call roundRect first, then fill/stroke as separate statements."
            ),
            (
                "- If gameplay, camera, or HUD code reads viewWidth/viewHeight-style aliases, declare them from the live "
                "canvas dimensions in the same init/resize path before render, update, or spawn code uses them."
            ),
            (
                "- Every helper or property referenced from input, update, render, spawn, or scoring code must be "
                "declared before use; never invent missing methods or state accessors."
            ),
            (
                "- Do not reference bare placeholder locals such as type, line, touch, pointer, cell, or anim "
                "unless they are explicitly declared in the same scope before use."
            ),
            (
                "- If you use helper functions such as generateBackgroundLayers(), declare them before the first call, "
                "or inline the layer construction during top-level initialization."
            ),
            (
                "- When animation or effect progress belongs to an entity or cell, keep it on a declared object field "
                "such as cell.anim or particle.anim; never read a bare anim identifier unless it is explicitly declared in scope."
            ),
            "- Do not use shorthand aliases like w, h, sx, or sy unless they are declared in the same scope that reads them.",
            (
                "- The first primary interaction must immediately leave boot/ready, start play, or visibly mutate "
                "the canvas or HUD within the same frame or the next animation frame."
            ),
            (
                "- Primary canvas/document pointer or touch handlers must handle ready/boot input too; do not early-return "
                "before `playing` unless that same handler can call startGame() or switch state into `playing`."
            ),
            (
                "- Overlay buttons may complement the UX, but a first tap or pointerdown on the play surface must also "
                "dismiss the intro state and produce an immediate visible state change for runtime QA."
            ),
            (
            "- Maintain a declared gameplay state variable that can reach `playing`; transition into `playing` "
            "during auto-start or the first valid gameplay interaction instead of staying in boot/ready forever."
            ),
            "- Auto-advance boot/loading into ready without requiring a tap, and do not require two separate taps before gameplay starts.",
            "- Call resize/setup, render at least one visible first frame, and start the main requestAnimationFrame loop from top-level initialization.",
        ]
        if orientation == "portrait_first":
            lines.append(
                "- For portrait-first code shape, use `const REF_W = 360; const REF_H = 640;`, then derive `scaleX`, `scaleY`, `uiScale`, `viewWidth`, and `viewHeight` from live canvas dimensions inside resize/init."
            )
        else:
            lines.append(
                "- For landscape-first code shape, use `const REF_W = 640; const REF_H = 360;`, then derive `scaleX`, `scaleY`, `uiScale`, `viewWidth`, and `viewHeight` from live canvas dimensions inside resize/init."
            )
        if "touch" in input_modes:
            lines.append(
                "- Register gameplay touchstart, touchmove, and touchend handlers on the canvas or primary input target; "
                "prevent accidental page scrolling during active play."
            )
            lines.append(
                "- When reading touch coordinates, use touches[0] for active touches and changedTouches[0] for touchend/touchcancel; "
                "never assume touches[0] exists on release events."
            )
            lines.append(
                "- Guard every touch read with a length check, for example `const touch = (e.touches && e.touches.length ? e.touches[0] "
                ": (e.changedTouches && e.changedTouches.length ? e.changedTouches[0] : null)); if (!touch) return;`."
            )
            lines.append(
                "- Prefer a shared helper such as `function getInputPoint(e) { const point = (e.touches && e.touches.length ? e.touches[0] : (e.changedTouches && e.changedTouches.length ? e.changedTouches[0] : e)); if (!point || point.clientX == null || point.clientY == null) return null; return { x: point.clientX, y: point.clientY }; }` and call it from every touchstart/touchmove/touchend handler."
            )
        if "pointer" in input_modes:
            lines.append(
                "- Register gameplay pointerdown, pointermove, and pointerup handlers on the canvas or primary input target."
            )
        if restart_required:
            lines.append(
                "- Provide an explicit restart entry such as restartGame(), resetGame(), or restart() that returns "
                "terminal states back into ready or playing."
            )
        if profile_value.startswith("casual_lane"):
            lines.append(
                "- For lane games, keep lane positions in declared data and compute x from lane index with a declared helper "
                "or array lookup; never call an undefined method like player.laneX()."
            )
        if profile_value.startswith("puzzle_grid"):
            lines.append(
                "- Grid puzzle interaction must support direct touch on the board; pointer support may complement touch, "
                "but touch handlers are mandatory."
            )
            lines.append(
                "- Drag or selection state such as dragStartCell, dragTarget, selectedCell, or hoveredCell must either be "
                "initialized to a safe object shape like `{ active: false, row: -1, col: -1 }` before the loop starts "
                "or be guarded before every property access."
            )
            lines.append(
                "- Initialize a full rectangular grid before scanning for matches or neighbors, then read cells through "
                "named locals such as `const rowBucket = grid[row]; const cell = rowBucket && rowBucket[col];`; never "
                "access `grid[row][col].prop` directly without first proving both the row bucket and cell exist."
            )
            lines.append(
                "- Define a safe accessor like `function getCell(grid, row, col) { const rowBucket = grid[row]; return rowBucket ? rowBucket[col] : null; }` before any match-finding, merge, gravity, or hint "
                "logic, and route every read of `cell.type`, `cell.fruit`, `cell.anim`, or neighbor cells through that "
                "helper instead of raw `grid[row][col]` indexing."
            )
            lines.append(
                "- The first valid tap on a puzzle cell must cause an immediate visible board-state change such as a selection highlight, "
                "focus ring, hint pulse, or committed swap; never use no-op selection toggles."
            )
        return "\n".join(lines)


    @staticmethod
    def _format_compact_contract_items(items: List[str], *, max_items: int) -> str:
        normalized = [str(item).strip() for item in (items or []) if str(item).strip()]
        if not normalized:
            return "none"
        if len(normalized) <= max_items:
            return ", ".join(normalized)
        remaining = len(normalized) - max_items
        return ", ".join(normalized[:max_items]) + f", +{remaining} more"


    @staticmethod
    def _resolve_bundle_layer_keys(prompt_bundle_snapshot: Optional[Dict[str, Any]]) -> str:
        resolved_prompts = ((prompt_bundle_snapshot or {}).get("layers") or {}).get("resolved_prompts")
        if not isinstance(resolved_prompts, dict):
            return ""
        keys = [str(key).strip() for key in resolved_prompts.keys() if str(key).strip()]
        return ", ".join(keys[:8])


    @staticmethod
    def _strip_empty_prompt_lines(prompt: str) -> str:
        cleaned: List[str] = []
        for raw_line in (prompt or "").splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if stripped.startswith("- ") and stripped.endswith(":"):
                continue
            cleaned.append(line)

        compacted: List[str] = []
        previous_blank = False
        for line in cleaned:
            is_blank = not line.strip()
            if is_blank and previous_blank:
                continue
            compacted.append(line)
            previous_blank = is_blank
        return "\n".join(compacted).strip()


    @classmethod
    def _rewrite_profile_few_shot_for_orientation(cls, prompt: str, orientation: str) -> str:
        normalized = (prompt or "").strip()
        if not normalized:
            return ""
        if orientation != "landscape_first":
            return normalized

        replacements = (
            ("centered portrait canvas", "centered landscape canvas"),
            ("portrait canvas", "landscape canvas"),
            ("portrait playfield", "landscape playfield"),
            ("portrait-first", "landscape-first"),
            ("portrait first", "landscape first"),
            ("portrait", "landscape"),
        )
        updated = normalized
        for source, target in replacements:
            updated = updated.replace(source, target)

        if "landscape" not in updated.lower():
            updated += (
                " Adapt the same interaction model to a landscape-first playfield, "
                "wider camera framing, and side-friendly HUD placement."
            )
        return updated


    @classmethod
    def _compact_profile_few_shot(cls, prompt: str) -> str:
        normalized = (prompt or "").strip()
        if not normalized:
            return ""

        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if len(lines) <= 4 and len(normalized) <= 280:
            return normalized

        if len(lines) >= 3:
            kept_lines = lines[:3]
            if lines[-1] not in kept_lines:
                kept_lines.append(lines[-1])
            compact = "\n".join(kept_lines)
        else:
            compact = cls._truncate_prompt_snippet(normalized, max_chars=260)

        if len(compact) > 300:
            compact = cls._truncate_prompt_snippet(compact, max_chars=300)
        return compact


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
            return "Filled rounded rectangle drawn with inline canvas primitives"
        if shape_key == "diamond":
            return "Rotated square diamond drawn with Canvas path commands"
        return "Filled circle or simple geometric sprite drawn with inline canvas primitives"


    @staticmethod
    def _derive_player_init_pos(game_type: str, canvas_w: int, canvas_h: int) -> str:
        if game_type in {"casual", "funny"}:
            return f"bottom-center ({canvas_w // 2}, {canvas_h - 72})"
        return f"center ({canvas_w // 2}, {canvas_h // 2})"


    def _format_entities_yaml(
        self,
        entities: List[Any],
        *,
        fallback_speed: float,
        fallback_spawn_interval: int,
        max_entities: int = 3,
    ) -> str:
        if not entities:
            return "  - none"

        lines: List[str] = []
        for entity in entities[:max_entities]:
            shape = entity.shape or "auto"
            color = entity.color or "#ffffff"
            spawn_interval = entity.spawn_rate or fallback_spawn_interval
            lines.extend([
                f"  - name: {entity.name}",
                f"    visual: {shape} shape, color {color}",
                "    size: 32-56px",
                f"    behavior: {fallback_speed} px/s, every {spawn_interval} ms",
                f"    effect: {'damage player' if entity.role in ('obstacle', 'enemy') else 'award score'}",
            ])
        overflow = len(entities) - min(len(entities), max_entities)
        if overflow:
            lines.append(f"  - note: +{overflow} more {entities[0].role} entities")
        return "\n".join(lines)


    @classmethod
    def _format_input_map_yaml(cls, input_map: Dict[str, str], *, max_items: int = 4) -> str:
        if not input_map:
            return "  touchstart: start or primary interaction"
        pairs = [f"{event}: {action}" for event, action in input_map.items()]
        visible, overflow = cls._compact_text_items(pairs, max_items=max_items, max_chars=72)
        lines = [f"  {item}" for item in visible]
        if overflow:
            lines.append(f"  note: +{overflow} more inputs")
        return "\n".join(lines)


    @classmethod
    def _format_special_rules(cls, spec: GameSpec, *, max_items: int = 5) -> str:
        rules: List[str] = []
        if spec.special_rules:
            rules.extend(f"- {rule}" for rule in spec.special_rules)
        if spec.reference_game:
            rules.append(f"- Reference game inspiration: {spec.reference_game}")
        if spec.visual_style.effects:
            rules.extend(f"- Visual effect: {effect}" for effect in spec.visual_style.effects)
        if spec.platform_constraints.platform:
            rules.append(f"- Target platform: {spec.platform_constraints.platform}")
        visible, overflow = cls._compact_text_items(rules, max_items=max_items, max_chars=84)
        if overflow:
            visible.append(f"- +{overflow} more rules")
        return "\n".join(visible) if visible else "- none"
