# 09 会话寿命与自动续期（T2，2026-10-14，APK 3.59.1 静态分析）

目的：T2。弄清会话存了什么、app 自己怎样在 serviceToken 过期后续期，并把 `auth/renew.py::renew_session` 实现的请求/响应对应关系记下来，作为后续真机核对（T2 Task 5）和寿命观测的依据。

本文只有字段名、类名、方法名和合成示例，不含任何真实 token、id 或健康数值。§1–§4 的 APK 结论全部是静态分析（`tools/rea.sh inspect-android-method`，输出留在 scratch，不入库），**续期本身尚未真机验证**。

## 1. 会话里存了什么；为什么 T2 之前的会话不能续期

- `~/.mifit/session.json`（`auth/session.py::Session`）：
  - T2 之前就有：`user_id, c_user_id, service_token, ssecurity, region, created_at, extra_cookies{cUserId, userId}`。
  - T2 Task 1 新增：`pass_token`（账号级 passToken）、`device_id`（登录时发给账号服务的随机设备 id，16 位大写十六进制）、`renewed_at`（最近一次自动续期的 UTC 时间，`null` = 从未续期）。
- `created_at` 是**登录时间**（UTC，`…Z`），续期时保留；不另设 `login_time` 字段（改名会破坏旧文件的加载）。
- T2 之前的 `exchange_sts` 拿到 passToken 后直接丢掉，所以旧会话只有 serviceToken + ssecurity。**这两样不能自助续期**——这就是 notes/04 §1 "续期依赖账号 SDK" 的确切含义：依赖的是账号 SDK 保存的 passToken。旧会话 `can_renew()` 为假，T2 上线后需要 `mifit login` 一次。
- `Session.__repr__` 隐藏 `service_token / ssecurity / pass_token / device_id`，`extra_cookies` 只显示 cookie 名。

## 2. APK 调用链（app 自己怎样续期）

1. `fitness.account.token.VerifyToken.onHttpResponseBefore`：健康 API 返回 401 时最多重试 3 次（`mCount >= 3` 直接返回 401）；只有重建后的请求与被拒的请求**不同**时才重试。`cookie(..., refreshToken=true)` 只在缓存 token 等于刚被拒的 token 时才强制刷新。
2. `MiAccountInternalManager.getServiceToken(sid, refresh)` → `XiaomiAccountManager.getServiceToken`，再 `refreshServiceToken`。
3. `passport.accountmanager.OwnAppXiaomiAccountAuthenticator.getAuthTokenBundle`：构造 `PassTokenLoginParams{userId, passToken, serviceId}`，调用 `AccountHelper.getServiceTokenByPassToken`。
   - 没有 passToken → 弹登录界面。
   - `InvalidCredentialException` → 清掉凭据、要求重新登录。
   - 返回了新 passToken → `updateAccountInfo` 写回（passToken 轮换）。
4. `AccountHelper.getServiceTokenByPassToken`：加上 `deviceId`（哈希后的设备 id）和 `uDevId`，调用 `XMPassport.loginByPassToken`。
5. `XMPassport.loginByPassToken`：`GET https://account.xiaomi.com/pass/serviceLogin`，参数 `sid`、`_json=true`、`_locale`；cookie `userId`、`passToken`、`deviceId`（可选 `uDevId`、`pass_o`、`userSpaceId`）。响应交给 `processLoginContent`——与扫码登录 `loginByLongPolling` **同一个解析器**：
   - `code≠0` 为错误（`70016` = 凭据无效，附 meta-login 数据）；
   - `securityStatus≠0` = 需要安全确认（notification）；
   - `userId`/`passToken` 取自 JSON 或响应头/cookie（取哪边由 `isResultFromCA` 决定）；
   - `ssecurity`/`nonce` 取自 JSON 或 `Extension-Pragma` 头；
   - `location` 交给 `getServiceTokenByStsUrl`，即我们 `exchange_sts` 实现的 STS 交换（clientSign = `Base64(SHA1("nonce=<nonce>&<ssecurity>"))`，取 `<sid>_serviceToken`）。
6. `XMPassport.refreshPassToken`（`/pass/login/passtoken/refresh`）需要设备 `fid` 签名，无法复现，**不用**。

结论：续期只需 userId + passToken（+ deviceId），与 app 给自己账号续期的方式一致。参考实现 Mi-Fitness-Sync 没有续期代码，只在密码登录时发 `deviceId = secrets.token_hex(8).upper()`，并按 JSON → cookie → 头 的顺序取 `passToken`/`cUserId`。

## 3. `renew_session` 实现的请求与响应映射

`renew_session(session, http=None, *, sid="miothealth") -> Session`（`src/mifit/auth/renew.py`）：

**请求**（只有这一个账号请求，加上 STS）

| 项 | 值 |
|---|---|
| URL | `GET https://account.xiaomi.com/pass/serviceLogin`（`RENEW_URL`） |
| 查询参数 | `sid=<sid>`、`_json=true` |
| cookie | `userId`、`passToken`、`deviceId`（会话没有 device_id 时不发） |
| 其他 | `timeout=30`；`allow_redirects=False`（`requests` 会把 dict cookie——包括 passToken——带到别的主机的跳转目标） |
| 不发 | `_locale`、`uDevId`、`pass_o`、`userSpaceId` |

会话没有 passToken 时，在发任何请求之前就报 `RenewalError`。

**响应**（`qr._parse_login_content`，与扫码长轮询共用；`known` = 会话里存的 `userId/passToken/cUserId`）

| 情况 | 处理 |
|---|---|
| HTTP 非 2xx（含 3xx 跳转） | `RenewalError` |
| `&&&START&&&` 前缀 | 去掉后按 JSON 解析；不是 JSON 对象 → `RenewalError`（不回显响应体） |
| `code≠0` | `RenewalError`，`.code` 保留（如 70016："Xiaomi refused the stored passToken (code 70016)."） |
| `securityStatus≠0` | `RenewalError`（不是 `NotificationRequiredError`：无人值守的续期不能去做安全确认） |
| `userId`/`passToken`/`cUserId` | JSON → 响应 cookie（cookie jar + 原始 `Set-Cookie`）→ 会话里存的值 |
| `ssecurity`/`nonce` | JSON → `Extension-Pragma` 头 |
| `location` | 只取 JSON |
| 仍缺字段 | `RenewalError`，消息只列字段名 |
| STS | `exchange_sts(result, sid, http, device_id=session.device_id)`，用**新**的 nonce 和 ssecurity 计算 clientSign；失败 → `RenewalError` |

**结果**：新的 `service_token`、`ssecurity`、STS cookie（`extra_cookies`）；保留 `region`、`created_at`（登录时间）、`device_id`；`pass_token` 取服务器返回的（轮换），没返回则保留旧的；`renewed_at` = 当前 UTC 时间（`…Z`）。传入的 `session` 对象本身不变。

**错误**：`RenewalError` 是 `AuthError` 的子类，所以现有的 `except AuthError`（包括 CLI）仍以 "Run `mifit login`" 收尾。所有 `RenewalError` 都 `from None` 抛出，消息只说原因，不含任何凭据、cookie 值、STS URL 或安全确认 URL。`requests` 自身的网络异常（超时、连接失败）原样向上传。

## 4. 未知项

- **passToken 是否绑定 deviceId**：app 每次都带哈希后的设备 id；我们每次登录生成一个随机 `device_id`，登录（QR `loginUrl`、长轮询）和续期都发同一个。服务器是否校验二者一致、换 deviceId 是否会让 passToken 失效，未知。T2 之前的会话没有 device_id。
- **passToken 寿命**：未知。app 在 `InvalidCredentialException`（对应 `code=70016`）时要求重新登录；我们同样报 `RenewalError` 并提示 `mifit login`。
- **过期 401 与签名 401 能否区分**：notes/04 §3——签名/鉴权错误也返回 HTTP 401（业务 code=0）。目前不知道过期时的 401 响应是否有不同之处。因此 T2 的策略是：只在 401 时续期，每个请求最多 1 次、每个 Transport 最多 3 次；续期后**仍然** 401 视为签名问题，该 Transport 停止续期（Task 4）。
- **`serviceLogin` 在 `_json=true` + passToken cookie 下是否直接回 JSON**：静态分析如此（`loginByPassToken` 走 `processLoginContent`），但未真机验证；若服务器改回 302 跳转，我们按设计报 `RenewalError`，不跟随。
- **区域**：续期结果里的区域提示被忽略，沿用会话里存的 `region`。

## 5. 寿命观测

- **2026-10-14**：03:18Z 扫码登录，同日约 10:20Z 会话仍可用 → serviceToken 寿命 **≥ 7 h**。尚未观察到过期。
- 自动续期会把过期对用户隐藏，所以寿命靠 `~/.mifit/session-log.jsonl`（T2 Task 3 引入）观测。每行一个 JSON 对象，只有四个键：

  ```json
  {"event": "unauthorized", "at": "2026-10-15T03:18:19Z", "login_age_s": 86400, "token_age_s": 86400}
  ```

  （合成示例。）`event` 取 `login`、`unauthorized`、`renewed`、`renew_failed`；`at` 是 UTC 时间；`login_age_s` 从 `created_at` 算起；`token_age_s` 从 `renewed_at` 算起（从未续期则从 `created_at`）。日志不含 token、id 或任何 cookie 值。
- 只有从 `~/.mifit` 读会话（或自己登录）的客户端才写日志；显式传入的会话只在内存里续期，不写。
- 读法：
  - serviceToken 寿命 ≈ 每次 `login`/`renewed` 之后第一条 `unauthorized` 的 `token_age_s`。这是上界：401 只在实际发请求时出现，所以 token 至少活到上一次成功请求、至多活到这次 401。
  - `unauthorized` 后紧跟 `renewed` = 自动续期成功；紧跟 `renew_failed` = 需要 `mifit login`，此时的 `login_age_s` 是 passToken 寿命（或 deviceId 绑定问题）的线索。

（Task 3–5 在此追加：日志格式落地、真机续期结果、自然过期的观测。）
