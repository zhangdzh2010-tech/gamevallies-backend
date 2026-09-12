"""Specification extracted from pipeline_v2_runner.py."""

from __future__ import annotations
import hashlib
import re
from typing import Any, Optional
from ..api.models import GenerationTier, GameEntity, GameRuntimeContract, GameSpec, RunPipelineV2Request, SourceBundleContext
from .dialogue_engine import SlotExtractionFailure
from .pipeline_errors import PipelineExecutionError
from .requested_platform import normalize_requested_platform, requested_contract_overrides, requires_desktop
from .runtime_profile_ids import normalize_runtime_profile_id
from .visual_pack_catalog import apply_visual_pack_defaults
from .pipeline_v2_support import (
    ACTION_FOCUSED_RUNTIME_PROFILES,
    PROFILE_TO_GAME_TYPE_HINT,
    BASELINE_RUNTIME_PROFILES,
    ENTITY_BUDGET_EXPANSION_LIBRARY,
)

class PipelineV2SpecificationMixin:
    """Specification behavior; state remains owned by V2PipelineRunner."""

    async def _build_create_spec(self, request: RunPipelineV2Request) -> GameSpec:
        if request.source_spec:
            spec = request.source_spec.model_copy(deep=True)
            spec.generation_tier = self._resolve_generation_tier(
                request.generation_tier,
                request.metadata.get("generation_tier"),
                request.normalized_request.get("generation_tier"),
                request.request_context.metadata.get("generation_tier"),
                request.prompt_bundle_snapshot.layers.get("generation_tier"),
                request.runtime_contract.metadata.get("generation_tier"),
                getattr(spec, "generation_tier", None),
            )
            spec.complexity_budget = str(
                getattr(spec.generation_tier, "value", spec.generation_tier)
                or spec.complexity_budget
                or "standard"
            )
            if request.raw_user_input.strip():
                spec.source_description = request.raw_user_input.strip()
            if request.title and spec.intent_summary:
                spec.intent_summary = f"{request.title}: {spec.intent_summary}"
            spec = self._expand_spec_entities_for_budget(spec)
            return apply_visual_pack_defaults(
                normalize_requested_platform(
                    spec,
                    orientation=self._request_orientation(request),
                    metadata=getattr(request, "metadata", None),
                ),
                variation_seed=request.game_id,
            )

        description = request.raw_user_input.strip() or str(
            request.normalized_request.get("description", "")
        ).strip()
        if not description:
            raise PipelineExecutionError("raw_user_input is required", stage="spec_build")

        spec = await self._parse_spec_with_retries(
            description=description,
            stage="spec_build",
            title=request.title,
            preferred_game_type=PROFILE_TO_GAME_TYPE_HINT.get(
                (request.runtime_contract.runtime_profile or "").strip(),
            ),
            variation_seed=request.game_id,
        )
        spec.generation_tier = self._resolve_generation_tier(
            request.generation_tier,
            request.metadata.get("generation_tier"),
            request.normalized_request.get("generation_tier"),
            request.request_context.metadata.get("generation_tier"),
            request.prompt_bundle_snapshot.layers.get("generation_tier"),
            request.runtime_contract.metadata.get("generation_tier"),
        )
        spec.complexity_budget = str(getattr(spec.generation_tier, "value", spec.generation_tier) or "standard")
        spec = self._expand_spec_entities_for_budget(spec)
        return apply_visual_pack_defaults(
            normalize_requested_platform(
                spec,
                orientation=self._request_orientation(request),
                metadata=getattr(request, "metadata", None),
            ),
            variation_seed=request.game_id,
        )


    @staticmethod
    def _resolve_generation_tier(*candidates: Any) -> GenerationTier:
        for candidate in candidates:
            raw_value = getattr(candidate, "value", candidate)
            value = str(raw_value or "").strip().lower()
            if value == "safe":
                return GenerationTier.safe
            if value == "showcase":
                return GenerationTier.showcase
            if value == "standard":
                return GenerationTier.standard
        return GenerationTier.standard


    @staticmethod
    def _should_fallback_iteration_spec(exc: PipelineExecutionError) -> bool:
        if isinstance(exc.__cause__, SlotExtractionFailure):
            return True

        artifacts = getattr(exc, "artifacts", None) or []
        if any(
            isinstance(artifact, dict) and artifact.get("artifact_type") == "spec_build_diagnostics"
            for artifact in artifacts
        ):
            return True

        return "llm slot extraction returned no valid json" in str(exc).lower()


    def _rank_runtime_profile_candidates(
        self,
        spec: GameSpec,
        candidates: list[str],
    ) -> list[str]:
        ordered: list[tuple[int, int, str]] = []
        for index, profile in enumerate(candidates):
            ordered.append((self._score_runtime_profile_candidate(spec, profile), -index, profile))
        ordered.sort(reverse=True)
        return [profile for _, _, profile in ordered]


    def _score_runtime_profile_candidate(self, spec: GameSpec, profile: str) -> int:
        combined = self._profile_selection_text(spec)
        input_mode = (spec.platform_constraints.input_mode or "").lower()
        sparse = self._should_allow_profile_variation(spec)
        raw_generation_tier = getattr(spec, "generation_tier", GenerationTier.standard)
        generation_tier = str(getattr(raw_generation_tier, "value", raw_generation_tier))
        action_request = self._looks_like_action_runtime_request(spec)
        puzzle_request = self._looks_like_puzzle_runtime_request(spec)
        quiz_show_request = self._looks_like_quiz_show_runtime_request(spec)
        character_driven = self._is_character_driven_spec(spec)
        score = 0

        base_scores = (
            ("puzzle_grid", {"puzzle", "educational"}, 4),
            ("puzzle_grid_match", {"puzzle", "educational"}, 7),
            ("puzzle_grid_merge", {"puzzle"}, 7),
            ("puzzle_grid_route", {"puzzle", "educational"}, 8),
            ("casual_lane", {"casual", "funny"}, 3),
            ("casual_lane_dash", {"casual", "funny"}, 6),
            ("casual_lane_chase", {"casual", "funny"}, 6),
            ("casual_action", {"casual", "funny"}, 3),
            ("casual_action_arena", {"casual", "funny"}, 6),
            ("casual_action_survival", {"casual", "funny"}, 6),
            ("tap_challenge", {"funny", "educational"}, 4),
            ("tap_challenge_timing", {"funny", "educational"}, 7),
            ("tap_challenge_combo", {"funny", "casual"}, 7),
            ("casual_arcade", {"casual", "funny", "puzzle"}, 4),
            ("casual_arcade_burst", {"casual", "funny"}, 7),
            ("casual_arcade_orbit", {"casual", "funny"}, 7),
            ("casual_arcade_rescue", {"casual", "funny", "educational"}, 7),
        )
        for candidate, game_types, value in base_scores:
            if profile == candidate and spec.game_type in game_types:
                score += value

        if "swipe" in input_mode and profile in {"casual_lane", "casual_lane_dash", "casual_lane_chase"}:
            score += 3
        if "drag" in input_mode and profile in {
            "casual_action",
            "casual_action_arena",
            "casual_action_survival",
            "puzzle_grid",
            "puzzle_grid_route",
            "casual_arcade_orbit",
        }:
            score += 2
        if "tap" in input_mode and profile in {
            "casual_arcade",
            "casual_arcade_burst",
            "casual_arcade_rescue",
            "tap_challenge",
            "tap_challenge_timing",
            "tap_challenge_combo",
            "puzzle_grid",
            "puzzle_grid_match",
            "puzzle_grid_merge",
        }:
            score += 2

        if any(token in combined for token in ("quiz", "lesson", "teacher", "learn", "math", "word", "spell", "answer")) and profile in {"puzzle_grid", "puzzle_grid_match", "puzzle_grid_route", "tap_challenge_timing"}:
            score += 4
        if quiz_show_request and profile in {"tap_challenge_timing", "tap_challenge_combo"}:
            score += 6
        if quiz_show_request and profile.startswith("puzzle_grid"):
            score -= 2
        if any(token in combined for token in ("match", "sort", "logic", "connect", "solve")) and profile in {"puzzle_grid", "puzzle_grid_match", "puzzle_grid_route"}:
            score += 3
        if "merge" in combined and profile in {"puzzle_grid_merge", "puzzle_grid_match"}:
            score += 4
        if any(token in combined for token in ("beat", "rhythm", "music", "timing", "tempo")) and profile in {"tap_challenge", "tap_challenge_timing", "tap_challenge_combo"}:
            score += 4
        if any(token in combined for token in ("run", "race", "dash")) and profile in {"casual_lane", "casual_lane_dash"}:
            score += 4
        if any(token in combined for token in ("chase", "pursuit", "escape")) and profile in {"casual_lane_chase", "casual_action_survival"}:
            score += 4
        if any(token in combined for token in ("shoot", "projectile", "weapon", "fire")) and profile in {"casual_action", "casual_action_arena"}:
            score += 5
        if any(token in combined for token in ("avoid", "survive", "hazard")) and profile in {"casual_action", "casual_action_survival", "casual_arcade_orbit"}:
            score += 2
        if any(token in combined for token in ("collect", "rescue", "delivery", "escort")) and profile in {"casual_arcade", "casual_arcade_rescue", "casual_action", "puzzle_grid_route"}:
            score += 2
        if any(token in combined for token in ("boss", "combat", "battle", "fight", "arena")) and profile in {"casual_action", "casual_action_arena"}:
            score += 2
        if any(token in combined for token in ("funny", "comedy", "meme", "prank", "office", "goose", "slacker")) and profile in {"casual_arcade", "casual_arcade_burst", "casual_action_arena", "tap_challenge_combo"}:
            score += 3

        if action_request:
            if profile in ACTION_FOCUSED_RUNTIME_PROFILES:
                score += 5
            if profile.startswith("puzzle_grid"):
                score -= 6
        if character_driven and profile.startswith("puzzle_grid"):
            score -= 4
        if puzzle_request and not action_request and profile.startswith("puzzle_grid"):
            score += 3

        if sparse and profile in {"casual_arcade_burst", "casual_arcade_orbit", "casual_arcade_rescue", "puzzle_grid_route", "tap_challenge_combo"}:
            score += 2
        if sparse and profile in {"casual_action", "puzzle_grid", "casual_arcade"}:
            score -= 1

        if generation_tier == "safe":
            score += 2 if profile in BASELINE_RUNTIME_PROFILES else -2
        elif generation_tier == "showcase":
            score += 3 if profile not in BASELINE_RUNTIME_PROFILES else -1
            if action_request and profile in ACTION_FOCUSED_RUNTIME_PROFILES:
                score += 2

        return score


    @staticmethod
    def _should_allow_profile_variation(spec: GameSpec) -> bool:
        raw_generation_tier = getattr(spec, "generation_tier", GenerationTier.standard)
        if str(getattr(raw_generation_tier, "value", raw_generation_tier)) == "showcase":
            return True
        return any(
            "Favor a distinctive gameplay loop" in rule
            for rule in (spec.special_rules or [])
        )


    @staticmethod
    def _profile_variant_index(spec: GameSpec, *, variation_seed: Optional[str], count: int) -> int:
        if count <= 1:
            return 0
        seed = "|".join(
            item.strip()
            for item in [
                spec.game_type or "",
                spec.source_description or "",
                spec.intent_summary or "",
                spec.visual_style.theme or "",
                variation_seed or "",
            ]
            if item and item.strip()
        )
        digest = hashlib.sha256(seed.encode("utf-8")).digest()
        return int.from_bytes(digest, "big") % count


    def _compose_runtime_contract(
        self,
        *,
        base_contract: GameRuntimeContract,
        spec: GameSpec,
        runtime_profile: str,
        entrypoint: str,
    ) -> GameRuntimeContract:
        runtime_profile = normalize_runtime_profile_id(runtime_profile)
        contract = base_contract.model_copy(deep=True)
        contract.runtime_profile = runtime_profile
        requested_orientation = self._resolve_contract_orientation(base_contract, spec)
        normalized_render_api = str(spec.platform_constraints.render_api or "").strip().lower()
        allow_webgl = normalized_render_api in {"", "webgl", "webgl2", "canvas2d_or_webgl", "canvas_or_webgl"}
        requires_canvas_2d = normalized_render_api == "canvas2d"
        contract.canvas = contract.canvas.model_copy(
            update={
                "requires_canvas_2d": requires_canvas_2d,
                "allow_webgl": allow_webgl,
                "orientation": requested_orientation,
                "ui_scale_mode": "short_edge",
                "target_fps": spec.platform_constraints.target_fps or contract.canvas.target_fps,
            }
        )
        contract.input = contract.input.model_copy(update=self._profile_input_overrides(runtime_profile))
        contract.state = contract.state.model_copy(update=self._profile_state_overrides(runtime_profile))
        contract.gameplay = contract.gameplay.model_copy(update=self._profile_gameplay_overrides(runtime_profile))
        # Explicit user requirements take precedence over genre/profile defaults.
        input_overrides, extra_states = requested_contract_overrides(
            spec, orientation=requested_orientation, metadata=contract.metadata,
        )
        if input_overrides:
            contract.input = contract.input.model_copy(update=input_overrides)
        if extra_states:
            contract.state = contract.state.model_copy(update={
                "required_states": list(dict.fromkeys([*contract.state.required_states, *extra_states]))})

        contract.mobile_layout = contract.mobile_layout.model_copy(
            update={
                "orientation": contract.canvas.orientation,
                "ui_scale_mode": contract.canvas.ui_scale_mode,
            }
        )
        contract.metadata = {
            **contract.metadata,
            "entrypoint": entrypoint,
            "game_type": spec.game_type,
            "difficulty_curve": spec.difficulty_curve,
            "orientation": requested_orientation,
        }
        return contract


    @staticmethod
    def _request_orientation(request: Any) -> str | None:
        metadata = getattr(request, "metadata", None)
        contract = getattr(request, "runtime_contract", None)
        for candidate in (
            metadata.get("orientation") if isinstance(metadata, dict) else None,
            metadata.get("requested_orientation") if isinstance(metadata, dict) else None,
            getattr(getattr(contract, "mobile_layout", None), "orientation", None),
            getattr(getattr(contract, "canvas", None), "orientation", None),
            (getattr(contract, "metadata", None) or {}).get("orientation") if contract else None,
        ):
            value = str(candidate or "").strip()
            if value:
                return value
        return None

    @staticmethod
    def _resolve_contract_orientation(
        base_contract: GameRuntimeContract,
        spec: GameSpec | None = None,
    ) -> str:
        orientation = str(
            getattr(base_contract.mobile_layout, "orientation", None)
            or getattr(base_contract.canvas, "orientation", None)
            or (base_contract.metadata or {}).get("orientation")
            or "portrait_first"
        ).strip()
        if orientation in {"landscape", "landscape_first"}:
            return "landscape_first"
        if spec is not None and requires_desktop(spec, orientation=orientation, metadata=base_contract.metadata):
            return "landscape_first"
        return "portrait_first"


    def _profile_input_overrides(self, runtime_profile: str) -> dict[str, Any]:
        if runtime_profile.startswith("puzzle_grid"):
            return {
                "required_modes": ["touch"],
                "allow_mouse_fallback": True,
                "gestures": ["tap", "drag"],
            }
        if runtime_profile.startswith("casual_lane"):
            return {
                "required_modes": ["touch", "pointer"],
                "allow_mouse_fallback": True,
                "gestures": ["tap", "swipe"],
            }
        if runtime_profile.startswith("tap_challenge"):
            return {
                "required_modes": ["touch", "pointer"],
                "allow_mouse_fallback": True,
                "gestures": ["tap"],
            }
        if runtime_profile in {"casual_arcade_orbit", "casual_arcade_rescue"}:
            return {
                "required_modes": ["touch", "pointer"],
                "allow_mouse_fallback": True,
                "gestures": ["drag", "tap"],
            }
        return {
            "required_modes": ["touch", "pointer"],
            "allow_mouse_fallback": True,
            "gestures": ["tap", "drag"],
        }


    def _profile_state_overrides(self, runtime_profile: str) -> dict[str, Any]:
        if runtime_profile.startswith("puzzle_grid"):
            return {
                "required_states": ["boot", "ready", "playing", "level_complete"],
                "required_flags": ["levelComplete", "currentLevel", "showHint"],
                "restartable": True,
            }
        return {
            "required_states": ["boot", "ready", "playing", "game_over"],
            "required_flags": ["score", "gameOver"],
            "restartable": True,
        }


    def _profile_gameplay_overrides(self, runtime_profile: str) -> dict[str, Any]:
        if runtime_profile.startswith("puzzle_grid"):
            primary_goal = "grid_completion"
            if runtime_profile == "puzzle_grid_merge":
                primary_goal = "merge_progression"
            elif runtime_profile == "puzzle_grid_route":
                primary_goal = "route_completion"
            return {
                "requires_player_entity": False,
                "requires_scoring": False,
                "requires_terminal_state": True,
                "requires_restart_entry": True,
                "terminal_state_aliases": [
                    "level_complete",
                    "completed",
                    "complete",
                    "solved",
                    "success",
                    "win",
                    "cleared",
                ],
                "primary_goal": primary_goal,
            }
        if runtime_profile.startswith("casual_lane"):
            primary_goal = "lane_survival" if runtime_profile != "casual_lane_chase" else "lane_chase"
            return {
                "requires_player_entity": True,
                "requires_scoring": True,
                "requires_terminal_state": True,
                "requires_restart_entry": True,
                "terminal_state_aliases": ["game_over", "over", "ended", "lost", "failed"],
                "primary_goal": primary_goal,
            }
        if runtime_profile == "casual_action_arena":
            return {
                "requires_player_entity": True,
                "requires_scoring": True,
                "requires_terminal_state": True,
                "requires_restart_entry": True,
                "terminal_state_aliases": ["game_over", "over", "ended", "lost", "failed"],
                "primary_goal": "arena_clearance",
            }
        if runtime_profile == "casual_action_survival":
            return {
                "requires_player_entity": True,
                "requires_scoring": True,
                "requires_terminal_state": True,
                "requires_restart_entry": True,
                "terminal_state_aliases": ["game_over", "over", "ended", "lost", "failed"],
                "primary_goal": "survival_holdout",
            }
        if runtime_profile == "casual_arcade_rescue":
            return {
                "requires_player_entity": True,
                "requires_scoring": True,
                "requires_terminal_state": True,
                "requires_restart_entry": True,
                "terminal_state_aliases": ["game_over", "over", "ended", "lost", "failed", "rescued", "success"],
                "primary_goal": "rescue_route",
            }
        if runtime_profile == "casual_arcade_orbit":
            return {
                "requires_player_entity": True,
                "requires_scoring": True,
                "requires_terminal_state": True,
                "requires_restart_entry": True,
                "terminal_state_aliases": ["game_over", "over", "ended", "lost", "failed"],
                "primary_goal": "orbit_control",
            }
        if runtime_profile == "tap_challenge_combo":
            return {
                "requires_player_entity": True,
                "requires_scoring": True,
                "requires_terminal_state": True,
                "requires_restart_entry": True,
                "terminal_state_aliases": ["game_over", "over", "ended", "lost", "failed"],
                "primary_goal": "combo_target",
            }
        return {
            "requires_player_entity": True,
            "requires_scoring": True,
            "requires_terminal_state": True,
            "requires_restart_entry": True,
            "terminal_state_aliases": ["game_over", "over", "ended", "lost", "failed"],
            "primary_goal": "clear_feedback_loop",
        }


    @staticmethod
    def _target_seed_entity_count(spec: GameSpec) -> int:
        tier_value = str(getattr(spec.generation_tier, "value", spec.generation_tier) or "standard").strip().lower()
        if tier_value == "showcase":
            return 7 if spec.game_type in {"casual", "funny", "educational"} else 6
        if tier_value == "safe":
            return 3
        return 6 if spec.game_type == "puzzle" else 5


    def _expand_spec_entities_for_budget(self, spec: GameSpec) -> GameSpec:
        expanded = spec.model_copy(deep=True)
        target_count = min(
            max(3, self._target_seed_entity_count(expanded)),
            max(3, int(expanded.platform_constraints.max_entities or 50)),
        )
        if len(expanded.entities) >= target_count:
            return expanded

        catalog = ENTITY_BUDGET_EXPANSION_LIBRARY.get(
            expanded.game_type,
            ENTITY_BUDGET_EXPANSION_LIBRARY["casual"],
        )
        existing_names = {str(entity.name or "").strip().lower() for entity in expanded.entities}
        for candidate in catalog:
            if len(expanded.entities) >= target_count:
                break
            candidate_name = str(candidate.get("name") or "").strip().lower()
            if not candidate_name or candidate_name in existing_names:
                continue
            expanded.entities.append(GameEntity(**candidate))
            existing_names.add(candidate_name)
        return expanded


    def _summarize_current_code(self, current_code: str) -> str:
        title_match = re.search(r"<title>([^<]{1,80})</title>", current_code or "", re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else "Untitled game"
        hints: list[str] = []
        lower = (current_code or "").lower()
        if "score" in lower:
            hints.append("has scoring")
        if "enemy" in lower or "obstacle" in lower:
            hints.append("contains hazards")
        if "touchstart" in lower or "pointerdown" in lower:
            hints.append("uses touch controls")
        if "requestanimationframe" in lower:
            hints.append("runs an animated loop")
        if "lane" in lower:
            hints.append("lane-based movement")
        if "grid" in lower:
            hints.append("grid interactions")
        return f"{title}; " + ", ".join(hints[:4]) if hints else title


    @staticmethod
    def _summarize_source_spec(source_spec: Optional[GameSpec]) -> str:
        if not source_spec:
            return ""

        mechanic = source_spec.intent_summary or (
            source_spec.core_mechanics[0].type if source_spec.core_mechanics else source_spec.game_type
        )
        return "; ".join(
            part
            for part in [
                f"type={source_spec.game_type}",
                f"theme={source_spec.visual_style.theme}",
                f"mechanic={mechanic}",
                f"win={source_spec.rules.win_condition}",
                f"progression={source_spec.progression_shape}" if source_spec.progression_shape else "",
                f"reward_loop={source_spec.reward_loop}" if source_spec.reward_loop else "",
                f"signature_moment={source_spec.signature_moment}" if source_spec.signature_moment else "",
                f"tone={source_spec.tone}" if source_spec.tone else "",
                f"complexity_budget={source_spec.complexity_budget}" if source_spec.complexity_budget else "",
                f"ui_language={source_spec.ui_language}",
            ]
            if part
        )


    @staticmethod
    def _summarize_source_bundle_context(source_bundle_context: Optional[SourceBundleContext]) -> str:
        if not source_bundle_context:
            return ""

        segments = [
            f"title={source_bundle_context.title}" if source_bundle_context.title else "",
            (
                f"bundle_version={source_bundle_context.latest_bundle_version}"
                if source_bundle_context.latest_bundle_version is not None
                else ""
            ),
            f"game_type={source_bundle_context.latest_game_type}" if source_bundle_context.latest_game_type else "",
            (
                f"latest_iteration_type={source_bundle_context.latest_iteration_type}"
                if source_bundle_context.latest_iteration_type
                else ""
            ),
            source_bundle_context.summary or "",
        ]
        revision_lines = [
            ", ".join(
                item
                for item in [
                    f"v{revision.version}" if revision.version is not None else "",
                    revision.iteration_type or "",
                    revision.summary or "",
                ]
                if item
            )
            for revision in (source_bundle_context.recent_revisions or [])[:3]
        ]
        if revision_lines:
            segments.append("recent_revisions=" + " | ".join(line for line in revision_lines if line))
        return "; ".join(segment for segment in segments if segment)


    @staticmethod
    def _feedback_mentions_any(feedback: str, markers: tuple[str, ...]) -> bool:
        lowered = (feedback or "").lower()
        return any(marker in lowered for marker in markers)


    def _build_iteration_fallback_spec(
        self,
        *,
        base_spec: GameSpec,
        feedback: str,
        title: Optional[str],
        source_bundle_context: SourceBundleContext,
    ) -> GameSpec:
        fallback = base_spec.model_copy(deep=True)
        fallback.source_description = feedback
        fallback.intent_summary = self._build_iteration_intent_summary(base_spec, feedback, title)
        fallback.special_rules = self._merge_special_rules(
            base_spec.special_rules,
            self._derive_feedback_rules(feedback, base_spec.ui_language),
            [source_bundle_context.latest_feedback] if source_bundle_context.latest_feedback else [],
        )
        return fallback


    def _merge_iteration_spec(
        self,
        *,
        base_spec: Optional[GameSpec],
        parsed_spec: GameSpec,
        feedback: str,
        title: Optional[str],
        source_bundle_context: SourceBundleContext,
    ) -> GameSpec:
        if not base_spec:
            parsed_spec.intent_summary = self._build_iteration_intent_summary(parsed_spec, feedback, title)
            parsed_spec.special_rules = self._merge_special_rules(
                parsed_spec.special_rules,
                self._derive_feedback_rules(feedback, parsed_spec.ui_language),
            )
            return parsed_spec

        merged = base_spec.model_copy(deep=True)
        merged.source_description = feedback
        merged.intent_summary = self._build_iteration_intent_summary(base_spec, feedback, title)
        merged.reference_game = parsed_spec.reference_game or merged.reference_game
        merged.ui_language = base_spec.ui_language or parsed_spec.ui_language
        merged.difficulty_curve = parsed_spec.difficulty_curve or merged.difficulty_curve
        merged.special_rules = self._merge_special_rules(
            base_spec.special_rules,
            parsed_spec.special_rules,
            self._derive_feedback_rules(feedback, merged.ui_language),
            [source_bundle_context.latest_feedback] if source_bundle_context.latest_feedback else [],
        )

        if parsed_spec.game_type != base_spec.game_type and self._feedback_mentions_any(
            feedback,
            ("redesign", "overhaul", "change genre", "different game", "改成", "换成", "重做", "大改"),
        ):
            merged.game_type = parsed_spec.game_type
            merged.core_mechanics = parsed_spec.core_mechanics or merged.core_mechanics
            merged.entities = parsed_spec.entities or merged.entities
            merged.visual_style = parsed_spec.visual_style or merged.visual_style
            merged.rules = parsed_spec.rules or merged.rules
            merged.platform_constraints = parsed_spec.platform_constraints or merged.platform_constraints
            return merged

        if self._feedback_mentions_any(
            feedback,
            ("theme", "style", "visual", "art", "skin", "look", "界面", "主题", "风格", "美术", "视觉"),
        ):
            merged.visual_style = parsed_spec.visual_style or merged.visual_style

        if self._feedback_mentions_any(
            feedback,
            ("control", "input", "tap", "swipe", "drag", "touch", "操作", "控制", "点击", "滑动", "拖拽"),
        ):
            merged.platform_constraints = parsed_spec.platform_constraints or merged.platform_constraints
            merged.core_mechanics = parsed_spec.core_mechanics or merged.core_mechanics

        if self._feedback_mentions_any(
            feedback,
            ("goal", "win", "lose", "score", "lives", "objective", "胜利", "失败", "得分", "生命", "目标"),
        ):
            merged.rules = parsed_spec.rules or merged.rules

        if self._feedback_mentions_any(
            feedback,
            ("mechanic", "rule", "level", "stage", "enemy", "obstacle", "玩法", "规则", "关卡", "敌人", "障碍"),
        ):
            merged.core_mechanics = parsed_spec.core_mechanics or merged.core_mechanics

        return merged


    @staticmethod
    def _build_iteration_intent_summary(base_spec: GameSpec, feedback: str, title: Optional[str]) -> str:
        feedback_summary = re.sub(r"\s+", " ", (feedback or "").strip())
        if title and feedback_summary:
            return f"{title}: {feedback_summary[:160]}"
        if feedback_summary:
            return feedback_summary[:160]
        return base_spec.intent_summary


    @staticmethod
    def _merge_special_rules(*groups: Optional[list[str]]) -> list[str]:
        merged: list[str] = []
        for group in groups:
            for rule in group or []:
                text = re.sub(r"\s+", " ", str(rule or "").strip())
                if text and text not in merged:
                    merged.append(text)
        return merged


    @staticmethod
    def _derive_feedback_rules(feedback: str, ui_language: str) -> list[str]:
        text = re.sub(r"\s+", " ", (feedback or "").strip())
        if not text:
            return []

        rules: list[str] = []
        level_match = re.search(r"(?<!\d)(\d{1,2})\s*(?:levels?|stages?)\b", text, re.IGNORECASE)
        if not level_match:
            level_match = re.search(r"(?<!\d)(\d{1,2})\s*(?:个)?关卡", text)
        if level_match:
            count = int(level_match.group(1))
            rules.append(f"包含{count}个关卡" if ui_language == "zh-CN" else f"Include {count} levels")
        return rules
