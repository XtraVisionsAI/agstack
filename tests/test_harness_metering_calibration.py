#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""模型级校准（harness.metering，3.2）：ModelCalibration / update_calibration / calibration_from_pair / bounds_for

- 首个样本直取观测比值，之后 EMA；比值钳在分词器种类对应的区间，overhead 非负且有上限；
- 两份不同长度的探针样本一次解出比值与固定开销；长度相同退化为单样本；
- CalibratedCounter.request = messages + 每请求 overhead；from_calibration 绑定。
"""

from __future__ import annotations

import pytest

from agstack.genai.harness import (
    APPROX_RATIO_BOUNDS,
    EXACT_RATIO_BOUNDS,
    CalibratedCounter,
    ModelCalibration,
    bounds_for,
    calibration_from_pair,
    update_calibration,
)
from agstack.genai.harness.metering import OVERHEAD_CAP


def test_bounds_by_tokenizer_kind():
    assert bounds_for(True) == EXACT_RATIO_BOUNDS == (0.9, 1.1)
    assert bounds_for(False) == APPROX_RATIO_BOUNDS == (0.5, 2.0)


def test_first_sample_then_ema_with_clamp():
    cold = ModelCalibration(model="m", exact=False)
    assert cold.apply(1000) == 1000 and cold.samples == 0
    first = update_calibration(cold, estimated=1000, actual=700)
    assert first.samples == 1 and first.ratio == pytest.approx(0.7) and first.overhead == 0
    second = update_calibration(first, estimated=1000, actual=800)  # 观测 0.8 → EMA 0.73
    assert second.ratio == pytest.approx(0.7 + 0.3 * 0.1)
    assert second.overhead == int(0.3 * (800 - second.ratio * 1000))
    # 钳制：近似 (0.5, 2.0)；精确 (0.9, 1.1)
    assert update_calibration(cold, 1000, 100).ratio == 0.5
    exact = update_calibration(ModelCalibration(model="m", exact=True), 1000, 1300)
    assert exact.ratio == 1.1 and exact.overhead == 200  # 残差进 overhead
    # 无效样本原样返回
    assert update_calibration(first, 0, 10) is first and update_calibration(first, 10, 0) is first
    # overhead 上限
    huge = update_calibration(ModelCalibration(model="m", exact=True), 10, 100000)
    assert huge.overhead == OVERHEAD_CAP


def test_calibration_from_pair_solves_both():
    cal = calibration_from_pair("m", short=(1000, 1250), long=(3000, 3650), exact=True)
    assert cal.samples == 2 and cal.ratio == pytest.approx(1.1) and cal.overhead == int(1250 - 1.1 * 1000)
    assert cal.apply(2000) == int(2000 * 1.1) + cal.overhead
    approx = calibration_from_pair("m", short=(1000, 600), long=(2000, 1100), exact=False)
    assert approx.ratio == pytest.approx(0.5) and approx.overhead == 100
    # 相同长度 / 无效 → 退化
    same = calibration_from_pair("m", short=(1000, 700), long=(1000, 720), exact=False)
    assert same.samples == 1 and same.ratio == pytest.approx(0.72)
    assert calibration_from_pair("m", short=(0, 0), long=(0, 0), exact=False).samples == 0


def test_roundtrip_dict_and_counter_binding():
    cal = ModelCalibration(model="m", ratio=0.75, overhead=120, samples=5, exact=False)
    data = cal.to_dict()
    assert ModelCalibration.from_dict(data, model="m", exact=False) == cal
    assert ModelCalibration.from_dict(data, model="other", exact=False).samples == 0  # 模型不匹配 → 冷
    assert ModelCalibration.from_dict({"model": "m", "ratio": "bad"}, model="m", exact=False).ratio == 1.0
    clamped = ModelCalibration.from_dict({"model": "m", "ratio": 5.0, "overhead": -3}, model="m", exact=True)
    assert clamped.ratio == 1.1 and clamped.overhead == 0

    counter = CalibratedCounter.from_calibration(lambda text, model: len(text), cal)
    assert counter("abcd") == 3 and counter.model == "m"
    msgs = [{"content": "ab"}, {"content": "cd"}]
    assert counter.messages(msgs) == 1 + 4 + 1 + 4
    assert counter.request(msgs) == counter.messages(msgs) + 120
