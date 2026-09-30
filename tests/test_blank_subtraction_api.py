"""空白扣除 HTTP 接口测试（TestClient 走真实 ASGI 栈），含与定量接口的衔接。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

REFS_2 = [
    {"id": "A", "time_ms": 3},
    {"id": "B", "time_ms": 7},
]
REFS_3 = [
    {"id": "A", "time_ms": 3},
    {"id": "B", "time_ms": 7},
    {"id": "C", "time_ms": 12},
]


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


def payload(**over):
    # 样本：面积 9/2、12（area_scale 2）；空白：不同采样时间，面积 3、8
    # （area_scale 1），峰顶 5/9 经内标仿射校准后恰为 3/7。
    body = {
        "reference_peaks": REFS_2,
        "is_ref_id_1": "A",
        "is_ref_id_2": "B",
        "tolerance_ms": 0,
        "sample": run([0, 3, 4, 7, 10], [1, 4, 2, 5, 0], [1, 3], 0, 1),
        "blank": run([0, 5, 6, 9, 14], [1, 2, 1, 3, 1], [1, 3], 0, 1),
    }
    body.update(over)
    return body


def test_blank_subtraction_happy_path():
    r = client.post("/v1/blank-subtraction", json=payload())
    assert r.status_code == 200, r.text
    data = r.json()
    # 9/2 - 3 = 3/2；12 - 8 = 4
    assert data["sample_peaks"] == [
        {"ref_id": "A", "corrected_area": {"n": 3, "d": 2}},
        {"ref_id": "B", "corrected_area": {"n": 4, "d": 1}},
    ]
    assert data["area_scale"] == 2
    assert data["peaks"] == [
        {"time_ms": 3, "area": 3},
        {"time_ms": 7, "area": 8},
    ]


def test_negative_correction_clamped_to_zero():
    # 空白 A 峰面积 15 > 样本 9/2，校正后截为 0；零面积峰保留
    blank = run([0, 5, 6, 9, 14], [1, 6, 1, 3, 1], [1, 3], 0, 1)
    r = client.post("/v1/blank-subtraction", json=payload(blank=blank))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["sample_peaks"] == [
        {"ref_id": "A", "corrected_area": {"n": 0, "d": 1}},
        {"ref_id": "B", "corrected_area": {"n": 4, "d": 1}},
    ]
    assert data["area_scale"] == 1
    assert data["peaks"] == [
        {"time_ms": 3, "area": 0},
        {"time_ms": 7, "area": 4},
    ]


def test_unassigned_sample_peak_keeps_area_and_blank_only_peak_dropped():
    # 样本中间峰在 8（容差 0 下未归属），空白在 7 有 B 峰（空白独有）：
    # 样本未归属峰保持原面积 19/2，空白独有的 B 峰不参与扣除也不输出。
    sample = run([0, 3, 4, 8, 9, 12, 14], [1, 4, 2, 5, 1, 6, 0], [1, 3, 5], 0, 2)
    blank = run([0, 3, 4, 7, 9, 12, 14], [1, 2, 1, 4, 1, 3, 1], [1, 3, 5], 0, 2)
    body = payload(
        reference_peaks=REFS_3,
        is_ref_id_1="A",
        is_ref_id_2="C",
        sample=sample,
        blank=blank,
    )
    r = client.post("/v1/blank-subtraction", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    # A: 9/2 - 2 = 5/2；中间峰未归属保持 19/2；C: 14 - 5 = 9
    assert data["sample_peaks"] == [
        {"ref_id": "A", "corrected_area": {"n": 5, "d": 2}},
        {"ref_id": None, "corrected_area": {"n": 19, "d": 2}},
        {"ref_id": "C", "corrected_area": {"n": 9, "d": 1}},
    ]
    assert data["area_scale"] == 2
    assert data["peaks"] == [
        {"time_ms": 3, "area": 5},
        {"time_ms": 8, "area": 19},
        {"time_ms": 12, "area": 18},
    ]


def test_blank_missing_reference_peak_means_no_subtraction():
    # 空白只有 A、C 两峰：样本 B 峰不扣；空白峰数与样本可以不同
    sample = run([0, 3, 4, 7, 9, 12, 14], [1, 4, 2, 5, 1, 6, 0], [1, 3, 5], 0, 2)
    blank = run([0, 3, 4, 12, 14], [1, 2, 1, 3, 1], [1, 3], 0, 1)
    body = payload(
        reference_peaks=REFS_3,
        is_ref_id_1="A",
        is_ref_id_2="C",
        sample=sample,
        blank=blank,
    )
    r = client.post("/v1/blank-subtraction", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    # A: 9/2 - 2 = 5/2；B: 空白无此参考峰，保持 9；C: 14 - 10 = 4
    assert data["sample_peaks"] == [
        {"ref_id": "A", "corrected_area": {"n": 5, "d": 2}},
        {"ref_id": "B", "corrected_area": {"n": 9, "d": 1}},
        {"ref_id": "C", "corrected_area": {"n": 4, "d": 1}},
    ]
    assert data["area_scale"] == 2
    assert data["peaks"] == [
        {"time_ms": 3, "area": 5},
        {"time_ms": 7, "area": 18},
        {"time_ms": 12, "area": 8},
    ]


def test_422_on_sample_integration_failure():
    body = payload()
    body["sample"]["samples"][2]["time_ms"] = 3  # 样本时间非严格递增
    r = client.post("/v1/blank-subtraction", json=body)
    assert r.status_code == 422
    assert r.json()["detail"].startswith("sample: ")
    assert "strictly increasing" in r.json()["detail"]


def test_422_on_blank_integration_failure():
    body = payload()
    body["blank"]["apex_indices"] = [1, 2]  # 空白相邻峰顶无内部采样点
    r = client.post("/v1/blank-subtraction", json=body)
    assert r.status_code == 422
    assert r.json()["detail"].startswith("blank: ")
    assert "internal sample" in r.json()["detail"]


def test_422_on_sample_assignment_failure():
    body = payload()
    body["sample"]["is_apex_idx_2"] = 5  # 样本内标下标越界
    r = client.post("/v1/blank-subtraction", json=body)
    assert r.status_code == 422
    assert r.json()["detail"].startswith("sample: ")
    assert "out of range" in r.json()["detail"]


def test_422_on_blank_assignment_failure():
    body = payload()
    body["blank"]["is_apex_idx_2"] = 0  # 空白两枚内标相同
    r = client.post("/v1/blank-subtraction", json=body)
    assert r.status_code == 422
    assert r.json()["detail"].startswith("blank: ")
    assert "internal standards must differ" in r.json()["detail"]


def test_422_on_shared_input_errors():
    # 共用参考峰表/内标错误在处理样本时即被发现并指出组别
    body = payload(is_ref_id_1="nope")
    r = client.post("/v1/blank-subtraction", json=body)
    assert r.status_code == 422
    assert r.json()["detail"].startswith("sample: ")
    body = payload(tolerance_ms=-1)
    r = client.post("/v1/blank-subtraction", json=body)
    assert r.status_code == 422


def test_422_on_wrong_types():
    body = payload()
    body["sample"]["samples"][1]["intensity"] = True  # 布尔不能冒充整数
    r = client.post("/v1/blank-subtraction", json=body)
    assert r.status_code == 422
    body = payload()
    body["blank"]["samples"][1]["time_ms"] = 5.5  # 浮点不能被截断为整数
    r = client.post("/v1/blank-subtraction", json=body)
    assert r.status_code == 422
    body = payload()
    del body["blank"]  # 缺字段
    r = client.post("/v1/blank-subtraction", json=body)
    assert r.status_code == 422


def test_corrected_peaks_chain_into_assign_and_standard_addition():
    """校正峰数组直接作为定量接口的 measured_peaks，其时间序列作为归属
    接口的 measured_times_ms。"""
    r = client.post("/v1/blank-subtraction", json=payload())
    assert r.status_code == 200, r.text
    peaks = r.json()["peaks"]  # 峰顶时间 3/7，校正面积 3/8

    # —— 衔接归属接口 ——
    r = client.post(
        "/v1/assign",
        json={
            "reference_peaks": REFS_2,
            "measured_times_ms": [p["time_ms"] for p in peaks],
            "is_ref_id_1": "A",
            "is_meas_idx_1": 0,
            "is_ref_id_2": "B",
            "is_meas_idx_2": 1,
            "tolerance_ms": 0,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["assignments"] == [0, 1]

    # —— 衔接定量接口：A 作目标峰、B 作归一化内标，另设一枚参考内标 ——
    refs = [
        {"id": "T", "time_ms": 3},
        {"id": "S1", "time_ms": 7},
        {"id": "S2", "time_ms": 20},
    ]
    injections = []
    for conc in (0, 5, 10):
        measured = [dict(p) for p in peaks]
        measured.append({"time_ms": 20, "area": 50})
        measured[0]["area"] += 16 * conc  # 目标峰面积随加标浓度线性增长
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
    # 面积比 = (3 + 16c) / 8 = 2c + 3/8
    assert data["slope"] == {"n": 2, "d": 1}
    assert data["intercept"] == {"n": 3, "d": 8}
    assert data["sample_concentration"] == {"n": 3, "d": 16}
