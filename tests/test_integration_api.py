"""峰面积积分 HTTP 接口测试（TestClient 走真实 ASGI 栈），含与既有接口的衔接。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

TIMES = list(range(0, 101, 10))
INTENS = [2, 5, 9, 5, 3, 4, 8, 2, 5, 3, 2]


def payload(**over):
    body = {
        "samples": [
            {"time_ms": t, "intensity": y} for t, y in zip(TIMES, INTENS)
        ],
        "start_index": 0,
        "end_index": 10,
        "apex_indices": [2, 6, 8],
    }
    body.update(over)
    return body


def test_integrate_happy_path():
    r = client.post("/v1/integrate", json=payload())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["segments"] == [
        {"start_index": 0, "end_index": 4, "area": {"n": 115, "d": 1}},
        {"start_index": 4, "end_index": 7, "area": {"n": 70, "d": 1}},
        {"start_index": 7, "end_index": 10, "area": {"n": 40, "d": 1}},
    ]
    assert data["area_scale"] == 1
    assert data["peaks"] == [
        {"time_ms": 20, "area": 115},
        {"time_ms": 60, "area": 70},
        {"time_ms": 80, "area": 40},
    ]


def test_422_on_non_increasing_times():
    body = payload()
    body["samples"][3]["time_ms"] = 20
    r = client.post("/v1/integrate", json=body)
    assert r.status_code == 422
    assert "strictly increasing" in r.json()["detail"]


def test_422_on_sample_count_out_of_range():
    r = client.post("/v1/integrate", json=payload(samples=payload()["samples"][:4]))
    assert r.status_code == 422
    big = [{"time_ms": i, "intensity": 1} for i in range(501)]
    r = client.post(
        "/v1/integrate",
        json=payload(samples=big, start_index=0, end_index=500,
                     apex_indices=[100, 300]),
    )
    assert r.status_code == 422


def test_422_on_negative_intensity():
    body = payload()
    body["samples"][0]["intensity"] = -1
    r = client.post("/v1/integrate", json=body)
    assert r.status_code == 422


def test_422_on_bad_index_ranges():
    r = client.post("/v1/integrate", json=payload(start_index=11))
    assert r.status_code == 422
    assert "out of range" in r.json()["detail"]
    r = client.post("/v1/integrate", json=payload(end_index=11))
    assert r.status_code == 422
    r = client.post("/v1/integrate", json=payload(start_index=-1))
    assert r.status_code == 422
    r = client.post("/v1/integrate", json=payload(start_index=6, end_index=6))
    assert r.status_code == 422
    assert "less than" in r.json()["detail"]


def test_422_on_bad_apex_indices():
    # 数量越界
    r = client.post("/v1/integrate", json=payload(apex_indices=[2]))
    assert r.status_code == 422
    r = client.post("/v1/integrate", json=payload(apex_indices=list(range(61))))
    assert r.status_code == 422
    # 非严格递增
    r = client.post("/v1/integrate", json=payload(apex_indices=[6, 2, 8]))
    assert r.status_code == 422
    assert "strictly increasing" in r.json()["detail"]
    # 峰顶落在起止边界或之外
    r = client.post("/v1/integrate", json=payload(apex_indices=[0, 6, 8]))
    assert r.status_code == 422
    assert "strictly between" in r.json()["detail"]
    r = client.post("/v1/integrate", json=payload(apex_indices=[2, 6, 10]))
    assert r.status_code == 422
    # 相邻峰顶之间没有内部采样点
    r = client.post("/v1/integrate", json=payload(apex_indices=[2, 3, 8]))
    assert r.status_code == 422
    assert "internal sample" in r.json()["detail"]


def test_422_on_wrong_types():
    body = payload()
    body["samples"][1]["intensity"] = True  # 布尔不能冒充整数
    r = client.post("/v1/integrate", json=body)
    assert r.status_code == 422
    body = payload()
    body["samples"][1]["time_ms"] = 1.5  # 浮点不能被截断为整数
    r = client.post("/v1/integrate", json=body)
    assert r.status_code == 422
    body = payload()
    del body["apex_indices"]  # 缺字段
    r = client.post("/v1/integrate", json=body)
    assert r.status_code == 422


def test_integrate_output_chains_into_assign_and_standard_addition():
    """峰数组直接作为定量接口的 measured_peaks，其时间序列作为归属接口的
    measured_times_ms。"""
    r = client.post("/v1/integrate", json=payload())
    assert r.status_code == 200, r.text
    peaks = r.json()["peaks"]  # 峰顶时间 20/60/80，面积 115/70/40
    peak_times = [p["time_ms"] for p in peaks]

    # —— 衔接归属接口：峰顶时间作为实测时间 ——
    refs = [
        {"id": "T", "time_ms": 20},
        {"id": "S1", "time_ms": 60},
        {"id": "S2", "time_ms": 80},
    ]
    r = client.post(
        "/v1/assign",
        json={
            "reference_peaks": refs,
            "measured_times_ms": peak_times,
            "is_ref_id_1": "S1",
            "is_meas_idx_1": 1,
            "is_ref_id_2": "S2",
            "is_meas_idx_2": 2,
            "tolerance_ms": 0,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["assignments"] == [0, 1, 2]

    # —— 衔接定量接口：峰数组作为各次进样的 measured_peaks ——
    injections = []
    for conc in (0, 5, 10):
        measured = [dict(p) for p in peaks]
        measured[0]["area"] += 100 * conc  # 目标峰面积随加标浓度线性增长
        injections.append(
            {
                "added_concentration": conc,
                "measured_peaks": measured,
                "is_meas_idx_1": 1,
                "is_meas_idx_2": 2,
            }
        )
    r = client.post(
        "/v1/standard-addition",
        json={
            "reference_peaks": refs,
            "target_ref_id": "T",
            "is_ref_id_1": "S1",
            "is_ref_id_2": "S2",
            "tolerance_ms": 0,
            "injections": injections,
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    # 面积比 = (115 + 100c) / 70 = (10/7)c + 23/14
    assert data["slope"] == {"n": 10, "d": 7}
    assert data["intercept"] == {"n": 23, "d": 14}
    assert data["sample_concentration"] == {"n": 23, "d": 20}
