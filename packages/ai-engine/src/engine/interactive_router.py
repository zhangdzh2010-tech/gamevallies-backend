"""Science/tool template router: HIT / SOFT / MISS.

MISS is a successful decision to run full logic_generate, not a failure.
Never force-fit a family when the brief is scientifically ambiguous.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .interactive_families import FAMILIES, RECIPES, Family, Recipe, normalize_brief

HIT_MIN_SCORE = 4.0
SOFT_MIN_SCORE = 2.0
# A rival family this close means the interaction type is ambiguous → MISS.
AMBIGUITY_MARGIN = 1.5


@dataclass(frozen=True)
class RouteDecision:
    route: str  # HIT | SOFT | MISS
    family_id: Optional[str] = None
    recipe_id: Optional[str] = None
    subject: Optional[str] = None
    confidence: float = 0.0
    reason: str = ""
    scores: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["template_route"] = self.route
        return payload

    @property
    def uses_short_path(self) -> bool:
        return self.route in {"HIT", "SOFT"} and bool(self.family_id) and bool(self.recipe_id)


def _recipe_score(recipe: Recipe, blob: str) -> float:
    hits = recipe.keyword_hits(blob)
    if not hits:
        return 0.0
    # Distinct recipe keywords beat a single shared family word.
    return float(hits * 2 + (1 if recipe.subject in blob or _subject_alias(recipe.subject, blob) else 0))


def _family_score(family: Family, blob: str) -> float:
    return float(family.keyword_hits(blob))


def _subject_alias(subject: str, blob: str) -> bool:
    aliases = {
        "physics": ("物理", "physics", "力学", "电学", "光学"),
        "chem": ("化学", "chem", "气体", "反应"),
        "bio": ("生物", "bio", "细胞", "酶", "种群", "渗透", "光合"),
    }
    return any(token in blob for token in aliases.get(subject, ()))


def route_interactive_template(
    kind: str,
    brief: str,
    *,
    variation_seed: str = "",
) -> RouteDecision:
    """Map a science/tool brief onto HIT / SOFT / MISS.

    ``variation_seed`` is accepted for telemetry/diversity callers; routing
    itself is content-driven so the same experiment stays in the same family.
    """
    del variation_seed  # routing is not seed-dependent
    blob = normalize_brief(brief)
    if kind not in {"science", "tool"}:
        return RouteDecision(route="MISS", reason="not_interactive_kind")
    # Tools share the shell only when the brief is clearly a formula/model
    # family. Counters and converters stay on full generate.

    recipe_scores: List[Tuple[float, Recipe]] = []
    for recipe in RECIPES.values():
        score = _recipe_score(recipe, blob)
        if score > 0:
            recipe_scores.append((score, recipe))
    recipe_scores.sort(key=lambda item: (-item[0], item[1].id))

    family_scores: Dict[str, float] = {
        family_id: _family_score(family, blob) for family_id, family in FAMILIES.items()
    }
    if recipe_scores:
        for score, recipe in recipe_scores:
            family_scores[recipe.family_id] = family_scores.get(recipe.family_id, 0.0) + score

    ranked_families = sorted(family_scores.items(), key=lambda item: (-item[1], item[0]))
    best_family_id, best_family_score = ranked_families[0] if ranked_families else ("", 0.0)
    second_family_score = ranked_families[1][1] if len(ranked_families) > 1 else 0.0

    best_recipe = recipe_scores[0][1] if recipe_scores else None
    best_recipe_score = recipe_scores[0][0] if recipe_scores else 0.0
    second_recipe_score = recipe_scores[1][0] if len(recipe_scores) > 1 else 0.0

    score_map = {family_id: round(score, 3) for family_id, score in family_scores.items() if score}

    competing_families = {
        recipe.family_id
        for score, recipe in recipe_scores
        if score >= SOFT_MIN_SCORE
    }
    # Conflicting interaction families: do not force-fit a single model.
    if (
        len(competing_families) >= 2
        and (best_recipe_score - second_recipe_score) < HIT_MIN_SCORE
    ) or (
        best_family_score >= SOFT_MIN_SCORE
        and second_family_score >= SOFT_MIN_SCORE
        and (best_family_score - second_family_score) < AMBIGUITY_MARGIN
        and best_recipe_score < HIT_MIN_SCORE
    ):
        return RouteDecision(
            route="MISS",
            confidence=best_family_score,
            reason="ambiguous_family",
            scores=score_map,
        )

    if best_recipe and best_recipe_score >= HIT_MIN_SCORE and (best_recipe_score - second_recipe_score) >= 1.0:
        return RouteDecision(
            route="HIT",
            family_id=best_recipe.family_id,
            recipe_id=best_recipe.id,
            subject=best_recipe.subject,
            confidence=best_recipe_score,
            reason="recipe_keyword_match",
            scores=score_map,
        )

    if kind == "tool":
        return RouteDecision(
            route="MISS",
            confidence=best_recipe_score,
            reason="tool_requires_strong_recipe",
            scores=score_map,
        )

    if best_recipe and best_recipe_score >= SOFT_MIN_SCORE:
        return RouteDecision(
            route="SOFT",
            family_id=best_recipe.family_id,
            recipe_id=best_recipe.id,
            subject=best_recipe.subject,
            confidence=best_recipe_score,
            reason="partial_recipe_match",
            scores=score_map,
        )

    if best_family_id and best_family_score >= SOFT_MIN_SCORE:
        fallback_recipe = next((r for r in RECIPES.values() if r.family_id == best_family_id), None)
        if fallback_recipe is None:
            return RouteDecision(route="MISS", reason="family_without_recipe", scores=score_map)
        return RouteDecision(
            route="SOFT",
            family_id=best_family_id,
            recipe_id=fallback_recipe.id,
            subject=fallback_recipe.subject,
            confidence=best_family_score,
            reason="family_match_default_recipe",
            scores=score_map,
        )

    return RouteDecision(
        route="MISS",
        confidence=best_family_score,
        reason="no_family_match",
        scores=score_map,
    )
