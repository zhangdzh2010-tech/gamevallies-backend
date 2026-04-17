"""Game engine module – 8-stage pipeline implementation"""

from .dialogue_engine import DialogueEngine
from .game_designer import GameDesigner
from .code_generator import CodeGenerator
from .qa_pipeline import QAPipeline
from .pipeline_orchestrator import PipelineOrchestrator

# P1 additive modules (PR-07 .. PR-12). Import lazily-safe; failure to
# import any of these should not break the 8-stage pipeline.
try:  # pragma: no cover — best-effort re-export
    from . import creative_anchors      # PR-07
    from . import numeric_sampler       # PR-08
    from . import diversity_planner     # PR-09
    from . import qa_tiers              # PR-10
    from . import runtime_qa_scheduler  # PR-11
    from . import template_inspiration  # PR-12
except Exception:
    pass

__all__ = [
    "DialogueEngine",
    "GameDesigner",
    "CodeGenerator",
    "QAPipeline",
    "PipelineOrchestrator",
]
