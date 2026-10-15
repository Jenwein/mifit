# 07 git 历史审计（T1；只报告，未改写任何历史）

- 日期：2026-10-14
- 范围：36 个提交（`git rev-list --all --count`，扫描时）；refs：`refs/heads/main`（a1e1326）、`refs/heads/t1-compliance`（69e3e8a）、`refs/remotes/origin/main`、`refs/remotes/origin/HEAD`（均为 a1e1326）；无 tag、无 stash；远端对照：一致。`git ls-remote origin` 只有 `HEAD` 和 `refs/heads/main`（a1e1326，本地已有），没有仅远端存在的 ref（无 `refs/pull/*`），未建 `refs/audit/*`。公开远端有其中 32 个提交；`t1-compliance` 上的 4 个 T1 提交（d38faa5、cee3dad、387ae45、69e3e8a）尚未推送，本次一并扫描。本文所在的提交扫描时尚不存在，它的内容另做了自查（见附录"自查"）。
- 方法：文件名清单 + 扫描全部提交的新增行与提交信息（模式见附录）+ 逐个阅读曾提交的 .md/.json。另做三项补充核对：把本机会话文件里的值逐字比对全部新增行和提交信息（只输出位置，不输出值）；把测试夹具每行解码后的 `value` 与本机核对导出（`$HOME/mifit-verify/`）的 `raw` 逐条比对；对不被正则覆盖、但计划里点名的地区查询 `auth_key` 做人工核查。
- 结论：未发现真实凭证或真实账号标识（本机会话的 6 个值在历史中一次都没有出现，扫描命中全部是代码标识符、测试合成值、公开 app 常量或提交署名）；个人健康数字除 3 个核对文档（按设计）外，还出现在路线图、2 份实现计划、notes/04、notes/05、3 个测试文件和 1 条提交信息里，且这些提交都已在公开远端上。
- 本文不含任何匹配到的值，只给"提交 / 文件 / 行"。

## 1. 曾提交过的文件名

历史中共出现 70 个路径：`docs/` 9、`notes/` 6、`src/` 24、`tests/` 19、`tools/` 7，以及 `.gitignore`、`pyproject.toml`、`LICENSE`、`README.md`、`THIRD_PARTY_NOTICES`。没有删除过的文件，没有合并提交。目录模式按任一级目录名匹配，文件模式按文件名匹配（不区分大小写）。

| 模式 | 命中 | 位置 |
|---|---|---|
| `session.json` | 0 | — |
| `*.session.json` | 0 | — |
| `.mifit/` | 0 | — |
| `secrets/` | 0 | — |
| `private/` | 0 | — |
| `evidence/` | 0 | — |
| `target/` | 0 | — |
| `reference/` | 0 | — |
| `work/` | 0 | — |
| `.env` | 0 | — |
| `*.har` | 0 | — |
| `*.pcap*` | 0 | — |
| `*.db` | 0 | — |
| `*.sqlite*` | 0 | — |
| `*.apk` | 0 | — |
| `*.dex` | 0 | — |
| `*.log` | 0 | — |
| `*cookie*` | 0 | — |
| `*token*` | 0 | — |

## 2. 凭证与账号标识

扫描了 9471 行新增内容（与 `git log --numstat` 的新增行数逐文件一致）和 177 行提交信息，共 110 处命中：代码标识符 52、测试合成值 20、公开 app 常量 1、提交署名 37、疑似真实凭证/标识 0。

| 类别 | 判定 | 位置（提交 / 文件 / 行）或计数 | 严重度 | 建议 |
|---|---|---|---|---|
| 会话序列化、Cookie 值、带密参的 URL、16 字节 base64（`session_json`、`cookie_value`、`url_secret`、`ssecurity_shape`） | 无命中 | 0 | — | — |
| 本机会话文件的 6 个值（service token、ssecurity、c_user_id、user_id 及 cookie 里的两个副本），逐字比对 | 未出现 | 0 | — | — |
| 凭证名 + 值（`cred_assign`） | 代码标识符（值是变量、参数或函数调用） | 45：`src/mifit/auth/qr.py` 9、`src/mifit/transport.py` 3、`tests/test_auth_qr.py` 10、`tests/test_crypto.py` 9、`tests/test_transport.py` 6、`tests/test_session_store.py` 3、`tests/test_cli.py` 2、`tests/test_client.py` 1、`docs/superpowers/plans/2026-10-14-mifit-foundation-core-read.md` 1（公式）、`notes/02-reference-mi-fitness-sync.md` 1（公式） | 信息 | 无需处理 |
| 凭证名 + 值（`cred_assign`） | 测试合成值（`synthetic-*`、`SYNTH-*`、顺序号、"synthetic-…" 的 base64） | 13：`tests/test_transport.py` 5、`tests/test_client.py` 4、`tests/test_cli.py` 2、`tests/test_session_store.py` 2 | 信息 | 无需处理 |
| 长 base64 串（`long_b64`） | 测试合成值（由合成密钥 `bytes(range(16))` 等算出的签名/加密向量） | 7：`tests/test_crypto.py` 7 | 信息 | 无需处理 |
| 长 base64 串（`long_b64`） | 代码标识符（用 `/` 连写的字段名、类名、方法名） | 7：`docs/superpowers/specs/2026-10-13-mifitness-sdk-design.md` 2、`notes/01-static-analysis-findings.md` 1、`notes/02-reference-mi-fitness-sync.md` 1、`notes/03-crosscheck-mi-health-lab.md` 1、`notes/04-mi-health-lab-cloud-breakthrough.md` 1、`notes/05-calorie-field-investigation.md` 1 | 信息 | 无需处理 |
| 长 base64 串（`long_b64`） | 公开 app 常量（APK 文件的 SHA-256，已与本地 APK 重新计算的值核对一致） | 1：`notes/01-static-analysis-findings.md` 1 | 信息 | 无需处理 |
| 邮箱（`email`） | 提交署名（`Co-Authored-By` 尾注，Anthropic 的公共 noreply 地址） | 37：提交信息 34、`docs/superpowers/plans/2026-10-14-mifit-t1-compliance.md` 3（计划里引用的尾注） | 信息 | 无需处理 |
| 地区查询的 `auth_key` | 公开 app 常量。正则没有命中（写法前面没有 `?`/`&`，值也短于 40 位），人工核查：同一常量在本地的上游副本和 APK 的 dex 里都有 | 5a583ea `notes/02-reference-mi-fitness-sync.md`:21 | 信息 | 保留 |
| 手机号（`phone_cn`）、MAC（`mac`） | 无命中 | 0 | — | — |
| 账号属性：地区 cn、东八区（UTC+8 / Asia/Shanghai）、用户手机型号与系统 | 不是凭证，也不能单独定位账号 | 手机型号：5a583ea `notes/03-crosscheck-mi-health-lab.md`:9；地区与时区散见 README、计划、核对文档、notes/06 和测试夹具 | 信息 | 见 §5 第 5 条 |

严重度：严重（真实凭证，需要你注销会话）/ 高（真实账号标识、邮箱、手机号）/ 信息。本次没有"严重"和"高"。

## 3. 个人健康数字

`*.md` 和 `*.json` 共 20 个路径，都没有被删除过，下表按当前版本加全部历史版本判定。夹具与本机核对导出（只有 consumption 和 sleep 两类）逐条比对：夹具行 0/9 与导出的 `raw` 相同，睡眠分段 0/3 相同。

| 文件 | 有无 | 类别 | 引入提交 | 说明 |
|---|---|---|---|---|
| `README.md` | 无 | — | — | 387ae45、69e3e8a；只有示例日期和"作者账号在 cn 区、UTC+8"的说明 |
| `docs/superpowers/plans/2026-10-14-mifit-consumption-domain.md` | 有 | 卡路里（基础代谢值、某一天的活动消耗，作为测试示例写进步骤） | 169efd8 | 两个数与 notes/05 记录的真实读数相同 |
| `docs/superpowers/plans/2026-10-14-mifit-foundation-core-read.md` | 无 | — | — | 5a583ea；示例数据为合成整数 |
| `docs/superpowers/plans/2026-10-14-mifit-sleep-domain.md` | 有 | 睡眠（一晚的深睡/浅睡/清醒分钟数）、读数日期 | 1655fc1 | 在"Live-verified shapes"一节，用来核对分期编号 |
| `docs/superpowers/plans/2026-10-14-mifit-t1-compliance.md` | 无 | — | — | d38faa5；只说明哪些文件有健康数字，不含数值 |
| `docs/superpowers/roadmap.md` | 有 | 心率（静息心率、睡眠心率各一个，M3 的核对目标）、血氧（日均与点均，M5） | cfab44f | 3450d7b、3d3b608、a1e1326 未增删这些数字 |
| `docs/superpowers/specs/2026-10-13-mifitness-sdk-design.md` | 无 | — | — | 5a583ea |
| `docs/superpowers/verification/2026-10-14-consumption-live.md` | 个人健康数字存在（按设计） | 卡路里（逐日静息/活动/摄入/总计、探测请求的应答值）、读数日期 | 79f90da | f1851a9 增补了探测表和复测结果 |
| `docs/superpowers/verification/2026-10-14-core-read-live.md` | 个人健康数字存在（按设计） | 心率（某日点数、最低、最高、均值；全量读法同上）、静息心率、睡眠心率、卡路里（app 卡路里环、步数报告卡路里、`calories` 日报，均逐日）、读数日期 | 522058c | c257ed3 是空白模板；步数和距离只记"一致"，没有数值 |
| `docs/superpowers/verification/2026-10-14-sleep-live.md` | 个人健康数字存在（按设计） | 睡眠（总时长、夜间起止时刻、分期分钟、小睡时段、分数）、睡眠平均心率、读数日期 | 3d3b608 | — |
| `notes/01-static-analysis-findings.md` | 无 | — | — | 5a583ea |
| `notes/02-reference-mi-fitness-sync.md` | 无 | — | — | 5a583ea |
| `notes/03-crosscheck-mi-health-lab.md` | 无 | — | — | 5a583ea；有手机型号，见 §2 |
| `notes/04-mi-health-lab-cloud-breakthrough.md` | 有 | 血氧（日均与点均）、心率（某日原始点数的计数） | 5a583ea | 来自 mi-health-lab 对同一账号的读取 |
| `notes/05-calorie-field-investigation.md` | 有 | 卡路里（app 卡路里环、步数报告卡路里、`calories` 日报、活动消耗，均逐日；基础代谢）、读数日期 | 169efd8 | — |
| `notes/06-daily-report-day-semantics.md` | 无 | — | — | 3450d7b；只有读数日期范围和"逐日一致"，没有步数值 |
| `tests/fixtures/hr_raw_sample.json` | 无 | — | — | f5f984d；`"sid": "synthetic-sid"`，整数示例值；导出里没有心率，无法比对 |
| `tests/fixtures/sleep_daily_sample.json` | 无 | — | — | 89a1e56；合成；与导出比对：不匹配 |
| `tests/fixtures/sleep_raw_sample.json` | 无 | — | — | a3f43b7；合成；与导出比对：不匹配 |
| `tests/fixtures/steps_daily_sample.json` | 无 | — | — | 7d05ae1、7a17a34；合成整数示例；导出里没有步数，无法比对 |

表外（.md/.json 以外）补充两处：

- **测试代码**：`tests/test_domain_consumption.py`（b1aefd9 引入）、`tests/test_client.py` 和 `tests/test_cli.py`（9ea113f 引入）把真实的基础代谢值和某一天的活动卡路里当测试数据用，当前版本仍在。
- **提交信息**：f1851a9 的正文有三天的活动卡路里读数。

## 4. 提交元数据

| 身份类型 | 提交数 |
|---|---|
| 项目别名（作者与提交者相同；既不是个人邮箱，也不是 GitHub noreply） | 36 |

全部 36 个提交只有这一个身份，作者与提交者完全一致。另有 34 个提交带 `Co-Authored-By` 尾注（Anthropic 公共 noreply 地址，见 §2）。`LICENSE` 和 cee3dad 的提交信息里写的是仓库所有者的 GitHub 用户名，与仓库地址一致，按设计；`THIRD_PARTY_NOTICES` 里是上游作者公开的版权行，按设计。

## 5. 待你决定

本里程碑不做任何改写。以下内容所在的提交都已推送到公开远端；改写历史并强推会改变所有提交 SHA，而且挡不住已经存在的克隆、fork 和缓存。

1. **核对文档里的健康数字**（3 个 `verification/*.md`，按设计）→ 可选：保留（核对记录本来就靠这些数字）/ 后续提交里删成"一致/不一致"/ 改写历史并强推。
2. **核对文档以外的健康数字**（路线图 M3/M5、睡眠计划、consumption 计划、notes/04、notes/05）→ 可选：保留 / 后续提交里删改为不含数值的描述（只影响以后的版本，历史里仍在）/ 改写历史并强推。
3. **测试代码里的真实读数**（`tests/test_domain_consumption.py`、`tests/test_client.py`、`tests/test_cli.py`）→ 可选：保留 / 后续提交里换成合成值（测试不依赖具体数值，改起来机械）/ 改写历史并强推。
4. **提交信息里的读数**（f1851a9）→ 可选：保留 / 改写历史并强推（提交信息没法用后续提交修改）。
5. **账号属性**（地区 cn、东八区、手机型号）→ 信息级，可选：保留 / 后续提交里删去手机型号 / 改写历史并强推。

**2026-10-15 的决定**：

- 1–3：后续提交删改（核对文档写一致 / 不一致、日期相对核对日，其余文档改为不含数值的描述，测试换合成值），不改写历史。
- 4：保留。
- 5：删手机型号，保留地区和时区。
- 另：notes/10 §4 等处不再指明本账号的运动类型（映射表仍在代码里）。

这些删改只影响以后的版本：历史里的旧版本原样保留，没有改写历史，也没有强推。

## 附录：扫描模式

扫描脚本放在仓库外，没有提交。输入为 `git log --all -m -p --text --unified=0 --no-color --no-ext-diff --format=commit:%H`（只测新增行，按 `@@ … +c[,d] @@` 计新文件行号）和 `git log --all --format=commit:%H%n%B`（提交信息，按正文行号）；每处命中只输出 `<短 SHA> <路径或 "message">:<行> <模式> len=<长度>`，不输出匹配内容。

文件名模式（Step 2）：`session.json`, `*.session.json`, `.mifit/`, `secrets/`, `private/`, `evidence/`, `target/`, `reference/`, `work/`, `.env`, `*.har`, `*.pcap*`, `*.db`, `*.sqlite*`, `*.apk`, `*.dex`, `*.log`, `*cookie*`, `*token*`

正则（Step 3；Python `re`；表格里的 `\|` 在正则中是普通的 `|`）：

| name | regex | catches |
|---|---|---|
| `cred_assign` | `(?i)\b(service_?token\|pass_?token\|ssecurity\|c_?user_?id\|user_?id\|device_?id\|clientsign\|step1token)\b["']?\s*[:=]\s*["']?([A-Za-z0-9+/=_%.-]{6,})` | a credential name with a value |
| `cookie_value` | `(?i)\b(set-)?cookie\b["']?\s*[:=]\s*["']?[^"'\n]*=[^;"'\s]{6,}` | cookie headers with values |
| `url_secret` | `(?i)[?&](servicetoken\|passtoken\|ssecurity\|clientsign\|_ssign\|sign\|nonce\|auth_key)=` | URLs carrying a secret parameter |
| `ssecurity_shape` | `(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{22}==(?![A-Za-z0-9+/=])` | 16-byte base64 values |
| `long_b64` | `(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{40,}={0,2}` | token-like base64 runs |
| `session_json` | `"(service_token\|ssecurity\|c_user_id)"\s*:\s*"[^"]{6,}"` | a serialized mifit session |
| `email` | `[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}` | email addresses |
| `phone_cn` | `(?<!\d)1[3-9]\d{9}(?!\d)` | CN mobile numbers |
| `mac` | `(?i)\b([0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b` | device MAC addresses |

自查：同一组正则在提交前扫过本文、`README.md`、`THIRD_PARTY_NOTICES` 和改过的 `docs/superpowers/roadmap.md`，零命中（附录里的正则字面量也不匹配自身）；`git diff --check` 无输出。
