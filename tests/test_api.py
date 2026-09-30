"""FastAPI 接口层测试（TestClient 走真实 ASGI 栈）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def payload(**over):
    body = {
        "reference_peaks": [
            {"id": "p0", "time_ms": 10},
            {"id": "p1", "time_ms": 22},
            {"id": "p2", "time_ms": 31},
            {"id": "p3", "time_ms": 34},
            {"id": "p4", "time_ms": 40},
            {"id": "p5", "time_ms": 55},
        ],
        "measured_times_ms": [3, 6, 14, 20, 22, 26, 36],
        "is_ref_id_1": "p0",
        "is_meas_idx_1": 1,
        "is_ref_id_2": "p4",
        "is_meas_idx_2": 5,
        "tolerance_ms": 0,
    }
    body.update(over)
    return body


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_assign_happy_path():
    r = client.post("/v1/assign", json=payload())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["assignments"] == [1, 2, 3, 4, 5, 6]
    assert data["unpaired_meas_indices"] == [0]
    m0 = next(m for m in data["matches"] if m["ref_id"] == "p0")
    assert m0 == {
        "ref_id": "p0",
        "ref_index": 0,
        "meas_index": 1,
        "calibrated_time_ms": {"n": 10, "d": 1},
        "signed_deviation_ms": {"n": 0, "d": 1},
    }
    # matches 按参考下标升序
    assert [m["ref_index"] for m in data["matches"]] == [0, 1, 2, 3, 4, 5]


def test_422_on_crossed_internal_standards():
    r = client.post(
        "/v1/assign",
        json=payload(is_meas_idx_1=5, is_meas_idx_2=1),
    )
    assert r.status_code == 422
    assert "order must be consistent" in r.json()["detail"]


def test_422_on_bad_ref_id():
    r = client.post("/v1/assign", json=payload(is_ref_id_1="nope"))
    assert r.status_code == 422
    assert "not found" in r.json()["detail"]


def test_422_on_index_out_of_range():
    r = client.post("/v1/assign", json=payload(is_meas_idx_2=99))
    assert r.status_code == 422


def test_422_on_duplicate_ref_ids():
    peaks = payload()["reference_peaks"]
    peaks[1]["id"] = "p0"
    r = client.post("/v1/assign", json=payload(reference_peaks=peaks))
    assert r.status_code == 422
    assert "unique" in r.json()["detail"]


def test_422_on_non_increasing_ref_times():
    peaks = payload()["reference_peaks"]
    peaks[2]["time_ms"] = 22
    r = client.post("/v1/assign", json=payload(reference_peaks=peaks))
    assert r.status_code == 422
    assert "strictly increasing" in r.json()["detail"]


def test_422_on_negative_tolerance():
    r = client.post("/v1/assign", json=payload(tolerance_ms=-1))
    assert r.status_code == 422


def test_422_on_length_violations():
    body = payload()
    body["reference_peaks"] = body["reference_peaks"][:1]  # 仅 1 个
    r = client.post("/v1/assign", json=body)
    assert r.status_code == 422

    body = payload()
    body["measured_times_ms"] = [1]
    r = client.post("/v1/assign", json=body)
    assert r.status_code == 422

    # 超过 60 个实测峰
    body = payload()
    body["measured_times_ms"] = list(range(61))
    r = client.post("/v1/assign", json=body)
    assert r.status_code == 422


def test_422_on_wrong_types():
    # 布尔不能冒充整数
    r = client.post("/v1/assign", json=payload(tolerance_ms=True))
    assert r.status_code == 422
    # 浮点不能冒充整数
    r = client.post("/v1/assign", json=payload(tolerance_ms=1.5))
    assert r.status_code == 422
    # 缺字段
    body = payload()
    del body["tolerance_ms"]
    r = client.post("/v1/assign", json=body)
    assert r.status_code == 422


def test_swapped_is_pair_order_accepted():
    # 请求中两对内标的书写顺序互换，服务端按参考方向归一化后结果一致
    r1 = client.post("/v1/assign", json=payload())
    r2 = client.post(
        "/v1/assign",
        json=payload(
            is_ref_id_1="p4", is_meas_idx_1=5,
            is_ref_id_2="p0", is_meas_idx_2=1,
        ),
    )
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
