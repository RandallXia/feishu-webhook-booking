# admin-request-history - Work Plan

## TL;DR (For humans)

**What you'll get:** 配置页新增「记账历史」卡片：每次 AI 记账请求（成功/失败/重复混排）按时间倒序展示，带状态色芯片，可展开查看原始 OCR、精简原始数据和失败原因。历史落在本机 SQLite 库（Python 自带，无新依赖），重启不丢，用一个环境变量开关（不配置 = 完全关闭，行为与现在一致）。

**Why this approach:** SQLite 是 Python 标准库且写入原子——崩溃不会留脏数据，按时间倒序/筛选是一句 SQL；相比 JSONL 文件部署改动相同（都要一个可写目录）但可靠性更高。失败原因此前只进日志就丢失了，这次顺手打通了数据链路（仅入历史库，webhook 响应保持不变）。

**What it will NOT do:** 不改手机快捷指令调用的 webhook 接口（契约零变更）；不引入任何第三方依赖或数据库服务；原文不进日志、密钥永不入库；不做自动刷新/分页（手动刷新 + 条数上限）。

**Effort:** Medium
**Risk:** Medium - 风险集中在「历史功能绝不拖垮记账主链路」：所有存储失败都要静默降级，需专门测试锁死库/只读目录场景
**Decisions to sanity-check:** ① 持久化用 stdlib sqlite3 单文件（AGENTS.md 的"无数据库"铁律为此加了一条窄化例外）；② Docker 需新增 ./data 可写卷（历史库默认建议放 /data/history.db）；③ 记录上限默认查询 200 条（可调 1-1000），存储本身不设上限——磁盘占满属运维责任，文档有备份/清理提示。

Your next move: 审批后交由 worker 执行（`$start-work admin-request-history`）。Full execution detail follows below.

---

> TL;DR (machine): Medium effort / Medium risk — sqlite3 请求历史（存储模块 + PipelineResult 通路 + 路由钩子 + admin UI 卡片 + compose/gitignore/AGENTS + 测试文档），6 实现 todo + 4 终验；零新依赖，webhook 契约零变更。

## Scope
### Must have

1. **历史存储模块**（`app/history_store.py` 新建，零新依赖——stdlib sqlite3）：
   - `HistoryStore` 类：`__init__(db_path: Path)`；连接 `sqlite3.connect(db_path, check_same_thread=False)` + `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=2000`；`threading.Lock` 串行化全部操作（写+读）。
   - 建表 `CREATE TABLE IF NOT EXISTS history (...)`（列：id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, ts_iso TEXT, request_id TEXT, model TEXT, elapsed_ms INTEGER, ai_status TEXT, error TEXT, bill_record_id TEXT, original_text TEXT, summary TEXT, extracted TEXT(JSON), warnings TEXT(JSON)）+ `user_version=1` schema 版本戳；启动时 `PRAGMA user_version` != 1 且非 0 → 视为不兼容 schema，store 进入 degraded 模式（insert/list 全部 no-op + 一次性 error 日志），绝不抛出。
   - `insert(entry: HistoryEntry) -> None`：**吞掉一切异常**（`except Exception: logger.error(...)` 一次性节流）——历史写失败绝不影响 webhook。
   - `list_recent(limit: int) -> list[dict]`：`ORDER BY ts DESC, id DESC LIMIT ?`；空表返回 `[]`。
   - 初始化语义：`ensure_parent_dir`（`mkdir(parents=True, exist_ok=True)`，失败 → degraded no-op）；`db_path` 所在目录只读/不可建 → degraded no-op + 日志，**服务正常启动**。
   - `HistoryEntry` dataclass：与表列一一对应（extracted/warnings 为 dict/list，insert 时 json.dumps，list 时 json.loads）。
2. **Settings 扩展**（`app/config.py`）：`ai_history_db_path: Path | None` 字段 + `_path_env("AI_HISTORY_DB_PATH")`（None = 历史功能关闭）；`.env.example` AI 段追加注释行（含 Docker 建议值 `/data/history.db`）。
3. **PipelineResult 数据通路**（`app/pipeline.py`）：
   - 末尾追加 `summary: str | None = None` 与 `error: str | None = None`（frozen slots 默认值——现有 5 字段位置构造/关键字构造零回归）。
   - 4 个失败路径填 `error`：extract 失败（:175-188，`f"extract {exc.stage}: {exc}"`）、encode 失败（:201-213，`f"encode: {exc}"`）、create_record 失败（:259-272，`f"bill create {exc.stage}: {exc}"`）、顶层 except（:120-130，`"internal error"`——不带 str(exc)，防上游 payload 泄漏）。
   - 2 个成功路径填 `summary=extraction.summary`（all-bill-disabled 早退路径 :239-245 与最终路径 :284-290）。
   - duplicate 路径（:151-157）不填 summary/error。
   - **webhook 响应零变更**：WebhookSuccessResponse 不加字段，`ingest_ocr` 不读取 result.error/summary 进响应。
4. **记录钩子**（`app/main.py` `ingest_ocr`）：
   - AI 阶段分支（:292-347）内：`stage_start = time.monotonic()` 置于 `ai_registry.maybe_reload()` **之前**；三个记录点——①pipeline.run 正常返回后（status=result.ai_status，elapsed=int((monotonic()-stage_start)*1000)）②registry 503 分支 raise 之前（status="failed"，error="profile registry unavailable"）。
   - 记录内容：`ts=time.time()`、`ts_iso=datetime.now(Asia/Shanghai).isoformat(timespec="seconds")`、`request_id`、`model=settings.ai_model or ""`、`elapsed_ms`、`ai_status`、`error=result.error`（503 分支为固定文案）、`bill_record_id=result.bill_record_id`、`original_text=original_text`、`summary=result.summary`、`extracted=result.extracted or {}`、`warnings=result.warnings or []`。
   - **不记录** 401/422/Feishu 502/无 AI 阶段请求。
   - 全部 store 调用 `await asyncio.to_thread(store.insert, entry)` + store 自身吞异常 → webhook 主链路零阻塞风险。
   - store 实例化在 `lifespan`：`ai_history_db_path` 非 None 且 `settings.ai_enabled` 时构建 `app.state.history_store`，否则 None；构建失败（目录不可建等）由 HistoryStore 内部 degraded 吸收。
5. **历史读取 API**（`app/main.py` 新路由 `GET /admin/ai/history`）：
   - 鉴权逐字镜像 `_require_admin_auth`（X-Admin-Token + compare_digest；CONFIG_RELOAD_TOKEN 未设 → 404 RELOAD_DISABLED）。
   - `settings.ai_enabled` false / registry None → 404 `AI_DISABLED`；`ai_history_db_path` None 或 `app.state.history_store` None → 404 `HISTORY_DISABLED`（新错误码，形状镜像现有 error envelope）。
   - `limit`：`Query(default=200, ge=1, le=1000)`（pydantic 校验，越界 422）。
   - 响应 `{"success": true, "items": [...倒序...], "limit": N}`；已配置但零行 → 200 + `items: []`。
   - 读取走 `await asyncio.to_thread(store.list_recent, limit)`。
6. **前端历史卡片**（`app/static/admin.{html,css,js}`）：
   - admin.html：OCR Flow Tester 卡片之后新增 `<div class="card" id="history-section">`：标题「记账历史 / Request History」+ `#history-banner` + 刷新按钮 `#history-refresh-btn`（btn-secondary）+ `#history-limit-input`（number，默认 200，min=1 max=1000）+ `#history-list` 容器 + placeholder 文案。
   - admin.js：`function loadHistory()` + `function renderHistory(items)`（**grep 门要求 `function loadHistory(` / `function renderHistory(` 声明存在**）——`api('/admin/ai/history?limit=' + n)`；404 HISTORY_DISABLED/AI_DISABLED → placeholder 显示「历史未启用」；每条记录 = 混排时间线单卡：
     - **meta 行**（`.history-meta` flex，gap 12px，各字段 `white-space: nowrap; flex-shrink: 0`，短字段不互挤）：状态芯片（`.status-tag` 复用：succeeded→status-ok「✓ 成功」/ failed→status-err「✗ 失败」/ duplicate→status-warn「⏭ 重复」）+ 时间（ts_iso）+ 模型名 + 耗时（`N ms`）+ 摘要段（`¥{extracted.amount} {extracted.category}`，extracted 空则显示 `—`）。
     - **可展开详情**（`<details class="history-details">`，复用 env-details 样式模式）：summary「详情 / Details」；内容全宽块：`失败原因`（error 非空时红字 `.history-error`）、`精简原始数据`（summary，pre-wrap）、`原始 OCR`（original_text，pre-wrap）、`提取字段`（extracted 键值对，`  ↳ key: value` 风格镜像 renderOcrFlowResult :452-476）、`警告`（warnings 逐条）。
     - **XSS 铁律**：OCR 原文/summary/error/模型名/extracted 值/warnings 全部经 `el()` + textContent 渲染（复用 admin.js:93-111 helper），零 innerHTML。
   - admin.css：`.history-meta`（flex + nowrap 子项 + 640px 以下 flex-wrap）、`.history-details`（复用 env-details 折叠视觉）、`.history-error`（#c62828）、`.history-entry`（底部分隔线）、`.history-text`（pre-wrap + word-break）。
   - 初始化：init 段（admin.js:2261-2266）追加 `loadHistory()`；刷新按钮 click → `loadHistory()`。
7. **部署与配置面**：`docker-compose.yml` volumes 追加 `- ./data:/data`；`.gitignore` 追加 `data/`（DB + -wal + -shm 全不进 git）；`.env.example` 注释（见 Must-2）。
8. **AGENTS.md 修订**：反模式「Do not introduce DB/ORM/queue deps」追加窄化例外：stdlib sqlite3、单文件、无 ORM、仅请求历史用途（写失败 no-op 降级）；STRUCTURE 树 + CODE MAP 增 `history_store.py` / `GET /admin/ai/history` 行。
9. **测试**（TDD：Gherkin 空测试文件 → 用户确认 → 正式测试 → 实现；用户已在本计划审批中预先授权该流程的自动推进）：
   - `test/unit/test_history_store.py`：tmp_path 真库——insert/list_recent round-trip（含中文/换行/JSON 字段）；倒序+limit；空表 []；**损坏/不兼容 schema（user_version=99）→ degraded no-op 不抛**；只读目录 → 初始化不抛、insert no-op；**5 位置参数旧式 PipelineResult 构造回归**（默认 summary/error=None）。
   - `test/unit/test_history_api.py`：mock pipeline 范式（镜像 test_webhook_ai.py:36-90）——webhook 成功/失败/重复/registry-503 四路径后 `list_recent` 均有条目（用同 db_path 的独立 store 实例断言）；webhook JSON 不含 error/summary 新键（契约回归）；401/404（AI_DISABLED/HISTORY_DISABLED）/422 limit 越界；limit 边界 1/1000；**conftest 增补**：`os.environ["AI_HISTORY_DB_PATH"] = ""` 钉死（镜像 :10-14 模式），启用测试用 `dataclasses.replace` + monkeypatch tmp_path。
   - `test/unit/test_admin_history_ui.py`：grep 门（`function loadHistory(` / `function renderHistory(` 声明、`id="history-section"` / `id="history-refresh-btn"` / `id="history-limit-input"` / `id="history-list"` 存在、`.history-meta` / `.history-details` CSS 存在）+ `node --check admin.js`（镜像 test_admin_page.py:134-153）+ history-render 全 textContent 断言（admin.js history 段无 innerHTML 赋值——`re.search(r"innerHTML", <history section slice>)` 为 None）。
   - 全量回归：`python -m pytest` 0 failed（存量 ~272+ 全绿）。
10. **双语文档**：`docs/ai-pipeline.md` + `docs/en/ai-pipeline.md` 新增「请求历史 / Request History」节（`## ` 计数 +1 双语同步）：AI_HISTORY_DB_PATH 配置、记录的字段清单、隐私边界（原文入库不进日志、admin token 门控）、运维查看（sqlite3 CLI / UI）、data/ 卷说明 + 备份提示（-wal/-shm 随库）；README 两镜像各一段落（AI 管线简介后）。

### Must NOT have (guardrails, anti-slop, scope boundaries)

- 不改 `WebhookRequest` / `WebhookSuccessResponse`（webhook 契约零变更；响应不加 ai_error/ai_summary——error/summary 仅入历史）
- 不引第三方依赖（sqlite3 = stdlib；无 ORM/无 alembic/无 DB 服务器/不进 requirements.txt）
- 历史库写失败/读失败/初始化失败**绝不抛出到 webhook 主链路**（全部吞掉 + 节流日志；webhook 延迟不受锁死库影响——to_thread + busy_timeout=2000）
- 不进日志：original_text/summary/extracted 不写 logger（沿用 pipeline.py:13 铁律）；error 字段入库用固定模板文案（顶层 except 不带 str(exc)，防上游 payload 泄漏）
- 密钥（AI_API_KEY/FEISHU_APP_SECRET/WEBHOOK_SHARED_TOKEN/CONFIG_RELOAD_TOKEN）永不进历史条目
- 不记录 401/422/无 AI 阶段请求；401/422 路径零改动
- 不做分页 UI/轮询/自动刷新（手动刷新 + limit 单参）；不做历史条目删除/清空 API
- 不做前端框架/构建（纯 vanilla 三文件）；history 渲染零 innerHTML（全 el()/textContent）
- `data/` 进 .gitignore——真实 OCR 文本永不提交
- 现有 16 条路由/现有卡片/现有测试断言零回归（grep 门函数名、id 清单、AI_KEYS 等全部保留）

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: **TDD**（仓库主人硬性约定：Gherkin 骨架 → 确认 → 正式测试 → 实现；本计划审批时用户已预先授权自动推进该确认点）+ pytest + pytest-asyncio；store 用 tmp_path 真 sqlite 库；路由用 `httpx.AsyncClient(transport=ASGITransport(app))` + `async with lifespan(app):` + AsyncMock pipeline（镜像 test_webhook_ai.py 范式）；JS 语法门 `node --check`（硬性）。
- Evidence: `.omo/evidence/task-<N>-admin-request-history.txt`（happy + failure 命令与输出各一段）。
- 关键回归锁：存量测试全绿；webhook 成功响应 JSON 键集合不变（5 键 disabled 模式 + ai_* 键 enabled 模式，均不含 error/summary）；`AI_HISTORY_DB_PATH` 未设时行为与现在逐字节一致（零 DB 文件创建、零新路由响应体差异）。

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

- **Wave 1（后端原语，2 项并行）**：todo 1（history_store + Settings）∥ todo 2（PipelineResult 数据通路）
- **Wave 2（接线，串行）**：todo 3（lifespan + ingest_ocr 钩子 + GET /admin/ai/history，依赖 1+2）
- **Wave 3（前端+部署）**：todo 4（前端三文件，依赖 3）∥ todo 5（compose/.gitignore/.env.example/AGENTS.md，依赖 1）
- **Wave 4（收口）**：todo 6（集成测试 + 双语文档，依赖 3+4+5）

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | — | 3, 4 | 2 |
| 2 | — | 3 | 1 |
| 3 | 1, 2 | 4, 6 | 5 |
| 4 | 3 | 6 | 5 |
| 5 | 1 | 6 | 3, 4 |
| 6 | 3, 4, 5 | F1-F4 | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

- [x] 1. app/history_store.py + Settings：sqlite3 历史存储与门控配置
  What to do / Must NOT do: 新模块 `app/history_store.py`：`HistoryEntry` frozen dataclass（ts/ts_iso/request_id/model/elapsed_ms/ai_status/error/bill_record_id/original_text/summary/extracted/warnings——extracted=list[dict]→JSON 列，warnings=list[str]→JSON 列）；`HistoryStore(db_path)`：`sqlite3.connect(str(db_path), check_same_thread=False)` + `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=2000` + `threading.Lock` 全操作串行化；`CREATE TABLE IF NOT EXISTS history`（列同 entry + `id INTEGER PRIMARY KEY AUTOINCREMENT`）+ `PRAGMA user_version=1`；构造时 `PRAGMA user_version` 读出：0 → 建表并置 1；1 → 直接用；其它值 → **degraded 模式**（`self._degraded=True`，insert/list_recent 直接 return，一次性 `logger.error`）。父目录 `mkdir(parents=True, exist_ok=True)` 失败或连接/PRAGMA 异常 → 同样 degraded，**构造函数不抛**。`insert(entry)`：锁内 INSERT，`except Exception: logger.error`（模块级节流：首次全文本，其后每 60s 一条摘要）；`list_recent(limit)`：锁内 `SELECT ... ORDER BY ts DESC, id DESC LIMIT ?` → list[dict]（JSON 列 json.loads，空表 []）。`app/config.py`：Settings 字段 `ai_history_db_path: Path | None`，`get_settings` 内 `_path_env("AI_HISTORY_DB_PATH")`。`.env.example` AI 段追加注释块（`#AI_HISTORY_DB_PATH=/data/history.db` + 说明：未设=历史关闭；Docker 需可写卷）。**Must NOT**：构造/insert/list 不抛异常到调用方；不建连接池/不做 ORM；不在模块 import 时触碰文件系统。
  Parallelization: Wave 1 | Blocked by: 无 | Blocks: 3, 5
  References: `app/config.py:58-86`（Settings 字段序）、`app/config.py:120-128`（_path_env）、`app/config.py:131-191`（get_settings 组装点）、`app/pipeline.py:105-107`（线程锁模式先例）、`.env.example:20-38`（AI 段注释风格）
  Acceptance criteria (agent-executable): `python -m pytest test/unit/test_history_store.py -v` 全绿——round-trip（中文/换行/emoji 原文 + extracted/warnings JSON 往返）；倒序+limit 截断；空表 []；user_version=99 预置库 → insert/list no-op 且不抛；只读目录（chmod 0o555 tmpdir）→ 构造不抛 + degraded；Settings 断言 `get_settings().ai_history_db_path is None`（未设 env 时）且 monkeypatch 设值后为 Path。
  QA scenarios: happy = `python -m pytest test/unit/test_history_store.py -v`；failure = `-k "degraded or readonly or incompatible"`。Evidence `.omo/evidence/task-1-admin-request-history.txt`
  Commit: Y | Add sqlite-backed request history store and AI_HISTORY_DB_PATH setting

- [x] 2. app/pipeline.py：PipelineResult 补 summary/error 数据通路
  What to do / Must NOT do: `PipelineResult`（:34-40）末尾追加 `summary: str | None = None`、`error: str | None = None`（保持 frozen slots；现有构造全部兼容）。填充：extract 失败路径（:182-188）`error=f"extract {exc.stage}: {exc}"`；encode 失败路径（:207-213）`error=f"encode: {exc}"`；create_record 失败路径（:266-272）`error=f"bill create {exc.stage}: {exc}"`；**顶层 except（:124-130）`error="internal error"`（不带 str(exc)——异常文本可能含上游 payload）**；成功路径（:239-245 与 :284-290）`summary=extraction.summary`；duplicate 路径（:151-157）不填。**Must NOT**：不改 run() 永不抛语义；不改日志行（error 只进 PipelineResult，不进 logger）；不动 _build_extracted/_validate_input；不把 error/summary 暴露到任何响应体。
  Parallelization: Wave 1 | Blocked by: 无 | Blocks: 3
  References: `app/pipeline.py:34-40`（dataclass）、`:120-130`（顶层 except）、`:175-188`（extract 失败）、`:201-213`（encode 失败）、`:233-245`（all-disabled 成功路径）、`:259-272`（create 失败）、`:274-290`（最终成功路径）
  Acceptance criteria: `python -m pytest test/unit/test_pipeline.py -v` 全绿 + 新增断言：旧式 `PipelineResult("succeeded", "rec", [], {}, False)` 5 位置参数构造成功且 summary/error 均 None；4 失败路径各自 error 文案前缀（extract/encode/bill create/internal error）；成功路径 summary == extraction.summary；duplicate 两字段 None。全量 pytest 存量 0 failed。
  QA scenarios: happy = `python -m pytest test/unit/test_pipeline.py test/unit/test_webhook_ai.py -v`；failure = `-k "error"`（文案前缀断言）。Evidence `.omo/evidence/task-2-admin-request-history.txt`
  Commit: Y | Carry extraction summary and failure reason through PipelineResult

- [x] 3. app/main.py 接线：lifespan 构建 store + ingest_ocr 记录钩子 + GET /admin/ai/history
  What to do / Must NOT do: ①`lifespan`（:83-111）AI 分支内：`settings.ai_history_db_path` 非 None → `app.state.history_store = HistoryStore(settings.ai_history_db_path)`，否则 None（HistoryStore 构造自身不抛，无需 try）。②`ingest_ocr` AI 分支（:292-347）：`stage_start = time.monotonic()` 放在 `await ai_registry.maybe_reload()` 之前；registry 503 分支（:307-319）在 raise 之前 `await asyncio.to_thread(history_store.insert, HistoryEntry(ts=time.time(), ts_iso=..., request_id=..., model=settings.ai_model or "", elapsed_ms=int((time.monotonic()-stage_start)*1000), ai_status="failed", error="profile registry unavailable", bill_record_id=None, original_text=original_text, summary=None, extracted={}, warnings=[]))`（store None 时跳过）；pipeline.run 返回后同式记录（ai_status=result.ai_status、error=result.error、bill_record_id=result.bill_record_id、summary=result.summary、extracted=result.extracted or {}、warnings=result.warnings or []）。`ts_iso=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")`（main.py 顶部 import time/zoneinfo/HistoryEntry/HistoryStore——HistoryStore import 放模块顶部但仅 lifespan 使用）。③新路由 `GET /admin/ai/history`：`limit: int = Query(default=200, ge=1, le=1000)`；`_require_admin_auth` 镜像；AI off/registry None → 404 AI_DISABLED（镜像 :472-479 形状）；`settings.ai_history_db_path is None or getattr(app.state,"history_store",None) is None` → 404 `HISTORY_DISABLED`（新码，envelope 形状同现有）；读 `await asyncio.to_thread(store.list_recent, limit)` → `{"success": True, "items": [...], "limit": limit}`。**Must NOT**：不记录 401/422/Feishu 502/无 AI 阶段请求；WebhookSuccessResponse 零变更；store 调用必须 to_thread 包裹（不阻塞事件循环）；不改现有 16 路由任何行为。
  Parallelization: Wave 2 | Blocked by: 1, 2 | Blocks: 4, 6
  References: `app/main.py:83-111`（lifespan AI 分支）、`:292-347`（AI 阶段全文）、`:307-319`（registry 503 分支）、`:449-469`（ai_test_dry_run 鉴权模板）、`:472-479`（AI_DISABLED 形状）、`:660-667`（_require_admin_auth）、`app/history_store.py`（todo 1 产出）、`app/pipeline.py`（todo 2 产出）
  Acceptance criteria: `python -m pytest test/unit/test_history_api.py -v` 全绿——四路径记录断言（成功/失败/重复/registry-503 各产生一条状态正确的记录；503 时 webhook 仍 503）；webhook 成功响应 JSON 键集合断言不含 error/summary；401 无 token；404 AI_DISABLED（AI off）；404 HISTORY_DISABLED（AI on 但 db_path None——dataclasses.replace 构造 settings）；limit=0/1001 → 422；limit=1/1000 → 200；配置+零行 → 200 items:[]。
  QA scenarios: happy = `python -m pytest test/unit/test_history_api.py -v`；failure = `-k "disabled or unauthorized or limit"`。Evidence `.omo/evidence/task-3-admin-request-history.txt`
  Commit: Y | Record AI request history in webhook route and expose history API

- [x] 4. 前端历史卡片：admin.html/css/js 混排时间线
  What to do / Must NOT do: `admin.html`：OCR Flow Tester 卡片（:181-201）之后追加 `<div class="card" id="history-section">`——card-title「记账历史 / Request History」+ `<div id="history-banner"></div>` + 控件行（`<button class="btn btn-secondary" id="history-refresh-btn">刷新 / Refresh</button>` + `<input type="number" id="history-limit-input" min="1" max="1000" value="200">` + hint「条数 / Entries」）+ `<div id="history-list"><p class="placeholder">加载中 / Loading…</p></div>`。`admin.js`（OCR flow 段后新增 section，风格镜像 :420-535）：`function loadHistory()`（读 #history-limit-input 值 clamp 1..1000 → `api('/admin/ai/history?limit='+n)` → renderHistory；404 → placeholder「历史未启用 / History disabled（设置 AI_HISTORY_DB_PATH 后可用）」；其它错误 → showError）+ `function renderHistory(items)`（清空容器；空数组 → placeholder「暂无记录 / No records yet」；每条 `el('div',{className:'history-entry'})`：`.history-meta` 行 = 状态芯片 span.status-tag（succeeded→status-ok「✓ 成功」/failed→status-err「✗ 失败」/duplicate→status-warn「⏭ 重复」/其它→status-info 原文）+ span 时间(ts_iso) + span 模型(model||'—') + span 耗时(elapsed_ms+' ms') + span 摘要（extracted.amount/category 存在→`¥amount category`，否则 '—'）；`el('details',{className:'history-details'})` 内 summary「详情 / Details」+ 全宽块：error 非空 → `.history-error` 行「失败原因: …」；summary 非空 → 「精简原始数据」块 `.history-text`；original_text → 「原始 OCR」块；extracted 非空 → 「提取字段」逐键 `↳ k: v`；warnings 逐条「⚠ …」。**全部动态文本 el()/textContent，禁止 innerHTML**）。init 段（:2261-2266）追加 `loadHistory()` + `history-refresh-btn` click → loadHistory。`admin.css`：`.history-entry{border-bottom:1px solid #e8e8ed;padding:10px 0;}`、`.history-meta{display:flex;gap:12px;align-items:center;font-size:0.8rem;flex-wrap:wrap;}`、`.history-meta > span{white-space:nowrap;}`、`.history-details{margin-top:6px;}`（复用 env-details 视觉：:198-205 模式抄写为 .history-details 规则）、`.history-error{color:#c62828;font-size:0.8rem;}`、`.history-text{white-space:pre-wrap;word-break:break-word;background:#f5f5f7;border-radius:8px;padding:8px 12px;font-size:0.78rem;margin-top:6px;}`、640px 断言 .history-meta wrap（默认已 wrap，无需额外）。**Must NOT**：grep 门函数 `loadHistory(`/`renderHistory(` 必须以 `function name(` 声明；不动现有卡片/函数/id；history 段零 innerHTML；不改测试 grep 的既有函数名。
  Parallelization: Wave 3 | Blocked by: 3 | Blocks: 6
  References: `app/static/admin.html:181-201`（卡片结构先例）、`app/static/admin.js:420-535`（OCR flow 段——api/el/showBanner/renderOcrFlowResult 先例）、`admin.js:93-111`（el helper）、`admin.js:2261-2266`（init 段）、`app/static/admin.css:20-24`（status-tag 四色）、`:198-205`（env-details 折叠）、`test/unit/test_admin_bill_flow.py:586-620`（grep 门范式）、`test/unit/test_admin_page.py:134-153`（node --check 范式）
  Acceptance criteria: `node --check app/static/admin.js` 退出 0；`python -m pytest test/unit/test_admin_history_ui.py -v` 全绿——grep 断言（两函数声明 + 4 个 id + .history-meta/.history-details CSS 存在）+ history 段 innerHTML 零命中断言（切片 admin.js 的 history section 注释行之后文本 re.search innerHTML → None）；`python -m pytest test/unit/test_admin_page.py -v` 存量零回归。
  QA scenarios: happy = node --check + 全部 ui pytest；failure = 人为在 renderHistory 塞 innerHTML → innerHTML 断言失败（开发期自检，不入库）。Evidence `.omo/evidence/task-4-admin-request-history.txt`
  Commit: Y | Add request history card to admin page with status chips and expandable details

- [x] 5. 部署与配置面：compose 卷 + .gitignore + AGENTS.md
  What to do / Must NOT do: `docker-compose.yml` volumes 追加 `- ./data:/data`（注释：AI_HISTORY_DB_PATH 建议值 /data/history.db）；`.gitignore` 追加 `data/` 段（注释：请求历史 sqlite 库——含 -wal/-shm——含真实 OCR 文本，永不提交）；根 `AGENTS.md`：反模式「Do not introduce DB/ORM/queue deps」句尾追加窄化例外「exception: stdlib sqlite3 in app/history_store.py (single file, no ORM, request-history only, all store failures swallowed)」；STRUCTURE 树 app/ 段加 history_store.py 行 + CODE MAP 表加 HistoryStore/GET /admin/ai/history 行；app/AGENTS.md WHERE TO LOOK 表同步加行。**Must NOT**：不动 requirements*.txt；不改既有 compose 行为（仅加一行卷）；不提交任何 data/ 实物。
  Parallelization: Wave 3 | Blocked by: 1 | Blocks: 6
  References: `docker-compose.yml`（全文 17 行）、`.gitignore`（runtime 段 :10-16 附近模式）、`AGENTS.md`（STRUCTURE/CODE MAP/ANTI-PATTERNS 三处）、`app/AGENTS.md`（WHERE TO LOOK 表）
  Acceptance criteria: `git status --short` 不出现 data/（创建 data/history.db 假文件后验证被忽略）；`git check-ignore data/test.db` 退出 0；AGENTS.md grep「history_store」≥2 命中；compose 含 `./data:/data` 行。
  QA scenarios: happy = 上述 grep/check-ignore；failure = git check-ignore 退出码非 0 即败。Evidence `.omo/evidence/task-5-admin-request-history.txt`
  Commit: Y | Add data volume, gitignore, and docs for request history store

- [x] 6. 集成测试 + 双语文档
  What to do / Must NOT do: `test/unit/test_history_integration.py`：①conftest 钉 `AI_HISTORY_DB_PATH=""`（编辑根 conftest.py :10-14 段——注意该文件被 todo 1-5 的测试共用，只增不删）；②端到端：monkeypatch settings（dataclasses.replace ai_enabled=True + ai_history_db_path=tmp_path/'h.db'）+ mock feishu/pipeline/registry（镜像 test_webhook_ai.py:36-90）→ 连发 3 请求（成功/失败/重复）→ GET /admin/ai/history → 断言 3 条、倒序、状态集合 {succeeded,failed,duplicate}、meta 字段齐全（model/elapsed_ms/ts_iso 非空）；③重启语义：同 tmp_path 新建第二个 HistoryStore 实例 → list_recent 读到同 3 条（跨连接持久化证明）；④隐私断言：list_recent 结果序列化全文不含 WEBHOOK token 值/AI_API_KEY 值。文档：`docs/ai-pipeline.md` + `docs/en/ai-pipeline.md`「请求历史 / Request History」节（编号顺延、`## ` 计数双语同步 +1）：配置（AI_HISTORY_DB_PATH + 未设=关闭）、记录字段表、隐私边界（原文入库不进日志、admin token 门控、data/ 不进 git）、运维（UI 卡片 + sqlite3 history.db CLI + 备份含 -wal/-shm 提示）；`README.md` + `docs/en/README.md` AI 管线段后各加 2-3 句简介。**Must NOT**：不改 docs 其它节语义；双语结构必须对称（标题层级/表格行数一致）。
  Parallelization: Wave 4 | Blocked by: 3, 4, 5 | Blocks: F1-F4
  References: `test/unit/test_webhook_ai.py:36-90`（mock 范式全文）、`test/unit/test_config_ui_integration.py`（集成范式）、`docs/ai-pipeline.md`（节结构）、`docs/en/ai-pipeline.md`（镜像）、`README.md` + `docs/en/README.md`（AI 段落位置）、conftest.py:9-14
  Acceptance criteria: `python -m pytest test/unit/test_history_integration.py -v` 全绿；`python -m pytest` 全量 0 failed（存量 + 新增）；`grep -c '^## ' docs/ai-pipeline.md` == `grep -c '^## ' docs/en/ai-pipeline.md`；README 两文件均含「请求历史」/"Request History"。
  QA scenarios: happy = 全量 pytest + 文档结构断言；failure = 重复请求去重语义（重复路径也入库，断言不误删）。Evidence `.omo/evidence/task-6-admin-request-history.txt`
  Commit: Y | Add history integration tests and bilingual request-history docs

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit
- [x] F2. Code quality review
- [x] F3. Real manual QA
- [x] F4. Scope fidelity

## Commit strategy
每 todo 一原子提交（祈使句英文，镜像仓库现有风格）；提交前全量 pytest 0 failed + `node --check`；不提交 runtime 真实值、data/ 实物与 .omo/；在 master 或用户指定分支延续（无 PR 要求时直接 master）。

## Success criteria
1. [agent] 全量 pytest 0 failed（存量 ~272 + 新增）；PipelineResult 5 位置参数旧式构造回归通过
2. [agent] webhook 契约零变更：成功响应 JSON 键集合与现在逐字节一致（不含 error/summary）；`AI_HISTORY_DB_PATH` 未设时零 DB 文件创建、全部现有路由行为不变
3. [agent] 四路径记录完整：succeeded/failed/duplicate/registry-503 各有记录，meta（时间/模型/耗时/状态）+ 详情（原文/精简数据/失败原因/警告）字段齐全
4. [agent] 历史功能故障隔离：损坏库/只读目录/锁死场景下 webhook 正常响应、服务正常启动（专项测试锁定）
5. [human F3] /admin/ai 页面历史卡片：混排时间线 + 状态芯片着色正确；短字段行不互挤；OCR 原文/精简数据/失败原因在展开块全宽可读；刷新按钮工作
6. [agent] 双语文档同步 + AGENTS.md 例外条款落地 + data/ 进 gitignore
