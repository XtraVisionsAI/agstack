#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""token 计量：估算校准（实报 / 估算反馈闭环）与锚点增量计量

- 校准：装配侧用 tokenizer 估算的 prompt token 与 API 实报之间有系统性偏差（回退 tokenizer 对中文、网关侧模型切换），
  用「上一轮实报 / 估算」作为本轮估算的乘数，钳制在应用给的区间内防脏样本放大误差；
- 锚点：上一次真实请求的 prompt_tokens 是历史主体的精确值，只对锚点之后新增的内容做估算（benchmark B1）。

本模块是纯函数 + 一个可调用对象；样本从哪张表取、锚点存在哪里由应用定。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .ports import TokenAnchor


#: 校准系数缺省钳制区间
DEFAULT_RATIO_BOUNDS: tuple[float, float] = (0.8, 1.5)


def clamp_ratio(estimated: int, actual: int, *, bounds: tuple[float, float] = DEFAULT_RATIO_BOUNDS) -> float:
    """``actual / estimated`` 钳制到 ``bounds``；任一为非正数时返回 1.0（不校准）"""
    if estimated <= 0 or actual <= 0:
        return 1.0
    lo, hi = bounds
    return min(max(actual / estimated, lo), hi)


def calibration_from_samples(
    samples: Iterable[dict[str, Any] | None], model: str, *, bounds: tuple[float, float] = DEFAULT_RATIO_BOUNDS
) -> float:
    """从最近的校准样本（新→旧）取第一条与 ``model`` 匹配、数值有效的，返回钳制后的系数；没有则 1.0

    样本形状：``{"estimated": int, "actual": int, "model": str}``；模型已切换的样本跳过
    （系数天然反映当前模型 tokenizer 的压缩率，不能跨模型沿用）。
    """
    for sample in samples:
        if not isinstance(sample, dict) or sample.get("model") != model:
            continue
        estimated, actual = sample.get("estimated"), sample.get("actual")
        if isinstance(estimated, int) and isinstance(actual, int) and estimated > 0 and actual > 0:
            return clamp_ratio(estimated, actual, bounds=bounds)
    return 1.0


def calibration_sample(estimated: int, actual: int, model: str) -> dict[str, Any] | None:
    """生成一条校准样本（估算或实报无效时 None，调用方不落库）"""
    if estimated <= 0 or actual <= 0:
        return None
    return {"estimated": int(estimated), "actual": int(actual), "model": model}


@dataclass(frozen=True, slots=True)
class CalibratedCounter:
    """按校准系数放大的 token 计数：``int(count_tokens(text, model) * ratio)``"""

    count_tokens: Callable[[str, str], int]
    model: str
    ratio: float = 1.0

    def __call__(self, text: str) -> int:
        return int(self.count_tokens(text, self.model) * self.ratio)

    def messages(self, messages: Iterable[dict[str, Any]], *, overhead_per_message: int = 4) -> int:
        """消息列表的估算总量（每条加固定开销）"""
        return sum(self(str(m.get("content") or "")) + overhead_per_message for m in messages)


def anchored_estimate(anchor: TokenAnchor | None, tail_tokens: int, *, fallback_tokens: int) -> int:
    """锚点增量计量：有锚点时 ``anchor.prompt_tokens + 锚点之后新增内容的估算``，无锚点时用整体估算"""
    if anchor is None:
        return fallback_tokens
    return anchor.prompt_tokens + max(tail_tokens, 0)


__all__ = [
    "DEFAULT_RATIO_BOUNDS",
    "CalibratedCounter",
    "anchored_estimate",
    "calibration_from_samples",
    "calibration_sample",
    "clamp_ratio",
]
