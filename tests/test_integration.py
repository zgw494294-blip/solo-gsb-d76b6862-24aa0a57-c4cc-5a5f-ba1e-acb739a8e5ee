"""峰面积积分核心算法测试：固定用例手算核对 + 随机实例与浮点实现对拍。"""

from __future__ import annotations

import random
from fractions import Fraction
from math import lcm

import pytest

from app.assignment import AssignmentError
from app.integration import integrate_peaks


def samples(times, intensities):
    return [
        {"time_ms": t, "intensity": y} for t, y in zip(times, intensities)
    ]


# 公共样例：时间轴 0..100（步长 10），两个明显峰 + 一个肩峰
TIMES = list(range(0, 101, 10))
INTENS = [2, 5, 9, 5, 3, 4, 8, 2, 5, 3, 2]


def test_two_peak_fixed_hand_computed():
    # 分界：峰顶 2 与 6 之间的内部点 3,4,5 强度为 5,3,4 -> 最低点 4
    res = integrate_peaks(samples(TIMES, INTENS), 0, 10, [2, 6])
    assert [(s["start_index"], s["end_index"]) for s in res["segments"]] == [
        (0, 4),
        (4, 10),
    ]
    # 手算：段 1 高度 0, 11/4, 13/2, 9/4, 0 -> 115；
    # 段 2 下标 7 处强度低于基线被截为 0 -> 100
    assert [s["area"] for s in res["segments"]] == [
        {"n": 115, "d": 1},
        {"n": 100, "d": 1},
    ]
    assert res["area_scale"] == 1
    assert res["peaks"] == [
        {"time_ms": 20, "area": 115},
        {"time_ms": 60, "area": 100},
    ]


def test_three_peaks_share_boundaries():
    # 峰顶 6 与 8 之间唯一内部点 7 为第二处分界
    res = integrate_peaks(samples(TIMES, INTENS), 0, 10, [2, 6, 8])
    assert [(s["start_index"], s["end_index"]) for s in res["segments"]] == [
        (0, 4),
        (4, 7),
        (7, 10),
    ]
    assert [s["area"] for s in res["segments"]] == [
        {"n": 115, "d": 1},
        {"n": 70, "d": 1},
        {"n": 40, "d": 1},
    ]
    assert res["area_scale"] == 1
    assert [p["area"] for p in res["peaks"]] == [115, 70, 40]
    assert [p["time_ms"] for p in res["peaks"]] == [20, 60, 80]


def test_fractional_area_and_lcm_scaling():
    # 非等距时间轴；段 1 面积 9/2，段 2 面积 12 -> 公倍数 2 统一放大
    res = integrate_peaks(
        samples([0, 3, 4, 7, 10], [1, 4, 2, 5, 0]), 0, 4, [1, 3]
    )
    assert [s["area"] for s in res["segments"]] == [
        {"n": 9, "d": 2},
        {"n": 12, "d": 1},
    ]
    assert res["area_scale"] == 2
    assert res["peaks"] == [
        {"time_ms": 3, "area": 9},
        {"time_ms": 7, "area": 24},
    ]


def test_boundary_tie_prefers_earliest_index():
    # 内部点 2、3 强度同为 2，并列取最早下标 2
    res = integrate_peaks(
        samples([0, 1, 2, 3, 4, 5], [0, 9, 2, 2, 9, 0]), 0, 5, [1, 4]
    )
    assert [(s["start_index"], s["end_index"]) for s in res["segments"]] == [
        (0, 2),
        (2, 5),
    ]
    assert [s["area"] for s in res["segments"]] == [
        {"n": 8, "d": 1},
        {"n": 9, "d": 1},
    ]


def test_zero_area_segments_are_retained():
    # 内部点强度全部低于基线，截非负后两段面积均为 0，峰数组仍保留
    res = integrate_peaks(
        samples([0, 1, 2, 3, 4], [5, 1, 1, 1, 5]), 0, 4, [1, 3]
    )
    assert [s["area"] for s in res["segments"]] == [
        {"n": 0, "d": 1},
        {"n": 0, "d": 1},
    ]
    assert res["area_scale"] == 1
    assert res["peaks"] == [
        {"time_ms": 1, "area": 0},
        {"time_ms": 3, "area": 0},
    ]


def test_nonuniform_times_clipping_and_lcm():
    # 非等距时间轴；段 2 中下标 5 处强度低于基线被截为 0
    times = [0, 2, 3, 7, 8, 10, 12]
    intens = [5, 9, 1, 9, 5, 3, 5]
    res = integrate_peaks(samples(times, intens), 0, 6, [1, 3])
    assert [(s["start_index"], s["end_index"]) for s in res["segments"]] == [
        (0, 2),
        (2, 6),
    ]
    # 手算：段 1 = 10，段 2 = 164/9
    assert [s["area"] for s in res["segments"]] == [
        {"n": 10, "d": 1},
        {"n": 164, "d": 9},
    ]
    assert res["area_scale"] == 9
    assert res["peaks"] == [
        {"time_ms": 2, "area": 90},
        {"time_ms": 7, "area": 164},
    ]


def test_apex_at_integration_bounds_rejected():
    with pytest.raises(AssignmentError):
        integrate_peaks(samples(TIMES, INTENS), 0, 10, [0, 6])
    with pytest.raises(AssignmentError):
        integrate_peaks(samples(TIMES, INTENS), 0, 10, [2, 10])


def test_adjacent_apices_need_internal_sample():
    with pytest.raises(AssignmentError, match="internal sample"):
        integrate_peaks(samples(TIMES, INTENS), 0, 10, [2, 3])


def test_non_increasing_times_rejected():
    bad = samples([0, 1, 2, 2, 4, 5], [0, 1, 2, 3, 2, 1])
    with pytest.raises(AssignmentError, match="strictly increasing"):
        integrate_peaks(bad, 0, 5, [1, 4])


# ---------------------------------------------------------------------------
# 随机实例与独立浮点实现对拍
# ---------------------------------------------------------------------------

def _float_integrate(times, intens, start, end, apices):
    """独立浮点参照实现（仅用于对拍）。"""
    bounds = [start]
    for i in range(len(apices) - 1):
        lo, hi = apices[i], apices[i + 1]
        valley = min(range(lo + 1, hi), key=lambda j: (intens[j], j))
        bounds.append(valley)
    bounds.append(end)
    areas = []
    for i in range(len(apices)):
        a, b = bounds[i], bounds[i + 1]
        ta, tb = times[a], times[b]
        ya, yb = intens[a], intens[b]
        heights = []
        for j in range(a, b + 1):
            base = ya + (yb - ya) * (times[j] - ta) / (tb - ta)
            heights.append(max(0.0, intens[j] - base))
        area = 0.0
        for j in range(a, b):
            area += (heights[j - a] + heights[j - a + 1]) * (
                times[j + 1] - times[j]
            ) / 2
        areas.append(area)
    return bounds, areas


def test_random_against_float_reference():
    rng = random.Random(20260929)
    for _ in range(300):
        n = rng.randint(5, 60)
        times = sorted(rng.sample(range(0, 100000), n))
        intens = [rng.randint(0, 2000) for _ in range(n)]
        start = rng.randint(0, n - 5)
        end = rng.randint(start + 4, n - 1)
        while True:
            apices = []
            for j in range(start + 1, end):
                if (not apices or j - apices[-1] >= 2) and rng.random() < 0.4:
                    apices.append(j)
            if len(apices) >= 2:
                break

        res = integrate_peaks(samples(times, intens), start, end, apices)
        bounds, float_areas = _float_integrate(
            times, intens, start, end, apices
        )

        # 段边界与参照一致，且首尾相接覆盖整个积分区间
        got_bounds = [start] + [s["end_index"] for s in res["segments"]]
        assert got_bounds == bounds
        for s1, s2 in zip(res["segments"], res["segments"][1:]):
            assert s1["end_index"] == s2["start_index"]

        # 面积与浮点参照一致（精确分数 vs 浮点近似）
        exact_areas = [
            Fraction(s["area"]["n"], s["area"]["d"]) for s in res["segments"]
        ]
        for exact, approx in zip(exact_areas, float_areas):
            assert float(exact) == pytest.approx(approx, rel=1e-12, abs=1e-9)

        # 放大倍数为全部面积分母的最小公倍数，峰面积为整数
        assert res["area_scale"] == lcm(
            *(a.denominator for a in exact_areas)
        )
        assert len(res["peaks"]) == len(apices)  # 零面积保留
        for apex, exact, peak in zip(apices, exact_areas, res["peaks"]):
            assert peak["time_ms"] == times[apex]
            assert peak["area"] == exact * res["area_scale"]
            assert isinstance(peak["area"], int)
        # 峰数组按峰顶时间排序
        peak_times = [p["time_ms"] for p in res["peaks"]]
        assert peak_times == sorted(peak_times)
