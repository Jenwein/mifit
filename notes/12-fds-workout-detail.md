# 12 运动明细与 GPS 轨迹（FDS，M6，2026-10-15，APK 3.59.1 + 真机）

目的：M6。把运动明细（逐秒数据）和 GPS 轨迹的取回链路、文件标识、解密方式和文件布局记下来，作为 `mifit.fds` 子包、`workout_detail` 域（`domains/workout_detail.py`）和 `WorkoutDetail` / `WorkoutSample` / `TrackPoint`（`models/workout_detail.py`）的依据。本文是设计文档（`docs/superpowers/specs/2026-10-14-mifit-fds-design.md`）§4 的事实版。

本文所有数值都是合成示例（`sid="1234567890"`、`time=1700000000` 之类）。真机结果只给形状、大小、条数和比值，不给时间戳、坐标、`sid`、URL、密钥或任何健康数值。真机为东八区账号：设计阶段做了 8 次只读请求（列表 1、取链接 2、下载 5）；推送前又用本机保存的 4 份文件做了一次不联网的闸门检查（§8.4）。这些文件都在 `work/m6/`，git-ignored，不进仓库。

> 用词："record 文件 / GPS 文件"指 FDS 的 `fileType` 0 和 2。`proto_type` 是运动协议号（本账号跑步 22、骑行 23），不是文件类型。

## 1. 链路与请求预算

```
运动列表 data/get_sport_records_by_time（与 workouts 同一读取）
  → 每次支持的运动：healthapp/service/gen_download_url（一次请求，record + GPS 两项）
  → 每个文件：预签名 URL（cnbj1.fds.api.xiaomi.com，约 30 分钟有效）→ 不带会话下载
  → 正文：JSON 字符串字面量里的 base64url → AES-128-CBC（固定 IV）→ 去 PKCS#7 填充
  → 明文：8 字节头 + 有效性标志 + 正文（record：段 + 逐秒点；GPS：连续的点）
```

- APK 链路：`FitnessFDSDataGetter.getSportRecordData` / `getSportGpsData` → `FitnessFDSUploader.downloadFromFDS`：`genFdsUrlItem` → `CloudSyncLineRouter.genFDSDownloadUrl`（旧线路 → `FitnessFDSRequest.getFDSDownloadUrl`）→ `download`（`objectKey` 为空就放弃；`downloadFromUrl` 每次 new 一个 `OkHttpClient`，没有 Cookie 罐；`AESCoder.decrypt`）→ `FitnessDataParser.parse`。
- 本账号的文件版本不在 `schemas.zip` 里（§9），app 对它们走老解析器：`isSchemaVersionSupport` 为假 → `nativeParse` → `OutdoorStepRecordParser`（跑）、`OutdoorNoStepRecordParser`（骑）、`SportGpsParser`（GPS）。SDK 按这几个类的字段表手写了三种布局（D1、D2）。
- 我们读的是旧线路（line 1，与 M1–M5 相同），时间都是秒。新线路（`isNewWebLine()` 为真时的 `HealthKitDataRequest.genFDSDownloadUrl`）和毫秒时间戳的 10 字节文件键不在本期范围。
- 两处 app 行为有意不照抄：OkHttp 默认跟随重定向，SDK 不跟随（3xx 算失败）；app 不看 HTTP 状态就去解密，SDK 只认 200。
- 请求预算（一次 `workout_detail` 调用）：
  - 1 次列表读取，至多 20 页（与 `workouts` 相同的窗口规则、页数上限和 notes，notes/10 §3）。
  - 每次**支持的**运动（`proto_type` 22 或 23）：1 次 `gen_download_url`，加至多 2 次下载（`track=False` 时至多 1 次）。服务端没有的文件不下载（§3.3）。
  - 不支持的运动不发任何 FDS 请求，也不计入上限。
  - 一次调用至多读最新的 10 次支持的运动（`_MAX_WORKOUTS`，D13）。更早的照样有一条记录，`samples`、`track` 为 `None`，结果里有一条 note 说有几条没读、建议缩短范围。分块读取（`mifit.chunks`、CLI 的长区间）对 `workout_detail` 默认按周（`week`）分块。
  - 每次下载前与 `Transport` 同样节流：`delay_s`（默认 1.0 s）加 `uniform(0, 0.5 × delay_s)`；`delay_s=0` 关掉。
- 不缓存、不写临时文件：每次调用都重新下载（D16）。

## 2. 文件标识

规则（APK `FitnessFDSUploader.genFdsUrlItem`、`genDataIdKeyBytes`，`PreSignedUrlItem.toServerKey`；SDK `fds/ids.py`）：

- 输入取自运动列表的外层行和解码后的 `value`（`SportBasicReport`）：
  - `sid`：外层行的 `sid`。真机上账号的所有行是同一个值（10 位数字字符串），是数据来源设备的标识，不是每次运动的 id。请求里叫 `did`。SDK 只在内存里用它算文件名、填请求参数，不进任何输出。
  - `timestamp`：`value.time`，**不是** `start_time`。真机骑行那一条 `value.time = start_time − 1`，用 `start_time` 会算错文件名；文件头里的时间戳也等于 `value.time`（§5）。
  - `tz15`：`value.timezone`，刻钟数（东八区 32），按 int8 写入。
  - `proto`：`value.proto_type`，不是 `sport_type`。
  - `fileType`：0 record，2 GPS（1 report、3 hrr 本期不用）。
- 键字节（6 字节，小端）：`<I timestamp> <b tz15> <B ((1<<7) + (proto<<2) + fileType) & 0xFF>`。
- `suffix = b64url_nopad(键字节) + "_" + b64url_nopad(SHA1(sid 的 UTF-8))`。Android Base64 标志 11 = NO_WRAP | NO_PADDING | URL_SAFE。
- 响应映射的键：`server_key = suffix + "_" + str(timestamp)`。
- APK 的 `genDataIdKeyBytes` 在时间戳十进制 ≥ 13 位时改用 8 字节 long（10 字节键）。SDK 只接受 `0 < time < 2^32` 的秒；其余以及缺 `time` / `timezone` / `proto_type`、缺 `sid` 的运动都加 note、不发请求。

测试向量（`tests/test_fds_ids.py`；合成，用 Python 现算）：

| `sid` | `time` | `timezone` | `proto` | 文件 | 键字节 | `suffix` |
|---|---|---|---|---|---|---|
| `"1234567890"` | 1700000000 | 32 | 22 | record | `00 f1 53 65 20 d8` | `APFTZSDY_AbMHrLpPVPVar8M7sGu79sqAPpo` |
| `"1234567890"` | 1700000000 | 32 | 22 | GPS | `00 f1 53 65 20 da` | `APFTZSDa_AbMHrLpPVPVar8M7sGu79sqAPpo` |
| `"1234567890"` | 1700000000 | −20 | 23 | record | `00 f1 53 65 ec dc` | `APFTZezc_AbMHrLpPVPVar8M7sGu79sqAPpo` |
| `"1234567890"` | 1700000000 | −20 | 23 | GPS | `00 f1 53 65 ec de` | `APFTZeze_AbMHrLpPVPVar8M7sGu79sqAPpo` |

第一行的 `server_key` 是 `APFTZSDY_AbMHrLpPVPVar8M7sGu79sqAPpo_1700000000`。

## 3. `gen_download_url`

### 3.1 请求

- `GET https://hlth.io.mi.com/healthapp/service/gen_download_url`；非 cn 地区加地区前缀，与其它请求相同。
- 签名路径是 `/service/gen_download_url`：去掉 `healthapp/` 前缀、保留前导 `/`，与 M5 的用户档案接口同一规则（notes/11 §3）。SDK：`t.get_json("service/gen_download_url", data, sign_path="/service/gen_download_url", base_path="healthapp/")`。
- 业务参数只在签名加密的 `data` 里，record 在前、GPS 在后：

  ```json
  {"did": "1234567890",
   "items": [{"timestamp": 1700000000, "suffix": "APFTZSDY_AbMHrLpPVPVar8M7sGu79sqAPpo"},
             {"timestamp": 1700000000, "suffix": "APFTZSDa_AbMHrLpPVPVar8M7sGu79sqAPpo"}]}
  ```

- 一次请求可以带多个文件项：真机一次带 4 项（跑、骑各 record + GPS），4 项都返回了。SDK 每次运动发一次（D10）：链接约 30 分钟过期，按运动就近下载最稳。
- 401 照常走 T2 的续期逻辑；续不了的 `AuthError` 抛出。`ApiError` 只影响这一次运动：两个文件都不读，加 note，接着读下一次。

### 3.2 响应

与其它接口相同（RC4 加密，`{code: 0, message, result}`）。`result` 是映射，键是 `server_key`：

```json
{"APFTZSDY_AbMHrLpPVPVar8M7sGu79sqAPpo_1700000000": {
   "url": "https://cnbj1.fds.api.xiaomi.com/<bucket>/<object>?GalaxyAccessKeyId=...&Expires=...&Signature=...",
   "obj_name": "<76 个字符>",
   "obj_key": "<base64url，解出 16 字节>",
   "method": "GET",
   "expires_time": 1700001800}}
```

- `expires_time` 是 Unix 秒（10 位整数），距请求约 1800 s。
- 预签名 URL 是 30 分钟内谁拿到谁能下的凭据，`obj_key` 是解密密钥。SDK（`fds/urls.py`）把每个条目整理成 `FdsEntry(url, key_b64, method, expires_s)`，丢掉 `obj_name`；`FdsEntry` 的 `repr` 只显示主机、方法和有无密钥。
- `result` 不是映射 → `ApiError`。值不是对象、或没有字符串 `url` 的条目被忽略（等同于"没有这个文件"）。

### 3.3 文件不存在

- 真机：给一次力量训练（`proto_type` 8，没有 GPS）请求 GPS 文件，服务端照样返回这个键和一个 URL，但**条目里没有 `obj_key`**；对那个 URL 下载得 HTTP 404。
- app 在 `objectKey` 为空时直接放弃，不下载。SDK 照做（D9）：`obj_key` 缺失或为空 → `key_b64=None` → 该文件 `None`，note "the service has no record/GPS file for it"，省一次下载。
- 平时 SDK 不会给 `proto_type` 8 发请求（§1）；这一条只记下服务端的行为。

## 4. 下载与解密

### 4.1 下载（真机 4/4）

- 4/4 返回 HTTP 200，`Content-Type: application/octet-stream`，没有重定向、没有 `Set-Cookie`。请求不带 Cookie，也不带会话头。
- 正文是**带双引号的 JSON 字符串**：`"<base64url，无填充>"`，引号里的字符只有 `[A-Za-z0-9_-]`。
- 大小：编码后的正文几十 KB 到约 110 KB（30–80 分钟的运动），解密后约为其 3/4。
- SDK 的下载器（`fds/download.py` 的 `FdsDownloader`，独立于 `Transport`，D8）：
  - 每次下载恰好是 `http.get(url, timeout=30.0, allow_redirects=False, stream=True)`，自己不加任何头、Cookie 或认证。默认的 `http` 每次下载新开一个 `requests.Session`，并关掉它的 `netrc` 查找（否则 `~/.netrc` 里匹配的条目会被当成 `Authorization: Basic` 发出去）；环境里的代理和 CA 设置照常生效。
  - 下载前先检查 URL：只接受 `https`；主机必须由合法的标签组成，并以 `.fds.api.xiaomi.com` 结尾（构造参数 `host_suffixes`，见 §10 Q8）；URL 不能带用户名或密码，不能含空白、控制字符、反斜杠或非 ASCII 字符。不通过就不发请求。
  - 只认 HTTP 200；3xx 不跟随，算失败。正文流式读取，超过 16 MiB 就放弃。
  - 任何失败都是 `FdsUnavailable`，消息是固定片段（`HTTP 404`、`network error (ConnectTimeout)`、`unexpected host <主机名>`……），从不含 URL；它在 `except` 块外抛出，不带 `__context__`（`requests` 的异常消息里有 URL）。

### 4.2 解密（APK `persist.utils.AESCoder` + 真机；SDK `fds/cipher.py`）

1. 正文去掉首尾空白；以 `"` 开头时 `json.loads` 必须得到 `str`。为防服务端以后去掉引号，也接受整个正文就是 base64url（D22）。
2. 严格的 base64url 解码：只允许该字母表，至多两个尾部 `=`；解出的长度必须是 16 的非零倍数。app 能直接解带引号的正文，是因为 Android 的 `Base64.decode` 会跳过字母表以外的字符；SDK 不模仿这种宽松行为。
3. 密钥 = base64url 解码 `obj_key`（APK `Base64.decode(key, 8)`，即 URL_SAFE）。真机是 16 字节，即 AES-128；SDK 接受 16 / 24 / 32 字节。
4. AES-CBC，IV 是 ASCII `1234567887654321`（`AESCoder.getInitialVector`），去 PKCS#7 填充（Java 里叫 PKCS5Padding）。

- 真机 4/4 填充校验通过，明文第一个块里的文件头与汇总逐项相等。第一块的明文依赖 IV，所以这同时证实了 IV。
- 任何失败都是 `FdsDecryptError`，固定消息，不回显正文或密钥。
- 依赖：`cryptography>=42`，硬依赖，只在 `fds/cipher.py` 里导入（D7）。

## 5. 文件头与有效性标志长度表

云端文件开头是 8 字节服务端头，后面紧跟有效性标志（APK `FitnessDataParser.parseNativeBinaryData`；SDK `fds/binary.py`）：

| 偏移 | 类型 | 含义 | A 层名 | 真机 |
|---|---|---|---|---|
| 0 | uint32 LE | 时间戳（= `value.time`） | `timestamp` | 4/4 相等 |
| 4 | int8 | 时区（刻钟，= `value.timezone`） | `timezone` | 4/4 相等 |
| 5 | uint8 | 文件格式版本 | `version` | 跑 record 8，骑 record 5，GPS 2 |
| 6 | uint8 | 协议号（= `value.proto_type`） | `protoType` | 22 / 23 |
| 7 | uint8 | 加密 / 压缩模式 | `encryptionCompressionMode` | 4/4 为 0 |
| 8 | n 字节 | 有效性标志 | `validityFlags`（十六进制串） | 长度按下表 |

合成示例：`00 f1 53 65 20 08 16 00` 后跟 7 字节标志，是时间戳 1700000000、时区 32、版本 8、协议 22、模式 0 的跑步 record 文件。

- 第 6 字节（从 0 数）只是协议号。`constructSchemaBinaryData` 会去掉这 8 字节，换成 schema 格式的头 `[ts, tz, version, genDataTypeByte, 0]`，schema 头里对应的那个字节是位域 `(1<<7)|(proto<<2)|fileType`。所以**不能直接拿 `schemas.zip` 的头定义去解析云端文件**。
- 有效性标志的长度（APK `FitnessDataValidity`）：

  | 文件 | 版本 → 字节数 |
  |---|---|
  | 跑 record（协议 22，`getSportStepRecordValidityLen`） | v1→2，v2–4→3，v5→5，v6–7→6，v8–9→7 |
  | 骑 record（协议 23，`getSportNoStepRecordValidityLen`） | v1–2→2，v3–6→3，v7→4 |
  | GPS（任何协议，`getSportGpsValidityLen`） | v1–4→1，v5→2 |

  表里查不到（APK 返回 −1）= 不支持该版本。
- SDK 的处理：
  - 头部的时间戳、时区、协议号必须与汇总相等，否则整个文件不读（D12）：防止拿错文件时静默产出错误数据。
  - 模式不是 0（压缩或加密，真机没见过）：后面的内容未知，只给头部，加 note。
  - 版本没有启用的布局（§6、§7 以外的版本，例如跑 record v9）：A 层只有 `header`，B 层为 `None`，note "format version N of the … file is not supported yet"（D1、D14）。

## 6. record v8 / v5 布局与四维有效位

APK `SportRecordBaseParser.parseFourDimenData`，`OutdoorStepRecordParser`（跑）/ `OutdoorNoStepRecordParser`（骑），`FourDimenDataParser`；SDK `fds/layouts.py`。

### 6.1 段

正文是若干"段"（每次暂停 / 分组一段），一直读到剩余字节不够一个段头为止。每段是段头加 `recordCount` 个点，点每秒一个：第 i 个点的时刻是 `resumeTimestamp + i`（真实 Unix 秒）。

跑（协议 22，v8；段头 27 字节）：

| 字段（A 层名） | 类型 | 说明 |
|---|---|---|
| `initialAltitude` | float32 | 段起始海拔（米） |
| `recordCount` | uint32 | 本段点数 |
| `resumeTimestamp` | uint32 | 本段第一个点的时刻（第一段等于文件头时间戳） |
| `segmentInfo` | uint8 | 位 7 `pauseType`（0 暂停，1 本段结束），位 6–0 `segmentType`（间歇 / 课程类型）；A 层拆成这两个名字 |
| `totalDistance` | uint32 | 本段距离（米） |
| `totalDuration` | uint32 | 本段时长（秒；真机每段都等于 `recordCount`） |
| `totalSteps` | uint32 | 本段步数 |
| `segmentPace` | uint16 | 本段配速（秒/公里） |

骑（协议 23，v5；段头 23 字节）：`initialAltitude` f32、`recordCount` u32、`resumeTimestamp` u32、`segmentInfo` u8、`totalDistance` u32、`totalTime` u32、`totalSpeed` u16（0.1 km/h）。

真机：跑的文件 16 段，骑的文件 1 段；两份文件的最后一段都正好结束在文件末尾，没有剩余字节。

### 6.2 四维有效位

- 字段表按 APK 数组顺序，只取 `supportVersion ≤ 文件版本 ≤ maxSupportVersion` 的项；nibble 只为入选的项递增。第 k 个入选字段（从 0 数）用有效性标志第 `k // 2` 字节的高 4 位（k 为偶数）或低 4 位（k 为奇数）。
- nibble 内的位：`8` = 字段存在（`exists`），`4` / `2` / `1` = 第一 / 第二 / 第三个子值有效。子值按 high → middle → low 的顺序占位：只有一个子值的字段（`heartRate`、`pace`、`cadence`……）看 `4`；两个子值的（`newCalories` / `newSteps`）看 `4`、`2`；三个子值的看 `4`、`2`、`1`。
- `exists=0` 的字段**不占字节**。`exists=1` 的字段占它的字节数，按小端无符号整数读出；每个子值按自己的位段取出：子值占满整个字段时取整个值，否则取 `(值 >> 起始位) & (2^位数 − 1)`。有效位为 0 的子值不输出。
- 点长 = 入选且存在的字段的字节数之和。读一段的点之前，先检查 `recordCount × 点长` 不超过剩余字节；整个文件至多 1,000,000 个点（`MAX_POINTS`）。

### 6.3 跑（协议 22）v8 的字段

顺序即字节顺序：

| # | 字节 | 子值（位段） | A 层名 |
|---|---|---|---|
| 1 | 1 | [7..4] / [3..0] | `newCalories` / `newSteps` |
| 2 | 1 | [7..0] | `heartRate` |
| 3 | 1 | [7] / [6] / [5..0] | `isKilometer` / `isAltitudeRising` / `altitudeChange`（0.1 m） |
| 4 | 1 | [7..0] | `newDistance`（**分米**） |
| 5 | 1 | [7..0] | `stride`（厘米） |
| 6 | 4 | [31..26] / [25..13] / [12..0] | `newImpactForce` / `newContactTime` / `newFlightTime` |
| 7 | 1 | [7..0] | `contactRatio` |
| 8 | 1 | [7..0] | `cadence`（步/分） |
| 9 | 2 | [15..0] | `pace`（秒/公里） |
| 10 | 2 | [15..0] | `equalIntensityPace` |
| 11 | 2 | [15..0] | `runningPower` |
| 12 | 2 | [15..0] | `verticalStrideRatio`（×10） |
| 13 | 2 | [15..0] | `verticalAmplitude`（×10） |

APK 的版本区间（`FourDimenDataInfo` 的 `supportVersion` / `maxSupportVersion`）：`newCalories/newSteps`、`heartRate`、海拔三元组、1 字节 `newDistance` 自 v1；`stride` 自 v2；冲击力三元组自 v4；`contactRatio`、`cadence`、`pace` 自 v5；`equalIntensityPace`、`runningPower` 自 v6；`verticalStrideRatio`、`verticalAmplitude` 自 v8。海拔三元组和 1 字节 `newDistance` **只到 v8**：v9 起换成 4 字节 `altitude`（×10，65535 表示无效）和排在 `heartRate` 之后的 2 字节 `isKilometer/newDistance`。所以 v9 不能按这张表读，SDK 也不启用它（D1）。

### 6.4 骑（协议 23）v5 的字段

| # | 字节 | 子值（位段） | A 层名 |
|---|---|---|---|
| 1 | 1 | [7..4] / [3..0] | `newCalories` / `workoutcount` |
| 2 | 1 | [7..0] | `heartRate` |
| 3 | 1 | [7] / [6] / [5..0] | `isKilometer` / `isAltitudeRising` / `altitudeChange`（0.1 m） |
| 4 | 1 | [7..0] | `newDistance`（**米**） |
| 5 | 2 | [15..0] | `speed`（0.1 km/h） |
| 6 | 1 | [7..0] | `cadence`（踏频） |

版本区间：`newCalories/workoutcount`、`heartRate`、海拔三元组、`newDistance` 自 v1、**只到 v5**；`speed`、`cadence` 自 v3；v6 起同样换成 4 字节 `altitude` 和 2 字节 `isKilometer/newDistance`。

### 6.5 两个标志向量

与真机文件同形（不含个人数据），测试里是 `tests/fds_fixtures.py` 的 `RUN_FLAGS_LIVE`、`RIDE_FLAGS_LIVE`、`RIDE_FLAGS_NO_CADENCE`：

- **跑 v8，`FF FF FA BF FB BF FF`**（7 字节，13 个字段 nibble + 1 个填充 nibble）：
  - 13 个字段全部 `exists=1`，点长 1+1+1+1+1+4+1+1+2+2+2+2+2 = **21 字节**；
  - 第 6 个 nibble `A`（1010）：冲击力三元组只有中间的 `newContactTime` 有效，`newImpactForce`、`newFlightTime` 不出现；
  - 第 7、10、11 个 nibble `B`（1011）：`contactRatio`、`equalIntensityPace`、`runningPower` 存在、占字节，但唯一的子值无效，不出现。
- **骑 v5，`DF FF FB`**（3 字节）：
  - 第 1 个 nibble `D`（1101）：`newCalories` 有效，`workoutcount` 无效；
  - 第 6 个 nibble `B`（1011）：`cadence` 存在、占 1 字节，但无效（真机没有踏频传感器）；
  - 点长 1+1+1+1+2+1 = **7 字节**。
- 把骑 v5 的踏频 nibble 改成 `3`（`exists=0`），即 `DF FF F3`：`cadence` 不占字节，点长变成 6。

### 6.6 距离倍数、海拔与整公里点

- 距离倍数（APK `getDistanceRecordTimes`）：协议 9、10、21、23 为 1，其余为 10。点里的 `newDistance` 除以它得到米：跑（22）是分米，骑（23）是米。
- 海拔（APK `SportRecordBaseParser.setSportRecordItemValue`，类型 7 / 8）：每段从 `initialAltitude` 开始；某一秒 `isAltitudeRising` 和 `altitudeChange` 都有效时，`海拔 += (isAltitudeRising ? +1 : −1) × altitudeChange / 10`，该秒的海拔就是累加后的值；任一无效时该秒没有海拔，累计值不变。这就是 app 画海拔图用的值。
- `isKilometer` 为 1 的秒是整公里点：真机两次运动的整公里标记个数都等于汇总距离的整公里数。

## 7. GPS v2 布局

APK `SportGpsParser`、`OneDimenDataParser`；SDK `fds/layouts.py` 的 `GPS_FIELDS`。

- GPS 文件用"一维"字段表：字段按表的顺序，只取 `supportVersion ≤ 文件版本` 的项。**每个字段在有效性标志里占 1 位**（高位在前）；无效的字段**照样占字节**，只是不出值。这与 record 的四维规则不同。
- 字段表：

  | 类型号 | 字节 | 起始版本 | A 层名 |
  |---|---|---|---|
  | 0 | 4 | v1 | `time`（uint32，真实 Unix 秒） |
  | 1 | 4 | v1 | `longitude`（float32） |
  | 2 | 4 | v1 | `latitude`（float32） |
  | 3 | 4 | v2 | `locationHorizontalAccuracy`（float32） |
  | 4 | 2 | v2 | 速度 + 来源（uint16，A 层拆成 `locationSpeed`、`gpsSource`） |
  | 5 | 0 | v2 | 来源的有效位（不占字节） |
  | 6 | 4 | v3 | `altitude` |
  | 7 | 4 | v3 | `hdop` |

- 所以 v2 的点是 18 字节，有效性标志 1 字节（真机 `0xFF`，前 6 位对应 v2 的 6 个字段）。正文是连续的点，一直到文件末尾；末尾不够一个点的字节丢弃并加 note。真机两份 GPS 文件都正好整除。
- **经度在前、纬度在后。** 点间隔绝大多数是 1 s；暂停处有几十到上百秒的间隔。
- 速度与来源（`SportGpsParser.processOneRecordData`）：`speed = ((u16 & 0xFFF0) >> 4) / 10`，`gpsSource = u16 & 0x0F`（只有类型 5 有效时才取来源）。APK 没写速度的单位。A 层按 schema v3 的位域名给 `locationSpeed`（位 15..4 的存储值，不除以 10）和 `gpsSource`（位 3..0）。
- `time`、`longitude`、`latitude` 任一无效，app 丢弃整条轨迹（`processRecordList` 返回 null）。SDK 照做（D23）：A 层照常给解码结果，`track=None`，加 note。
- v4 起 app 改走 `parseTgcGps`（带"特征文件"），三项全能（17）和滑雪（21）走 `parseTriItemsGps`：都不在本期范围。
- 合成的 A 层点：

  ```json
  {"time": 1700000006, "longitude": 20.000099182128906, "latitude": 10.000100135803223,
   "locationHorizontalAccuracy": 4.5, "locationSpeed": 30, "gpsSource": 0}
  ```

  float32 原样转成 Python float，不四舍五入（20.0001 存成 float32 后就是这个数）。

## 8. SDK 映射

### 8.1 A 层（`raw`）

`DomainResult.raw` 每次运动一个 dict，与 `records` 同一下标：

```json
{"workout": {"...": "解码后的 SportBasicReport，与 workouts 的 raw 同一对象"},
 "record": {"header": {"timestamp": 1700000000, "timezone": 32, "version": 8, "protoType": 22,
                       "encryptionCompressionMode": 0, "validityFlags": "fffffabffbbfff"},
            "segments": [{"initialAltitude": 50.0, "recordCount": 2, "resumeTimestamp": 1700000000,
                          "pauseType": 1, "segmentType": 4, "totalDistance": 5, "totalDuration": 2,
                          "totalSteps": 6, "segmentPace": 400,
                          "dataPoints": [{"newCalories": 1, "newSteps": 3, "heartRate": 120,
                                          "isKilometer": 0, "isAltitudeRising": 1, "altitudeChange": 2,
                                          "newDistance": 25, "...": "..."}]}]},
 "gps": {"header": {"...": "同上；文件里没有 fileType，是哪种文件由键 record / gps 表示"},
         "gpsPoints": [{"time": 1700000006, "longitude": 20.000099182128906, "...": "..."}]}}
```

- 字段名取自 `schemas.zip`（D6）；值是**存储值**，不缩放（协议 22 的 `newDistance` 就是分米）。缩放只在 B 层映射里做。
- 无效的子值和 `exists=0` 的字段不出现在点里（与 app 一致）。
- `"record"` / `"gps"`：没读到为 `null`；只读到头部（版本或模式不支持）时只有 `header`；否则是整个解码结果。解析中途越界的文件整个为 `null`，不返回半截数据。
- **永不出现**：`sid`、`did`、`suffix`、`server_key`、URL、`obj_key`、`obj_name`、`expires_time`。
- 需要解密后原始字节的人：`mifit.fds` 的底层函数 `urls.request_urls` → `download.FdsDownloader().fetch` → `cipher.decrypt_body`（见 `mifit/fds/__init__.py`）。CLI 不落盘二进制。

### 8.2 B 层

`WorkoutDetail(workout, samples, track)`：`workout` 是 `workouts` 给同一次运动的 `Workout`；`samples` 是 `list[WorkoutSample]`，`track` 是 `list[TrackPoint]`。`None` = 没读（不支持的运动或版本、文件不存在、下载 / 解密 / 解析失败，都有 note；`track=False` 时 `track` 为 `None` 且没有 note）；`[]` = 读到了文件但里面没有点。

`WorkoutSample`（每个点一条，跨段按文件顺序）：

| B 字段 | 来源 | 规则 |
|---|---|---|
| `epoch_s` | 段 `resumeTimestamp` + 段内序号 | 真实 Unix 秒，不加 app 的显示时区平移（D11）；两条样本之间的间隔就是暂停 |
| `heart_rate_bpm` | `heartRate` | `0`（设备的"无读数"）或无效 → `None` |
| `distance_m` | Σ`newDistance` ÷ 距离倍数 | 从运动开始累计；某秒没有该值 → 该秒 `None`、累计不加。累计在存储单位里做，最后才除，不积累舍入误差 |
| `steps` | Σ`newSteps` | 只在协议 22；从运动开始累计 |
| `cadence_spm` | `cadence` | 只在协议 22；`0` → `None` |
| `pace_s_per_km` | `pace` | 只在协议 22；`0` → `None` |
| `speed_mps` | `speed` ÷ 36 | 只在协议 23（0.1 km/h → m/s）；`0` 是真实的静止，保留为 `0.0`（§10 Q6）；无效 → `None` |
| `altitude_m` | 段 `initialAltitude` + Σ(±`altitudeChange` / 10) | §6.6 的规则；该秒变化无效 → `None`；`initialAltitude` 不是有限值 → 整段 `None`。暂进 B（D21），跑步极值与汇总差 2–3 m，待真机定（§10 Q4） |

`TrackPoint`（每个 GPS 点一条）：

| B 字段 | 来源 | 规则 |
|---|---|---|
| `epoch_s` | `time` | 真实 Unix 秒 |
| `latitude_deg` / `longitude_deg` | `latitude` / `longitude` | float32 原样；不是有限值、超出 ±90 / ±180、或恰为 (0, 0) 的点丢弃，丢弃个数进 note |

小米字段名只出现在 `domains/workout_detail.py` 的映射里，`models/` 里没有。SDK 不输出 GPX、TCX、FIT 等任何文件格式：`TrackPoint` 和 `WorkoutSample` 是给上层自己拼导出用的中立数据。

### 8.3 只留在 A 层

含义未核实（设计 §2 反目标），只在 `raw` 里：

- 逐秒卡路里 `newCalories`：跑的总和与汇总差 0.3%（骑的精确相等，但两种运动一起留 A）。
- `stride`、`verticalStrideRatio`、`verticalAmplitude`：均值与汇总只差约 1%。
- 冲击力三元组 `newImpactForce` / `newContactTime` / `newFlightTime`：没有可对照的汇总。
- `contactRatio`、`equalIntensityPace`、`runningPower`：真机文件里无效。
- `workoutcount`、骑行的 `cadence`：真机文件里无效（没有踏频传感器，D20）。
- `isKilometer`：与汇总一致，但累计距离已能给出整公里点。
- 海拔的原始位（`isAltitudeRising`、`altitudeChange`）：`altitude_m` 由它们算出。
- 段的字段（`segmentType`、`pauseType`、段合计）：含义未核实（§10 Q3）；暂停已经体现为 `epoch_s` 的间隔。
- GPS 精度 `locationHorizontalAccuracy`：中位数几米，像水平精度（米），未核实。
- GPS 速度 `locationSpeed` 与 `gpsSource`：速度与由轨迹算出的速度之比只有 0.87–0.95，单位不明（D19）。

### 8.4 核对依据

设计阶段的离线核对（spec §4.9，解析结果对同一次运动的 `SportBasicReport`）：

| 量 | 跑（22 / v8） | 骑（23 / v5） |
|---|---|---|
| 点数 / `duration` | 0.997 | 0.998 |
| Σ`newSteps` / `steps` | 1.000 | — |
| Σ`newCalories` / `calories` | 0.997 | 1.000 |
| 心率最大、最小（非 0）与 `max_hrm`、`min_hrm` | 相等 | 相等 |
| `cadence` 最大与 `max_cadence`；`pace` 极值与 `max_pace` / `min_pace` | 相等 | — |
| `speed` 最大 ÷ 10 − `max_speed` | — | ≤ 0.05 |
| 首个样本时刻 − `start_time` | 0 | −1（样本从 `value.time` 起算） |

推送前的本机真实文件闸门（D25，2026-10-15）：脚本放在 `work/m6/scripts/`（不进仓库），禁止一切网络连接，用发布的代码解析已保存的 4 份文件，只打印布尔值、条数和比值：

- 4/4 份文件：头部与汇总一致，有对应布局，解码不抛异常，剩余字节为 0。
- 跑：累计步数 = 汇总步数；末样本累计距离 / `distance` = 1.000；心率最大、最小（非 0）与汇总相等；轨迹（只算间隔 ≤ 10 s 的相邻点）长度 / `distance` = 0.99；丢弃的轨迹点 0。
- 骑：Σ`newCalories` / `calories` = 1.000（精确相等）；末样本累计距离 / `distance` = 1.000；心率极值相等；轨迹长度比 1.02；丢弃的轨迹点 0。
- 海拔（不是闸门，供 §10 Q4）：重建海拔的最高 / 最低与汇总 `max_height` / `min_height` 之差，跑约 −2 m / −3 m，骑 +0.1 m / +0.1 m。
- 整条链路离线：用保存的列表、链接答复和下载正文，通过假的 `Transport` 和真的 `FdsDownloader`（假 http）跑 `fetch_workout_detail`：保存的正文用 `obj_key` 解密后与已解密文件逐字节相同；窗口内 4 次运动，2 次（跑、骑）有样本和轨迹，另 2 次（`proto_type` 8）只有"不支持"的 note，没有别的 note；2 次链接请求的形状正确，请求里的每个 `suffix` 都是服务端答复过的键；输出里没有 `sid`、URL、`obj_key`、`obj_name`、`suffix` 或 `server_key`。

## 9. `schemas.zip` 的语义（给以后的新版本）

- APK `assets/schemas.zip`（72 项，`.version` 1.2.3）是 iOS / Android 共用的 JSON 二进制描述。`index.json` 把头部数字映射到活动名（运动：`22→outdoor_foot`、`23→outdoor_non_foot`、`8→free_training`……）和文件类型（`0 record / 1 report / 2 gps / 3 hrr`），再按版本列出 schema 文件。
- 它只覆盖**新版本**：跑 record 只有 v9，骑 record 有 v6 / v7，GPS 有 v3–v5。app 只在精确匹配到（活动, 文件类型, 版本）时才用它；本账号的 record v8 / v5、GPS v2 都不在里面。
- 它的字段名（`newDistance`、`heartRate`、`cadence`……）就是 A 层的命名来源（D6）；字段的语义说明（单位、×10）是核对的线索。
- 解释器 `BinarySchemaDecoder` 的语义：小端；`bitField` 低位在前，除非标了 `highBitFirst`；`exists` 指针为 0 时元素不占字节；`valid` 指针为 0 时元素占字节但不出值；`itemCount: "EOF"` 读到文件末尾；`scale` / `offset` 做线性变换。
- 用它解析时，头部要先按 `constructSchemaBinaryData` 改写（§5）。
- 本期不实现它，也不随包分发 `schemas.zip`（D2：那是小米的资产，放进 MIT 公开仓库有再分发问题）。以后用户的设备产出新版本时，两条路：按它的定义给 `LAYOUTS` 加手写的表，并先用真实文件核对；或者实现 `BinarySchemaDecoder` 的 Python 版，运行时从用户本地的 APK 读 schema。

## 10. 未决

以下问题只能在收尾的真机会话里和用户一起回答（编号同 spec §16）：

- **Q1** 轨迹起止、距离与 app 是否一致。跑步那次有暂停：app 的距离是否只算运动中？我们的运动中轨迹长度 / 汇总距离 = 0.99。
- **Q2** 心率曲线形态与 app 是否一致；心率读数为 0 的秒在 app 里是断点还是插值？
- **Q3** 跑步那次是一个分段课程：app 是否按段展示？若是，`segmentType` 的含义能否借此核实、进 B？
- **Q4** 海拔：骑行的重建海拔与汇总极值一致，跑步差 2–3 m。app 的海拔图是否就是我们重建的曲线？决定 `altitude_m` 留在 B 还是挪回 A。
- **Q5** app 的每公里配速分段与由样本算出的是否一致；`pace` 是瞬时配速还是平滑值？
- **Q6** 骑行速度曲线与 app 是否一致；速度为 0 的秒（停车）在 app 里怎么显示，决定 `speed_mps=0.0` 是否保留。
- **Q7** 逐秒卡路里、步幅、垂直振幅等是否值得进 B：取决于用户是否在 app 里看这些图。
- **Q8** 非 cn 地区的对象存储主机名（只有一个 cn 账号，无法自测）：白名单 `.fds.api.xiaomi.com` 是否需要放宽。
- **Q9** 用户今后会记录哪些运动类型（户外步行、徒步、游泳？），手表固件会不会升级到产出 record v9 / GPS v3+ 的版本：决定 D1 / D2 的后续工作量。
- **Q10** 核对轨迹形状时，用户是否愿意在本机打开一张不带底图的 SVG。
