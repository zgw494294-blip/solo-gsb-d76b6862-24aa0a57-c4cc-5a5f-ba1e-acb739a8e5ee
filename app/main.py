"""FastAPI 入口：POST /v1/assign 离线色谱峰归属、
POST /v1/standard-addition 标准加入法定量、
POST /v1/integrate 原始强度采样峰面积积分、
POST /v1/blank-subtraction 样本/空白两次运行的空白扣除。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .assignment import AssignmentError, assign_peaks
from .blank_subtraction import blank_subtraction
from .integration import integrate_peaks
from .models import (
    AssignRequest,
    AssignResponse,
    BlankSubtractionRequest,
    BlankSubtractionResponse,
    IntegrateRequest,
    IntegrateResponse,
    StandardAdditionRequest,
    StandardAdditionResponse,
)
from .standard_addition import standard_addition

app = FastAPI(
    title="离线色谱峰归属 API",
    version="1.0.0",
    description="基于两对互异内标的正斜率仿射校准与保序一对一峰归属；"
    "另提供标准加入法定量、原始强度采样峰面积积分与空白扣除。",
)


@app.exception_handler(AssignmentError)
async def assignment_error_handler(
    request: Request, exc: AssignmentError
) -> JSONResponse:
    # 业务规则不满足与 Pydantic 校验失败一致，统一返回 422。
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/assign", response_model=AssignResponse)
async def assign(request: AssignRequest) -> dict:
    return assign_peaks(
        ref_peaks=[p.model_dump() for p in request.reference_peaks],
        meas_times=list(request.measured_times_ms),
        is_ref_id_1=request.is_ref_id_1,
        is_meas_idx_1=request.is_meas_idx_1,
        is_ref_id_2=request.is_ref_id_2,
        is_meas_idx_2=request.is_meas_idx_2,
        tolerance_ms=request.tolerance_ms,
    )


@app.post(
    "/v1/standard-addition",
    response_model=StandardAdditionResponse,
)
async def standard_addition_endpoint(request: StandardAdditionRequest) -> dict:
    return standard_addition(
        ref_peaks=[p.model_dump() for p in request.reference_peaks],
        target_ref_id=request.target_ref_id,
        is_ref_id_1=request.is_ref_id_1,
        is_ref_id_2=request.is_ref_id_2,
        tolerance_ms=request.tolerance_ms,
        injections=[inj.model_dump() for inj in request.injections],
    )


@app.post("/v1/integrate", response_model=IntegrateResponse)
async def integrate(request: IntegrateRequest) -> dict:
    return integrate_peaks(
        samples=[s.model_dump() for s in request.samples],
        start_index=request.start_index,
        end_index=request.end_index,
        apex_indices=list(request.apex_indices),
    )


@app.post(
    "/v1/blank-subtraction",
    response_model=BlankSubtractionResponse,
)
async def blank_subtraction_endpoint(request: BlankSubtractionRequest) -> dict:
    return blank_subtraction(
        ref_peaks=[p.model_dump() for p in request.reference_peaks],
        is_ref_id_1=request.is_ref_id_1,
        is_ref_id_2=request.is_ref_id_2,
        tolerance_ms=request.tolerance_ms,
        sample=request.sample.model_dump(),
        blank=request.blank.model_dump(),
    )
