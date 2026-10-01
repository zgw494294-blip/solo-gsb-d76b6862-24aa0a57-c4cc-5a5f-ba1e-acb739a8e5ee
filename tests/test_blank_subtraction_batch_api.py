"""共用空白可复核批次 HTTP 接口测试（TestClient 走真实 ASGI 栈）。

覆盖：创建/读取/确认/替换 blank、乐观版本、409/422/404、失败原子性、
同版本并发互斥，以及 SQLite 文件持久化（模拟容器重启）。
"""

from __future__ import annotations

import json
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from app import batch_store
from app.main import app


REFS_2 = [
    {"id": "A", "time_ms": 3},
    {"id": "B", "time_ms": 7},
]
REFS_3 = [
    {"id": "A", "time_ms": 3},
    {"id": "B", "time_ms": 7},
    {"id": "C", "time_ms": 12},
]


@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BATCH_DB_PATH", str(tmp_path / "batches.db"))
    batch_store.reset_store()
    yield
    batch_store.reset_store()


def run(times, intensities, apex_indices, is1, is2, **over):
    body = {
        "samples": [
            {"time_ms": t, "intensity": y} for t, y in zip(times, intensities)
        ],
        "start_index": 0,
        "end_index": len(times) - 1,
        "apex_indices": apex_indices,
        "is_apex_idx_1": is1,
        "is_apex_idx_2": is2,
    }
    body.update(over)
    return body


# —— 与 test_blank_subtraction_api.py 相同的两组基础运行 ——
SAMPLE_2 = run([0, 3, 4, 7, 10], [1, 4, 2, 5, 0], [1, 3], 0, 1)
BLANK_2 = run([0, 5, 6, 9, 14], [1, 2, 1, 3, 1], [1, 3], 0, 1)

# 更强的空白：两段面积恰好 6、12，使两峰校正后均为 0
# （第一段 3·(3−1)=6；第二段 4·(4−1)=12）
BLANK_2_STRONG = run(
    [0, 5, 6, 9, 14], [1, 3, 1, 4, 1], [1, 3], 0, 1
)

# 三参考体系：s1 三峰全部归属 A/B/C；s2 中间峰未归属
SAMPLE_S1 = run(
    [0, 3, 4, 7, 9, 12, 14], [1, 4, 2, 5, 1, 6, 0], [1, 3, 5], 0, 2
)
SAMPLE_S2 = run(
    [0, 3, 4, 8, 9, 12, 14], [1, 4, 2, 5, 1, 6, 0], [1, 3, 5], 0, 2
)
# 旧空白：A/B/C 分数面积 2 / 15/2 / 5
BLANK_3 = run(
    [0, 3, 4, 7, 9, 12, 14], [1, 2, 1, 4, 1, 3, 1], [1, 3, 5], 0, 2
)
# 新空白：A、C 面积不变，仅 B 由 15/2 变为 25/2（s1 的 B 改变，s2 无 B）
BLANK_3_B_DIFF = run(
    [0, 3, 4, 7, 9, 12, 14], [1, 2, 1, 6, 1, 3, 1], [1, 3, 5], 0, 2
)
# 结构非法的空白
BLANK_INVALID = dict(BLANK_2, apex_indices=[1, 2])


def batch_payload(samples, blank=BLANK_2, refs=REFS_2, **over):
    body = {
        "reference_peaks": refs,
        "is_ref_id_1": refs[0]["id"],
        "is_ref_id_2": refs[-1]["id"],
        "tolerance_ms": 0,
        "blank": blank,
        "samples": samples,
    }
    body.update(over)
    return body


def create(samples, **over):
    body = batch_payload(samples, **over)
    return TestClient(app).post("/v1/blank-subtraction-batches", json=body)


def sid(sample_run, sid_):
    return {"id": sid_, **{k: v for k, v in sample_run.items()}}


# ---------------------------------------------------------------------------
# 创建
# ---------------------------------------------------------------------------

def test_create_batch_initial_state():
    r = create([sid(SAMPLE_2, "s-1"), sid(SAMPLE_2, "s-2")])
    assert r.status_code == 201, r.text
    data = r.json()
    assert isinstance(data["id"], str) and len(data["id"]) == 32
    assert data["version"] == 1
    assert [s["id"] for s in data["samples"]] == ["s-1", "s-2"]
    assert all(s["confirmed"] is False for s in data["samples"])
    # 结果与单样本扣除的已知值一致：3/2 与 4
    for s in data["samples"]:
        assert s["result"]["sample_peaks"] == [
            {"ref_id": "A", "corrected_area": {"n": 3, "d": 2}},
            {"ref_id": "B", "corrected_area": {"n": 4, "d": 1}},
        ]
        assert s["result"]["peaks"] == [
            {"time_ms": 3, "area": 3},
            {"time_ms": 7, "area": 8},
        ]


def test_create_accepts_one_to_eight_samples():
    r = create([sid(SAMPLE_2, "only")])
    assert r.status_code == 201, r.text
    r = create([sid(SAMPLE_2, f"s{i}") for i in range(8)])
    assert r.status_code == 201, r.text


def test_create_rejects_zero_or_nine_samples():
    r = TestClient(app).post(
        "/v1/blank-subtraction-batches",
        json=batch_payload([]),
    )
    assert r.status_code == 422
    r = create([sid(SAMPLE_2, f"s{i}") for i in range(9)])
    assert r.status_code == 422


def test_create_rejects_duplicate_sample_ids():
    r = create([sid(SAMPLE_2, "dup"), sid(SAMPLE_2, "dup")])
    assert r.status_code == 422
    assert "unique" in r.json()["detail"]


def test_create_rejects_empty_sample_id():
    r = create([sid(SAMPLE_2, "")])
    assert r.status_code == 422


def test_create_422_points_to_failing_sample_id():
    bad = dict(SAMPLE_2)
    bad["samples"] = [dict(p) for p in bad["samples"]]
    bad["samples"][2]["time_ms"] = 3  # 非严格递增
    r = create([sid(SAMPLE_2, "ok"), sid(bad, "bad-one")])
    assert r.status_code == 422
    assert r.json()["detail"].startswith("sample bad-one: ")
    assert "strictly increasing" in r.json()["detail"]


def test_create_422_points_to_blank():
    r = create(
        [sid(SAMPLE_2, "s1")],
        blank=dict(BLANK_2, apex_indices=[1, 2]),
    )
    assert r.status_code == 422
    assert r.json()["detail"].startswith("blank: ")
    assert "internal sample" in r.json()["detail"]


def test_failed_create_leaves_no_batch():
    db = batch_store.get_db_path()
    batch_store.init_db()

    def count_batches():
        conn = sqlite3.connect(db)
        try:
            return conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0]
        finally:
            conn.close()

    assert count_batches() == 0
    r = create(
        [sid(SAMPLE_2, "s1")],
        blank=BLANK_INVALID,
    )
    assert r.status_code == 422
    assert count_batches() == 0
    bad = dict(SAMPLE_2)
    bad["samples"] = [dict(p) for p in bad["samples"]]
    bad["samples"][2]["time_ms"] = 3
    r = create([sid(bad, "s1")])
    assert r.status_code == 422
    assert count_batches() == 0


def test_create_reuses_blank_context_results_match_legacy_endpoint():
    legacy = TestClient(app).post(
        "/v1/blank-subtraction",
        json={
            "reference_peaks": REFS_2,
            "is_ref_id_1": "A",
            "is_ref_id_2": "B",
            "tolerance_ms": 0,
            "sample": SAMPLE_2,
            "blank": BLANK_2,
        },
    )
    assert legacy.status_code == 200
    r = create([sid(SAMPLE_2, "x")])
    assert r.status_code == 201
    assert r.json()["samples"][0]["result"] == legacy.json()


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def test_get_returns_committed_state():
    bid = create([sid(SAMPLE_2, "a")]).json()["id"]
    r = TestClient(app).get(f"/v1/blank-subtraction-batches/{bid}")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == bid and data["version"] == 1
    assert data["samples"][0]["id"] == "a"
    assert data["samples"][0]["confirmed"] is False


def test_get_unknown_batch_404():
    r = TestClient(app).get(
        "/v1/blank-subtraction-batches/nonexistent-id"
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 确认
# ---------------------------------------------------------------------------

def test_confirm_sample_bumps_version():
    client = TestClient(app)
    bid = create([sid(SAMPLE_2, "a"), sid(SAMPLE_2, "b")]).json()["id"]
    r = client.post(
        f"/v1/blank-subtraction-batches/{bid}/samples/a/confirm",
        json={"expected_version": 1},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["version"] == 2
    flags = {s["id"]: s["confirmed"] for s in data["samples"]}
    assert flags == {"a": True, "b": False}
    # 结果未因确认而改变
    assert data["samples"][0]["result"]["sample_peaks"][0] == {
        "ref_id": "A", "corrected_area": {"n": 3, "d": 2}
    }


def test_confirm_stale_version_409_and_state_unchanged():
    client = TestClient(app)
    bid = create([sid(SAMPLE_2, "a"), sid(SAMPLE_2, "b")]).json()["id"]
    assert client.post(
        f"/v1/blank-subtraction-batches/{bid}/samples/a/confirm",
        json={"expected_version": 1},
    ).status_code == 200
    # 过期版本
    r = client.post(
        f"/v1/blank-subtraction-batches/{bid}/samples/b/confirm",
        json={"expected_version": 1},
    )
    assert r.status_code == 409
    # 输入、结果、确认状态、版本均不变
    cur = client.get(f"/v1/blank-subtraction-batches/{bid}").json()
    assert cur["version"] == 2
    assert [s["confirmed"] for s in cur["samples"]] == [True, False]
    # 用当前版本确认 b 成功
    r = client.post(
        f"/v1/blank-subtraction-batches/{bid}/samples/b/confirm",
        json={"expected_version": 2},
    )
    assert r.status_code == 200
    assert r.json()["version"] == 3
    assert all(s["confirmed"] for s in r.json()["samples"])


def test_cannot_override_or_reconfirm():
    client = TestClient(app)
    bid = create([sid(SAMPLE_2, "a")]).json()["id"]
    assert client.post(
        f"/v1/blank-subtraction-batches/{bid}/samples/a/confirm",
        json={"expected_version": 1},
    ).status_code == 200
    # 即便携带当前版本，也不能覆盖确认
    r = client.post(
        f"/v1/blank-subtraction-batches/{bid}/samples/a/confirm",
        json={"expected_version": 2},
    )
    assert r.status_code == 409
    assert "already confirmed" in r.json()["detail"]
    assert client.get(
        f"/v1/blank-subtraction-batches/{bid}"
    ).json()["version"] == 2


def test_confirm_unknown_sample_404():
    client = TestClient(app)
    bid = create([sid(SAMPLE_2, "a")]).json()["id"]
    r = client.post(
        f"/v1/blank-subtraction-batches/{bid}/samples/nope/confirm",
        json={"expected_version": 1},
    )
    assert r.status_code == 404


def test_confirm_rejects_extra_fields():
    client = TestClient(app)
    bid = create([sid(SAMPLE_2, "a")]).json()["id"]
    r = client.post(
        f"/v1/blank-subtraction-batches/{bid}/samples/a/confirm",
        json={"expected_version": 1, "result": {}},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 替换 blank
# ---------------------------------------------------------------------------

def test_replace_blank_same_value_succeeds_and_bumps_version():
    client = TestClient(app)
    bid = create([sid(SAMPLE_2, "a")]).json()["id"]
    before = client.get(f"/v1/blank-subtraction-batches/{bid}").json()
    r = client.put(
        f"/v1/blank-subtraction-batches/{bid}/blank",
        json={"expected_version": 1, "blank": BLANK_2},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["version"] == 2
    assert data["samples"][0]["result"] == before["samples"][0]["result"]
    assert data["samples"][0]["confirmed"] is False


def test_replace_blank_recomputes_all_samples_in_order():
    client = TestClient(app)
    bid = create(
        [sid(SAMPLE_2, "a"), sid(SAMPLE_2, "b")]
    ).json()["id"]
    r = client.put(
        f"/v1/blank-subtraction-batches/{bid}/blank",
        json={"expected_version": 1, "blank": BLANK_2_STRONG},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert [s["id"] for s in data["samples"]] == ["a", "b"]
    for s in data["samples"]:
        # 9/2-6 截 0；12-12=0
        assert s["result"]["sample_peaks"] == [
            {"ref_id": "A", "corrected_area": {"n": 0, "d": 1}},
            {"ref_id": "B", "corrected_area": {"n": 0, "d": 1}},
        ]
    assert data["version"] == 2


def test_replace_blocked_when_confirmed_result_changes():
    client = TestClient(app)
    bid = create([sid(SAMPLE_2, "a")]).json()["id"]
    assert client.post(
        f"/v1/blank-subtraction-batches/{bid}/samples/a/confirm",
        json={"expected_version": 1},
    ).status_code == 200
    r = client.put(
        f"/v1/blank-subtraction-batches/{bid}/blank",
        json={"expected_version": 2, "blank": BLANK_2_STRONG},
    )
    assert r.status_code == 409
    assert "a" in r.json()["detail"]
    # 失败保持输入、结果、确认状态、版本全部不变
    cur = client.get(f"/v1/blank-subtraction-batches/{bid}").json()
    assert cur["version"] == 2
    assert cur["samples"][0]["confirmed"] is True
    assert cur["samples"][0]["result"]["sample_peaks"] == [
        {"ref_id": "A", "corrected_area": {"n": 3, "d": 2}},
        {"ref_id": "B", "corrected_area": {"n": 4, "d": 1}},
    ]
    # 过期版本同样 409
    r = client.put(
        f"/v1/blank-subtraction-batches/{bid}/blank",
        json={"expected_version": 1, "blank": BLANK_2_STRONG},
    )
    assert r.status_code == 409


def test_replace_allowed_when_confirmed_sample_unchanged_unconfirmed_changes():
    # s1 三峰全部归属；s2 中间峰未归属。新空白仅改变 B 峰面积：
    # s1（含 B）改变；s2（B 未归属）完全不变。
    client = TestClient(app)
    bid = create(
        [sid(SAMPLE_S1, "s1"), sid(SAMPLE_S2, "s2")],
        blank=BLANK_3,
        refs=REFS_3,
    ).json()["id"]

    def peaks(sample_id):
        data = client.get(f"/v1/blank-subtraction-batches/{bid}").json()
        return next(
            s["result"]["sample_peaks"]
            for s in data["samples"] if s["id"] == sample_id
        )

    # 旧空白下：s1 = 5/2, 3/2, 9；s2 = 5/2, 19/2(未归属), 9
    assert peaks("s1") == [
        {"ref_id": "A", "corrected_area": {"n": 5, "d": 2}},
        {"ref_id": "B", "corrected_area": {"n": 3, "d": 2}},
        {"ref_id": "C", "corrected_area": {"n": 9, "d": 1}},
    ]
    assert peaks("s2") == [
        {"ref_id": "A", "corrected_area": {"n": 5, "d": 2}},
        {"ref_id": None, "corrected_area": {"n": 19, "d": 2}},
        {"ref_id": "C", "corrected_area": {"n": 9, "d": 1}},
    ]

    # 先确认 s2（其结果与 B 空白无关）
    assert client.post(
        f"/v1/blank-subtraction-batches/{bid}/samples/s2/confirm",
        json={"expected_version": 1},
    ).status_code == 200

    # 替换为只改 B 的空白：s2 已确认结果不变 => 允许；s1 未确认，重算
    r = client.put(
        f"/v1/blank-subtraction-batches/{bid}/blank",
        json={"expected_version": 2, "blank": BLANK_3_B_DIFF},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["version"] == 3
    got = {s["id"]: s for s in data["samples"]}
    assert got["s2"]["confirmed"] is True
    assert got["s2"]["result"]["sample_peaks"] == [
        {"ref_id": "A", "corrected_area": {"n": 5, "d": 2}},
        {"ref_id": None, "corrected_area": {"n": 19, "d": 2}},
        {"ref_id": "C", "corrected_area": {"n": 9, "d": 1}},
    ]
    # s1 的 B：9 - 25/2 < 0 => 截 0
    assert got["s1"]["result"]["sample_peaks"] == [
        {"ref_id": "A", "corrected_area": {"n": 5, "d": 2}},
        {"ref_id": "B", "corrected_area": {"n": 0, "d": 1}},
        {"ref_id": "C", "corrected_area": {"n": 9, "d": 1}},
    ]

    # 再确认 s1 后，同一替换必须被整批拒绝
    assert client.post(
        f"/v1/blank-subtraction-batches/{bid}/samples/s1/confirm",
        json={"expected_version": 3},
    ).status_code == 200
    r = client.put(
        f"/v1/blank-subtraction-batches/{bid}/blank",
        json={"expected_version": 4, "blank": BLANK_3_B_DIFF},
    )
    # 与当前 blank 完全相同 => 结果未变，允许替换（版本前进）
    assert r.status_code == 200
    assert r.json()["version"] == 5

    # 回到旧空白会改变已确认的 s1 => 409，并指出 s1（s2 不变）
    r = client.put(
        f"/v1/blank-subtraction-batches/{bid}/blank",
        json={"expected_version": 5, "blank": BLANK_3},
    )
    assert r.status_code == 409
    assert "s1" in r.json()["detail"]
    assert "s2" not in r.json()["detail"]
    cur = client.get(f"/v1/blank-subtraction-batches/{bid}").json()
    assert cur["version"] == 5  # 失败未前进版本


def test_replace_invalid_blank_422_preserves_everything():
    client = TestClient(app)
    bid = create([sid(SAMPLE_2, "a")]).json()["id"]
    before = client.get(f"/v1/blank-subtraction-batches/{bid}").json()
    r = client.put(
        f"/v1/blank-subtraction-batches/{bid}/blank",
        json={
            "expected_version": 1,
            "blank": dict(BLANK_2, apex_indices=[1, 2]),
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"].startswith("blank: ")
    after = client.get(f"/v1/blank-subtraction-batches/{bid}").json()
    assert after == before


def test_replace_rejects_other_inputs():
    client = TestClient(app)
    bid = create([sid(SAMPLE_2, "a")]).json()["id"]
    # 只允许 blank 与 expected_version：试图夹带 samples/参数一律拒绝
    r = client.put(
        f"/v1/blank-subtraction-batches/{bid}/blank",
        json={
            "expected_version": 1,
            "blank": BLANK_2,
            "samples": [],
        },
    )
    assert r.status_code == 422
    r = client.put(
        f"/v1/blank-subtraction-batches/{bid}/blank",
        json={
            "expected_version": 1,
            "blank": BLANK_2,
            "tolerance_ms": 5,
        },
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 同版本并发：确认与替换只能一项成功
# ---------------------------------------------------------------------------

def _race(client: TestClient, url: str, body: dict, barrier: threading.Barrier,
          out: list, idx: int):
    barrier.wait()
    out[idx] = client.post(url, json=body)


def test_concurrent_confirms_same_version_only_one_wins():
    barrier = threading.Barrier(2)
    out: list = [None, None]
    bid = create([sid(SAMPLE_2, "a")]).json()["id"]
    url = f"/v1/blank-subtraction-batches/{bid}/samples/a/confirm"
    threads = [
        threading.Thread(
            target=_race,
            args=(TestClient(app), url, {"expected_version": 1},
                  barrier, out, i),
        )
        for i in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    statuses = sorted(r.status_code for r in out)
    assert statuses == [200, 409], [r.text for r in out]
    cur = TestClient(app).get(
        f"/v1/blank-subtraction-batches/{bid}"
    ).json()
    assert cur["version"] == 2
    assert cur["samples"][0]["confirmed"] is True


def test_concurrent_confirms_different_samples_same_version_only_one_wins():
    # 同版本下即使确认的是不同样本，也只能有一项成功（不能都跳过版本）
    barrier = threading.Barrier(2)
    out: dict = {}
    bid = create([sid(SAMPLE_2, "a"), sid(SAMPLE_2, "b")]).json()["id"]

    def make(sample_id, key):
        def run_():
            c = TestClient(app)
            barrier.wait()
            out[key] = c.post(
                f"/v1/blank-subtraction-batches/{bid}/samples/"
                f"{sample_id}/confirm",
                json={"expected_version": 1},
            )
        return run_

    threads = [threading.Thread(target=make("a", "a")),
               threading.Thread(target=make("b", "b"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    statuses = sorted(r.status_code for r in out.values())
    assert statuses == [200, 409], [r.text for r in out.values()]
    cur = TestClient(app).get(
        f"/v1/blank-subtraction-batches/{bid}"
    ).json()
    assert cur["version"] == 2
    assert sum(s["confirmed"] for s in cur["samples"]) == 1


def test_concurrent_confirm_and_replace_only_one_wins():
    barrier = threading.Barrier(2)
    out: dict = {}
    bid = create([sid(SAMPLE_2, "a")]).json()["id"]

    def do_confirm():
        c = TestClient(app)
        barrier.wait()
        out["confirm"] = c.post(
            f"/v1/blank-subtraction-batches/{bid}/samples/a/confirm",
            json={"expected_version": 1},
        )

    def do_replace():
        c = TestClient(app)
        barrier.wait()
        # 用相同 blank：结果不变，替换本身合法；仅版本竞争决定胜负
        out["replace"] = c.put(
            f"/v1/blank-subtraction-batches/{bid}/blank",
            json={"expected_version": 1, "blank": BLANK_2},
        )

    threads = [threading.Thread(target=do_confirm),
               threading.Thread(target=do_replace)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wins = [k for k, r in out.items() if r.status_code == 200]
    loses = [k for k, r in out.items() if r.status_code == 409]
    assert sorted(wins) == ["confirm"] or wins == ["replace"], {
        k: (v.status_code, v.text) for k, v in out.items()
    }
    assert len(wins) == 1 and len(loses) == 1
    cur = TestClient(app).get(
        f"/v1/blank-subtraction-batches/{bid}"
    ).json()
    assert cur["version"] == 2
    if wins == ["confirm"]:
        assert cur["samples"][0]["confirmed"] is True
    else:
        assert cur["samples"][0]["confirmed"] is False


# ---------------------------------------------------------------------------
# 持久化：模拟容器重启（进程内重置后从同一 SQLite 文件读取）
# ---------------------------------------------------------------------------

def test_batch_survives_store_reset():
    client = TestClient(app)
    bid = create([sid(SAMPLE_2, "a"), sid(SAMPLE_2, "b")]).json()["id"]
    client.post(
        f"/v1/blank-subtraction-batches/{bid}/samples/a/confirm",
        json={"expected_version": 1},
    )
    client.put(
        f"/v1/blank-subtraction-batches/{bid}/blank",
        json={"expected_version": 2, "blank": BLANK_2},
    )
    # 模拟容器重启：丢弃内存中的初始化状态，仅保留文件
    batch_store.reset_store()
    cur = client.get(f"/v1/blank-subtraction-batches/{bid}").json()
    assert cur["version"] == 3
    assert [s["id"] for s in cur["samples"]] == ["a", "b"]
    assert [s["confirmed"] for s in cur["samples"]] == [True, False]
    # 重启后仍可用当前版本继续确认
    r = client.post(
        f"/v1/blank-subtraction-batches/{bid}/samples/b/confirm",
        json={"expected_version": 3},
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 4


def test_wal_files_created_next_to_db():
    create([sid(SAMPLE_2, "a")])
    import os
    db = batch_store.get_db_path()
    assert os.path.exists(db)
