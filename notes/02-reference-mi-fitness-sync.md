# 参考项目：kevinkwee/Mi-Fitness-Sync（MIT）

来源：https://github.com/kevinkwee/Mi-Fitness-Sync
许可证：**MIT**（可自由复用/修改/商用，保留版权声明即可）
语言：Python 3.12+ · 定位：拉取小米运动健康的**运动记录**并导出 GPX/TCX/FIT、同步到 Strava
本地副本（只读参考，勿直接运行）：`reference/mi-fitness-sync/`

## 为什么对我们极其有价值
这个项目已经用纯 Python 把我们路径 B 最难的部分**全部实现并验证过了**，而且结论和我们独立静态分析**逐条吻合**（sid `miothealth`、base `hlth.io.mi.com/app/v1/`、地区路由、签名方案）。等于帮我们把地基打好了。

**关键：它的登录是纯账号密码流程，完全不需要手机/root。**

## 它直接给了我们什么（可复用）
- **登录** `auth/client.py`：`serviceLogin` 取 meta → `serviceLoginAuth2`（密码 MD5 大写）→ 解析 `passToken/cUserId/ssecurity/nonce/location` → STS（`clientSign=Base64(SHA1("nonce=<n>&<ssecurity>"))`）换 `{sid}_serviceToken`。已处理：验证码(87001)、短信二次验证(81003 step2)、通知确认(notificationUrl)。
- **签名/加密** `activity/crypto.py`（与我们反编译结论一致）：
  - `signedNonce = Base64(SHA256(b64decode(ssecurity) + b64decode(nonce)))`
  - 参数 RC4 加密（key=b64decode(signedNonce)，前置消费 1024 个 0 字节）
  - `signature = Base64(SHA1("GET&<path>&<sorted k=v>&<signedNonce>"))`，另有 `rc4_hash__`
  - 发送 query：加密后的 `data`、`rc4_hash__`、`signature`、`_nonce`
  - 响应是 Base64(RC4) 密文，用同一 signedNonce 解密为 JSON
- **传输** `activity/transport.py`：地区路由（cn 原host / 其它加 `<region>.` 前缀 + `region_tag` 头）、cookie（`serviceToken`、`cUserId`、`userId`、`locale`）、按 IP 查地区（`region.hlth.io.mi.com/.../user_region_by_ip`，固定 `auth_key=rwelJuWBFJxmbMKD`）。
- **地区映射** `activity/region_mapping.py` + APK `assets/server_config/servers.json`（247 国家码 → cn/de/i2/ru/sg/us）。
- **端点**：
  - `app/v1/data/get_sport_records_by_time`（运动记录）
  - `app/v1/data/get_fitness_data_by_time`（**健康数据，含我们要的日常指标**）
  - `healthapp/service/gen_download_url`（FDS 原始文件：逐秒心率/GPS，AES 加密 blob）
- **FDS 二进制解析**：`docs/mi-fitness-fds-findings.md` 有 proto_type 1–25 的解析细节（运动轨迹/逐秒数据）。

## 它没覆盖、我们仍需补的（针对你的目标）
它主打**运动记录**。你要的**日常指标**（步数、全天心率、睡眠、血氧、体重/身体成分）要通过 `get_fitness_data_by_time` 的 `key` 过滤取得，但其文档里的 HuaMi key 偏运动/HM 设备：
`huami_rest_heart_rate`、`huami_manual_heartrate_record`、`huami_all_day_stress_record`、`huami_pai_record`、`huami_manual_measure_record`(手动血氧)、`huami_vitality_index`、`huami_training_load_record`、`huami_sport_record` 等。
→ **待办**：确定步数/睡眠/全天血氧/体重对应的 `key` 与 `value` JSON 结构。我们本地 Room 表名（`step_record`/`sleep_segment`/`spo2_record`/`weight_item_record`…，见 01 文档）是线索，但云端 key 可能不同——用登录后的客户端实际探测最快。

`GetFitnessDataByTime` 请求字段：`key`(数据类型) / `startTime` / `endTime`(秒) / `reverse` / `next_key`(分页)。
响应 `FitnessDataResultByTime`：`data_list[]`(每条 `sid/key/time/value(JSON)/zone_name/zone_offset/deleted`) / `next_key` / `has_more`。

## 建议的做法
以这个 MIT 项目为**基座**（fork 或 vendor 进我们项目），复用它的 auth+crypto+transport，然后：
1. 用 `login` 拿到 `miothealth` 的 token（本地账号密码，可能触发短信/验证码）。
2. 针对 `get_fitness_data_by_time` **探测**步数/心率/睡眠/血氧/体重的 key 与 value 结构。
3. 封装成你自己的只读 SDK；写/删（`up_fitness_data`/`delete_fitness_data`）最后再谨慎处理。

风险同 01 文档第 5 节：优先只读、低频、模仿官方；登录环节是主要风控点。
