#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""token 计量：估算校准（实报 / 估算反馈闭环）与锚点增量计量

- 校准：装配侧用 tokenizer 估算的 prompt token 与 API 实报之间有系统性偏差（回退 tokenizer 对中文、网关侧模型切换），
  用「上一轮实报 / 估算」作为本轮估算的乘数，钳制在应用给的区间内防脏样本放大误差；
- 锚点：上一次真实请求的 prompt_tokens 是历史主体的精确值，只对锚点之后新增的内容做估算（benchmark B1）；
- 模型级校准（3.2）：:class:`ModelCalibration` 把「比值 + 固定开销」按模型持久化为 EMA，叠在任何分词器之上——
  精确分词器（``llm.token`` 的 ``exact=True``）钳制 (0.9, 1.1) 只吸收 chat template / 工具声明的固定项，
  近似分词器钳制 (0.5, 2.0) 兜闭源模型；:func:`calibration_from_pair` 用两份不同长度的探针样本一次解出两项。

本模块是纯函数 + 可调用对象；样本从哪张表取、锚点与模型级校准存在哪里由应用定。
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


#: 精确分词器的钳制区间：只剩 chat template / 工具声明等固定项，比值几乎不该动
EXACT_RATIO_BOUNDS: tuple[float, float] = (0.9, 1.1)
#: 近似分词器的钳制区间：闭源模型的词表压缩率未知，放宽
APPROX_RATIO_BOUNDS: tuple[float, float] = (0.5, 2.0)
#: 固定开销上限（tokens）：防脏样本把 overhead 顶成天文数字
OVERHEAD_CAP = 4096
#: EMA 缺省步长
DEFAULT_ALPHA = 0.3


def bounds_for(exact: bool) -> tuple[float, float]:
    """按分词器是否精确给钳制区间"""
    return EXACT_RATIO_BOUNDS if exact else APPROX_RATIO_BOUNDS


@dataclass(frozen=True, slots=True)
class ModelCalibration:
    """模型级校准：``actual ≈ ratio × estimated + overhead``（按模型持久化，进程内缓存）

    :param ratio: 比值 EMA（分词器压缩率偏差）
    :param overhead: 固定开销 EMA（chat template / 工具声明序列化等每请求常数项）
    :param samples: 已吸收样本数（0 = 冷，应用据此决定是否探针 / 用保守缺省）
    :param exact: 对应分词器是否精确（决定钳制区间）
    """

    model: str
    ratio: float = 1.0
    overhead: int = 0
    samples: int = 0
    exact: bool = False

    def apply(self, estimated: int) -> int:
        """估算 → 校准后估算"""
        return int(estimated * self.ratio) + self.overhead

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "ratio": round(self.ratio, 4),
            "overhead": int(self.overhead),
            "samples": int(self.samples),
            "exact": bool(self.exact),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None, *, model: str, exact: bool) -> ModelCalibration:
        """从持久化字典恢复；形状不对或模型不匹配 → 冷校准（ratio 1.0）"""
        if not isinstance(data, dict) or data.get("model") != model:
            return cls(model=model, exact=exact)
        try:
            ratio = float(data.get("ratio") or 1.0)
            overhead = int(data.get("overhead") or 0)
            samples = int(data.get("samples") or 0)
        except (TypeError, ValueError):
            return cls(model=model, exact=exact)
        lo, hi = bounds_for(exact)
        return cls(
            model=model,
            ratio=min(max(ratio, lo), hi),
            overhead=min(max(overhead, 0), OVERHEAD_CAP),
            samples=max(samples, 0),
            exact=exact,
        )


def update_calibration(
    cal: ModelCalibration,
    estimated: int,
    actual: int,
    *,
    alpha: float = DEFAULT_ALPHA,
    bounds: tuple[float, float] | None = None,
) -> ModelCalibration:
    """吸收一条「估算 / 实报」样本（单样本不能同时解出两项，按坐标下降：先用现 overhead 定比值，再用新比值定残差）

    首个样本直接取观测值（不经 EMA），之后按 ``alpha`` 平滑；无效样本原样返回。
    """
    if estimated <= 0 or actual <= 0:
        return cal
    lo, hi = bounds or bounds_for(cal.exact)
    observed_ratio = min(max((actual - cal.overhead) / estimated, lo), hi)
    ratio = observed_ratio if cal.samples == 0 else cal.ratio + alpha * (observed_ratio - cal.ratio)
    ratio = min(max(ratio, lo), hi)
    residual = actual - ratio * estimated
    overhead = residual if cal.samples == 0 else cal.overhead + alpha * (residual - cal.overhead)
    overhead = int(min(max(overhead, 0), OVERHEAD_CAP))
    return ModelCalibration(model=cal.model, ratio=ratio, overhead=overhead, samples=cal.samples + 1, exact=cal.exact)


def calibration_from_pair(
    model: str,
    short: tuple[int, int],
    long: tuple[int, int],
    *,
    exact: bool,
    bounds: tuple[float, float] | None = None,
) -> ModelCalibration:
    """两份不同长度的探针样本 ``(estimated, actual)`` 一次解出比值与固定开销（冷启动标定）

    ``ratio = Δactual / Δestimated``，``overhead = actual_short − ratio × estimated_short``；
    两份估算长度相同或任一无效时退化为单样本 :func:`update_calibration`。
    """
    (e1, a1), (e2, a2) = short, long
    cold = ModelCalibration(model=model, exact=exact)
    if min(e1, a1, e2, a2) <= 0 or e1 == e2:
        return update_calibration(cold, e2, a2, bounds=bounds) if e2 > 0 and a2 > 0 else cold
    lo, hi = bounds or bounds_for(exact)
    ratio = min(max((a2 - a1) / (e2 - e1), lo), hi)
    overhead = int(min(max(a1 - ratio * e1, 0), OVERHEAD_CAP))
    return ModelCalibration(model=model, ratio=ratio, overhead=overhead, samples=2, exact=exact)


@dataclass(frozen=True, slots=True)
class CalibratedCounter:
    """按校准系数放大的 token 计数：``int(count_tokens(text, model) * ratio)``；``overhead`` 为每请求固定项"""

    count_tokens: Callable[[str, str], int]
    model: str
    ratio: float = 1.0
    overhead: int = 0

    @classmethod
    def from_calibration(cls, count_tokens: Callable[[str, str], int], cal: ModelCalibration) -> CalibratedCounter:
        return cls(count_tokens=count_tokens, model=cal.model, ratio=cal.ratio, overhead=cal.overhead)

    def __call__(self, text: str) -> int:
        return int(self.count_tokens(text, self.model) * self.ratio)

    def messages(self, messages: Iterable[dict[str, Any]], *, overhead_per_message: int = 4) -> int:
        """消息列表的估算总量（每条加固定开销；不含每请求 ``overhead``）"""
        return sum(self(str(m.get("content") or "")) + overhead_per_message for m in messages)

    def request(self, messages: Iterable[dict[str, Any]], *, overhead_per_message: int = 4) -> int:
        """整份请求的估算：``messages(...) + overhead``"""
        return self.messages(messages, overhead_per_message=overhead_per_message) + self.overhead


def anchored_estimate(anchor: TokenAnchor | None, tail_tokens: int, *, fallback_tokens: int) -> int:
    """锚点增量计量：有锚点时 ``anchor.prompt_tokens + 锚点之后新增内容的估算``，无锚点时用整体估算"""
    if anchor is None:
        return fallback_tokens
    return anchor.prompt_tokens + max(tail_tokens, 0)


__all__ = [
    "APPROX_RATIO_BOUNDS",
    "DEFAULT_ALPHA",
    "DEFAULT_RATIO_BOUNDS",
    "EXACT_RATIO_BOUNDS",
    "OVERHEAD_CAP",
    "CalibratedCounter",
    "ModelCalibration",
    "anchored_estimate",
    "bounds_for",
    "calibration_from_pair",
    "calibration_from_samples",
    "calibration_sample",
    "clamp_ratio",
    "update_calibration",
]
