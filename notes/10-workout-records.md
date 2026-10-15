# 10 运动记录列表（sport records，M4 Task 1）

目的：M4。把"每次运动一行"的接口 `data/get_sport_records_by_time` 的请求、返回形状、时间窗规则、运动类型对照记下来，作为 `workouts` 域（`domains/workouts.py`）和 `Workout`（`models/workouts.py`）的依据。

本文所有数值都是合成示例。真机结果只给形状和关系，不给日期、条数或 `sid`。真机为东八区账号，全部只读（6 次调用）。

## 1. 接口与请求

- 端点：`data/get_sport_records_by_time`，GET，走和其他数据接口相同的签名/加密（`Transport.get_paged_result`）。
- APK 请求类：`persist.server.data.GetSportReportByTime`。线上字段名取自它的 `@SerializedName`：

  | 线上名 | 含义 |
  |---|---|
  | `category` | 可选，按 Mi 的运动类别过滤 |
  | `start_time`, `end_time` | Unix 秒 |
  | `reverse` | 新到旧 |
  | `next_key` | 可选，翻页游标 |
  | `limit` | 每页条数 |

- app 自己的拉取：`CloudSyncLineRouter.pullSportReportByTime` 用该类的三参构造，发出 `category=null`、`reverse=true`、`limit=50`（时间窗和 `next_key` 由调用方给）。SDK 照此发 `reverse=true`、`limit=50`，不带 `category`。
- 上游 Mi-Fitness-Sync 发的是 `startTime`/`endTime`，**不是**线上字段名（APK 是 `start_time`/`end_time`）；它随后在客户端按开始时间再过滤。它的文档还把心率区间字段写成 `hr_*_duration`，线上是 `hrm_*_duration`。这两点都不能照用；本项目没有移植它的活动客户端代码（计划 Decisions 13）。

## 2. 返回形状与字段

### 2.1 外层

`result` 信封由 `get_paged_result` 拆开：

```json
{"sport_records": [ ... ], "has_more": false, "next_key": ""}
```

- 行按新到旧排列。
- 外层行的键（真机每一行都有）：

  ```json
  {"sid": "synthetic", "key": "outdoor_running", "category": "running",
   "time": 1760436000, "value": "<JSON 字符串>",
   "zone_name": "Asia/Shanghai", "zone_offset": 28800,
   "update_time": 1760440000, "watermark": 1}
  ```

- `key` 和 `category` 都是 Mi 自己的标签，两者可以不同（如 `outdoor_running` 对 `running`）；SDK 不读它们，运动类型以 `value.sport_type` 为准（§4）。
- APK 的行类还声明了 `deleted` 和 `cheat_info`，真机一行都没出现。

### 2.2 `value`（解码后）

`value` 是 JSON 字符串，解码后是 `SportBasicReport`：真机每行 38–72 个键，没有嵌套对象。

- 每一行都有：`sport_type`、`proto_type`、`time`、`timezone`、`start_time`、`end_time`、`duration`、`calories`、`total_cal`、`avg_hrm`、`max_hrm`、`min_hrm`、`version`，外加各运动自己的额外键（心率区间时长 `hrm_*_duration`、训练效果/负荷、最大摄氧量、配速、速度、步频……）。
- `distance` 只在有距离的运动上有（跑步、骑行），`steps` 只在跑步上有；其他运动上是**缺键**，不是 `0`。
- 合成示例（一次跑步，只列部分键）：

  ```json
  {"sport_type": 1, "proto_type": 22, "time": 1760436000, "timezone": 32,
   "start_time": 1760436000, "end_time": 1760439001, "duration": 2700,
   "distance": 6000, "calories": 400, "total_cal": 470, "steps": 7000,
   "avg_hrm": 150, "max_hrm": 175, "min_hrm": 95, "avg_pace": 450,
   "hrm_aerobic_duration": 1500, "version": 6}
  ```

### 2.3 单位与已核实的关系（真机每一行都成立）

- 外层 `time` == `value.start_time`。`value.time` 是 `start_time` 或 `start_time − 1`，所以不用它。
- 外层 `zone_offset` == `value.timezone × 900`：`timezone` 按"刻钟"（15 分钟）计，东八区是 `32`。
- `end_time − start_time − duration`：普通一行是 1 s；有暂停的跑步是几分钟。
- `avg_pace`（秒/公里）和 `avg_speed`（公里/小时）都与 `distance / duration` 吻合。所以 `distance` 是**米**，`duration` 是**秒**，且是移动时间，暂停不计入。
- `total_cal − calories` 随经过时间增长，速率约等于静息代谢。所以 `calories` 是活动消耗（千卡），`total_cal` 在其上加了静息消耗。
- `min_hrm ≤ avg_hrm ≤ max_hrm`；`hrm_*_duration` 各区间之和约等于 `duration`。

### 2.4 APK 事实

- `SportBasicReportKt.isValid` 拒绝：`start_time ≥ end_time`、`start_time ≤ 0`、`duration ≤ 0` 或 `duration > 2678400`（31 天的秒数）。SDK 不照搬整条丢弃：每个数字单独判断，不是正数的变 `None`（`as_int` 之后要求 `> 0`），只有没有可用 `start_time` 的才丢出 `records`（仍留在 `raw`）。
- app 的 Health Connect 导出（`SportSyncBiz`）用 `Instant.ofEpochSecond(start_time/end_time)`、`Length.meters(distance)`（`distance ≤ 0` 时跳过），时区偏移取自 `timezone`（`getTzOffsetInSecBy15min`）。这印证了开始/结束是真实 Unix 秒、距离是米、`timezone` 是刻钟数。

## 3. 时间窗、翻页、category

### 3.1 时间窗

实验：用开始于 `S` 的一次运动做两个窗口。

| 窗口 | 结果 |
|---|---|
| `[S − 1, S]` | 返回该次运动 |
| `[S, S + 1]` | 空 |

结论：服务端按外层 `time`（运动的**开始时刻**）选行，规则是 `start_time < time <= end_time`，起点不含、终点含。这与日汇总相同（notes/06），不是"与窗口重叠"，也不是按结束时间选。

SDK 的做法（`fetch_workouts`）：

- 请求 `start_time = start − 1`，`end_time` 不变，使恰好开始于调用方起点的运动也被取到。
- 再按外层 `time` 过滤：`start <= time <= end` 才保留。边际一秒里的行、窗口外的行、`deleted` 为真的行都静默丢弃，不加 note。
- 外层 `time` 缺失或不是数的行：跳过并加 note `Skipped a workout row without a usable time.`，不进 `raw`。
- `value` 不是合法 JSON（或缺失）、或不是对象：跳过并加 note，不进 `raw`。
- `value` 里没有正数 `start_time`：不生成 `Workout`，加 note，但解码后的 `value` 仍进 `raw`。

### 3.2 翻页

- 用 `limit=2` 试：第一页 `has_more=true` 并带 `next_key`；带着这个 `next_key` 读第二页，与一次读完相比没有重叠、没有缺口。
- SDK 用 `limit=50`，最多读 20 页（`_MAX_PAGES`）。页数上限在服务端还有下一页时截断读取，此时最早的那些运动（`reverse=true`，新到旧）会缺，结果里带一条 note，建议缩短时间范围。`get_paged_result` 的 `truncated` 为真才加。

### 3.3 category

- `category: "running"` 只返回了那次跑步。
- SDK 暂不使用：`category` 是 Mi 自己的标签，不是中立词汇。

## 4. 运动类型

目前核实过的四个 `sport_type` 和它们的中立名：

| `sport_type` | APK `FitnessSportType` | 中立名（`Workout.sport`） | app 的 Health Connect 类型（`TypeConvert`） |
|---|---|---|---|
| 1 | `RUNNING_OUTDOOR` | `running` | 56 `RUNNING` |
| 6 | `BIKING_OUTDOOR` | `biking` | 8 `BIKING` |
| 300 | `CLIMBING_MACHINE` | `stair_climbing_machine` | 69 `STAIR_CLIMBING_MACHINE` |
| 308 | `STRENGTH_TRAINING` | `strength_training` | 70 `STRENGTH_TRAINING` |

- 中立名取 Health Connect 的运动类型词汇，小写蛇形。表只放核实过的类型，在 `domains/workouts.py` 的 `_SPORTS` 里，Mi 的编号只出现在那里（`models/` 里没有 Mi 字段名）。
- 没有对应项的类型：`sport` 为 `None`，`source_sport_code` 保留原编号，记录照常生成，没有 note。缺失或不是数的 `sport_type`（字符串、布尔）两者都是 `None`。
- 更多编号的权威来源：
  - `com.xiaomi.fit.data.common.data.annotation.FitnessSportType`：编号和名字。
  - `com.xiaomi.fitness.repo.healthconnect.biz.sport.TypeConvert`（`convertMiToHealthConnectType`）：app 自己的 Mi → Health Connect 对照。
- 增加一个类型：`_SPORTS` 里加一项，`test_maps_the_known_sport_types` 里加一行，并对 app 核一次（找一次该类型的运动，看 app 里显示的运动名是否与中立名对得上）。

## 5. B/A 划分与未决

### 5.1 进入 B 层（`Workout`）的字段

| `value` 键 | `Workout` 字段 | 处理 |
|---|---|---|
| `start_time` | `start_epoch_s` | 必须是正数，否则不生成记录 |
| `end_time` | `end_epoch_s` | 正数，否则 `None` |
| `timezone × 900` | `utc_offset_s` | `as_int`，`0`（UTC）保留 |
| `sport_type` | `sport`、`source_sport_code` | §4 |
| `duration` | `duration_s` | 正数，否则 `None` |
| `distance` | `distance_m` | 同上 |
| `calories` | `active_kcal` | 同上 |
| `total_cal` | `total_kcal` | 同上 |
| `avg_hrm` / `max_hrm` | `avg_hr_bpm` / `max_hr_bpm` | 同上 |
| `steps` | `steps` | 同上 |

"没有"一律是 `None`，不编 `0`：缺键、`0`、负数、布尔、字符串、列表、`NaN` 都变 `None`。

### 5.2 只留在 `raw`（A 层）

`min_hrm`、各 `hrm_*_duration`、配速/速度/步频/步幅/身高类字段、训练效果/负荷/最大摄氧量/恢复、`proto_type`、`version`、`value.time`，以及其余没进上表的键。原因：含义未核实（设计 §2 反目标）。

### 5.3 未决

- **`calories` 与 `total_cal`**：关系见 §2.3，但 app 的运动详情页显示的是哪一个，要在 Task 3 对真机核。核实前 `active_kcal` 只取 `calories`。
- **`duration` 与 `end − start`**：差值在有暂停时是几分钟，`duration` 是移动时间。`Workout` 两个都给，不互相推算。
- **区间时长**：各 `hrm_*_duration` 之和约等于 `duration`，但区间阈值因用户而异，所以只留 A 层。
- **`deleted` 和 `cheat_info`**：APK 行类里有，真机从未见过。SDK 把真值的 `deleted` 当作已删除并忽略（按字段名推断，未见实例）；`cheat_info` 不处理。
- **每次运动的明细**（逐点心率、轨迹、分段）：不在这个接口里，走 FDS，属于 M6。
- **覆盖面**：只有一个东八区账号；运动类型只核实了 §4 的四种；其他时区的 `timezone`（含负值、非整小时）只有单元测试覆盖。
