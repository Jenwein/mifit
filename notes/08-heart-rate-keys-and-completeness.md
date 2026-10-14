# 08 心率相关 key 与原始心率窗口完整性（M3 Task 4）

目的：M3。一是把心率相关的几个 key 的形状和含义记下来（日汇总 `heart_rate_daily` 的依据）；二是查清"原始心率按时间窗查会漏点"（notes/04 §4）的原因并在 SDK 里修掉。

本文所有数值都是合成示例。真机结果只给条数和相对时钟偏移（相对窗口起点/终点或本地零点），不给日期、时刻或 bpm。真机为东八区账号，全部只读。

## 1. 心率相关 key 与形状

### 1.1 日汇总 `heart_rate`（`get_aggregated_fitness_data_by_time`，`tag=daily_report`）

- 外层与其他日汇总相同：`{tag:"daily_report", key:"heart_rate", time, value(JSON 字符串), update_time, watermark, source_sid_list, sid}`。`time` 是该本地日期的 UTC 零点（notes/06）。每个有数据的日子一行；当天的行在当天内会被改写。
- `value` 解码后（APK 类 `CloudHrReport`；真机 6/6 天每个键都在）：

  ```json
  {"avg_hr": 70, "max_hr": 130, "min_hr": 50, "avg_rhr": 60,
   "abnormal_hr_count": 0,
   "warm_up_hr_zone_duration": 20, "fat_burning_hr_zone_duration": 3,
   "aerobic_hr_zone_duration": 0, "anaerobic_hr_zone_duration": 0,
   "extreme_hr_zone_duration": 0,
   "latest_hr": {"bpm": 75, "type": 0, "time": 1760486400,
                 "dbKey": "heart_rate", "dbTime": 1760457600, "sid": "synthetic"}}
  ```

- APK 含义（`CloudDailyHelper.convertHr` 组装上传对象，只在 `maxHr > 0` 时上传）：
  - `avg_hr`/`max_hr`/`min_hr` = `DailyHrReport` 的 avg/max/min；`avg_rhr` = `DailyHrReport.restHr`（可为空）。
  - `abnormal_hr_count` = 当天异常心率记录条数。
  - 五个 `*_hr_zone_duration` = `DailyHrDistribute` 的 warmUp/fatBurn/aerobic/anaerobic/extreme。APK 没写单位；真机值是每天合计几十的小整数，像分钟，未核实。
  - 类里还声明了 `hr_total`、`hr_time_total`，上传从不设置，真机也没出现。
  - `latest_hr`：东八区账号上 `time − dbTime = +28800`，即 `time` 是"把本地钟点当 UTC 写"，`dbTime` 才是真实时刻（notes/04 §4 的 ReportConvertor 平移）。`type` 有时缺失。
- 已对 app 核对一天：日均、范围、静息心率分别等于 `avg_hr`、`min_hr`/`max_hr`、`avg_rhr`。
- 进入 B 层（`HeartRateDailySummary`）的只有 `avg_hr`→`avg_bpm`、`max_hr`→`max_bpm`、`min_hr`→`min_bpm`、`avg_rhr`→`resting_bpm`；其余字段含义未核实，只留在 `raw`。

### 1.2 原始记录（`get_fitness_data_by_time`）

| key | APK 含义（`FitnessPersistKey`） | 外层 | `value` 解码后（合成） |
|---|---|---|---|
| `heart_rate` | 普通（全天）心率 | `{key, sid, time, value, …}`；外层 `time` = `value.time` | `{"time": 1760400000, "bpm": 72, "type": 0}` |
| `resting_heart_rate` | 静息心率 | `{key, sid, time, update_time, value, zone_name, zone_offset}`；`time` 是本地日期的 UTC 零点（同日汇总），旧行 `zone_offset` 为 `null` | `{"bpm": 60, "date_time": <同外层 time>}` |
| `single_heart_rate` | 手动（单次）测量 | 外层 `time` = `value.time`（真实时刻） | `{"bpm": 80, "time": 1760400000}`（无 `type`） |

- `heart_rate`：`type` 均为 `0`；间隔多为 10 分钟；没有两行同一 `time`。不带 `start_time` 的一次读取一页有 200 多行，页大小由服务端决定。本次基准读取的 476 行全部外层 `time` = `value.time`，无重复。
- `resting_heart_rate`：每个本地日至多一行，有的日子没有；6/6 天其 `bpm` 等于同日日汇总的 `avg_rhr`。
- `single_heart_rate`：成簇出现，一簇 10–15 个、相隔几秒，像一次点测；一页 20 行。
- 后两个 key 不进 B 层（含义与日汇总重复或未核实），`MiFit.raw` 可直接取。

## 2. 窗口漏点：现象、实验、结论

### 2.1 线索（编写 M3 计划时的 9 次只读调用）

- 窗口起点恰为某个偶数整点 UTC 小时 H：返回的第一行在 H+2h，[H, H+2h) 的行全部缺失，但不带起点的读取里都有。
- 同一窗口改 `start_time = H − 1 s`：全部都在。改 `start_time = H − 1 天`：落在 [H, end] 内的行与不带起点的读取完全一致。
- 终点：恰在 `end_time` 的行会返回，之后的不返回。

### 2.2 本次实验（10 次只读调用，间隔 ≥ 2 s，无 401）

取最近 3 个有数据的完整本地日 D1–D3。基准是不带起点的读取（`end_time = D3 次日本地零点 − 1`，`reverse=true`，按 `next_key` 手动翻页，2 页共 476 行，已越过 D1 零点）。比较全部离线完成，按外层 `time` 与 `value` 字符串逐行比对。

**Q3：旧窗口 `[本地零点(D), 本地零点(D+1) − 1]` 与加宽窗口 `[本地零点(D) − 86400, 同终点]`**

东八区的本地零点 = UTC 16:00，是偶数整点 UTC 小时。

| 日 | 基准当日行数 | 旧窗口 | 旧窗口缺的行 | 加宽窗口当日行数 | 加宽 = 基准 |
|---|---|---|---|---|---|
| D1 | 53 | 47 | 6 行，+00:00…+00:50（首个 2 h 内的全部行） | 53 | 是 |
| D2 | 77 | 77 | 0（首个 2 h 内本来没有数据） | 77 | 是 |
| D3 | 139 | 133 | 6 行，+01:00…+01:50（首个 2 h 内的全部行） | 139 | 是 |

（偏移相对该日本地零点。）

- D3 缺的行都**严格晚于** `start_time`，所以不是"逐行左开"，而是整组被丢。
- 旧窗口三天都没有多出的行，也没有 `value` 不同的行。
- 加宽窗口自己的边际日也照样丢首个 2 h：D2 的加宽窗口从 D1 零点开始，于是 D1 的那 6 行同样缺失。这些行在窗口外，会被过滤掉，不影响结果。

**Q1：起点落在桶中间**。H 取一个奇数整点 UTC 小时（本地零点 + 3 h），基准在 [H, H+2h) 内有 12 行。窗口 `[H+30min, H+6h]`：

- 基准在窗口内 33 行，返回 30 行；缺的 3 行在 H+30min、H+40min、H+50min；返回的第一行在 H+1h（偶数整点 UTC 小时）。
- 即：起点所在的偶数整点 2 h 桶 [H−1h, H+1h) 被整组丢掉，下一桶起完整。终点侧无多余、无缺失。

**Q2：终点落在桶中间**。E = 本地零点 + 14 h 30 min（偶数整点 UTC 小时 + 30 min），在一段连续数据中。窗口 `[E − 1 天, E]`：

- 基准在窗口内 116 行，返回 108 行；缺的 8 行全在起点侧（起点 +00:10…+01:20，即起点所在桶的剩余部分），返回的第一行在起点 +01:30（偶数整点 UTC 小时）。
- 终点侧：E 所在的桶里，基准在 E 及之前有 4 行（含恰在 E 的一行），全部返回；E 之后同一桶内基准还有 9 行，一行都没返回。→ 终点是逐行右闭，E 之前不缺行，**不需要**在终点侧加边距。

另：不带起点的读取两页的最早一行都正好落在偶数整点 UTC 小时上，像是按整桶分页（弱观察，未专门验证）。

### 2.3 结论

- 原始 `heart_rate` 按约 2 h 的桶存放，桶边界在偶数整点 UTC 小时（东八区上也是偶数整点本地小时，二者在本账号上分不开）。桶的键 = 桶的起点。
- 时间窗查询：桶键 `> start_time` 的桶才参与（左开，按桶）；桶内行再按 `time <= end_time` 逐行截断（右闭，按行）。
- 所以起点所在的桶（含起点恰为桶边界的情形）会整组丢失，包括其中晚于起点的行。SDK 的窗口从本地零点开始，在大多数时区是整点 UTC 小时，于是丢掉当天首桶。notes/04 §4 "同日分两段之和少于整日"也由此解释：第二段的起点丢了它所在的桶。
- 合成例：窗口 [00:00, 23:59:59]（本地），首桶 [00:00, 02:00) 里有 00:10、00:20、…、01:50 的样本。服务端返回从 02:00 开始，前面全部缺失；把起点提前一天再按 [00:00, 23:59:59] 过滤，就一行不缺。

## 3. SDK 处理

- `fetch_heart_rate_window`：请求 `start_time = start − _START_MARGIN_S`（`_START_MARGIN_S = 86400`，远大于 2 h 的桶），`end_time` 不变；只保留外层 `time` 满足 `start <= time <= end` 的行再解码。与 sleep 域原始读取的做法相同。
  - 边际日的行静默丢弃，不加 note。
  - 外层 `time` 缺失或不是整数的行跳过，加 note "Skipped a heart-rate row without a usable time."，也不进 `raw`。
- **闸门：G-pass。** 3 个比较日上，加宽窗口在当日内的行都与基准当日的行逐行相同（D1 53/53、D2 77/77、D3 139/139）。因此删掉了原先每个窗口结果都带的 "MAY BE INCOMPLETE" note；README 的已知限制改为说明 SDK 已自行修正。
- `fetch_heart_rate_full`（不带起点、从终点往回翻页，最多 10 页，被页数上限截断时有 note）不变，仍可用于往回翻全量。
- `MiFit.raw` 是服务端原样回答，**不**做此修正；其 docstring 已说明：桶式 key（如 `heart_rate`）会缺起点所在桶的行，需要全部行时自行把 `start` 提前再过滤。

## 4. 未决

- **桶的确切大小与对齐**：只在一个东八区账号、一台设备上看到 2 h、偶数整点。对齐的是 UTC 还是账号时区分不出来；本地零点不是整点 UTC 小时的时区（如 UTC+5:30）、其他设备或其他采样间隔下的桶未验证。86400 s 的边距假设桶不超过 1 天。
- **其他原始 key 是否同样按桶**：`resting_heart_rate`、`single_heart_rate`、`spo2` 等都没测。`MiFit.raw` 因此保持不过滤（只在 docstring 提醒）。sleep 域的原始读取本来就两侧各加一天再按会话匹配，不受影响。
- **app 自己不用时间窗拉原始数据**：`CloudSyncLineRouter.pullFullPullFitnessData(key, endTime, nextKey)` 发 `GetFitnessDataByTime(key, start=null, end=endTime, reverse=true, nextKey)`，即 SDK 的 `fetch_heart_rate_full`；按窗口的 `pullFitnessDataByTime(start, end, nextKey, key)` 使用类的默认 `reverse`。所以 app 很可能从未踩到这个问题。
- 3 个比较日里只有 2 天（D1、D3）的首桶有数据，真正触发了丢失；D2 只说明加宽与过滤不会多出或改动行。
- **带 `start_time` 的多页窗口读取：已核对（2026-10-14，真机，4 次只读调用，间隔 ≥ 2 s，无 401）。** 用 SDK 的 `fetch_heart_rate_window` 读 D1–D3 三天（同 §2.2 的三天；请求起点 = D1 本地零点 − 86400），服务端分 2 页返回（247 + 148 行，第 2 页带 `start_time` 和 `next_key`）。同时做不带起点的读取（同一 `end_time`，按 `next_key` 翻 2 页，247 + 229 行，已越过 D1 零点）。
  - 在 [D1 零点, D3 次日零点 − 1] 内两者都是 269 行（53 + 77 + 139，与 §2.2 相同），外层 `time` 集合相同，`value` 逐行相同，两边都无重复。
  - 两种读取的第 1 页逐行相同，分页边界在同一处（D1 零点 +14:00，偶数整点 UTC 小时）；窗口读取第 2 页最新一行在边界前 20 分钟，不带起点的读取在两者之间没有行，即翻页处不丢行。起点桶的丢失只发生在 `start_time` 处，落在边际日，被过滤掉。
