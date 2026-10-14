# 数据域目录（受控目录，M7 起持续维护）

- 一轮：2026-10-14 探测，2026-10-15 成文（APK 3.59.1 + 真机）。
- 性质：活文档。每探一个 key、每加一个 B 域就更新对应的行（§6）；不追求一次完备（设计 §6"全量"的操作性定义）。

## 0. 说明

### 0.1 用途

- 这是路线图 M7 的"受控目录"：每个 key ↔ 接口 ↔ value 结构 ↔ 本账号有没有数据 ↔ 核对到哪一步 ↔ 有没有 B 层。
- A 层不依赖本目录：任何 key 都能用 `MiFit.raw_window(key, …)`（原始）、`MiFit.daily_report(key, …, tag=…)`（汇总）或 `mifit raw <key> [--daily [--tag TAG]]` 读，SDK 不拿本目录校验 key。本目录记的是"已知有哪些 key、各是什么、核对到哪一步"。
- 进 B 层的规矩不变（设计 §2）：含义核对过才进。进了 B 的 key 在 `B 层` 一栏写域名。
- 守卫测试 `tests/test_domain_catalog.py` 解析本文的表格（§6）。

### 0.2 方法（一轮）

1. APK 3.59.1 的常量与值类：
   - `FitnessPersistKey`（`fit.fitness.export.data`）有 224 个字符串常量：172 个运动标签（§4）、51 个健康 key（§3）和 `passion_cheer`（像运动 / 加油标签，未探测）。`health_kit_sdk.restapi` 里有一份近似副本，多一个 `data_collector`，少几个运动标签。
   - `CloudKey.getCloudRequestKey`：app 首页的数据类型 → 汇总 key，共 17 个（§2）。
   - `CloudParamsKt`：三个 tag，`daily_report`、`daily_fitness`、`daily_mark`。
   - `CloudDailyHelper`：上传 10 类日汇总（`steps`、`calories`、`sleep`、`heart_rate`、`stress`、`spo2`、`intensity`、`valid_stand`、`blood_pressure`、`vo2_max`）。
   - 值类的线上字段名取自 `@SerializedName`。
2. 只带 tag 的汇总读取（不带 `key`，最近 7 个完整本地日，`limit` 100）：三个 tag 各一次，各一页，`has_more=false`。
3. `data/get_latest_fitness_data`，每批 10 个 key（`time` = 当时，`limit` = 1）：问了 51 个 key，即除已知的 `spo2` 外的全部健康 key，加 `data_collector`。
4. 对 latest 读取里有数据的新 key 做 7 天原始读取（提前一天请求，`reverse=true`，只看第一页）。
5. 首桶丢失核对（`stress`、`calories`），以及一个完整本地日上的一致性核对（只记布尔）。

### 0.3 账号与调用

- 一个东八区账号（中国区），全部只读。一轮共 37 次调用（预算 40），间隔 ≥ 2 s，没有 401：
  - 只带 tag 的汇总读取 3 次；
  - latest 读取 8 次，其中 2 次回 `code -8`（一次不带 `time`，一次 51 个 key 一起问）；
  - 7 天原始读取 16 次；
  - 首桶核对 4 次；
  - 一致性读取 6 次。
- `heart_rate`、`spo2`、`resting_heart_rate`、`single_heart_rate`、`single_spo2`、`sleep`、`weight` 的形状另见 notes/04、notes/08、notes/11（M3、M5 的探测）。

### 0.4 隐私

- 只记 key 名、接口名、JSON 字段名（与类型）、分桶的行数（0 / 1–10 / >10）和布尔。不记任何数值、日期、时刻或 id。
- 探测脚本和摘要在 `work/m7/`（git-ignored），里面也只有这些。
- 本目录不放示例值；要看合成示例，见 notes/04–12。

### 0.5 图例

- `接口`：`raw` = `data/get_fitness_data_by_time`；`daily_report`、`daily_fitness`、`daily_mark` = `data/get_aggregated_fitness_data_by_time` 的三个 tag。
- `APK 值类`：线上字段名所在的 APK 类。只有常量、没找到类的写 —。没有真机行的 key，类是按类名与常量名对上的，没追到解析代码，后面带"？"。
- `值字段（顶层）`：`value` 解码后的顶层字段名，必要时注 JSON 类型；字段后的 `?` 表示有的行缺这个字段。有真机行时写真机所见；没有时写"APK："加值类的字段名；都没有写 —。
- `本账号`（探测时）：
  - `7 天 >10`、`7 天 1–10`：最近 7 个完整本地日里有数据，行数分桶（原始 key 看 7 天读取的第一页；汇总 key 看只带 tag 的读取）；
  - `仅较早数据`：latest 读取有行，7 天读取 0 行；
  - `无`：原始 key 是 latest 读取没有行（没有数据，或服务端不认识这个 key，二者分不开）；汇总 key 是只带 tag 的 7 天读取里没有它的行（更早的没查）；
  - `未探测`：这一格本轮没测（缘由写在该节的说明里）。
- `已核对`：
  - `app 已核对`：与 app 显示的数字对过（`docs/superpowers/verification/`）；
  - `待真机核对`：已进 B 层，等收尾的 app 对照；
  - `内部一致（1 天）`：在一个完整本地日上与另一来源对上（只记布尔，见该节说明），还没对 app；
  - `—`：没有核对。
- `B 层`：进了 B 层的写数据域名（反引号）；`候选` = 含义有线索，等 app 对照；`—` = 只在 A 层。

## 1. 接口一览

| 接口 | 用途 | 状态 | SDK 入口 |
|---|---|---|---|
| `data/get_fitness_data_by_time` | 原始记录，按 key 读（§3） | 已用 | `MiFit.raw`、`MiFit.raw_window`（CLI `mifit raw`）、`heart_rate`、`spo2`、`weight`、`sleep`（原始部分） |
| `data/get_aggregated_fitness_data_by_time` | 汇总；tag `daily_report` / `daily_fitness` / `daily_mark`（§2） | 已用 | `MiFit.daily_report`（CLI `mifit raw --daily`）、`steps`、`heart_rate_daily`、`spo2_daily`、`sleep`、`stress_daily` |
| `data/get_sport_records_by_time` | 运动列表，每次运动一行（notes/10） | 已用 | `workouts` |
| `diet/user_consumption` | 每日卡路里：静息、活动、摄入 | 已用 | `consumption` |
| `healthapp/user/get_miot_user_profile` | 用户档案（notes/11 §3） | 已用 | `MiFit.profile` |
| `healthapp/service/gen_download_url` + FDS | 运动明细与 GPS 文件（notes/12） | 已用 | `workout_detail` |
| `data/get_latest_fitness_data` | 每个 key 最新的若干行；请求 `{params: [{key, limit, time}]}` | 已找到，未封装 | — |
| `*_by_watermark`、`get_stat_data_by_time`、医疗与项目数据 | APK `FitnessApiService` 的 `get*ByWM`、`getStatData*`、`getMedicalData*`、`getProjectData*` | 未探测 | — |

- `get_latest_fitness_data`：APK `CloudSyncLineRouter.pullLatestFitnessData` 发 `LatestFitnessDataParam{params: [LatestFitnessDataModel{key, limit, time}]}`。
  - 不带 `time` 回 `code -8`；51 个 key 一次问也回 `code -8`。
  - 每批 10 个 key、`time` = 当时、`limit` = 1 时回 `{data_list}`：每个有数据的 key 至多一行（最新的一行），外层与原始记录相同。
- 汇总接口：app 自己同步时只带 tag，不带 key 和时间窗（notes/06）；SDK 按 key 读，日窗口是 `report_window`（左开右闭）。

## 2. 汇总 key

| key | 接口 | APK 值类 | 值字段（顶层） | 本账号 | 已核对 | B 层 |
|---|---|---|---|---|---|---|
| `steps` | daily_report | `CloudStepReport` | `steps, distance, calories` | 7 天 1–10 | app 已核对 | `steps` |
| `heart_rate` | daily_report | `CloudHrReport` | `avg_hr, max_hr, min_hr, avg_rhr, abnormal_hr_count`，五个 `*_hr_zone_duration`，`latest_hr`（object） | 7 天 1–10 | 待真机核对 | `heart_rate_daily` |
| `sleep` | daily_report | `CloudSleepReport` | `total_duration, sleep_deep_duration, sleep_light_duration, sleep_awake_duration, sleep_score, sleep_stage, avg_hr, avg_spo2, max_hr?, min_hr?, day_sleep_evaluation, long_sleep_evaluation, segment_details`（list of object） | 7 天 1–10 | app 已核对 | `sleep` |
| `spo2` | daily_report | `CloudSpo2Report` | `avg_spo2, max_spo2, min_spo2, lack_spo2_count, latest_spo2`（object） | 7 天 1–10 | 待真机核对 | `spo2_daily` |
| `stress` | daily_report | `CloudStressReport` | `avg_stress, max_stress, min_stress`（int），`stress_scale`（object：`relax, mild, moderate, severe`） | 7 天 1–10 | 待真机核对 | `stress_daily` |
| `calories` | daily_report | `CloudCaloriesReport` | `calories`（int） | 7 天 1–10 | 内部一致（1 天） | 候选 |
| `intensity` | daily_report | `CloudHMStrengthReport` | `duration`（int） | 7 天 1–10 | 内部一致（1 天） | 候选 |
| `valid_stand` | daily_report | `CloudStandReport` | `count`（int） | 7 天 1–10 | 内部一致（1 天） | 候选 |
| `vo2_max` | daily_report | `CloudVo2MaxReport` | APK：`avg_vo2_max, max_vo2_max, min_vo2_max, …` | 无 | — | — |
| `blood_pressure` | daily_report | `CloudBloodPressureReport` | — | 无 | — | — |
| `energy` | daily_report | — | — | 无 | — | — |
| `pai` | daily_report | — | — | 无 | — | — |
| `blood_sugar` | daily_report | — | — | 无 | — | — |
| `headset` | daily_report | — | — | 无 | — | — |
| `weight` | daily_report | — | — | 无 | — | — |
| `menstruation` | daily_report | — | — | 无 | — | — |
| `goal` | daily_fitness | — | `date_time, timeInZero, goal_items`（list of object：`field, target_value, achieved_value`），`sidList?` | 7 天 1–10 | — | — |
| `*` | daily_mark | — | `has_data`（bool） | 7 天 >10 | — | — |

- **外层**：`{tag, key, time, value, update_time, watermark, source_sid_list, sid}`，`value` 是 JSON 字符串。`time` 恒为本地日期的 UTC 零点（notes/06）。`daily_fitness` 的行有时缺 `source_sid_list`，`daily_mark` 的行没有它。
- **哪些 key**：本账号 `daily_report` 回答的正是上面前 8 个（notes/06 的 app 式请求里也是这 8 个）。后 8 个来自 `CloudKey.getCloudRequestKey`，7 天里没有行：`vo2_max`、`blood_pressure` 有值类（`CloudDailyHelper` 也上传它们），其余 6 个没找到值类。
- **APK 里声明、真机没出现的字段**：`CloudStressReport` 的 `stress_total`、`stress_time_total`（只在 `maxStress > 0` 时上传）；`CloudCaloriesReport` 的 `goal`；`CloudHMStrengthReport` 的 `process`。
- **`daily_mark`**：每个本地日、每个 key 一行，`value` 只有 `has_data`，是"哪天哪个 key 有数据"的索引。本账号出现的 key：`steps`、`calories`、`heart_rate`、`intensity`、`sleep`、`spo2`、`stress`、`valid_stand`、`headset`。`headset` 在这里有行，`daily_report` 里却没有。
- **`goal`**：只在 `daily_fitness` 下；外层 `time` 也是 UTC 零点，等于 `value.date_time`。只在 A 层（设计 §2 反目标）：`MiFit.daily_report("goal", …, tag="daily_fitness")`。
- **单日一致性**（一个完整本地日，只记布尔）：
  - `stress`：`min_stress ≤ avg_stress ≤ max_stress`，都在 1–100；`max_stress`、`min_stress` 分别是当天原始 `stress` 点的最大、最小值，`avg_stress` 是原始点均值四舍五入（当天原始点完整，没有 ≤ 0 的点）；`stress_scale` 四档之和 ≤ 1440。这就是 `stress_daily` 的依据；`stress_scale`（各档时长，单位未核实）只在 A 层。
  - `valid_stand.count` = 当天有原始 `valid_stand` 区间的不同本地小时数（≠ 区间个数），在 0–24 内。
  - `intensity.duration` = 当天原始 `intensity` 的行数，在 0–1440 内。
  - `calories.calories` = `diet/user_consumption` 的 `exercise`（SDK 的 `consumption.active_kcal`）；≠ 同日 `steps` 日汇总的 `calories`，≠ `exercise + basal`。
- **候选**：`valid_stand`（站立小时数？）和 `intensity`（中高强度分钟数？）等 app 对照；`calories` 与 B 层已有的 `consumption.active_kcal` 相同，进 B 也不添新东西。

## 3. 原始 key

前 23 行是本账号有数据的 key，后面是没有数据的 28 个健康 key，最后是 `data_collector` 和 `passion_cheer`。

| key | 接口 | APK 值类 | 值字段（顶层） | 本账号 | 已核对 | B 层 |
|---|---|---|---|---|---|---|
| `heart_rate` | raw | `HrItem` | `time, bpm, type` | 7 天 >10 | app 已核对 | `heart_rate` |
| `spo2` | raw | `Spo2Item` | `time, spo2` | 7 天 >10 | 待真机核对 | `spo2` |
| `sleep` | raw | `SleepSegmentReport` | `bedtime, wake_up_time, duration, timezone, items?`（list of object），`sleep_deep_duration, sleep_light_duration, sleep_awake_duration, awake_count, device_bedtime, device_wake_up_time, is_uncomplete, protoTime, version` | 未探测 | app 已核对 | `sleep` |
| `weight` | raw | `WeightItem` | `time, weight, bmi`，身体成分字段（`body_fat_rate, muscle_rate, …`） | 仅较早数据 | 待真机核对 | `weight` |
| `steps` | raw | `StepItem` | `time, steps, distance, calories`（int 或 float） | 7 天 >10 | — | — |
| `resting_heart_rate` | raw | `RestHrItem` | `date_time, bpm` | 7 天 1–10 | — | — |
| `single_heart_rate` | raw | `HrItem` | `time, bpm` | 未探测 | — | — |
| `single_spo2` | raw | `Spo2Item` | `time, spo2` | 未探测 | — | — |
| `stress` | raw | `StressItem` | `time, stress` | 7 天 >10 | 内部一致（1 天） | 候选 |
| `single_stress` | raw | `StressItem` | `time, stress` | 7 天 1–10 | — | — |
| `calories` | raw | `CaloriesItem` | `time, calories`（int 或 float） | 7 天 >10 | — | — |
| `valid_stand` | raw | `ValidStandItem` | `start_time, end_time` | 7 天 >10 | 内部一致（1 天） | — |
| `intensity` | raw | `MHStrengthItem` | `time` | 7 天 >10 | 内部一致（1 天） | — |
| `headset` | raw | `HeadsetItem` | `start_time, end_time, high`（bool） | 7 天 >10 | — | — |
| `light_sensitivity_value` | raw | `LightValueItem` | `time, light_sensitivity_value` | 7 天 >10 | — | — |
| `body_momentum` | raw | `BodyMomentumItem` | `time, body_momentum` | 7 天 >10 | — | — |
| `vitality` | raw | `VitalityItem` | `date_time, daily_low_intensity_vitality, daily_medium_intensity_vitality, daily_high_intensity_vitality, latest_accumulated_vitality, suggested_activity_type, suggested_activity_duration` | 7 天 1–10 | — | — |
| `training_load` | raw | `TrainingLoadItem` | `date_time, current_day_train_load, current_train_load_level, wtl_sum` | 仅较早数据 | — | — |
| `vo2_max` | raw | `Vo2MaxItem` | `time, vo2_max, vo2_max_level` | 仅较早数据 | — | — |
| `dynamic` | raw | `ActiveStageItem` | `start_time, end_time, type, steps, distance, calories, rise_height` | 仅较早数据 | — | — |
| `high_heart_rate` | raw | `AbnormalRecordItem` | `start_time, end_time, threshold, items`（list of object：`time, value`） | 仅较早数据 | — | — |
| `grade_prediction` | raw | `GradePredictionItem` | `time, five_kilometre_grade_prediction_duration, ten_kilometre_grade_prediction_duration, half_marathon_grade_prediction_duration, full_marathon_grade_prediction_duration` | 仅较早数据 | — | — |
| `running_ability_index` | raw | `RunningIndicatorItem` | `time, running_ability_index, running_ability_level` | 仅较早数据 | — | — |
| `energy` | raw | `EnergyItem`？ | APK：`time, energy, state, state_value` | 无 | — | — |
| `high_stress` | raw | — | — | 无 | — | — |
| `pai` | raw | `OneDayPaiReport`？ | APK：`date_time, daily_pai, total_pai, low_zone_pai, medium_zone_pai, high_zone_pai` | 无 | — | — |
| `abnormal_heart_beat` | raw | `AbnormalFibItem`？ | APK：`start_time, end_time` | 无 | — | — |
| `abnormal_heart_rate` | raw | `AbnormalHrItem`？ | APK：`time, bpm, prev_bpm` | 无 | — | — |
| `low_heart_rate` | raw | — | — | 无 | — | — |
| `single_temperature` | raw | `TemperatureItem`？ | APK：`time, body_temperature, skin_temperature` | 无 | — | — |
| `temperature_characteristic` | raw | `TemperatureCharacterItem`？ | APK：`time` | 无 | — | — |
| `temperature_trend` | raw | `TemperatureTrendItem`？ | APK：`time, base_line, base_temp, diff_temp, status` | 无 | — | — |
| `physical_fitness_status` | raw | `PhysicalStatusItem`？ | APK：`date_time, physical_fitness_status, physical_fitness_level, fatigue_level, training_stress` | 无 | — | — |
| `lactate_threshold` | raw | `LactateThresholdItem`？ | APK：`time, heart_rate, pace, timeZone` | 无 | — | — |
| `noise` | raw | `NoiseItem`？ | APK：`time, decibel` | 无 | — | — |
| `goal` | raw | — | — | 无 | — | — |
| `blood_pressure` | raw | `BloodPressureItem`？ | APK：`time, systolic_pressure, diastolic_pressure, pulse, measurement_status` | 无 | — | — |
| `blood_sugar` | raw | `BloodSugarItem`？ | APK：`time, blood_sugar, measurement_period` | 无 | — | — |
| `single_blood_sugar` | raw | `BloodSugarItem`？ | APK：同 `blood_sugar` | 无 | — | — |
| `mc_blood_pressure` | raw | — | — | 无 | — | — |
| `mc_ecg` | raw | `EcgMedicalItemReport`？ | APK：`time, start_measure_time, measurement_duration, avg_hr, analysis_result, file_type, …` | 无 | — | — |
| `menstruation` | raw | `WomenHealthStatus`？ | APK：`date_time, status, update_time` | 无 | — | — |
| `menstrual_symptoms` | raw | `WomenHealthSymptoms`？ | APK：`date_time, hp, mood, pain` | 无 | — | — |
| `max_heart_rate` | raw | — | — | 无 | — | — |
| `min_heart_rate` | raw | — | — | 无 | — | — |
| `max_spo2` | raw | — | — | 无 | — | — |
| `min_spo2` | raw | — | — | 无 | — | — |
| `low_spo2` | raw | — | — | 无 | — | — |
| `watch_daytime_sleep` | raw | `DayNightSleepReport`？ | APK：`date_time, bedtime, wake_up_time, duration, timezone, items, total_score, …` | 无 | — | — |
| `watch_night_sleep` | raw | `DayNightSleepReport`？ | APK：同 `watch_daytime_sleep` | 无 | — | — |
| `unknown` | raw | — | — | 无 | — | — |
| `data_collector` | raw | `DataCollectorItem`？ | APK：`updateTime, sourceId, sourceName, sourceType, name, appInfo, deviceInfo, sourceDataType` | 无 | — | — |
| `passion_cheer` | raw | — | — | 未探测 | — | — |

- **外层**：`{key, sid, time, update_time, value, zone_name, zone_offset}`，`value` 是 JSON 字符串；回答是 `{data_list, has_more, next_key}`。`training_load` 的行（latest 读取）和部分 `vitality` 行（7 天读取）缺 `zone_offset`；旧的 `resting_heart_rate` 行 `zone_offset` 为 `null`（notes/08）。
- **外层 `time`**：
  - 多数 key 的外层 `time` = `value.time`，是真实时刻：`heart_rate`、`spo2`、`steps`、`stress`、`single_stress`、`calories`、`intensity`、`light_sensitivity_value`、`body_momentum`、`single_heart_rate`、`single_spo2`、`weight`、`vo2_max`、`grade_prediction`、`running_ability_index`。
  - `value` 里没有 `time` 的：`valid_stand`、`headset`、`dynamic`、`high_heart_rate`（区间 `start_time` / `end_time`），`sleep`（`bedtime` / `wake_up_time`）。
  - **日型原始 key**：`resting_heart_rate`、`vitality`、`training_load` 的外层 `time` 是本地日期的 UTC 零点，等于 `value.date_time`。`MiFit.raw_window` 按外层 `time` 原样过滤、不换算，所以在 UTC 以西的时区，这样的行会落进相邻本地日的窗口。
- **首桶丢失**（notes/08 §2.3：窗口起点所在的约 2 h 桶整组丢失）：
  - 已证实：`heart_rate`（notes/08）、`spo2`（notes/11 §1.3）、`stress`、`calories`（本轮：起点为本地零点、即偶数整点 UTC 小时的 4 h 窗口，普通读取首 2 h 为 0 行，提前一天的读取为 1–10 行，其余部分相同）。查过的 4 个密集 key 全都有。
  - 其余 key 未知。`MiFit.raw_window` / `mifit raw` 对所有原始 key 都提前一天请求，再按外层 `time` 过滤（只会多出随后被过滤掉的行）；`MiFit.raw` 是服务端原样的回答，不修正。
- **`本账号` 的出处**：`heart_rate`、`spo2`、`resting_heart_rate`、`weight` 取自 notes/08、notes/11（M3、M5 的探测；`resting_heart_rate` 每个本地日至多一行，`weight` 只有一条几年前的记录）；其余有数据的 key 取自本轮的 7 天读取。`single_heart_rate`、`single_spo2`、原始 `sleep` 在本轮 latest 读取里有行，但 7 天行数没测，所以标 `未探测`。
- **已有 B 层覆盖的**：`resting_heart_rate` 6/6 天等于同日 `heart_rate` 日汇总的 `avg_rhr`（notes/08），`heart_rate_daily` 的 `resting_bpm` 已取后者，不另建 B。
- **没有数据的 28 个健康 key 和 `data_collector`**：latest 读取没有行，分不出是没有数据还是服务端不认识这个 key。分组：
  - 压力、PAI、能量：`energy`、`high_stress`、`pai`；
  - 心律与房颤：`abnormal_heart_beat`、`abnormal_heart_rate`、`low_heart_rate`；
  - 体温：`single_temperature`、`temperature_characteristic`、`temperature_trend`；
  - 体能：`physical_fitness_status`、`lactate_threshold`；
  - 噪音与目标：`noise`、原始 `goal`；
  - 血压、血糖、心电：`blood_pressure`、`blood_sugar`、`single_blood_sugar`、`mc_blood_pressure`、`mc_ecg`；
  - 女性健康：`menstruation`、`menstrual_symptoms`；
  - 报告极值：`max_heart_rate`、`min_heart_rate`、`max_spo2`、`min_spo2`；
  - 其他：`data_collector`、`low_spo2`、`watch_daytime_sleep`、`watch_night_sleep`、`unknown`。
- **值类未定的**：`high_stress`、`low_heart_rate`、`low_spo2` 的常量名是 `AbnormalStress`、`AbnormalHrLow`、`AbnormalSpo2`，可能与 `high_heart_rate` 同为 `AbnormalRecordItem`；notes/04 把 `low_spo2` 记为 `Spo2Item`（mi-health-lab 的结论）。本账号都没有行，未定。`watch_daytime_sleep`、`watch_night_sleep` → `DayNightSleepReport` 见 notes/04。
- **其他细节**：
  - `steps`、`calories` 的 `calories` 有 int 也有 float。
  - `dynamic` 线上是 `type`（APK 也认 `dynamic_type`）。
  - latest 读取里的 `training_load` 没有 APK 声明的 `wtl_sum_optimal_min`、`wtl_sum_optimal_max`、`wtl_sum_overreaching`。
  - `weight` 的 `WeightItem` 约 60 个字段；本账号身体成分全为 0（notes/11 §2）。
  - `data_collector` 只在 `health_kit_sdk.restapi` 的副本里是常量；`passion_cheer` 像运动 / 加油标签（§4），没有探测。
- **候选**：原始 `stress` 点（1–100 的压力值；一天里与日汇总的最大、最小、均值对上）已能用 `MiFit.raw_window("stress", …)` 读，进 B 要等 app 对照。

## 4. 运动标签 key

`FitnessPersistKey` 里约 170 个常量（本版 172 个，如 `outdoor_running`、`badminton`）是运动标签：它们是运动记录（`data/get_sport_records_by_time`）外层的 `key`（notes/10 §2.1），不是健康数据的 key，本轮没有拿它们查过原始接口。SDK 不读这个外层 `key`，运动类型以 `value.sport_type` 为准（notes/10 §4）。完整列表就是 APK 的 `FitnessPersistKey`，本目录不逐个列出；`passion_cheer` 看名字也像这一类，未探测（§3）。

## 5. 未决与下一步

- **候选 key 的 app 对照**（M7 Task 5，收尾的真机会话）：在 app 里读几天的压力日均 / 最高 / 最低（核对 `stress_daily`）、站立小时数（`valid_stand.count`）、中高强度分钟数（`intensity.duration`）和活动卡路里（`calories`）。
  - 对上的标 `app 已核对`，以后一轮再考虑进 B。
  - `stress_daily` 哪个数对不上，就退出 B，只留在 `raw`。
- **29 个没有数据的 key**（§3）：可能是本账号的设备或功能没有产出（体温、血压、血糖、心电、女性健康、噪音……），也可能服务端不认识。换设备、开功能或换账号时再探；有了行，要补真机字段，并把值类后的"？"去掉。
- **只有较早数据的 key**：`training_load`、`vo2_max`、`dynamic`、`high_heart_rate`、`grade_prediction`、`running_ability_index` 最近 7 天没有行，`vitality` 只有零星几行，含义都没法对照；有新数据（如做过运动）后再看。
- **汇总 key**：`daily_report` 里 APK 的另外 8 个 key（`vo2_max`、`blood_pressure`、`energy`、`pai`、`blood_sugar`、`headset`、`weight`、`menstruation`）本账号 7 天没有行；`headset` 在 `daily_mark` 有行、在 `daily_report` 没有，原因未知。
- **`stress_scale` 的单位**：四档之和 ≤ 1440，像分钟，未核实，只在 A 层。
- **首桶丢失的范围**：只查了 4 个密集 key、一个东八区账号；其他 key、其他时区未验证（`raw_window` 对全部 key 都提前一天，不受影响）。
- **未探测的接口**：`*_by_watermark`、`get_stat_data_by_time`、医疗与项目数据，以及 APK `FitnessApiService` 里的 `getRelative*`、`getSportSummary`、`getBatchSportSummary`、`getLatestSportReport`、`getSportCategories`、`getCurWeekTrainingPlan` 等。
- **`get_latest_fitness_data` 值不值得封装**：枚举时最省（一次 10 个 key），但目前没有用户需求；三个 tag 的只带 tag 读取（app 自己的同步方式）也一样。有需求再做，做之前先定它返回什么。

## 6. 维护规则

- **加或复查一个 key**：一次探测至多 10 次调用，间隔 ≥ 2 s，遇 401 即停；只记名字、分桶（0 / 1–10 / >10）和布尔，脚本与输出放在 git-ignored 的 `work/` 下（照 `work/m7/` 的做法）。然后更新这个 key 的行，`本账号` 写探测时的状态。
- **新的 B 域**：在对应行的 `B 层` 写域名，`已核对` 写当前状态，核对后改成 `app 已核对`。新的接口在 §1 加一行。
- **表格**：列和取值集合固定（§0.5）；单元格里不写竖线，不放任何示例值。
- **守卫测试** `tests/test_domain_catalog.py`：
  - 每个已注册的数据域（`REGISTRY`）都要出现在某个表格行最后一格的反引号里；
  - §2、§3 表格 `B 层` 一格里的每个反引号名都是已注册的数据域（`profile` 除外：它不按时间窗读取，没有注册）；
  - 每个表格里，（第一格，第二格）不重复；
  - 全文不得出现 2020–2026 年的 Unix 秒时间戳。
