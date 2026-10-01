"""请求/响应 Pydantic 模型。

严格类型：布尔值不会被当作整数，浮点不会被静默截断为整数，
类型层拒绝统一产生 HTTP 422。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, StrictInt, StrictStr, field_validator


class ReferencePeak(BaseModel):
    id: StrictStr
    time_ms: StrictInt


class AssignRequest(BaseModel):
    reference_peaks: list[ReferencePeak] = Field(min_length=2, max_length=40)
    measured_times_ms: list[StrictInt] = Field(min_length=2, max_length=60)
    is_ref_id_1: StrictStr
    is_meas_idx_1: StrictInt
    is_ref_id_2: StrictStr
    is_meas_idx_2: StrictInt
    tolerance_ms: StrictInt = Field(ge=0)


class FractionOut(BaseModel):
    n: int  # 约分后的分子（可负）
    d: int  # 约分后的分母（恒正）


class MatchOut(BaseModel):
    ref_id: str
    ref_index: int
    meas_index: int
    calibrated_time_ms: FractionOut
    signed_deviation_ms: FractionOut  # 校准值 - 参考值


class AssignResponse(BaseModel):
    # 与参考峰输入顺序一一对应：配对给出实测下标，否则 null
    assignments: list[int | None]
    unpaired_meas_indices: list[int]
    matches: list[MatchOut]


# ---------------------------------------------------------------------------
# 标准加入法（POST /v1/standard-addition）
# ---------------------------------------------------------------------------

class MeasuredPeak(BaseModel):
    time_ms: StrictInt
    area: StrictInt = Field(ge=0)


class Injection(BaseModel):
    # 非负整数加标浓度
    added_concentration: StrictInt = Field(ge=0)
    # 按采集顺序排列的实测峰
    measured_peaks: list[MeasuredPeak] = Field(min_length=2, max_length=60)
    # 两枚内标在 measured_peaks 中的下标
    is_meas_idx_1: StrictInt
    is_meas_idx_2: StrictInt


class StandardAdditionRequest(BaseModel):
    # 复用 /v1/assign 的参考峰表（id 唯一、时间严格递增）
    reference_peaks: list[ReferencePeak] = Field(min_length=2, max_length=40)
    target_ref_id: StrictStr
    is_ref_id_1: StrictStr
    is_ref_id_2: StrictStr
    tolerance_ms: StrictInt = Field(ge=0)
    injections: list[Injection] = Field(min_length=3, max_length=12)


class ResidualOut(BaseModel):
    # 按进样顺序；area_ratio = 目标峰面积 / 第一枚内标面积
    added_concentration: int
    area_ratio: FractionOut
    predicted_area_ratio: FractionOut
    # residual = area_ratio - predicted_area_ratio（观测减拟合）
    residual: FractionOut


class StandardAdditionResponse(BaseModel):
    # 含截距最小二乘：area_ratio = slope * added_concentration + intercept
    slope: FractionOut
    intercept: FractionOut
    # 原样浓度 = intercept / slope
    sample_concentration: FractionOut
    residuals: list[ResidualOut]


# ---------------------------------------------------------------------------
# 峰面积积分（POST /v1/integrate）
# ---------------------------------------------------------------------------

class IntegrationSample(BaseModel):
    time_ms: StrictInt
    # 原始色谱强度：非负整数
    intensity: StrictInt = Field(ge=0)


class IntegrateRequest(BaseModel):
    # 原始强度采样点，时间须严格递增（业务层校验）
    samples: list[IntegrationSample] = Field(min_length=5, max_length=500)
    # 积分起止下标（含），须为合法采样点下标且 start < end
    start_index: StrictInt = Field(ge=0)
    end_index: StrictInt = Field(ge=0)
    # 严格递增的峰顶下标；须严格位于起止之间，
    # 且相邻峰顶至少隔一个内部采样点（业务层校验）
    apex_indices: list[StrictInt] = Field(min_length=2, max_length=60)


class SegmentOut(BaseModel):
    # 段的起止采样点下标（含）；相邻段共用分界点
    start_index: int
    end_index: int
    area: FractionOut


class IntegrateResponse(BaseModel):
    # 与峰顶下标同序，每峰一段
    segments: list[SegmentOut]
    # 全部面积分母的最小公倍数：peaks 面积 = 段面积 × area_scale
    area_scale: int
    # 按峰顶时间排序的整数峰数组（零面积保留），可直接作为
    # /v1/standard-addition 的 measured_peaks；
    # 其 time_ms 序列可作为 /v1/assign 的 measured_times_ms
    peaks: list[MeasuredPeak]


# ---------------------------------------------------------------------------
# 空白扣除（POST /v1/blank-subtraction）
# ---------------------------------------------------------------------------

class BlankSubtractionRun(BaseModel):
    # 单次色谱运行：与 /v1/integrate 相同的积分输入
    samples: list[IntegrationSample] = Field(min_length=5, max_length=500)
    start_index: StrictInt = Field(ge=0)
    end_index: StrictInt = Field(ge=0)
    apex_indices: list[StrictInt] = Field(min_length=2, max_length=60)
    # 两枚内标在 apex_indices（即积分输出峰列表）中的下标
    is_apex_idx_1: StrictInt
    is_apex_idx_2: StrictInt


class BlankSubtractionRequest(BaseModel):
    # 两组共用的参考峰表与内标（同 /v1/assign）
    reference_peaks: list[ReferencePeak] = Field(min_length=2, max_length=40)
    is_ref_id_1: StrictStr
    is_ref_id_2: StrictStr
    tolerance_ms: StrictInt = Field(ge=0)
    # 样本与空白两次运行；两组的采样时间与面积放大倍数可以不同
    sample: BlankSubtractionRun
    blank: BlankSubtractionRun


class CorrectedPeakOut(BaseModel):
    # 与样本峰顶同序：归属的参考 id（未归属为 null）与约分校正面积
    ref_id: str | None
    corrected_area: FractionOut


class BlankSubtractionResponse(BaseModel):
    sample_peaks: list[CorrectedPeakOut]
    # 全部校正面积分母的最小公倍数：peaks 面积 = 校正面积 × area_scale
    area_scale: int
    # 按样本峰顶时间排序的整数峰数组（零面积保留），可直接作为
    # /v1/standard-addition 的 measured_peaks
    peaks: list[MeasuredPeak]


# ---------------------------------------------------------------------------
# 空白扣除可复核批次（/v1/blank-subtraction-batches）
# ---------------------------------------------------------------------------

class BatchSampleInput(BlankSubtractionRun):
    # 批次内唯一的样本字符串 id；其余字段同单次空白扣除的 sample 运行
    id: StrictStr = Field(min_length=1)


class BatchCreateRequest(BaseModel):
    # 扣除参数与 blank 同 /v1/blank-subtraction；samples 为 1～8 组带唯一 id 的样本
    reference_peaks: list[ReferencePeak] = Field(min_length=2, max_length=40)
    is_ref_id_1: StrictStr
    is_ref_id_2: StrictStr
    tolerance_ms: StrictInt = Field(ge=0)
    blank: BlankSubtractionRun
    samples: list[BatchSampleInput] = Field(min_length=1, max_length=8)

    @field_validator("samples")
    @classmethod
    def _sample_ids_unique(cls, v: list[BatchSampleInput]) -> list[BatchSampleInput]:
        ids = [s.id for s in v]
        if len(set(ids)) != len(ids):
            raise ValueError("sample ids must be unique within a batch")
        return v


class BatchConfirmRequest(BaseModel):
    sample_id: StrictStr = Field(min_length=1)
    # 乐观锁：客户端持有的版本，过期则 409
    expected_version: StrictInt = Field(ge=1)


class BatchBlankReplaceRequest(BaseModel):
    # 仅 blank 可替换；其余输入（扣除参数、样本）不可改
    blank: BlankSubtractionRun
    expected_version: StrictInt = Field(ge=1)


class BatchSampleOut(BaseModel):
    # 与创建请求中的样本同序
    id: str
    confirmed: bool
    # 该样本当前空白扣除结果（形状同 /v1/blank-subtraction 响应）
    result: BlankSubtractionResponse
    # 该样本的运行输入（id 在外层给出）
    input: BlankSubtractionRun


class BatchResponse(BaseModel):
    batch_id: str
    version: int
    reference_peaks: list[ReferencePeak]
    is_ref_id_1: str
    is_ref_id_2: str
    tolerance_ms: int
    blank: BlankSubtractionRun
    samples: list[BatchSampleOut]
