"""Compact contract digest for short-path fills.

Avoids re-sending the full interactive SYSTEM_PROMPT on HIT/SOFT while
keeping the quality bars that runtime QA and structured review enforce.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from .interactive_families import Recipe

CONTRACT_DIGEST_VERSION = "2026-09-12-interactive-v1"

SCIENCE_CONTRACT_DIGEST = """科学短路径契约（验收不放宽）：
动态模型点击开始后必须从可见非平衡初态持续推进；用 leftover 累加帧时间，不能把小于步长的 elapsed 向下取整为零。
开始、暂停、重置必须是可见且可区分的独立控件（按钮文本或 aria-label）。
参数必须进入方程并立即更新公式读数。不要编造测量结果，不要把示意动画当成实验数据。
不要套用问答、关卡、积分、生命或输赢机制。不要把拖拽释放当作唯一启动方式。
禁止网络、iframe、弹窗、外部库、eval 与动态 Function。iframe sandbox 无 allow-modals。
画布 max-height:min(38vh,240px)，首屏保留开始/暂停/重置与参数。"""

TOOL_CONTRACT_DIGEST = """工具短路径契约（验收不放宽）：
输入按纯文本处理；重置恢复完整初态；错误用页内提示。
开始/暂停/重置若存在必须真正改变状态。禁止网络、eval、弹窗与外部库。"""


def contract_digest(*, kind: str, recipe: Optional[Recipe] = None) -> str:
    body = SCIENCE_CONTRACT_DIGEST if kind == "science" else TOOL_CONTRACT_DIGEST
    extra = ""
    if recipe:
        extra = (
            f"\n家族={recipe.family_id} 配方={recipe.id} 学科={recipe.subject}\n"
            f"公式={recipe.formula}\n假设={recipe.assumptions}\n限制={recipe.limits}"
        )
    digest = hashlib.sha256((CONTRACT_DIGEST_VERSION + body + extra).encode()).hexdigest()[:12]
    return f"ContractDigest {CONTRACT_DIGEST_VERSION} sha={digest}\n{body}{extra}"
