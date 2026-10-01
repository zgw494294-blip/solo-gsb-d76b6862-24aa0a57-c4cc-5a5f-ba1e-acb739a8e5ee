"""空白扣除可复核批次的 SQLite 持久化与乐观并发控制。

数据库文件路径由环境变量 ``BATCH_DB_PATH`` 指定（默认 /data/batches.db），
以 Docker Compose 命名卷挂载，容器重启后批次仍可读取。

并发模型：所有写事务以 ``BEGIN IMMEDIATE`` 立即获取 SQLite 写锁，
同版本的确认与替换只有一个事务能成功提交（另一个提交时版本已前进，
按 409 失败）；读事务只读已提交快照，绝不会见到半成品批次。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from typing import Any

from .assignment import AssignmentError
from .blank_subtraction import prepare_blank, subtract_sample

# —— 业务异常：main.py 分别映射为 404 / 409 / 422 ——


class BatchNotFound(LookupError):
    """批次不存在，映射为 HTTP 404。"""


class BatchConflict(RuntimeError):
    """版本过期或已确认结果不允许覆盖，映射为 HTTP 409。"""


class BatchInputError(ValueError):
    """批次输入不满足业务规则（样本 id 重复等），映射为 HTTP 422。"""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    id          TEXT PRIMARY KEY,
    version     INTEGER NOT NULL,
    params      TEXT NOT NULL,   -- 不可变的共用扣除参数（JSON）
    blank       TEXT NOT NULL    -- 当前 blank 运行输入（JSON）
);
CREATE TABLE IF NOT EXISTS batch_samples (
    batch_id    TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    ord         INTEGER NOT NULL,   -- 提交顺序（从 0 开始），不可变
    sample_id   TEXT NOT NULL,      -- 批次内唯一，不可变
    input       TEXT NOT NULL,      -- 样本运行输入（JSON），不可变
    result      TEXT NOT NULL,      -- 当前扣除结果（JSON）
    confirmed   INTEGER NOT NULL,   -- 0/1
    PRIMARY KEY (batch_id, ord),
    UNIQUE (batch_id, sample_id)
);
"""

_lock = threading.Lock()
_initialized = False


def get_db_path() -> str:
    return os.environ.get("BATCH_DB_PATH", "/data/batches.db")


def reset_store() -> None:
    """丢弃初始化状态（测试切换数据库路径时使用）。"""
    global _initialized
    with _lock:
        _initialized = False


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(
        get_db_path(), timeout=30, isolation_level=None
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db() -> None:
    """惰性建库建表（首次访问时一次），并开启 WAL 与持久化同步。"""
    global _initialized
    if _initialized:
        return
    with _lock:
        if _initialized:
            return
        db_path = get_db_path()
        parent = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(parent, exist_ok=True)
        conn = _connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = FULL")
            conn.executescript(_SCHEMA)
        finally:
            conn.close()
        _initialized = True


def _params_dict(req: dict[str, Any]) -> dict[str, Any]:
    """抽出创建后不可变的共用扣除参数。"""
    return {
        key: req[key]
        for key in (
            "reference_peaks",
            "is_ref_id_1",
            "is_ref_id_2",
            "tolerance_ms",
        )
    }


def _fingerprint(result: dict[str, Any]) -> list[tuple[Any, ...]]:
    """已确认结果的比较口径：按峰顺序的 time_ms、参考 id、约分校正面积。

    样本积分峰数组与 sample_peaks 严格同序；area_scale 与整数 peaks 只是
    校正面积的统一放大表示，不参与比较（校正面积 n/d 已是唯一约分表示）。
    """
    return [
        (
            peak["time_ms"],
            sp["ref_id"],
            sp["corrected_area"]["n"],
            sp["corrected_area"]["d"],
        )
        for peak, sp in zip(result["peaks"], result["sample_peaks"])
    ]


def create_batch(req: dict[str, Any]) -> dict[str, Any]:
    """创建批次：先离线算完全部结果，再在单个写事务中整体提交。

    任何计算失败（422）都发生在写事务之前，数据库中不会留下批次。
    """
    init_db()
    samples = req["samples"]
    sample_ids = [s["id"] for s in samples]
    if len(set(sample_ids)) != len(sample_ids):
        raise BatchInputError("sample ids must be unique within a batch")

    params = _params_dict(req)
    # 批次语境先处理 blank：共用参数/blank 非法时报 "blank: ..."，
    # 样本自身非法时报 "sample <id>: ..."。
    blank_ctx = prepare_blank(
        ref_peaks=params["reference_peaks"],
        is_ref_id_1=params["is_ref_id_1"],
        is_ref_id_2=params["is_ref_id_2"],
        tolerance_ms=params["tolerance_ms"],
        blank=req["blank"],
    )

    results: list[dict[str, Any]] = []
    for sample in samples:
        try:
            results.append(
                subtract_sample(
                    ref_peaks=params["reference_peaks"],
                    is_ref_id_1=params["is_ref_id_1"],
                    is_ref_id_2=params["is_ref_id_2"],
                    tolerance_ms=params["tolerance_ms"],
                    sample=sample,
                    blank_ctx=blank_ctx,
                )
            )
        except AssignmentError as exc:
            # subtract_sample 的失败前缀恒为 "sample: "，改为指出样本 id。
            msg = str(exc)
            if msg.startswith("sample: "):
                msg = msg[len("sample: "):]
            raise AssignmentError(f"sample {sample['id']}: {msg}") from exc

    batch_id = uuid.uuid4().hex
    conn = _connect()
    committed = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO batches (id, version, params, blank) "
            "VALUES (?, 1, ?, ?)",
            (batch_id, json.dumps(params), json.dumps(req["blank"])),
        )
        for ord_, (sample, result) in enumerate(zip(samples, results)):
            conn.execute(
                "INSERT INTO batch_samples "
                "(batch_id, ord, sample_id, input, result, confirmed) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (
                    batch_id,
                    ord_,
                    sample["id"],
                    json.dumps(sample),
                    json.dumps(result),
                ),
            )
        conn.execute("COMMIT")
        committed = True
    except Exception:
        if not committed:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        raise
    finally:
        conn.close()
    return get_batch(batch_id)


def get_batch(batch_id: str) -> dict[str, Any]:
    """读取完整的已提交批次状态；不存在报 404。"""
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, version FROM batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if row is None:
            raise BatchNotFound(f"batch {batch_id} not found")
        sample_rows = conn.execute(
            "SELECT sample_id, result, confirmed FROM batch_samples "
            "WHERE batch_id = ? ORDER BY ord",
            (batch_id,),
        ).fetchall()
        return {
            "id": row["id"],
            "version": row["version"],
            "samples": [
                {
                    "id": r["sample_id"],
                    "confirmed": bool(r["confirmed"]),
                    "result": json.loads(r["result"]),
                }
                for r in sample_rows
            ],
        }
    finally:
        conn.close()


def confirm_sample(
    batch_id: str, sample_id: str, expected_version: int
) -> dict[str, Any]:
    """确认单个样本（乐观锁）。已是确认状态不允许覆盖确认。"""
    init_db()
    conn = _connect()
    committed = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT version FROM batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if row is None:
            raise BatchNotFound(f"batch {batch_id} not found")
        if row["version"] != expected_version:
            raise BatchConflict(
                f"expected version {expected_version}, current {row['version']}"
            )
        srow = conn.execute(
            "SELECT confirmed FROM batch_samples "
            "WHERE batch_id = ? AND sample_id = ?",
            (batch_id, sample_id),
        ).fetchone()
        if srow is None:
            raise BatchNotFound(
                f"sample {sample_id} not found in batch {batch_id}"
            )
        if srow["confirmed"]:
            raise BatchConflict(f"sample {sample_id} is already confirmed")
        conn.execute(
            "UPDATE batch_samples SET confirmed = 1 "
            "WHERE batch_id = ? AND sample_id = ?",
            (batch_id, sample_id),
        )
        conn.execute(
            "UPDATE batches SET version = version + 1 WHERE id = ?",
            (batch_id,),
        )
        conn.execute("COMMIT")
        committed = True
        return get_batch(batch_id)
    except Exception:
        if not committed:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        raise
    finally:
        conn.close()


def replace_blank(
    batch_id: str, expected_version: int, new_blank: dict[str, Any]
) -> dict[str, Any]:
    """替换 blank 并在同一事务中重算全部样本。

    先在事务内完成版本检查与全部重算：任一已确认样本的比较口径改变则
    整批回滚报 409；计算失败回滚报 422；成功才整体提交并版本加 1。
    """
    init_db()
    conn = _connect()
    committed = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT version, params FROM batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if row is None:
            raise BatchNotFound(f"batch {batch_id} not found")
        if row["version"] != expected_version:
            raise BatchConflict(
                f"expected version {expected_version}, "
                f"current {row['version']}"
            )

        params = json.loads(row["params"])
        try:
            blank_ctx = prepare_blank(
                ref_peaks=params["reference_peaks"],
                is_ref_id_1=params["is_ref_id_1"],
                is_ref_id_2=params["is_ref_id_2"],
                tolerance_ms=params["tolerance_ms"],
                blank=new_blank,
            )
        except AssignmentError:
            raise  # 消息已带 "blank: " 前缀

        sample_rows = conn.execute(
            "SELECT ord, sample_id, input, result, confirmed "
            "FROM batch_samples WHERE batch_id = ? ORDER BY ord",
            (batch_id,),
        ).fetchall()

        new_results: list[dict[str, Any]] = []
        changed_confirmed: list[str] = []
        for srow in sample_rows:
            try:
                result = subtract_sample(
                    ref_peaks=params["reference_peaks"],
                    is_ref_id_1=params["is_ref_id_1"],
                    is_ref_id_2=params["is_ref_id_2"],
                    tolerance_ms=params["tolerance_ms"],
                    sample=json.loads(srow["input"]),
                    blank_ctx=blank_ctx,
                )
            except AssignmentError as exc:
                # 此处 subtract_sample 只会因不可变的样本输入而失败，
                # 理论上创建时已验证；仍按样本 id 指出并整体回滚。
                msg = str(exc)
                if msg.startswith("sample: "):
                    msg = msg[len("sample: "):]
                raise AssignmentError(
                    f"sample {srow['sample_id']}: {msg}"
                ) from exc
            if srow["confirmed"] and _fingerprint(
                json.loads(srow["result"])
            ) != _fingerprint(result):
                changed_confirmed.append(srow["sample_id"])
            new_results.append(result)

        if changed_confirmed:
            raise BatchConflict(
                "replacement would change confirmed results for samples: "
                + ", ".join(changed_confirmed)
            )

        # 结果未变也允许替换：blank 输入更新、全部结果重写、版本加 1。
        conn.execute(
            "UPDATE batches SET blank = ?, version = version + 1 WHERE id = ?",
            (json.dumps(new_blank), batch_id),
        )
        for srow, result in zip(sample_rows, new_results):
            conn.execute(
                "UPDATE batch_samples SET result = ? WHERE batch_id = ? "
                "AND ord = ?",
                (json.dumps(result), batch_id, srow["ord"]),
            )
        conn.execute("COMMIT")
        committed = True
        return get_batch(batch_id)
    except Exception:
        # 任何失败（含 404/409/422）都整体回滚，杜绝半成品事务；
        # 已提交则不再回滚（避免掩盖提交之后读取阶段的异常）。
        if not committed:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        raise
    finally:
        conn.close()
