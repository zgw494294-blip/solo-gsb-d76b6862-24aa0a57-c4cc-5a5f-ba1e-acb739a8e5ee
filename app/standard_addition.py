"""标准加入法定量（纯函数，全部使用精确有理数运算）。

逐次复用 ``assignment.assign_peaks`` 的保序归属定位目标峰，用第一枚内标的
面积对目标峰面积归一化，再对 (加标浓度, 面积比) 做含截距最小二乘拟合：

    area_ratio = slope * added_concentration + intercept

原样浓度取 x 轴截距绝对值 ``intercept / slope``。分数全程不转浮点。
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any

from .assignment import AssignmentError, assign_peaks


def _frac_out(value: Fraction) -> dict[str, int]:
    """把约分有理数序列化为 {n, d}，分母恒为正。"""
    return {"n": value.numerator, "d": value.denominator}


def standard_addition(
    ref_peaks: list[dict[str, Any]],
    target_ref_id: Any,
    is_ref_id_1: Any,
    is_ref_id_2: Any,
    tolerance_ms: int,
    injections: list[dict[str, Any]],
) -> dict[str, Any]:
    """标准加入法主流程，返回可直接序列化的结果字典。"""

    # —— 参考峰表层面的校验（assign_peaks 每次进样也会检查，这里先给出
    #     与进样下标无关的明确报错）——
    ref_ids = [p["id"] for p in ref_peaks]
    ref_times = [p["time_ms"] for p in ref_peaks]
    if len(set(ref_ids)) != len(ref_ids):
        raise AssignmentError("reference peak ids must be unique")
    if any(ref_times[i] >= ref_times[i + 1] for i in range(len(ref_times) - 1)):
        raise AssignmentError("reference times must be strictly increasing")

    id_to_idx = {pid: i for i, pid in enumerate(ref_ids)}
    if target_ref_id not in id_to_idx:
        raise AssignmentError("target_ref_id not found in reference peaks")
    if is_ref_id_1 not in id_to_idx:
        raise AssignmentError("is_ref_id_1 not found in reference peaks")
    if is_ref_id_2 not in id_to_idx:
        raise AssignmentError("is_ref_id_2 not found in reference peaks")
    if is_ref_id_1 == is_ref_id_2:
        raise AssignmentError("the two reference internal standards must differ")
    # 目标峰不得是内标
    if target_ref_id == is_ref_id_1 or target_ref_id == is_ref_id_2:
        raise AssignmentError("target peak must not be an internal standard")

    target_ref_idx = id_to_idx[target_ref_id]

    # 加标浓度至少有两个不同值（进样数 3～12 已由模型层保证）
    concentrations = [inj["added_concentration"] for inj in injections]
    if len(set(concentrations)) < 2:
        raise AssignmentError(
            "at least two distinct added_concentration values are required"
        )

    points: list[tuple[Fraction, Fraction]] = []  # (浓度, 面积比)
    residual_seed: list[dict[str, Any]] = []

    for k, inj in enumerate(injections):
        peaks = inj["measured_peaks"]
        meas_times = [p["time_ms"] for p in peaks]
        is1 = inj["is_meas_idx_1"]
        is2 = inj["is_meas_idx_2"]
        n_meas = len(peaks)

        # 归属前的下标级错误同样需指出进样下标
        if not 0 <= is1 < n_meas:
            raise AssignmentError(
                f"injection {k}: is_meas_idx_1 out of range [0, {n_meas - 1}]"
            )
        if not 0 <= is2 < n_meas:
            raise AssignmentError(
                f"injection {k}: is_meas_idx_2 out of range [0, {n_meas - 1}]"
            )
        if is1 == is2:
            raise AssignmentError(
                f"injection {k}: the two measured internal standards must differ"
            )

        try:
            assigned = assign_peaks(
                ref_peaks=ref_peaks,
                meas_times=meas_times,
                is_ref_id_1=is_ref_id_1,
                is_meas_idx_1=is1,
                is_ref_id_2=is_ref_id_2,
                is_meas_idx_2=is2,
                tolerance_ms=tolerance_ms,
            )
        except AssignmentError as exc:
            raise AssignmentError(f"injection {k}: {exc}") from exc

        target_meas_idx = assigned["assignments"][target_ref_idx]
        if target_meas_idx is None:
            raise AssignmentError(
                f"injection {k}: target peak not assigned within tolerance"
            )

        # 用请求中第一枚内标（未经方向归一化）的面积归一化
        is_area = peaks[is1]["area"]
        if is_area == 0:
            raise AssignmentError(
                f"injection {k}: internal standard 1 area must be non-zero"
            )
        target_area = peaks[target_meas_idx]["area"]
        ratio = Fraction(target_area, is_area)

        x = Fraction(concentrations[k])
        points.append((x, ratio))
        residual_seed.append(
            {
                "concentration": concentrations[k],
                "ratio": ratio,
            }
        )

    # —— 含截距最小二乘（闭式解，全程精确有理数）——
    n = len(points)
    sx = sum((x for x, _ in points), Fraction(0))
    sy = sum((y for _, y in points), Fraction(0))
    sxx = sum((x * x for x, _ in points), Fraction(0))
    sxy = sum((x * y for x, y in points), Fraction(0))

    denom = n * sxx - sx * sx
    # 浓度至少两个不同值 => Σ(x-x̄)² > 0 => denom > 0
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n

    if slope <= 0:
        raise AssignmentError("least-squares slope must be positive")
    if intercept < 0:
        raise AssignmentError("least-squares intercept must be non-negative")

    sample_concentration = intercept / slope  # 原样浓度（x 轴截距绝对值）

    residuals_out = []
    for seed in residual_seed:
        y = seed["ratio"]
        predicted = slope * seed["concentration"] + intercept
        residuals_out.append(
            {
                "added_concentration": seed["concentration"],
                "area_ratio": _frac_out(y),
                "predicted_area_ratio": _frac_out(predicted),
                "residual": _frac_out(y - predicted),
            }
        )

    return {
        "slope": _frac_out(slope),
        "intercept": _frac_out(intercept),
        "sample_concentration": _frac_out(sample_concentration),
        "residuals": residuals_out,
    }
