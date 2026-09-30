"""原始色谱强度采样的峰面积积分（纯函数，全部使用精确有理数运算）。

相邻峰顶之间的内部采样点中取强度最低者作为共用分界（并列取最早下标），
每个峰顶独占一段。每段以两端采样点连线为基线，逐点将强度减基线后截为
非负，再按实际时间间隔做精确梯形积分。各段面积以 fractions.Fraction
精确表示，最后用全部面积分母的最小公倍数统一放大为整数峰面积，
得到的 {time_ms, area} 峰数组可直接供标准加入法定量接口使用，
其时间序列可供峰归属接口使用。
"""

from __future__ import annotations

from fractions import Fraction
from math import lcm
from typing import Any

from .assignment import AssignmentError


def _frac_out(value: Fraction) -> dict[str, int]:
    """把约分有理数序列化为 {n, d}，分母恒为正。"""
    return {"n": value.numerator, "d": value.denominator}


def integrate_peaks(
    samples: list[dict[str, Any]],
    start_index: int,
    end_index: int,
    apex_indices: list[int],
) -> dict[str, Any]:
    """峰面积积分主流程，返回可直接序列化的结果字典。"""
    n = len(samples)
    times = [s["time_ms"] for s in samples]
    intensities = [s["intensity"] for s in samples]

    # —— 采样点：时间严格递增（点数 5～500、强度非负整数由模型层保证）——
    if any(times[i] >= times[i + 1] for i in range(n - 1)):
        raise AssignmentError("sample times must be strictly increasing")

    # —— 积分起止下标 ——
    if not 0 <= start_index < n:
        raise AssignmentError(f"start_index out of range [0, {n - 1}]")
    if not 0 <= end_index < n:
        raise AssignmentError(f"end_index out of range [0, {n - 1}]")
    if start_index >= end_index:
        raise AssignmentError("start_index must be less than end_index")

    # —— 峰顶下标：严格递增、严格位于起止之间、相邻至少隔一个内部点 ——
    if any(
        apex_indices[i] >= apex_indices[i + 1]
        for i in range(len(apex_indices) - 1)
    ):
        raise AssignmentError("apex indices must be strictly increasing")
    if not start_index < apex_indices[0] or not apex_indices[-1] < end_index:
        raise AssignmentError(
            "apex indices must lie strictly between start_index and end_index"
        )
    if any(
        apex_indices[i + 1] - apex_indices[i] < 2
        for i in range(len(apex_indices) - 1)
    ):
        raise AssignmentError(
            "adjacent apex indices must be separated by "
            "at least one internal sample"
        )

    # —— 共用分界：相邻峰顶间强度最低的内部点，并列取最早下标 ——
    bounds = [start_index]
    for i in range(len(apex_indices) - 1):
        lo, hi = apex_indices[i], apex_indices[i + 1]
        valley = min(range(lo + 1, hi), key=lambda j: (intensities[j], j))
        bounds.append(valley)
    bounds.append(end_index)

    # —— 逐段扣基线、截非负、按实际时间间隔精确梯形积分 ——
    segments: list[dict[str, Any]] = []
    areas: list[Fraction] = []
    for i in range(len(apex_indices)):
        a, b = bounds[i], bounds[i + 1]
        t_a, t_b = times[a], times[b]
        y_a, y_b = intensities[a], intensities[b]
        span = t_b - t_a  # 时间严格递增 => span > 0

        # 段两端点处基线恒等于强度，截断后高度为 0
        heights: list[Fraction] = []
        for j in range(a, b + 1):
            baseline = y_a + Fraction(y_b - y_a) * (times[j] - t_a) / span
            h = intensities[j] - baseline
            heights.append(h if h > 0 else Fraction(0))

        area = Fraction(0)
        for j in range(a, b):
            area += (heights[j - a] + heights[j - a + 1]) * (
                times[j + 1] - times[j]
            ) / 2
        segments.append(
            {"start_index": a, "end_index": b, "area": _frac_out(area)}
        )
        areas.append(area)

    # —— 用全部面积分母的最小公倍数统一放大为整数面积 ——
    scale = lcm(*(area.denominator for area in areas))
    # 峰顶下标严格递增且采样时间严格递增，峰数组天然按峰顶时间排序；
    # 零面积的峰保留在数组中。
    peaks = [
        {"time_ms": times[apex], "area": int(area * scale)}
        for apex, area in zip(apex_indices, areas)
    ]

    return {"segments": segments, "area_scale": scale, "peaks": peaks}
