# 小米运动健康 (Mi Fitness) 逆向调查 — 阶段一：静态分析发现

> 目标 APK：`com.mi.health` v3.59.1 (versionCode 359001)
> SHA-256：`46100649d47000e8886d94cfbb34c18706e492a5d3aaa7f34cd4785841ffc254`
> minSdk 26 / targetSdk 35 · allowBackup=**false** · 49984 classes (JADX) / 106581 defined (dex)
> 分析工具：REA 6.4.0 (JADX headless 0.7.1, apktool 3.0.3, adb 37.0.1)
> 本文所有结论来自**静态反编译**，尚未在设备上验证。

---

## 0. 结论速览

小米运动健康的本人健康数据有三条可能的访问路径：

| 路径 | 机制 | 需要 root？ | 可行性（静态判断） |
|---|---|---|---|
| **A. 本地 Room 数据库** | `/data/data/com.mi.health/databases/<account>/<region>/` 下的 SQLite | **大概率需要**（allowBackup=false，私有目录） | 结构已完全掌握；读取被 Android 沙盒限制 |
| **B. 云端 HTTP API** | `hlth.io.mi.com`（按地区路由），小米账号 serviceToken 鉴权 | 否 | 协议对称完整（读/写/删）；需抓包确认签名 |
| **C. 设备内 IPC（ContentProvider）** | exported `DataContentProvider` | 否 | **大概率不可行**：访问被限制为系统/小米签名/白名单应用 |

---

## 1. 本地存储层（路径 A）

### 1.1 数据库与访问类
- 主库类：`com.xiaomi.fit.fitness.persist.db.FitnessDatabase`（Room，schema version 23）
  - 实现：`FitnessDatabase_Impl`；openDelegate 标识 `("374b9a517d7a2f209432adc392a8e0ab", "82812fe02829c63a85afb6200eb950fb")`
  - 另有 `MiKitDataBase`、`FitnessSummaryConnector`
- 库文件路径按**账号 + 地区**分目录：
  `FileUtils.getDatabaseDir(ctx)/<account|"__no_account__">/<region>/`
  （源自 `FitnessDBConfiguration.makeDatabasePath`）→ 印证了地区差异问题。
- 每种指标一个 DAO（`FitnessDatabase` 暴露 40+ 个 DAO）：
  步数 `StepRecordDao` · 心率 `HrRecordDao` · 睡眠 `SleepSegmentDao` ·
  血氧 `Spo2RecordDao` · 体重 `WeightItemDao` · 血压 `BloodPressureRecordDao` ·
  血糖 `BloodSugarDao` · 压力 `StressRecordDao` · 体温 `TemperatureRecordDao` ·
  VO2max `Vo2MaxRecordDao` · 卡路里/能量/站立/PAI/活力/训练负荷 等。
- 读取入口（示例）：`StepRepository.queryStepRecordsSync(start,end): List<StepItem>`
  → 内部走 `DbGetter.getAllDailyRecord(HomeDataType.STEP, …)`，返回 Map（key `"steps"`）。
- 读写桥：`FitnessDbQueryBridge` / `FitnessDbWriteBridge`（用 `SupportSQLiteDatabase` + 原生 SQL，`value` 列用 **Gson** 解析为 JSON）。

### 1.2 统一表结构（所有日常指标通用）
```
<table>(key TEXT, sid TEXT, time INTEGER, value TEXT,
        endTime INTEGER, zoneOffsetInSec INTEGER, zoneName TEXT,
        timeIn0Tz INTEGER, endTimeIn0Tz INTEGER,
        isUpload INTEGER, isDeleted INTEGER,
        PRIMARY KEY(key, sid, time))
```
- `sid` = 数据来源/用户标识；`time` = 时间戳；`value` = 该条记录的 **JSON 负载**（实际步数/心率序列/睡眠分期等）。
- `isUpload` = 是否已同步到云；`isDeleted` = 软删除标记；`*In0Tz` = UTC0 对齐时间。

真实表名（节选，均来自 dex 内 `CREATE TABLE` 语句）：
`step_record` · `hr_record` · `sleep_segment`(多一列 `isComplete`) · `spo2_record` ·
`stress_record` · `temperature_record` · `v02max_record` · `pai_record` ·
`blood_pressure_record` · `blood_sugar_record` · `calorie_record` · `energy_record` ·
`stand_record` · `vitality_record` · `training_load_record` · `health_report_record` ·
`daily_report` / `daily_report_tmp`（聚合日报）

**体重/身体成分**（列是展开的，不是 JSON）：
```
weight_item_record(key,sid,time, weight REAL, bmi, bodyFatRate, moistureRate,
  boneMass, basalMetabolism, muscleMass, proteinRate, visceralFat, value, …)
user_info_profile(name, sex, birth, height, weight, vo2_max, max_hrm, min_hrm,
  daily_cal_goal, daily_step_goal, daily_sleep_goal, initial_weight, …)
```
另有旧版/体脂秤表：`record_weight`、`weight`、`claim_record`（含阻抗 impedance）、`virtual_account`。

### 1.3 加密情况
- 腾讯 **WCDB**（基于 SQLCipher 的加密 SQLite）**只用于旧的迁移库** `com.xiaomi.fitness.migration.*`
  （HealthSleepDatabase/HealthWeightDatabase/HealthHeartRateDatabase 等 11 个）。
- 当前主库 `FitnessDatabase` **未出现在 WCDB 工厂引用中** → 很可能是**普通未加密 SQLite**（待设备验证）。

### 1.4 路径 A 的障碍
`allowBackup=false` + 应用私有目录 → 非 root 手机无法通过 `adb backup` / `adb pull` / `run-as`（非 debuggable）读取。**此路线实际需要 root。**

---

## 2. 云端同步层（路径 B）

### 2.1 API 主机
- `hlth.io.mi.com` — 健康数据主 API
- `region.hlth.io.mi.com` — **地区路由**（解释地区差异）
- `pv.api.io.mi.com` / `api.io.mi.com` — 小米 IoT 通用
- `cnbj1.fds.api.xiaomi.com` 等 FDS — 对象存储，存原始传感器文件/运动轨迹（对应表 `fds_data_id`，列 `idBytes BLOB`）
- `hlth.research.xiaomiwear.com` — 研究用

### 2.2 协议（Retrofit 接口 `…persist.server.service.FitnessApiService`）
请求体为 JSON（Gson 序列化），经 `HttpPoint/HttpConfig` 统一处理响应。核心方法（读/写/删对称）：
- 读：`getFitnessDataByTime` · `getFitnessDataByWM`(watermark 增量) · `getLatestFitnessData` ·
  `getSportRecordsByTime/ByWM` · `getSportSummary` · `getStatDataByTime/ByWM` · `getAggregatedDataByTime/ByWM`
- 写：`uploadFitnessData` · `uploadSportRecord` · `upAggregatedData` · `syncHMDataToServer` · `syncThirdPartyDataToServer`
- 删：`deleteFitnessData` · `deleteSportRecords` · `deleteStatData`
- 封装类：`com.xiaomi.fit.fitness.persist.server.FitnessDataRequest`（单例）

### 2.2.1 服务 ID（serviceToken 的 sid）
`com.xiaomi.fitness.account.SIDs` 定义：`MIOT_HEALTH` / `MIOT_HEALTH_AT` / `MIOT_HEALTH_STAGING` / `PLATO_TO_APP_API`。
字符串池对应值（高置信推断，待运行期确认）：生产 **`miothealth`**、staging `miothealth-staging`、另有 `miothealth-onebox`。
→ 读取健康云数据需申请 **sid = `miothealth`** 的 serviceToken。相关头：`MIOT-TOKEN-V1`。开放平台另有 host `pv.hlthopen.io.mi.com`。

### 2.3 鉴权与签名（已确认方案族）
- 账号：小米 Passport，`serviceToken` + `cUserId` + `passToken`，按服务分 token（cookie `<sid>_serviceToken=…`）。
- 签名：**小米 IoT/MiCloud 家族方案**（与米家 `api.io.mi.com` 同族）——
  字符串池确认存在 `ssecurity`、`nonce`、`HmacSHA256`，响应可能 `RC4` 加密（对应 `SecretData.encryptResponse`，用到 hutool RC4）。
  典型流程：`signedNonce = SHA256(ssecurity + nonce)` → `sign = HmacSHA256(signedNonce, METHOD+path+sortedParams)`。
  → **有成熟开源参考，大幅降低难度。** 确切算法建议用一次真实抓包核对。
- 每接口的鉴权描述类 `com.xiaomi.fitness.net.url.SecretData`：字段 `sid`（用哪个 serviceToken）、
  `pathPrefix`、`encryptResponse`、`filterSignatureKeys`（参与签名的字段）、`loginPolicy`。
- base URL 来自接口类上的 `@BaseUrl(host, path)` 注解（`ApiHolder.getAnnotationUrl`）。
- 地区路由 `RegionUrlSwitcherImpl`：`cn` → 原 host；其它地区 → host 前加 `<region>.`（如 `de.hlth.io.mi.com`），并带请求头 `region_tag`。地区取自 `RegionManager.getCurrentRegion()`。
- 请求级 locale：`ParameterInterceptor` 加 cookie `locale=<当前 locale>`。

### 2.4 真实接口路径（base `https://hlth.io.mi.com/app/v1/…`，海外加地区前缀）
健康日常数据（steps/HR/sleep/SpO2/weight 等，对应 `FitnessApiService`）：
```
读  data/get_fitness_data_by_time              # 按时间区间
    data/get_fitness_data_by_watermark         # 增量（水位线）
    data/get_latest_fitness_data               # 最新
    data/get_max_watermark
写  data/up_fitness_data
删  data/delete_fitness_data
```
运动记录：`data/get_sport_records_by_time` · `data/get_sport_records_by_watermark` ·
`data/get_latest_sport_record` · `data/get_sport_category_list` · `data/up_sport_records` · `data/delete_sport_records`
聚合日报：`data/get_aggregated_fitness_data_by_time` · `…_by_watermark` · `data/up_aggregated_fitness_data`
医疗/项目/统计数据亦有对应 `…medical…` / `…project…` / `watermark` 接口。
其它：`app/v1/public/user_region_by_ip`（IP 推断地区）、`healthkit/v1/…`、`account/user/info`。

---

## 3. 设备内 IPC 层（路径 C）

- exported ContentProvider：`DataContentProvider`，authority `com.mi.health.provider.main`（进程 `:device`）。
- 另有 exported `StatusContentProvider`、`DeviceProvider`、`HealthProviderService`。
- 访问控制：`DataContentProvider.query` → `checkReadPermission` + `DataProviderManager.onIntercept`
  → 访问过滤器 `onAcceptAccess` + `isPrivilegedPackage`。
- `isPrivilegedPackage` 仅放行：**系统应用**（FLAG_SYSTEM/UPDATED_SYSTEM_APP）、**小米内部签名**（`sInternalSignatures`）、**白名单**（`mWhiteList`）。
- 结论：普通第三方 app 即使申请 `normal` 级 `DEFAULT_READ_DATA`，大多数健康 URI 仍会被拦截。**该路线整体不可行**（除非个别无过滤器的 URI，待枚举确认）。

---

## 4. 方向决定
手机**未 root 且不打算 root** → 路径 A 排除、路径 C 排除 → **采用路径 B（云端 API）**。

## 5. 路径 B 下一步（需设备，但只需插 USB，不需 root）
1. 连手机（USB 调试），读 logcat / 配合一次 MITM 代理 + 用户 CA，**抓一次真实同步请求**：
   - 确认 `FitnessApiService` 的 `@BaseUrl` host/path 与 `SecretData.sid`（健康服务的 serviceToken sid）。
   - 确认签名算法细节（nonce 来源、stringToSign 组装、是否 RC4 加密响应）。
   - 注意可能有 **证书绑定（cert pinning）**；若有，非 root 下 MITM 会失败，改为继续静态还原签名器。
2. 取得你本人账号对健康服务的 `serviceToken` + `ssecurity` + `cUserId`（登录流程）。
3. 写最小客户端：调 `data/get_fitness_data_by_time` 读最近 N 天步数 → 与 app 显示核对。
4. 核对通过后，逐接口封装为 SDK（读 steps/HR/sleep/SpO2/workouts/weight；写/删谨慎处理）。

## 6. 暂不处理（已知但本阶段不深入）
- 路径 A 的真实库文件名、`region` 目录名、主库是否明文（只有将来 root 才有意义）。
- `DataProviderManager.deploy(...)` 注册的完整 URI 列表（路径 C 已基本排除）。
- `value` 列 JSON 的逐指标字段结构（抓包/取到真实数据后对照更准）。

## 附：证据文件
- `evidence/inspect-android-package.json` — 包/权限/清单
- `evidence/AndroidManifest.xml` — 解码清单
- `evidence/classes.txt` — 全类名索引（dex 自解析）
- `evidence/sql_create.txt` — 全部 CREATE TABLE
- `evidence/methods/`、`evidence/inspect-android-class/`、`evidence/trace-android-references/` — 各反编译证据
