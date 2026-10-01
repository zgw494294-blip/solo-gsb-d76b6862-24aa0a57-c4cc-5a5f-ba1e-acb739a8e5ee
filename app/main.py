"""FastAPI 入口：POST /v1/assign 离线色谱峰归属、
POST /v1/standard-addition 标准加入法定量、
POST /v1/integrate 原始强度采样峰面积积分、
POST /v1/blank-subtraction 样本/空白两次运行的空白扣除，
以及 /v1/blank-subtraction-batches 共用空白的可复核批次。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .assignment import AssignmentError, assign_peaks
from .batch_store import (
    BatchConflict,
    BatchInputError,
    BatchNotFound,
    confirm_sample,
    create_batch,
    get_batch,
    replace_blank,
)
from .blank_subtraction import blank_subtraction
from .integration import integrate_peaks
from .models import (
    AssignRequest,
    AssignResponse,
    BatchConfirmRequest,
    BatchCreateRequest,
    BatchReplaceBlankRequest,
    BatchResponse,
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
    version="1.1.0",
    description="基于两对互异内标的正斜率仿射校准与保序一对一峰归属；"
    "另提供标准加入法定量、原始强度采样峰面积积分、空白扣除与"
    "共用空白的可复核批次（SQLite 持久化、乐观并发）。",
)


@app.exception_handler(AssignmentError)
async def assignment_error_handler(
    request: Request, exc: AssignmentError
) -> JSONResponse:
    # 业务规则不满足与 Pydantic 校验失败一致，统一返回 422。
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(BatchInputError)
async def batch_input_error_handler(
    request: Request, exc: BatchInputError
) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(BatchConflict)
async def batch_conflict_error_handler(
    request: Request, exc: BatchConflict
) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(BatchNotFound)
async def batch_not_found_error_handler(
    request: Request, exc: BatchNotFound
) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


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


# ---------------------------------------------------------------------------
# 共用空白的可复核批次
# ---------------------------------------------------------------------------

def _batch_request_dump(request: BatchCreateRequest) -> dict:
    return {
        "reference_peaks": [
            p.model_dump() for p in request.reference_peaks
        ],
        "is_ref_id_1": request.is_ref_id_1,
        "is_ref_id_2": request.is_ref_id_2,
        "tolerance_ms": request.tolerance_ms,
        "blank": request.blank.model_dump(),
        "samples": [s.model_dump() for s in request.samples],
    }


@app.post(
    "/v1/blank-subtraction-batches",
    response_model=BatchResponse,
    status_code=201,
)
async def create_batch_endpoint(request: BatchCreateRequest) -> dict:
    return create_batch(_batch_request_dump(request))


@app.get(
    "/v1/blank-subtraction-batches/{batch_id}",
    response_model=BatchResponse,
)
async def get_batch_endpoint(batch_id: str) -> dict:
    return get_batch(batch_id)


@app.post(
    "/v1/blank-subtraction-batches/{batch_id}/samples/{sample_id}/confirm",
    response_model=BatchResponse,
)
async def confirm_sample_endpoint(
    batch_id: str, sample_id: str, request: BatchConfirmRequest
) -> dict:
    return confirm_sample(
        batch_id=batch_id,
        sample_id=sample_id,
        expected_version=request.expected_version,
    )


@app.put(
    "/v1/blank-subtraction-batches/{batch_id}/blank",
    response_model=BatchResponse,
)
async def replace_blank_endpoint(
    batch_id: str, request: BatchReplaceBlankRequest
) -> dict:
    return replace_blank(
        batch_id=batch_id,
        expected_version=request.expected_version,
        new_blank=request.blank.model_dump(),
    )
