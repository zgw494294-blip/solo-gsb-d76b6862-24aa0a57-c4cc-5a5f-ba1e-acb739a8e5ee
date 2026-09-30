# 离线色谱峰归属 / 标准加入法定量 / 峰面积积分 / 空白扣除 API

- **峰归属**：基于 **两对互异内标** 对实测色谱峰进行正斜率仿射时间校准，并将实测峰与参考峰做
  **保序、一对一** 的最优归属。
- **标准加入法定量**：对 3～12 次加标进样逐次复用上述归属定位目标峰，以第一枚内标面积归一化后，
  对 (加标浓度, 面积比) 做含截距精确最小二乘，给出斜率、截距与原样浓度。
- **峰面积积分**：对原始色谱强度采样按峰顶分段（相邻峰顶间取强度最低的内部点为共用分界），
  逐段扣端点连线基线、截非负后按实际时间间隔精确梯形积分，输出约分分数面积与
  统一放大的整数峰数组。
- **空白扣除**：对样本与空白两次运行分别积分并按峰顶时间归属，仅对归属**同一参考 id** 的峰
  用各自精确面积（整数面积 ÷ 各自 area_scale）相减、负值截零，输出按样本峰顶顺序的
  校正面积与统一放大的整数峰数组。

Python 3.12 + FastAPI，全部时间/偏差/拟合/面积计算使用 `fractions.Fraction` 精确有理数，
返回约分后的 `n/d`。

## 一条命令启动

```bash
docker compose up --build -d
```

- 本地端口：**http://localhost:8000**
- 峰归属：`POST http://localhost:8000/v1/assign`
- 标准加入法定量：`POST http://localhost:8000/v1/standard-addition`
- 峰面积积分：`POST http://localhost:8000/v1/integrate`
- 空白扣除：`POST http://localhost:8000/v1/blank-subtraction`
- 健康检查：`GET http://localhost:8000/health`
- 交互文档：`http://localhost:8000/docs`（Swagger UI）

停止：`docker compose down`

## `POST /v1/assign`（峰归属）

### 请求

`Content-Type: application/json`

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `reference_peaks` | 数组，长度 2～40 | 每项 `{id, time_ms}`；`id` 唯一，`time_ms` 整数且按时间**严格递增** |
| `measured_times_ms` | 整数数组，长度 2～60 | 实测峰时间（下标从 **0** 开始） |
| `is_ref_id_1` / `is_ref_id_2` | 字符串 | 两个互异的内标参考峰 id，必须存在于参考峰中 |
| `is_meas_idx_1` / `is_meas_idx_2` | 整数 | 两个互异内标的实测下标，范围 `[0, len(measured_times_ms)-1]` |
| `tolerance_ms` | 非负整数 | 校准后允许的最大绝对偏差 |

> 两对内标的书写顺序可以互换，服务端会自动归一化；但两对必须同向：
> 沿参考时间递增方向，实测下标也必须递增（不可交叉），且两个实测内标时间严格有序，
> 从而保证仿射斜率严格为正。

```bash
curl -s http://localhost:8000/v1/assign \
  -H 'Content-Type: application/json' \
  -d '{
    "reference_peaks": [
      {"id": "p0", "time_ms": 10},
      {"id": "p1", "time_ms": 22},
      {"id": "p2", "time_ms": 31},
      {"id": "p3", "time_ms": 34},
      {"id": "p4", "time_ms": 40},
      {"id": "p5", "time_ms": 55}
    ],
    "measured_times_ms": [3, 6, 14, 20, 22, 26, 36],
    "is_ref_id_1": "p0", "is_meas_idx_1": 1,
    "is_ref_id_2": "p4", "is_meas_idx_2": 5,
    "tolerance_ms": 0
  }'
```

### 响应

- `assignments`：与参考峰**同序**一一对应，值为配对的实测下标，未配对为 `null`。
- `unpaired_meas_indices`：未配对的实测下标，升序。
- `matches`：每个配对峰一条记录，`calibrated_time_ms` 为实测时间经校准后的值，
  `signed_deviation_ms = 校准值 − 参考值`；两者均为约分分数 `{"n": 分子, "d": 分母}`，
  分母恒为正（负值体现在分子上）。

上例的校准直线为 `校准值 = (3/2) × 实测值 + 1`：

```json
{
  "assignments": [1, 2, 3, 4, 5, 6],
  "unpaired_meas_indices": [0],
  "matches": [
    {"ref_id": "p0", "ref_index": 0, "meas_index": 1,
     "calibrated_time_ms": {"n": 10, "d": 1},
     "signed_deviation_ms": {"n": 0, "d": 1}},
    {"ref_id": "p1", "ref_index": 1, "meas_index": 2,
     "calibrated_time_ms": {"n": 22, "d": 1},
     "signed_deviation_ms": {"n": 0, "d": 1}},
    {"ref_id": "p2", "ref_index": 2, "meas_index": 3,
     "calibrated_time_ms": {"n": 31, "d": 1},
     "signed_deviation_ms": {"n": 0, "d": 1}},
    {"ref_id": "p3", "ref_index": 3, "meas_index": 4,
     "calibrated_time_ms": {"n": 34, "d": 1},
     "signed_deviation_ms": {"n": 0, "d": 1}},
    {"ref_id": "p4", "ref_index": 4, "meas_index": 5,
     "calibrated_time_ms": {"n": 40, "d": 1},
     "signed_deviation_ms": {"n": 0, "d": 1}},
    {"ref_id": "p5", "ref_index": 5, "meas_index": 6,
     "calibrated_time_ms": {"n": 55, "d": 1},
     "signed_deviation_ms": {"n": 0, "d": 1}}
  ]
}
```

非整数示例（参考 `[10,25,40]`、实测 `[6,15,26]`、容差 2）中，实测 15 校准为
`47/2`，对参考 25 的有符号偏差为 `-3/2`：

```json
{"n": 47, "d": 2}
{"n": -3, "d": 2}
```

### 归属规则

1. **内标强制配对**：两对互异内标始终配对，并据此确定仿射变换
   `t_cal = slope·t_meas + intercept`，其中 `slope > 0`。
2. **容差门限**：其余峰仅当 `|t_cal − t_ref| ≤ tolerance_ms` 时允许配对。
3. **一对一 + 保序**：每个参考峰最多配一个实测峰（反之亦然），
   配对链上参考下标与实测下标都严格递增；允许任意一侧的峰连续跳过。
4. **优化目标**（依次比较）：
   1. 配对数最多；
   2. 所有配对的绝对偏差之和最小；
   3. 仍并列时，取 `(参考下标, 实测下标)` 配对序列（按参考下标排序）字典序最小。

实现上，两对内标把问题切为三个互不重叠的独立区间，每个区间用 LCS 风格动态规划求解
（规模 ≤ 40×60，远低于毫秒级），最后合并强制配对。分数全程不转浮点，无精度损失。

### 错误处理（HTTP 422）

以下情况均返回 `422 Unprocessable Entity`，响应体为 `{"detail": "原因"}`：

- 参考峰 `id` 重复，或参考时间不是严格递增；
- 参考峰/实测峰数量超出 2～40 / 2～60 的范围；
- 内标参考 id 不存在，或两个 id 相同；
- 内标实测下标越界、相同；
- 两对内标顺序不一致（实测下标交叉），或两个实测内标时间不严格有序
  （导致斜率不为正或无定义）；
- `tolerance_ms` 为负；
- 类型错误（布尔不会被当作整数、浮点不会被截断为整数）或缺少必填字段。

## `POST /v1/standard-addition`（标准加入法定量）

对 3～12 次加标进样逐次复用 `/v1/assign` 的保序归属定位目标峰，以**第一枚内标**的面积
归一化目标面积，再对 `(加标浓度, 面积比)` 做含截距最小二乘：

```
area_ratio = slope × added_concentration + intercept
原样浓度 sample_concentration = intercept / slope   （x 轴截距绝对值）
```

全程使用 `fractions.Fraction` 精确求解闭式最小二乘，不引入浮点。

### 请求

`Content-Type: application/json`

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `reference_peaks` | 数组，长度 2～40 | 同 `/v1/assign`：`{id, time_ms}`，`id` 唯一、时间严格递增 |
| `target_ref_id` | 字符串 | 目标峰参考 id，必须存在且**不得**等于两枚内标 id |
| `is_ref_id_1` / `is_ref_id_2` | 字符串 | 两个互异内标参考峰 id（归一化使用**第一枚**的面积） |
| `tolerance_ms` | 非负整数 | 归属时允许的最大绝对偏差 |
| `injections` | 数组，长度 **3～12** | 每次进样一项，按进样（采集）顺序排列 |

`injections[]` 每项：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `added_concentration` | 非负整数 | 本次加标浓度；全部进样中**至少有两个不同值** |
| `measured_peaks` | 数组，长度 2～60 | 按采集顺序排列的实测峰 `{time_ms: 整数, area: 非负整数}` |
| `is_meas_idx_1` / `is_meas_idx_2` | 整数 | 两枚内标在本次 `measured_peaks` 中的下标（从 0 开始） |

```bash
curl -s http://localhost:8000/v1/standard-addition \
  -H 'Content-Type: application/json' \
  -d '{
    "reference_peaks": [
      {"id": "p0", "time_ms": 10},
      {"id": "p1", "time_ms": 20},
      {"id": "p2", "time_ms": 30},
      {"id": "p3", "time_ms": 40}
    ],
    "target_ref_id": "p1",
    "is_ref_id_1": "p2",
    "is_ref_id_2": "p3",
    "tolerance_ms": 0,
    "injections": [
      {"added_concentration": 0,
       "measured_peaks": [
         {"time_ms": 10, "area": 0},
         {"time_ms": 20, "area": 500},
         {"time_ms": 30, "area": 1000},
         {"time_ms": 40, "area": 800}],
       "is_meas_idx_1": 2, "is_meas_idx_2": 3},
      {"added_concentration": 10,
       "measured_peaks": [
         {"time_ms": 10, "area": 0},
         {"time_ms": 20, "area": 560},
         {"time_ms": 30, "area": 1000},
         {"time_ms": 40, "area": 800}],
       "is_meas_idx_1": 2, "is_meas_idx_2": 3},
      {"added_concentration": 20,
       "measured_peaks": [
         {"time_ms": 10, "area": 0},
         {"time_ms": 20, "area": 620},
         {"time_ms": 30, "area": 1000},
         {"time_ms": 40, "area": 800}],
       "is_meas_idx_1": 2, "is_meas_idx_2": 3},
      {"added_concentration": 50,
       "measured_peaks": [
         {"time_ms": 10, "area": 0},
         {"time_ms": 20, "area": 800},
         {"time_ms": 30, "area": 1000},
         {"time_ms": 40, "area": 800}],
       "is_meas_idx_1": 2, "is_meas_idx_2": 3}
    ]
  }'
```

本例面积比恰为 `area_ratio = (3/500) × concentration + 1/2`（残差全为 0），
原样浓度 `(1/2) / (3/500) = 250/3`：

```json
{
  "slope": {"n": 3, "d": 500},
  "intercept": {"n": 1, "d": 2},
  "sample_concentration": {"n": 250, "d": 3},
  "residuals": [
    {"added_concentration": 0,
     "area_ratio": {"n": 1, "d": 2},
     "predicted_area_ratio": {"n": 1, "d": 2},
     "residual": {"n": 0, "d": 1}},
    {"added_concentration": 10,
     "area_ratio": {"n": 14, "d": 25},
     "predicted_area_ratio": {"n": 14, "d": 25},
     "residual": {"n": 0, "d": 1}}
  ]
}
```

（上示响应省略了浓度 20、50 的两条残差，结构相同。）

### 响应字段

- `slope` / `intercept`：约分分数 `{"n", "d"}`；要求 **slope > 0** 且 **intercept ≥ 0**。
- `sample_concentration`：原样浓度 `intercept / slope`（约分分数）。
- `residuals`：与 `injections` **同序**，每项含 `added_concentration`、
  观测 `area_ratio`（目标面积 ÷ 第一枚内标面积）、`predicted_area_ratio` 拟合值、
  `residual = 观测 − 拟合`；均为约分分数，分母恒正。

### 422 规则

除继承 `/v1/assign` 的全部校验外（参考峰、内标、容差等；任何一次进样归属失败都会在
消息中指出 **进样下标**，形如 `injection 2: ...`），本接口还可能因下列原因返回 422：

- 目标峰 id 不存在，或目标峰就是内标之一；
- 某次进样的目标峰在容差内**未归属**（消息指出进样下标）；
- 某次进样**第一枚内标面积为 0**（无法归一化；消息指出进样下标）；
- 加标浓度少于两个不同值；
- 最小二乘斜率不为正（≤ 0），或截距为负；
- 进样数不在 3～12、单次实测峰数不在 2～60、负浓度/负面积，以及严格类型错误或缺字段。

## `POST /v1/integrate`（峰面积积分）

对原始色谱强度采样做逐段基线校正梯形积分：

1. **分段**：相邻峰顶之间的内部采样点中取强度最低者为**共用分界**（并列取最早下标）；
   积分起止下标为最外侧边界，每个峰顶独占一段，相邻段共用分界点。
2. **基线校正**：每段以两端采样点连线为基线，逐点将强度减基线后**截为非负**。
3. **精确积分**：按实际时间间隔做梯形积分，全程 `fractions.Fraction` 精确有理数。
4. **整数放大**：用全部面积分母的**最小公倍数**统一放大，得到整数面积峰数组
   （`峰面积 = 段面积 × area_scale`）。

### 请求

`Content-Type: application/json`

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `samples` | 数组，长度 **5～500** | 每项 `{time_ms: 整数, intensity: 非负整数}`；`time_ms` **严格递增** |
| `start_index` / `end_index` | 非负整数 | 积分起止采样点下标（含端点），须 `< len(samples)` 且 `start_index < end_index` |
| `apex_indices` | 整数数组，长度 **2～60** | **严格递增**的峰顶下标；须**严格**位于起止之间，且相邻峰顶至少隔一个内部采样点 |

```bash
curl -s http://localhost:8000/v1/integrate \
  -H 'Content-Type: application/json' \
  -d '{
    "samples": [
      {"time_ms": 0,   "intensity": 2},
      {"time_ms": 10,  "intensity": 5},
      {"time_ms": 20,  "intensity": 9},
      {"time_ms": 30,  "intensity": 5},
      {"time_ms": 40,  "intensity": 3},
      {"time_ms": 50,  "intensity": 4},
      {"time_ms": 60,  "intensity": 8},
      {"time_ms": 70,  "intensity": 2},
      {"time_ms": 80,  "intensity": 5},
      {"time_ms": 90,  "intensity": 3},
      {"time_ms": 100, "intensity": 2}
    ],
    "start_index": 0,
    "end_index": 10,
    "apex_indices": [2, 6, 8]
  }'
```

峰顶 2 与 6 之间的内部点 3、4、5 强度为 5、3、4，最低点 4 为共用分界；
峰顶 6 与 8 之间唯一内部点 7 为第二处分界。三段 `[0,4]`、`[4,7]`、`[7,10]`
的面积约分后均为整数，故放大倍数为 1：

```json
{
  "segments": [
    {"start_index": 0, "end_index": 4, "area": {"n": 115, "d": 1}},
    {"start_index": 4, "end_index": 7, "area": {"n": 70, "d": 1}},
    {"start_index": 7, "end_index": 10, "area": {"n": 40, "d": 1}}
  ],
  "area_scale": 1,
  "peaks": [
    {"time_ms": 20, "area": 115},
    {"time_ms": 60, "area": 70},
    {"time_ms": 80, "area": 40}
  ]
}
```

非整数面积示例：采样 `(0,1),(3,4),(4,2),(7,5),(10,0)`、起止 `0,4`、峰顶 `[1,3]`，
两段面积分别为 `9/2` 与 `12`，分母最小公倍数为 2：

```json
{
  "segments": [
    {"start_index": 0, "end_index": 2, "area": {"n": 9, "d": 2}},
    {"start_index": 2, "end_index": 4, "area": {"n": 12, "d": 1}}
  ],
  "area_scale": 2,
  "peaks": [
    {"time_ms": 3, "area": 9},
    {"time_ms": 7, "area": 24}
  ]
}
```

### 响应字段

- `segments`：与 `apex_indices` **同序**，每峰一段；`start_index` / `end_index` 为段边界的
  采样点下标（相邻段共用分界点），`area` 为约分分数 `{"n", "d"}`，分母恒正。
- `area_scale`：全部面积分母的最小公倍数（整数，≥ 1）。
- `peaks`：按**峰顶时间**排序的 `{time_ms, area}` 整数峰数组，
  `area = 段面积 × area_scale`；**零面积的峰保留**在数组中。

### 与既有接口衔接

`peaks` 的形状与 `/v1/standard-addition` 的 `measured_peaks` 完全一致，
其 `time_ms` 序列可直接作为 `/v1/assign` 的 `measured_times_ms`。
以下衔接示例假设已把上面的积分响应保存在 shell 变量 `RESP` 中
（`RESP=$(curl -s ... /v1/integrate ...)`），并依赖 `jq` 组装请求体：

```bash
# 积分 -> 峰归属：峰顶时间序列作为 measured_times_ms
echo "$RESP" | jq '{
  reference_peaks: [{id:"T",time_ms:20},{id:"S1",time_ms:60},{id:"S2",time_ms:80}],
  measured_times_ms: [.peaks[].time_ms],
  is_ref_id_1: "S1", is_meas_idx_1: 1,
  is_ref_id_2: "S2", is_meas_idx_2: 2,
  tolerance_ms: 0
}' | curl -s http://localhost:8000/v1/assign \
  -H 'Content-Type: application/json' -d @-
# => {"assignments":[0,1,2], ...}

# 积分 -> 标准加入法定量：峰数组作为各次进样的 measured_peaks
# （演示用：令目标峰面积随加标浓度线性 +100/浓度；实际使用中应对每次
#   加标进样分别调用 /v1/integrate 取各自的 peaks）
echo "$RESP" | jq '. as $r | {
  reference_peaks: [{id:"T",time_ms:20},{id:"S1",time_ms:60},{id:"S2",time_ms:80}],
  target_ref_id: "T",
  is_ref_id_1: "S1", is_ref_id_2: "S2",
  tolerance_ms: 0,
  injections: [0, 5, 10] | map(. as $c | {
    added_concentration: $c,
    measured_peaks: ($r.peaks | .[0].area += 100 * $c),
    is_meas_idx_1: 1, is_meas_idx_2: 2
  })
}' | curl -s http://localhost:8000/v1/standard-addition \
  -H 'Content-Type: application/json' -d @-
# => {"slope":{"n":10,"d":7},"intercept":{"n":23,"d":14},
#     "sample_concentration":{"n":23,"d":20}, ...}
```

端到端可运行版本见 `tests/test_integration_api.py` 的衔接测试。

### 错误处理（HTTP 422）

以下情况均返回 `422 Unprocessable Entity`，响应体为 `{"detail": "原因"}`：

- 采样点数为 5～500 之外、`time_ms` 非严格递增、`intensity` 为负；
- 起止下标越界（`[0, len(samples)-1]`），或 `start_index >= end_index`；
- 峰顶数量在 2～60 之外、非严格递增、未严格位于起止之间，
  或相邻峰顶之间没有内部采样点；
- 类型错误（布尔不会被当作整数、浮点不会被截断为整数）或缺少必填字段。

## `POST /v1/blank-subtraction`（空白扣除）

对**样本**与**空白**两次色谱运行做空白扣除：

1. **分别积分**：两组各自复用 `/v1/integrate` 的积分流程（输入形状相同）；
   两组的采样时间与面积放大倍数 `area_scale` 可以不同。
2. **分别归属**：两组各自按峰顶时间复用 `/v1/assign` 的保序归属，
   内标以其在 `apex_indices`（即积分输出峰列表）中的下标给出。
3. **精确扣除**：仅当样本峰与空白峰归属**同一参考 id** 时，
   用各自整数面积除以各自 `area_scale` 后的精确值相减，**负值截为零**；
   空白没有该参考峰则不扣，样本未归属峰保持原面积，空白独有峰不输出。
4. **整数放大**：取全部校正面积分母的**最小公倍数**作为 `area_scale`，
   生成 `{time_ms, area}` 整数峰数组（零面积保留），可直接供定量接口使用。

### 请求

`Content-Type: application/json`

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `reference_peaks` | 数组，长度 2～40 | 两组**共用**的参考峰表，同 `/v1/assign` |
| `is_ref_id_1` / `is_ref_id_2` | 字符串 | 两个互异的内标参考峰 id（两组共用） |
| `tolerance_ms` | 非负整数 | 归属时允许的最大绝对偏差 |
| `sample` / `blank` | 对象 | 样本 / 空白运行的积分输入与内标下标（见下） |

`sample` / `blank` 每项：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `samples` | 数组，长度 **5～500** | 同 `/v1/integrate`：`{time_ms, intensity}`，时间严格递增 |
| `start_index` / `end_index` | 非负整数 | 积分起止采样点下标（含端点），`start_index < end_index` |
| `apex_indices` | 整数数组，长度 **2～60** | 严格递增，严格位于起止之间，相邻至少隔一个内部采样点 |
| `is_apex_idx_1` / `is_apex_idx_2` | 整数 | 两枚互异内标在 `apex_indices` 中的下标（从 0 开始） |

```bash
curl -s http://localhost:8000/v1/blank-subtraction \
  -H 'Content-Type: application/json' \
  -d '{
    "reference_peaks": [
      {"id": "A", "time_ms": 3},
      {"id": "B", "time_ms": 7}
    ],
    "is_ref_id_1": "A", "is_ref_id_2": "B",
    "tolerance_ms": 0,
    "sample": {
      "samples": [
        {"time_ms": 0,  "intensity": 1},
        {"time_ms": 3,  "intensity": 4},
        {"time_ms": 4,  "intensity": 2},
        {"time_ms": 7,  "intensity": 5},
        {"time_ms": 10, "intensity": 0}
      ],
      "start_index": 0, "end_index": 4,
      "apex_indices": [1, 3],
      "is_apex_idx_1": 0, "is_apex_idx_2": 1
    },
    "blank": {
      "samples": [
        {"time_ms": 0,  "intensity": 1},
        {"time_ms": 5,  "intensity": 2},
        {"time_ms": 6,  "intensity": 1},
        {"time_ms": 9,  "intensity": 3},
        {"time_ms": 14, "intensity": 1}
      ],
      "start_index": 0, "end_index": 4,
      "apex_indices": [1, 3],
      "is_apex_idx_1": 0, "is_apex_idx_2": 1
    }
  }'
```

样本两段面积为 `9/2`、`12`（`area_scale` 2，整数面积 9、24）；空白采样时间不同，
两段面积为 `3`、`8`（`area_scale` 1，整数面积 3、8），其峰顶 5、9 经内标仿射校准后
恰为参考时间 3、7。两组峰分别归属参考 `A`、`B`，精确扣除 `9/2 − 3 = 3/2`、
`12 − 8 = 4`，分母最小公倍数为 2：

```json
{
  "sample_peaks": [
    {"ref_id": "A", "corrected_area": {"n": 3, "d": 2}},
    {"ref_id": "B", "corrected_area": {"n": 4, "d": 1}}
  ],
  "area_scale": 2,
  "peaks": [
    {"time_ms": 3, "area": 3},
    {"time_ms": 7, "area": 8}
  ]
}
```

### 响应字段

- `sample_peaks`：与**样本峰顶同序**，每项含归属的参考 `ref_id`（未归属为 `null`）
  与约分校正面积 `corrected_area`（`{"n", "d"}`，分母恒正）。
- `area_scale`：全部校正面积分母的最小公倍数（整数，≥ 1）。
- `peaks`：按**样本峰顶时间**排序的 `{time_ms, area}` 整数峰数组，
  `area = 校正面积 × area_scale`；**零面积的峰保留**在数组中。

### 与定量接口衔接

`peaks` 的形状与 `/v1/standard-addition` 的 `measured_peaks` 完全一致，
其 `time_ms` 序列可直接作为 `/v1/assign` 的 `measured_times_ms`。
以下衔接示例假设已把上面的空白扣除响应保存在 shell 变量 `RESP` 中
（`RESP=$(curl -s ... /v1/blank-subtraction ...)`），并依赖 `jq` 组装请求体：

```bash
# 空白扣除 -> 峰归属：样本峰顶时间序列作为 measured_times_ms
echo "$RESP" | jq '{
  reference_peaks: [{id:"A",time_ms:3},{id:"B",time_ms:7}],
  measured_times_ms: [.peaks[].time_ms],
  is_ref_id_1: "A", is_meas_idx_1: 0,
  is_ref_id_2: "B", is_meas_idx_2: 1,
  tolerance_ms: 0
}' | curl -s http://localhost:8000/v1/assign \
  -H 'Content-Type: application/json' -d @-
# => {"assignments":[0,1], ...}

# 空白扣除 -> 标准加入法定量：校正峰数组作为各次进样的 measured_peaks
# （演示用：追加一枚 20ms 内标峰，并令目标峰面积随加标浓度线性 +16/浓度；
#   实际使用中应对每次加标进样分别调用 /v1/blank-subtraction 取各自的 peaks）
echo "$RESP" | jq '. as $r | {
  reference_peaks: [{id:"T",time_ms:3},{id:"S1",time_ms:7},{id:"S2",time_ms:20}],
  target_ref_id: "T",
  is_ref_id_1: "S1", is_ref_id_2: "S2",
  tolerance_ms: 0,
  injections: [0, 5, 10] | map(. as $c | {
    added_concentration: $c,
    measured_peaks: ($r.peaks + [{time_ms: 20, area: 50}] | .[0].area += 16 * $c),
    is_meas_idx_1: 1, is_meas_idx_2: 2
  })
}' | curl -s http://localhost:8000/v1/standard-addition \
  -H 'Content-Type: application/json' -d @-
# => {"slope":{"n":2,"d":1},"intercept":{"n":3,"d":8},
#     "sample_concentration":{"n":3,"d":16}, ...}
```

端到端可运行版本见 `tests/test_blank_subtraction_api.py` 的衔接测试。

### 错误处理（HTTP 422）

以下情况均返回 `422 Unprocessable Entity`，响应体为 `{"detail": "原因"}`：

- 任一组的积分或归属失败：消息以 **`sample: `** 或 **`blank: `** 前缀指出
  出错的组别，正文同 `/v1/integrate` 与 `/v1/assign` 的校验规则
  （如 `blank: apex indices must be strictly increasing`）；
- 共用参考峰表/内标/容差不合法（在处理样本组时被发现，前缀为 `sample: `）；
- 类型错误（布尔不会被当作整数、浮点不会被截断为整数）或缺少必填字段。

## 本地开发与测试

不用 Docker 时：

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 测试（另需 pytest、httpx）
pip install pytest httpx
pytest -q
```

测试包含固定边界用例，以及 400 组随机实例与全局暴力枚举（枚举所有保序配对链）对拍，
验证“最大配对数 → 最小绝对偏差和 → 字典序最小”三级目标；积分接口另有 300 组随机实例
与独立浮点参照实现对拍，并含积分 → 归属 / 定量的端到端衔接测试。

## 目录结构

```
app/
  main.py               # FastAPI 应用、路由、422 异常映射
  models.py             # 严格类型的请求/响应模型
  assignment.py         # 仿射校准 + 区间 DP 归属（纯函数）
  standard_addition.py  # 标准加入法：逐次归属 + 精确最小二乘（纯函数）
  integration.py        # 峰面积积分：分段 + 扣基线 + 精确梯形积分（纯函数）
  blank_subtraction.py  # 空白扣除：双运行积分 + 归属 + 精确相减（纯函数）
tests/                  # API 测试与算法对拍测试
Dockerfile
docker-compose.yml
requirements.txt
```
