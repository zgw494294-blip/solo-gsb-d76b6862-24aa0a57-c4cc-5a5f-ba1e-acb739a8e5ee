"""空白扣除可复核批次的工作流服务（复用 ``blank_subtraction`` 纯函数）。

创建时用同一组扣除参数与 blank 依次计算 1～8 组样本；确认只翻转样本的
确认标志；替换 blank 时重算全部样本，已确认样本的结果按
（峰顺序下的 ref_id、time_ms、约分校正面积）比较，任一改变即拒绝整批替换。
计算全部在写事务外完成，因此计算失败（422）绝不会触碰已提交状态。
"""

from __future__ import annotations

import os
from typing import Any

from .blank_subtraction import blank_subtraction
from .storage import BatchStore, ConfirmedResultChanged

# 与单次接口相同的共用扣除参数
PARAM_KEYS = (
    "reference_peaks",
    "is_ref_id_1",
    "is_ref_id_2",
    "tolerance_ms",
)


# 重新导出：API 层可统一从 .batches 引用
__all__ = [
    "ConfirmedResultChanged",
    "create_batch",
    "get_batch",
    "confirm_sample",
    "replace_blank",
    "get_store",
    "reset_store",
]


def default_db_path() -> str:
    env = os.environ.get("BATCH_DB_PATH")
    if env:
        return env
    # 容器内 /data 挂载了持久卷；本地开发退回到工作目录下的本地文件。
    if os.path.isdir("/data") and os.access("/data", os.W_OK):
        return "/data/batches.db"
    return os.path.join(os.getcwd(), "batches.db")


_store: BatchStore | None = None


def get_store() -> BatchStore:
    global _store
    if _store is None:
        _store = BatchStore(default_db_path())
    return _store


def reset_store(path: str | None = None) -> BatchStore:
    """重置全局存储（测试隔离用）。"""
    global _store
    _store = BatchStore(path or default_db_path())
    return _store


def _params_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {key: data[key] for key in PARAM_KEYS}


def _compute_one(
    params: dict[str, Any],
    sample: dict[str, Any],
    blank: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    """对单组样本复用扣除逻辑；失败时消息指出样本 id 或 blank。"""
    return blank_subtraction(
        ref_peaks=params["reference_peaks"],
        is_ref_id_1=params["is_ref_id_1"],
        is_ref_id_2=params["is_ref_id_2"],
        tolerance_ms=params["tolerance_ms"],
        sample=sample,
        blank=blank,
        sample_label=label,
        blank_label="blank",
    )


def create_batch(data: dict[str, Any]) -> dict[str, Any]:
    """计算全部样本后再一次性原子写入；任何计算失败都不留下批次。"""
    params = _params_dict(data)
    blank = data["blank"]

    # 逐样本（按输入顺序）复用扣除逻辑：blank 在第一组样本的计算中一并校验，
    # 失败前缀为 "blank: "；任一样本失败前缀为 'sample "<id>": '，且尚未写库。
    results: dict[str, dict[str, Any]] = {}
    for sample in data["samples"]:
        results[sample["id"]] = _compute_one(
            params,
            sample["sample_input"],
            blank,
            f'sample "{sample["id"]}"',
        )

    return get_store().create(
        params=params,
        blank=blank,
        samples=[
            {"id": s["id"], "input": s["sample_input"], "result": results[s["id"]]}
            for s in data["samples"]
        ],
    )


def get_batch(batch_id: str) -> dict[str, Any]:
    return get_store().get(batch_id)


def confirm_sample(batch_id: str, sample_id: str, expected_version: int) -> dict[str, Any]:
    return get_store().confirm(batch_id, sample_id, expected_version)


def replace_blank(
    batch_id: str, request: dict[str, Any]
) -> dict[str, Any]:
    """重算全部样本；blank 本身或任何样本计算失败都返回 422 且状态不变。

    样本输入与扣除参数不可改且创建时已合法，故此处失败只会来自新 blank
    （前缀 ``blank: ``）。重算在写事务外完成，失败不触碰已提交状态。
    """
    store = get_store()
    batch = store.get(batch_id)  # 不存在抛 BatchNotFound -> 404
    expected_version = request["expected_version"]
    blank = request["blank"]
    params = _params_dict(batch)

    new_results = {
        s["id"]: _compute_one(
            params, s["input"], blank, f'sample "{s["id"]}"'
        )
        for s in batch["samples"]
    }

    return store.replace_blank(
        batch_id,
        expected_version,
        blank,
        new_results,
    )
