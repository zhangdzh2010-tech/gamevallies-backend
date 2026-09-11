"""Retain validated progress when a proposed local repair introduces regressions."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from .generated_quality_policy import QUALITY_POLICY


@dataclass(frozen=True)
class CandidateCheckpoint:
    code: str
    runtime: dict
    assessment: dict | None

    @classmethod
    def capture(cls, code, runtime, assessment):
        return cls(code, deepcopy(runtime), deepcopy(assessment))

    def regression_errors(self, runtime, assessment, kind):
        if self.runtime.get('passed') and not runtime.get('passed'):
            return ['修复引入运行或保留约束回归，已恢复此前通过运行检查的候选。']
        errors = []
        if self.runtime.get('ran'):
            if not runtime.get('ran'):
                errors.append('修复候选缺少真实运行证据，保留此前候选。')
            for field in ('js_errors', 'sandboxViolations'):
                if self.runtime.get(field) == [] and runtime.get(field):
                    errors.append(f'修复引入新的{field}，已恢复此前候选。')
            if self.runtime.get('contentChanged') and not runtime.get('contentChanged'):
                errors.append('修复破坏了已验证的交互响应，已恢复此前候选。')
        if not self.assessment or not self.assessment.get('review_ran'):
            return errors
        if not assessment or not assessment.get('review_ran'):
            return errors + ['修复候选缺少有效审核，保留此前已审核候选。']
        policy = QUALITY_POLICY['artifact_rubrics'][kind]
        if self.assessment.get('complete') and not assessment.get('complete'):
            errors.append('修复破坏了已完整实现的要求，保留此前候选。')
        if not self.assessment.get('critical_issues') and assessment.get('critical_issues'):
            errors.append('修复引入新的关键缺陷，保留此前候选。')
        for field, threshold in policy['minimums'].items():
            before = min(self.assessment['scores'][field], threshold)
            after = min(assessment['scores'][field], threshold)
            if after + 1e-6 < before:
                errors.append(f'修复引入{field}质量回归，已恢复此前候选。')
        if min(assessment['score'], policy['pass_score']) + 1e-6 < min(self.assessment['score'], policy['pass_score']):
            errors.append('修复降低了未达标的分类总分，已恢复此前候选。')
        return errors
