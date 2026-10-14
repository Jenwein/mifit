# mifit 路线图

- 更新：2026-10-15
- 依据：[设计文档](specs/2026-10-13-mifitness-sdk-design.md)（§4 交付顺序、§6 数据域清单、§10 里程碑）、notes/01–05、已完成的两轮真机核对
- 性质：活文档。每完成一个里程碑就更新状态，并把新发现的坑补进"遗留问题"一节。

---

## 0. 一页总览

| # | 里程碑 | 规模 | 状态 |
|---|---|---|---|
| M0 | 冒烟：签名/登录今天仍有效 | — | ✅ 完成 |
| M1 | 核心闭环：传输 + 扫码登录 + 步数 + 原始心率 | — | ✅ 完成（真机核对 PASS） |
| M1+ | 卡路里（consumption） | — | ✅ 完成（真机核对 PASS，日期修复已复测） |
| M2 | "日"的语义统一 + 睡眠 | 中 | ✅ 完成（M2a + M2b，真机核对 PASS） |
| **M3** | **心率日汇总（静息心率等）+ 原始心率完整性限时调查** | 中 | ✅ 实现完成（2026-10-14），待真机核对（心率日汇总） |
| M4 | 运动记录汇总（运动列表） | 中 | ✅ 实现完成（2026-10-14），待真机核对 |
| M5 | 血氧 + 体重/身体成分 + 用户档案 | 中 | ✅ 实现完成（2026-10-14），待真机核对 |
| M6 | 运动明细 + GPS 轨迹（FDS） | 大，需单独设计文档 | ✅ 实现完成（2026-10-15），待真机核对 |
| M7 | 长尾数据域：枚举 key、建数据域目录 | 中，持续 | ✅ 一轮完成（2026-10-15）；目录 notes/domain-catalog.md 持续维护；stress_daily 待真机核对 |

横向工作（不占主线顺序，按触发点插入）：

| # | 横向工作 | 何时做 |
|---|---|---|
| T1 | 合规与仓库卫生：LICENSE（MIT）、MIT 署名、README | ✅ 完成（2026-10-14）；历史审计见 notes/07（只报告，未改写历史） |
| T2 | 会话寿命与自动续期 | ✅ 实现完成（2026-10-14），待真机核对（需重新 `mifit login` 一次以保存 passToken） |
| T3 | 长区间 / 全量导出：分块、节流、断点续传 | ✅ 实现完成（2026-10-14）：分块串行读取、超过 92 天需 --long、--resume 断点续传 |
| T4 | CI（Linux 上跑测试，含夏令时用例） | ✅ 完成（2026-10-14）：GitHub Actions，ubuntu-latest × Python 3.12/3.13/3.14；`test` extra 带 tzdata |
| T5 | 密码登录备选 | 推迟；扫码登录够用就不做 |

主线顺序沿用设计 §4.6/§10：步数 → 睡眠 → 心率 → 运动记录 → 血氧、体重 → FDS 明细 → 其余。唯一的插入是 M2 开头的"日语义统一"（理由见 M2）。

---

## 1. 已完成（截至 2026-10-15）

- **底座**：nonce/RC4/SHA1 签名、地区路由、扫码登录 + STS、本地会话存储、签名传输（含 401 处理）、分页、`MiFit` 门面、`mifit login` / `mifit export` CLI。
- **数据域**（除标注外均已真机核对）：
  - `steps`：日步数、距离与 app 一致（`step_calories` 是步数报告自带的走路卡路里，不是 app 的卡路里环）。
  - `heart_rate`：原始心率点，单日区间和均值与 app 一致；按时间窗读取已补全（M3：起点提前一天再过滤，见 notes/08；多页读取也已真机核对）；`--full` 为不带起点、往回翻页的读法，触顶（10 页）时有提示。
  - `heart_rate_daily`：日汇总的平均 / 最高 / 最低 / 静息心率（`avg_bpm` / `max_bpm` / `min_bpm` / `resting_bpm`）；待真机核对。
  - `consumption`：`active_kcal` 等于 app 的活动卡路里，`resting_kcal` 为 BMR；按本地零点请求（与 app 一致）。
  - `workouts`：每次运动一条（`Workout`：开始 / 结束时刻、时区偏移、运动类型的中立名、时长、距离、活动卡路里、平均 / 最高心率、步数；0 或缺失记为 `None`，时区偏移除外：0 即 UTC，照实保留）；待真机核对（notes/10）。
  - `workout_detail`：每次运动的逐秒样本（`WorkoutSample`）与 GPS 轨迹（`TrackPoint`），户外跑 / 骑（record v8 / v5、GPS v2）；待真机核对（notes/12）。
  - `spo2_daily`：日汇总的平均 / 最高 / 最低血氧（`Spo2DailySummary`：`avg_spo2_pct` / `max_spo2_pct` / `min_spo2_pct`，日均直接取服务端的值，不重算；0 或缺失为 `None`）；待真机核对（notes/11）。
  - `spo2`：原始全天血氧点（`Spo2Point`）；与原始心率一样起点提前一天再过滤（共享的 `raw_window`）；待真机核对。
  - `stress_daily`：日汇总的平均 / 最高 / 最低压力（`StressDailySummary`：`avg_stress_level` / `max_stress_level` / `min_stress_level`，1–100 的压力指数，直接取服务端的值；0 或缺失为 `None`；各档时长 `stress_scale` 只留在 `raw`）；待真机核对（notes/domain-catalog.md）。
  - `weight`：体重记录（`WeightRecord`：时刻、kg、BMI），身体成分只留在 `raw`；待真机核对。
  - `MiFit.profile()` / `mifit profile`：用户档案（`UserProfile`：身高、体重、每日步数目标与卡路里目标），`raw` 是完整档案（含个人标识）；待真机核对。
  - `MiFit.raw(key, ...)`：任意原始 key 的透传读取。
  - `MiFit.raw_window(key, ...)` / `MiFit.daily_report(key, ..., tag=...)` / `mifit raw <key> [--daily [--tag TAG]]`：任意 key 的通用 A 层读取。原始 key 起点提前一天再按窗口过滤，汇总 key 按步数的日窗口；`records` 为空，`raw` 是服务端的行（`value` 已解码）。已知的 key 见 notes/domain-catalog.md。
- 测试 1178 个通过、2 个跳过（装上 `test` extra；跳过的 2 个需要 `time.tzset`，Windows 上没有，CI 的 Linux 上会跑），全部离线（假 Transport；3 个测试文件含真实读数，见 notes/07 §3、§5）。

---

## 2. 遗留问题（带入后续里程碑）

| 问题 | 影响 | 归入 |
|---|---|---|
| ~~steps 的日界语义只在东八区成立~~ → **已修（M2a）**：窗口改为按日汇总键左开右闭构造，与时区无关（notes/06）。 | — | ✅ M2a |
| consumption 当初的教训：自己推"服务端怎么切日"会推错；照抄 app 发什么才稳。 | 方法论 | M2a 起所有日汇总域 |
| ~~原始心率按时间窗查询会漏点（根因未知）；`--full` 有 10 页上限，触顶时没有提示。~~ → **已修（M3）**：根因是服务端丢掉键在 / 早于 `start_time` 的那个约 2 h 桶；修法是起点提前一天再按窗口过滤（notes/08）；`--full` 触顶时现在有提示。 | — | ✅ M3 |
| 会话多久过期、能否用 passToken 自助续期，都未验证（T2 之前一遇 401 就提示重新扫码）。 → **已实现（T2），待真机核对；寿命观测中**：passToken→serviceLogin→STS 续期、401 时自动续期一次、生命周期日志（notes/09）。 | 续期是否真有效、真实寿命，仍待真机核对和日志观测。 | T2 |
| ~~移植自 kevinkwee/Mi-Fitness-Sync（MIT）的签名/传输逻辑没有保留署名；仓库无 LICENSE、无 README~~ → **已修（T1）**：LICENSE（MIT）、THIRD_PARTY_NOTICES、4 个源文件头署名、README。 | — | ✅ T1 |
| ~~夏令时用例在本机因缺 tzdata 被跳过。~~ → **已修（T4）**：`pip install -e ".[test]"` 装上 tzdata，Windows 本机也跑夏令时用例；CI 在 Linux 上跑全部测试，测试里禁止联网。 | — | ✅ T4 |
| ~~sleep 审查遗留 Minor ①–⑧~~ → **已修（sleep 加固，2026-10-14）**：①畸形数据跳过并记 note（不可哈希的 state 记为 unknown）；②相同起止的重复原始会话优先取有分期的；③`raw` 只含日汇总和实际匹配上的原始会话（已删除、加宽窗口带出的不再混入；全部原始会话用 `MiFit.raw("sleep", …)`）；④分期列表按会话复制；⑤没有会话时不发原始请求；⑥补 UTC−5 原始窗口测试；⑦重写该测试使断言可失败；⑧原始请求的 ApiError 保留日汇总并记 note，AuthError 照常抛出。另：`score` 移出 B 层，只留在 `raw`（`sleep_score`）：app 只显示等级，数值未核对（设计 §2 反目标）。 | — | ✅ sleep 加固 |

---

## 3. 主线里程碑

每个里程碑沿用现有流程：实现计划（`docs/superpowers/plans/`）→ TDD 实现 → 真机核对（`docs/superpowers/verification/`）→ 更新本路线图。核对口径沿用设计 §8.1：A 层必须正确完整；与 app 数字对齐尽力而为，简单聚合要对齐，复杂聚合要求量级和形态一致。

### M2 — "日"的语义统一 + 睡眠

**M2a 日语义统一（先做，小）**
- 为什么先做：后面的睡眠、心率日汇总、血氧日汇总都走同一个日汇总接口 `data/get_aggregated_fitness_data_by_time`（`tag=daily_report`）。consumption 的日期 bug 返工了两轮，不能在四个数据域上各踩一遍。
- 内容：
  1. 反编译 app 调日汇总接口的地方，确认 app 怎么算 `start_time` / `end_time`、怎么把返回的 `time` 映射回日期（同 consumption 的做法：看 app 发什么，不猜服务端规则）。
  2. 抽一个共享的"本地日 ⇄ 服务端日键"小工具，时区可注入（默认本机时区）。
  3. steps 改用它；补东八区、西五区、夏令时的测试。
- 完成标准：steps 在东八区真机结果不变；非东八区由单元测试覆盖（只有一个东八区账号，无法真机核对，需在核对文档里写明）。
- **结果（2026-10-14）✅**：app 同步日汇总不带时间窗，按 GMT+0 取 `time` 的日期再换本地零点；服务端窗口实测为 `start_time < time <= end_time`（notes/06）。新增 `domains/days.py`，steps 改用 `report_window`，`StepRecord.epoch_s` 统一为本地零点；东八区真机 10-01…10-14 新旧结果逐日一致。

**M2b 睡眠**
- 数据来源（notes/04 §2）：
  - 日汇总 `key=sleep` → `CloudSleepReport`：总时长、深睡/浅睡/REM/清醒时长、睡眠分数、平均心率/血氧、`segment_details[]`（每段入睡/醒来时间、`timezone` 以 15 分钟为单位、效率等）。
  - 原始 `key=sleep` → `SleepSegmentReport.items[]`：分期序列，`state` 取值 0 非睡 / 2 深睡 / 3 浅睡 / 4 REM / 5 清醒 / 6 trace。
  - `watch_daytime_sleep` / `watch_night_sleep` → `DayNightSleepReport`，结构不同，本期只做 A 层透传。
- B 层（app 中立命名，设计 §12）：`SleepRecord`（日级：各分期分钟数、总时长、分数、入睡/醒来时刻）和 `SleepStage`（分期区间：开始、结束、阶段）。分期枚举用中立名称，小米的数字只在映射层出现。
- 已知坑：睡眠时间走 app 的 `sleepSegmentToLocalTime`，和步数的日界规则不同；先反编译确认再写。一晚睡眠跨两个日期，"这晚算哪天"必须和 app 一致。
- 完成标准：2–3 晚的总时长、各分期时长、分数、入睡/醒来时刻与 app 睡眠页一致（取整误差内）；分期区间与 app 的分期图形态一致。
- 需要你：核对时在 app 里读出这几晚的上述数字（我会提前列好清单）。
- **结果（2026-10-14）✅**：`MiFit.sleep()` / `mifit export sleep`，`SleepRecord`（按天）→ `SleepSession`（每段）→ `SleepStage`（分期）。总睡眠（含小睡）、夜间起止、深睡/浅睡/清醒、睡眠平均心率与 app 一致；分数 app 只显示等级，无法核对；小睡在 B 层表现为无分期的会话。见 [verification/2026-10-14-sleep-live.md](verification/2026-10-14-sleep-live.md)。审查遗留的 Minor 见第 2 节遗留问题表。（`score` 后移出 B 层，见第 2 节）

### M3 — 心率日汇总 + 原始心率完整性（限时）

- 日汇总 `key=heart_rate` → `CloudHrReport`：平均、最高、最低心率，静息心率 `avg_rhr`，各心率区间时长，异常心率次数。B 层 `HeartRateDailySummary`。
- 原始 key 补齐：`resting_heart_rate`（静息）、`single_heart_rate`（手动测量）。
- 上次核对记下的目标：静息心率 77、睡眠心率 65 要能对上（睡眠心率可能来自 M2 的睡眠报告）。
- **完整性限时调查**（建议 1 个工作单元，到时间就停，结论写进 notes）：对照 app 的 `pullFullPullFitnessData` 读法，比较窗口查询和全量翻页的点数，尝试定位漏点原因；同时让 `--full` 触顶时给出提示（这一项放到 T3 也可以）。
- 完成标准：静息心率和日均、日最高、日最低与 app 一致；完整性问题要么定位，要么形成书面"已知限制"。
- **结果（2026-10-14）✅（待真机核对）**：`MiFit.heart_rate_daily()` / `mifit export heart_rate_daily` 产出 `HeartRateDailySummary`（avg / max / min / resting），待真机核对（日均、范围、静息心率对照 app）。完整性已定位并修复：服务端丢掉键在 / 早于 `start_time` 的那个约 2 h 桶，窗口读取改为起点提前一天再过滤，3/3 天与不带起点的读取逐行一致（notes/08）；`--full` 触顶 10 页时现在有提示。带起点的多页窗口读取也已真机核对：3 天的窗口分 2 页，窗口内 269/269 行与不带起点的读取逐行一致（notes/08 §4）。范围上有意收窄：各心率区间时长、异常心率次数、`latest_hr` 含义未核实，只留在 `raw`，不进 B 层；`resting_heart_rate` / `single_heart_rate` 没有专门的数据域，用 `MiFit.raw` 读取。

### M4 — 运动记录汇总

- 接口 `data/get_sport_records_by_time`（`reverse=true`、`limit=50`、按 `next_key` 翻页，可选 `category`）。
- 每条 `value` 是约 200 个字段的 `SportBasicReport`：A 层全量透传；B 层 `Workout` 只放核对过的核心字段（运动类型、开始/结束、时长、距离、卡路里、平均/最高心率、步数），其余留在 A 层（设计反目标：不确定含义的字段不进 B）。
- 运动类型：小米的类型编号 → 中立运动类型的映射表放在映射层。先覆盖你实际做过的类型，其余保留原始编号。
- 完成标准：你最近的几次运动，列表条数和核心字段与 app 运动记录页一致。
- 前置：T2（会话续期）最好在这之前有结论，因为运动历史往往要长区间拉取。
- **结果（2026-10-14）✅（待真机核对）**：`MiFit.workouts()` / `mifit export workouts` 产出 `Workout`（开始 / 结束、时区偏移、中立运动类型、时长、距离、活动卡路里 `active_kcal`、平均 / 最高心率、步数；0 或缺失为 `None`，时区偏移除外：0 即 UTC，照实保留），运动类型只映射了已核实的 4 个编号，其余 `sport` 为 `None` 并保留 `source_sport_code`；`total_cal`（含静息消耗，app 显示哪一个卡路里待真机核对）、最低心率、心率区间、配速 / 步频等留在 `raw`。按开始时刻选行（服务端规则 `start_time < time <= end_time`，客户端再过滤），最多读 20 页，触顶时结果带提示建议缩短范围（notes/10）；app 对照推迟到收尾的真机核对会话。

### M5 — 血氧 + 体重/身体成分 + 用户档案

- 血氧：日汇总 `key=spo2`（`CloudSpo2Report`），原始 `spo2` / `single_spo2` / `low_spo2`。已知 app 日均（95）≠ 点的简单平均（96），聚合算法未知，**不硬凑系数**；B 层日均直接取服务端的日汇总值。
- 体重：key 待探测（候选 `weight_item_record` 系列）。身体成分字段在已知样本里全是 0，含义未核实前只进 A 层。
- 用户档案/目标（身高、体重、各项目标）：入口待确认，可能是单独的接口；先反编译定位。
- 完成标准：血氧日汇总与 app 一致；体重记录条数和数值与 app 历史一致。身体成分能核对到哪一步取决于你是否有体脂秤数据。
- 需要你：告诉我你有没有体脂秤或手动记录过体重；没有的话，体重只做到"能读、结构正确"。
- 可调：血氧和心率日汇总走同一路径，想提前的话可以并进 M3。
- **结果（2026-10-14）✅（待真机核对）**：key 与入口已找到（notes/11）：血氧日汇总 `key=spo2`（`tag=daily_report`），原始 `spo2` / `single_spo2` / `low_spo2`，体重 key 为 `weight`（APK `FitnessPersistKey.Weight`），都走现有接口；用户档案是单独的接口 `healthapp/user/get_miot_user_profile`（不带 `data` 参数，签名路径去掉 `healthapp/` 前缀），`Transport.get_json` 为此加了 `base_path` 关键字并接受 `data_obj=None`。原始 `spo2` 有与原始心率相同的首桶丢失，经共享的 `domains/raw_window.py`（起点提前一天再过滤）修正，`heart_rate` 行为不变，`weight` 也经它读取。B 层：`MiFit.spo2_daily()` → `Spo2DailySummary`（服务端的日均 / 最高 / 最低，不重算、不拟合；`lack_spo2_count`、`latest_spo2` 只留在 `raw`），`MiFit.spo2()` → `Spo2Point`，`MiFit.weight()` → `WeightRecord`（时刻、kg、BMI）；身体成分只进 A 层，本账号上全为 0；`MiFit.profile()` / `mifit profile` 的 B 层只有身高、体重、每日步数目标和卡路里目标（`UserProfile`），睡眠 / 站立目标、目标列表和个人标识只留在 `raw`。`single_spo2` / `low_spo2` 用 `MiFit.raw` 读取。app 对照推迟到收尾的真机核对会话。

### M6 — 运动明细 + GPS（FDS）

- 规模最大、风险最高，**先单独写设计文档**再写计划。
- 已知链路（notes/04 §2d）：`healthapp/service/gen_download_url`（签名路径要去掉 `healthapp/` 前缀再补回 `/`）→ 预签名 URL → 下载（不带会话 Cookie、不跟随重定向）→ Base64 → AES/CBC/PKCS5（固定 IV）→ 二进制头部 + body → 按 APK `assets/schemas.zip`（72 项）解析。
- 范围建议：先做 `record`（逐秒明细，proto 0）和 `gps`（proto 2），只覆盖你实际做过的 1–2 种运动；其余 proto_type 按需补。
- 新依赖：AES 需要引入一个加密库（如 `cryptography`），到时在设计文档里定。
- 完成标准：一次运动的轨迹点数、起止时间、距离与 app 轨迹页一致；逐秒心率曲线形态与 app 一致。
- **结果（2026-10-15）✅（待真机核对）**：链路为运动列表（与 `workouts` 共用 `sport_rows`）→ `healthapp/service/gen_download_url`（签名路径 `/service/gen_download_url`；每次运动一次请求，带 record、GPS 两项）→ 预签名 URL → 独立的下载器（不带 Cookie、会话或 netrc 凭据，不跟随重定向，只认 200，主机须在 `.fds.api.xiaomi.com` 下，正文上限 16 MiB，与 `Transport` 同样节流）→ JSON 字符串里的 base64url → AES-128-CBC（固定 IV）→ 8 字节头 + 有效性标志，头部的时间戳、时区、协议号须与汇总一致才解析。新增硬依赖 `cryptography>=42`（只在 `fds/cipher.py` 里导入）。只启用真机见过的三种布局（D1）：跑 record v8（协议 22）、骑 record v5（协议 23）、GPS v2；其它运动（本账号的爬楼机、力量训练，协议 8）不发 FDS 请求，其它版本只给头部，都带 note。`MiFit.workout_detail()` / `mifit export workout_detail` 每次运动一条 `WorkoutDetail(workout, samples, track)`：B 层 `WorkoutSample`（逐秒：真实 Unix 秒、心率、累计距离、累计步数、步频、配速、骑行速度、海拔）和 `TrackPoint`（时刻、纬度、经度）；海拔暂进 B，跑步极值与汇总差 2–3 m，待真机定（spec §16 Q4），其余（逐秒卡路里、步幅、冲击力、垂直振幅、段字段、GPS 精度与速度等）只留 A 层（`raw` 用 `schemas.zip` 的字段名、存储值不缩放）。一次调用至多读 10 次支持的运动（超出加 note），分块读取按周（`week`）；`--no-track` / `track=False` 不请求 GPS 文件。URL、`obj_key`、`sid` 不进任何输出。本机真实文件闸门通过（D25，不联网）：4/4 份文件走到末尾、无剩余字节、无异常，头部与汇总一致；跑步步数、骑行卡路里、心率最大 / 最小值与汇总相等，距离与汇总的比值 1.000，轨迹长度 / 距离 0.99（跑）、1.02（骑），丢点 0；用保存的列表、链接答复和下载正文离线重放整条链路，得 2 条带样本和轨迹的明细，另 2 条只有“不支持”的 note。事实见 notes/12；app 对照推迟到收尾的真机核对会话（spec §14、§16）。

### M7 — 长尾数据域

- 系统性枚举 app 用到的全部 key（APK 中的 `FitnessPersistKey` 常量 + 设计 §6 候选：压力、PAI、活力指数、训练负荷、VO2max、站立、体温、房颤、`calories` 日报等），低频逐个探测。
- 产出一份受控的**数据域目录**（建议 `notes/domain-catalog.md`）：key ↔ 接口 ↔ value 结构 ↔ 是否核对 ↔ 是否有 B 层。
- 默认只做 A 层（通过 `raw` 或一个通用透传数据域）；只有核对过含义的才加 B 层。
- 持续进行，不追求一次完备（设计 §6"全量"的操作性定义）。
- **结果（2026-10-15）✅ 一轮完成（`stress_daily` 待真机核对）**：枚举方法是 APK 常量表（`FitnessPersistKey` 的 224 个常量 = 约 170 个运动标签 + 51 个健康 key + `passion_cheer`）加 37 次只读调用（只带 tag 的汇总读取、`get_latest_fitness_data` 每批 10 个 key、7 天原始读取、首桶核对和单日一致性核对），只记 key 名、字段名、分桶的行数和布尔。51 个健康 key 里本账号有数据的原始 key 23 个，其余 28 个与 `data_collector` 没有行（分不出是没有数据还是服务端不认识）；汇总 key 9 个，分在三个 tag 下（`daily_report` 8 个、`daily_fitness` 的 `goal`，`daily_mark` 是每天每个 key 的 `has_data` 索引）。通用 A 层读取 `MiFit.raw_window` / `MiFit.daily_report` / `mifit raw` 能读任何 key：原始 key 一律提前一天请求再过滤，因为首桶丢失已在查过的 4 个 key 上全部证实（`heart_rate`、`spo2`、`stress`、`calories`）。新 B 域 `stress_daily`（`StressDailySummary`，日均 / 最高 / 最低压力）：依据是一天的内部一致（三个数恰是当天原始点的最小、最大和均值四舍五入），app 对照待做；各档时长 `stress_scale` 只留在 `raw`。候选（只在 A 层，等 app 对照）：`valid_stand`（站立小时数？）、`intensity`（中高强度分钟数？）、`calories`（一天里等于 `consumption.active_kcal`）。目录 notes/domain-catalog.md 记录 key ↔ 接口 ↔ 值字段 ↔ 本账号有无数据 ↔ 核对状态 ↔ B 层，守卫测试 `tests/test_domain_catalog.py` 保证每个已注册的数据域都在目录里、目录里没有 Unix 时间戳。目录持续维护，不追求一次完备。

---

## 4. 横向工作

### T1 合规与仓库卫生（完成）
- 本项目 LICENSE：**MIT**（已定，2026-10-14）。仓库公开。
- 对移植自 kevinkwee/Mi-Fitness-Sync 的部分（签名/RC4、传输、登录流程思路）在源文件头和 `THIRD_PARTY_NOTICES` 里保留其 MIT 版权声明（设计 §5 要求）。
- 写 README：安装、扫码登录、导出、已知限制（云端滞后、卡路里环是 app 端合成、心率完整性、只读）。
- 复查 git 历史里没有会话、token 或个人健康数据（`.gitignore` 已覆盖常见路径，做一次确认）。
- **结果（2026-10-14）✅**：新增 LICENSE（MIT）、THIRD_PARTY_NOTICES、4 个源文件头署名、README；历史审计未发现真实凭证或账号标识，个人健康数字在 3 个核对文档之外还见于路线图、计划、notes/04–05、测试代码和 1 条提交信息（只报告，未改写历史）；notes/07 §5 留给你决定 5 项。

### T2 会话寿命与自动续期
- 现在就开始观察：记录每次登录时间和首次 401 的时间，先得到真实的有效期。
- 调研：能否用 `passToken` 重新走 STS 换新的 serviceToken，而不必重新扫码（notes/04 指出续期依赖账号 SDK，不能只靠 serviceToken + ssecurity）。
- 实现目标：过期时自动续期一次；续不了再提示扫码。401 也可能是签名错误，不能无限重试（设计 §3：最多 3 次）。
- **结果（2026-10-14）✅（待真机核对）**：会话保存 passToken / deviceId / 续期时间，`renew_session` 走 passToken→serviceLogin→STS，传输层遇 401 自动续期一次并重试（每请求最多 1 次、每个 Transport 最多 3 次），`~/.mifit/session-log.jsonl` 记录生命周期（只有事件名、UTC 时间和时长）。目前观察到的有效期至少 7 小时，自然过期时间将从会话日志读出；续期本身和新 deviceId 下的扫码登录尚未真机核对，当前已存会话没有 passToken，需重新 `mifit login` 一次。

### T3 长区间 / 全量导出
- 默认分块（按周或按月）+ 串行 + 带抖动的延时（设计 §3 风控默认值），全量导出需要显式开启。
- 断点续传（中断后从上次的块继续），`--full` 等分页上限触顶时给出明确提示。
- **结果（2026-10-14）✅**：`mifit.chunks` 按数据域分块（原始心率 / 血氧 7 天，其余 31 天，从 `--from` 起算），逐块串行读取，每个请求前仍是传输层的约 1 s + 抖动；一块装得下的区间仍是一次调用，与以前相同。超过 92 天需 `--long` 显式开启；`heart_rate --full` 不分块。`--json` 旁的 `<PATH>.partial` 每完成一块追加一行（只有数据域、日期、块大小和该块结果，不含凭证），中断后 `--resume` 从未完成的块继续；某块失败时已完成的块保留，不跳过也不自动重试。另：服务端说还有下一页却不给可用游标时结果带提示；CLI 输出为严格 JSON（NaN/Infinity 记为 null 并提示）。库接口 `MiFit.read_chunks` + `merge_chunks`。
- ~~后续清理（backlog）：抽出共享的日报行解码器（`heart_rate_daily`、`spo2_daily`、`steps`、`workouts`、`weight`、`spo2` 都在重复同一段解码 + notes 逻辑）、共享的测试替身（各测试文件里 `FakeTransport` / `payload_with_rows` 的拷贝），以及一个 `as_positive_float` 辅助函数。~~ → **已做（T3 Task 2、Task 7）**：共享的测试替身 `tests/support.PagedFake` 取代了各文件的 `FakeTransport`（Task 2；consumption、profile 只用 `get_json`，保留各自的替身）；共享的行解码 `domains/base.decode_value` 和 `as_positive_float`（Task 7）。`payload_with_rows` 仍是各测试文件各一份（各自基于本域的 fixture），未合并。

### T4 CI（完成）
- GitHub Actions 在 Linux 上跑 `pytest`（Linux 有 tzdata，夏令时用例会真正执行）。
- **结果（2026-10-14）✅**：`.github/workflows/ci.yml`，push / pull_request 触发，无 secrets、只读权限；`tests/conftest.py` 拦截一切网络连接；README 加 CI 徽章和 "Development" 一节。

### T5 密码登录（推迟）
- 设计里是扫码登录的备选。扫码已经稳定可用，不做；以后真有需要再说。

---

## 5. 不做（重申设计 §2）

写入/修改/删除云端数据；读手机本地数据库（需 root）；设备内 IPC；Strava/GPX/FIT 等下游导出；图形界面；多数据源框架（只按 §12 做轻量预留，每个里程碑顺手检查 B 层命名是否中立）。

---

## 6. 需要你参与的节点

| 节点 | 你要做的 |
|---|---|
| 每个里程碑的真机核对 | 按我列的清单在 app 里读数（每次约 5–10 分钟） |
| 会话过期时 | 重新 `mifit login` 扫码 |
| M5 | ~~告知体重/体脂秤数据情况~~ → ✅ 已答（2026-10-14）：账号上只有一条手动体重，身体成分全 0 → 只做到能读、结构正确 |
| M6 开工前 | ~~评审 FDS 设计文档；告知你常做的运动类型~~ → ✅ 已答（2026-10-14）：设计按控制方裁定批准（自主运行）；常做的运动类型（以及固件会不会产出新版本文件）挪到收尾的真机会话里问（spec §16 Q9） |
| M7 | 真机核对时读 app 的压力日均/最高/最低、站立小时数、中高强度分钟数、活动卡路里 |

---

## 7. 本路线图里我做的决定（可推翻）

- M2 开头插入"日语义统一"（M2a），不直接上睡眠。
- T1 合规提前到和 M2 同期，因为仓库在 GitHub 上公开。
- M3 的心率完整性调查设上限时，查不出就记为已知限制，不阻塞后续。
- M6 单独写设计文档，且只先覆盖 record + GPS 两种 proto。
- T5 密码登录推迟。
- 其余顺序严格沿用设计 §4.6/§10。
