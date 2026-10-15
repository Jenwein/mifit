# 11 血氧、体重与用户档案（M5，2026-10-14，APK 3.59.1 + 真机）

目的：M5。把血氧（日汇总与原始点）、体重、用户档案三类数据的 key、入口、形状和含义记下来，作为 `spo2_daily`、`spo2`、`weight` 三个域和 `MiFit.profile()` 的依据；并确认原始血氧是否有与原始心率相同的"首桶丢失"（notes/08）。

本文所有数值都是合成示例。真机结果只给条数、形状、关系和相对时钟偏移，不给日期、时刻、id 或任何健康/档案数值。真机为东八区账号，全部只读（编写 M5 计划时 10 次调用，间隔 ≥ 2 s，无 401）。

## 1. 血氧

### 1.1 APK 依据

- `FitnessPersistKey`：`Spo2 = "spo2"`（全天血氧）、`ManualSpo2 = "single_spo2"`（手动点测）、`AbnormalSpo2 = "low_spo2"`（低血氧事件）。另有 `ReportMaxSpo2 = "max_spo2"`、`ReportMinSpo2 = "min_spo2"`，未探测（§4）。
- 日汇总值类 `CloudSpo2Report`：`avg_spo2`、`max_spo2`、`min_spo2`、`lack_spo2_count`（int），`latest_spo2`（`Spo2Item{spo2, time}`），`spo2_total`、`spo2_time_total`（可空 int）。
- `CloudDailyHelper.convertSpo2` 从 `DailySpo2Report` 组装上传对象：max/min/avg、`lackTimes`、`latestSpoRecord`；两个 total 恒为 `null`。只在 `maxSpo2 > 0` 时上传。

### 1.2 形状

**日汇总 `spo2`**（`get_aggregated_fitness_data_by_time`，`tag=daily_report`）

- 外层与其他日汇总相同：`{tag:"daily_report", key:"spo2", time, value(JSON 字符串), update_time, watermark, source_sid_list, sid}`。`time` 是该本地日期的 UTC 零点（notes/06）。只有有血氧数据的日子才有行（本账号是最近几天）。
- `value` 解码后（真机每一行每个键都在）：

  ```json
  {"avg_spo2": 96, "max_spo2": 99, "min_spo2": 92, "lack_spo2_count": 0,
   "latest_spo2": {"spo2": 97, "time": 1760486400, "dbTime": 1760457600,
                   "dbKey": "spo2", "sid": "synthetic"}}
  ```

- `spo2_total`、`spo2_time_total` 一次都没出现。
- 每一行都满足 `min_spo2 ≤ avg_spo2 ≤ max_spo2`，都在 70–100 之间，像百分比。`lack_spo2_count` 一直是 `0`，含义（APK `lackTimes`）未核实。
- `latest_spo2.time − dbTime = +28800`：`time` 是"把本地钟点当 UTC 写"的平移时钟，与 `latest_hr` 相同（notes/08 §1.1），不是真实时刻。
- 日均是服务端自己的 `avg_spo2`，不是当天各点的简单平均（notes/04 §4：日均与点均不同）。SDK 不重算、不拟合。
- 进入 B 层（`Spo2DailySummary`）的只有 `avg_spo2`、`max_spo2`、`min_spo2`（`0`、缺失、负数、非数 → `None`）；`lack_spo2_count`、`latest_spo2` 和两个 total 只留在 `raw`。

**原始 `spo2`**（`get_fitness_data_by_time`）

- 外层 `{key, sid, time, update_time, value, zone_name, zone_offset}`；每一行外层 `time` = `value.time`（真实时刻）；`zone_offset` 是秒数（int），`zone_name` 是 IANA 时区名。
- `value` 解码后：`{"time": 1760400600, "spo2": 97}`。
- 开着全天监测时约每 10 分钟一个点；更早的历史只有零星几个点。
- 不带 `start_time` 的一次读取返回一页几十行、往回跨了几个月，带 `has_more=true`：页大小由服务端决定。

**原始 `single_spo2`**：`value` 为 `{"time", "spo2"}`，外层 `time` = `value.time`；一页 20 行，带 `has_more`；是零散的点测。

**原始 `low_spo2`**：本账号没有任何行，形状未见（§4）。

`single_spo2`、`low_spo2` 不进 B 层，用 `MiFit.raw` 原样读取。

### 1.3 首桶丢失：与原始心率完全相同

实验窗口 `[H, 该本地日结束]`，H = 某个本地整点（东八区上是偶数整点 UTC 小时）。比较全部离线完成，按外层 `time` 与 `value` 逐行比对。

| 读取 | 窗口内行数 |
|---|---|
| 不带起点的读取（基准） | 36 |
| 窗口读取，`start_time = H` | 27 |
| 窗口读取，`start_time = H − 86400` | 36 |

- `start_time = H` 缺的 9 行恰好是 [H, H+2h) 内的全部行；返回的第一行在 H+2h02m。
- `start_time = H − 86400` 在窗口内的 36 行与基准逐行相同（`time` 与 `value`），没有多出或改动的行。
- 结论：原始 `spo2` 与原始 `heart_rate` 一样按桶存放（本账号上看到 2 h、偶数整点 UTC 小时），桶键 ≤ `start_time` 的桶整组丢失（notes/08 §2.3）。修正办法相同：请求起点提前一天，再按外层 `time` 过滤。

## 2. 体重

- **key 的 APK 依据**：`FitnessPersistKey.Weight = "weight"`；`CrossYearSyncController.pullOneYearWeightIfNeed` → `pullAndSaveFitnessDataByTime(…, "weight", start, end, …)`，即与原始血氧同一个接口 `get_fitness_data_by_time`。值类 `WeightItem`，约 60 个字段（体重、BMI、体脂率/肌肉率/水分率/蛋白质率、分段脂肪/肌肉、身体得分……）。
- **真机行数**：账号上的记录为 1–10 条，最近 7 天没有；身体成分字段全为 0：是。不带起点的读取与一个起点远早于这些记录的窗口返回的行相同，`has_more=false`。
- **形状**：外层与原始血氧相同（`{key, sid, time, update_time, value, zone_name, zone_offset}`）；外层 `time` = `value.time`，不是零点。`value` 解码后（类型同真机）：

  ```json
  {"time": 1600000000, "weight": 70.2, "bmi": 22.9,
   "body_fat_rate": 0.0, "muscle_rate": 0.0, "moisture_rate": 0.0,
   "bone_mass": 0.0, "protein_rate": 0.0, "visceral_fat": 0.0,
   "basal_metabolism": 0}
  ```

- **kg 与 BMI 的交叉核对**：`weight` 四舍五入后等于用户档案的 `weight`（§3），所以单位是千克；`bmi` 等于 `weight / (档案身高 m)²`，精确到 0.1。
- **身体成分全 0**：所有身体成分字段都是 `0`（notes/04 §4 也是这样），一个都无法核对，全部只留在 `raw`，不进 B 层。

## 3. 用户档案

- **入口**：`GET https://hlth.io.mi.com/healthapp/user/get_miot_user_profile`。APK：`AccountService.getUserProfile()`（无参数）→ `BaseResult<UserInfoProfile>`，`HOST = "https://hlth.io.mi.com/"`，`PATH = "healthapp/"`。
- **请求**：没有 `data` 参数（查询串只有 `rc4_hash__`、`signature`、`_nonce`）。签名路径是 `/user/get_miot_user_profile`：去掉 `healthapp/` 前缀、保留前导 `/`，与 FDS `gen_download_url` 的规则相同（notes/04 §2(d)）。
- **响应**：与 `app/v1/` 相同的加密，解密后 `{code: 0, message, result}`。同一路径放到 `app/v1/` 下会失败（非 401 错误）。
- **`result`**（合成）：

  ```json
  {"id": "x1", "xiaomi_id": 1000001, "name": "synthetic", "sex": "male",
   "birth": "1990-01-01", "age": 35, "height": 175, "weight": 70,
   "initial_weight": {"weight": 70, "timestamp": 1600000000},
   "daily_step_goal": 8000, "daily_cal_goal": 300, "daily_sleep_goal": 480,
   "stand_goal": 0,
   "regular_goal_list": [{"field": 1, "target": 8000}, {"field": 2, "target": 300},
                         {"field": 4, "target": 30}],
   "record_max_hrm": {"hrm": 190, "source": "auto"}, "region": "CN",
   "personalization": null, "wear_medal": null}
  ```

- `height`、`weight` 真机是整数，尽管 APK 声明为浮点；`height` 在厘米的量级。
- `daily_step_goal`、`daily_cal_goal` 分别等于 `regular_goal_list` 里 `field` 1、2 的 `target`；`field` 4 的 `target` 是 app 的"目标活动时间"（分钟，2026-10-15 已对 app 核实），映射到 `activity_goal_min`。`daily_sleep_goal` 是 30 的倍数（像分钟，未核实）。`stand_goal` 是 `0`。
- **个人标识**：`name`、`birth`、`id`、`xiaomi_id`、`region` 是个人标识（`age`、`sex` 也是个人信息）。它们不进 B 层，也绝不能出现在仓库、日志或测试里。
- **写接口**：同一服务上还有 `user/set_miot_user_profile`、`user/remove_miot_user_profile`，都是写操作，SDK 永不调用。

## 4. 未决

- **血氧的桶大小**：只在一个东八区账号上看到 2 h、偶数整点 UTC 小时；其他账号、其他时区、其他设备未验证。与 notes/08 §4 相同，86400 s 的边距假设桶不超过 1 天。
- **`weight`、`single_spo2`、`low_spo2` 是否按桶**：都没测（`weight` 的记录太少，测不出；`low_spo2` 没有行）。`weight` 和 `spo2` 的域无论如何都走提前一天的读取；`MiFit.raw` 不做修正，保持服务端原样。
- **原始 `max_spo2` / `min_spo2` key**：APK 里有，未探测。
- **`low_spo2` 的形状**：本账号没有行，未见。
- **档案**：`daily_sleep_goal` 的单位，`regular_goal_list` 中 `field` 4 的含义。

## 5. SDK 落地

- 血氧：`spo2_daily`（`domains/spo2_daily.py`）按步数的日规则读日汇总，每个本地日一条 `Spo2DailySummary`（服务端的日均/最高/最低，`0` → `None`）；`spo2`（`domains/spo2.py`，`MiFit.spo2`）读原始全天血氧，每个点一条 `Spo2Point`（时刻取外层 `time`，`spo2` 不是正数的行跳过并加 note，解码值仍进 `raw`）。原始窗口读取抽成共享的 `domains/raw_window.py::fetch_raw_window`（起点提前 `START_MARGIN_S = 86400`，按外层 `time` 过滤，返回保留的行和无可用 `time` 的行数）；`heart_rate` 改为经它读取，请求、过滤和 note 与之前逐字相同，`spo2` 也经它读取。
- 体重：`weight`（`domains/weight.py`，`MiFit.weight`）经 `fetch_raw_window` 读 key `"weight"`（APK `FitnessPersistKey.Weight`），每条记录一个 `WeightRecord(epoch_s, weight_kg, bmi)`：时刻取外层 `time`，`weight` 不是正数的行跳过并加 note（解码值仍进 `raw`），`bmi` 不是正数则为 `None`。身体成分字段全部只留在 `raw`（本账号全为 `0`，无法核对，§2）。
- 档案：`MiFit.profile()` / `mifit profile [--json PATH]`（`domains/profile.py::fetch_profile`）。档案没有时间窗，不注册为数据域，`mifit export` 里没有它。`Transport.get_json` 新增 `base_path` 关键字（默认 `app/v1/`），`data_obj=None` 时不带 `data` 参数（查询串只有 `rc4_hash__`、`signature`、`_nonce`）；档案请求是 `get_json("user/get_miot_user_profile", None, sign_path="/user/get_miot_user_profile", base_path="healthapp/")`，地区前缀和 401 续期重试与其他请求相同。`result` 原样进 `raw`（含个人标识，按个人数据对待）；B 层 `UserProfile(height_cm, weight_kg, step_goal, calorie_goal_kcal, activity_goal_min)` 取 `height`、`weight`、`daily_step_goal`、`daily_cal_goal`，以及 `regular_goal_list` 里第一个 `field` 为 4 的项的 `target`（后面的项不看；列表不是列表或没有这一项则为 `None`），不是正数则为 `None`（不加 note）。睡眠/站立目标、`regular_goal_list` 的其余项（`field` 1、2 与步数、卡路里目标重复）、`initial_weight`、`record_max_hrm` 和个人标识只留在 `raw`。回答里没有 `result` 对象时没有记录、`raw` 为空，加一条 note。
