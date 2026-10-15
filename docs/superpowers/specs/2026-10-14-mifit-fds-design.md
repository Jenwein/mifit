# 设计文档：M6 运动明细 + GPS 轨迹（FDS）

- 日期：2026-10-14
- 状态：草稿（自主运行产出）。用户审批一步按"approved by controller ruling: autonomous run"处理；只有用户能回答的问题收在 §16，留到收尾的真机会话。已做 builder check（独立子代理通读）：提出 3 个问题——四维有效位的位序（已由 APK 回答，写进 §4.7）、推送前能否用本机真实文件做闸门（我方裁定，D25）、与 T3 续传/分块的关系（我方裁定，§7.3、D24）；没有必须由用户回答的。
- 目标读者：接手写实现计划（`superpowers:writing-plans`）和实现的开发者。
- 上游：[路线图](../../roadmap.md) §3 M6；[总设计](2026-10-13-mifitness-sdk-design.md) §2（反目标）、§3（约束）、§5（复用）、§8.1（核对口径）、§12（中立命名）；notes/04 §2(d)；notes/10（运动列表）；notes/11 §3（`healthapp/` 路径与签名规则）；参考项目文档 `reference/mi-fitness-sync/docs_mi-fitness-fds-findings.md`（背景，见 §15 D17）。
- 依据：APK 3.59.1 静态分析（REA/JADX，产物在 `work/m6/apk/`）；真机只读探测 8 次请求（`work/m6/`，均 git-ignored）。本文所有数值都是合成的，或只给形状、大小、比值；不含真实坐标、时间戳、`sid`、URL、密钥或健康数值。

> 文中"record 文件 / GPS 文件"指 FDS 的 `fileType` 0 和 2。路线图把它们写成"proto 0 / proto 2"是笔误：`proto_type` 是运动协议号（本账号跑步 22、骑行 23），`fileType` 才是 0=record、1=report、2=gps、3=hrr（心率恢复）。

---

## 0. 一页结论

- **链路真机端到端跑通**（2026-10-14，8 次只读请求）：运动列表 → `healthapp/service/gen_download_url`（一次请求带 4 个文件项，全部返回）→ 预签名 URL（`https://cnbj1.fds.api.xiaomi.com/...`，约 30 分钟有效）→ 不带 Cookie、不跟随重定向下载（4/4 成功）→ 正文是一个 **JSON 字符串字面量**，里面是 base64url（无填充）→ AES-128-CBC/PKCS#7，固定 IV → 明文头部的时间戳、时区、协议号与该运动的汇总逐项相等。
- **解析不走 `schemas.zip`**：账号上的文件版本是 record v8（跑步）、record v5（骑行）、GPS v2，`schemas.zip` 里没有这些版本；app 对它们走的是"原生"老解析器（`FitnessDataParser.parse` → `isSchemaVersionSupport` 为假 → `nativeParse`）。本期按 APK 老解析器的字段表手写这三种布局，`schemas.zip` 只当字段命名和新版本的参考文档，不随包分发。
- **离线核对已经很强**：解析结果与同一次运动的汇总（`SportBasicReport`）对比——步数之和、骑行卡路里之和、距离之和、心率最大/最小值都**精确相等**；心率均值、配速极值、踏频最大值、骑行速度最大值在取整误差内；GPS 轨迹（只算运动中的段）长度 / 汇总距离 ≈ 0.99（跑）、1.02（骑）。
- **公开形态**：`MiFit.workout_detail(start, end, *, track=True)` + 注册数据域 `workout_detail` + `mifit export workout_detail --from --to [--no-track]`。每次运动一条 `WorkoutDetail(workout, samples, track)`；B 层 `WorkoutSample`（逐秒）和 `TrackPoint`（轨迹点）只放核对过的字段。不做 GPX/TCX/FIT（总设计 §2 反目标）。
- **新依赖**：`cryptography`（硬依赖）。
- **`sid` 怎么拿**：不改 M4。`workout_detail` 自己按时间窗重读运动列表的外层行（与 `workouts` 共用一个内部函数），`sid` 只在内存里用来算文件名和请求参数，不进任何输出。单次运动用 `client.workout_detail(w.start_epoch_s, w.start_epoch_s)`。

---

## 1. 意图与为什么

M4 给了"每次运动一行"的汇总。用户在 app 里还能看到每次运动的**逐秒曲线**（心率、配速/速度、步频、海拔）和**地图轨迹**；这些不在汇总接口里，而在 FDS 对象存储的二进制文件里。按总设计"app 里能看到什么，接口就能取到什么"，M6 把这部分补齐，作为下游做训练分析、可视化、自建导出的底座。

## 2. 目标 / 非目标 / 反目标

### 目标
1. 对本账号做过的两种 GPS 运动——户外跑（`sport_type` 1，`proto_type` 22）和户外骑行（`sport_type` 6，`proto_type` 23）——取回 record 文件（逐秒数据）和 GPS 文件（轨迹）。
2. 完整的 A 层：解密后按 app 的字段表解码出的每个字段原样返回（未缩放的存储值、源字段名），不丢信息。
3. B 层：`WorkoutSample` 和 `TrackPoint`，只放 §4.9 核对过的字段。
4. 安全地处理预签名 URL 和密钥（§11），把下载内容当不可信输入解析。
5. 测试全部离线、用合成数据；仓库里不出现真实文件。

### 非目标（本期不做，以后可以做）
- 其它 `proto_type`（例如爬楼机 300、力量训练 308，都是 `proto_type` 8 自由训练，无 GPS）和其它 `fileType`（1 report、3 hrr）。对它们 M6 不发 FDS 请求，只在结果里说明（§12）。
- 本账号没有的文件版本：record v9（跑）/ v6、v7（骑）、GPS v3–v5 等。遇到时只解析头部并加 note（§12）。`schemas.zip` 里有这些版本的定义，见 §15 D2 的后续路径。
- 新线路（`CloudSyncLineRouter.isNewWebLine()` 为真时走的 `HealthKitDataRequest.genFDSDownloadUrl`）和毫秒时间戳的 10 字节文件键（`genDataIdKeyBytes` 的 ≥13 位分支）。我们读的是旧线路（line 1，与 M1–M5 相同），时间都是秒。
- 路线文件（`getRouteFDSUrl` / `downloadRoute`）、ECG、体温等其它 FDS 内容。
- 本地缓存下载结果。
- 长区间分块与断点续传（归 T3；本域只设单次调用上限，§7.3）。

### 反目标（出现即算失败）
- **GPX / TCX / FIT 等导出**：总设计 §2 明确划给上层使用者。M6 的 CLI 只输出 `{records, raw, notes}` JSON。
- **B 层悄悄失真**：没核对过含义的字段（GPS 速度与精度、逐秒卡路里、步幅、触地/腾空、垂直振幅与步幅比、分段类型……）只进 A 层。
- **把 app 的显示时区偏移带进数据**：app 解析时把时间加上"记录时区 − 手机当前时区"（`getCurTzDiffInSec`），那是显示用的平移；SDK 一律给真实 Unix 秒，另给 `utc_offset_s`。
- **泄露凭据**：预签名 URL（带签名，30 分钟内谁拿到谁能下）、`obj_key`（解密密钥）、`obj_name`、`sid` 进入输出、日志、异常消息或 notes。
- **把下载内容当代码或可信结构**：任何声明的长度、计数都先对剩余字节做边界检查。

## 3. 约束

- 只读、低频：沿用 `Transport` 的节流（每请求 1 s + 至多 0.5 s 抖动），下载同样节流；一次运动 = 1 次签名请求 + 至多 2 次下载（外加窗口内的 1 次列表请求）。
- 签名路径规则：发送 `/healthapp/service/gen_download_url`，签名用 `/service/gen_download_url`（去 `healthapp/` 前缀、保留前导 `/`，与 M5 档案接口同一规则）。M5 已给 `Transport.get_json` 加了 `base_path` 关键字，本期直接用。
- 401 处理不变：签名请求的 401 走 T2 的续期逻辑；下载不带会话，不会 401（遇到非 200 只记 note）。
- 隐私：GPS 轨迹能定位到住处。数据是用户自己的，按需输出；文档提醒；提供 `track=False` / `--no-track` 不请求 GPS 文件。
- 测试禁止联网（`tests/conftest.py`）。
- 文件行尾 LF（见 user memory：Windows 上脚本写文件要归一）。
- 前置：M5 已合入 `main`（`get_json(..., base_path=...)`、`data_obj` 可为 `None`）；M4 已合入（`domains/workouts.py`）。T3 先合与否都可以，见 §7.3。

---

## 4. 已核实的事实

### 4.1 链路总览（APK）

```
FitnessFDSDataGetter.getSportRecordData / getSportGpsData(sid, SportBasicReport)
  → FitnessFDSUploader.downloadFromFDS(sid, FitnessDataId, line)
      genFdsUrlItem(sid, dataId) → PreSignedUrlItem(timestamp, suffix)
      CloudSyncLineRouter.genFDSDownloadUrl(sid, [item], line)
         旧线路 → FitnessFDSRequest.getFDSDownloadUrl(sid, FDSItem[])  = GET healthapp/service/gen_download_url
      map.get(item.toServerKey())           # "<suffix>_<timestamp>"
      download(preSignedUrl):
         objectKey 为空 → 抛异常（不下载）
         downloadFromUrl(url): new OkHttpClient().newCall(GET url)   # 全新客户端：无 Cookie 罐
         new AESCoder(objectKey).decrypt(bodyString)
  → FitnessDataParser.parse(dataId, bytes)
      isSchemaVersionSupport(dataId)  # ParserWayControl.findSchemaFilePath != null
        真 → constructSchemaBinaryData + schemaParse（BinarySchemaDecoder + schemas.zip）
        假 → nativeParse（老解析器：OutdoorStepRecordParser / OutdoorNoStepRecordParser / SportGpsParser）
```

注意两点 app 行为我们**有意不照抄**：OkHttp 默认跟随重定向（我们不跟随）；app 不检查 HTTP 状态码就去解密正文（我们要求 200）。理由见 §11。

### 4.2 文件标识（FitnessDataId）与 `suffix`

- 输入取自运动列表的**外层行**和解码后的 `value`（`SportBasicReport`）：
  - `sid`：外层行的 `sid`。真机上一个账号的所有行同一个值（10 位数字字符串），是数据来源设备的标识，**不是**每次运动的 id。请求里序列化为 `did`。
  - `timestamp`：`value.time`（`SportBasicReport.timeStamp`），**不是** `start_time`。真机骑行那一条 `value.time = start_time − 1`，用 `start_time` 会算错文件名；文件头里的时间戳也等于 `value.time`（§4.6）。
  - `tz15`：`value.timezone`（刻钟数，东八区 32），按 int8 写入。
  - `proto`：`value.proto_type`（不是 `sport_type`）。
  - `fileType`：0 record，2 gps。
- 键字节（秒级时间戳，6 字节，小端）：`<I timestamp> <b tz15> <B ((1<<7) + (proto<<2) + fileType) & 0xFF>`。
- `suffix = b64url_nopad(键字节) + "_" + b64url_nopad(SHA1(sid 的 UTF-8))`（Android Base64 标志 11 = NO_WRAP|NO_PADDING|URL_SAFE）。
- 响应映射的键 `server_key = suffix + "_" + str(timestamp)`（`PreSignedUrlItem.toServerKey`）。
- APK 的 `genDataIdKeyBytes` 在时间戳十进制 ≥13 位时改用 8 字节 long（10 字节键）；旧线路的 `value.time` 是秒，本期遇到 ≥13 位直接判为不支持（note）。

合成示例（`sid="1234567890"`，`time=1700000000`，`timezone=32`，`proto=22`，record）：键字节 `00 f1 53 65 20 d8`，`suffix` = `"APFTZSDY_" + b64url_nopad(sha1("1234567890"))`。实现计划里要用 Python 现算一遍再写进测试，不要照抄本段。

### 4.3 `gen_download_url` 请求与响应（真机）

- `GET https://hlth.io.mi.com/healthapp/service/gen_download_url`（非 cn 地区加地区前缀，同其它请求），业务参数只在签名加密的 `data` 里：
  ```json
  {"did": "<sid>", "items": [{"timestamp": 1700000000, "suffix": "<suffix>"}, ...]}
  ```
- 一次请求可以带多个文件项：真机一次带 4 项（跑、骑各 record + gps），4 项全部返回。
- 响应同其它接口（RC4 加密、`{code: 0, message, result}`）。`result` 是映射，键为 `server_key`，值：
  ```json
  {"url": "https://cnbj1.fds.api.xiaomi.com/<bucket>/<obj>?GalaxyAccessKeyId=...&Expires=...&Signature=...",
   "obj_name": "<76 字符>", "obj_key": "<base64url，解出 16 字节>", "method": "GET", "expires_time": 1700001800}
  ```
  `expires_time` 是 Unix 秒（10 位整数），距请求约 1800 s。
- **文件不存在时**（真机：给一次 `proto_type` 8 的运动请求 GPS 文件）：照样返回该键，但**没有 `obj_key` 字段**；对其 URL 下载得 HTTP 404。app 在 `objectKey` 为空时直接放弃，不下载——我们照做，省一次请求。

### 4.4 下载（真机）

- 4/4 返回 HTTP 200，`Content-Type: application/octet-stream`，无重定向、无 `Set-Cookie`。请求不带 Cookie、不带会话头。
- 正文是**带双引号的 JSON 字符串**：`"<base64url 无填充>"`，字符集只有 `[A-Za-z0-9_-]`。
- 大小：编码后的正文几十 KB 到约 110 KB（30–80 分钟的运动）；解密后约为其 3/4。

### 4.5 解密（APK `persist.utils.AESCoder` + 真机）

- `json.loads(正文)` 必须得到 `str` → base64url 解码（补齐 `=`）→ 长度是 16 的倍数 →
- AES-CBC，密钥 = base64url 解码 `obj_key`（APK `Base64.decode(key, 8)`，URL_SAFE；真机 16 字节，即 AES-128），IV = ASCII `1234567887654321`（`AESCoder.getInitialVector`），PKCS#7 去填充（Java 名 PKCS5Padding）。
- 真机 4/4 填充校验通过；明文第一个块里的头部（§4.6）与汇总逐项相等——第一块依赖 IV，所以这同时证实了 IV。
- app 为什么能直接解带引号的正文：Android 的 `Base64.decode` 跳过字母表以外的字符（包括 `"`）。我们不靠这个宽松行为：先按 JSON 字符串取出内容，再用严格字符集解码；为防服务端以后改成不带引号，也接受"整个正文就是 base64url"这种形式（同样严格字符集）。

### 4.6 文件头（APK `FitnessDataParser.constructSchemaBinaryData` / `parseNativeBinaryData` + 真机）

云端文件的头部是 **8 字节服务端头 + 有效性标志**：

| 偏移 | 类型 | 含义 | 真机 |
|---|---|---|---|
| 0 | uint32 LE | 时间戳（= `value.time`） | 4/4 相等 |
| 4 | int8 | 时区（刻钟，= `value.timezone`） | 4/4 相等 |
| 5 | uint8 | 文件格式版本 | 跑 record 8，骑 record 5，GPS 2 |
| 6 | uint8 | 协议号（= `value.proto_type`） | 22 / 23 |
| 7 | uint8 | 加密/压缩模式 | 4/4 为 0 |
| 8 | n 字节 | 有效性标志 | 长度按 (协议, 文件类型, 版本) 查表 |

- `constructSchemaBinaryData` 证实了这一点：它去掉前 `serverDataIdLen + 1 = 8` 字节，换成 schema 格式的头 `[ts, tz, version, genDataTypeByte, 0]`。也就是说 schema 头的第 7 字节是位域 `(1<<7)|(proto<<2)|fileType`，云端文件的第 7 字节只是协议号——**不能直接拿 `schemas.zip` 的头定义解析云端文件**。
- 有效性标志长度（APK `FitnessDataValidity`）：
  - 跑（协议 22，`getSportStepRecordValidityLen`）：v1→2，v2–4→3，v5→5，v6–7→6，v8–9→7。
  - 骑（协议 23，`getSportNoStepRecordValidityLen`）：v1–2→2，v3–6→3，v7→4。
  - GPS（`getSportGpsValidityLen`）：v1–4→1，v5→2。
  - 查不到（-1）= 不支持该版本。
- 加密/压缩模式非 0 的文件本期判为不支持（真机没见过）。

### 4.7 record 文件布局（老解析器，APK `SportRecordBaseParser.parseFourDimenData` + `OutdoorStepRecordParser` / `OutdoorNoStepRecordParser`）

**正文 = 若干"段"（每次暂停/分组一段），直到剩余字节不够一个段头。** 每段：

跑（协议 22，v8；段头 27 字节）：

| 字段（A 层名，取自 schema） | 类型 | 说明 |
|---|---|---|
| `initialAltitude` | float32 LE | 段起始海拔（米） |
| `recordCount` | uint32 | 本段点数 |
| `resumeTimestamp` | uint32 | 本段第一个点的时刻（真实 Unix 秒；第一段等于文件头时间戳） |
| `segmentInfo` | uint8 | 位 7 `pauseType`（0 暂停，1 本段结束），位 6–0 `segmentType`（间歇/课程类型） |
| `totalDistance` | uint32 | 本段距离（米） |
| `totalDuration` | uint32 | 本段时长（秒；真机每段都等于 `recordCount`） |
| `totalSteps` | uint32 | 本段步数 |
| `segmentPace` | uint16 | 本段配速（秒/公里） |

骑（协议 23，v5；段头 23 字节）：`initialAltitude` f32、`recordCount` u32、`resumeTimestamp` u32、`segmentInfo` u8、`totalDistance` u32、`totalTime` u32、`totalSpeed` u16（0.1 km/h）。

**点（每秒一个，`resumeTimestamp + i`）**："四维"字段表。每个字段在有效性标志里占 4 位（一个 nibble，高 nibble 在前）：`exists`、`high`、`middle`、`low` 三个子值的有效位。规则（`FourDimenDataParser.parseDataValid` / `parseOneRecordItem`）：
- 字段表按 APK 数组顺序，只取 `supportVersion ≤ 文件版本 ≤ maxSupportVersion` 的项；nibble 只为入选的项递增。第 k 个入选字段用有效性标志第 `k // 2` 字节的高 4 位（k 为偶数）或低 4 位（k 为奇数）。
- nibble 内的位：`8` = `exists`，`4` = `high`，`2` = `middle`，`1` = `low`。一个字段的子值按 `high → middle → low` 的顺序占位：只有一个子值的字段（`heartRate`、`pace`、`cadence`……）由 `4` 位控制；两个子值的（`newCalories`/`newSteps`、`isKilometer`/`newDistance`）由 `4`、`2` 两位控制；三个子值的由 `4`、`2`、`1` 控制。真机印证：跑的冲击力 nibble `0xA`（exists=1，force=0，contact=1，flight=0），跑的 `contactRatio` nibble `0xB`（high=0，整列都是 0），骑的 `newCalories/workoutcount` nibble `0xD`（`workoutcount` 无效，整列为 0），骑的 `cadence` nibble `0xB`（无效，整列为 0）。
- `exists=0` 的字段**不占字节**；`exists=1` 时占 `byteSize` 字节（小端读成整数），每个子值按自己的位段取出，对应有效位为 0 的子值不输出。位段规则（`FourDimenDataParser.computeItemValue`）：子值的字节数等于字段字节数时取整个值，否则 `(值 >> 起始位) & (2^位数 − 1)`。
- 点长 = 入选且 `exists=1` 的字段字节数之和；段内点数 × 点长必须不超过剩余字节（边界检查）。
- 测试向量（实现计划直接用；标志字节与真机文件同形，不含个人数据）：跑 v8、标志 `FF FF FA BF FB BF FF` → 点长 21；冲击力只出 `newContactTime`；`contactRatio`、`equalIntensityPace`、`runningPower` 不出现。骑 v5、标志 `DF FF FB` → 点长 7；点里没有 `workoutcount` 和 `cadence`。把某字段的 `exists` 位清零（例如骑 v5 标志改成 `DF FF F3`）→ 该字段不占字节、点长变 6。

跑（协议 22）v8 入选字段（顺序即字节顺序；跑 v9 增加的 4 字节海拔与 2 字节整公里/距离不在 v8 里）：

| # | 字节 | 子值（位段） | A 层名 | 核对（§4.9） |
|---|---|---|---|---|
| 1 | 1 | [7..4] / [3..0] | `newCalories` / `newSteps` | 步数 ✓；卡路里近似 |
| 2 | 1 | [7..0] | `heartRate` | ✓ |
| 3 | 1 | [7] / [6] / [5..0] | `isKilometer` / `isAltitudeRising` / `altitudeChange`（0.1 m） | 整公里标记 ✓；海拔见 §4.9 |
| 4 | 1 | [7..0] | `newDistance`（**分米**，协议 22 的距离倍数 10） | ✓ |
| 5 | 1 | [7..0] | `stride`（厘米） | 近似 |
| 6 | 4 | [31..26] / [25..13] / [12..0] | `newImpactForce` / `newContactTime` / `newFlightTime` | 未核对 |
| 7 | 1 | [7..0] | `contactRatio` | 真机无效位为 0 |
| 8 | 1 | [7..0] | `cadence`（步/分） | ✓（最大值） |
| 9 | 2 | [15..0] | `pace`（秒/公里） | ✓（极值） |
| 10 | 2 | [15..0] | `equalIntensityPace` | 真机无效 |
| 11 | 2 | [15..0] | `runningPower` | 真机无效 |
| 12 | 2 | [15..0] | `verticalStrideRatio`（×10） | 近似 |
| 13 | 2 | [15..0] | `verticalAmplitude`（×10） | 近似 |

真机：7 字节有效性标志、13 个 nibble + 1 个填充 nibble，13 个字段全部 `exists=1`，点长 21 字节；16 段，段尾正好落在文件末尾（无剩余字节）。

骑（协议 23）v5 入选字段：

| # | 字节 | 子值 | A 层名 | 核对 |
|---|---|---|---|---|
| 1 | 1 | [7..4] / [3..0] | `newCalories` / `workoutcount` | 卡路里 ✓；`workoutcount` 真机无效 |
| 2 | 1 | [7..0] | `heartRate` | ✓ |
| 3 | 1 | [7] / [6] / [5..0] | `isKilometer` / `isAltitudeRising` / `altitudeChange`（0.1 m） | 整公里标记 ✓；海拔 ✓ |
| 4 | 1 | [7..0] | `newDistance`（**米**，协议 23 的距离倍数 1） | ✓ |
| 5 | 2 | [15..0] | `speed`（0.1 km/h） | ✓（最大值） |
| 6 | 1 | [7..0] | `cadence`（踏频） | 真机无效（无踏频传感器） |

真机：3 字节有效性标志，点长 7 字节，1 段。

距离倍数来自 APK `getDistanceRecordTimes`：协议 9、10、21、23 为 1，其余为 10（点里的 `newDistance` 要除以它）。

APK 字段表的版本区间（`FourDimenDataInfo` 的 `supportVersion` / `maxSupportVersion`）：

- 协议 22：`newCalories/newSteps`、`heartRate`、海拔三元组、`newDistance`（1 字节）自 v1；`stride` 自 v2；冲击力三元组自 v4；`contactRatio`、`cadence`、`pace` 自 v5；`equalIntensityPace`、`runningPower` 自 v6；`verticalStrideRatio`、`verticalAmplitude` 自 v8；海拔三元组和 1 字节 `newDistance` **只到 v8**，v9 起换成 4 字节 `altitude`（×10，65535 表示无效）和 2 字节 `isKilometer/newDistance`（排在 `heartRate` 之后）。
- 协议 23：`newCalories/workoutcount`、`heartRate`、海拔三元组、`newDistance` 自 v1、**只到 v5**；`speed`、`cadence` 自 v3；v6 起同样换成 4 字节 `altitude` 和 2 字节 `isKilometer/newDistance`。

这张表就是实现里布局表的来源；本期只启用 v8（22）和 v5（23）两行组合，其它版本即使能按表推出来，也按 D1 判为不支持。

**海拔的含义**（APK `SportRecordBaseParser.setSportRecordItemValue`，类型 7/8）：每段从 `initialAltitude` 开始；某秒 `isAltitudeRising` 和 `altitudeChange` 都有效时，`海拔 += (isAltitudeRising ? +1 : −1) × altitudeChange / 10`，该秒的海拔即累加后的值；任一无效时，该秒没有海拔（累计值不变）。`isKilometer`（类型 6）为 1 的秒是整公里点：真机两次运动的整公里标记个数都等于"汇总距离的整公里数"。

### 4.8 GPS 文件布局（老解析器，APK `SportGpsParser`；v2）

- GPS 文件用"一维"字段表（`OneDimenDataParser`）：字段按表顺序、只取 `supportVersion ≤ 文件版本` 的项；**每个字段在有效性标志里占 1 位**（高位在前），无效字段**照样占字节**，只是不出值（与 record 的四维规则不同）。
- 字段表（类型号 / 字节 / 起始版本 / A 层名）：0 / 4 / v1 / `time`（uint32，真实 Unix 秒）；1 / 4 / v1 / `longitude`（float32）；2 / 4 / v1 / `latitude`（float32）；3 / 4 / v2 / `locationHorizontalAccuracy`（float32）；4 / 2 / v2 / `locationSpeedAndGpsSource`（uint16）；5 / 0 / v2 / 来源有效位（不占字节）；6 / 4 / v3 / `altitude`；7 / 4 / v3 / `hdop`。
- 所以 v2 的点 = 18 字节：`time`、`longitude`、`latitude`、精度、速度+来源；1 字节有效性标志（真机 `0xFF`，前 6 位对应 v2 的 6 个字段）。正文 = 连续的点直到文件末尾；真机两份文件都正好整除。
- 速度与来源（`SportGpsParser.processOneRecordData`）：`speed = ((u16 & 0xFFF0) >> 4) / 10`，`gpsSource = u16 & 0x0F`（只有类型 5 有效时才取来源）。速度的单位 APK 没写，见 §4.9。A 层按 schema v3 的位域名拆成 `locationSpeed`（位 15..4 的存储值，不除 10）和 `gpsSource`（位 3..0）。
- **`time`、`longitude`、`latitude` 任一无效，app 丢弃整条轨迹**（`processRecordList` 返回 null）。我们照做：`track=None` 并加 note。
- **经度在前、纬度在后。** 点间隔绝大多数为 1 s；暂停处有几十到上百秒的间隔。
- v4 起 app 改走 `parseTgcGps`（带"特征文件"），三项全能（17）和滑雪（21）走 `parseTriItemsGps`——都不在本期范围。

### 4.9 离线核对（解析结果 vs 同一运动的 `SportBasicReport`）

只列比值和差值（真机数值不入文档）：

| 量 | 跑（22/v8） | 骑（23/v5） | 结论 |
|---|---|---|---|
| 点数 / `duration` | 0.997 | 0.998 | 1 Hz，每点 1 秒 |
| 各段 `totalDuration` 之和 / `duration` | 0.997 | — | 段时长 = 点数 |
| Σ`newSteps` / `steps` | **1.000** | — | 步数增量 ✓ |
| Σ`newCalories` / `calories` | 0.997 | **1.000** | 骑精确；跑差 0.3%，逐秒卡路里留 A 层 |
| Σ`newDistance` / 倍数 / `distance` | 1.001 | **1.000** | 距离增量 ✓（跑为分米） |
| 心率均值（非 0）/ `avg_hrm` | 1.000 | 0.998 | ✓ |
| 心率最大 − `max_hrm`，最小（非 0）− `min_hrm` | 0 / 0 | 0 / 0 | ✓；0 表示无读数 |
| `cadence` 最大 − `max_cadence` | 0 | — | ✓ |
| `pace` 最小（非 0）− `max_pace`，最大 − `min_pace` | 0 / 0 | — | ✓（`max_pace` 是最快即最小秒数） |
| `speed` 最大 / 10 − `max_speed` | — | ≤ 0.05 | ✓（0.1 km/h） |
| `stride` 均值 / `avg_stride` | 1.012 | — | 近似，留 A |
| `verticalStrideRatio`/10、`verticalAmplitude`/10 均值 / 汇总均值 | 1.008 / 1.011 | — | 近似，留 A |
| 海拔重建（§4.7 规则）− `max_height` / − `min_height` | 约 −2 m / 约 −3 m | ≤ 0.1 m / ≤ 0.1 m | 骑精确；跑有 2–3 m 偏差（汇总可能另行平滑），真机看图定（§16 Q4） |
| 整公里标记个数 − 汇总距离的整公里数 | 0 | 0 | `isKilometer` ✓（留 A，可由累计距离推出） |
| GPS 首点 − `start_time`，`end_time` − 末点 | 几秒 | 0–1 s | 时间是真实 Unix 秒 |
| GPS 运动中轨迹长度 / `distance` | 0.99 | 1.02 | 经纬度含义 ✓ |
| GPS 速度字段 / 由轨迹算出的速度 | 0.87 | 0.95 | 单位未知，留 A |
| GPS 精度字段 | 中位数几米 | 中位数几米 | 像水平精度（米），未核对，留 A |
| 首个样本时刻 − `start_time` | 0 | −1 | 样本从 `value.time` 起算（骑行那条比 `start_time` 早 1 s） |

这些数是用 `work/m6/scripts/proto_parse.py`（一次性原型，不进仓库）按 §4.7/§4.8 的规则解出来的：两份 record、两份 GPS 都按规则走到文件末尾、无剩余字节；有效位为 0 的子值（跑的冲击力/腾空/触地比/等强配速/功率，骑的 `workoutcount`/踏频）都被正确排除。

### 4.10 `schemas.zip` 的角色

- APK `assets/schemas.zip`（72 项，`.version` 1.2.3）是 iOS/Android 共用的 JSON 二进制描述：`index.json` 把头部数字映射到活动名（运动：`22→outdoor_foot`、`23→outdoor_non_foot`、`8→free_training`……）和文件类型（`0 record / 1 report / 2 gps / 3 hrr`），再按版本列出 schema 文件。
- 它只覆盖**新版本**：跑 record 只有 v9，骑 record v6/v7，GPS v3–v5。app 只有在精确匹配到 (活动, 文件类型, 版本) 时才用它。
- 它的字段名（`newDistance`、`heartRate`、`cadence`……）就是我们 A 层的命名来源；字段语义说明（单位、×10）是核对的线索。
- 它的解释器（`BinarySchemaDecoder`）语义：小端；`bitField` 低位在前，除非 `highBitFirst`；`exists` 指针为 0 时元素不占字节；`valid` 指针为 0 时元素占字节但不出值；`itemCount: "EOF"` 读到末尾；`scale`/`offset` 线性变换。本期不实现它（§15 D2），记在这里给以后用。

### 4.11 已核实的类型

- 已核实的运动类型 {1 跑, 6 骑, 300 爬楼机, 308 力量训练}；后两者 `proto_type` 8（自由训练），`supportGpsSport(proto)` 只认 1、2、4、5、6、10、15、17、21–25，所以没有 GPS。
- 本期只覆盖跑（协议 22）和骑（协议 23）的 record + GPS。

---

## 5. 数据流

```
MiFit.workout_detail(start, end, track=True)
  └─ fetch_workout_detail(t, start, end, downloader=..., track=...)
       1. rows = sport_rows(t, start, end)                 # 与 workouts 共用：同窗口规则、同页数上限
            每行：外层 sid + 解码后的 value
       2. 每行（新到旧，至多 _MAX_WORKOUTS 行）：
            workout = _to_workout(value)                   # M4 的映射，原样复用
            若 proto 不在支持表 → WorkoutDetail(workout, None, None) + note，不发请求
            ids = [record] + ([gps] if track 且该 proto 有 GPS)
            refs = [FileRef(sid, value.time, value.timezone, proto, ft) for ft in ids]
       3. urls = t.get_json("service/gen_download_url", {"did": sid, "items": [...]},
                            sign_path="/service/gen_download_url", base_path="healthapp/")
            ApiError → 该运动 note，继续下一条；AuthError → 抛出
       4. 每个文件：
            entry = result.get(server_key)
            无 entry / 无 obj_key → "文件不存在" note（不下载）
            校验 entry（https、主机白名单、method == GET）
            body = downloader.fetch(url)                   # 无 Cookie、不跟随重定向、限大小、节流
            plain = decrypt(body, obj_key)                 # JSON 字符串 → b64url → AES-CBC → 去填充
            header = parse_header(plain)；与 value 的 time/timezone/proto_type 比对
            layout = LAYOUTS.get((proto, file_type, header.version))  # 无 → note + 只回头部
            decoded = layout.decode(plain)                 # A 层 dict
       5. samples = to_samples(decoded_record, proto)；track = to_track(decoded_gps)
       6. DomainResult(records=[WorkoutDetail...], raw=[{"workout": value, "record": ..., "gps": ...}], notes)
```

## 6. 模块布局

小米专有的部分放进新子包 `mifit/fds/`（总设计 §12.3 "小米相关代码聚拢"），中立模型进 `models/`，映射在 `domains/`：

| 文件 | 职责 | 依赖 |
|---|---|---|
| `src/mifit/fds/__init__.py` | 子包说明（链路、安全规则、"这里是小米专有"的接缝标注） | — |
| `src/mifit/fds/ids.py` | `FileRef`（sid、timestamp、tz15、proto、file_type）、`suffix()`、`server_key()`、`RECORD=0`/`GPS=2` 常量；纯函数 | 标准库 |
| `src/mifit/fds/urls.py` | `request_urls(t, sid, refs) -> dict[server_key, entry]`：调 `Transport.get_json`；把响应里的条目整理成 `FdsEntry(url, key_b64, method, expires_s)`（`obj_name` 丢弃；`key_b64` 缺失或为空时为 `None`） | `transport` |
| `src/mifit/fds/download.py` | `FdsDownloader`：独立 HTTP 客户端、URL 校验、无 Cookie、不跟随重定向、超时、大小上限、节流；`fetch(url) -> bytes`；错误类型 `FdsUnavailable`（消息不含 URL） | `requests` |
| `src/mifit/fds/cipher.py` | `decrypt_body(body: bytes, key_b64: str) -> bytes` | `cryptography` |
| `src/mifit/fds/binary.py` | `FileHeader`、`parse_header()`、有效性标志查表、小端读取与位段工具（全部边界检查） | 标准库 |
| `src/mifit/fds/layouts.py` | 三种布局（跑 record v8、骑 record v5、GPS v2）的声明式字段表 + `decode(plain) -> dict`；`LAYOUTS[(proto, file_type, version)]` | `binary` |
| `src/mifit/models/workout_detail.py` | `WorkoutDetail`、`WorkoutSample`、`TrackPoint`（中立命名，无小米字段名） | `models.workouts` |
| `src/mifit/domains/workout_detail.py` | `@register("workout_detail") fetch_workout_detail`；小米 → 中立映射（`to_samples`、`to_track`）；note 文案 | 以上 |
| `src/mifit/domains/workouts.py`（改） | 抽出内部函数 `sport_rows(t, start, end) -> SportRows`（外层行 + 解码值 + notes + truncated），`fetch_workouts` 改为调用它，**行为与输出不变** | — |
| `src/mifit/client.py`（改） | `MiFit.workout_detail`；构造参数加 `fds_http=None`（测试注入下载客户端） | — |
| `src/mifit/cli/__init__.py`（改） | `export workout_detail` 自动出现（注册表驱动）；新增 `--no-track`（只对该域有效，同 `--full` 的处理方式） | — |
| `notes/12-fds-workout-detail.md` | 本文 §4 的事实版（合成示例），作为数据域目录的权威来源 | — |

不新建 `providers/` 树，不建通用插件机制（总设计 §12 "明确不做"）。

## 7. 公开 API 与 CLI

### 7.1 SDK

```python
client = MiFit()
res = client.workout_detail("2026-10-01", "2026-10-14")          # 窗口内每次运动一条
for d in res.records:            # WorkoutDetail
    d.workout                    # M4 的 Workout（同一映射）
    d.samples                    # list[WorkoutSample] | None
    d.track                      # list[TrackPoint] | None
res.raw                          # 每次运动一个 dict（§9）
res.notes                        # 文件不存在、不支持的版本、上限截断……

w = client.workouts("2026-10-12", "2026-10-12").records[0]
one = client.workout_detail(w.start_epoch_s, w.start_epoch_s)    # 精确取这一次
no_gps = client.workout_detail("2026-10-01", "2026-10-14", track=False)
```

- 选行规则与 `workouts` 完全相同（按外层 `time` 即开始时刻，`start ≤ time ≤ end`，请求从 `start − 1` 开始；notes/10 §3）。
- `None` 与 `[]` 的区别：`None` = 没读（不支持的运动/版本、文件不存在、下载或解析失败，都有 note）；`[]` = 读到了文件但里面没有点。
- `track=False`：不请求 GPS 文件，`track` 为 `None`，不加 note。

### 7.2 CLI

```
mifit export workout_detail --from 2026-10-01 --to 2026-10-14 [--json out.json] [--no-track]
```

输出与其它域一样是 `{records, raw, notes}`。`--no-track` 只对 `workout_detail` 有效，对其它域给出与 `--full` 相同风格的提示并忽略。不新增任何文件格式输出。

### 7.3 上限与 T3

- `_MAX_WORKOUTS = 10`：一次调用最多处理窗口内最新的 10 次**支持的**运动；超出时加 note 建议缩小范围。不支持的运动不计入、不发请求。
- 列表读取沿用 `workouts` 的 20 页上限和截断 note。
- 若 T3 已合入：在 `DOMAIN_CHUNK` 给 `workout_detail` 定 `week`，顺序与 `workouts` 一致（新到旧）。若 T3 后合入，由 T3 的计划补这一行。
- T3 的 `.partial` 续传文件对 `workout_detail` 照常使用（D24）：它是用户用 `--json` 要求写出的同一份数据，中断后留在输出旁边，CLI 已经会告诉用户它在哪。
- T3 的 `MiFit.read_chunks` / `mifit.chunks.read_chunks` 目前以 `getattr(client, domain)(first, last)` 调用、不带关键字参数。M6 给它加一个向后兼容的可选参数（例如 `options: Mapping[str, Any] | None = None`，原样作为关键字参数转给域方法），让 SDK 用户能分块读取时传 `track=False`；CLI 走 `_fetch`，M6 本来就要改它以支持 `--no-track`。参数名由实现者定。

## 8. B 层模型（中立命名）

```python
@dataclass
class WorkoutSample:
    """One recorded second of a workout.

    epoch_s is true Unix seconds and the sample covers [epoch_s, epoch_s + 1).
    Paused time has no samples, so a gap between consecutive epoch_s values is a pause.
    distance_m and steps are cumulative from the start of the workout (summed from the
    source's per-second increments across all segments). Any figure the source did not
    report, or marked invalid, is None, never a made-up zero.
    """
    epoch_s: int
    heart_rate_bpm: int | None
    distance_m: float | None      # cumulative, metres
    steps: int | None             # cumulative; None for sports without steps
    cadence_spm: int | None       # steps per minute (running)
    pace_s_per_km: int | None     # seconds per km (running)
    speed_mps: float | None       # metres per second (cycling; source 0.1 km/h)
    altitude_m: float | None      # metres, as the app charts it (segment start + per-second changes)

@dataclass
class TrackPoint:
    """One GPS fix: true Unix seconds and WGS-84-style degrees as the device recorded them."""
    epoch_s: int
    latitude_deg: float
    longitude_deg: float

@dataclass
class WorkoutDetail:
    workout: Workout                       # the M4 summary of the same workout
    samples: list[WorkoutSample] | None
    track: list[TrackPoint] | None
```

映射规则（`domains/workout_detail.py`，小米字段名只在这里出现）：

| B 字段 | 来源 | 规则 |
|---|---|---|
| `epoch_s` | 段 `resumeTimestamp` + 段内序号 | 不加 app 的显示时区平移 |
| `heart_rate_bpm` | `heartRate` | 0 或无效 → `None` |
| `distance_m` | Σ`newDistance` / 距离倍数 | 累计；该字段不存在/无效 → 整条 `None`；某秒无效 → 该秒 `None`，累计不加 |
| `steps` | Σ`newSteps` | 只在协议 22；累计 |
| `cadence_spm` | `cadence` | 只在协议 22；0 → `None`（骑行踏频真机无效，暂不映射） |
| `pace_s_per_km` | `pace` | 只在协议 22；0 → `None` |
| `speed_mps` | `speed` / 36 | 只在协议 23；0 是真实的静止，保留为 `0.0`（真机核对见 §16 Q6）；无效 → `None` |
| `altitude_m` | 段 `initialAltitude` + Σ(±`altitudeChange`/10) | §4.7 规则；该秒变化无效 → `None`；`initialAltitude` 非有限值 → 整段 `None`。跑步有 2–3 m 偏差待真机定（§16 Q4，不通过就挪回 A 层） |
| `TrackPoint.latitude_deg/longitude_deg` | `latitude` / `longitude`（float32） | 非有限值、超出 ±90/±180、或恰为 (0, 0) 的点丢弃，计数进 note |
| `TrackPoint.epoch_s` | `time` | — |

只留 A 层（含义未核实，总设计 §2）：逐秒卡路里、`stride`、触地/腾空/冲击力、`contactRatio`、等强配速、跑步功率、垂直步幅比与振幅、`workoutcount`、骑行踏频、海拔变化的原始位、段的 `segmentType`/`pauseType`/段合计、GPS 精度、GPS 速度与来源。

关于"不做 GPX/FIT"：`TrackPoint` 与 `WorkoutSample` 是给上层自己拼导出用的中立数据，SDK 不提供任何导出格式。

## 9. A 层

`DomainResult.raw` 每次运动一个 dict：

```json
{
  "workout": { "...": "解码后的 SportBasicReport，与 workouts 的 raw 同一对象" },
  "record": {
    "header": {"timestamp": 1700000000, "timezone": 32, "version": 8, "protoType": 22,
               "encryptionCompressionMode": 0, "validityFlags": "ffffffffffffff"},
    "segments": [
      {"initialAltitude": 50.0, "recordCount": 2, "resumeTimestamp": 1700000000,
       "pauseType": 1, "segmentType": 4, "totalDistance": 5, "totalDuration": 2,
       "totalSteps": 6, "segmentPace": 400,
       "dataPoints": [{"newCalories": 1, "newSteps": 3, "heartRate": 120, "newDistance": 25, "...": "..."}]}
    ]
  },
  "gps": {
    "header": {"...": "同上（文件里没有 fileType 字段；是哪种文件由 raw 的键 record/gps 表示）"},
    "gpsPoints": [{"time": 1700000006, "longitude": 20.0001, "latitude": 10.0001,
                   "locationHorizontalAccuracy": 4.5, "locationSpeed": 30, "gpsSource": 0}]
  }
}
```

- 值是**存储值**，不缩放（`newDistance` 在协议 22 里就是分米）。缩放只在 B 层映射里做（总设计 §8.1）。
- 无效子值不出现在点里（与 app 一致）；`exists=0` 的字段也不出现。
- 没读到的文件为 `null`；只读到头部（不支持的版本）时只有 `header`。
- **永不出现**：`sid`、`did`、`suffix`、URL、`obj_key`、`obj_name`、`expires_time`。
- float32 原样转成 Python float（JSON 里会是 `10.000100135803223` 这种形式），不四舍五入。
- 需要解密后原始字节的人：`mifit.fds` 提供底层函数（`request_urls` / `FdsDownloader.fetch` / `decrypt_body`），文档写明它们的输入输出；CLI 不落盘二进制。

## 10. 依赖

- 新增运行时依赖 `cryptography>=42`（写进 `pyproject.toml` 的 `dependencies`），只在 `mifit/fds/cipher.py` 里导入（顶层导入即可，不做懒加载）。
- 测试用同一个库做 AES-CBC 加密，生成合成密文。
- CI 不需要改：`pip install -e ".[test]"` 会装上它；Python 3.12–3.14 都有官方 wheel。

## 11. 安全

| 风险 | 措施 |
|---|---|
| 预签名 URL 是 30 分钟有效的持有者凭据；`obj_key` 是解密密钥 | 只在内存里流转；不进 `raw`、records、notes、日志、异常消息；`FdsEntry.__repr__` 隐去 URL 和密钥；`requests` 异常（消息含 URL）在下载器里捕获后换成只带类型名的 `FdsUnavailable`（沿用 CLI 只打印异常类型的做法），并且在 `except` 块外抛出，不留 `__context__` |
| 会话凭据外泄到对象存储主机 | 下载用**独立的** `requests.Session`（不复用 `Transport` 的 http），不带 Cookie 头、`region_tag`、`Authorization`；不跟随重定向（`allow_redirects=False`），3xx 视为失败；只接受 `https` |
| URL 指向意外主机 | 主机白名单：后缀 `.fds.api.xiaomi.com`（真机 `cnbj1.fds.api.xiaomi.com`）；不在名单 → note（只写主机名）、不下载。名单是 `FdsDownloader` 的构造参数，便于非 cn 地区补充（§16 Q8） |
| `method` 不是 GET | 不下载，note |
| 超大/恶意正文 | 流式读取，编码正文上限 16 MiB（真机最大约 110 KB；24 小时 1 Hz 的极端运动也只有几 MB），超出即中止；连接/读取超时 30 s |
| 畸形正文 | 必须是 JSON 字符串字面量或裸 base64url（D22）；字符集只允许 base64url；解码长度必须是 16 的倍数；密钥长度必须是 16/24/32；填充错误 → note "无法解密"；不回显正文 |
| 解析不可信二进制 | 纯 `struct` 读取，每次读取前检查剩余长度；`recordCount × 点长` 先对剩余字节检查再循环；总点数上限 1,000,000；头部与汇总不一致（时间戳/时区/协议号）→ 不解析；版本/加密模式不在支持表 → 不解析；没有递归、没有 `eval`、不写盘 |
| 解密结果落盘 | SDK 自己不缓存、不写临时文件。CLI 只写用户要求的输出：`--json` 文件，以及（T3 合入后）同一输出的 `<PATH>.partial` 续传文件——内容与最终输出相同，是用户要求写的东西，不算缓存（§7.3）；`--no-track` 时两者都没有轨迹 |
| 测试/仓库泄露真实数据 | 只用合成夹具（§13）；`work/m6/` 已被 `.gitignore` 覆盖 |

## 12. 错误处理与 notes

每条 note 都是固定文案 + 不敏感的上下文（运动开始的本地日期由调用方自己知道，所以 note 里只写序号或"该运动"）：

| 情况 | 行为 |
|---|---|
| 运动的 `proto_type` 不在支持表 | `samples=None, track=None`；note "Workout detail is not supported for this sport yet (format N)."；不发请求 |
| `value` 缺 `time`/`timezone`/`proto_type` 或类型不对；时间戳 ≥13 位 | 同上，note 说明缺什么 |
| 外层行无效（无 `time`、`value` 不是 JSON 对象、`deleted`、窗口外） | 与 `workouts` 完全相同的处理（共用 `sport_rows`） |
| `value` 没有可用的 `start_time`（`_to_workout` 返回 `None`） | 不生成 `WorkoutDetail`，note（同 `workouts`）；不发 FDS 请求 |
| `gen_download_url` 抛 `ApiError` | 该运动两项都 `None`，note；继续下一条 |
| `AuthError`（续期失败或签名被拒） | 照常抛出（与其它域一致） |
| 条目缺失或无 `obj_key` | 该文件 `None`，note "The service has no <record/GPS> file for this workout." |
| URL 校验失败 / 非 200 / 超时 / 网络错误 / 超大 | 该文件 `None`，note（只含 HTTP 状态或异常类型） |
| 解密失败 | 该文件 `None`，note |
| 头部与汇总不一致 | 该文件 `None`，note |
| 版本或加密模式不支持 | A 层只有 `header`，B 为 `None`，note "Format version N of the <record/GPS> file is not supported yet." |
| 解析中途越界 | 该文件 `None`（不返回半截数据），note |
| GPS 的时间/经度/纬度有效位为 0 | A 层照常给解码结果，`track=None`，note（D23） |
| 轨迹里有被丢弃的点 | track 照常，note 给出丢弃数量 |
| 超过 `_MAX_WORKOUTS` | note 建议缩小范围 |

## 13. 测试策略（全部离线）

1. **夹具生成器**（`tests/fds_fixtures.py`，测试专用）：用我们的布局表反向写出合成文件——8 字节头 + 有效性标志 + 段 + 点；GPS 点用虚构坐标（如纬度 10.0、经度 20.0 附近的小偏移，故意让经度 ≠ 纬度以抓住顺序错误）。再用 `cryptography` 按同一方案加密（随机 16 字节密钥、固定 IV、PKCS#7），base64url 去填充、包成 JSON 字符串。不提交任何真实文件，不提交从真实文件派生的字节。
2. **ids**：`suffix` 对一组手算向量（时间戳、负时区、协议 22/23、fileType 0/2）；`server_key` 格式；`value.time` 与 `start_time` 不同时用前者。
3. **cipher**：往返；错误的密钥长度、非 16 倍数、坏填充、非 JSON 字符串、非法字符 → 各自的错误。
4. **binary/layouts**：每种布局的往返（写 → 解）；`exists=0` 的字段不占字节；无效子值不输出；多段、暂停间隔；`recordCount` 超过剩余字节；截断文件；版本不支持；加密模式非 0；头部不一致；GPS 不能整除的尾巴（丢弃并 note，或判错——实现者择一并测试）。
5. **映射**：累计距离/步数、协议 22 的分米换算、心率 0 → `None`、速度换算、`(0,0)` 和越界坐标丢弃、`epoch_s` 不受本机时区影响（在两个 `TZ` 下跑同一测试）。
6. **下载器**：假 http 记录调用——断言不带 Cookie/会话头、`allow_redirects=False`、超时参数；3xx/404/超大/非 https/白名单外主机/`method` 非 GET；异常消息里不含 URL（用含唯一标记的假 URL 检查 `str(exc)`、`repr(exc)`、`__context__`）。
7. **域**：假 `Transport`（记录 `get_json` 的 endpoint、`base_path`、`sign_path`、`data`）+ 假下载器；断言请求形状（`did`、`items`、`base_path="healthapp/"`、`sign_path="/service/gen_download_url"`）；不支持的运动不发请求；`track=False` 只请求 record；无 `obj_key` 不下载；`ApiError` 只影响一条；`AuthError` 抛出；上限 note；`raw` 里没有 URL/密钥/`sid`（对序列化后的 JSON 做字符串检查）。
8. **workouts 回归**：抽出 `sport_rows` 后，`tests/test_domain_workouts.py` 原样全过。
9. **CLI**：`export workout_detail` 的 JSON 形状；`--no-track`；对其它域的 `--no-track` 提示。
10. **许可**：`tests/test_licensing.py` 不变（本期不移植上游代码，见 §15 D17）。

## 14. 收尾真机核对（与用户）

我先在本地跑一次 `mifit export workout_detail`（覆盖跑、骑各一次），把下面的数算好；用户在 app 的运动详情页读数对照。核对口径沿用总设计 §8.1。

| # | 核对项 | 我给出 | 用户在 app 里看 | 通过标准 |
|---|---|---|---|---|
| 1 | 轨迹起止 | 首/末轨迹点的本地时刻 | 运动开始/结束时刻 | 差 ≤ 10 s |
| 2 | 轨迹距离 | 轨迹（运动中各段）长度 | 运动距离 | 差 ≤ 3% |
| 3 | 轨迹形状 | 本地生成一张不带底图的轨迹 SVG（只在本机打开，不提交、不上传） | 地图轨迹 | 形状一致（起终点、折返、暂停处） |
| 4 | 心率曲线 | 每分钟均值的折线（本地图）+ 均值/最高/最低 | 心率图 + 三个数 | 形态一致；三个数一致 |
| 5 | 配速（跑）/速度（骑） | 每公里平均配速、最高速度 | 配速分段、速度图 | 分段配速差 ≤ 5 s/km；最高速度一致 |
| 6 | 步频（跑） | 平均/最高 | 步频 | 一致 |
| 7 | 暂停 | 样本时间线里的间隔数 | 暂停次数/位置 | 一致 |
| 8 | 海拔 | 海拔曲线（本地图）+ 最高/最低 | 海拔图 + 最高/最低（若显示） | 形态一致；跑步极值差 ≤ 3 m 视为通过，否则 `altitude_m` 挪回 A 层 |
| 9 | 轨迹点数 | 点数、点间隔分布 | （app 不显示） | 只记录，不判定 |

本地生成图用的脚本放 `work/m6/`（不进仓库）；图里不带地图底图，避免把坐标发给任何第三方瓦片服务。

## 15. 决定记录（我的判断，均可推翻）

- **D1 范围 = 已核实的三种布局。** 跑 record v8、骑 record v5、GPS v2（两种协议共用）。理由：只有这三种有真实数据可核；总设计 §2 反目标不允许把没核过的东西放进 B。备选：顺手实现 `schemas.zip` 里的新版本（跑 v9、GPS v3–v5）——没有真实文件可核，等用户的设备产出新版本再做。
- **D2 手写声明式布局表，不在运行时解释 `schemas.zip`，也不随包分发它。** 理由：账号文件的版本根本不在 `schemas.zip` 里；那是小米的资产，放进 MIT 公开仓库有再分发问题；三张表很小。备选 A：实现 `BinarySchemaDecoder` 的 Python 版并在运行时从用户本地 APK 读 schema（以后新版本多了再考虑，`notes/12` 记下 §4.10 的语义）；备选 B：把 schema JSON 复制进仓库（否决：再分发）。
- **D3 `sid` 由本域重读运动列表获得，M4 不改输出。** 抽内部函数 `sport_rows` 供两个域共用。理由：M4 的 `raw` 约定是"解码后的 value 原样"，`Workout` 是中立模型，都不该塞进小米的设备标识；重读只多 1 次低成本请求。备选：在 M4 `raw` 里加外层字段（改 A 层约定）、在 `Workout` 里加 `source_id`（小米标识进 B）、缓存上次列表（有状态）——均否决。
- **D4 API 按时间窗**，单次运动用 `[start, start]`；注册为数据域，CLI 自动获得 `export workout_detail`。理由：与其它域一致，CLI/T3 零特殊处理（除 `--no-track`）。备选：`MiFit.workout_detail(workout: Workout)`——`Workout` 里没有 `sid`/`proto`/`value.time`，还得再查一次，没有省事。
- **D5 B 层字段只取 §4.9 精确或取整级核对的**；累计距离、累计步数是对源增量的简单求和（总设计 §8.1 允许的简单聚合）。备选：给增量（更"原样"，但 A 层已经有增量）。
- **D6 A 层用 `schemas.zip` 的字段名、存储值不缩放。** 理由：最接近小米自己的命名，以后接 schema 版本时名字不变。
- **D7 `cryptography` 作硬依赖。** 理由：成熟、各平台有 wheel、API 清楚；FDS 是 SDK 的核心数据域，做成 extra 会让 CLI 用户撞上 ImportError。备选：`pycryptodome`（也可，但与老 `pycrypto` 的 `Crypto` 命名空间冲突史不好）；纯 Python AES（探测时用过，慢且是自写密码学代码，否决）；optional extra `mifit[fds]`（否决，理由同上）。
- **D8 下载器独立于 `Transport`。** 理由：不同主机、无签名、不能带会话；独立对象让"不带 Cookie"在结构上成立，而不是靠约定。节流参数与 `Transport` 一致。
- **D9 无 `obj_key` 即"文件不存在"，不下载。** 与 app 一致，真机证实对应 404。
- **D10 每次运动一次 `gen_download_url`，带 record（和 GPS）两项。** 理由：URL 30 分钟过期，按运动就近下载最稳；真机证实多项可行。备选：整窗一次请求（请求更少，但 GET 的 `data` 变长、过期风险随运动数增加）。
- **D11 时间一律真实 Unix 秒**，忽略 app 的显示平移；样本时刻 = 段 `resumeTimestamp` + 序号（1 Hz，真机点数 = 段时长）。
- **D12 文件头必须与汇总一致才解析**（时间戳、时区、协议号）。理由：防止拿错文件时静默产出错误数据。
- **D13 单次调用上限 10 次运动**，新到旧；超出加 note。理由：每次运动 3 个请求，10 次约 30 个请求、半分钟到一分钟，仍属低频；更长的区间交给 T3。
- **D14 不支持的组合只给头部 + note**，不抛异常，不阻断同一调用里的其它运动。
- **D15 `track=False` / `--no-track`。** 理由：轨迹是最敏感的数据，给一个不取它的开关成本很低。
- **D16 不缓存。** 理由：隐私和简单；探测和使用都是低频。
- **D17 不移植上游代码，无需新署名。** 本仓库的参考快照里没有上游的 FDS 实现（只有 activity client/crypto/transport 和文档）；本设计的事实全部来自 APK 和真机，上游文档只作背景。若实现者决定移植上游任何代码，按 T1 的做法：文件头加 `adapted from Mi-Fitness-Sync` 署名、列进 `THIRD_PARTY_NOTICES`、`tests/test_licensing.py` 的清单同步更新。
- **D18 notes 编号 12**（10 = M4，11 = M5）。
- **D19 `speed_mps` 进 B、GPS 速度不进 B。** record 的 `speed` 最大值与汇总精确到取整；GPS 速度字段与轨迹算出的速度比值只有 0.87–0.95，单位不明。
- **D20 骑行踏频不进 B。** 真机文件里该字段有效位为 0；以后有踏频传感器的数据再核。
- **D21 `altitude_m` 暂进 B。** 理由：含义由 APK 代码确定（就是 app 画海拔图用的值），骑行与汇总极值精确一致；跑步差 2–3 m，交给真机看图（§14 #8）。不通过就挪回 A 层，改动只在映射一处。备选：先只放 A，核完再进 B（更保守，但海拔是 app 详情页的主图之一，先放 B 便于真机对图）。
- **D22 正文解码严格、形式宽松。** 接受"JSON 字符串字面量"和"裸 base64url"两种正文，但字符集严格；不模仿 Android 跳过非法字符的行为。理由：前者防服务端小改动，后者防把错误内容当密文。
- **D23 GPS 的 `time`/经度/纬度任一有效位为 0 → 整条轨迹不用**（与 app 一致），A 层仍给出解码结果。
- **D24 T3 的 `.partial` 对本域照常使用**，`read_chunks` 增加转发关键字参数的可选参数。理由：`.partial` 的内容就是用户要求写到 `--json` 的数据；把本域排除在续传之外会让长区间导出（每周一块、每块多次下载）失去断点续传，代价更大。备选：本域不参与续传（否决，理由同上）；`.partial` 里去掉轨迹（否决：续传后的输出会和一次读完的不一致）。
- **D25 推送前在本机用真实文件做一次"只读、只看结构"的闸门检查。** 实现计划在推送 `main` 之前加一个本地任务：用新解析器解 `work/m6/dec/` 里的四份已解密文件（git-ignored，不联网、不提交），要求每份都走到文件末尾、无剩余字节、无异常，并复现 §4.9 的比值（步数、距离、心率极值、轨迹长度比）；输出只打印比值和结构，不打印数值。`work/m6/scripts/proto_parse.py` 可作参照实现。理由：合成夹具是按我们自己的布局表写出来的，布局表错了往返测试照样通过；没有这道闸门，错误会在收尾真机会话之前就推到公开仓库。真机 app 对照（§14）仍留到收尾会话。

## 16. 开放问题（只能在真机会话里和用户一起回答）

- **Q1** 轨迹起止、距离与 app 是否一致（§14 #1–#2）。跑步那次有暂停：app 的距离是否只算运动中？我们的运动中轨迹长度/汇总距离 = 0.99。
- **Q2** 心率曲线形态与 app 是否一致（§14 #4）；心率读数为 0 的秒在 app 里是断点还是插值？
- **Q3** 跑步那次是一个分段课程（快走/慢跑/中速跑……）：app 是否按段展示？若是，`segmentType` 的含义能否借此核实、进 B？
- **Q4** 海拔：骑行的重建海拔与汇总极值精确一致，跑步差 2–3 m。app 的海拔图是否就是我们重建的曲线（形态、极值）？决定 `altitude_m` 留在 B 还是挪回 A。
- **Q5** app 的配速分段（每公里）与我们由样本算出的是否一致；`pace` 字段是瞬时配速还是平滑值？
- **Q6** 骑行速度曲线与 app 是否一致；速度为 0 的秒（停车）在 app 里如何显示——决定 `speed_mps=0.0` 是否保留。
- **Q7** 逐秒卡路里（跑差 0.3%）、步幅、垂直振幅等是否值得进 B——取决于用户是否在 app 里看这些图。
- **Q8** 非 cn 地区的对象存储主机名（只有一个 cn 账号，无法自测）：白名单是否需要放宽。
- **Q9** 用户今后会记录哪些运动类型（户外步行/徒步/游泳？），手表固件是否会升级到产出 record v9 / GPS v3+ 的版本——决定 D1/D2 的后续工作量。
- **Q10** 轨迹形状核对时用户是否愿意在本机打开一张不带底图的 SVG（§14 #3）。

## 17. 留给实现者

- 布局表的具体 Python 表示（dataclass 列表、元组表都行），只要满足 §4.7/§4.8 的规则和 §13 的往返测试。
- `FdsEntry`、`FileHeader` 等内部类型的命名；note 的确切英文文案（风格与现有域一致）。
- GPS 文件尾部不足一个点时：丢弃尾巴并 note，或判整个文件失败——择一并测试（建议前者）。
- 下载器是否复用 `Transport` 的节流实现（抽一个小函数）还是自带一份（几行），二者皆可，但节流参数必须一致且可注入以便测试设 0。
- 计划拆分建议：(1) `fds.ids` + `fds.cipher` + 依赖；(2) `fds.binary` + `fds.layouts` + 夹具生成器；(3) `fds.urls` + `fds.download`；(4) 模型 + 域 + `sport_rows` 抽取；(5) 门面 + CLI（含 T3 的 `read_chunks` 关键字转发，若 T3 已合入）+ README/路线图；(6) 本机真实文件闸门（D25，推送前）；(7) 真机 app 核对（推迟到收尾会话）。
- 若 `work/m6/dec/` 在实现时已不存在（被清理），D25 的闸门改为：用 `work/m6/scripts/probe_fds.py` 的同一链路重新下载一次（≤ 6 次只读请求：列表 1、取 URL 1、下载 4；≥ 2 s 间隔，遇 401 即停），或跳过并在计划里记明"布局只经合成测试"。

## 18. 规模与下一步

规模：项目（本文件即设计文档）。下一步：把本文件提交为 `docs/superpowers/specs/2026-10-14-mifit-fds-design.md`，再用 `superpowers:writing-plans` 写实现计划。用户审批：approved by controller ruling: autonomous run；§16 的问题在收尾真机会话里问。
