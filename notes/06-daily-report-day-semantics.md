# 日汇总（daily_report）的"日"语义（2026-10-14，APK 3.59.1 + 真机）

目的：M2a。睡眠、心率日汇总、血氧日汇总、步数都走 `data/get_aggregated_fitness_data_by_time`（`tag=daily_report`），先把"哪一天"彻底弄对，避免重演 consumption 的日期错位（见 verification/2026-10-14-consumption-live.md）。

## 结论

1. **`time` 是"浮动日期"**：每条日汇总的 `time` = 该**本地日期**对应的 **UTC 零点**（`timeInZero`），与用户时区无关。记录上**没有** `zone_name`/`zone_offset`。
   → 日期 = `time` 的 UTC 日历日期，对任何时区都成立。
2. **服务端时间窗是左开右闭**：返回 `start_time < time <= end_time` 的行（真机实测，见下）。
   → 取日期 D1…D2（含两端）：`start_time = D1 前一天的 UTC 零点`，`end_time = D2 的 UTC 零点`。与时区无关。
3. **app 自己同步时根本不用时间窗**：按 tag 拉全量、`next_key` 翻页，再在本地把 `time` 换算成本地零点存库、按本地日期展示。我们按窗口查只是为了少拉数据，日期语义以 1、2 为准。

## 证据（APK）

- 同步入口：`CloudSyncLineRouter.pullAggregatedDataByDuration(tag, nextKey)`（旧线）→ `new AggregateFitnessDataByTimeParam(tag, nextKey)` → `FitnessDataRequest.getAggregatedDataByTime`。
  - 两参构造等价于 `(tag, key=null, reverse=TRUE, limit=100, next_key, start_time=null, end_time=null)`：**不带 key、不带时间窗**。
  - 新线（`isNewWebLine()`）走 `HealthKitDataRequest.getAggregatedDataByDuration`，本项目未用。
- 上传：`CloudDailyHelper.convertSteps` → `getAggregateData(dailyStepReport.getTimeInZero(), …)`；`AggregateData.time = timeInZero`。
- 下载后换算：`ReportConvertor.convertReport(type, report, timeInZero)` → 各 `convertXxxReport` 都先 `FitnessDateUtils.changeToCurrentTzZeroTimeOneDay(timeInZero, 0)`：
  ```java
  Calendar calendar = getCalendar(0);          // GMT+0
  calendar.setTimeInMillis(originTime * 1000);
  Calendar calendar2 = Calendar.getInstance(); // 设备默认时区
  calendar2.set(Y, M, D, 0, 0, 0);
  return calendar2.getTimeInMillis() / 1000;   // 该日期的本地零点
  ```
  即：按 GMT+0 取年月日 → 换成设备时区该日零点。步数的 `stepRecords`/`activeStageList` 等日内时间随之整体平移（`+ 本地零点 - 原 time`）。
- 睡眠：`convertSleepReport` 同样先换日，再 `FitnessDataUtils.sleepSegmentToLocalTime(segments)` 处理段内时间（M2b 再看）。

## 证据（真机，只读，东八区账号）

- 日汇总行字段：`tag, key, time, value, update_time, watermark, source_sid_list, sid`；无时区字段。步数、睡眠的 `time` 都落在 UTC 零点（如 10-14 → `2026-10-14T00:00Z`）。
- app 式请求（只带 tag、limit 100）一页混合返回：`calories, heart_rate, steps, valid_stand, stress, intensity, sleep, spo2`。
- 窗口边界（key=steps）：

  | start_time, end_time | 返回日期 |
  |---|---|
  | 10-11T00:00Z, 10-13T00:00Z | 10-12, 10-13 |
  | 10-11T00:00:01Z, 10-12T23:59:59Z | 10-12 |
  | 10-12T00:00Z, 10-12T00:00Z | （空） |

  三组一致：`start_time` 不含、`end_time` 含。

## 对现有代码的影响

- steps 原先用本地日界作窗口：东八区恰好正确（`(D1-1)T16:00Z < D1T00:00Z`，`D2T00:00Z <= D2T15:59:59Z`）；西半球（如 UTC-5）会漏首日、多带一天。→ 改用结论 2 的窗口。
- 日期标签（UTC 日期）本来就对，不用改。
- `StepRecord.epoch_s` 原为服务端键（UTC 零点），`ConsumptionRecord.epoch_s` 为本地零点，两者含义不一。→ 统一为"该日期的本地零点"（与 app 存库一致）。
- 原始记录接口 `get_fitness_data_by_time` 的窗口边界是否也是左开右闭未验证（M3 心率完整性调查时一并看）。

## 落地（M2a，2026-10-14）

- `src/mifit/domains/days.py`：`local_day_span`、`local_midnight`、`report_window`（左开右闭窗口）、`report_day`（键 → 日期）。
- steps：按 `report_window(*local_day_span(...))` 查询；`StepRecord.epoch_s` 改为该日期的本地零点（与 `ConsumptionRecord` 一致）。consumption 复用 `local_day_span`/`local_midnight`，行为不变。
- 真机（东八区）：10-01…10-14 新窗口与旧窗口的"日期 → 步数"逐日一致（14 天，无 notes）。非东八区只有单元测试覆盖（东五区回归用例），没有真机账号可核。
