import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import GameRules, GameSpec, PlatformConstraints, VisualStyle
from src.engine.code_template_cache import CodeTemplateCache


def _sample_code(label: str) -> str:
    return f"""
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const state = '{label}';
          function loop() {{
            requestAnimationFrame(loop);
          }}
          loop();
        </script>
      </body>
    </html>
    """


def test_template_cache_uses_theme_and_goal_in_fingerprint():
    cache = CodeTemplateCache()
    space_survival = GameSpec(
        game_type="casual",
        visual_style=VisualStyle(theme="space"),
        rules=GameRules(win_condition="Survive for 60 seconds"),
        platform_constraints=PlatformConstraints(input_mode="drag"),
    )
    forest_rescue = GameSpec(
        game_type="casual",
        visual_style=VisualStyle(theme="forest"),
        rules=GameRules(win_condition="Rescue three travelers"),
        platform_constraints=PlatformConstraints(input_mode="drag"),
    )

    cache.store(space_survival, "casual_action", _sample_code("space"))

    assert cache.get_skeleton(space_survival, "casual_action") is not None
    assert cache.get_skeleton(forest_rescue, "casual_action") is None
