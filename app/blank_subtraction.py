"""样本/空白两次色谱运行的空白扣除（纯函数，全部使用精确有理数运算）。

对样本与空白两组原始强度采样分别复用 ``integration.integrate_peaks`` 积分，
并按峰顶时间复用 ``assignment.assign_peaks`` 归属（两组的采样时间与面积
放大倍数 area_scale 可以不同）。仅当样本峰与空白峰归属**同一参考 id** 时，
用各自整数面积除以各自 area_scale 后的精确值相减，负值截为零；空白没有
该参考峰则不扣，样本未归属峰保持原面积，空白独有峰不输出。全部校正面积
分母的最小公倍数作为统一 area_scale 放大为整数峰数组（零面积保留），
可直接供标准加入法定量接口使用。
"""

from __future__ import annotations

from fractions import Fraction
from math import lcm
from typing import Any

from .assignment import AssignmentError, assign_peaks
from .integration import integrate_peaks


def _frac_out(value: Fraction) -> dict[str, int]:
    """把约分有理数序列化为 {n, d}，分母恒为正。"""
    return {"n": value.numerator, "d": value.denominator}


def _integrate_and_assign(
    run: dict[str, Any],
    ref_peaks: list[dict[str, Any]],
    is_ref_id_1: Any,
    is_ref_id_2: Any,
    tolerance_ms: int,
    label: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """对单次运行积分并按峰顶时间归属；失败时在消息前指出 sample/blank。"""
    try:
        integrated = integrate_peaks(
            samples=run["samples"],
            start_index=run["start_index"],
            end_index=run["end_index"],
            apex_indices=list(run["apex_indices"]),
        )
        # 积分输出的峰数组与峰顶下标同序，故内标在峰顶列表中的下标
        # 即归属接口中的实测下标。
        assigned = assign_peaks(
            ref_peaks=ref_peaks,
            meas_times=[p["time_ms"] for p in integrated["peaks"]],
            is_ref_id_1=is_ref_id_1,
            is_meas_idx_1=run["is_apex_idx_1"],
            is_ref_id_2=is_ref_id_2,
            is_meas_idx_2=run["is_apex_idx_2"],
            tolerance_ms=tolerance_ms,
        )
    except AssignmentError as exc:
        raise AssignmentError(f"{label}: {exc}") from exc
    return integrated, assigned


def blank_subtraction(
    ref_peaks: list[dict[str, Any]],
    is_ref_id_1: Any,
    is_ref_id_2: Any,
    tolerance_ms: int,
    sample: dict[str, Any],
    blank: dict[str, Any],
    *,
    sample_label: str = "sample",
    blank_label: str = "blank",
) -> dict[str, Any]:
    """空白扣除主流程，返回可直接序列化的结果字典。

    ``sample_label`` / ``blank_label`` 用于失败消息前缀：单样本接口固定为
    ``sample`` / ``blank``；批次接口传入带样本 id 的前缀（如 ``sample "s2"``）。
    """
    ref_ids = [p["id"] for p in ref_peaks]

    sample_int, sample_asg = _integrate_and_assign(
        sample, ref_peaks, is_ref_id_1, is_ref_id_2, tolerance_ms, sample_label
    )
    blank_int, blank_asg = _integrate_and_assign(
        blank, ref_peaks, is_ref_id_1, is_ref_id_2, tolerance_ms, blank_label
    )

    # 空白：参考 id -> 精确面积（整数面积 / 自身 area_scale）；
    # 归属为一对一，每个参考 id 至多对应一个空白峰。
    blank_area_by_ref: dict[Any, Fraction] = {}
    for ref_idx, meas_idx in enumerate(blank_asg["assignments"]):
        if meas_idx is not None:
            blank_area_by_ref[ref_ids[ref_idx]] = Fraction(
                blank_int["peaks"][meas_idx]["area"], blank_int["area_scale"]
            )

    # 样本：实测（峰顶）下标 -> 参考 id
    sample_ref_by_meas = {
        meas_idx: ref_ids[ref_idx]
        for ref_idx, meas_idx in enumerate(sample_asg["assignments"])
        if meas_idx is not None
    }

    corrected: list[Fraction] = []
    sample_peaks_out: list[dict[str, Any]] = []
    for meas_idx, peak in enumerate(sample_int["peaks"]):
        ref_id = sample_ref_by_meas.get(meas_idx)
        area = Fraction(peak["area"], sample_int["area_scale"])
        if ref_id is not None and ref_id in blank_area_by_ref:
            area -= blank_area_by_ref[ref_id]
            if area < 0:
                area = Fraction(0)
        corrected.append(area)
        sample_peaks_out.append(
            {"ref_id": ref_id, "corrected_area": _frac_out(area)}
        )

    # —— 用全部校正面积分母的最小公倍数统一放大为整数面积 ——
    scale = lcm(*(area.denominator for area in corrected))
    # 样本峰顶下标严格递增且采样时间严格递增，峰数组天然按峰顶时间排序；
    # 零面积的峰保留在数组中。空白独有峰不在样本峰顶序列中，自然不输出。
    peaks = [
        {"time_ms": peak["time_ms"], "area": int(area * scale)}
        for peak, area in zip(sample_int["peaks"], corrected)
    ]

    return {
        "sample_peaks": sample_peaks_out,
        "area_scale": scale,
        "peaks": peaks,
    }
