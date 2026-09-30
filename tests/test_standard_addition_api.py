"""标准加入法 HTTP 接口测试（TestClient 走真实 ASGI 栈）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

REFS = [
    {"id": "junk", "time_ms": 10},
    {"id": "T", "time_ms": 20},
    {"id": "S1", "time_ms": 30},
    {"id": "S2", "time_ms": 40},
]


def inj(concentration, target, a1, a2):
    return {
        "added_concentration": concentration,
        "measured_peaks": [
            {"time_ms": 10, "area": 0},
            {"time_ms": 20, "area": target},
            {"time_ms": 30, "area": a1},
            {"time_ms": 40, "area": a2},
        ],
        "is_meas_idx_1": 2,
        "is_meas_idx_2": 3,
    }


def payload(**over):
    body = {
        "reference_peaks": REFS,
        "target_ref_id": "T",
        "is_ref_id_1": "S1",
        "is_ref_id_2": "S2",
        "tolerance_ms": 0,
        "injections": [
            inj(0, 412, 1000, 700),
            inj(10, 510, 1000, 700),
            inj(20, 708, 1200, 800),
            inj(40, 1008, 1500, 900),
        ],
    }
    body.update(over)
    return body


def test_standard_addition_happy_path():
    r = client.post("/v1/standard-addition", json=payload())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["slope"] == {"n": 139, "d": 21875}
    assert data["intercept"] == {"n": 1087, "d": 2500}
    assert data["sample_concentration"] == {"n": 38045, "d": 556}
    assert len(data["residuals"]) == 4
    assert [x["added_concentration"] for x in data["residuals"]] == [0, 10, 20, 40]
    # 分数全部约分、分母为正
    for key in ("slope", "intercept", "sample_concentration"):
        assert data[key]["d"] > 0
    for row in data["residuals"]:
        assert set(row) == {
            "added_concentration",
            "area_ratio",
            "predicted_area_ratio",
            "residual",
        }
        assert row["area_ratio"]["d"] > 0
        assert row["residual"]["d"] > 0


def test_422_target_is_internal_standard():
    r = client.post("/v1/standard-addition", json=payload(target_ref_id="S1"))
    assert r.status_code == 422
    assert "target peak must not be an internal standard" in r.json()["detail"]


def test_422_unknown_target():
    r = client.post("/v1/standard-addition", json=payload(target_ref_id="zzz"))
    assert r.status_code == 422
    assert "target_ref_id not found" in r.json()["detail"]


def test_422_target_unassigned_names_injection_index():
    body = payload()
    body["injections"][1]["measured_peaks"][1]["time_ms"] = 9999
    r = client.post("/v1/standard-addition", json=body)
    assert r.status_code == 422
    assert r.json()["detail"].startswith("injection 1:")
    assert "target peak not assigned" in r.json()["detail"]


def test_422_zero_is1_area_names_injection_index():
    body = payload()
    body["injections"][2]["measured_peaks"][2]["area"] = 0
    r = client.post("/v1/standard-addition", json=body)
    assert r.status_code == 422
    assert r.json()["detail"].startswith("injection 2:")


def test_422_single_concentration_value():
    body = payload()
    for x in body["injections"]:
        x["added_concentration"] = 7
    r = client.post("/v1/standard-addition", json=body)
    assert r.status_code == 422
    assert "at least two distinct" in r.json()["detail"]


def test_422_non_positive_slope():
    body = payload()
    for x, area in zip(body["injections"], (800, 700, 600, 400)):
        x["measured_peaks"][1]["area"] = area
    r = client.post("/v1/standard-addition", json=body)
    assert r.status_code == 422
    assert "slope must be positive" in r.json()["detail"]


def test_422_negative_intercept():
    body = payload()
    body["injections"] = [
        inj(0, 0, 1000, 5),
        inj(10, 1000, 1000, 5),
        inj(20, 2001, 1000, 5),
    ]
    r = client.post("/v1/standard-addition", json=body)
    assert r.status_code == 422
    assert "intercept must be non-negative" in r.json()["detail"]


def test_422_on_injection_count_violations():
    body = payload()
    body["injections"] = body["injections"][:2]  # 仅 2 次
    r = client.post("/v1/standard-addition", json=body)
    assert r.status_code == 422

    body = payload()
    body["injections"] = [inj(i, 500 + 6 * i, 1000, 5) for i in range(13)]  # 13 次
    r = client.post("/v1/standard-addition", json=body)
    assert r.status_code == 422


def test_422_on_measured_peak_count_violations():
    body = payload()
    # 仅保留 1 个实测峰（低于下限 2），同步修正内标下标避免越界干扰
    body["injections"][0]["measured_peaks"] = [
        {"time_ms": 30, "area": 1000}
    ]
    body["injections"][0]["is_meas_idx_1"] = 0
    body["injections"][0]["is_meas_idx_2"] = 0
    r = client.post("/v1/standard-addition", json=body)
    assert r.status_code == 422


def test_422_on_wrong_types_and_constraints():
    body = payload()
    # 负面积
    body["injections"][0]["measured_peaks"][1]["area"] = -1
    r = client.post("/v1/standard-addition", json=body)
    assert r.status_code == 422

    # 负加标浓度
    body = payload()
    body["injections"][0]["added_concentration"] = -1
    r = client.post("/v1/standard-addition", json=body)
    assert r.status_code == 422

    # 布尔/浮点不能冒充整数
    body = payload()
    body["injections"][0]["measured_peaks"][1]["area"] = 1.5
    r = client.post("/v1/standard-addition", json=body)
    assert r.status_code == 422

    body = payload()
    body["tolerance_ms"] = True
    r = client.post("/v1/standard-addition", json=body)
    assert r.status_code == 422

    # 缺字段
    body = payload()
    del body["target_ref_id"]
    r = client.post("/v1/standard-addition", json=body)
    assert r.status_code == 422


def test_422_equal_measured_is_indices_names_injection():
    body = payload()
    body["injections"][0]["is_meas_idx_2"] = body["injections"][0]["is_meas_idx_1"]
    r = client.post("/v1/standard-addition", json=body)
    assert r.status_code == 422
    assert r.json()["detail"].startswith("injection 0:")


def test_assign_endpoint_still_works():
    # 回归：既有 /v1/assign 不受影响
    r = client.post(
        "/v1/assign",
        json={
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
        },
    )
    assert r.status_code == 200
    assert r.json()["assignments"] == [1, 2, 3, 4, 5, 6]
