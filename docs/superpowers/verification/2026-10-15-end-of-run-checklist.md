# 收尾真机核对清单（M3–M7、T2）

**状态：** 2026-10-15 已核对（第 1、2 节结果见各表；第 3 节已决定，见文末）。自主运行实现了路线图的全部条目，所有 app 对照都推迟到这一次与你一起的会话。本清单即这次核对的正式记录，不另写各域核对文档（2026-10-15 决定）；只记 PASS / 差异 / 未核对和原因，不含读数、读数日期或 id。

**规则**

- 结果栏只填 `PASS`、`差异` 或 `未核对`。备注写原因或差异的方向（例如"app 显示的是另一个卡路里数"），不写数值。本清单即各域的核对记录，不另写各域核对文档（第 4 节）。
- 导出一律写到仓库外的 `$HOME\mifit-verify\`。不提交 token、会话内容、导出的 JSON 或健康数值。
- 命令一次跑一个，出错不循环重试。每次都看 `notes`（CLI 在 stderr 打出的 `note:` 行）。
- 日期记号：`D` 是核对当天，`D-1` 是昨天，依此类推，都是本地日期 `YYYY-MM-DD`。日汇总只比完整的过去日，因为当天的数据云端可能还没同步完。
- 对比口径：**完全相等**指 app 显示的整数或离散值相等；**取整误差**指按 app 的显示精度四舍五入后相等；**形态一致**指曲线或轨迹的走势、峰谷、断点位置一致（设计 §8.1 对复杂聚合的口径）。
- B 层字段对不上，就移出 B 层（留在 `raw`），或者改名为它真实的含义。决定写进本清单的结果与决定一节。

## 0. 准备

```powershell
cd C:\dev\mifitness-re
git pull
pip install -e ".[test]"
python -m pytest -q        # 全部通过；Windows 上跳过 2 个（需要 time.tzset）
New-Item -ItemType Directory -Force "$HOME\mifit-verify" | Out-Null
Copy-Item "$HOME\.mifit\session.json" "$HOME\.mifit\session.pre-t2.json"   # 会话备份，你决定何时删除
```

开始前请在会话里告诉我以下几点（不写进仓库）：

- 用来比日汇总的 3 个完整日：默认是 D-3 到 D-1；如果这几天有没戴表的，换成最近 3 个戴表的完整日。
- 最近 3–4 次运动所在的日期范围，以及其中一次户外跑、一次户外骑的日期（2.7 节）。
- app 里最早的运动记录和体重记录大致是哪一年。2.2 和 2.5 节的长区间读取从那年的 1 月 1 日开始。
- 2.7 节核对轨迹形状时，我会在本机生成一张**不带底图**的轨迹 SVG：不提交、不上传，也不请求任何地图瓦片。你愿意打开看吗（spec §16 Q10）？不愿意的话，2.7 节第 3 项记 `未核对`。
- 手机上打开小米运动健康 app（同一账号），先下拉同步一次。

## 1. T2 会话：重新登录与强制续期

依据：[T2 计划](../plans/2026-10-14-mifit-session-renewal.md) Task 5，以及 notes/09 §5。当前保存的会话是 T2 之前的，里面没有 passToken，不能续期，所以要重新登录一次。

看日志用 `Get-Content "$HOME\.mifit\session-log.jsonl" -Tail 5`（日志里只有事件名、UTC 时间和时长）。看会话用下面的命令，它只打印字段名和布尔值，不打印内容：

```powershell
python -I -c "import json,pathlib;d=json.loads((pathlib.Path.home()/'.mifit'/'session.json').read_text());print(sorted(d), bool(d.get('pass_token')), bool(d.get('device_id')), bool(d.get('renewed_at')))"
```

1. **旧会话的最后一个数据点**：`mifit export steps --from D-1 --to D-1 --json "$HOME\mifit-verify\t2-before.json"`。成功：旧会话寿命的下界更新。失败并提示 `mifit login`：日志里应有 `unauthorized`，后面跟 `renew_unavailable`；这是第一次观测到自然过期，这条 `unauthorized` 的 `token_age_s` 是寿命的上界。
2. **重新登录**：`mifit login`，用手机扫码并批准。期望：上面的检查命令列出 `pass_token` 和 `device_id`，前两个布尔值都是 `True`。
   - **登录失败时的回退**（计划 Decision 3）：如果扫码登录因为新加的 `deviceId` cookie 而失败，先记下 CLI 的错误提示（不含 token）。然后我修改 `src/mifit/auth/qr.py`，让扫码的两个请求（`loginUrl` 和长轮询）不再发送 `deviceId`；续期用的 serviceLogin 不变。测试全部通过后，你再运行一次 `mifit login`。
3. **基线**：`mifit export steps --from D-1 --to D-1 --json "$HOME\mifit-verify\t2-after-login.json"`。期望：导出成功；登录之后日志只多了一行 `login`。
4. **制造一次 401**（第 0 节已经备份；这条命令什么都不打印）：

   ```powershell
   python -I -c "import dataclasses as d;from mifit.auth.session import load_session as l,save_session as s;s(d.replace(l(),service_token='t2-invalid'))"
   ```

   然后再跑一次第 3 步的导出，输出改为 `t2-renew.json`。期望：导出成功；日志新增 `unauthorized`、`renewed` 两行；`renewed_at` 有值，`created_at` 与第 2 步之后相同。要比较这两个时间，在运行前后各跑一次检查命令，把其中的 `print(...)` 换成 `print(d.get('created_at'), d.get('renewed_at'))`。
5. **不重复续期**：第三次跑同一个导出，输出改为 `t2-third.json`。期望：导出成功，日志没有新事件。
6. **失败的情况**：续期失败时只记 `RenewalError` 的原因和 code；无效 token 没有引起 401 时，记下服务端返回的状态。两种情况都再运行一次 `mifit login`，恢复可用的会话。

| 步骤 | 期望 | 结果 | 备注 |
|---|---|---|---|
| 1 旧会话导出 | 成功；或 `unauthorized` 后跟 `renew_unavailable` | PASS | 旧会话（无 passToken）登录约 22 h 后仍可读；寿命下界 ≥ 22 h，未观测到自然过期 |
| 2 `mifit login` | 会话里有 passToken 和 deviceId | PASS | 带 deviceId 的扫码登录正常；日志新增 `login` |
| 2′ 回退（只在需要时） | 停发 deviceId 后登录成功 | 未核对 | 不需要回退 |
| 3 基线导出 | 成功；日志只多一行 `login` | PASS |  |
| 4 强制续期 | 成功；`unauthorized` 后跟 `renewed`；`created_at` 不变 | PASS | passToken → serviceLogin → STS 续期真机可用；`renewed_at` 有值 |
| 5 第三次导出 | 没有新事件 | PASS | 四次导出的记录完全一致 |

## 2. 待真机核对的数据域

每节先跑命令，再在 app 里读数。我从导出算出要比的数，你读 app 上对应的数，双方都在会话里口头对照。

### 2.1 `heart_rate_daily`（M3）

```powershell
mifit export heart_rate_daily --from D-3 --to D-1 --json "$HOME\mifit-verify\hr_daily.json"
mifit export heart_rate --from D-1 --to D-1 --json "$HOME\mifit-verify\hr_window.json"
```

依据：[M3 计划](../plans/2026-10-14-mifit-hr-daily-summary.md) Task 5。在 app 的心率页读每一天的平均、最高、最低和静息心率。另外记一下 app 有没有显示心率区间时长和异常心率提醒（留给以后用，本次不判定）。

| 项 | app 里对照 | 口径 | 结果 | 备注 |
|---|---|---|---|---|
| `avg_bpm` | 日平均心率 | 完全相等 | PASS | 3 天一致 |
| `max_bpm` / `min_bpm` | 日最高 / 最低心率 | 完全相等 | PASS | 3 天一致（有一天睡眠时未佩戴，最低值偏高与之相符） |
| `resting_bpm` | 静息心率 | 完全相等 | PASS | 3 天一致 |
| D-1 的原始点 | 我算出最小值、最大值、均值，对照 app 当天的范围和平均，也对照 `heart_rate_daily` | 范围完全相等；均值在取整误差内 | PASS | 范围与日汇总、app 相等；点的简单均值与日均相差 < 1 |
| 心率区间 / 异常提醒 | app 有没有显示 | 只记录 | 未核对 | app 心率页不显示区间时长和异常提醒 |

### 2.2 `workouts`（M4）

依据：[M4 计划](../plans/2026-10-14-mifit-workouts.md) Task 3，以及 notes/10 §5.3。

```powershell
mifit export workouts --from <最近 3–4 次运动的前一天> --to D --json "$HOME\mifit-verify\workouts.json"
mifit export workouts --from <最早一次运动那年>-01-01 --to D --long --json "$HOME\mifit-verify\workouts_all.json"
```

第二条是长区间读取：按月分块，每块一次请求。跨度有几年，就是几十次请求，要几分钟。中途断了，加 `--resume` 重跑同一条命令即可接着读。

在 app 的运动记录页，把最近几次运动逐条读一遍：运动名、开始时刻、时长、距离（如果有）、卡路里、平均 / 最高心率、步数（如果有）。再读全部历史的条数；如果列表按月分组，就记每组的条数。我会从 `raw` 里同时算出 `total_cal` 和"结束 − 开始"，用来回答下面两个未决问题。

| 项 | app 里对照 | 口径 | 结果 | 备注 |
|---|---|---|---|---|
| 条数（最近这段） | 列表条数 | 完全相等 | PASS |  |
| `sport` | 运动名 | 能对应上；未映射的类型 `sport` 为 `None`，只看 `source_sport_code` | PASS | 4 种类型都能对应 |
| 开始时刻 | 把 `start_epoch_s + utc_offset_s` 当作 UTC 读，就是 app 上的本地开始时刻 | 到分钟相等 | PASS |  |
| `duration_s` | 时长 | 取整误差。如果 app 显示的是"结束 − 开始"：字段不改，只改 docstring | PASS | app 显示扣除暂停的时长，等于 `duration_s`；"结束 − 开始"含暂停 |
| `distance_m` | 距离（km） | 取整误差 | PASS |  |
| `active_kcal` | 卡路里 | 完全相等。如果 app 显示的是 `total_cal`：字段改名为 `total_kcal`，改从 `total_cal` 取值 | PASS | app 同时显示：`calories` = 活动卡路里，`total_cal` = 总卡路里 → `total_cal` 含义已核实，待加入 B 层（`total_kcal`） |
| `avg_hr_bpm` / `max_hr_bpm` | 平均 / 最高心率 | 完全相等 | PASS |  |
| `steps` | 步数 | 完全相等 | PASS |  |
| 长区间条数 | 全部历史的条数 | 完全相等；`notes` 里没有触顶或断页的提示 | PASS | 按月分块读取，条数与 app 一致，无提示 |

### 2.3 `spo2_daily`（M5）

```powershell
mifit export spo2_daily --from D-3 --to D-1 --json "$HOME\mifit-verify\spo2_daily.json"
```

依据：[M5 计划](../plans/2026-10-14-mifit-spo2-weight-profile.md) Task 5，以及 notes/11（2.3–2.6 节同）。在 app 的血氧页读每一天的平均、最高和最低。

| 项 | app 里对照 | 口径 | 结果 | 备注 |
|---|---|---|---|---|
| `avg_spo2_pct` | 日平均 | 完全相等。这是服务端给的日均，SDK 不重算；不要拿原始点的平均值来比 | PASS | 有数据的日子一致；无数据的一天服务端不返回日汇总，app 也无数据 |
| `max_spo2_pct` / `min_spo2_pct` | 最高 / 最低 | 完全相等 | PASS |  |

### 2.4 `spo2`（M5）

```powershell
mifit export spo2 --from D-1 --to D-1 --json "$HOME\mifit-verify\spo2.json"
```

在 app 的血氧页看 D-1 的全天图：曲线形态、第一个和最后一个点的时刻、全天范围。

| 项 | app 里对照 | 口径 | 结果 | 备注 |
|---|---|---|---|---|
| 第一个 / 最后一个点的时刻 | 图上的第一个 / 最后一个点 | 差不超过一个采样间隔 | PASS | 窗口起点的第一个桶没有丢（M3/M5 的提前一天修复生效） |
| 最小值 / 最大值 | 当天最低 / 最高 | 完全相等 | PASS |  |
| 全天曲线 | 全天图 | 形态一致 | PASS | 含一段无点的空白，与 app 一致 |

### 2.5 `weight`（M5）

```powershell
mifit raw weight --from <最早一次记录那年>-01-01 --to D --long --json "$HOME\mifit-verify\weight_raw.json"
mifit export weight --from <第一条记录那天> --to <最后一条记录那天> --json "$HOME\mifit-verify\weight.json"
```

第一条是一次分页读取，只用来找出记录落在哪几天。第二条的日期范围由我从第一条的结果算出；如果超过 92 天，要加 `--long`，按月分块读取。

在 app 的体重历史里读每一条的日期、时刻和 kg，以及 BMI（如果显示）。另外记一下 app 有没有显示身体成分（体脂率等）。

| 项 | app 里对照 | 口径 | 结果 | 备注 |
|---|---|---|---|---|
| 条数 | 历史条数 | 完全相等 | PASS | 只有一条手动导入的记录（没有体重数据源） |
| `epoch_s`（换算成本地时间） | 日期和时刻 | 到分钟相等 | PASS |  |
| `weight_kg` | kg | 取整误差 | PASS |  |
| `bmi` | BMI | 取整误差 | PASS |  |
| 身体成分 | app 有没有显示 | 只记录（第 3 节第 7 项） | 未核对 | 没有体脂秤，身体成分全 0，继续只留在 A 层 |

### 2.6 `profile`（M5）

```powershell
mifit profile --json "$HOME\mifit-verify\profile.json"
```

`raw` 是完整档案，含个人标识，所以这个文件只留在本机。在 app 里读个人信息的身高和体重，以及目标里的每日步数目标和卡路里目标。另外记一下睡眠目标（和它的单位）、目标列表里有哪几项；这两样留给以后用，用来确定睡眠目标的单位和 `regular_goal_list` 第 4 项的含义。

| 项 | app 里对照 | 口径 | 结果 | 备注 |
|---|---|---|---|---|
| `height_cm` | 身高 | 取整误差 | PASS |  |
| `weight_kg` | 体重 | 取整误差 | PASS |  |
| `step_goal` | 每日步数目标 | 完全相等 | PASS |  |
| `calorie_goal_kcal` | 卡路里目标 | 完全相等 | PASS |  |
| 睡眠目标 / 目标列表 | — | 只记录 | 差异 | 睡眠目标在 app 里没找到；`regular_goal_list` 第 4 项 = app 的"目标活动时间"（分钟）→ 含义已核实 |

### 2.7 `workout_detail`（M6）

依据：[FDS 设计](../specs/2026-10-14-mifit-fds-design.md) §14 和 §16，[M6 计划](../plans/2026-10-14-mifit-fds-workout-detail.md) Task 8，以及 notes/12 §10。

```powershell
mifit export workout_detail --from <跑步那天> --to <跑步那天> --json "$HOME\mifit-verify\detail_run.json"
mifit export workout_detail --from <骑行那天> --to <骑行那天> --json "$HOME\mifit-verify\detail_ride.json"
```

下表"我给出"一列由我用 `work/m6/` 下的脚本从导出算出（脚本不进仓库）；你在 app 的运动详情页读对应的数。

| # | 项 | 我给出 | app 里看 | 通过标准 | 结果 | 备注 |
|---|---|---|---|---|---|---|
| 1 | 轨迹起止 | 第一个 / 最后一个轨迹点的本地时刻 | 开始 / 结束时刻 | 差 ≤ 10 s | PASS | 跑步轨迹起止与记录起止相差 < 10 s |
| 2 | 轨迹距离 | 运动中各段的轨迹长度 | 距离 | 差 ≤ 3% | PASS | 只计运动中的轨迹：跑步与记录距离比 0.99，骑行 1.02 |
| 3 | 轨迹形状 | 不带底图的 SVG（只在本机打开；Q10 同意才做） | 地图上的轨迹 | 起点、终点、折返和暂停处一致 | PASS | 本机无底图 SVG 与 app 地图轨迹形状一致 |
| 4 | 心率 | 每分钟均值的折线，加平均 / 最高 / 最低 | 心率图和这三个数 | 形态一致；三个数相等 | PASS | 跑步平均 / 最高 / 最低一致；骑行平均 / 最高一致（app 不显示最低） |
| 5 | 配速（跑）/ 速度（骑） | 每公里配速、最高速度 | 配速分段、速度图 | 每公里配速差 ≤ 5 s/km；最高速度相等 | PASS | 每公里配速（扣除暂停）与 app 一致；骑行骑行最高速度一致；app 的平均速度 = 距离 / 含停车的总时长；停车时速度图为 0（Q6：`speed_mps = 0.0` 保留） |
| 6 | 步频（跑） | 平均 / 最高步频 | 步频 | 相等 | 差异 | app 的平均步频把步频为 0 的秒数算在内（含 0 的均值与 app 相等）；逐秒值正确 → 步频 0 改为保留 0，不再转 None |
| 7 | 暂停 | 样本时间线里的间隔数 | 暂停的次数和位置 | 一致 | 未核对 | app 里没找到暂停位置 |
| 8 | 海拔 | 海拔曲线，加最高 / 最低 | 海拔图和最高 / 最低 | 形态一致；跑步的极值差 ≤ 3 m。不通过则把 `altitude_m` 挪回 A 层 | 差异 | 骑行极值与 app 相差约 0.1 m；跑步极值相差 2–3 m（最低值超过 3 m 标准）→ `altitude_m` 移回 A 层 |
| 9 | 点数 | 点数和点间隔的分布 | （app 不显示） | 只记录 | 未核对 | 只记录：两次运动都有逐秒样本和轨迹点，点间隔基本为 1 s |

核对时顺带回答下面的问题（spec §16），答案写进备注：

- **Q1** 有暂停的那次跑步，app 的距离是不是只算运动中的部分？
- **Q2** 心率读数为 0 的那几秒，app 的心率图是断开还是连起来？
- **Q3** 那次跑步是分段课程。app 有没有按段展示？有的话，能不能借此核实 `segmentType` 的含义？
- **Q5** app 显示的配速是瞬时值还是平滑过的值（与样本的 `pace_s_per_km` 比较）？
- **Q6** 骑行停车（速度为 0）时，app 的速度图怎么显示？这决定 `speed_mps = 0.0` 保留，还是改为 `None`。
- **Q7** 你在 app 里会看逐秒卡路里、步幅、垂直振幅这些图吗？不看的话，它们继续只留在 A 层。
- **Q8** 非 cn 地区的对象存储主机名：只有一个 cn 账号，本次无法核对。默认不放宽白名单 `.fds.api.xiaomi.com`，在 README 里记为已知限制。

### 2.8 `stress_daily` 和候选 key（M7）

```powershell
mifit export stress_daily --from D-3 --to D-1 --json "$HOME\mifit-verify\stress.json"
mifit raw valid_stand --daily --from D-3 --to D-1 --json "$HOME\mifit-verify\stand.json"
mifit raw intensity --daily --from D-3 --to D-1 --json "$HOME\mifit-verify\intensity.json"
mifit raw calories --daily --from D-3 --to D-1 --json "$HOME\mifit-verify\calories.json"
```

依据：[M7 计划](../plans/2026-10-14-mifit-domain-catalog.md) Task 5，以及 notes/domain-catalog.md §5。在 app 里读每一天的平均、最高、最低压力（1–100 的压力指数）、站立小时数、中高强度分钟数和活动卡路里。如果压力页显示各压力档的时长，也读一下，用来认 `stress_scale` 的单位。

| 项 | app 里对照 | 口径 | 结果 | 备注 |
|---|---|---|---|---|
| `avg_stress_level` | 日平均压力 | 完全相等 | PASS | 3 天一致 |
| `max_stress_level` / `min_stress_level` | 最高 / 最低压力 | 完全相等 | PASS | 3 天一致 |
| `stress_scale`（`raw`） | 各档时长 | 只记录 | PASS | 单位是分钟（各档时长与 app 的时长和占比一致）→ 含义已核实 |
| `valid_stand.count`（候选） | 站立小时数 | 完全相等；对上了，就在目录里标 `app 已核对` | 未核对 | app 里没找到对应页面 |
| `intensity.duration`（候选） | 中高强度分钟数 | 同上 | 未核对 | app 里没找到对应页面 |
| `calories.calories`（候选） | 活动卡路里 | 同上；预计与 `consumption.active_kcal` 相同 | 未核对 | app 里没找到对应页面 |

## 3. 需要你决定的事项

前 5 项来自 notes/07 §5（T1 的历史审计），每项都有三个选项：**保留**；**在后续提交里删改**（只影响以后的版本，历史里仍然在）；**改写历史并强推**。改写历史是破坏性操作，会改变所有提交的 SHA，也挡不住已有的克隆、fork 和缓存；只在你明确要求时才做，我不会默认去做。

1. 3 个已有核对文档（`verification/2026-10-14-*.md`）里的健康数字。你的选择也决定本次新写的核对文档怎么记；默认只写一致或不一致。
2. 核对文档以外的健康数字：路线图的 M3 / M5、睡眠和 consumption 的计划、notes/04、notes/05。
3. 3 个测试文件里的真实读数：`tests/test_domain_consumption.py`、`tests/test_client.py`、`tests/test_cli.py`。可以机械地换成合成值。
4. 一条提交信息里的读数（见 notes/07 §5 第 4 项）。提交信息没法用后续提交修改，所以只能保留，或者改写历史。
5. 账号属性：地区、时区、手机型号。这一项只是信息级的；也可以只删去手机型号。
6. **notes/10 §4 公开列出了本账号做过的 4 种运动类型。** 可以保留，也可以改成不指明账号的写法（映射表本身仍留在代码里）。
7. **体脂秤和体重数据（M5）。** 现在的判断（路线图 §6）是：身体成分全为 0，所以体重只做到"能读、结构正确"。如果你有体脂秤或打算用，请告诉我，以后再核对身体成分。
8. **常做的运动类型和手表固件（M6，spec §16 Q9）。** 你以后会记录哪些运动（户外步行、徒步、游泳……）？固件会不会升级？现在只解析户外跑和户外骑（record v8 / v5、GPS v2）。新的运动类型或文件版本都要加一张手写布局表，并先用真实文件核对。

## 4. 核对完成后的收尾（我来做）

- 按各计划写正式核对文档：`verification/<日期>-<名称>-live.md`，名称分别是 hr-daily、session-renewal、workouts、spo2-weight-profile、workout-detail 和 m7。写法按第 3 节第 1 项的选择，默认只写一致或不一致。→ 2026-10-15 决定：不另写，本清单即核对记录。
- 不通过的 B 字段移出 B 层（留在 `raw`）或改名，模型、域、测试和 CLI 一起改。已经预见到的情况：`active_kcal` 改为 `total_kcal`（2.2）；`duration_s` 只改 docstring（2.2）；`altitude_m` 挪回 A 层（2.7 第 8 项）；`speed_mps` 的 0 改为 `None`（Q6）。
- README 的 "What it reads today" 表：通过的行把 `pending` 改为 `yes`；部分通过的行写明哪个字段只在 `raw` 里。再补上 workouts 的已知限制，把 Q8 记为已知限制。
- 路线图：§0 状态（待真机核对改为完成或部分完成）；§1 数据域清单；§2 的会话一行（续期已核对或被拒，以及寿命下界）；§3 M3–M7 和 §4 T2 的结果行；§6 需要你参与的节点。
- notes：notes/09 §5 写入续期结果和寿命下界；notes/10 §5.3、notes/11 §4、notes/12 §10 关闭已回答的未决问题；notes/domain-catalog.md 更新 `已核对` 和 `B 层` 两列，对上的候选 key 标 `app 已核对`。
- 补上 core-read-live 的核对 4（T1 留下的记录缺口）：导出与 mi-health-lab 已核样本日期重叠的 steps，比对后只记是或否。
- 按第 3 节的决定处理健康数字和 notes/10。除非你明确要求，不改写历史。
- 每次提交前运行 `python -m pytest -q`，全部通过才提交；推送前先问你。`session.pre-t2.json` 和 `$HOME\mifit-verify\` 何时删除由你决定。

## 结果与决定（2026-10-15）

- 第 1、2 节：T2 续期真机可用；8 个数据域与 app 一致，差异只在 2.7 的平均步频口径（app 含步频为 0 的秒数）和跑步海拔（相差 2–3 m）。新核实的含义：`total_cal` = 总卡路里，`stress_scale` 各档单位为分钟，`regular_goal_list` 第 4 项 = 目标活动时间（分钟），骑行速度 0 在 app 图上显示为 0。
- 第 3 节的决定：1–3 在后续提交里删改（测试换合成值），不改写历史；4 保留；5 删去手机型号、保留地区和时区；6 notes/10 改为不指明本账号；7 没有体重数据源（唯一一条为手动导入），身体成分继续只留在 A 层；8 维持现状，户外步行 / 徒步、游泳、室内跑等以后有真实文件时再补布局表。
