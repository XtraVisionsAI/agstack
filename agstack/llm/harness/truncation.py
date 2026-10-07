#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""工具结果截断设施（写入侧防线；零状态、模型无关）

截断的**设施**在此，**调用**留在各工具体内——结果列表按相关度丢弃并回写丢弃数、渲染串截断、单字段截断三种形态
语义各异，且语义化截断依赖工具内知识，不适合做成盲 post 钩子。上限数值（单条 / 合计占窗口比例）由调用方给。
"""

import logging
from collections.abc import Callable
from typing import Any


logger = logging.getLogger(__name__)

#: token 计数函数：text -> tokens（调用方按模型绑定，如 functools.partial(count_tokens, model=...)）
TokenCounter = Callable[[str], int]

#: 截断标注模板；{tokens} / {lines} 为原文规模
DEFAULT_MARKER = "\n[... 原文约 {tokens} tokens / {lines} 行，已截断中段；如需完整内容请缩小查询范围重试 ...]\n"


def truncate_middle(text: str, max_tokens: int, count_tokens: TokenCounter, *, marker: str = DEFAULT_MARKER) -> str:
    """超限文本保头尾截断，中间段替换为规模标注（截断绝不静默）；未超限原样返回"""
    total = count_tokens(text)
    if total <= max_tokens:
        return text
    lines = text.count("\n") + 1
    # 按 token 比例折算保留字符数，留 10% 余量给标注与估算误差
    keep_chars = max(int(len(text) * max_tokens / total * 0.9), 200)
    head_chars = int(keep_chars * 0.7)
    head = text[:head_chars]
    tail = text[len(text) - (keep_chars - head_chars) :]
    return head + marker.format(tokens=total, lines=lines) + tail


def clamp_results(
    results: list[Any],
    *,
    per_item_max: int,
    total_max: int,
    count_tokens: TokenCounter,
    content_key: str = "content",
    relevance_key: str = "relevance_score",
    tool: str = "",
) -> tuple[list[Any], int]:
    """结果列表写入侧统一截断：单条 ``content`` 超 per_item_max 保头尾截断；合计超 total_max 从低相关度端整条丢弃

    返回 ``(处理后的 results, 丢弃条数)``，丢弃数由调用方显式呈现给模型。
    """
    if not results:
        return results, 0
    clamped: list[Any] = []
    tokens: list[int] = []
    for item in results:
        if isinstance(item, dict) and isinstance(item.get(content_key), str) and item[content_key]:
            content = truncate_middle(item[content_key], per_item_max, count_tokens)
            if content != item[content_key]:
                item = {**item, content_key: content}
            clamped.append(item)
            tokens.append(count_tokens(item[content_key]))
        else:
            clamped.append(item)
            tokens.append(0)

    total = sum(tokens)
    if total <= total_max:
        return clamped, 0

    def _relevance(i: int) -> float:
        item = clamped[i]
        if isinstance(item, dict):
            score = item.get(relevance_key)
            if isinstance(score, (int, float)):
                return float(score)
        return 0.0

    dropped: set[int] = set()
    for i in sorted(range(len(clamped)), key=_relevance):
        if total <= total_max:
            break
        if tokens[i] == 0:
            continue
        dropped.add(i)
        total -= tokens[i]
    if dropped:
        logger.warning(
            "[%s] tool results exceed %d tokens, dropped %d low-relevance items", tool, total_max, len(dropped)
        )
    return [item for i, item in enumerate(clamped) if i not in dropped], len(dropped)


__all__ = ["DEFAULT_MARKER", "TokenCounter", "clamp_results", "truncate_middle"]
