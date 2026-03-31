from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..api.models import GameSpec, VisualStyle


DEFAULT_VISUAL_PACKS: List[Dict[str, Any]] = [
    {
        "id": "neon_glass",
        "displayName": "Neon Glass",
        "fontFamily": "'Trebuchet MS', 'Helvetica Neue', Arial, sans-serif",
        "hudStyle": "glass_panel",
        "buttonStyle": "pill_glow",
        "backgroundStyle": "aurora_gradient",
        "particleStyle": "glow_trails",
        "accentShapes": ["rounded_panel", "soft_blur_orb"],
        "motionStyle": "electric_sweep",
        "palette": ["#0b1026", "#5b7cff", "#31e1d0", "#ff5aa5", "#f8fbff"],
        "effects": ["glow", "bloom", "scanlines"],
        "preferredArtStyle": "neon",
        "suitableThemes": ["space", "neon", "city", "arcade"],
        "suitableGameTypes": ["casual", "funny"],
        "showcasePreferred": True,
    },
    {
        "id": "toy_3d",
        "displayName": "Toy 3D",
        "fontFamily": "'Verdana', 'Trebuchet MS', sans-serif",
        "hudStyle": "chunky_cards",
        "buttonStyle": "rounded_plastic",
        "backgroundStyle": "playroom_depth",
        "particleStyle": "toy_confetti",
        "accentShapes": ["capsule", "bubble"],
        "motionStyle": "springy_bounce",
        "palette": ["#1d4ed8", "#60a5fa", "#f97316", "#ef4444", "#fff7ed"],
        "effects": ["drop_shadow", "bounce", "sparkles"],
        "preferredArtStyle": "playful",
        "suitableThemes": ["toy", "garden", "food", "city"],
        "suitableGameTypes": ["casual", "funny", "educational"],
        "showcasePreferred": True,
    },
    {
        "id": "pixel_arcade",
        "displayName": "Pixel Arcade",
        "fontFamily": "'Courier New', monospace",
        "hudStyle": "pixel_panels",
        "buttonStyle": "pixel_button",
        "backgroundStyle": "arcade_grid",
        "particleStyle": "pixel_bursts",
        "accentShapes": ["pixel_frame", "scanline_bar"],
        "motionStyle": "snappy_step",
        "palette": ["#111827", "#38bdf8", "#22c55e", "#f59e0b", "#f8fafc"],
        "effects": ["pixel_flash", "screen_shake"],
        "preferredArtStyle": "pixel",
        "suitableThemes": ["arcade", "city", "space", "toy"],
        "suitableGameTypes": ["casual", "puzzle", "funny"],
        "showcasePreferred": False,
    },
    {
        "id": "comic_bounce",
        "displayName": "Comic Bounce",
        "fontFamily": "'Trebuchet MS', 'Arial Black', Arial, sans-serif",
        "hudStyle": "sticker_panels",
        "buttonStyle": "comic_burst",
        "backgroundStyle": "halftone_rays",
        "particleStyle": "pop_bursts",
        "accentShapes": ["burst_star", "speech_bubble"],
        "motionStyle": "rubber_pop",
        "palette": ["#172554", "#fb7185", "#facc15", "#38bdf8", "#fff7ed"],
        "effects": ["pop_lines", "squash_stretch"],
        "preferredArtStyle": "comic",
        "suitableThemes": ["city", "food", "toy", "garden"],
        "suitableGameTypes": ["funny", "casual"],
        "showcasePreferred": True,
    },
    {
        "id": "clean_edu",
        "displayName": "Clean Edu",
        "fontFamily": "'Helvetica Neue', Arial, sans-serif",
        "hudStyle": "clean_cards",
        "buttonStyle": "soft_capsule",
        "backgroundStyle": "airy_layers",
        "particleStyle": "minimal_confetti",
        "accentShapes": ["card", "underline"],
        "motionStyle": "gentle_slide",
        "palette": ["#0f172a", "#0ea5e9", "#14b8a6", "#facc15", "#f8fafc"],
        "effects": ["soft_glow"],
        "preferredArtStyle": "clean",
        "suitableThemes": ["ocean", "forest", "toy", "arcade"],
        "suitableGameTypes": ["educational", "puzzle"],
        "showcasePreferred": False,
    },
    {
        "id": "retro_terminal",
        "displayName": "Retro Terminal",
        "fontFamily": "'Courier New', monospace",
        "hudStyle": "terminal_frame",
        "buttonStyle": "inline_tab",
        "backgroundStyle": "crt_gradient",
        "particleStyle": "data_sparks",
        "accentShapes": ["terminal_border", "grid_ticks"],
        "motionStyle": "type_in",
        "palette": ["#020617", "#22c55e", "#38bdf8", "#f97316", "#e2fbe8"],
        "effects": ["scanlines", "glitch", "cursor_blink"],
        "preferredArtStyle": "retro_terminal",
        "suitableThemes": ["space", "city", "arcade", "neon"],
        "suitableGameTypes": ["puzzle", "educational", "casual"],
        "showcasePreferred": False,
    },
    {
        "id": "soft_fantasy",
        "displayName": "Soft Fantasy",
        "fontFamily": "'Georgia', 'Times New Roman', serif",
        "hudStyle": "storybook_cards",
        "buttonStyle": "ornate_capsule",
        "backgroundStyle": "mist_layers",
        "particleStyle": "sparkle_dust",
        "accentShapes": ["ornament", "magic_ribbon"],
        "motionStyle": "floaty_drift",
        "palette": ["#312e81", "#8b5cf6", "#22c55e", "#f59e0b", "#fef3c7"],
        "effects": ["sparkles", "mist", "light_rays"],
        "preferredArtStyle": "storybook",
        "suitableThemes": ["fantasy", "forest", "garden", "ocean"],
        "suitableGameTypes": ["casual", "educational", "funny"],
        "showcasePreferred": True,
    },
    {
        "id": "sports_broadcast",
        "displayName": "Sports Broadcast",
        "fontFamily": "'Arial Black', Arial, sans-serif",
        "hudStyle": "broadcast_hud",
        "buttonStyle": "score_bug",
        "backgroundStyle": "stadium_bars",
        "particleStyle": "speed_streaks",
        "accentShapes": ["score_chip", "angle_cut_panel"],
        "motionStyle": "whip_pan",
        "palette": ["#0f172a", "#22c55e", "#38bdf8", "#f97316", "#f8fafc"],
        "effects": ["speed_lines", "flash_frame"],
        "preferredArtStyle": "bold_flat",
        "suitableThemes": ["sports", "city", "arcade"],
        "suitableGameTypes": ["casual", "funny"],
        "showcasePreferred": True,
    },
]


def _catalog_path() -> Path:
    resolved = Path(__file__).resolve()
    seen: set[str] = set()
    fallback: Path | None = None
    for root in (resolved.parent, *resolved.parents):
        candidates = (
            root / "game" / "catalogs" / "visual-pack-catalog.json",
            root / "src" / "game" / "catalogs" / "visual-pack-catalog.json",
            root / "packages" / "game-service" / "src" / "game" / "catalogs" / "visual-pack-catalog.json",
        )
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            fallback = candidate
            if candidate.exists():
                return candidate
    return fallback or Path("visual-pack-catalog.json")


@lru_cache(maxsize=1)
def get_visual_pack_catalog() -> List[Dict[str, Any]]:
    path = _catalog_path()
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list) and loaded:
                return loaded
        except Exception:
            pass
    return DEFAULT_VISUAL_PACKS


def get_visual_pack(pack_id: Optional[str]) -> Optional[Dict[str, Any]]:
    normalized = str(pack_id or "").strip().lower()
    if not normalized:
        return None
    for pack in get_visual_pack_catalog():
        if str(pack.get("id") or "").strip().lower() == normalized:
            return dict(pack)
    return None


def _stable_variant_index(*parts: str, count: int) -> int:
    if count <= 1:
        return 0
    seed = "|".join((part or "").strip() for part in parts if part is not None)
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(digest, "big") % count


def _pack_score(
    pack: Dict[str, Any],
    *,
    game_type: str,
    theme: str,
    generation_tier: str,
) -> int:
    score = 0
    if game_type in (pack.get("suitableGameTypes") or []):
        score += 4
    if theme and theme in (pack.get("suitableThemes") or []):
        score += 5
    if generation_tier == "showcase" and bool(pack.get("showcasePreferred")):
        score += 4
    if generation_tier == "safe" and bool(pack.get("showcasePreferred")):
        score -= 2
    return score


def select_visual_pack(
    *,
    game_type: str,
    theme: str,
    generation_tier: str = "standard",
    variation_seed: Optional[str] = None,
) -> Dict[str, Any]:
    catalog = list(get_visual_pack_catalog())
    candidates = [pack for pack in catalog if game_type in (pack.get("suitableGameTypes") or [])] or catalog
    scored = sorted(
        candidates,
        key=lambda pack: _pack_score(pack, game_type=game_type, theme=theme, generation_tier=generation_tier),
        reverse=True,
    )
    pool = scored[: min(3, len(scored))]
    index = _stable_variant_index(game_type, theme, generation_tier, variation_seed or "", count=len(pool))
    return dict(pool[index])


def apply_visual_pack_defaults(
    spec: GameSpec,
    *,
    variation_seed: Optional[str] = None,
) -> GameSpec:
    generation_tier = str(getattr(getattr(spec, "generation_tier", "standard"), "value", getattr(spec, "generation_tier", "standard")) or "standard")
    selected_pack = get_visual_pack(spec.visual_style.visual_pack) or select_visual_pack(
        game_type=spec.game_type,
        theme=spec.visual_style.theme,
        generation_tier=generation_tier,
        variation_seed=variation_seed,
    )

    effects = list(dict.fromkeys([*(spec.visual_style.effects or []), *(selected_pack.get("effects") or [])]))
    inherited_intensity = (spec.visual_style.render_style_intensity or "").strip().lower()
    if generation_tier == "showcase":
        intensity = "high" if inherited_intensity in {"", "balanced"} else inherited_intensity
    elif generation_tier == "safe":
        intensity = "restrained" if inherited_intensity in {"", "balanced"} else inherited_intensity
    else:
        intensity = inherited_intensity or "balanced"
    spec.visual_style = VisualStyle(
        theme=spec.visual_style.theme or "arcade",
        palette=list(selected_pack.get("palette") or spec.visual_style.palette),
        art_style=selected_pack.get("preferredArtStyle") or spec.visual_style.art_style,
        background=selected_pack.get("backgroundStyle") or spec.visual_style.background,
        effects=effects,
        visual_pack=selected_pack.get("id"),
        render_style_intensity=intensity,
    )
    return spec


def visual_pack_direction_lines(pack: Optional[Dict[str, Any]]) -> List[str]:
    if not pack:
        return []
    return [
        f"- Visual pack: {pack.get('displayName') or pack.get('id')}",
        f"- Font family direction: {pack.get('fontFamily')}",
        f"- HUD style: {pack.get('hudStyle')}",
        f"- Button style: {pack.get('buttonStyle')}",
        f"- Background style: {pack.get('backgroundStyle')}",
        f"- Motion style: {pack.get('motionStyle')}",
        f"- Particle style: {pack.get('particleStyle')}",
        f"- Accent shapes: {', '.join(pack.get('accentShapes') or [])}",
    ]
