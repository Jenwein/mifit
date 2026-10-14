# 设计文档：小米运动健康数据 SDK（mifit-sdk）

- 日期：2026-10-13
- 状态：待确认（brainstorming 产出，尚未开始实现）
- 目标读者：接手实现的开发者
- 相关：静态分析见 [notes/01-static-analysis-findings.md](../../../notes/01-static-analysis-findings.md)；参考项目分析见 [notes/02-reference-mi-fitness-sync.md](../../../notes/02-reference-mi-fitness-sync.md)

---

## 1. 意图与为什么（Intent & Why）

小米运动健康（`com.mi.health`）把用户的运动与健康数据同步到小米云，但没有向个人开放可直接使用的公开 API。本项目通过逆向，**把"本该由小米官方开放、但未开放"的能力自己补齐**：让任何一个小米用户，凭自己的账号凭证，就能把自己在 app 里能看到的全部运动健康数据取出来，作为进一步开发应用、做数据处理的底座——无需自建硬件、自行采集、自搭数据体系，直接站在小米官方的数据生态上。

核心主张：**"app 里能看到什么，接口就能取到什么。"**

## 2. 目标 / 非目标 / 反目标

### 数据来源（已确认 2026-10-14）
SDK 的数据来源是**小米云端**：日常指标/运动列表走 `https://hlth.io.mi.com/app/v1/...`，运动逐秒/GPS 走云端 FDS 对象存储。读取的是"app 已通过'同步云端'上传到小米云的、属于本人账号的数据"。
- **不**以手机本地 Room 库为源（需 root，已排除）。
- 免 root 的 adb 日志读取仅作**验证时的真值对照**，不作为 SDK 取数来源（见 §8）。
- 真机已验证：扫码会话 + 旧云端只读链（line 1）可读步数/心率/睡眠/血氧/体重/运动记录（notes/04）。

### 目标（Goals）
- 一个 **Python 库**：输入=某小米用户自己的账号凭证，输出=该用户的运动健康数据，封装掉登录、地区路由、签名、加密、分页、解密等全部底层细节。
- **全量为目标边界**：覆盖 app 可见 + 逆向可发现的所有数据域（见 §6 清单）。
- **分批交付**：先跑通高频核心集（步数、睡眠、心率、运动记录；血氧、体重紧随），再逐步补齐其余数据域；架构上"新增一类数据只需加一小块"。
- **数据形态 = A 为主 + 高频加规整层**：
  - A（忠实透传）：解密后的 JSON 原样返回，字段/结构与小米一致，不丢信息。
  - B（规整模型）：仅对高频数据类型，在 A 之上再提供一层我们定义的、好懂的类型化模型。
- 配套 **CLI**：能把任意数据域导出成 JSON/文件，供不写代码也能使用、并用于验证。
- 每类数据都以**真实账号实测、与 app 显示核对**的方式验证正确性。

### 多数据源（Multi-provider）定位（已确认 2026-10-13）
长期愿景是支持更多运动健康 app（华为/Zepp/佳明……），让 B 规整层成为"与 app 无关的统一健康数据模型"、各 app 作为 provider 接入。**但本期聚焦小米、做扎实；通用层不现在抽象**（只有一个数据源时抽象缺乏验证、易过度设计）。本期只做 **"轻量预留"**——见 §12，几条近零成本、避免将来被架构卡死的约定。接第二个 app 时再正式把通用层抽出来，并接受那时会有一轮"把 B 提升为共享核心"的机械式重构。

### 非目标（Non-Goals，本期不做，但不排斥将来做）
- **不**现在搭多 provider 的插件/注册框架、不写投机性的通用基类/抽象（§12 的"轻量预留"是约定与边界，不是基础设施）。
- **写 / 改 / 删** 云端数据：本期纯读。写入本质上来自采集设备，人为写入既不自然、风控风险也最高；留待以后单独调研可行性与合理性。
- 本地数据库路径（`/data/data/com.mi.health/...`）：需要 root，用户明确不 root，排除。
- 设备内 IPC（ContentProvider）路径：被限制为系统/小米签名/白名单应用，排除。
- Strava / 第三方同步、GPX/TCX/FIT 导出等"下游应用"：不属于本 SDK，交给上层使用者。
- 图形界面。

### 反目标（Anti-Goals，出现即算失败）
- **让规整层（B）悄悄失真**：B 的任何字段含义都必须经真实数据核对；无法确证含义的字段**不进 B**，只留在 A 的原样数据里。
- 把某个具体上层用途（如 Strava 同步）的结构耦合进 SDK，导致"全量底座"退化成"专用工具"。
- 触发账号风控导致用户被封/被限：设计上必须默认低频、只读、尽量贴近官方客户端行为。
- 明文或不安全地处理用户凭证。

## 3. 约束（Constraints）

- **凭证与隐私**：SDK 只访问"调用者自己账号"的数据。密码仅用于本地登录换取 token，绝不落盘明文、绝不外传；token/ssecurity 等敏感凭证存本地、默认权限收紧、纳入 `.gitignore`。登录流程里 Claude 不经手用户密码（由用户本地运行/输入）。
- **风控**：默认只读、低频、带官方风格请求头；对 401 做有限次 token 刷新重试（参考 app 的 `VerifyToken` 行为：≤3 次）。不做并发轰炸式抓取。限流默认值（我的判断，均可配置）：**串行、不并发**；请求间插入小延迟（默认 ~1s、带抖动）；按时间分页、单页窗口保守（如按天/按周分块，`limit` 取官方默认量级如 50）；**"全量/长区间导出"默认分块节流并可被显式开启**，不作为无节制的默认行为。这些是"尽量贴近官方客户端、降低被判异常的概率"的保守默认，**不构成"绝对不会触发风控"的保证**。
- **协议会变**：小米可能随时改接口/签名/风控。SDK 要把"端点、签名、key 清单"等易变点集中、可配置、可快速维护，并接受"会时不时需要维护"。
- **合规**：自动化访问可能触及小米用户协议，属个人自用灰色地带；SDK 面向"用户访问自己的数据"，不采集/聚合他人数据。
- **Python**：3.12+（与参考项目一致）。第三方依赖尽量少（`requests` 等成熟库）。
- **纯静态不足以定全部字段含义**：部分 `key`/`value` 结构必须用登录后的真实数据探测确认。
- **云端数据依赖 app 的"同步云端"**（已确认 2026-10-14）：我们读的是 app 定期上传到小米云的数据。若用户关闭云同步、或最新数据尚未同步，云端会缺失或滞后；历史覆盖取决于过去累计同步量。这是数据源的固有前提，SDK 无法控制，只能如实反映（例如明确"截至云端最新同步"的边界，不臆造缺失数据）。
- **已知完整性问题——原始心率**（见 notes/04）：按时间窗查 `get_fitness_data_by_time` 会漏点（可复现，根因未定位）。SDK 需如实暴露"窗口查询可能不完整"，并提供候选的全量翻页读法（省略 start_time、靠 next_key 翻页）；根因在实现期用真实会话做对照实验定位，不阻塞整体方案。

## 4. 已确认的"怎么做"（决定与依据）

以下为 brainstorming 中与用户共同确认的决定：

1. **形态**：Python 库为主 + 配套 CLI 导出。（用户确认）
2. **范围**：纯读优先；写/改/删延后调研。（用户确认）
3. **数据形态**：A 忠实透传为主 + 高频数据加 B 规整层。（用户确认）
4. **全量策略**：设计文档列全量数据域清单作为目标边界；实现分批交付、核心集先稳、架构可扩展；逐类真实账号核对。（用户确认）
5. **参考项目 kevinkwee/Mi-Fitness-Sync 的定位**：参考而非默认基座；由实现者按"方向契合度"判断复用/重写（§5 给出我的判断）。（用户确认）
6. **第一批核心集顺序**：步数 → 睡眠 → 心率 → 运动记录（汇总）；血氧、体重紧随。（默认，用户未否决）

## 5. 对参考项目的复用判断（我的判断，实现者可调整）

kevinkwee/Mi-Fitness-Sync 为 **MIT**，可自由复用/修改。其与我们独立静态分析**逐条吻合**，可信度高。我的判断：

**建议复用（vendoring 进本项目并改造，保留 MIT 版权声明）——这几块是已验证的"硬通货"：**
- 账号登录流程（`auth/client.py`）：`serviceLogin` 取 meta → `serviceLoginAuth2`（密码 MD5 大写）→ 解析 `passToken/cUserId/ssecurity/nonce/location` → STS 换 `{sid}_serviceToken`；含验证码(87001)、短信二次验证(81003/step2)、通知确认(notificationUrl) 处理。sid 固定 `miothealth`。
- 签名/加密（`activity/crypto.py`）：`signedNonce=b64(SHA256(b64dec(ssecurity)+b64dec(nonce)))`；参数 RC4（key=b64dec(signedNonce)，前置消费 1024 个 0 字节）；`signature=b64(SHA1("GET&<path>&<sorted k=v>&<signedNonce>"))`；另发 `rc4_hash__`；响应为 b64(RC4) JSON。
- 传输与地区路由（`activity/transport.py` + `region_mapping.py`）：cn 用原 host、其它加 `<region>.` 前缀 + `region_tag` 头；cookie=`serviceToken/cUserId/userId/locale`；IP 查地区端点；国家码→region 映射表（247 条）。
- `@Secret(pathPrefix=...)` 的签名路径裁剪规则（`miothealth` 服务 pathPrefix="" → 用完整路径签名；`healthapp/` 前缀服务要裁掉前缀）。

**建议重写（不照搬其组织方式）：**
- 整体架构：参考项目围绕"运动记录→Strava"组织；我们要围绕"通用数据域目录（registry）+ A 透传 + B 规整层"组织。
- CLI：重写为"按数据域导出"的通用 CLI，去掉 Strava/同步等下游逻辑。
- token 存储：重用其"本地缓存 + 过期刷新"思路，但凭证存储要满足 §3 的安全约束。

**可直接拿来做素材：** 其 `docs/*-findings.md`（auth/activity/fds）与 APK 内 `assets/server_config/servers.json`（地区映射源表）。

> 实现者注：优先 **vendoring 改造**（把需要的模块抄进来、改命名空间、补测试、保留 MIT LICENSE），而非 `pip install` 依赖它——因为我们要大改架构且它是个 app 而非发布到 PyPI 的库。

## 6. 数据域清单（全量目标边界）

数据主要经两类云端入口获取（base `https://hlth.io.mi.com/app/v1/`，海外加地区前缀）：

**入口一：健康/日常数据** `data/get_fitness_data_by_time`（及 `..._by_watermark`、`get_latest_fitness_data`）
- 请求：`{key, startTime(秒), endTime, reverse, next_key}`；响应 `data_list[]`，每条 `{sid,key,time,value(JSON),zone_name,zone_offset,deleted}` + `next_key` + `has_more`。
- `key` 取值需**逐个探测确认**。已知/候选（来自参考项目 + 本地 Room 表名线索，待实测校正）：
  - 步数 / 日常活动（`step_record` / `daily_report` / 候选 `huami_regular_record`）
  - 心率：静息 `huami_rest_heart_rate`、手动 `huami_manual_heartrate_record`、全天（`hr_record`，key 待定）
  - 睡眠（`sleep_segment`，key 待定）
  - 血氧：手动 `huami_manual_measure_record`、低血氧 `huami_low_spo2`、全天（`spo2_record`，key 待定）
  - 压力 `huami_all_day_stress_record` / `huami_single_stress_record`
  - PAI `huami_pai_record`、活力 `huami_vitality_index`、训练负荷 `huami_training_load_record`
  - 卡路里/能量 `huami_app_estimate_record`、站立、VO2max、体温、房颤 `huami_atrial_fibrillation_record` 等
  - 体重/身体成分（`weight_item_record`：weight/bmi/bodyFatRate/moistureRate/boneMass/basalMetabolism/muscleMass/proteinRate/visceralFat；key 待定）
  - 用户档案/目标（`user_info_profile`：身高/体重/各项目标；入口待确认，可能另有接口）

**入口二：运动记录** `data/get_sport_records_by_time`（及 `..._by_watermark`）
- 汇总：响应 `sport_records[]`，每条 `value` 是 `SportBasicReport`（约 200 字段：类型/时长/距离/卡路里/步数/心率区间/配速/海拔/训练效果/VO2max/游泳/功率/力量/课程/铁三嵌套等）。
- 逐秒明细 + GPS 轨迹：在 **FDS 对象存储**，经 `healthapp/service/gen_download_url` 取预签名 URL，下载 **AES 加密二进制 blob**，按 `proto_type`(1–25) 分类解析。细节见参考项目 `docs/mi-fitness-fds-findings.md`。

**其它**：设备列表/信息、日报/周报聚合（`data/get_aggregated_fitness_data_by_time`）、统计（`get_stat_data_by_time`）、医疗/项目数据等——纳入全量边界，分批补齐。

> "全量"的操作性定义：实现者需在实现期系统性枚举 app 实际用到的全部 `key` 与端点（结合本仓 `evidence/` 的类/字符串证据 + 真实账号探测），形成一份**受控的数据域目录**并持续补充，而非一次性保证绝对完备。

## 7. 建议架构（留给实现者，可调整）

建议 Python 包（名称我暂定 `mifit`，实现者可改）：

```
mifit/
  auth/        登录 + token 缓存（vendored 自参考项目并改造）
  crypto/      nonce/signedNonce/RC4/签名（vendored）
  transport/   OkHttp 等价的会话层：地区路由、cookie、签名拦截、401 重试、分页
  fds/         FDS 预签名下载 + AES 解密 + proto_type 解析（分批）
  domains/     数据域 registry：每个数据域一个模块，声明 {入口, key, 解析/规整}
  models/      B 规整层：高频数据的类型化模型（dataclass）
  client.py    顶层门面：MiFit(credential).steps(...)/sleep(...)/workouts(...)/raw(key,...)
  cli/         通用导出 CLI：mifit export <domain> --from --to --json out.json
  credentials/ 凭证存储（本地、安全、.gitignore）
tests/         每个解析器配真实样本的回归测试（样本脱敏）
```

关键设计点：
- **数据域 registry**：新增一类数据 = 新增一个 domain 模块（声明用哪个入口、哪个 key、如何从 `value` 解析/是否有 B 模型）。顶层门面与 CLI 自动暴露已注册的 domain → 满足"可扩展、分批交付"。
- **A 永远可得**：任何 domain 都能拿到原样解密 JSON；B 是可选叠加。
- **传输层统一**：所有 domain 复用同一套签名/地区/分页/重试，避免每类数据各写一遍。
- **凭证抽象**：支持"用户名密码登录换 token"与"直接传入已有 token"两种入口，便于用户选择不同的取 token 方式。
- **登录默认按"可能交互"设计（我的判断）**：登录可能触发验证码(87001)/短信二次验证(81003)，因此 CLI/凭证层从一开始就按"可提示用户输入验证码/短信码"来设计；首次登录成功后**缓存 token 到本地安全存储**，后续调用（含无人值守/定时场景）复用缓存、仅在过期时刷新或要求重新登录。"直接传入已有 token"入口需在文档中给出明确的取得方式（本 SDK 的 `login` 命令导出，或用户自有工具），否则该入口形同虚设。

## 8. 验证策略

- 单元/回归：对签名、RC4、分页、每个解析器用**脱敏真实样本**做测试（CI 可跑，不需要真账号）。
- 端到端：用用户本人账号登录一次（纯账号密码、只读），逐个数据域拉取并与 app 核对。
- 探测产物沉淀：把确认的 `key`↔数据类型↔`value` 结构写进 `notes/`，作为数据域目录的权威来源。

### 8.1 "核对通过"的判定口径（我的判断）
app 显示的是**聚合/取整后**的数字（见 notes/01 §1.1：读取走 `DbGetter` 聚合），而云端 `get_fitness_data_by_time` 返回的是**原始 intraday 记录**。因此 domain "done" 的默认口径定为：
- **A 层（必须）**：该数据类型的原始记录能被正确拉取、解密、解析，覆盖所指定时间范围、分页完整、字段与小米一致且可解释。
- **与 app 数字对齐（尽力而非严格）**：对聚合方式简单的指标（如步数按日求和）复算到日级并与 app 比对，允许取整误差；对聚合复杂的指标（睡眠分期、全天心率曲线等）只要求"原始记录在量级与形态上与 app 一致、可复现出 app 展示所需的数据"，**不要求逐值精确等于 app 显示**。
- 若某指标需要复算聚合才能对齐，该聚合逻辑**只在对应的 B 规整层**实现，不污染 A 层。

> 若用户希望更严格（要求所有核心指标日级数字与 app 精确一致），需复算更多 app 端聚合，工作量显著增加——此为可调项，默认取上面的宽口径。

## 9. 留给实现者的开放项（Open Questions for Builder）

- 具体 `key` 清单与各 `value` JSON 字段语义：需实现期用真实账号探测确认（本设计给了候选与线索，未逐一坐实）。
- `user_info_profile`/目标/设备信息/日报周报的确切云端入口与参数。
- FDS `proto_type` 1–25 的解析覆盖程度与优先级（可在核心集之后分批）。
- token 的本地存储格式与加密方式的具体实现（满足 §3 约束即可）。
- 包名、公开 API 命名的最终定稿。

## 10. 规模与下一步

规模：**项目（带设计文档）**。本文件经确认后，下一步进入 `superpowers:writing-plans` 产出实现计划。

实现里程碑（建议）：
- **M0 冒烟验证（go/no-go）**：先用参考项目的 vendored 登录+签名，对真实账号做一次最小请求，确认"该套 auth/crypto/transport 今天对小米线上仍然有效"。若无效，M1 的性质从"接好已知可用的签名器"变为"从 APK 重新推导签名器"，时间/风险预算显著不同——因此 M0 必须最先做。**M0 依赖§11 的开发期账号访问方式确定后才能执行。**
- **M1 核心闭环**：传输 + 登录 + `步数` 一个 domain 端到端打通并与 app 核对。
- **M2+**：按 睡眠 → 心率 → 运动记录（汇总）→ 血氧 → 体重 顺序补齐核心集，再扩展其余数据域与 FDS 明细。

## 12. 轻量预留：为将来多数据源留的几条低成本约定

目标是"现在几乎不花额外成本，将来接第二个 app 时不被卡死"。只做下面这些，**不做**比这更多的通用化：

1. **B 模型用 app 中立命名、独立成层**：规整模型放在独立模块（如 `models/`），命名/字段用通用健康术语（如 `SleepRecord(deep_minutes, rem_minutes, light_minutes, awake_minutes, ...)`、`Workout(sport_type, duration_s, distance_m, calories, avg_hr, ...)`），**不带小米专有词**、不硬编码小米枚举值。可自由时让词汇靠拢 Health Connect 等现成标准，便于将来对齐。
2. **"接入"与"映射"分层清晰**：把小米专有的 ingestion（auth/crypto/transport/FDS/原始 A）集中在一处，把"小米原始 → 中立 B"的映射单独成一层（映射层是将来每个新 provider 各写一份的"接缝"）。B 模型内部不出现小米字段名。
3. **小米相关代码聚拢在一个子命名空间**：本期不建 `providers/` 目录树，但把小米专有部分收拢在一个清晰的子包边界内，使将来"整体挪到 `providers/mifitness/` 之下"是机械式移动而非重写。
4. **数据域 registry 只做本项目够用的程度**：保留"新增一类数据=加一小块"的扩展点（本就需要），但不为"多 app"提前泛化它。
5. **文档标注接缝**：在代码与 notes 里明确标出"这里是将来抽通用层的接缝 / 这里是小米专有"，让将来的人一眼看懂边界。

**明确不做**：provider 注册表、插件加载、跨 app 的抽象基类、统一配置框架、为第二个 app 预留的空接口——这些等真有第二个数据源、有真实数据佐证时再做。

> 命名：本期包名仍暂定 `mifit`（小米优先）。将来真要多数据源时，把中立的 `models/` 提升为共享核心、小米部分下沉为一个 provider，并视情况改包名——这一轮机械重构的成本是本决定明确接受的。

## 11. 开发期如何取得可用 token（已确认）

本设计的"探测 `key`、建 B 模型、与 app 核对"依赖**开发期能对真实账号发起已认证请求**；§3 约定 Claude 不经手用户密码。

**用户决定（2026-10-13，2026-10-14 更新登录方式）**：由**用户在本地运行登录脚本**取得会话，把 `serviceToken` + `ssecurity`(+ `cUserId`/`userId`/region) 粘贴给 Claude，Claude 用它对真实账号做探测与验证。

**登录方式：扫码登录(QR)为首选**（用户确认 2026-10-14）——`GET account.xiaomi.com/longPolling/loginUrl?sid=miothealth` → 用户用小米 app/官方页扫码确认 → longPolling → STS 换 `<sid>_serviceToken`（mi-health-lab 已真机验证，见 notes/04）。**用户全程不需要把密码交给任何人/任何脚本以外的环节**。密码登录（notes/02 的 `serviceLoginAuth2` 流程）作为备选。两种方式产出的 token 一致，后续使用相同。

对实现的影响：
- **M0 冒烟验证所需的"登录脚本"是实现的第一个产物**（vendored 自参考项目的 auth 流程，纯账号密码→token），用户跑它拿到 token 后，后续探测/验证才能进行。
- 开发可"较自主"推进：拿到 token 后 Claude 可自行探测 key、建模型、与 app 核对，减少逐步人工来回。
- token 是敏感凭证：进入对话后视为用户自有数据，仅用于本项目开发验证；放入本地安全存储与 `.gitignore`，不写入仓库/证据文件。
