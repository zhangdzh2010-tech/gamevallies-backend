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
from typing import Any, Dict

from ..api.models import (
    CanvasConfig,
    CollisionConfig,
    GDD,
    GameSpec,
    NumericsConfig,
)
from ..config.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Numeric baseline per game type
# ---------------------------------------------------------------------------

GAME_TYPE_NUMERICS: Dict[str, Dict[str, Any]] = {
    "dodge": {
        "player_speed": 8.0,
        "base_obstacle_speed": 3.0,
        "speed_formula": "base + base * 0.02 * Math.pow(elapsed_s, 1.1)",
        "spawn_interval_ms": 900,
        "score_per_second": 1,
        "score_per_collect": 10,
        "expected_survival_s": 60,
    },
    "runner": {
        "player_speed": 6.0,
        "base_obstacle_speed": 5.0,
        "speed_formula": "base + base * 0.015 * elapsed_s",
        "spawn_interval_ms": 1200,
        "score_per_second": 2,
        "score_per_collect": 5,
        "expected_survival_s": 75,
    },
    "platformer": {
        "player_speed": 5.0,
        "base_obstacle_speed": 0.0,
        "speed_formula": "base",
        "spawn_interval_ms": 0,
        "score_per_second": 0,
        "score_per_collect": 10,
        "expected_survival_s": 90,
    },
    "shooter": {
        "player_speed": 4.0,
        "base_obstacle_speed": 2.0,
        "speed_formula": "base + 0.01 * elapsed_s",
        "spawn_interval_ms": 1500,
        "score_per_second": 0,
        "score_per_collect": 20,
        "expected_survival_s": 60,
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
    "rhythm": {
        "player_speed": 0.0,
        "base_obstacle_speed": 4.0,
        "speed_formula": "base",
        "spawn_interval_ms": 600,
        "score_per_second": 0,
        "score_per_collect": 10,
        "expected_survival_s": 90,
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

    async def design(self, spec: GameSpec) -> GDD:
        """Main entry point. Returns a fully-specified GDD."""
        canvas = self._build_canvas(spec)
        numerics = self._derive_numerics(spec)
        collision = self._build_collision(spec)
        ui_layout = self._build_ui_layout(spec, canvas)
        input_map = self._build_input_map(spec)
        state_machine = self._build_state_machine(spec)

        return GDD(
            canvas=canvas,
            numerics=numerics,
            collision=collision,
            ui_layout=ui_layout,
            input_map=input_map,
            state_machine=state_machine,
            raw_description=self._compose_raw_description(spec),
        )

    # ------------------------------------------------------------------
    # Canvas
    # ------------------------------------------------------------------

    def _build_canvas(self, spec: GameSpec) -> CanvasConfig:
        platform = spec.platform_constraints.platform
        if platform == "wechat_webview":
            return CanvasConfig(width=360, height=640, dpr_adaptive=True, target_fps=60)
        return CanvasConfig(width=390, height=693, dpr_adaptive=True, target_fps=60)

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
        # Puzzle and rhythm don't use lives-based collision
        if spec.game_type in ("puzzle", "rhythm"):
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
        if spec.game_type == "puzzle":
            return {
                "labels": labels,
                "score": {
                    "x": 16,
                    "y": 32,
                    "font": "bold 16px Arial",
                    "align": "left",
                    "label": labels.get("objective", labels["score"]),
                },
                "lives": {
                    "x": canvas.width - 16,
                    "y": 32,
                    "font": "bold 16px Arial",
                    "align": "right",
                    "label": labels.get("level", labels["lives"]),
                },
                "game_over_overlay": {
                    "title": {
                        "x": canvas.width // 2,
                        "y": canvas.height // 2 - 36,
                        "font": "bold 32px Arial",
                        "label": labels.get("completed", labels["game_over"]),
                    },
                    "score": {
                        "x": canvas.width // 2,
                        "y": canvas.height // 2 + 14,
                        "font": "18px Arial",
                        "label": labels.get("objective", labels["score"]),
                    },
                    "restart": {
                        "x": canvas.width // 2,
                        "y": canvas.height // 2 + 52,
                        "font": "16px Arial",
                        "label": labels["restart"],
                    },
                },
            }
        return {
            "labels": labels,
            "score": {
                "x": 16,
                "y": 32,
                "font": "bold 16px Arial",
                "align": "left",
                "label": labels["score"],
            },
            "lives": {
                "x": canvas.width - 16,
                "y": 32,
                "font": "bold 16px Arial",
                "align": "right",
                "label": labels["lives"],
            },
            "game_over_overlay": {
                "title": {
                    "x": canvas.width // 2,
                    "y": canvas.height // 2 - 36,
                    "font": "bold 32px Arial",
                    "label": labels["game_over"],
                },
                "score": {
                    "x": canvas.width // 2,
                    "y": canvas.height // 2 + 14,
                    "font": "18px Arial",
                    "label": labels["score"],
                },
                "restart": {
                    "x": canvas.width // 2,
                    "y": canvas.height // 2 + 52,
                    "font": "16px Arial",
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

        if spec.game_type == "platformer":
            base = {
                "touchstart_left": "player_jump",
                "touchstart_right": "player_jump",
                "click_game_over": "restart",
            }
        elif spec.game_type == "puzzle":
            base = DEFAULT_PUZZLE_INPUT_MAP.copy()
        elif spec.game_type == "rhythm":
            base = {
                "touchstart": "tap_action",
                "click_game_over": "restart",
            }
        return base

    @staticmethod
    def _build_state_machine(spec: GameSpec) -> Dict[str, Any]:
        if spec.game_type == "puzzle":
            return DEFAULT_PUZZLE_STATE_MACHINE.copy()
        return DEFAULT_STATE_MACHINE.copy()
