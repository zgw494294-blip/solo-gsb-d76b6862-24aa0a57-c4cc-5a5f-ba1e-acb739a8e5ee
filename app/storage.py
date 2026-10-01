"""可复核批次的 SQLite 持久化层。

批次表存共用扣除参数与当前 blank，样本表存每组样本的输入、当前结果与
确认状态，结果按创建输入顺序排序。所有修改以单一事务提交，事务内以
``version == expected_version`` 做乐观锁校验：过期返回 ``StaleVersion``；
确认已确认样本返回 ``AlreadyConfirmed``。版本仅在提交成功后加 1，
任何失败（含计算失败）都不会留下中间状态。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id      TEXT PRIMARY KEY,
    version       INTEGER NOT NULL,
    params        TEXT NOT NULL,
    blank         TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS batch_samples (
    batch_id   TEXT NOT NULL REFERENCES batches(batch_id),
    ord        INTEGER NOT NULL,
    sample_id  TEXT NOT NULL,
    input      TEXT NOT NULL,
    result     TEXT NOT NULL,
    confirmed  INTEGER NOT NULL,
    PRIMARY KEY (batch_id, ord),
    UNIQUE (batch_id, sample_id)
);
"""


class BatchNotFound(Exception):
    """批次不存在（或样本不属于该批次），API 层映射为 404。"""


class SampleNotFound(BatchNotFound):
    """批次存在但样本 id 不存在，同样映射为 404。"""


class StaleVersion(Exception):
    """expected_version 与当前版本不一致，API 层映射为 409。"""


class AlreadyConfirmed(Exception):
    """样本已确认，不能覆盖确认状态，API 层映射为 409。"""


class ConfirmedResultChanged(Exception):
    """替换 blank 会改变已确认样本的结果，API 层映射为 409。"""


def canonical_result(result: dict[str, Any]) -> list[tuple]:
    """已确认结果的比较键：按峰顺序取 (ref_id, time_ms, 约分校正面积)。

    ``sample_peaks`` 与 ``peaks`` 均按样本峰顶顺序排列、逐一对齐：
    ref_id 与约分校正面积在前者，time_ms 在后者。
    """
    return [
        (
            sp["ref_id"],
            p["time_ms"],
            sp["corrected_area"]["n"],
            sp["corrected_area"]["d"],
        )
        for sp, p in zip(result["sample_peaks"], result["peaks"])
    ]


class BatchStore:
    """SQLite 批次存储；进程内一把锁串行化写事务（配合 BEGIN IMMEDIATE）。"""

    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """取进程锁 + BEGIN IMMEDIATE：写事务全局串行，杜绝跨事务覆盖。"""
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    def get(self, batch_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT version, params, blank FROM batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if row is None:
                raise BatchNotFound(f"batch {batch_id!r} not found")
            version, params_raw, blank_raw = row
            samples = conn.execute(
                "SELECT sample_id, input, result, confirmed FROM batch_samples "
                "WHERE batch_id = ? ORDER BY ord",
                (batch_id,),
            ).fetchall()
        return {
            "batch_id": batch_id,
            "version": version,
            **json.loads(params_raw),
            "blank": json.loads(blank_raw),
            "samples": [
                {
                    "id": sample_id,
                    "input": json.loads(input_raw),
                    "result": json.loads(result_raw),
                    "confirmed": bool(confirmed),
                }
                for sample_id, input_raw, result_raw, confirmed in samples
            ],
        }

    # ------------------------------------------------------------------
    # 写（均为单一原子事务；失败回滚不留痕）
    # ------------------------------------------------------------------

    def create(
        self,
        params: dict[str, Any],
        blank: dict[str, Any],
        samples: list[dict[str, Any]],
    ) -> dict[str, Any]:
        batch_id = uuid.uuid4().hex
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO batches (batch_id, version, params, blank) "
                "VALUES (?, 1, ?, ?)",
                (batch_id, json.dumps(params), json.dumps(blank)),
            )
            conn.executemany(
                "INSERT INTO batch_samples "
                "(batch_id, ord, sample_id, input, result, confirmed) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                [
                    (
                        batch_id,
                        ord,
                        s["id"],
                        json.dumps(s["input"]),
                        json.dumps(s["result"]),
                    )
                    for ord, s in enumerate(samples)
                ],
            )
        return self.get(batch_id)

    def confirm(
        self, batch_id: str, sample_id: str, expected_version: int
    ) -> dict[str, Any]:
        with self._transaction() as conn:
            self._check_version(conn, batch_id, expected_version)
            row = conn.execute(
                "SELECT confirmed FROM batch_samples "
                "WHERE batch_id = ? AND sample_id = ?",
                (batch_id, sample_id),
            ).fetchone()
            if row is None:
                raise SampleNotFound(
                    f"sample {sample_id!r} not found in batch {batch_id!r}"
                )
            if row[0]:
                raise AlreadyConfirmed(
                    f"sample {sample_id!r} is already confirmed"
                )
            conn.execute(
                "UPDATE batch_samples SET confirmed = 1 "
                "WHERE batch_id = ? AND sample_id = ?",
                (batch_id, sample_id),
            )
            conn.execute(
                "UPDATE batches SET version = version + 1 WHERE batch_id = ?",
                (batch_id,),
            )
        return self.get(batch_id)

    def replace_blank(
        self,
        batch_id: str,
        expected_version: int,
        blank: dict[str, Any],
        new_results: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """整批替换 blank 并重算结果。

        仅当**所有已确认样本**的结果（峰顺序下的 ref_id / time_ms /
        约分校正面积）均未改变时才提交；任一改变抛 ``ConfirmedResultChanged``，
        由事务上下文回滚——blank、结果、确认状态与版本全部不变。
        新结果在调用前已在事务外计算完成。
        """
        with self._transaction() as conn:
            self._check_version(conn, batch_id, expected_version)
            rows = conn.execute(
                "SELECT sample_id, result, confirmed FROM batch_samples "
                "WHERE batch_id = ? ORDER BY ord",
                (batch_id,),
            ).fetchall()
            if not rows:
                raise BatchNotFound(f"batch {batch_id!r} not found")
            for sample_id, result_raw, confirmed in rows:
                if confirmed:
                    old = json.loads(result_raw)
                    new = new_results[sample_id]
                    if canonical_result(old) != canonical_result(new):
                        raise ConfirmedResultChanged(
                            f"replacing the blank would change the confirmed "
                            f"result of sample {sample_id!r}"
                        )
            conn.execute(
                "UPDATE batches SET blank = ?, version = version + 1 "
                "WHERE batch_id = ?",
                (json.dumps(blank), batch_id),
            )
            conn.executemany(
                "UPDATE batch_samples SET result = ? "
                "WHERE batch_id = ? AND sample_id = ?",
                [
                    (json.dumps(new_results[sample_id]), batch_id, sample_id)
                    for sample_id, _, _ in rows
                ],
            )
        return self.get(batch_id)

    @staticmethod
    def _check_version(
        conn: sqlite3.Connection, batch_id: str, expected_version: int
    ) -> None:
        row = conn.execute(
            "SELECT version FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        if row is None:
            raise BatchNotFound(f"batch {batch_id!r} not found")
        if row[0] != expected_version:
            raise StaleVersion(
                f"expected version {expected_version}, current version is {row[0]}"
            )
