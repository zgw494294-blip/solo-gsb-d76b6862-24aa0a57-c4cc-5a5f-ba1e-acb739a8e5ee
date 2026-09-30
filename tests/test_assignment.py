"""核心算法测试：固定用例 + 与全局暴力枚举对拍的随机测试。"""

from __future__ import annotations

import itertools
import random
from fractions import Fraction

import pytest

from app.assignment import AssignmentError, assign_peaks


def refs(pairs):
    return [{"id": str(i), "time_ms": t} for i, t in enumerate(pairs)]


# ---------------------------------------------------------------------------
# 固定用例
# ---------------------------------------------------------------------------

def test_basic_matching():
    # 仿射校准：校准时间 = (3/2) * 实测时间 + 1
    # 内标：(ref0=10, meas10=6)、(ref4=40, meas26)
    r = refs([10, 22, 31, 34, 40, 55])
    meas = [3, 6, 14, 20, 22, 26, 36]
    res = assign_peaks(r, meas, "0", 1, "4", 5, 0)

    # ref1=22 <- meas2=14(22), ref2=31 <- meas3=20(31),
    # ref3=34 <- meas4=22(34), ref5=55 <- meas6=36(55)
    assert res["assignments"] == [1, 2, 3, 4, 5, 6]
    assert res["unpaired_meas_indices"] == [0]
    assert len(res["matches"]) == 6
    # 内标偏差为 0，且为整数值 10/1
    m0 = next(m for m in res["matches"] if m["ref_id"] == "0")
    assert m0["calibrated_time_ms"] == {"n": 10, "d": 1}
    assert m0["signed_deviation_ms"] == {"n": 0, "d": 1}
    # 分数斜率产生的非整数校准值：meas=3 未配对，检查 meas=14 -> 22 为整数
    m1 = next(m for m in res["matches"] if m["ref_id"] == "1")
    assert m1["calibrated_time_ms"] == {"n": 22, "d": 1}
    assert m1["signed_deviation_ms"] == {"n": 0, "d": 1}


def test_fraction_slope_and_tolerance():
    # slope = 3/2, intercept = 1
    r = refs([10, 25, 40])
    meas = [6, 15, 26]  # 校准: 10, 47/2=23.5, 40
    res = assign_peaks(r, meas, "0", 0, "2", 2, 1)
    # ref1=25：候选 meas1=15 -> 47/2，偏差 -3/2，超出 tol=1
    assert res["assignments"] == [0, None, 2]
    assert res["unpaired_meas_indices"] == [1]
    # tol=2 时可配对，偏差 -3/2
    res2 = assign_peaks(r, meas, "0", 0, "2", 2, 2)
    assert res2["assignments"] == [0, 1, 2]
    m = next(x for x in res2["matches"] if x["ref_id"] == "1")
    assert m["calibrated_time_ms"] == {"n": 47, "d": 2}
    assert m["signed_deviation_ms"] == {"n": -3, "d": 2}


def test_lex_tie_break():
    # 配对数/偏差和并列时，取 (参考下标, 实测下标) 配对序列字典序最小者。
    # 恒等校准（IS: ref0<-meas1(0)，ref2<-meas5(20)）。
    r = refs([0, 10, 20])
    meas = [-100, 0, 0, 0, 10, 20]
    res = assign_peaks(r, meas, "0", 1, "2", 5, 0)
    # ref1=10 只能配 meas4=10；meas2/meas3=0 偏差 10 超出 tol=0
    assert res["assignments"] == [1, 4, 5]
    assert res["unpaired_meas_indices"] == [0, 2, 3]

    # ref1=10 有 meas1/meas2 两个等偏差候选，取较小下标 meas1
    r3 = refs([0, 10, 20, 30])
    meas3 = [0, 10, 10, 30]
    res3 = assign_peaks(r3, meas3, "0", 0, "3", 3, 0)
    assert res3["assignments"] == [0, 1, None, 3]
    assert res3["unpaired_meas_indices"] == [2]


def test_cost_breaks_count_tie():
    # 配对数相同时取绝对偏差和更小的方案，即使其字典序更大。
    # 恒等校准，tol=2
    r = refs([0, 10, 20])
    # IS 固定 ref0<-meas0(0)、ref2<-meas4(20)
    # 区间内 ref1=10 候选 meas2(9, 偏差1) 与 meas3(12, 偏差2)
    meas = [0, 99, 9, 12, 20]
    res = assign_peaks(r, meas, "0", 0, "2", 4, 2)
    assert res["assignments"] == [0, 2, 4]
    assert res["unpaired_meas_indices"] == [1, 3]


def test_two_reference_only():
    r = refs([5, 15])
    meas = [5, 15]
    res = assign_peaks(r, meas, "0", 0, "1", 1, 0)
    assert res["assignments"] == [0, 1]
    assert res["unpaired_meas_indices"] == []


def test_two_measured_only():
    r = refs([5, 15, 25])
    meas = [5, 15]
    res = assign_peaks(r, meas, "0", 0, "1", 1, 0)
    assert res["assignments"] == [0, 1, None]
    assert res["unpaired_meas_indices"] == []


# ---------------------------------------------------------------------------
# 非法输入
# ---------------------------------------------------------------------------

def test_duplicate_ref_ids():
    r = [{"id": "a", "time_ms": 0}, {"id": "a", "time_ms": 10},
         {"id": "c", "time_ms": 20}]
    with pytest.raises(AssignmentError, match="unique"):
        assign_peaks(r, [0, 5, 10], "a", 0, "c", 2, 1)


def test_ref_times_not_increasing():
    with pytest.raises(AssignmentError, match="strictly increasing"):
        assign_peaks(refs([0, 10, 10]), [0, 5, 10], "0", 0, "2", 2, 1)
    with pytest.raises(AssignmentError, match="strictly increasing"):
        assign_peaks(refs([0, 12, 10]), [0, 5, 10], "0", 0, "2", 2, 1)


def test_meas_index_out_of_range():
    r = refs([0, 10])
    with pytest.raises(AssignmentError, match="out of range"):
        assign_peaks(r, [0, 10], "0", 0, "1", 5, 0)
    with pytest.raises(AssignmentError, match="out of range"):
        assign_peaks(r, [0, 10], "0", -1, "1", 1, 0)


def test_same_measured_is():
    with pytest.raises(AssignmentError, match="measured internal standards"):
        assign_peaks(refs([0, 10, 20]), [0, 10], "0", 0, "2", 0, 1)


def test_same_reference_is():
    with pytest.raises(AssignmentError, match="reference internal standards"):
        assign_peaks(refs([0, 10, 20]), [0, 10, 20], "1", 0, "1", 1, 0)


def test_missing_ref_is_id():
    with pytest.raises(AssignmentError, match="not found"):
        assign_peaks(refs([0, 10]), [0, 10], "x", 0, "1", 1, 0)


def test_crossed_internal_standards():
    # ref 顺序为 0->2，但实测下标 3->1 交叉
    with pytest.raises(AssignmentError, match="order must be consistent"):
        assign_peaks(refs([0, 10, 20]), [0, 5, 10, 20], "0", 3, "2", 1, 5)


def test_non_positive_measured_is_slope():
    # 实测内标时间相等 -> 斜率无定义
    with pytest.raises(AssignmentError, match="strictly ordered"):
        assign_peaks(refs([0, 10]), [5, 5], "0", 0, "1", 1, 0)
    # 实测内标时间逆序
    with pytest.raises(AssignmentError, match="strictly ordered"):
        assign_peaks(refs([0, 10]), [10, 0], "0", 0, "1", 1, 0)


# ---------------------------------------------------------------------------
# 与暴力枚举对拍
# ---------------------------------------------------------------------------

def _brute_force(ref_times, meas_times, forced, slope, intercept, tol):
    """枚举所有含强制配对的保序链，返回与算法相同键下的最优配对集合。"""
    def cal(t):
        return slope * t + intercept

    forced_set = set(forced)
    used_r = {ri for ri, _ in forced}
    used_m = {mj for _, mj in forced}

    candidates = list(forced)
    for ri in range(len(ref_times)):
        if ri in used_r:
            continue
        for mj in range(len(meas_times)):
            if mj in used_m:
                continue
            dev = cal(meas_times[mj]) - ref_times[ri]
            if abs(dev) <= tol:
                candidates.append((ri, mj))

    candidates.sort()
    best_key = None

    def cost_of(pairs):
        return sum(abs(cal(meas_times[mj]) - ref_times[ri]) for ri, mj in pairs)

    def search(k, chosen, last_r, last_m):
        nonlocal best_key
        if k == len(candidates):
            pairs = tuple(sorted(chosen))
            # 强制对必须全部出现
            if not forced_set.issubset(pairs):
                return
            key = (-len(pairs), cost_of(pairs), pairs)
            if best_key is None or key < best_key:
                best_key = key
            return
        ri, mj = candidates[k]
        # 不选
        search(k + 1, chosen, last_r, last_m)
        # 选（严格保序、一对一）
        if ri > last_r and mj > last_m:
            search(k + 1, chosen + [(ri, mj)], ri, mj)

    search(0, [], -1, -1)
    return list(best_key[2])


def test_fuzz_against_brute_force():
    rng = random.Random(20260928)
    for case in range(400):
        n_ref = rng.randint(2, 7)
        n_meas = rng.randint(2, 8)
        # 真实校准参数
        a = rng.choice([Fraction(1), Fraction(2), Fraction(1, 2),
                        Fraction(3, 2), Fraction(2, 3)])
        b = rng.randint(0, 20)
        # 参考时间严格递增
        ref_times = sorted(rng.sample(range(0, 120), n_ref))
        # 选两个参考内标
        ir1, ir2 = sorted(rng.sample(range(n_ref), 2))

        # 构造实测时间网格：参考时间经逆变换后加噪，再插入杂峰/重复
        meas_values = []
        im1 = im2 = None
        for ri, rt in enumerate(ref_times):
            # 逆变换（a 为正）：t = (rt - b)/a
            base = (Fraction(rt) - b) / a
            noise = rng.choice([-3, -2, -1, 0, 0, 0, 1, 2, 3])
            t = base + noise
            if t.denominator != 1:
                continue
            meas_values.append((ri, int(t)))
        # 保证两对内标存在且无噪
        for target_ri in (ir1, ir2):
            t = int((Fraction(ref_times[target_ri]) - b) / a)
            meas_values.append((target_ri, t))
        rng.shuffle(meas_values)
        meas_values.sort(key=lambda x: x[1])  # 按时间排序使下标与时间一致
        meas_times = [t for _, t in meas_values]
        if len(meas_times) < 2:
            continue
        # 找两个内标的下标（同时间取唯一位置：去重后再定位）
        t1 = int((Fraction(ref_times[ir1]) - b) / a)
        t2 = int((Fraction(ref_times[ir2]) - b) / a)
        if t1 == t2:
            continue
        try:
            im1 = meas_times.index(t1)
            im2 = meas_times.index(t2)
        except ValueError:
            continue
        if im1 == im2:
            continue

        ids = [str(i) for i in range(n_ref)]
        tol = rng.choice([0, 1, 2, 5])
        slope = Fraction(ref_times[ir2] - ref_times[ir1],
                         meas_times[im2] - meas_times[im1])
        intercept = ref_times[ir1] - slope * meas_times[im1]

        expected = _brute_force(
            ref_times, meas_times,
            [(ir1, im1), (ir2, im2)],
            slope, intercept, tol,
        )

        res = assign_peaks(
            [{"id": ids[i], "time_ms": ref_times[i]} for i in range(n_ref)],
            meas_times,
            ids[ir1], im1, ids[ir2], im2, tol,
        )
        got = [(ri, mj) for ri, mj in enumerate(res["assignments"])
               if mj is not None]
        assert sorted(got) == sorted(expected), (
            f"case {case}: {got} != {expected}\n"
            f"refs={ref_times} meas={meas_times} IS=({ir1},{im1})({ir2},{im2}) "
            f"tol={tol} a={a} b={b}"
        )
        # 未配对下标互补且升序
        paired_m = {mj for _, mj in got}
        assert res["unpaired_meas_indices"] == [
            j for j in range(len(meas_times)) if j not in paired_m
        ]
