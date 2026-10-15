# 卡路里字段定位（2026-10-14，真机）

目标：找出小米运动健康 app「卡路里」环显示值（真机实测 4 天）对应的云端字段。

## 结论（definitive）

**app 的卡路里环不是任何单一云端字段，而是 app 端合成的。** 云端只提供"原料"，最终数由 `HeatViewModel` 在设备上混合（云端活动消耗 + 手机侧步数卡路里 `getPhoneCalories` + 基础代谢 + 饮食）后展示。因此 SDK 读不到一个等于 app 环的字段——这与心率"窗口查询会漏点"是同一类（云端 ≠ app 端实时合成）。

## 查到的真实云端卡路里数据

1. **步数报告自带 `calories`**（`get_aggregated_fitness_data_by_time`, tag=`daily_report`, key=`steps` 的 value 里）：走路卡路里。← 我们现在 `StepRecord.calories` 读的就是这个。
2. **`calories` 日报**（同端点, key=`calories`）：value `{calories:N}`。
3. **每日消耗** `GET app/v1/diet/user_consumption`（GET，query 参数 `data`，@Secret pathPrefix=""，host hlth.io.mi.com），请求体 `HeatConsumptionNewRequest{time:int}`（当日 epoch 秒；非毫秒、非日期串，否则报 `invalid time`），响应 `HeatConsumptionData{basal:float, exercise:float, intake:float}`：
   - `basal` = 基础代谢 BMR（恒定，来自个人资料）
   - `exercise` = 活动消耗（云端值；= 上面 calories 日报那套，随 time 落在哪个本地日而变）
   - `intake` = 饮食摄入

三者与 app 环在任何时区错位下都对不上（有一天云端 `exercise` 几乎为 0，app 环却明显不为 0）。证明 app 用的是设备端合成/未同步的手机侧数据。

## 相关代码链（APK v3.59.1）
- `HeatViewModel.getDailyConsumption/getHeatConsumptionData(LocalDate)` → `WeightDataRequest.getHeatConsumptionData(HeatConsumptionNewRequest)` → `WeightApiService.getHeatConsumptionData(data)` `@kub("diet/user_consumption")`（kub=GET）。
- `WeightApiService` 注解：`@BaseUrl(host="https://hlth.io.mi.com/", path="app/v1/")`，`@Secret(pathPrefix="")`。
- 另有 `DailyConsumptionRequest{tag,key,...}` 走 `data/get_aggregated_fitness_data_by_time`，HeatViewModel 用 tag=`daily_report`/key=`calories`（smali 坐实）。
- 合成相关：`HeatViewModel.getPhoneCalories(...)`、`DailyConsumptionCard(dietSummaryData + dailyConsumptionData)`、`BasalMetabolismInfo`。

## 对 SDK 的建议
- 新增一个「消耗/卡路里」只读域，**忠实暴露云端字段**：`basal`(BMR)、`exercise`(活动消耗)、`intake`(摄入)（来自 `diet/user_consumption`）。
- `StepRecord.calories` 改名/加注为「步数报告自带的走路卡路里」，避免和 app 卡路里环混淆。
- 文档注明：app 环是设备端合成值，SDK 不保证逐值复现（同心率完整性那条）。
- 不建议去复刻 app 的端上合成公式（需要手机侧步数卡路里，脱离"读云端"的范围，性价比低）。
