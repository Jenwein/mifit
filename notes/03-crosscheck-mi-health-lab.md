# 交叉核对：Codex 的 mi-health-lab 调查

来源：`C:\dev\mi-health-lab`（另一路并行调查，Codex 做的，自称 "Mi Health Lab"）。同一 APK（SHA-256 `46100649…`）、同一目标。
性质：对方的 `docs/` 是逆向结论（可参考）；`private/` 是用户本人健康数据（不碰、不复制）。下面只引用结构/字段层面的结论。

## 一、结论一致性：高度吻合（等于第三方独立印证）

对方独立得出的结论和我们（notes/01、02）**逐条一致**：
- 本地库路径需 root：真机上 `run-as` 被拒（app 非 debuggable）、databases 目录 `Permission denied`。→ 和我们"不 root 读不到"一致，且是在**用户真机（Xiaomi 15 / HyperOS / 未 root）上实测确认**的。
- Health Connect 权限未授予、小米 HealthKit app 未安装。→ 和我们排除路径 C 一致。
- 云端路径 B：`hlth.io.mi.com/app/v1/...`、sid `miothealth`、Cookie `serviceToken`+`cUserId`、签名用 `security`(ssecurity)、地区路由。→ 和我们 + kevinkwee **三方一致**。
- 签名算法逐字节一致：nonce(12 字节=8 随机+4 字节分钟桶,大端,base64) → `K=SHA256(b64dec(security)+b64dec(nonce))` → RC4(丢前 1024 字节、字段间连续状态) → SHA1 签名 → 响应用新 RC4 解密。→ **三方一致**，签名这块可以当定论了。
- 两边都**卡在同一处**：还没有从电脑成功发起过一次独立云端调用，都需要"真实登录拿到会话/token"这一步。正是我们已决定的下一步（你本地登录→给我 token）。

## 二、对方比我们多出来、且对我们有用的东西

### 1. 真机运行时确认：旧云端路径是"活的"
对方在真机的 app 日志里看到 `/app/v1/data/get_fitness_data_by_time` 实际在跑（30 秒内 320 条 cookie 获取事件），并有 `recordServerDataToDB` 落库、key 为 `steps`/`heart_rate`/`sleep`。
→ 意义：我们之前只有静态证据，现在**有运行时证据表明用户这台手机走的就是"旧云端路径（line 1）"**，也就是我们 SDK 要对接的 `/app/v1/data/...`。这给了 M0 冒烟验证很强的信心。

### 2. 有三条同步路径（我们之前只发现了旧路径）
`CloudSyncLineRouter` 按配置选路：
| 线 | 条件 | 接口 |
|---|---|---|
| 3 本机 HealthKit(AIDL) | Kit 路由开启 | 本地 AIDL |
| 2 新云端 | `USE_HEALTH_KIT_API=true` | `/healthkit/v1/fitnessPoints/duration`、`/watermark`、`/sportRecords/duration`、`/fitnessDatas`(写)、`/sportRecords`(写) |
| 1 旧云端 | 其余 | `/app/v1/data/get_fitness_data_by_time`、`..._by_watermark`、`get_sport_records_by_time`、`up_fitness_data`(写)、`up_sport_records`(写) |
→ 用户真机实测走 **line 1（旧路径）**。我们 SDK 先对接 line 1；line 2(`/healthkit/v1/`) 作为已知备选记录在案。注意时间尺度：Health Kit 分支把起止时间 ×1000(毫秒)，旧路径传秒；分页字段旧路径 `dataList/hasMore/nextKey`、新路径 `records/hasMore/cursor`。

### 3. 一条"免 root"的只读旁路（我们没考虑过）
未 root 的 adb 能读 `/sdcard/Android/data/com.mi.health/files/log/XiaomiFit.main.log`，里面 app 自己打印了**解析后的日报告**：`DailyStepReport[...]`、`DailyHrReport[...]`、`AllDaySleepReport{...}` 等，对方据此导出了真实步数/心率/睡眠/血氧/体重样本并和 app 页面核对通过。
→ 对我们的价值：**这是一个独立的"真值来源"**，M1 做"与 app 核对"时可以用它交叉验证云端拉到的数据对不对，省去反复手动看页面。局限：只有被日志打印的内容、日志会滚动、要插手机、逐秒明细不在日志里（见下）。不是最终方案，但验证阶段很有用。

### 4. 日常指标的 `value`→字段语义（直接喂给我们的 B 规整层，省探测）
对方已从反编译确认这些报告对象的字段（**关键单位/变换坑**一并记下）：
- **步数** `DailyStepReport[time, steps, distance, calories]`（距离米、time 为 Unix 秒、当天零点）。
- **心率** `DailyHrReport[time, restHr, avgHr, maxHr, minHr, latestHrRecord, hrDistribute]`——**日志里这些值被 ×5**，真实值 = 日志值/5；`HrItem(sid,time,hr)` 的 hr 同样 ×5；`DailyHrDistribute(smooth,warmUp,fatBurn,aerobic,anaerobic,extreme)`。（注意：这个 ×5 是"写日志"时的变换，云端 `value` 里是否 ×5 需实测核对——但字段名和结构可直接用。）
- **血氧** `DailySpo2Report[time, avgSpo2, maxSpo2, minSpo2, lackTimes]`，无乘除。
- **睡眠** `AllDaySleepReport{totalDuration, avgDuration, deepDuration, lightDuration, remDuration, awakeDuration, avgHr, avgSpo2, stage, score, ...}`；分段 `SleepSegmentReport{bedTime, wakeupTime, tzIn15Min, duration, deep/light/rem 时长, hr/spo2 min/max/avg, sleepItems, hrRecords, spo2Records, hrvDetailRecords}`；阶段项 `SleepStateItem(startTime, endTime, sleepState)`，**sleepState 常量**：0=非睡眠,2=深睡,3=浅睡,4=REM,5=清醒,6=SLEEP_TRACE,1=未知。
- **体重/身体成分** `WeightItem(time, sid, weight, bmi, …)` + 约 55 个可选成分字段（bodyFatRate/moistureRate/boneMass/muscleMass/proteinRate/visceralFat/分部位肌肉脂肪/体型评分/目标等，weight 无乘除）；命名坑：`COL_MUSCLE_RATE` 常量值其实是 `"muscleMass"`。
→ 这几类正好是我们第一批核心集，字段语义基本现成，B 层建模的探测量大幅减少（仍需用真实云端数据确认云端 `value` 的字段名/单位与这些报告对象是否一致）。

### 5. 逐秒明细 & 运动详情只能走 FDS（印证 kevinkwee）
- 逐秒心率/血氧在 release 渠道**不进日志**（`TimesDataRecordInt` 只打印 time）。
- 运动逐秒/GPS 在 FDS：`FitnessFDSDataGetter` 缓存到**应用内部** `filesDir`：`/data/user/0/com.mi.health/files/fitness/d<did>/…`——未 root 读不到。
- 运动文件导出（GPX/KML/TCX）经 `MyTrackLibraryActivity`，有"导出到路线库(含上传)"和"导出数据文件(本地)"两个分支。
→ 结论和 kevinkwee 一致：运动逐秒/轨迹要通过云端 FDS（`healthapp/service/gen_download_url` + AES），不走本地。

### 6. 零散有用事实
- 用户设备 `ro.miui.region=CN` → region `cn` → host 不加前缀，就是 `hlth.io.mi.com`。
- `@Secret` 默认值已坐实：`loginPolicy=2(必登)`、`encryptResponse=true`、`sid=""`(运行时取 `miothealth`)、`filterSignatureKeys=["data"]`。→ 和 kevinkwee crypto 里"只对 data 参数签名加密"一致。

## 三、对我们方案的影响

- **不改架构**，只是增强：路径 B 现在是三方一致 + 真机运行时确认，M0 信心很高。
- **数据域清单 + B 层**可直接吸收上面的字段语义（写进将来的实现）。
- **验证策略**新增一条独立真值来源：免 root 日志提取（`extract_*` 脚本思路可借鉴），用于 M1 核对。
- **先对接 line 1 旧路径**（`/app/v1/data/...`，真机实测在用），line 2 `/healthkit/v1/` 记为备选。
- 两边都还没打通独立云端调用 → 我们"你本地登录拿 token"这一步仍是真正的下一步，且能同时帮两路解套。

> 没有把对方的任何个人健康数据复制进本项目；以上只引用其反编译得出的结构/字段结论。
