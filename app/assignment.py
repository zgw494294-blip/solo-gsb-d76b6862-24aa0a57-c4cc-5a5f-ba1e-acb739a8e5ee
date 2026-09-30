"""色谱峰归属核心算法（纯函数，全部使用精确有理数运算）。"""

from __future__ import annotations

from fractions import Fraction
from typing import Any

# DP 单元状态：(配对数, 绝对偏差之和, 配对下标对序列)
# 比较顺序与题目一致：先比配对数（大优），再比绝对偏差和（小优），
# 最后比 (ref_idx, meas_idx) 配对序列的字典序（小优）。
Cell = tuple[int, Fraction, tuple[tuple[int, int], ...]]


class AssignmentError(ValueError):
    """输入不满足业务规则，API 层映射为 HTTP 422。"""


def _frac(value: Fraction) -> dict[str, int]:
    """把约分有理数序列化为 {n, d}，分母恒为正。"""
    return {"n": value.numerator, "d": value.denominator}


def _better(a: Cell | None, b: Cell | None) -> Cell | None:
    """在两个 DP 状态中取更优者；None 表示不可达。"""
    if a is None:
        return b
    if b is None:
        return a
    if a[0] != b[0]:
        return a if a[0] > b[0] else b
    if a[1] != b[1]:
        return a if a[1] < b[1] else b
    return a if a[2] <= b[2] else b


def _solve_region(
    ref_times: list[int],
    meas_times: list[int],
    r_lo: int,
    r_hi: int,
    m_lo: int,
    m_hi: int,
    calibrated,
    tol: int,
) -> list[tuple[int, int]]:
    """在两个强制内标之间（或边界之外）的独立区间内做最优保序配对。

    对参考下标区间 [r_lo, r_hi) 与实测下标区间 [m_lo, m_hi) 做
    LCS 风格动态规划：每个参考峰/实测峰都可不配对，允许任意一侧连续跳过，
    因此不要求时间轴等距；状态只在“配对数更多 / 偏差和更小 / 序列字典序更小”
    时被保留。
    """
    nr = r_hi - r_lo
    nm = m_hi - m_lo
    if nr == 0 or nm == 0:
        return []

    # best[i][j]：处理完前 i 个参考峰、前 j 个实测峰后的最优状态。
    best: list[list[Cell | None]] = [
        [None] * (nm + 1) for _ in range(nr + 1)
    ]
    empty: Cell = (0, Fraction(0), ())
    best[0][0] = empty

    for i in range(nr + 1):
        for j in range(nm + 1):
            cur = best[i][j]
            if cur is None:
                continue
            # 跳过第 i 个参考峰
            if i < nr:
                best[i + 1][j] = _better(best[i + 1][j], cur)
            # 跳过第 j 个实测峰
            if j < nm:
                best[i][j + 1] = _better(best[i][j + 1], cur)
            # 第 i 个参考峰与第 j 个实测峰配对
            if i < nr and j < nm:
                ri = r_lo + i
                mj = m_lo + j
                cal = calibrated(meas_times[mj])
                signed = cal - ref_times[ri]
                if abs(signed) <= tol:
                    count, cost, pairs = cur
                    nxt: Cell = (
                        count + 1,
                        cost + abs(signed),
                        pairs + ((ri, mj),),
                    )
                    best[i + 1][j + 1] = _better(best[i + 1][j + 1], nxt)

    final = best[nr][nm]
    assert final is not None
    return list(final[2])


def assign_peaks(
    ref_peaks: list[dict[str, Any]],
    meas_times: list[int],
    is_ref_id_1: Any,
    is_meas_idx_1: int,
    is_ref_id_2: Any,
    is_meas_idx_2: int,
    tolerance_ms: int,
) -> dict[str, Any]:
    """峰归属主流程，返回可直接序列化的结果字典。"""
    ref_ids = [p["id"] for p in ref_peaks]
    ref_times = [p["time_ms"] for p in ref_peaks]

    # —— 参考峰：id 唯一、时间严格递增 ——
    if len(set(ref_ids)) != len(ref_ids):
        raise AssignmentError("reference peak ids must be unique")
    if any(
        ref_times[i] >= ref_times[i + 1] for i in range(len(ref_times) - 1)
    ):
        raise AssignmentError("reference times must be strictly increasing")

    n_meas = len(meas_times)
    for label, idx in (
        ("is_meas_idx_1", is_meas_idx_1),
        ("is_meas_idx_2", is_meas_idx_2),
    ):
        if not 0 <= idx < n_meas:
            raise AssignmentError(f"{label} out of range [0, {n_meas - 1}]")
    if is_meas_idx_1 == is_meas_idx_2:
        raise AssignmentError("the two measured internal standards must differ")

    id_to_idx = {pid: i for i, pid in enumerate(ref_ids)}
    if is_ref_id_1 not in id_to_idx:
        raise AssignmentError("is_ref_id_1 not found in reference peaks")
    if is_ref_id_2 not in id_to_idx:
        raise AssignmentError("is_ref_id_2 not found in reference peaks")
    if is_ref_id_1 == is_ref_id_2:
        raise AssignmentError("the two reference internal standards must differ")

    ir1 = id_to_idx[is_ref_id_1]
    ir2 = id_to_idx[is_ref_id_2]

    # 统一按参考时间轴方向排序两对内标；实测一侧必须同序（下标不交叉），
    # 且实测时间差与参考时间差同为正数，即仿射变换斜率严格为正。
    if ir1 > ir2:
        ir1, ir2 = ir2, ir1
        is_meas_idx_1, is_meas_idx_2 = is_meas_idx_2, is_meas_idx_1
    if is_meas_idx_1 > is_meas_idx_2:
        raise AssignmentError(
            "internal standard order must be consistent: "
            "measured indices must not cross"
        )
    if is_meas_idx_1 == is_meas_idx_2:  # pragma: no cover - 前面已拦截
        raise AssignmentError("the two measured internal standards must differ")

    t_ref_1, t_ref_2 = ref_times[ir1], ref_times[ir2]
    t_meas_1, t_meas_2 = meas_times[is_meas_idx_1], meas_times[is_meas_idx_2]
    if t_meas_2 <= t_meas_1:
        raise AssignmentError(
            "the two measured internal-standard times must be strictly ordered"
        )
    # t_ref_2 > t_ref_1 由参考时间严格递增保证。
    slope = Fraction(t_ref_2 - t_ref_1, t_meas_2 - t_meas_1)  # > 0
    intercept = t_ref_1 - slope * t_meas_1

    def calibrated(t: int) -> Fraction:
        return slope * t + intercept

    tol = tolerance_ms  # int 与 Fraction 可直接比较

    forced = ((ir1, is_meas_idx_1), (ir2, is_meas_idx_2))
    matched: list[tuple[int, int]] = []

    # 区间 1：两对内标之前
    matched += _solve_region(
        ref_times, meas_times,
        0, ir1, 0, is_meas_idx_1,
        calibrated, tol,
    )
    # 区间 2：两对内标之间
    matched += _solve_region(
        ref_times, meas_times,
        ir1 + 1, ir2, is_meas_idx_1 + 1, is_meas_idx_2,
        calibrated, tol,
    )
    # 区间 3：两对内标之后
    matched += _solve_region(
        ref_times, meas_times,
        ir2 + 1, len(ref_times), is_meas_idx_2 + 1, n_meas,
        calibrated, tol,
    )
    matched += list(forced)
    matched.sort()

    match_by_ref = {ri: mj for ri, mj in matched}
    used_meas = {mj for _, mj in matched}

    assignments: list[dict[str, Any] | None] = []
    matches_out: list[dict[str, Any]] = []
    for ri, peak in enumerate(ref_peaks):
        mj = match_by_ref.get(ri)
        assignments.append(None if mj is None else mj)
        if mj is not None:
            cal = calibrated(meas_times[mj])
            matches_out.append(
                {
                    "ref_id": peak["id"],
                    "ref_index": ri,
                    "meas_index": mj,
                    "calibrated_time_ms": _frac(cal),
                    "signed_deviation_ms": _frac(cal - ref_times[ri]),
                }
            )

    return {
        "assignments": assignments,
        "unpaired_meas_indices": [
            j for j in range(n_meas) if j not in used_meas
        ],
        "matches": matches_out,
    }
