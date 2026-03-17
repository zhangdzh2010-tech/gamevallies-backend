"""Template engine for generating code from game specifications"""

from typing import Tuple
from pathlib import Path
from ..api.models import GameSpec


class TemplateEngine:
    """Engine for loading and rendering game templates"""

    # Templates that are a genuine match for the game type
    TEMPLATES = {
        "dodge": "space_dodge.html",
        "catcher": "fruit_catcher.html",
        "runner": "maze_runner.html",
        "rhythm": "rhythm_tap.html",
        "platformer": "platform_jump.html",
    }

    # Confidence for each matched type — kept below TEMPLATE_CONFIDENCE_THRESHOLD (0.8)
    # so the pipeline always uses hybrid (LLM-customised) path, never bare template fill
    CONFIDENCE = {
        "dodge": 0.65,
        "catcher": 0.65,
        "runner": 0.65,
        "rhythm": 0.65,
        "platformer": 0.65,
    }

    def __init__(self):
        """Initialize template engine"""
        self.template_dir = Path(__file__).parent.parent / "templates"

    def match(self, spec: GameSpec) -> Tuple[str, float]:
        """
        Match game spec to best template.
        
        Args:
            spec: GameSpec to match
            
        Returns:
            Tuple of (template_id, confidence score)
        """
        template_id = self.TEMPLATES.get(spec.game_type)
        if template_id:
            # Known template → hybrid path (LLM customises the skeleton)
            confidence = self.CONFIDENCE.get(spec.game_type, 0.65)
        else:
            # No matching template → full LLM generation
            template_id = None
            confidence = 0.0
        return (template_id, confidence)

    def generate(self, spec: GameSpec, template_id: str) -> str:
        """
        Generate HTML code from template and spec.
        
        Args:
            spec: GameSpec with game parameters
            template_id: Template filename to use
            
        Returns:
            Generated HTML code as string
        """
        # Load template
        template_path = self.template_dir / template_id

        if not template_path.exists():
            # Return default template for fallback
            template_path = self.template_dir / "space_dodge.html"

        try:
            with open(template_path, "r", encoding="utf-8") as f:
                html_code = f.read()
        except Exception as e:
            raise Exception(f"Failed to load template {template_id}: {str(e)}")

        # Replace parameters with values from spec
        html_code = self._replace_parameters(html_code, spec)

        return html_code

    def _replace_parameters(self, html_code: str, spec: GameSpec) -> str:
        """
        Replace template parameters with game spec values.
        
        Args:
            html_code: Template HTML code
            spec: GameSpec with values
            
        Returns:
            HTML code with parameters replaced
        """
        # Extract visual style parameters
        visual_style = spec.visual_style
        palette = visual_style.palette or []
        background_color = palette[0] if len(palette) > 0 else "#08080d"
        primary_color = palette[1] if len(palette) > 1 else "#00ff41"
        secondary_color = palette[3] if len(palette) > 3 else (
            palette[2] if len(palette) > 2 else "#ff006e"
        )
        text_color = palette[4] if len(palette) > 4 else "#ffffff"

        # Color replacements
        replacements = {
            "{{BACKGROUND_COLOR}}": background_color,
            "{{PRIMARY_COLOR}}": primary_color,
            "{{SECONDARY_COLOR}}": secondary_color,
            "{{TEXT_COLOR}}": text_color,
        }

        # Game parameter replacements
        rules = spec.rules
        replacements.update({
            "{{LIVES}}": str(rules.lives),
            "{{SPEED_MULTIPLIER}}": self._speed_multiplier(spec.difficulty_curve),
        })

        # Apply replacements
        for placeholder, value in replacements.items():
            html_code = html_code.replace(placeholder, value)

        return html_code

    def _speed_multiplier(self, difficulty_curve: str) -> str:
        if difficulty_curve == "hard":
            return "1.6"
        if difficulty_curve == "progressive":
            return "1.3"
        if difficulty_curve == "easy":
            return "0.9"
        return "1.0"
