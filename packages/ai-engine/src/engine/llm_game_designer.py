"""LLM Game Designer – Pass 1 of two-pass generation.

Uses a fast LLM model (e.g. Haiku) to enrich the rule-based GDD with:
  - Level design (enemy counts, speeds, spawn patterns per level)
  - Enemy behavior patterns (zigzag, homing, patrol, etc.)
  - Difficulty curve parameters (ramp formula, plateau, spikes)
  - Visual effects (particles, screen shake, glow, trails)
  - Gameplay phases (warmup → normal → climax → boss)

The enriched GDD is then passed to the CodeGenerator (Pass 2) so it
only needs to implement a pre-designed game, not design and implement
simultaneously.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from ..api.models import (
    EnrichedGDD,
    GDD,
    GameRuntimeContract,
    GameSpec,
)
from ..config.settings import settings
from ..services.llm_client import LLMClient
from .prompt_store import require_prompt

logger = logging.getLogger(__name__)

_DESIGN_SYSTEM_PROMPT = """\
You are a game designer for mobile HTML5 canvas games.
Given a game spec and base GDD, produce an enriched design document in JSON format.

Return ONLY valid JSON with these keys:
{
  "level_design": [
    {"level": 1, "enemy_count": 3, "speed_mult": 0.7, "spawn_pattern": "sequential", "duration_s": 15},
    ...
  ],
  "enemy_behaviors": [
    {"name": "zigzag", "description": "Move in a zigzag pattern across the screen", "applies_to": ["obstacle"]},
    ...
  ],
  "difficulty_curve_params": {
    "ramp_formula": "base + base * 0.03 * elapsed_s",
    "plateau_at_s": 30,
    "spike_at_s": 50,
    "max_speed_mult": 2.5
  },
  "visual_effects": ["particle_trail_on_player", "screen_shake_on_hit", "flash_on_collect"],
  "gameplay_phases": [
    {"name": "warmup", "duration_s": 10, "description": "Slow enemies, wide spacing"},
    {"name": "normal", "duration_s": 25, "description": "Standard difficulty"},
    {"name": "climax", "duration_s": 15, "description": "Fast enemies, tight spacing"},
    {"name": "finale", "duration_s": 10, "description": "Boss or survival challenge"}
  ]
}

Rules:
- Keep designs implementable in a single HTML file with Canvas 2D.
- Match the game type and core mechanic from the spec.
- Level count: 3-5 for action games, 5-10 for puzzle games, 1 for endless games.
- Total expected play time: 45-90 seconds for arcade, 120-180 for puzzle.
- Do NOT include code. Only design parameters and descriptions.
"""

_DESIGN_USER_TEMPLATE = """\
## Game Spec
- Type: {game_type}
- Core Mechanic: {core_mechanic}
- Theme: {theme}
- Difficulty: {difficulty}
- Win Condition: {win_condition}
- Entities: {entities}
- Special Rules: {special_rules}

## Base GDD
- Canvas: {canvas_w}x{canvas_h}
- Player Speed: {player_speed}
- Obstacle Speed: {obstacle_speed}
- Spawn Interval: {spawn_interval}ms
- Speed Formula: {speed_formula}
- State Machine: {state_machine}

Generate an enriched design document for this game.
"""


class LLMGameDesigner:
    """Pass 1: LLM generates detailed design from spec + base GDD."""

    def __init__(self) -> None:
        self._client = LLMClient()

    async def design(
        self,
        spec: GameSpec,
        gdd: GDD,
        runtime_contract: Optional[GameRuntimeContract] = None,
    ) -> EnrichedGDD:
        """Generate EnrichedGDD. Falls back to plain GDD on any failure."""
        if not self._client.is_enabled():
            logger.debug("LLM not enabled – skipping design pass")
            return self._as_enriched(gdd)

        prompt = self._build_prompt(spec, gdd)

        try:
            system = self._get_system_prompt()
            token_budget = settings.LLM_DESIGN_PASS_MAX_TOKENS
            raw = await self._client.complete_with_truncation_retry(
                max_tokens=settings.LLM_DESIGN_PASS_MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                step_key="llm_design.enrich",
                stage="designing",
                prefer_fast=True,
                request_timeout_s=settings.LLM_DESIGN_PASS_TIMEOUT_S,
                overall_timeout_s=settings.LLM_DESIGN_PASS_TIMEOUT_S,
                response_size_hint="large",
                context_scope="task",
                compression_policy="design_enrich",
                truncation_retry_attempts=1,
                truncation_retry_increment=1024,
                truncation_retry_max_tokens=max(token_budget, 6144),
                timeout_retry_attempts=1,
                timeout_retry_increment_s=30,
                timeout_retry_max_s=max(settings.LLM_DESIGN_PASS_TIMEOUT_S, 90),
            )
            return self._parse_response(raw, gdd)
        except Exception as exc:
            logger.warning("LLM design pass failed, falling back to base GDD: %s", exc)
            return self._as_enriched(gdd)

    def _build_prompt(self, spec: GameSpec, gdd: GDD) -> str:
        entities_str = ", ".join(
            f"{e.name}({e.role})" for e in (spec.entities or [])
        ) or "none"
        special_rules_str = "; ".join(spec.special_rules or []) or "none"
        core_mechanic = (
            spec.core_mechanics[0].name
            if spec.core_mechanics
            else spec.game_type
        )
        state_machine_str = json.dumps(gdd.state_machine) if gdd.state_machine else "{}"

        return _DESIGN_USER_TEMPLATE.format(
            game_type=spec.game_type,
            core_mechanic=core_mechanic,
            theme=spec.visual_style.theme if spec.visual_style else "default",
            difficulty=spec.difficulty_curve or "progressive",
            win_condition=spec.rules.win_condition if spec.rules else "survive",
            entities=entities_str,
            special_rules=special_rules_str,
            canvas_w=gdd.canvas.width,
            canvas_h=gdd.canvas.height,
            player_speed=gdd.numerics.player_speed,
            obstacle_speed=gdd.numerics.base_obstacle_speed,
            spawn_interval=gdd.numerics.spawn_interval_ms,
            speed_formula=gdd.numerics.speed_formula,
            state_machine=state_machine_str,
        )

    @staticmethod
    def _get_system_prompt() -> str:
        try:
            return require_prompt("prompt.llm_design_system")
        except Exception:
            return _DESIGN_SYSTEM_PROMPT

    @staticmethod
    def _parse_response(raw: str, base_gdd: GDD) -> EnrichedGDD:
        """Parse LLM JSON response into EnrichedGDD."""
        text = raw.strip()
        # Strip markdown code fences
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON from the response
            json_match = re.search(r'\{[\s\S]*\}', text)
            if json_match:
                data = json.loads(json_match.group())
            else:
                logger.warning("Could not parse LLM design response as JSON")
                return LLMGameDesigner._as_enriched(base_gdd)

        enriched = EnrichedGDD(**base_gdd.model_dump())
        enriched.level_design = data.get("level_design", [])
        enriched.enemy_behaviors = data.get("enemy_behaviors", [])
        enriched.difficulty_curve_params = data.get("difficulty_curve_params", {})
        enriched.visual_effects = data.get("visual_effects", [])
        enriched.gameplay_phases = data.get("gameplay_phases", [])
        return enriched

    @staticmethod
    def _as_enriched(gdd: GDD) -> EnrichedGDD:
        """Wrap a plain GDD as EnrichedGDD with empty enrichment fields."""
        return EnrichedGDD(**gdd.model_dump())
