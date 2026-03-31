"""Stage 03: Game Designer – GameSpec → GDD.

Converts the structured GameSpec into a fully-specified Game Design Document (GDD)
that the Code Generator can use directly without further reasoning.

Applies the numerical balance algorithm described in the Pipeline doc:
  speed = base + base * 0.02 * elapsed_s   (exponential)
  spawn_interval adjusted for entity count and difficulty
  target: average player survives 45-90 seconds
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, List

from ..api.models import (
    CanvasConfig,
    CollisionConfig,
    GDD,
    GameSpec,
    NumericsConfig,
)
from ..config.settings import settings
from .visual_pack_catalog import get_visual_pack

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Numeric baseline per game type
# ---------------------------------------------------------------------------

GAME_TYPE_NUMERICS = {
    "casual": {
        "player_speed": 6.0,
        "base_obstacle_speed": 3.2,
        "speed_formula": "base + base * 0.015 * elapsed_s",
        "spawn_interval_ms": 1000,
        "score_per_second": 1,
        "score_per_collect": 10,
        "expected_survival_s": 75,
    },
    "puzzle": {
        "player_speed": 0.0,
        "base_obstacle_speed": 0.0,
        "speed_formula": "0",
        "spawn_interval_ms": 0,
        "score_per_second": 0,
        "score_per_collect": 50,
        "expected_survival_s": 120,
    },
    "educational": {
        "player_speed": 0.0,
        "base_obstacle_speed": 0.0,
        "speed_formula": "0",
        "spawn_interval_ms": 0,
        "score_per_second": 0,
        "score_per_collect": 25,
        "expected_survival_s": 90,
    },
    "funny": {
        "player_speed": 5.5,
        "base_obstacle_speed": 2.8,
        "speed_formula": "base + base * 0.012 * elapsed_s",
        "spawn_interval_ms": 1100,
        "score_per_second": 1,
        "score_per_collect": 12,
        "expected_survival_s": 70,
    },
}

DEFAULT_NUMERICS: Dict[str, Any] = {
    "player_speed": 5.0,
    "base_obstacle_speed": 2.5,
    "speed_formula": "base + base * 0.01 * elapsed_s",
    "spawn_interval_ms": 1000,
    "score_per_second": 1,
    "score_per_collect": 10,
    "expected_survival_s": 75,
}

DIFFICULTY_MULTIPLIERS = {
    "easy": 0.7,
    "medium": 1.0,
    "hard": 1.4,
    "progressive": 1.0,  # adaptive curve handled via formula
}

DEFAULT_STATE_MACHINE = {
    "states": ["init", "playing", "paused", "game_over"],
    "initial": "init",
    "transitions": {
        "init": "playing",
        "playing": ["paused", "game_over"],
        "paused": "playing",
        "game_over": "init",
    },
}

DEFAULT_PUZZLE_STATE_MACHINE = {
    "states": ["boot", "ready", "playing", "level_complete"],
    "initial": "boot",
    "transitions": {
        "boot": "ready",
        "ready": "playing",
        "playing": ["level_complete"],
        "level_complete": "ready",
    },
}

DEFAULT_INPUT_MAP = {
    "touchmove": "player_follow_x",
    "touchstart": "player_follow_x",
    "touchend": "player_stop",
    "click_game_over": "restart",
}

DEFAULT_PUZZLE_INPUT_MAP = {
    "touchstart": "pick_component",
    "touchmove": "drag_component",
    "touchend": "drop_component",
    "tap_hint": "show_hint",
    "tap_restart": "restart_level",
}

UI_LABELS_BY_LANGUAGE = {
    "en-US": {
        "score": "Score",
        "lives": "Lives",
        "level": "Level",
        "objective": "Objective",
        "ready": "Tap to Start",
        "game_over": "Game Over",
        "completed": "Level Complete",
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


class GameDesigner:
    """Stage 03: Converts GameSpec → GDD with numerical balance."""

    async def design(self, spec: GameSpec, orientation: str = "portrait_first") -> GDD:
        """Main entry point. Returns a fully-specified GDD."""
        canvas = self._build_canvas(spec, orientation)
        numerics = self._derive_numerics(spec)
        collision = self._build_collision(spec)
        ui_layout = self._build_ui_layout(spec, canvas)
        input_map = self._build_input_map(spec)
        state_machine = self._build_state_machine(spec)
        level_structure = self._build_level_structure(spec)
        phase_plan = self._build_phase_plan(spec, level_structure)
        reward_plan = self._build_reward_plan(spec)
        tutorial_beats = self._build_tutorial_beats(spec)
        signature_interactions = self._build_signature_interactions(spec)
        feedback_moments = self._build_feedback_moments(spec)
        failure_recovery_plan = self._build_failure_recovery_plan(spec)

        return GDD(
            canvas=canvas,
            numerics=numerics,
            collision=collision,
            ui_layout=ui_layout,
            input_map=input_map,
            state_machine=state_machine,
            level_structure=level_structure,
            phase_plan=phase_plan,
            reward_plan=reward_plan,
            tutorial_beats=tutorial_beats,
            signature_interactions=signature_interactions,
            feedback_moments=feedback_moments,
            failure_recovery_plan=failure_recovery_plan,
            raw_description=self._compose_raw_description(spec),
        )

    # ------------------------------------------------------------------
    # Canvas
    # ------------------------------------------------------------------

    def _build_canvas(self, spec: GameSpec, orientation: str = "portrait_first") -> CanvasConfig:
        platform = spec.platform_constraints.platform
        if platform == "wechat_webview":
            width, height = 360, 640
        else:
            width, height = 390, 693
        if orientation == "landscape_first":
            width, height = height, width
        return CanvasConfig(width=width, height=height, dpr_adaptive=True, target_fps=60)

    @staticmethod
    def _compose_raw_description(spec: GameSpec) -> str:
        parts = [
            spec.source_description.strip(),
            spec.intent_summary.strip(),
            "; ".join(spec.special_rules).strip() if spec.special_rules else "",
            f"Reference game: {spec.reference_game.strip()}" if spec.reference_game else "",
        ]
        return "\n".join(part for part in parts if part)

    # ------------------------------------------------------------------
    # Numerical balance
    # ------------------------------------------------------------------

    def _derive_numerics(self, spec: GameSpec) -> NumericsConfig:
        base = GAME_TYPE_NUMERICS.get(spec.game_type, DEFAULT_NUMERICS).copy()

        diff_mult = DIFFICULTY_MULTIPLIERS.get(spec.difficulty_curve, 1.0)

        # Adjust speed by difficulty
        base["player_speed"] *= diff_mult
        base["base_obstacle_speed"] *= diff_mult

        # Adjust spawn interval for number of obstacle/enemy entities
        obstacle_count = sum(
            1 for e in spec.entities if e.role in ("obstacle", "enemy")
        )
        if obstacle_count > 1:
            base["spawn_interval_ms"] = max(
                300,
                int(base["spawn_interval_ms"] / math.sqrt(obstacle_count)),
            )

        # Difficulty curve: hard → faster progression
        if spec.difficulty_curve == "exponential":
            base["speed_formula"] = (
                f"{base['base_obstacle_speed']} + "
                f"{base['base_obstacle_speed']} * 0.02 * Math.pow(elapsed_s, 1.2)"
            )
        elif spec.difficulty_curve == "wave":
            base["speed_formula"] = (
                f"{base['base_obstacle_speed']} + "
                f"{base['base_obstacle_speed'] * 0.5} * Math.sin(elapsed_s * 0.1)"
            )

        return NumericsConfig(
            player_speed=round(base["player_speed"], 2),
            base_obstacle_speed=round(base["base_obstacle_speed"], 2),
            speed_formula=base["speed_formula"],
            spawn_interval_ms=base["spawn_interval_ms"],
            score_per_second=base["score_per_second"],
            score_per_collect=base["score_per_collect"],
            expected_survival_s=base["expected_survival_s"],
        )

    # ------------------------------------------------------------------
    # Collision
    # ------------------------------------------------------------------

    def _build_collision(self, spec: GameSpec) -> CollisionConfig:
        if spec.game_type in ("puzzle", "educational"):
            return CollisionConfig(method="AABB", hitbox_ratio=1.0, on_hit="score_check")
        return CollisionConfig(
            method="AABB",
            hitbox_ratio=0.8,
            on_hit="lives_minus_1",
        )

    # ------------------------------------------------------------------
    # UI layout
    # ------------------------------------------------------------------

    def _build_ui_layout(self, spec: GameSpec, canvas: CanvasConfig) -> Dict[str, Any]:
        labels = UI_LABELS_BY_LANGUAGE.get(spec.ui_language, UI_LABELS_BY_LANGUAGE["en-US"])
        visual_pack = get_visual_pack(spec.visual_style.visual_pack) or {}
        hud_font_family = visual_pack.get("fontFamily", "Arial, sans-serif")
        hud_style = visual_pack.get("hudStyle", "clean_cards")
        button_style = visual_pack.get("buttonStyle", "rounded_button")
        motion_style = visual_pack.get("motionStyle", "responsive")
        particle_style = visual_pack.get("particleStyle", "minimal")
        background_style = visual_pack.get("backgroundStyle", spec.visual_style.background)
        accent_shapes = list(visual_pack.get("accentShapes", []))
        if spec.game_type in ("puzzle", "educational"):
            return {
                "style": {
                    "visualPack": spec.visual_style.visual_pack,
                    "renderStyleIntensity": spec.visual_style.render_style_intensity,
                    "fontFamily": hud_font_family,
                    "hudStyle": hud_style,
                    "buttonStyle": button_style,
                    "motionStyle": motion_style,
                    "particleStyle": particle_style,
                    "backgroundStyle": background_style,
                    "accentShapes": accent_shapes,
                },
                "labels": labels,
                "score": {
                    "x": 16,
                    "y": 32,
                    "font": f"bold 16px {hud_font_family}",
                    "align": "left",
                    "label": labels.get("objective", labels["score"]),
                },
                "lives": {
                    "x": canvas.width - 16,
                    "y": 32,
                    "font": f"bold 16px {hud_font_family}",
                    "align": "right",
                    "label": labels.get("level", labels["lives"]),
                },
                "game_over_overlay": {
                    "title": {
                        "x": canvas.width // 2,
                        "y": canvas.height // 2 - 36,
                        "font": f"bold 32px {hud_font_family}",
                        "label": labels.get("completed", labels["game_over"]),
                    },
                    "score": {
                        "x": canvas.width // 2,
                        "y": canvas.height // 2 + 14,
                        "font": f"18px {hud_font_family}",
                        "label": labels.get("objective", labels["score"]),
                    },
                    "restart": {
                        "x": canvas.width // 2,
                        "y": canvas.height // 2 + 52,
                        "font": f"16px {hud_font_family}",
                        "label": labels["restart"],
                    },
                },
            }
        return {
            "style": {
                "visualPack": spec.visual_style.visual_pack,
                "renderStyleIntensity": spec.visual_style.render_style_intensity,
                "fontFamily": hud_font_family,
                "hudStyle": hud_style,
                "buttonStyle": button_style,
                "motionStyle": motion_style,
                "particleStyle": particle_style,
                "backgroundStyle": background_style,
                "accentShapes": accent_shapes,
            },
            "labels": labels,
            "score": {
                "x": 16,
                "y": 32,
                "font": f"bold 16px {hud_font_family}",
                "align": "left",
                "label": labels["score"],
            },
            "lives": {
                "x": canvas.width - 16,
                "y": 32,
                "font": f"bold 16px {hud_font_family}",
                "align": "right",
                "label": labels["lives"],
            },
            "game_over_overlay": {
                "title": {
                    "x": canvas.width // 2,
                    "y": canvas.height // 2 - 36,
                    "font": f"bold 32px {hud_font_family}",
                    "label": labels["game_over"],
                },
                "score": {
                    "x": canvas.width // 2,
                    "y": canvas.height // 2 + 14,
                    "font": f"18px {hud_font_family}",
                    "label": labels["score"],
                },
                "restart": {
                    "x": canvas.width // 2,
                    "y": canvas.height // 2 + 52,
                    "font": f"16px {hud_font_family}",
                    "label": labels["restart"],
                },
            },
        }

    # ------------------------------------------------------------------
    # Input map
    # ------------------------------------------------------------------

    def _build_input_map(self, spec: GameSpec) -> Dict[str, str]:
        input_method = spec.platform_constraints.input_mode
        base = DEFAULT_INPUT_MAP.copy()

        if spec.game_type in ("puzzle", "educational"):
            base = DEFAULT_PUZZLE_INPUT_MAP.copy()
        elif spec.game_type == "funny":
            base = {
                "touchstart": "tap_action",
                "click_game_over": "restart",
            }
        return base

    @staticmethod
    def _build_state_machine(spec: GameSpec) -> Dict[str, Any]:
        if spec.game_type in ("puzzle", "educational"):
            return DEFAULT_PUZZLE_STATE_MACHINE.copy()
        return DEFAULT_STATE_MACHINE.copy()

    @staticmethod
    def _build_level_structure(spec: GameSpec) -> List[Dict[str, Any]]:
        progression = spec.progression_shape or "score_chase"
        if spec.game_type == "puzzle":
            return [
                {"step": 1, "goal": "Introduce the core board rule", "twist": "single-variable solve"},
                {"step": 2, "goal": "Combine two puzzle constraints", "twist": progression},
                {"step": 3, "goal": "Deliver the final satisfying solve", "twist": spec.signature_moment or "board clear payoff"},
            ]
        if spec.game_type == "educational":
            return [
                {"step": 1, "goal": "Teach the base concept", "twist": spec.teaching_mode or "guided_exploration"},
                {"step": 2, "goal": "Practice the concept under pressure", "twist": progression},
                {"step": 3, "goal": "Apply the concept in a short mastery check", "twist": spec.reward_loop or "mastery milestone"},
            ]
        if spec.game_type == "funny":
            return [
                {"step": 1, "goal": "Set up the joke", "twist": spec.comedy_device or "surprise_punchline"},
                {"step": 2, "goal": "Escalate the joke through repeated play", "twist": progression},
                {"step": 3, "goal": "Pay off the round with a visible gag climax", "twist": spec.signature_moment or "absurd finale"},
            ]
        return [
            {"step": 1, "goal": "Teach the main loop", "twist": "immediate readable feedback"},
            {"step": 2, "goal": "Increase pressure and reward chaining", "twist": progression},
            {"step": 3, "goal": "Close the round with a strong payoff", "twist": spec.signature_moment or "short victory beat"},
        ]

    @staticmethod
    def _build_phase_plan(spec: GameSpec, level_structure: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        phases = ["opening", "midgame", "payoff"]
        plan: List[Dict[str, Any]] = []
        for phase_name, level in zip(phases, level_structure):
            plan.append({
                "phase": phase_name,
                "focus": level.get("goal"),
                "twist": level.get("twist"),
                "intensity": "low" if phase_name == "opening" else "medium" if phase_name == "midgame" else "high",
            })
        return plan

    @staticmethod
    def _build_reward_plan(spec: GameSpec) -> Dict[str, Any]:
        return {
            "loop": spec.reward_loop or "Clear a short objective, see a visible reward, then immediately want one more round.",
            "milestone": spec.signature_moment or "Reach the end-of-round payoff.",
            "sessionLength": spec.session_length or "short_bursts",
            "progressionShape": spec.progression_shape or "score_chase",
        }

    @staticmethod
    def _build_tutorial_beats(spec: GameSpec) -> List[str]:
        core = spec.intent_summary or "the main interaction"
        return [
            f"Show the player how to perform {core} within the first 5 seconds.",
            "Give one safe success moment before pressure increases.",
            "Introduce the round objective with a visible HUD reminder.",
        ]

    @staticmethod
    def _build_signature_interactions(spec: GameSpec) -> List[str]:
        interactions = [spec.signature_moment] if spec.signature_moment else []
        if spec.comedy_device:
            interactions.append(f"Comedic payoff pattern: {spec.comedy_device}")
        if spec.teaching_mode:
            interactions.append(f"Teaching pattern: {spec.teaching_mode}")
        if spec.reward_loop:
            interactions.append(f"Reward loop focus: {spec.reward_loop}")
        return [item for item in interactions if item]

    @staticmethod
    def _build_feedback_moments(spec: GameSpec) -> List[str]:
        if spec.game_type == "puzzle":
            return [
                "Immediate tile or node feedback on every valid move.",
                "Board-state clarity when progress is made.",
                "A stronger completion burst when the puzzle resolves.",
            ]
        if spec.game_type == "educational":
            return [
                "Immediate correct/incorrect feedback.",
                "Short reinforcement copy for learning progress.",
                "Clear milestone feedback when the concept is mastered.",
            ]
        if spec.game_type == "funny":
            return [
                "Exaggerated hit or reaction animation.",
                "Escalating payoff when the joke lands repeatedly.",
                "Round-end comedic reveal or reversal.",
            ]
        return [
            "Responsive feedback on every successful interaction.",
            "Visible score or progress acceleration during the midgame.",
            "A high-energy end-of-round payoff effect.",
        ]

    @staticmethod
    def _build_failure_recovery_plan(spec: GameSpec) -> Dict[str, Any]:
        return {
            "restartState": "init" if spec.game_type not in ("puzzle", "educational") else "ready",
            "hintAfterFailure": spec.game_type in ("puzzle", "educational"),
            "hintStyle": "contextual tip" if spec.game_type in ("puzzle", "educational") else "quick retry nudge",
            "preserveHighMoment": bool(spec.signature_moment),
        }
