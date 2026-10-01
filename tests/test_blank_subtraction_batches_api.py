"""共用空白可复核批次的 HTTP 接口测试（TestClient 走真实 ASGI 栈）。"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import batches
from app.main import app
from app.storage import BatchStore


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: str):
    # 每个测试一个全新的临时 SQLite 文件；用同一文件重置存储可模拟“重启”。
    db = str(Path(tmp_path) / "batches.db")
    batches.reset_store(db)
    yield db
    batches.reset_store(db)  # 断开旧连接，避免 Windows/CI 文件占用


client = TestClient(app)

REFS_2 = [
    {"id": "A", "time_ms": 3},
    {"id": "B", "time_ms": 7},
]


def run(times, intensities, apex_indices=(1, 3), is1=0, is2=1, **over):
    body = {
        "samples": [
            {"time_ms": t, "intensity": y} for t, y in zip(times, intensities)
        ],
        "start_index": 0,
        "end_index": len(times) - 1,
        "apex_indices": list(apex_indices),
        "is_apex_idx_1": is1,
        "is_apex_idx_2": is2,
    }
    body.update(over)
    return body


SAMPLE = [0, 3, 4, 7, 10], [1, 4, 2, 5, 0]
BLANK = [0, 5, 6, 9, 14], [1, 2, 1, 3, 1]
# 空白更强的版本：A 峰面积变大，会改变已确认样本的扣除结果
BLANK_STRONG = [0, 5, 6, 9, 14], [1, 6, 1, 3, 1]
BLANK_INVALID = [0, 5, 6, 9, 14], [1, 2, 1, 3, 1]


def batch_payload(*, blank=BLANK, sample_ids=("s1", "s2")):
    return {
        "reference_peaks": REFS_2,
        "is_ref_id_1": "A",
        "is_ref_id_2": "B",
        "tolerance_ms": 0,
        "blank": run(*blank),
        "samples": [
            {"id": sid, **run(*SAMPLE)} for sid in sample_ids
        ],
    }


def create(payload=None):
    r = client.post(
        "/v1/blank-subtraction-batches", json=payload or batch_payload()
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# 创建与读取
# ---------------------------------------------------------------------------

def test_create_returns_batch_id_version_1_unconfirmed_results_in_order():
    d = create()
    assert isinstance(d["batch_id"], str) and len(d["batch_id"]) == 32
    assert d["version"] == 1
    assert [s["id"] for s in d["samples"]] == ["s1", "s2"]
    assert [s["confirmed"] for s in d["samples"]] == [False, False]
    # 结果复用单样本扣除逻辑
    assert d["samples"][0]["result"]["sample_peaks"] == [
        {"ref_id": "A", "corrected_area": {"n": 3, "d": 2}},
        {"ref_id": "B", "corrected_area": {"n": 4, "d": 1}},
    ]
    assert d["samples"][0]["result"]["area_scale"] == 2
    assert d["samples"][0]["result"]["peaks"] == [
        {"time_ms": 3, "area": 3},
        {"time_ms": 7, "area": 8},
    ]
    # 回读内容一致（只见到完整已提交状态）
    r = client.get(f"/v1/blank-subtraction-batches/{d['batch_id']}")
    assert r.status_code == 200, r.text
    assert r.json() == d


def test_create_one_to_eight_samples_and_unique_ids():
    for n in (1, 8):
        d = create(batch_payload(sample_ids=tuple(f"s{i}" for i in range(n))))
        assert len(d["samples"]) == n
    # 9 组超限
    r = client.post(
        "/v1/blank-subtraction-batches",
        json=batch_payload(sample_ids=tuple(f"s{i}" for i in range(9))),
    )
    assert r.status_code == 422
    # 0 组
    p = batch_payload()
    p["samples"] = []
    assert client.post("/v1/blank-subtraction-batches", json=p).status_code == 422
    # id 重复
    r = client.post(
        "/v1/blank-subtraction-batches", json=batch_payload(sample_ids=("x", "x"))
    )
    assert r.status_code == 422
    assert "unique" in r.text
    # 空 id
    p = batch_payload()
    p["samples"][0]["id"] = ""
    assert client.post("/v1/blank-subtraction-batches", json=p).status_code == 422


def test_create_failure_names_sample_id_and_persists_nothing(isolated_db):
    p = batch_payload()
    p["samples"][1]["samples"][2]["time_ms"] = 3  # s2 时间非严格递增
    r = client.post("/v1/blank-subtraction-batches", json=p)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail.startswith('sample "s2": ')
    assert "strictly increasing" in detail
    # 失败后库里没有任何批次行
    conn = sqlite3.connect(isolated_db)
    assert conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM batch_samples").fetchone()[0] == 0
    conn.close()


def test_create_blank_failure_named_blank_and_persists_nothing(isolated_db):
    p = batch_payload(blank=BLANK_INVALID)
    p["blank"]["apex_indices"] = [1, 2]  # 空白相邻峰顶无内部采样点
    r = client.post("/v1/blank-subtraction-batches", json=p)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail.startswith("blank: ")
    assert "internal sample" in detail
    conn = sqlite3.connect(isolated_db)
    assert conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0
    conn.close()


def test_read_unknown_batch_404():
    assert client.get("/v1/blank-subtraction-batches/nope").status_code == 404


# ---------------------------------------------------------------------------
# 确认
# ---------------------------------------------------------------------------

def test_confirm_sample_marks_only_that_sample_and_bumps_version():
    d = create()
    bid = d["batch_id"]
    r = client.post(
        f"/v1/blank-subtraction-batches/{bid}/confirm",
        json={"sample_id": "s1", "expected_version": 1},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["version"] == 2
    assert [s["confirmed"] for s in d["samples"]] == [True, False]
    assert d["samples"][0]["result"] == create()["samples"][0]["result"]

    # 再确认 s2：版本变 3，s1 保持已确认
    r = client.post(
        f"/v1/blank-subtraction-batches/{bid}/confirm",
        json={"sample_id": "s2", "expected_version": 2},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["version"] == 3
    assert [s["confirmed"] for s in d["samples"]] == [True, True]


def test_confirm_stale_version_409_leaves_state_unchanged():
    d = create()
    bid = d["batch_id"]
    r = client.post(
        f"/v1/blank-subtraction-batches/{bid}/confirm",
        json={"sample_id": "s1", "expected_version": 9},
    )
    assert r.status_code == 409
    d2 = client.get(f"/v1/blank-subtraction-batches/{bid}").json()
    assert d2["version"] == 1
    assert not any(s["confirmed"] for s in d2["samples"])


def test_confirm_unknown_sample_404():
    d = create()
    r = client.post(
        f"/v1/blank-subtraction-batches/{d['batch_id']}/confirm",
        json={"sample_id": "zz", "expected_version": 1},
    )
    assert r.status_code == 404
    assert client.post(
        "/v1/blank-subtraction-batches/missing/confirm",
        json={"sample_id": "s1", "expected_version": 1},
    ).status_code == 404


def test_cannot_overwrite_already_confirmed_sample():
    d = create()
    bid = d["batch_id"]
    assert client.post(
        f"/v1/blank-subtraction-batches/{bid}/confirm",
        json={"sample_id": "s1", "expected_version": 1},
    ).status_code == 200
    # 已确认样本不能覆盖确认：版本也已前进，旧版本先报 409（过期）
    r = client.post(
        f"/v1/blank-subtraction-batches/{bid}/confirm",
        json={"sample_id": "s1", "expected_version": 1},
    )
    assert r.status_code == 409
    # 用当前版本再次确认同一样本：仍 409（不能覆盖确认），状态不变
    r = client.post(
        f"/v1/blank-subtraction-batches/{bid}/confirm",
        json={"sample_id": "s1", "expected_version": 2},
    )
    assert r.status_code == 409
    d2 = client.get(f"/v1/blank-subtraction-batches/{bid}").json()
    assert d2["version"] == 2
    assert [s["confirmed"] for s in d2["samples"]] == [True, False]


# ---------------------------------------------------------------------------
# 替换 blank 重算
# ---------------------------------------------------------------------------

def test_replace_blank_recomputes_all_samples_and_bumps_version():
    d = create()
    bid = d["batch_id"]
    old = {s["id"]: s["result"] for s in d["samples"]}

    r = client.post(
        f"/v1/blank-subtraction-batches/{bid}/replace-blank",
        json={"blank": run(*BLANK_STRONG), "expected_version": 1},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["version"] == 2
    assert d["blank"]["samples"][1]["intensity"] == 6
    # 全部样本按新 blank 重算：A 峰 9/2-15 截为 0；输入/确认状态不变
    for s in d["samples"]:
        assert s["confirmed"] is False
        assert s["input"]["samples"] == run(*SAMPLE)["samples"]
        assert s["result"]["sample_peaks"][0] == {
            "ref_id": "A",
            "corrected_area": {"n": 0, "d": 1},
        }
        assert s["result"] != old[s["id"]]


def test_replace_blank_unchanged_confirmed_results_allowed():
    d = create()
    bid = d["batch_id"]
    client.post(
        f"/v1/blank-subtraction-batches/{bid}/confirm",
        json={"sample_id": "s1", "expected_version": 1},
    )
    # 以同一个 blank（结果不变）替换：允许，版本 +1，确认状态保留
    r = client.post(
        f"/v1/blank-subtraction-batches/{bid}/replace-blank",
        json={"blank": run(*BLANK), "expected_version": 2},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["version"] == 3
    assert [s["confirmed"] for s in d["samples"]] == [True, False]
    assert d["samples"][0]["result"]["sample_peaks"] == [
        {"ref_id": "A", "corrected_area": {"n": 3, "d": 2}},
        {"ref_id": "B", "corrected_area": {"n": 4, "d": 1}},
    ]


def test_replace_blank_changed_confirmed_result_409_names_sample_and_rolls_back():
    d = create()
    bid = d["batch_id"]
    client.post(
        f"/v1/blank-subtraction-batches/{bid}/confirm",
        json={"sample_id": "s1", "expected_version": 1},
    )
    # 只确认 s1；新 blank 会改变 s1 的结果 -> 整批拒绝
    r = client.post(
        f"/v1/blank-subtraction-batches/{bid}/replace-blank",
        json={"blank": run(*BLANK_STRONG), "expected_version": 2},
    )
    assert r.status_code == 409
    assert "s1" in r.json()["detail"]
    # 输入、结果、确认状态、版本全部不变
    d2 = client.get(f"/v1/blank-subtraction-batches/{bid}").json()
    assert d2["version"] == 2
    assert d2["blank"]["samples"][1]["intensity"] == 2  # 仍是旧 blank
    assert [s["confirmed"] for s in d2["samples"]] == [True, False]
    assert d2["samples"][0]["result"]["sample_peaks"] == [
        {"ref_id": "A", "corrected_area": {"n": 3, "d": 2}},
        {"ref_id": "B", "corrected_area": {"n": 4, "d": 1}},
    ]
    # 未确认的 s2 也没有被局部重算（不允许混用空白/部分替换）
    assert d2["samples"][1]["result"] == d["samples"][1]["result"]


def test_replace_blank_stale_version_409():
    d = create()
    r = client.post(
        f"/v1/blank-subtraction-batches/{d['batch_id']}/replace-blank",
        json={"blank": run(*BLANK_STRONG), "expected_version": 7},
    )
    assert r.status_code == 409
    assert client.get(
        f"/v1/blank-subtraction-batches/{d['batch_id']}"
    ).json()["version"] == 1


def test_replace_blank_invalid_blank_422_and_state_unchanged():
    d = create()
    bid = d["batch_id"]
    bad = run(*BLANK_INVALID, apex_indices=(1, 2))
    r = client.post(
        f"/v1/blank-subtraction-batches/{bid}/replace-blank",
        json={"blank": bad, "expected_version": 1},
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail.startswith("blank: ")
    assert "internal sample" in detail
    d2 = client.get(f"/v1/blank-subtraction-batches/{bid}").json()
    assert d2["version"] == 1
    assert d2["blank"]["apex_indices"] == [1, 3]


def test_replace_blank_computes_before_version_check():
    # blank 非法且版本同时过期：计算失败（422）优先于乐观锁（409），状态不变
    d = create()
    bad = run(*BLANK_INVALID, apex_indices=(1, 2))
    r = client.post(
        f"/v1/blank-subtraction-batches/{d['batch_id']}/replace-blank",
        json={"blank": bad, "expected_version": 99},
    )
    assert r.status_code == 422
    assert "blank: " in r.json()["detail"]
    assert client.get(
        f"/v1/blank-subtraction-batches/{d['batch_id']}"
    ).json()["version"] == 1


def test_replace_other_inputs_are_not_modifiable():
    d = create()
    bid = d["batch_id"]
    # 替换接口只接受 blank 与 expected_version：试图夹带其他字段会被忽略，
    # 参考峰/扣除参数/样本输入保持原值。
    p = {
        "blank": run(*BLANK),
        "expected_version": 1,
        "reference_peaks": [{"id": "X", "time_ms": 1}],
        "samples": [],
        "tolerance_ms": 99,
    }
    r = client.post(
        f"/v1/blank-subtraction-batches/{bid}/replace-blank", json=p
    )
    assert r.status_code == 200, r.text
    d2 = client.get(f"/v1/blank-subtraction-batches/{bid}").json()
    assert d2["reference_peaks"] == REFS_2
    assert d2["tolerance_ms"] == 0
    assert [s["id"] for s in d2["samples"]] == ["s1", "s2"]
    assert d2["version"] == 2


# ---------------------------------------------------------------------------
# 并发：同版本竞争只有一项成功
# ---------------------------------------------------------------------------

def test_concurrent_confirms_same_version_only_one_succeeds():
    d = create(batch_payload(sample_ids=("s1", "s2")))
    bid = d["batch_id"]
    statuses: dict[str, int] = {}
    barrier = threading.Barrier(2)

    def confirm(sid: str):
        barrier.wait()
        r = client.post(
            f"/v1/blank-subtraction-batches/{bid}/confirm",
            json={"sample_id": sid, "expected_version": 1},
        )
        statuses[sid] = r.status_code

    threads = [threading.Thread(target=confirm, args=(sid,)) for sid in ("s1", "s2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(statuses.values()) == [200, 409]
    d2 = client.get(f"/v1/blank-subtraction-batches/{bid}").json()
    assert d2["version"] == 2
    assert sum(s["confirmed"] for s in d2["samples"]) == 1


def test_concurrent_confirm_and_replace_same_version_only_one_succeeds():
    d = create()
    bid = d["batch_id"]
    results: dict[str, int] = {}
    barrier = threading.Barrier(2)

    def do_confirm():
        barrier.wait()
        r = client.post(
            f"/v1/blank-subtraction-batches/{bid}/confirm",
            json={"sample_id": "s1", "expected_version": 1},
        )
        results["confirm"] = r.status_code

    def do_replace():
        barrier.wait()
        r = client.post(
            f"/v1/blank-subtraction-batches/{bid}/replace-blank",
            json={"blank": run(*BLANK_STRONG), "expected_version": 1},
        )
        results["replace"] = r.status_code

    t1 = threading.Thread(target=do_confirm)
    t2 = threading.Thread(target=do_replace)
    t1.start(); t2.start(); t1.join(); t2.join()

    assert sorted(results.values()) == [200, 409]
    d2 = client.get(f"/v1/blank-subtraction-batches/{bid}").json()
    assert d2["version"] == 2
    # 要么确认生效（blank 未换），要么换 blank 生效（未确认），二者不可混用
    confirmed = any(s["confirmed"] for s in d2["samples"])
    blank_swapped = d2["blank"]["samples"][1]["intensity"] == 6
    assert confirmed != blank_swapped
    if blank_swapped:
        # 换 blank 成功则所有样本均按新 blank 重算，无确认状态
        assert not any(s["confirmed"] for s in d2["samples"])
        assert all(
            s["result"]["sample_peaks"][0]["corrected_area"] == {"n": 0, "d": 1}
            for s in d2["samples"]
        )
    else:
        # 确认成功则 blank 与全部结果保持旧值
        assert d2["samples"][0]["result"]["sample_peaks"] == [
            {"ref_id": "A", "corrected_area": {"n": 3, "d": 2}},
            {"ref_id": "B", "corrected_area": {"n": 4, "d": 1}},
        ]


# ---------------------------------------------------------------------------
# 持久化：重启后仍可读
# ---------------------------------------------------------------------------

def test_batch_survives_store_restart(isolated_db):
    d = create()
    bid = d["batch_id"]
    client.post(
        f"/v1/blank-subtraction-batches/{bid}/confirm",
        json={"sample_id": "s1", "expected_version": 1},
    )
    client.post(
        f"/v1/blank-subtraction-batches/{bid}/replace-blank",
        json={"blank": run(*BLANK), "expected_version": 2},
    )
    before = client.get(f"/v1/blank-subtraction-batches/{bid}").json()

    # 用同一数据库文件重新打开存储（等价于容器重启后重新挂载卷）
    batches.reset_store(isolated_db)
    after = client.get(f"/v1/blank-subtraction-batches/{bid}").json()
    assert after == before
    assert after["version"] == 3
    assert [s["confirmed"] for s in after["samples"]] == [True, False]

    # 重新打开后仍可继续乐观并发操作
    r = client.post(
        f"/v1/blank-subtraction-batches/{bid}/confirm",
        json={"sample_id": "s2", "expected_version": 3},
    )
    assert r.status_code == 200
    assert r.json()["version"] == 4


def test_db_file_is_sqlite_with_named_table(isolated_db):
    create()
    conn = sqlite3.connect(isolated_db)
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"batches", "batch_samples"} <= names
    conn.close()
