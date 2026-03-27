"""Pre-generation validator – catches spec/contract gaps before LLM call.

Runs between contract_compose and logic_generate to prevent wasting LLM
tokens on inputs that will inevitably fail QA.
"""

from __future__ import annotations

import logging
from typing import List, Tuple

from ..api.models import GDD, GameEntity, GameRuntimeContract, GameSpec
from ..engine.code_generator import GAME_TYPE_CORE_MECHANIC_SUMMARY

logger = logging.getLogger(__name__)


class PreGenerationValidator:
    """Validates spec + GDD + contract coherence before code generation."""

    def validate(
        self,
        spec: GameSpec,
        gdd: GDD,
        runtime_contract: GameRuntimeContract,
    ) -> List[str]:
        """Return list of issue descriptions (empty = all good)."""
        issues: List[str] = []

        if not spec.entities or not any(e.role == "player" for e in spec.entities):
            issues.append("no_player_entity")

        if not (spec.core_mechanics or []):
            issues.append("no_core_mechanics")

        game_type = (spec.game_type or "").lower()
        if game_type and game_type not in GAME_TYPE_CORE_MECHANIC_SUMMARY:
            issues.append(f"unrecognized_game_type:{game_type}")

        if not (spec.rules.win_condition or "").strip():
            issues.append("no_win_condition")

        if runtime_contract.input:
            modes = getattr(runtime_contract.input, "required_modes", None) or []
            if not modes:
                issues.append("no_input_modes")

        return issues

    def auto_fix(
        self,
        spec: GameSpec,
        gdd: GDD,
        issues: List[str],
    ) -> Tuple[GameSpec, GDD]:
        """Patch obvious spec gaps and return updated copies. GDD is returned unchanged."""
        spec = spec.model_copy(deep=True)
        gdd = gdd.model_copy(deep=True)

        for issue in issues:
            try:
                if issue == "no_player_entity":
                    logger.info("Pre-gen fix: injecting default player entity")
                    spec.entities.insert(
                        0,
                        GameEntity(name="player", role="player", shape="circle", color="#6366f1"),
                    )
                elif issue == "no_win_condition":
                    logger.info("Pre-gen fix: setting default win condition to 'survive'")
                    spec.rules.win_condition = "survive"
            except Exception as exc:
                logger.warning("Pre-gen auto_fix failed for %s: %s", issue, exc)

        return spec, gdd
