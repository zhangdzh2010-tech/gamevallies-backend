"""PR-08: Deterministic range-based sampling for numerics and entity variants.

Problem this solves
-------------------
Before P1, numerics were a single fixed dict per game_type and entity
variants were drawn from a short hash-indexed list. Every casual game came
out with `player_speed=6.0`. Every puzzle run looked identical modulo the
theme preset. This sharply reduced observed output diversity.

Approach
--------
* Numerics: every knob is a `NumRange(lo, hi, step)`; we sample per knob by
  feeding sha256(variation_seed || game_type || field_name) into an
  independent `random.Random` instance. Output is deterministic and
  replayable, but varies across seeds.
* Entities: each game_type exposes an entity *pool* rather than a fixed
  triple. We pick a configurable number of entities by role, with a
  configurable mix of the generic pool to guarantee coverage when a theme
  does not have a rich named pool.

Both functions are *pure*: they mutate nothing and take all inputs via
arguments. Consumers wire them in behind the `P1_RANGE_SAMPLING_ENABLED`
feature flag; when the flag is off, old code paths are unchanged.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


# ----------------------------------------------------------------------
# Range primitive
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class NumRange:
    """Inclusive numeric range with optional discretization step.

    step=None means "continuous"; we still quantize to 2 decimals to
    avoid leaking seed-precision noise into downstream logs.
    """

    lo: float
    hi: float
    step: Optional[float] = None

    def sample(self, rng: random.Random) -> float:
        if self.hi <= self.lo:
            return round(self.lo, 4)
        if self.step is None:
            return round(rng.uniform(self.lo, self.hi), 4)
        n = int(round((self.hi - self.lo) / self.step))
        k = rng.randint(0, n)
        return round(self.lo + k * self.step, 4)


# ----------------------------------------------------------------------
# Game-type range tables
# ----------------------------------------------------------------------


# Each entry produces the same keys as the legacy GAME_TYPE_NUMERICS dict
# so the rest of the pipeline is unchanged.
GAME_TYPE_NUMERIC_RANGES: Dict[str, Dict[str, Any]] = {
    "casual": {
        "player_speed": NumRange(5.0, 7.5, step=0.25),
        "base_obstacle_speed": NumRange(2.5, 4.0, step=0.1),
        "spawn_interval_ms": NumRange(850, 1200, step=25),
        "score_per_second": NumRange(1, 2, step=1),
        "score_per_collect": NumRange(8, 15, step=1),
        "expected_survival_s": NumRange(60, 90, step=5),
        # Formula is categorical; sampled via choose_formula below.
        "speed_formula_choices": (
            "base + base * 0.012 * elapsed_s",
            "base + base * 0.015 * elapsed_s",
            "base + base * 0.018 * elapsed_s",
        ),
    },
    "puzzle": {
        "player_speed": NumRange(0.0, 0.0),
        "base_obstacle_speed": NumRange(0.0, 0.0),
        "spawn_interval_ms": NumRange(0, 0),
        "score_per_second": NumRange(0, 0),
        "score_per_collect": NumRange(30, 75, step=5),
        "expected_survival_s": NumRange(90, 180, step=10),
        "speed_formula_choices": ("0",),
    },
    "educational": {
        "player_speed": NumRange(0.0, 0.0),
        "base_obstacle_speed": NumRange(0.0, 0.0),
        "spawn_interval_ms": NumRange(0, 0),
        "score_per_second": NumRange(0, 0),
        "score_per_collect": NumRange(15, 40, step=5),
        "expected_survival_s": NumRange(60, 120, step=10),
        "speed_formula_choices": ("0",),
    },
    "funny": {
        "player_speed": NumRange(4.5, 6.5, step=0.25),
        "base_obstacle_speed": NumRange(2.2, 3.4, step=0.1),
        "spawn_interval_ms": NumRange(950, 1300, step=25),
        "score_per_second": NumRange(1, 2, step=1),
        "score_per_collect": NumRange(10, 18, step=1),
        "expected_survival_s": NumRange(55, 85, step=5),
        "speed_formula_choices": (
            "base + base * 0.010 * elapsed_s",
            "base + base * 0.012 * elapsed_s",
            "base + base * 0.014 * elapsed_s",
        ),
    },
}


DEFAULT_NUMERIC_RANGES: Dict[str, Any] = {
    "player_speed": NumRange(4.0, 6.5, step=0.25),
    "base_obstacle_speed": NumRange(2.0, 3.2, step=0.1),
    "spawn_interval_ms": NumRange(900, 1200, step=25),
    "score_per_second": NumRange(1, 2, step=1),
    "score_per_collect": NumRange(8, 14, step=1),
    "expected_survival_s": NumRange(60, 90, step=5),
    "speed_formula_choices": (
        "base + base * 0.010 * elapsed_s",
        "base + base * 0.015 * elapsed_s",
    ),
}


# ----------------------------------------------------------------------
# Seed helpers
# ----------------------------------------------------------------------


def _rng_for(variation_seed: str, *parts: str) -> random.Random:
    """Derive an independent PRNG per (seed, game_type, field) triple."""
    material = "|".join([variation_seed or "", *[p or "" for p in parts]])
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    seed_int = int.from_bytes(digest[:8], "big", signed=False)
    return random.Random(seed_int)


# ----------------------------------------------------------------------
# Public samplers
# ----------------------------------------------------------------------


def sample_numerics(
    variation_seed: str,
    game_type: str,
) -> Dict[str, Any]:
    """Return a numerics dict matching the legacy GAME_TYPE_NUMERICS shape.

    Output keys: player_speed, base_obstacle_speed, speed_formula,
    spawn_interval_ms, score_per_second, score_per_collect,
    expected_survival_s. All values are concrete (not ranges).
    """

    table = GAME_TYPE_NUMERIC_RANGES.get(game_type, DEFAULT_NUMERIC_RANGES)
    result: Dict[str, Any] = {}
    for key in (
        "player_speed",
        "base_obstacle_speed",
        "spawn_interval_ms",
        "score_per_second",
        "score_per_collect",
        "expected_survival_s",
    ):
        rng = _rng_for(variation_seed, game_type, key)
        rng_val = table[key].sample(rng)
        # Preserve integer-ness for fields that were historically ints.
        if key in {
            "spawn_interval_ms",
            "score_per_second",
            "score_per_collect",
            "expected_survival_s",
        }:
            rng_val = int(round(rng_val))
        result[key] = rng_val

    formula_rng = _rng_for(variation_seed, game_type, "speed_formula")
    choices: Tuple[str, ...] = table.get(
        "speed_formula_choices", DEFAULT_NUMERIC_RANGES["speed_formula_choices"]
    )
    result["speed_formula"] = formula_rng.choice(list(choices)) if choices else "0"
    return result


def sample_entities(
    variation_seed: str,
    game_type: str,
    *,
    variant_pool: List[List[Dict[str, Any]]],
    generic_pool: List[List[Dict[str, Any]]],
    mix_generic: float = 0.2,
    entity_pool_hints: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Pick one entity triple by seed, with a configurable generic-mix chance.

    * `variant_pool` is the game-type-specific pool (e.g. casual → 3 triples).
    * `generic_pool` is the universal fallback.
    * `mix_generic` is the probability that we draw from the generic pool
      instead of the themed pool on this particular seed. This is the only
      source of cross-type entity leakage; we keep it small (default 20%).
    * `entity_pool_hints` is advisory — if any hint substring matches an
      entity name, that triple is up-weighted (not forced).

    The function is deterministic given (seed, game_type, mix_generic,
    hints).
    """

    pool_rng = _rng_for(variation_seed, game_type, "entity_pool_pick")
    chose_generic = False
    if generic_pool and pool_rng.random() < max(0.0, min(1.0, mix_generic)):
        chose_generic = True

    src = generic_pool if chose_generic else (variant_pool or generic_pool)
    if not src:
        return []

    # Up-weight hint-matching triples.
    if entity_pool_hints:
        weights = [1.0] * len(src)
        normalized_hints = [h.lower().strip() for h in entity_pool_hints if h]
        for i, triple in enumerate(src):
            for entity in triple:
                name = str(entity.get("name", "")).lower()
                for hint in normalized_hints:
                    if hint and hint in name:
                        weights[i] += 2.0
                        break
        pick_rng = _rng_for(variation_seed, game_type, "entity_weighted_pick")
        total = sum(weights)
        r = pick_rng.random() * total
        acc = 0.0
        for i, w in enumerate(weights):
            acc += w
            if r <= acc:
                chosen = src[i]
                break
        else:
            chosen = src[-1]
    else:
        pick_rng = _rng_for(variation_seed, game_type, "entity_uniform_pick")
        chosen = src[pick_rng.randrange(len(src))]

    # Return a deep-enough copy so callers can mutate freely.
    return [dict(entity) for entity in chosen]
