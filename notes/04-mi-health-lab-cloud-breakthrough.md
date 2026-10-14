# mi-health-lab 第 06–08 轮：云端只读已真机打通（对我们的增强）

续 notes/03。来源同为 `C:\dev\mi-health-lab`（Codex 并行调查）。只引用其反编译/协议结论与已验证的请求形状；不复制其 `private/` 下的真实健康数据与凭据。

## 头条：独立云端只读 **已验证成功**
对方用**电脑 + 本人扫码会话**，无需持续连手机、无需 root，**实际成功读取**了本人：步数日汇总(3 页分页一致)、心率日汇总、睡眠(2 段/15 阶段，与本地逐项一致)、血氧(日汇总+测量点)、体重(历史记录)、以及一条运动记录的列表+FDS record/GPS 文件(下载→AES 解密→解析)。
→ 这把我们 M0/M1 从"摸索"变成"移植 + 核对"。两边都还没做的只剩：全历史完整性、会话自动续期、写入。

## 1. 登录：扫码登录(QR)已跑通——比密码登录更干净
路径：`GET https://account.xiaomi.com/longPolling/loginUrl?sid=miothealth` → 用户在官方页/设备扫码确认 → longPolling 拿结果 → `processLoginContent` 取 `ssecurity/nonce/cUserId/passToken/location` → STS 换 `<sid>_serviceToken`。
- STS 的 clientSign = `Base64_nowrap(SHA1("nonce="+登录nonce十进制+"&"+ssecurity))`（ssecurity 原字符串拼接；**注意**这与健康请求的 RC4 派生密钥是两回事）。
- 对方脚本：`evidence/tools/probe_qr_session.py`；会话存 `private/auth/.../session.json`。
- 对我们的意义：**用户完全不用把密码给任何人**——扫码即可。建议把 QR 登录作为首选登录方式（密码登录仍可作为备选）。会话可复用；真实过期/自动续期未验证（续期依赖账号 SDK，不能只凭 serviceToken+security 自助续）。

## 2. 已验证可用的只读端点与请求/响应形状
base `https://hlth.io.mi.com/`，地区 cn 不加前缀；GET 的业务参数都放在**查询参数 `data`**（JSON 字符串，经签名/加密层）。

**(a) 日汇总** `GET /app/v1/data/get_aggregated_fitness_data_by_time`
`data = {tag:"daily_report", key, reverse, limit, next_key, start_time, end_time}`
| key | value 对象 | 关键 wire 字段 |
|---|---|---|
| `steps` | 日步数 | time, steps, distance, calories（步数三页分页实测一致） |
| `heart_rate` | CloudHrReport | avg_hr/max_hr/min_hr/avg_rhr、各区间 *_hr_zone_duration、latest_hr(HrItem)、abnormal_hr_count。**云端值不做日志那种 ×5，直接是 bpm** |
| `spo2` | CloudSpo2Report | avg_spo2/max_spo2/min_spo2、lack_spo2_count、latest_spo2 |
| `sleep` | CloudSleepReport | total_duration、sleep_deep/light/rem/awake_duration、sleep_stage、sleep_score、avg_hr/spo2、segment_details[]（每段 bedtime/wake_up_time/timezone(15min)/duration/各时长/awake_count/avg_breath/sleep_efficiency） |

**(b) 原始记录** `GET /app/v1/data/get_fitness_data_by_time`
`data = {key, start_time, end_time, reverse, next_key}`（旧链用 **Unix 秒**；HealthKit 新链才 ×1000 毫秒）
响应 `data_list[]`，每条 `{sid,key,time,value(JSON字符串),zone_name,zone_offset,deleted}`；`value` **再解析一次 JSON** 即得对象（不是 Base64/Protobuf）。
原始 key（`FitnessPersistKey`）：
- 心率：`heart_rate`(普通)、`single_heart_rate`(手动)、`resting_heart_rate`(静息) → `HrItem{time,bpm,type}`（bpm 不除 5）
- 睡眠：`sleep` → `SleepSegmentReport`（含 `items:[SleepStateItem{start_time|start, end_time|end, state}]`；state 常量 0 非睡/2 深睡/3 浅睡/4 REM/5 清醒/6 trace；逐秒 hr/spo2 数组是 transient，不随此 JSON 返回）；`watch_daytime_sleep`/`watch_night_sleep` → 另一结构 `DayNightSleepReport`，不套 sleep 模型
- 血氧：`spo2`、`single_spo2`(手动)、`low_spo2`(异常) → Spo2Item

**(c) 运动列表** `GET /app/v1/data/get_sport_records_by_time`
`data = {category?, start_time, end_time, reverse:true, next_key?, limit:50}`（秒）
响应 `{sport_records[], has_more, next_key}`；每条 `SportReportModel{sid,key,time,value(JSON=SportBasicReport 约200字段),category,zone_name,zone_offset,deleted,cheat_info,update_time,watermark}`。

**(d) 运动明细/轨迹 = FDS** `GET /healthapp/service/gen_download_url`
- **签名路径坑**：实际发送 `/healthapp/service/gen_download_url`，但签名输入路径要**去掉 `healthapp/` 前缀后再补回前导 `/`** → `/service/gen_download_url`。只去前缀不补斜杠会签名错。
- `data = {did:<记录sid>, items:[{timestamp:<int>, suffix:<派生>}]}`；
  `suffix = b64url_nopad(LE(timestamp)||int8(tz)||uint8(((1<<7)+(proto_type<<2)+fileType)&0xff)) + "_" + b64url_nopad(SHA1(sid))`，fileType：明细=0 / GPS=2。
- 响应 map，key=`suffix_timestamp`，值 `{url,obj_name,obj_key,method,expires_time}`。
- 下载体解密：对 url GET 取正文字符串 → Base64(flag11) 解码 → **AES/CBC/PKCS5**，key=URL-safe b64 解码 obj_key，**固定 IV = ASCII `1234567887654321`**。下载主机**不转发会话 Cookie、不跟随重定向**。
- 运动二进制：头部(uint32 LE 时间戳, int8 时区(15min), version, protoType, 控制字节) + body；schema 在 APK `assets/schemas.zip`(72 项，`22→outdoor_foot`,`0→record`,`2→gps`)；解析前 `constructSchemaBinaryData` 会改写头部。对方已离线解出 record v8 / GPS v2。

**(e) 写入（仅静态，未执行）**：POST `/app/v1/data/up_fitness_data`、POST `/app/v1/data/delete_fitness_data`。与我们"本期只读"一致。

## 3. 签名/加密：三方 + 真机验证通过
与 notes/01、02 完全一致（nonce 12 字节 → SHA256 派生 K → RC4 丢 1024 字节、字段共享连续状态 → SHA1 签名 → 响应新建 RC4 解密）。对方做了 **31 项 Java↔Python 离线核对 + 真实服务器成功响应**。`@Secret` 默认：loginPolicy=2、encryptResponse=true、filterSignatureKeys=["data"]。
**重要坑**：签名/鉴权错误会返回 **HTTP 401 但业务 code=0** → 判断成功要看 HTTP 状态，不能只看 code。

## 4. 必须带进我们设计的真实限制（对方已踩坑，别重犯）
- **原始心率完整性问题（明确未通过）**：按时间窗查 `get_fitness_data_by_time` 会漏点（同日分两段 17+70=87 < 整日 98；可复现），服务端原因未知。候选更可靠做法：仿 app `pullFullPullFitnessData`——**省略 start_time、保留 end_time/reverse/next_key 翻页**（但全历史 has_more=false 未达成，仅候选）。→ 我们 SDK 读原始心率要显式处理/报告完整性，别假设窗口查询=完整。（以上为当时的状态，已被 notes/08 取代：2026-10-14 已定位并修复。）
- **时间语义按字段分别处理**：日汇总 `time` 是 UTC 零点 vs 本地零点差 28800s（步数）；嵌套 latest HR 的 time 经 `ReportConvertor` 平移；睡眠走另一条 `sleepSegmentToLocalTime`。**不能全局统一 ±8h**。`timezone` 是 15 分钟单位，不是秒。
- **聚合算法未知**：血氧日均 95 ≠ 点简单平均 96；心率/步数来源融合也未知。别为凑数硬套系数。
- **会话续期未验证**：401 可能是签名错也可能是过期，不能一遇 401 就让用户反复扫码。
- **体重身体成分**：可用样本里成分字段为 0，真实体脂/肌肉等未验证。
- **本地库**：文件名 `fitness_data`、41 张表、DDL 在 `evidence/storage-ddl-08.sql`——但仍需 root 才能读真机库（只读云端不受影响）。

## 5. 可复用材料（在 mi-health-lab 下，供我们移植时参考）
- 协议参考实现：`ProtocolReference08.java`、`evidence/tools/verify_protocol_08.py`、`evidence/protocol-validation-08.json`（31 项核对）。
- 研究探针：`evidence/tools/probe_qr_session.py`、`probe_steps_readonly.py`、`probe_confirmed_readonly.py`、`fetch_personal_fds.py`（有范围/停止条件，非正式客户端）。
- 真机样本（私有，仅本地核对用，不进我们仓库）：`private/exports/cloud-steps-phase06`、`cloud-health-phase07`、`cloud-raw-phase08`。
> 这些是 Codex 产出，我们当"参考/真值来源"用；移植进我们 SDK 时按我们架构重写、并保留 MIT/出处说明；真实凭据与健康数据不进我们项目。

## 6. 对我们方案的影响（仍不改架构）
- 路径 B 从"高置信设计"升级为"**真机端到端验证通过**"；M0 冒烟大概率直接过。
- **登录建议改以扫码(QR)为首选**（用户连密码都不用给）——仍符合之前"你本地登录、把 token 给我"的约定。
- 数据域清单 + B 层字段语义基本现成（上面 2、4 节），第一批核心集的探测量大幅下降。
- 验证策略多两条独立真值来源：免 root 日志(notes/03) + 对方已核对的真机样本。
- 明确纳入已知限制：原始心率完整性、时间语义分字段、401/code=0 坑、会话续期未知。
