"""Stage 06c: LLM Code Reviewer – semantic game quality assessment.

Calls the LLM to review the generated HTML5 game code and return a
structured JSON assessment:
  - is_complete_game: bool
  - has_real_gameplay: bool
  - difficulty_balanced: bool
  - fun_score: 1-10
  - visual_polish_score: 1-10
  - character_quality_score: 1-10
  - issues: list[str]

Returns ran=False only when review is disabled; failed assessments retain evidence.
"""

from __future__ import annotations

import json
import logging
import math
import re

from ..services.llm_client import LLMClient
from .prompt_format import safe_format_prompt
from .prompt_store import require_prompt
from .quality_scorer import LLMReviewResult
from .pipeline_errors import PipelineExecutionError
from .review_evidence import EVIDENCE_PROTOCOL, REVIEW_FLAGS, REVIEW_SCORES, validate_review_evidence, indexed_review_source
from .review_recovery import recover_review, InvalidReviewEvidence

logger = logging.getLogger(__name__)

def _build_code_preview(html_code: str, limit: int | None = None) -> str:
    """Review the complete artifact; slicing hides gameplay and character drawing.

    Kept the optional argument for older callers; no source is silently omitted.
    Provider context admission must fail explicitly if a document cannot fit.
    """
    return html_code


class CodeReviewer:
    """LLM-based semantic quality reviewer for HTML5 games."""

    def __init__(self) -> None:
        self._client = LLMClient()

    async def review(self, html_code: str, *, user_requirements: str = "") -> LLMReviewResult:
        """Run a bounded, source-cited review and fail closed on invalid evidence."""
        if not self._client.is_enabled():
            logger.debug("LLM not enabled – skipping code review")
            return LLMReviewResult(ran=False)

        code_preview = indexed_review_source(_build_code_preview(html_code))
        prompt = safe_format_prompt(require_prompt("prompt.code_review_template"), code_preview=code_preview)
        system = require_prompt("prompt.code_review_system")
        system += (
            "\nAUTHORITATIVE SCORING RUBRIC (supersedes generic premium-mobile examples in the template): "
            "Assess the supplied complete source against its original brief. A focused, intentionally simple game "
            "can earn 7-8 with a complete requested loop, responsive controls, readable coherent composition "
            "and working feedback; extra mechanics or expensive effects are not prerequisites. "
            "Scores of 5-6 mean identifiable implementation or presentation defects; 9-10 require exceptional execution. "
            "Do not award points merely because comments claim a feature exists. Trace the executed paths. "
            "Check elapsed-time movement, countdown and pause, single coordinate conversion, button hit regions "
            "and full restart reset. Missing required start/pause/resume/restart behavior means is_complete_game=false. "
            "Do not penalize absent sound, haptics, idle animation or transition effects unless the brief requires them "
            "or their absence causes a concrete usability defect. issues must list code-supported defects with the "
            "relevant function/expression and a feasible local correction, ordered by required behavior first. "
            "Exclude optional enhancements from issues and score deductions. Do not claim to have seen rendered pixels."
        )
        if user_requirements:
            system += (
                "\nREQUIREMENT-SCOPED REVIEW: Evaluate the target platform and requested scope in ORIGINAL USER REQUIREMENTS. "
                "Explicit desktop mouse/keyboard requirements take precedence over generic mobile defaults. "
                "Do not require touch gestures or haptics for desktop games. Do not treat unrequested features "
                "(such as radial timers instead of readable numeric timers) as missing requirements. "
                "For non-character games, grade the intentional design and readability of the actual props rather than anatomy. "
                "Still assess real gameplay, responsive controls, coherent composition and required feedback rigorously. "
                "For each low score, include a concrete code-supported defect and a feasible correction in issues; "
                "distinguish missing required behavior from optional aesthetic suggestions."
            )
            prompt = "ORIGINAL USER REQUIREMENTS:\n" + user_requirements + "\n\n" + prompt
        system += EVIDENCE_PROTOCOL

        async def request_review(correction):
            return await self._client.complete_with_truncation_retry(
                max_tokens=2048, system=system,
                messages=[{"role": "user", "content": prompt + ("\n\n" + correction if correction else "")}],
                step_key="code_review", stage="qa_checking", prefer_fast=True,
                response_size_hint="medium_structured", context_scope="request", compression_policy="code_review",
                truncation_retry_attempts=1, truncation_retry_increment=512, truncation_retry_max_tokens=3072,
                timeout_retry_attempts=0 if correction else 1,
                timeout_retry_increment_s=30, timeout_retry_max_s=120,
            )

        try:
            verified = await recover_review(request_review, self._parse_review,
                lambda result: validate_review_evidence(result, html_code) if result.ran else ['invalid review schema'])
            result = verified.assessment
            result.evidence_verified = True
            return result
        except InvalidReviewEvidence as exc:
            raise self._evidence_failure(html_code, exc.errors, 'review_evidence') from exc
        except PipelineExecutionError:
            raise
        except Exception as exc:
            logger.warning('Code review unavailable: %s', type(exc).__name__)
            raise self._evidence_failure(html_code, ['assessment unavailable'], 'review_infrastructure') from exc

    @staticmethod
    def _evidence_failure(code: str, errors: list[str], family: str) -> PipelineExecutionError:
        return PipelineExecutionError('Code review evidence could not be validated: ' + '; '.join(errors),
            stage='code_review', failure_family=family, artifacts=[
                {'artifact_type':'failed_quality_candidate','content_type':'text/html','payload':code,
                 'metadata':{'stage':'code_review','retained':True}},
                {'artifact_type':'review_evidence_report','content_type':'application/json',
                 'payload':{'errors':errors},'metadata':{'stage':'code_review'}},
            ])

    def _parse_review(self, raw: str) -> LLMReviewResult:
        """Parse LLM JSON output, with fallback for malformed responses."""
        # Strip markdown fences if present
        cleaned = re.sub(r"```(?:json)?\s*", "", raw, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE).strip()

        # Use raw_decode from first { to safely handle surrounding text
        start = cleaned.find("{")
        data = None
        if start != -1:
            try:
                data, _ = json.JSONDecoder().raw_decode(cleaned, start)
            except json.JSONDecodeError:
                # Last resort: first { to last }
                end = cleaned.rfind("}")
                if end > start:
                    try:
                        data = json.loads(cleaned[start:end + 1])
                    except json.JSONDecodeError as exc:
                        logger.warning(f"JSON parse error in review: {exc} | raw: {raw[:200]}")

        if not isinstance(data, dict):
            logger.warning(f"No JSON found in review response: {raw[:200]}")
            return LLMReviewResult(ran=False)

        if any(type(data.get(key)) is not bool for key in REVIEW_FLAGS):
            return LLMReviewResult(ran=False)
        if any(type(data.get(key)) not in (int, float) or not math.isfinite(data[key])
               or not 1 <= data[key] <= 10 for key in REVIEW_SCORES):
            return LLMReviewResult(ran=False)
        issues = data.get('issues')
        if not isinstance(issues, list) or len(issues) > 10 or any(not isinstance(x, str) or not x.strip() for x in issues):
            return LLMReviewResult(ran=False)

        result = LLMReviewResult(
            ran=True,
            is_complete_game=bool(data.get("is_complete_game", False)),
            has_real_gameplay=bool(data.get("has_real_gameplay", False)),
            difficulty_balanced=bool(data.get("difficulty_balanced", False)),
            fun_score=float(data['fun_score']),
            visual_polish_score=float(data['visual_polish_score']),
            character_quality_score=float(data['character_quality_score']),
            issues=issues,
            findings=data.get('findings', []),
        )
        logger.info(
            f"Code review: complete={result.is_complete_game}, "
            f"gameplay={result.has_real_gameplay}, fun={result.fun_score:.1f}, "
            f"visual={result.visual_polish_score:.1f}, "
            f"character={result.character_quality_score:.1f}, "
            f"issues={len(result.issues)}"
        )
        return result
