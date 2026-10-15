# 设计文档：发布到 PyPI + MCP 服务（接入 AI 生态）

- 日期：2026-10-15
- 状态：草稿。用户选择跳过问答、直接设计（2026-10-15 "直接设计,不问了"），文中凡是"决定"都是我的判断，集中列在 §11，均可推翻。
- 目标读者：接手写实现计划（`superpowers:writing-plans`）和实现的开发者。
- 上游：[总设计](2026-10-13-mifitness-sdk-design.md) §2（反目标）、§3（约束）、§8.1（核对口径）；[路线图](../roadmap.md)；README。
- 外部事实的出处与核实日期见 §12；它们会变，实现前按 §12 复查一遍。

---

## 0. 一页结论

- **两件事一起设计、分三期交付**，因为 MCP 最顺的接入方式（AI 客户端配置里一行 `uvx mifit mcp`）依赖包已经在 PyPI 上：
  - **R（发布）**：把已核对完的 0.2.0 发布到 PyPI，补齐打包元数据、`mifit --version`、标签触发的发布流水线（PyPI Trusted Publishing，仓库里不存令牌）和 GitHub Release。之后安装是 `uv tool install mifit`（或 `pipx` / `pip`），不再 `git clone`。
  - **P（MCP 服务）**：0.3.0 加 `mifit mcp`，一个跑在用户本机、走 stdio 的只读 MCP 服务。每个已核对的数据域一个工具，外加状态、档案和（P2）扫码登录工具。只交出 B 层 `records` 和 `notes`，不交 `raw`；时间换成带时区的本地时间；密集数据自动降采样，单次结果有上限。
  - **E（生态登记）**：登记到官方 MCP Registry；README 给常见客户端的配置；做 Claude Desktop 一键安装包（`.mcpb`，uv 类型，用户不用装 Python）。
- **四条红线**：凭据（会话、passToken、FDS 链接与密钥）永不出现在 MCP 输出里；GPS 轨迹默认根本发不出去（服务端开关 + 工具参数两道闸）；档案只给 B 层（`raw` 里有个人标识）；MCP 层只调用 `MiFit` 的公开方法，不为 MCP 改数据域。
- **用户要做的事**：在 PyPI（和 TestPyPI）上为 `mifit` 添加 pending trusted publisher、在 GitHub 建 `pypi` / `testpypi` 两个 environment（§4.6）；每期结束在自己的 AI 客户端里做一次真机核对（§5.12）。

---

## 1. 意图与为什么

- mifit 已经能把 app 里能看到的主要数据（13 个数据域，均与 app 核对）取出来，但现在只有会 `git clone`、会配 Python 环境的人能用。**发布**是把"能用"变成"别人一条命令就能装上"。
- 用户现在问健康数据的自然方式，是在 AI 助手里直接问"我上周睡得怎么样""最近静息心率是不是在升"。**MCP** 是 AI 客户端（Claude Desktop / Claude Code、Cursor、VS Code 等）接本地工具的通用协议：做一个 MCP 服务，这些客户端就都能读 mifit 的数据，而 mifit 不必绑定任何一家。
- 这与总设计的定位一致：mifit 是"底座"，MCP 服务和 CLI 一样是底座之上的一层薄壳；分析、图表、建议是 AI 的事，不是 mifit 的事。

## 2. 目标 / 非目标 / 反目标

### 目标
1. `uv tool install mifit` / `pipx install mifit` / `pip install mifit` 能装上可用的 `mifit` 命令和 `mifit` 库，Windows / macOS / Linux 都行。
2. 每个版本有 git 标签、PyPI 版本、GitHub Release（附 wheel / sdist 和该版本的 CHANGELOG 段落），三者版本号一致，由流水线保证。
3. 发布不需要在仓库或 GitHub Secrets 里存任何 PyPI 令牌（Trusted Publishing / OIDC）。
4. `mifit mcp` 启动一个本机 stdio MCP 服务，AI 客户端用一行配置接入；工具覆盖所有已核对的 B 层数据域和档案。
5. 在 AI 客户端里，没开过终端的用户也能完成扫码登录（P2）。
6. MCP 结果对 AI 友好：本地时间而不是 Unix 秒；单次结果有大小上限；超出时自动降采样并明说。
7. 在官方 MCP Registry 上能搜到 mifit（E）。

### 非目标（本期不做，以后可以做）
- **远程 / 托管的 MCP 服务**（Streamable HTTP、多用户）：那要把用户的小米会话放到别人的服务器上，与"凭据不出本机"冲突。只做本机 stdio。
- **独立可执行文件**（PyInstaller / Nuitka）、Homebrew、winget、conda-forge：`uv tool install` 会自动下载所需的 Python，"要先装 Python"已不是门槛；独立可执行文件在 Windows 上常被杀毒软件误报，维护成本高。以后有明确需求再做。
- **MCP 里的通用 `raw` 读取**：A 层字段含义未核对，交给 AI 容易被当成事实复述。CLI / 库里照旧有 `mifit raw`。以后可加一个默认关闭的工具。
- **MCP resources / prompts**：工具描述已经说清字段含义；"周报"之类的提示词是 AI 客户端或用户的事。
- MCP 结构化输出（`outputSchema` / `structuredContent`）：见 §5.4 与 D9。
- Agent Skill / Claude Code 插件形式的包装：见 §6.4。

### 反目标（出现即算失败）
- **凭据外泄**：会话 token、passToken、ssecurity、FDS 预签名链接与解密密钥、`sid`、登录长轮询链接、安全确认链接，出现在 MCP 输出、错误消息、日志或标准输出里。
- **GPS 轨迹在用户没开闸时被发出去**：轨迹能定位到住处，而 MCP 结果会被送到 AI 服务商。
- **结果撑爆上下文**：一次工具调用返回几万行逐秒数据。
- **B 层悄悄失真**：MCP 层只做格式转换（时间格式、字段挑选）和明说的降采样；不改名、不重算服务端已给的值、不引入未核对的字段。
- **为 MCP 改坏底座**：数据域、`MiFit` 的公开行为不因 MCP 改变；MCP 是只用公开 API 的一层。
- **把账号拖进风控**：MCP 不能绕开现有的串行 + 节流；AI 反复问同一范围不能变成反复请求（§5.7 缓存）。
- **发布物里夹带不该公开的东西**：真实健康数据、`evidence/` `work/` `reference/` `target/` 里的东西、会话文件。

## 3. 约束

- Python ≥ 3.12；现有依赖 `requests`、`qrcode`、`cryptography`。
- 只读；`Transport` 的节流（每请求约 1 s + 抖动）不变；串行。
- 只在一个中国区账号上核对过；B 层"只放核对过的字段"的规则不变；公开仓库里不放真实健康数字（路线图 / 记忆里的用户决定）。
- 开发机是 Windows，CI 是 Linux；仓库文件行尾 LF。
- 测试全部离线（`tests/conftest.py` 拦截网络）。
- 推送到 GitHub、打标签、发布到 PyPI 都是对外动作：每次先问用户。
- PyPI / TestPyPI 账号和 Trusted Publisher 配置只能由用户本人在网页上做（§4.6）。

---

## 4. R：发布到 PyPI

### 4.1 包名

- 分发名、导入名、命令名都是 `mifit`。2026-10-15 查 PyPI：`mifit` 无此项目（§12 F1）。
- 占名有时间窗：pending publisher **不保留名字**，别人先注册了它就作废。所以 R 期尽早发出第一个版本（§4.6）。
- 不要与已有的 `mi-fitness`（另一个人的"小米运动健康亲友数据 SDK"）混淆；PyPI 的相似名检查把 `mifitness` 视为与它冲突，但 `mifit` 不冲突（§12 F1）。
- 退路（万一 `mifit` 注册不了）：分发名改 `mifit-cloud`，导入名和命令名仍是 `mifit`（D1）。

### 4.2 版本号

- 首个发布版本就是 **0.2.0**：它从未打过标签、从未发布（CHANGELOG 0.2.0 段已这么写）。R 期只加打包和 `--version`，不改数据行为；CHANGELOG 0.2.0 段补一个 "Packaging" 小节，日期改成实际发布日。
- 版本号的唯一来源是 `pyproject.toml` 的静态 `version`（D3）。`mifit.__version__` 用 `importlib.metadata.version("mifit")` 取，取不到（源码树里没安装就导入）时为 `"0+unknown"`。
- `mifit --version` 打印 `mifit <version>`。
- 流水线在标签构建时检查：标签 `vX.Y.Z` == `pyproject` 的 `version` == CHANGELOG 里有 `## X.Y.Z` 段；不一致就失败，不发布。

### 4.3 `pyproject.toml` 元数据

补上（具体值实现时定，下面是要点）：

- `readme = "README.md"`（PyPI 项目页就是 README）。
- `keywords`、`classifiers`：Development Status :: 3 - Alpha；Environment :: Console；Operating System :: OS Independent；Programming Language :: Python :: 3 :: Only、3.12、3.13、3.14；Topic 选 Utilities 一类；Typing :: Typed。**不加** License 分类器（已用 PEP 639 的 `license = "MIT"` 表达式，二者不能并用）。
- `[project.urls]`：Homepage / Source（GitHub）、Issues、Changelog（指向 GitHub 上的 CHANGELOG.md）。
- 不写 `authors`（D4）：PyPI 页面会显示上传账号；用户想署名再加。
- 加 `src/mifit/py.typed`（库本来就有完整类型标注），并确保它进 wheel。

### 4.4 README 改动

- **安装**一节改成：
  ```bash
  uv tool install mifit     # 推荐：独立环境，命令放进 PATH；需要时自动下载 Python
  pipx install mifit        # 同上，用 pipx
  pip install mifit         # 装进当前环境，当库用
  ```
  以及升级（`uv tool upgrade mifit`）。原来的 `git clone` + `pip install -e .` 移到 "Development"。
- 顶部加 PyPI 版本徽章。
- **相对链接改成绝对 GitHub 链接**（`CHANGELOG.md`、`LICENSE`、`THIRD_PARTY_NOTICES` 等）：PyPI 渲染 README 时相对链接会断。正文里以代码格式出现的路径（如 `notes/domain-catalog.md`）也改成链接，否则 PyPI 读者找不到。
- P 期再加 "Use with an AI assistant (MCP)" 一节（§6.1）和隐私说明（§7）。

### 4.5 发布流水线 `.github/workflows/release.yml`

触发：推送 `v*` 标签 → 发 PyPI；手动 `workflow_dispatch` → 只发 TestPyPI（演练用）。顶层 `permissions: contents: read`，各 job 只拿自己要的权限。

| job | 做什么 | 权限 |
|---|---|---|
| `test` | 与 CI 相同：装 `.[test]`，跑离线测试（Python 3.12 / 3.14） | read |
| `build` | 检查版本一致（§4.2，只在标签触发时）；`python -m build` 出 sdist + wheel；`twine check --strict`；列出 sdist / wheel 内容并断言不含 `evidence/`、`work/`、`reference/`、`target/`、`.mifit/` 下的文件、`*.partial`、`session.json`（不能按"文件名含 session"判断：`tests/test_session_store.py` 是正常文件）；上传为 artifact | read |
| `smoke` | 矩阵 ubuntu / windows / macos × Python 3.12：在全新 venv 里装刚构建的 wheel，跑 `mifit --version`、`mifit --help`、`python -c "import mifit"`；P 期起再加 MCP 握手冒烟（§5.11） | read |
| `publish` | `needs: [test, build, smoke]`；`environment: pypi`（手动触发时 `testpypi`，并设 `repository-url: https://test.pypi.org/legacy/`）；`permissions: id-token: write`；用 `pypa/gh-action-pypi-publish@release/v1`。PEP 740 证明在 Trusted Publishing 下默认生成，不关（§12 F6） | id-token |
| `github-release` | 只在标签触发时；`needs: publish`；用 `gh release create` 建 Release，标题 `vX.Y.Z`，正文是 CHANGELOG 里该版本的段落（一个小脚本截取），附 `dist/*` | contents: write |

- 只从 CI 的干净检出构建，不从开发机构建上传：开发机上有 git-ignored 的 `evidence/`、`work/`、`reference/`、`target/`。
- checkout 用 `persist-credentials: false`；构建与发布分成两个 job，发布 job 只拿 artifact，不跑任何项目代码（PyPA 指南的做法，§12 F6）。action 的主版本与 `ci.yml` 保持一致。
- PyPI 自 2026-07 起，一个版本发布 14 天后不再接受新文件（§12 F6）：一个版本的 wheel / sdist 要一次传齐，漏了就发下一个补丁版本。
- 现有 `ci.yml` 不变（它照常在 push 上跑）。

### 4.6 首次发布的步骤（谁做什么）

| # | 谁 | 做什么 |
|---|---|---|
| 1 | 我 | 实现 R 期改动，本机 `python -m build` + `twine check` + 在临时 venv 里装 wheel 冒烟；提交到 main（推送前问你） |
| 2 | 你 | PyPI 和 TestPyPI 各要一个开了 2FA 的账号（已有就跳过） |
| 3 | 你 | 在 PyPI → Publishing 添加 **pending publisher**：项目 `mifit`，owner `Jenwein`，仓库 `mifit`，workflow `release.yml`，environment `pypi`；TestPyPI 上同样加一个，environment `testpypi` |
| 4 | 你 | GitHub 仓库 Settings → Environments 建 `pypi` 和 `testpypi`；建议给 `pypi` 设"需要审批"（审批人是你），这样每次正式发布都要你点一下 |
| 5 | 我（你同意后） | 手动触发 release 流水线发到 TestPyPI；在干净环境里从 TestPyPI 装一次验证 |
| 6 | 我（你同意后） | 打 `v0.2.0` 标签并推送 → 流水线发 PyPI（你在 `pypi` environment 审批）→ 自动建 GitHub Release |
| 7 | 我 | 从 PyPI `uv tool install mifit` 装一次，`mifit --version`、`mifit profile`（用你已有的会话）验证 |

### 4.7 验收

- PyPI 上 `mifit 0.2.0` 页面正常渲染 README，链接可点。
- 三个系统的 smoke job 全绿。
- `uv tool install mifit` 后 `mifit export steps ...` 与源码安装的结果一致（用户本机一次）。
- GitHub Release `v0.2.0` 存在，附件齐全，正文是 0.2.0 的 CHANGELOG。

---

## 5. P：MCP 服务

### 5.1 形态

- 命令：`mifit mcp [--allow-gps]`，走 stdio，跑在用户本机，用 `~/.mifit/session.json`（与 CLI 同一份会话）。
- 依赖：官方 MCP Python SDK，**硬依赖** `mcp>=2.3,<3`（D5）。2.x 是现行主线（2.3.0，2026-10-02），实现的是 MCP 规范 2026-07-28 版，也兼容 2025 年的客户端（§12 F2）。
- 写法用 v2 的 API：`from mcp.server import MCPServer`，`@server.tool(...)`，`server.run()`（默认 stdio）；工具注解用 `mcp.types.ToolAnnotations(read_only_hint=..., ...)`（v2 是下划线命名）。v1 的 `mcp.server.fastmcp` 在 v2 里已不存在，不要照网上的旧例子写。
- `mcp` 只在 `mifit mcp` 里才导入（惰性导入），`import mifit` 和其它子命令不付这个导入成本。
- stdio 的标准输出只能是 JSON-RPC：服务运行期间任何人类可读的输出一律走标准错误（库里本来就不往 stdout 打印；实现时加测试守住，§5.11）。
- 服务名 `mifit`，版本号 = 包版本。

### 5.2 工具清单

全部工具都标 `readOnlyHint: true`、`openWorldHint: true`（会访问小米云）、`idempotentHint: true`；登录工具除外（见下）。工具名不带 `mifit_` 前缀（客户端通常按服务名加命名空间）。

| 工具 | 参数 | 读取（`MiFit` 方法） | 单次上限 |
|---|---|---|---|
| `get_status` | — | 不联网：会话是否存在、年龄、能否续期（`describe_session` 那一套，只给年龄）；今天的本地日期、时区；版本；GPS 闸是否打开 | — |
| `get_steps` | `start_date`, `end_date` | `steps` | 92 天 |
| `get_calories` | 同上 | `consumption` | 92 天 |
| `get_activity` | 同上 | `activity_daily` | 92 天 |
| `get_heart_rate_daily` | 同上 | `heart_rate_daily` | 92 天 |
| `get_sleep` | 同上，`include_stages=false` | `sleep` | 92 天；`include_stages=true` 时 7 天 |
| `get_spo2_daily` | 同上 | `spo2_daily` | 92 天 |
| `get_stress_daily` | 同上 | `stress_daily` | 92 天 |
| `get_weight` | 同上 | `weight` | 92 天 |
| `get_workouts` | 同上 | `workouts` | 92 天 |
| `get_heart_rate` | 同上，`interval_min`（可选） | `heart_rate` | 7 天，输出 ≤ 500 行（§5.4） |
| `get_spo2` | 同上，`interval_min`（可选） | `spo2` | 7 天，输出 ≤ 500 行 |
| `get_workout_detail` | `workout_start`（`get_workouts` 给出的开始时间），`interval_s`（可选），`include_track=false` | `workout_detail(t, t, track=...)` | 一次运动；样本 ≤ 500 行，轨迹 ≤ 500 点 |
| `get_profile` | — | `profile`，只给 `records` | — |
| `login_start`（P2） | — | 见 §5.6 | — |
| `login_status`（P2） | — | 见 §5.6 | — |

- 每个工具的描述写清：读的是什么、每个字段的含义和单位（照搬 B 层模型 docstring 的口径）、范围上限、已知局限（例如 `get_calories` 最近一天可能低于 app 的卡路里环、`get_workout_detail` 只支持户外跑 / 骑）。描述是英文（工具描述给模型看；与 README 同语言）。
- 工具表在代码里是一张数据表（名字、方法、上限、描述、展示函数），数据域工具由它生成，不是十几个手写函数（D7）。
- 登录工具：`readOnlyHint: false`（会写本地会话文件）、`destructiveHint: false`、`openWorldHint: true`。

### 5.3 输入约定

- `start_date` / `end_date`：`YYYY-MM-DD`，本地日历日，首尾都含，与 CLI 相同。`end_date` 省略时等于 `start_date`。只接受这个格式；"昨天""上周"由 AI 换算——`get_status` 给出今天的本地日期和时区，服务说明（§5.9）让 AI 先调它。
- 超过上限：直接报错并说上限是多少、请拆成几次调用；不悄悄截断范围（D8）。
- 92 天以内照样按 `mifit.chunks` 的块大小分块读（`read_chunks` + `merge_chunks`），所以一次调用最多约 3 个块（`activity_daily` 每块 3 个请求，最多约 9 个请求）。
- `workout_start`：`get_workouts` 结果里的 `start` 字符串（ISO 8601 带偏移），或 Unix 秒整数。解析成秒后调用 `workout_detail(t, t, ...)`；该时刻没有运动时返回空结果和一条 note。

### 5.4 输出约定

每次调用返回一个 TextContent，内容是紧凑 JSON（D9）。v2 SDK 默认把返回类型注解变成输出 schema（结构化输出），所以工具一律声明 `structured_output=False`，返回字符串：

```json
{"tool": "get_sleep", "from": "2026-10-01", "to": "2026-10-07",
 "timezone": "UTC+08:00", "records": [...], "notes": [...]}
```

- **只给 `records` 和 `notes`**，不给 `raw`。`records` 的字段名与 B 层模型完全相同（文档、CHANGELOG 对 MCP 同样适用），唯一的变换是时间：
  - 日记录（有 `date` 的）：去掉 `epoch_s`，留 `date`。
  - 时刻字段：`epoch_s` → `time`，`start_epoch_s` → `start`，`end_epoch_s` → `end`，值是带偏移的 ISO 8601 本地时间（`2026-10-07T23:12:00+08:00`）。有自己 `utc_offset_s` 的记录（运动、睡眠会话）用它自己的偏移；其余用本机时区（与 CLI "本地日历日"同一口径）。
  - NaN / Infinity 换成 `null` 并记 note（复用 CLI 的 `_finite` / `_json_ready`，挪到共享模块，D10）。
- **睡眠**：默认每天的汇总 + 会话起止和各阶段分钟数，不带分段时间线；`include_stages=true` 才带。
- **密集序列**（`get_heart_rate`、`get_spo2`、运动样本、轨迹）用列式，省 token：
  ```json
  {"resolution": "5 min buckets", "columns": ["start", "min_bpm", "avg_bpm", "max_bpm", "count"],
   "rows": [["2026-10-07T00:00:00+08:00", 52, 55.4, 61, 5], ...]}
  ```
- **降采样**（结果 ≤ 500 行，D11）：
  - 心率 / 血氧：不传 `interval_min` 时，原始点数 ≤ 500 就给原始点（列：`time`、`bpm` / `time`、`spo2_pct`；`HeartRatePoint.kind` 是未解释的来源整数，不交给 AI）；否则从 5、15、30、60、180 分钟里取能放进 500 行的最小间隔，按本地时间对齐分桶，每桶给最小 / 平均（一位小数）/ 最大 / 点数。传了 `interval_min` 就照它分桶（0 表示原始点），放不下时报错请缩小范围。
  - 运动样本：逐秒样本按 1、5、10、15、30、60、120 秒里能放进 500 行的最小间隔**抽点**（取每个间隔开头那一秒的样本，不平均：累计距离 / 步数平均没有意义，D12）。传 `interval_s` 就照它抽。
  - 轨迹：每隔 N 个点取一个，N 取能放进 500 点的最小整数。
  - 每次降采样都在 `resolution` 里写明，并加一条 note（"heart rate shown as 15 min buckets; ask for a shorter range or interval_min=0 for raw samples"）。
- 输出不缩进；不重复 B 层模型的说明（在工具描述里）。

### 5.5 隐私闸门

- **GPS 轨迹两道闸**：`mifit mcp` 默认不带 `--allow-gps`，此时 `get_workout_detail` 一律按 `track=False` 读（连 GPS 文件都不下载），`include_track=true` 返回错误"GPS is disabled on this server; the user can start it with --allow-gps"。带了 `--allow-gps` 还要工具参数 `include_track=true` 才给（D13）。
- **档案**：只给 `UserProfile`（身高、体重、目标），不给 `raw`。
- **凭据**：MCP 层根本不碰会话字段；错误消息只用 SDK 已经脱敏的消息，网络错误只报异常类型（与 CLI 同一做法）。安全确认链接（`NotificationRequiredError.notification_url`）不进结果：由服务在用户本机用默认浏览器打开，结果里只说"已在浏览器打开确认页"（§5.6）。
- **结果会被送到 AI 服务商**：README 和 `get_status` 都写明（§7）。

### 5.6 会话与登录

- 客户端懒创建：第一次需要读数据时才 `MiFit()`；之后复用同一个（store-backed，续期会写回 `~/.mifit`）。遇到 `AuthError` 时重新从磁盘加载一次再试（用户可能刚在终端里 `mifit login` 过），仍失败才报错。
- 没有会话时，数据工具报错："Not logged in to Mi Fitness. Run `mifit login` in a terminal, or call login_start."
- **P2 登录工具**（让只用 Claude Desktop 之类、不开终端的人也能登录）：
  - `login_start`：向小米要一个扫码登录会话（`request_qr_login`），返回：一张二维码 PNG（v2 的 `mcp.server.mcpserver.Image(data=..., format="png")`，编码 `login_url`，与 CLI 一致）+ 文字（登录链接、"用小米 / 小米运动健康 app 扫码并确认"）；同时把 PNG 写到 `~/.mifit/login-qr.png` 并用系统默认程序打开，作为客户端不显示图片时的退路（D14：Claude Code 和 Cursor 会显示工具结果里的图片，Claude Desktop 未能核实，§12 F3）。然后在后台线程里长轮询（`poll_qr_login` → `exchange_sts` → `save_session` → `record_event("login")`），工具调用本身立即返回。已有进行中的登录时返回同一个二维码，不新开。
  - `login_status`：`pending` / `succeeded`（并把服务里的客户端换成新会话）/ `failed`（脱敏消息）/ `confirmation_required`（已在浏览器打开确认页，确认后再 `login_start`）/ `expired`（长轮询超时）。登录结束时删掉 `login-qr.png`。
  - 二维码 PNG 用 `qrcode` 自带的 `png` extra（`pypng`，纯 Python）：依赖从 `qrcode>=7.4` 改成 `qrcode[png]>=7.4`。
  - 长轮询链接（`lp_url`）只在内存里，永不进输出。
  - 不用 MCP 的 URL 模式 elicitation 来做登录：Python SDK 文档说它在 2026-07-28 版连接上不可用，Claude Desktop 是否支持也未能核实（§12 F3）。以后客户端普遍支持了再考虑。

### 5.7 并发、节流、缓存、超时

- **串行**：所有读取共用一把锁，同一时刻只有一个请求在飞（保持"串行 + 节流"）。
- **不阻塞事件循环**：读取在工作线程里跑，这样读取期间服务还能响应取消和 ping。工具写成 `async`，里面显式 `anyio.to_thread.run_sync(...)`；不依赖 SDK 对同步工具的默认处理（未核实，§12 F2）。
- **缓存**：进程内、按（工具、规范化后的参数）缓存成功结果 5 分钟，最多 64 条，LRU 淘汰（D15）。不落盘（落盘就是又一份健康数据）。`get_status`、登录工具不缓存。
- **进度**：分块读取时，若客户端给了 `progressToken`，每读完一块发一次进度通知；没给就不发。
- **超时**：不设服务端超时，靠范围上限控制时长（92 天的日数据约 3–9 个请求，约 5–15 s）。

### 5.8 错误

工具出错时返回 MCP 工具错误（`isError: true`），一句话说清原因和下一步：

| 情况 | 消息要点 |
|---|---|
| 没有会话 / 会话不可读 | 未登录；在终端 `mifit login` 或调 `login_start` |
| `RenewalError` | 会话已过期且续期失败；重新登录（同上） |
| `ApiError` | 原消息（已脱敏） |
| 网络错误 | "Network request failed (`<异常类型>`)"，不带 URL |
| 参数不合法 / 超上限 | 哪个参数、允许的格式或上限、建议怎么拆 |
| GPS 闸关闭时要轨迹 | §5.5 |

不把 Python traceback 交给客户端。

### 5.9 服务说明（`instructions`）

握手时给客户端的简短说明（英文，几行），要点：这是用户本人的小米运动健康数据，只读；先调 `get_status` 拿今天的日期和登录状态；日期是本地日历日；先用日汇总工具，需要细节再用 `get_heart_rate` / `get_workout_detail`；各工具的上限；字段都与 app 核对过，`notes` 里的提醒要转告用户；不是医疗建议。

### 5.10 模块布局

```
src/mifit/
  jsonsafe.py          # 从 cli 挪出的 _plain / _finite / _json_ready（CLI 改为引用它）
  mcp_server/          # 不叫 mifit/mcp：避免与 SDK 的顶层包 mcp 同名引起混淆（D6）
    __init__.py        # run(allow_gps: bool) —— `mifit mcp` 调它；惰性导入 mcp SDK
    server.py          # build_server(client_factory, *, allow_gps, clock, ...)：组装工具，测试可注入假客户端
    tools.py           # 工具表：名字、MiFit 方法、上限、描述、展示函数、注解
    present.py         # B 层记录 → MCP 输出（时间换算、字段挑选、列式）
    resample.py        # 分桶与抽点
    cache.py           # TTL + LRU 缓存
    login.py           # P2：后台扫码登录的状态机
  cli/__init__.py      # 加 `mcp` 子命令与 `--version`
```

`mcp_server` 只依赖 `mifit` 的公开 API（`MiFit`、模型、`chunks`、`auth.qr` 的登录函数、`auth.lifetime.describe_session`），不改任何数据域。

### 5.11 测试（全部离线）

- `present` / `resample` / `cache` 的纯函数单测（时区、DST 日、空结果、NaN、边界行数 500 / 501）。
- 服务级测试：用 SDK 的内存连接（客户端 ↔ 服务端，不起子进程；v2 的具体接口实现时查 SDK 文档的测试一节，没有就退回子进程 + stdio）接一个假 `MiFit`：
  - `tools/list`：工具名、参数 schema、注解（只读工具都是 `readOnlyHint: true`）。
  - 每个工具一次正常调用，检查输出形状与字段名 = B 层字段名（除时间字段外）。
  - 上限、参数错误、未登录、`RenewalError`、网络错误的消息；GPS 闸（不带 `--allow-gps` 时 `workout_detail` 被以 `track=False` 调用）。
  - **凭据不出现**：假会话里放上可识别的假 token / passToken / ssecurity，扫描所有工具输出与错误消息，一个都不能出现。
  - 缓存：同参数第二次调用不再调用假客户端；5 分钟后会。
- **stdout 干净**：子进程跑 `python -m mifit.cli mcp`（包根没有 `__main__.py`，入口在 `mifit/cli/__main__.py`），往 stdin 写 `initialize` + `tools/list`，断言 stdout 每一行都是合法 JSON-RPC（这一步不联网：`tools/list` 不读数据）。发布流水线的 smoke job 也跑这一步（§4.5）。
- P2：登录状态机用假的 `request_qr_login` / `poll_qr_login` / `exchange_sts` 测：并发 `login_start` 只开一个；成功后客户端切到新会话；`NotificationRequiredError` 走浏览器且 URL 不进输出；PNG 文件在结束时删除。

### 5.12 真机核对（与用户）

- 一致性（我来做，自动）：同一范围，`mifit export <domain>` 的 `records` 与 MCP 工具输出逐字段相等（时间字段换算后相等）。这比再去 app 读数更强：数据域本身已与 app 核对过。
- 用户在自己的 AI 客户端里（Claude Code 或 Claude Desktop）：
  1. 按 README 一行配置接入，`get_status` 正常。
  2. 问 3–5 个自然问题（例如"我上周每天睡了多久""昨天心率最高是几点""最近一次跑步的配速变化"），看 AI 调的工具和回答是否合理。
  3. P2：删掉会话（或换台机器）后在客户端里完成扫码登录。
  4. 不带 `--allow-gps` 时要求看轨迹，确认被拒。

---

## 6. E：生态登记

### 6.1 客户端配置（README）

通用写法：命令 `uvx`，参数 `["mifit", "mcp"]`；已经 `uv tool install mifit` 过的，命令直接用 `mifit`，参数 `["mcp"]`。各客户端（写法见 §12 F7）：

```bash
claude mcp add --scope user mifit -- uvx mifit mcp
```

```jsonc
// Claude Desktop：%APPDATA%\Claude\claude_desktop_config.json（macOS：~/Library/Application Support/Claude/）
{"mcpServers": {"mifit": {"command": "uvx", "args": ["mifit", "mcp"]}}}
// Cursor：~/.cursor/mcp.json
{"mcpServers": {"mifit": {"type": "stdio", "command": "uvx", "args": ["mifit", "mcp"]}}}
// VS Code：.vscode/mcp.json
{"servers": {"mifit": {"command": "uvx", "args": ["mifit", "mcp"]}}}
```

- 先在终端 `uvx mifit login` 扫码一次（P2 之后也可以在客户端里用 `login_start`）。
- 想开 GPS：参数末尾加 `--allow-gps`，README 说明后果（§7）。

### 6.2 官方 MCP Registry

注册表仍是预览版（§12 F4），格式可能再变；做 E 时先复查。

- 仓库根目录放 `server.json`（`$schema` 用当时注册表的现行版本，2026-10-15 是 `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`）：
  ```json
  {"$schema": "…", "name": "io.github.jenwein/mifit", "description": "…", "version": "0.3.0",
   "packages": [{"registryType": "pypi", "identifier": "mifit", "version": "0.3.0",
                 "runtimeHint": "uvx", "transport": {"type": "stdio"},
                 "packageArguments": [{"type": "positional", "value": "mcp"}]}]}
  ```
  命名空间 `io.github.<GitHub 用户名>/…` 是用 GitHub 身份发布的硬性要求。`packageArguments` 的确切字段写法以当时的 schema 为准。
- README 里放归属标记 `<!-- mcp-name: io.github.jenwein/mifit -->`：注册表发布时会去 PyPI 读**该版本**的 README 校验它，所以标记要随 P 期的 0.3.0 一起进 README，E 期登记的版本才能通过校验。
- 发布流水线在 PyPI 发布成功后加一个 job：`permissions: id-token: write`，`mcp-publisher login github-oidc` 再 `mcp-publisher publish`。`server.json` 的两个版本号与 `pyproject` 一致（加入 §4.2 的一致性检查）。
- 这一条也是 D5 的理由：注册表的 `identifier` 只能是裸包名（它去查 `pypi.org/pypi/<identifier>/<version>/json`），写不了 `mifit[mcp]` 这样的 extra。

### 6.3 Claude Desktop 一键安装包（`.mcpb`）

做。MCPB manifest 0.4 有 `server.type: "uv"`：包里只放 `manifest.json` 和一个声明依赖 `mifit==<版本>` 的 `pyproject.toml`，由 Claude Desktop 用 uv 装依赖，带原生扩展的依赖（`cryptography`、`pydantic-core`）也行，用户不必装 Python（§12 F5）。另一种 `type: "python"` 要把依赖整包打进去，原生扩展无法跨平台，不用。

- 用 `@anthropic-ai/mcpb` 的 `mcpb pack` 打包；放在 `packaging/mcpb/`，发布流水线构建 `mifit-<版本>.mcpb` 附在 GitHub Release 上。用户下载后双击，Claude Desktop（Windows / macOS）弹安装框。
- 入口参数是 `mcp`，默认不开 GPS。`user_config` 的类型有 string、number、directory、file（§12 F5 未列出 boolean）：实现时若 schema 支持 boolean，就加一个 "Allow GPS tracks" 开关映射到 `--allow-gps`；不支持就不提供，要 GPS 的人用 §6.1 的 JSON 配置。
- manifest 用 0.4（`latest` 的 schema 还指着 0.3，要显式写版本）。不签名：Claude Desktop 有拒收已签名包的未解决问题（§12 F5）。
- 风险：MCPB 的 uv 类型较新（Claude Desktop 2026-07-23 才修好相关问题）。E 期真机核对时在用户的 Claude Desktop 上装一次；装不上就先只给 JSON 配置，`.mcpb` 推迟。

### 6.4 以后可做（不在本期）

- Agent Skill（`SKILL.md`）：教不接 MCP 的代理怎么用 `mifit` 命令行。
- Claude Code 插件：把 MCP 配置和一个 skill 打包。
- 默认关闭的 `raw` 工具；结构化输出；更多客户端的配置样例。

---

## 7. 安全与隐私（汇总）

- 凭据只在用户本机：`~/.mifit/session.json`。MCP 服务是本机子进程，不监听网络端口。
- 经 MCP 交出去的只有用户点名要的 B 层数据，而且会被 AI 客户端送到它的模型服务商。README 的 MCP 一节开头写明这一点，并说明 GPS 默认关闭、怎么打开、打开意味着什么。
- 发布：Trusted Publishing，无长期令牌；`pypi` environment 要用户审批；只从 CI 干净检出构建；构建产物内容有断言（§4.5）。
- PyPI 上的包带 PEP 740 证明（attestation）：Trusted Publishing 下发布 action 默认生成（§12 F6），用户可以核对每个文件确实出自本仓库的这条流水线。
- 新增的依赖链（`mcp` 及其约 14 个直接依赖）扩大了供应链面：版本上限钉在主版本（`mcp<3`），不追最新主版本；依赖升级走正常提交和 CI。

## 8. 版本、CHANGELOG、文档

| 期 | 版本 | CHANGELOG |
|---|---|---|
| R | 0.2.0（首个发布） | 0.2.0 段加 "Packaging"：PyPI、`--version`、`py.typed` |
| P（含 P2 登录） | 0.3.0 | Added：`mifit mcp`、各工具；`--allow-gps`；依赖变化 |
| E | 0.3.x | `server.json`、注册表、`.mcpb`（装不上则推迟，D16） |

- 路线图加 R / P / E 三行；README 按 §4.4、§6.1、§7 改。
- 每期一个实现计划（`docs/superpowers/plans/`），P 可按 P1（读取工具）/ P2（登录工具）拆两个。

## 9. 规模与下一步

- R：小（约 4 个任务）。P1：中。P2：小到中。E：小。
- 下一步：用户批准本设计 → 写 R 的实现计划 → 实现 → 用户完成 §4.6 第 2–4 步 → TestPyPI 演练 → 发布 0.2.0 → P1 计划。

## 10. 需要用户做的事（汇总）

1. PyPI / TestPyPI 账号（2FA）与 pending publisher（§4.6 第 2–3 步）。
2. GitHub environments `pypi` / `testpypi`（§4.6 第 4 步）。
3. 同意每一次推送、打标签、发布。
4. P / E 期末在自己的 AI 客户端里做 §5.12 的核对。

## 11. 决定记录（我的判断，均可推翻）

- **D1 包名 `mifit`**：PyPI 上空着，与仓库名、导入名、命令名一致。注册不了时分发名退到 `mifit-cloud`，导入名和命令名不变。
- **D2 发布顺序**：先 R 后 P。R 不依赖 P，能早一步让人装上，也先把流水线跑通；P 的配置写法依赖 PyPI 上已有包。
- **D3 版本号静态写在 pyproject**，不用 setuptools-scm 从标签推：少一层魔法；一致性由流水线检查。
- **D4 不写 `authors`**：git 提交用的是 `mifit-dev`，不替用户决定公开署名。
- **D5 `mcp` 作为硬依赖**，不做成 `mifit[mcp]` extra：注册表的包标识写不了 extra（§6.2），客户端配置也从 `uvx --from "mifit[mcp]" mifit mcp` 简化为 `uvx mifit mcp`。代价：只当库用的人也会装上 `mcp` 和它的约 14 个依赖（pydantic、starlette、uvicorn 等）；导入成本靠惰性导入避免。用户把 AI 接入列为第一优先，接受这个代价。反过来的做法（extra + 单独发一个 `mifit-mcp` 薄包）要维护两个发布物，不划算。
- **D6 子包叫 `mifit.mcp_server`**，不叫 `mifit.mcp`。
- **D7 数据域工具由一张表生成**，每个域一个工具（而不是一个带 `domain` 枚举的通用工具）：模型按工具名和描述选工具更准；表驱动保证一致、好维护。
- **D8 超上限直接报错**，不悄悄截断：截断会让 AI 以为拿到了整个范围。
- **D9 输出用 TextContent 里的紧凑 JSON，不声明 `outputSchema`**（v2 默认开结构化输出，所以显式 `structured_output=False`）：结构化输出会让同一份数据以两种形式返回，有的客户端两份都放进上下文；B 层字段已在描述里说明。
- **D10 JSON 清洗函数挪到 `mifit/jsonsafe.py`**，CLI 与 MCP 共用。
- **D11 单次结果 ≤ 500 行 / 点，日数据 ≤ 92 天，密集数据 ≤ 7 天**：500 行列式约 5–8k token，在常见客户端的工具结果上限之内；92 天与 CLI 的 `--long` 门槛一致。
- **D12 运动样本抽点不平均**：累计量平均没有意义，抽点保持每个值都是真实样本。
- **D13 GPS 两道闸**（服务端 `--allow-gps` + 工具参数）：只有参数一道闸时，AI 一句"给我看看路线"就会把住处发给服务商。
- **D14 登录二维码同时作为图片返回、写文件并用系统程序打开**：不依赖客户端是否显示工具结果里的图片。
- **D15 进程内 5 分钟缓存**：AI 常在一次对话里反复读同一范围；5 分钟足够短，刚同步的数据不至于等太久。
- **D16 `.mcpb` 用 uv 类型**，不把依赖打进包；装不上就推迟，不阻塞 E 的其余部分。

## 12. 外部事实（出处与核实日期）

2026-10-15 由调研子代理从一手来源查得；会变，实现每一期之前复查与该期相关的几条。

- **F1 PyPI 名称**：`mifit`、`mifit-mcp`、`mi-fit`、`mifitness`、`mifit-cli` 的 JSON API 都是 404（`https://pypi.org/pypi/<name>/json`）。`mi-fitness` 已被占（0.2.0，2026-03-21，"小米运动健康亲友数据 SDK"，github.com/MistEO/MiSDK）。PyPI 的相似名检查（Warehouse 的 `ultranormalize_name`：去掉 `.` `_` `-`，`l`/`i`→`1`，`o`→`0`）使 `mifitness` 与 `mi-fitness` 冲突；`mifit` 不冲突。pending publisher 不保留名字（docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/）。
- **F2 MCP Python SDK**：`mcp` 2.3.0（2026-10-02）；2.0.0 于 2026-07-28 发布；1.x 仍维护（1.30.0）。`requires_python >=3.10`；约 14 个直接依赖（anyio、httpx2、jsonschema、mcp-types、opentelemetry-api、pydantic、pyjwt[crypto]、python-multipart、sse-starlette、starlette、typing-extensions、typing-inspection、uvicorn，Windows 上加 pywin32）。v2：`from mcp.server import MCPServer`，`mcp.server.fastmcp` 已移除（现为 `mcp.server.mcpserver`）；`ToolAnnotations(read_only_hint=..., open_world_hint=...)`；返回类型注解默认生成输出 schema，`structured_output=False` 关闭。现行规范版本 2026-07-28。（pypi.org/pypi/mcp/json；py.sdk.modelcontextprotocol.io 的 whats-new、servers/tools、servers/structured-output；modelcontextprotocol.io/specification/versioning）未核实：同步工具函数是否自动在线程池里跑；内存测试连接的 v2 接口。
- **F3 图片与 elicitation**：v2 用 `mcp.server.mcpserver.Image(data=..., format="png")` 返回图片（py.sdk…/servers/media/）。Claude Code 显示工具结果里的 PNG/JPEG/GIF/WebP（code.claude.com/docs/en/mcp）；Cursor 也会（cursor.com/docs/context/mcp）；Claude Desktop 未能从一手来源核实。URL 模式 elicitation 自规范 2025-11-25 起有；Claude Code 支持；Python SDK 文档说 `ctx.elicit_url` 在 2026-07-28 版连接上会失败。
- **F4 MCP Registry**（预览版）：`$schema` `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`；必填 `name`、`description`、`version`；包必填 `registryType`、`identifier`、`transport`，`version` 必须是确切版本；PyPI 包可选 `runtimeHint: "uvx"`、`packageArguments`、`runtimeArguments`。GitHub 身份发布要求 `io.github.<user>/…` 命名空间；PyPI 归属校验读 README 里的 `mcp-name: …`（HTML 注释可以）。CI 里 `mcp-publisher login github-oidc` + `mcp-publisher publish`（mcp-publisher v1.8.1）。`identifier` 不能带 extra。（modelcontextprotocol.io/registry/package-types、authentication、github-actions；registry 源码 internal/validators/registries/pypi.go）未核实：客户端怎样把 `runtimeArguments` 拼进 `uvx` 命令。
- **F5 MCPB**：manifest 0.4 新增 `server.type: "uv"`（发 `pyproject.toml`，宿主用 uv 装依赖，原生扩展可用，用户无需装 Python）；`mcpb-manifest-latest` 仍指向 0.3。`type: "python"` 无法可移植地打包带原生扩展的依赖。CLI `@anthropic-ai/mcpb` 2.1.2（`mcpb init` / `mcpb pack`）；Claude for macOS / Windows 打开文件即弹安装框。`user_config` 支持 string、number、directory、file，`sensitive` 标记，`${user_config.x}` 替换。未解决问题：#297（`mcp_config.env` 没传给服务）、#278（拒收已签名包）；uv 类型的 #291 在 2026-07-23 报告已修。（github.com/modelcontextprotocol/mcpb 的 README、MANIFEST.md、schemas/、issues）
- **F6 Trusted Publishing**：PyPA 指南（2026-10-12 更新）：构建 job `python -m build` 上传 artifact；发布 job `environment: pypi`、`id-token: write`、`pypa/gh-action-pypi-publish@release/v1`（最新 v1.14.2，2026-07-29）；checkout 用 `persist-credentials: false`。PEP 740 证明在 Trusted Publishing 下默认生成，非强制。TestPyPI 用 `repository-url: https://test.pypi.org/legacy/`。2026-07-22 起一个版本发布 14 天后不再接受新文件。（packaging.python.org 的 GitHub Actions 发布指南；github.com/pypa/gh-action-pypi-publish；blog.pypi.org）
- **F7 客户端配置**：Claude Code `claude mcp add --scope user <name> -- <command> <args…>`（`--` 之后原样传给服务）；Claude Desktop `claude_desktop_config.json` 的 `mcpServers`；Cursor `~/.cursor/mcp.json` 的 `mcpServers`（`type: "stdio"`）；VS Code `.vscode/mcp.json` 的 `servers`。`uvx --from 'pkg[extra]' cmd` 支持 extra（本设计不需要）。（code.claude.com/docs/en/mcp；modelcontextprotocol.io/docs/2026-07-28/develop/connect-local-servers；cursor.com/docs/context/mcp；code.visualstudio.com/docs/copilot/chat/mcp-servers；docs.astral.sh/uv/guides/tools/）

## 13. 留给实现者

- 工具描述的具体措辞；`get_status` 的确切字段名。
- 分桶对齐的细节（本地时间整点对齐；DST 日的桶长）。
- 发布流水线里截取 CHANGELOG 段落的脚本写法。
- `smoke` 矩阵是否加 Python 3.14；确认 `mcp` 2.x 在 3.14 上能装（CI 矩阵本来就含 3.14）。
- 注册表命名空间里 GitHub 用户名的大小写（`Jenwein` / `jenwein`）以注册表的校验为准。
