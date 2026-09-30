"""标准加入法核心算法测试：固定用例 + 精确闭式解核对。"""

from __future__ import annotations

from fractions import Fraction

import pytest

from app.assignment import AssignmentError
from app.standard_addition import standard_addition

# 参考时间轴：junk=10, target T=20, 内标 S1=30, S2=40
REFS = [
    {"id": "junk", "time_ms": 10},
    {"id": "T", "time_ms": 20},
    {"id": "S1", "time_ms": 30},
    {"id": "S2", "time_ms": 40},
]


def inj(concentration, target, a1, a2, junk=0):
    return {
        "added_concentration": concentration,
        "measured_peaks": [
            {"time_ms": 10, "area": junk},
            {"time_ms": 20, "area": target},
            {"time_ms": 30, "area": a1},
            {"time_ms": 40, "area": a2},
        ],
        "is_meas_idx_1": 2,
        "is_meas_idx_2": 3,
    }


def _ols(points):
    n = len(points)
    sx = sum((x for x, _ in points), Fraction(0))
    sy = sum((y for _, y in points), Fraction(0))
    sxx = sum((x * x for x, _ in points), Fraction(0))
    sxy = sum((x * y for x, y in points), Fraction(0))
    slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    intercept = (sy - slope * sx) / n
    return slope, intercept


def make_case():
    raw = [
        (0, 412, 1000, 700),
        (10, 510, 1000, 700),
        (20, 708, 1200, 800),
        (40, 1008, 1500, 900),
    ]
    injections = [inj(c, t, a1, a2) for c, t, a1, a2 in raw]
    points = [(Fraction(c), Fraction(t, a1)) for c, t, a1, _ in raw]
    return injections, points


def test_exact_ols_results():
    injections, points = make_case()
    res = standard_addition(REFS, "T", "S1", "S2", 0, injections)

    slope, intercept = _ols(points)
    assert res["slope"] == {"n": slope.numerator, "d": slope.denominator}
    assert res["intercept"] == {"n": intercept.numerator, "d": intercept.denominator}
    sample = intercept / slope
    assert res["sample_concentration"] == {
        "n": sample.numerator,
        "d": sample.denominator,
    }

    # 固定值，锁定精确分数
    assert res["slope"] == {"n": 139, "d": 21875}
    assert res["intercept"] == {"n": 1087, "d": 2500}
    assert res["sample_concentration"] == {"n": 38045, "d": 556}


def test_residuals_observed_minus_predicted_and_ordered():
    injections, points = make_case()
    res = standard_addition(REFS, "T", "S1", "S2", 0, injections)
    slope, intercept = _ols(points)

    assert [r["added_concentration"] for r in res["residuals"]] == [0, 10, 20, 40]
    total = Fraction(0)
    for r, (x, y) in zip(res["residuals"], points):
        predicted = slope * x + intercept
        assert r["area_ratio"] == {"n": y.numerator, "d": y.denominator}
        assert r["predicted_area_ratio"] == {
            "n": predicted.numerator,
            "d": predicted.denominator,
        }
        residual = y - predicted
        assert r["residual"] == {
            "n": residual.numerator,
            "d": residual.denominator,
        }
        total += residual
    # OLS 含截距 => 残差和恰为 0（精确）
    assert total == 0


def test_zero_residual_perfect_line():
    # 面积比 = (6/1000) * c + 1/2，原样浓度 = 250/3
    raw = [(0, 500, 1000), (10, 560, 1000), (20, 620, 1000), (50, 800, 1000)]
    injections = [inj(c, t, a1, 999) for c, t, a1 in raw]
    res = standard_addition(REFS, "T", "S1", "S2", 0, injections)
    assert res["slope"] == {"n": 3, "d": 500}
    assert res["intercept"] == {"n": 1, "d": 2}
    assert res["sample_concentration"] == {"n": 250, "d": 3}
    assert all(r["residual"] == {"n": 0, "d": 1} for r in res["residuals"])


def test_target_located_within_tolerance_with_shuffled_peaks():
    # 实测轴与参考轴之间存在仿射变换：t_cal = t_meas - 1000
    # 杂峰干扰 + 容差门限，目标仍须被保序归属正确定位
    injections = [
        {
            "added_concentration": c,
            "measured_peaks": [
                {"time_ms": -910, "area": 1},       # 杂峰，校准后远离任何参考
                {"time_ms": 1005, "area": 7},       # 校准 5，对 junk=10 偏差恰为 5
                {"time_ms": 1020, "area": 500 + 6 * c},  # <- T=20 校准后
                {"time_ms": 1030, "area": 1000},   # <- S1=30
                {"time_ms": 1040, "area": 42},     # <- S2=40
            ],
            "is_meas_idx_1": 3,
            "is_meas_idx_2": 4,
        }
        for c in (0, 10, 20)
    ]
    res = standard_addition(REFS, "T", "S1", "S2", 5, injections)
    assert res["slope"] == {"n": 3, "d": 500}
    assert res["intercept"] == {"n": 1, "d": 2}


def test_target_unassigned_reports_injection_index():
    # 第 2 次进样（下标 1）目标峰缺失
    injections, _ = make_case()
    injections[1]["measured_peaks"][1]["time_ms"] = 9999
    with pytest.raises(AssignmentError, match=r"^injection 1: target peak not assigned"):
        standard_addition(REFS, "T", "S1", "S2", 0, injections)


def test_zero_is1_area_reports_injection_index():
    injections, _ = make_case()
    injections[2]["measured_peaks"][2]["area"] = 0
    with pytest.raises(AssignmentError, match=r"^injection 2: .*non-zero"):
        standard_addition(REFS, "T", "S1", "S2", 0, injections)

    # 第二枚内标面积为零不参与归一化，允许通过
    injections2, _ = make_case()
    injections2[0]["measured_peaks"][3]["area"] = 0
    standard_addition(REFS, "T", "S1", "S2", 0, injections2)


def test_normalization_uses_first_is_area():
    # 内标书写顺序与参考方向相反：归一化仍须使用“请求中的第一枚”(S2)
    def inj_swapped(c, target, a1, a2):
        return {
            "added_concentration": c,
            "measured_peaks": [
                {"time_ms": 10, "area": 0},
                {"time_ms": 20, "area": target},
                {"time_ms": 30, "area": a1},
                {"time_ms": 40, "area": a2},
            ],
            "is_meas_idx_1": 3,  # S2
            "is_meas_idx_2": 2,  # S1
        }

    raw = [(0, 500, 1000, 800), (10, 548, 1000, 800), (20, 596, 1000, 800)]
    injections = [inj_swapped(c, t, a1, a2) for c, t, a1, a2 in raw]
    res = standard_addition(REFS, "T", "S2", "S1", 0, injections)
    # 面积比 = target/800：斜率 3/500，截距 5/8，原样浓度 625/6
    assert res["slope"] == {"n": 3, "d": 500}
    assert res["intercept"] == {"n": 5, "d": 8}
    assert res["sample_concentration"] == {"n": 625, "d": 6}


def test_target_must_not_be_internal_standard():
    injections, _ = make_case()
    with pytest.raises(AssignmentError, match="target peak must not be an internal standard"):
        standard_addition(REFS, "S1", "S1", "S2", 0, injections)
    with pytest.raises(AssignmentError, match="target peak must not be an internal standard"):
        standard_addition(REFS, "S2", "S1", "S2", 0, injections)


def test_target_id_missing():
    injections, _ = make_case()
    with pytest.raises(AssignmentError, match="target_ref_id not found"):
        standard_addition(REFS, "nope", "S1", "S2", 0, injections)


def test_fewer_than_two_distinct_concentrations():
    injections, _ = make_case()
    for inj_ in injections:
        inj_["added_concentration"] = 5
    with pytest.raises(AssignmentError, match="at least two distinct"):
        standard_addition(REFS, "T", "S1", "S2", 0, injections)


def test_non_positive_slope():
    # 面积比随加标浓度递减 -> 斜率为负
    raw = [(0, 800, 1000), (10, 700, 1000), (20, 600, 1000)]
    injections = [inj(c, t, a1, 5) for c, t, a1 in raw]
    with pytest.raises(AssignmentError, match="slope must be positive"):
        standard_addition(REFS, "T", "S1", "S2", 0, injections)

    # 面积比恒定 -> 斜率为零
    raw0 = [(0, 500, 1000), (10, 500, 1000), (20, 500, 1000)]
    injections0 = [inj(c, t, a1, 5) for c, t, a1 in raw0]
    with pytest.raises(AssignmentError, match="slope must be positive"):
        standard_addition(REFS, "T", "S1", "S2", 0, injections0)


def test_negative_intercept():
    # 正斜率但直线外推到零浓度时面积比为负：截距 -1/3000
    raw = [(0, 0, 1000), (10, 1000, 1000), (20, 2001, 1000)]
    injections = [inj(c, t, a1, 5) for c, t, a1 in raw]
    with pytest.raises(AssignmentError, match="intercept must be non-negative"):
        standard_addition(REFS, "T", "S1", "S2", 0, injections)


def test_zero_intercept_accepted():
    # 面积比严格过原点：原样浓度 0
    raw = [(0, 0, 1000), (10, 60, 1000), (20, 120, 1000)]
    injections = [inj(c, t, a1, 5) for c, t, a1 in raw]
    res = standard_addition(REFS, "T", "S1", "S2", 0, injections)
    assert res["intercept"] == {"n": 0, "d": 1}
    assert res["sample_concentration"] == {"n": 0, "d": 1}


def test_assignment_error_in_injection_is_indexed():
    # 第 0 次进样两个内标实测下标相同
    injections, _ = make_case()
    injections[0]["is_meas_idx_2"] = injections[0]["is_meas_idx_1"]
    with pytest.raises(AssignmentError, match=r"^injection 0:"):
        standard_addition(REFS, "T", "S1", "S2", 0, injections)

    # 第 2 次进样内标下标越界
    injections, _ = make_case()
    injections[2]["is_meas_idx_1"] = 99
    with pytest.raises(AssignmentError, match=r"^injection 2:"):
        standard_addition(REFS, "T", "S1", "S2", 0, injections)
