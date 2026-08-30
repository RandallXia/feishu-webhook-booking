# frontend-config-ui - Work Plan

## TL;DR (For humans)

**What you'll get:** 一个完整的网页配置界面——粘贴多维表格链接自动识别 key、下拉选择表和记录、可视化编辑全部配置项（两张表的映射、提示词、AI 连接、多账本），点保存即校验+写入+热重载，再也不用手改 TOML 文件。

**Why this approach:** 真机联调证实手改 TOML 是最大痛点（语法错误/改错文件/回退不匹配/日期格式）。完整编辑器满足"每个配置项都有 UI 控件"的原始需求；向导式自动推导（按字段名启发式预填映射）让配置从"填 8 行表"变成"确认 8 行预填"。

**What it will NOT do:** 不做扫码登录；不引入前端框架/构建步骤（纯 vanilla HTML/JS/CSS）；不新增运行时依赖（手写 TOML 序列化器）；不改 webhook 契约；v1 不解析 wiki 链接（报错提示改用 /base/ 链接）。

**Effort:** Large
**Risk:** Medium - 配置写回与热重载/只读卷的交互（原子写+强制重载+409 降级兜底）
**Decisions to sanity-check:** ① AI 连接在 UI 编辑后需重启生效（env 是启动时加载）——UI 会明确提示；② 保存会重写 TOML（注释丢失，自动 .bak 备份；.example 文件因文件名不同永不被写入，保留为注释参考）；③ 多账本完整增删改（按年记账模式）。

---

> TL;DR (machine): Large effort / Medium risk — 完整前端配置编辑器（URL 解析 + 表/字段/记录选择器 + 字段映射自动推导 + 配置写回 API + alias CRUD + env 编辑），12 实现 todo + 4 终验；零新运行时依赖。

## Scope
### Must have

1. **FeishuClient 扩展**（`app/feishu_client.py`）：`list_tables(app_token)`（GET /bitable/v1/apps/{app_token}/tables，分页）；`list_records(app_token, table_id, page_token)`（POST /records/search 搜索端点——官方推荐，GET list 已废弃；返回 record_id + fields）。
2. **TOML 序列化器**（`app/toml_writer.py` 新模块）：手写确定性序列化（schema 固定：targets 的 `[targets."alias"]` 点键形式 + profile 的顶键+两 section+[[fields]] 数组），round-trip 测试对拍 tomllib.loads。
3. **校验升级**：`parse_profile_text(text)` 重构（parse_profile 委托之）；`validate_profile_candidate()` —— 除现有校验外**新增**：summary_field 必须存在于**提取表**的 list_fields（现有代码只查账单表——隐性缺口，"保存成功但每个 webhook 静默失败"）；targets 校验函数复用化（alias 正则/年份一致/默认项存在）。
4. **配置 API**（全部 X-Admin-Token 门控，镜像 reload_config 鉴权）：
   - `GET /admin/feishu/tables?app_token=` / `GET /admin/feishu/fields?app_token=&table_id=` / `GET /admin/feishu/records?app_token=&table_id=&page_token=` —— 选择器数据源
   - `GET/PUT /admin/config/profile` —— PUT 流程：validate_profile_candidate（**两张表都拉 list_fields**）→ tmp+os.replace 原子写 + .bak 备份 → `ai_registry.reload(force=True)` → 返回 {generation, warnings}；校验失败 → 422 逐字段错误 [{path, message}]；:ro → 409 RUNTIME_READONLY
   - `GET/PUT /admin/config/targets` —— 完整替换式写入（alias CRUD 由 UI 层组装全量 targets）；校验 → 原子写 → `target_registry.reload(force=True)`
   - `GET/PUT /admin/config/env` —— AI_* 键编辑（AI_ENABLED/PROVIDER/BASE_URL/MODEL/TIMEOUT/API_KEY）；行式读写 env 文件（保留其它行与注释）；密钥 write-only（空=不修改）；返回 {restart_required: true}；无 env 文件 → 409 ENV_FILE_NOT_FOUND；其余 env（FEISHU_APP_SECRET 等）只显示 set/unset，不可编辑
5. **并发守卫**：PUT 携带 base_generation，与 registry.get_status() 不符 → 409 STALE_WRITE；服务端 asyncio.Lock 串行化 validate→write→reload。
6. **前端**（app/static/ 拆三文件：admin.html + admin.js + admin.css，`app.mount("/admin/ai/assets", StaticFiles(...))`，零构建）：
   - §Token 栏（sticky）+ 只读状态条（generation/config_valid/:ro 横幅/restart 提示）
   - §提取表配置：alias 卡片列表（完整 CRUD：新增/编辑/删除/启停）——粘 /base/ URL（JS 正则解析 app_token+table_id）→ 表下拉（list_tables）→ **记录选择器**（list_records 分页 + is_primary 字段预览 + 加载更多，上限 5 页）→ alias/year/原始信息字段下拉（提取表 list_fields）
   - §账单表配置：粘 URL → 表下拉 → **8 行字段映射表**（ai_key 固定列 + feishu_field 下拉（实时字段）+ 类型选择 + 回退下拉（single_select 时实时选项）+ prompt 文本域）+ **自动推导预填**（按字段名启发式：含"金额"→number、"日期"→date、"分类/类型/途径"→single_select+首选项为回退；仅预填未手选的行）+ prompt_header 编辑
   - §AI 连接：provider/base_url/model/timeout 可编辑 + API_KEY 密码框（write-only，显示已设置/未设置）+ 保存后"需重启生效"横幅
   - §保存/校验/重载：逐字段内联错误（422 的 path→对应控件红框）+ 成功显示新 generation + .bak 提示；干跑测试复用现有 POST /admin/ai/test
   - **XSS 铁律**：所有 Feishu 来源字符串（字段名/选项名/记录预览）一律 textContent/createElement 渲染，禁止 innerHTML 拼接
7. **测试**：TDD 全程（Gherkin 骨架→确认→正式测试→实现）；MockTransport mock 飞书；ASGITransport 测路由。
8. **文档**：docs/ai-pipeline.md + docs/en/ai-pipeline.md 增补配置界面章节（双语镜像）；README 两镜像段落；AGENTS.md 结构/命令更新。

### Must NOT have (guardrails, anti-slop, scope boundaries)

- 无前端框架/npm/构建步骤（纯 vanilla，≤3 个静态文件）
- 无新运行时依赖（不引 tomli_w/tomlkit——手写序列化器）
- 无 OAuth/扫码/用户 token
- webhook 契约不变（WebhookRequest 保持 extra="forbid"）
- **任何 GET 端点绝不返回 AI_API_KEY / FEISHU_APP_SECRET 的值**（只 set/unset）
- v1 不解析 wiki 链接（明确报错："请从浏览器地址栏复制 /base/ 链接"）；URL 域名限 *.feishu.cn / *.larksuite.com
- 无 CORS（同源 + X-Admin-Token header）
- 配置写入只走 admin 面（webhook 永远不触发配置写）
- 不做记录分享链接解析（不透明 token，无官方解析 API——事实核查结论）

## Verification strategy
> Zero human intervention except final manual QA - all verification is agent-executed.
- Test decision: **TDD**（仓库主人硬性约定：Gherkin 骨架 → question 确认 → 正式测试 → 实现）+ pytest + pytest-asyncio；飞书 API 用 `httpx.MockTransport`（handler 内断言请求），路由用 `httpx.AsyncClient(transport=ASGITransport(app))` + `async with lifespan(app):`。
- 测试隔离沿用现有 conftest（模式与 AI 变量钉死 + cache_clear autouse）。
- Evidence: `.omo/evidence/task-N-frontend-config-ui.txt`。
- 关键回归锁：现有 106 测试全绿；`AI_ENABLED=false` 时 webhook 5 键响应不变。
- F4 手动 QA（唯一人机门）：用户 Windows 本机打开 /admin/ai，粘真实 base URL，端到端配置两表并保存。

## Execution strategy
### Parallel execution waves

- **Wave 1（后端原语，3 项并行）**：todo 1（feishu_client 扩展）∥ todo 2（toml_writer）∥ todo 3（校验重构）
- **Wave 2（配置 API，4 项）**：todo 4（选择器端点，依赖 1）∥ todo 5（profile PUT/GET，依赖 2+3）∥ todo 6（targets PUT/GET，依赖 2）∥ todo 7（env 端点，独立）
- **Wave 3（前端，串行骨架→分区→集成）**：todo 8（骨架+资产拆分）→ {9（提取表）∥ 10（账单表）} → 11（AI 连接+保存流）→ 12（集成测试+文档）

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | — | 4, 9 | 2, 3 |
| 2 | — | 5, 6 | 1, 3 |
| 3 | — | 5 | 1, 2 |
| 4 | 1 | 9, 10 | 5, 6, 7 |
| 5 | 2, 3 | 11 | 4, 6, 7 |
| 6 | 2 | 9 | 4, 5, 7 |
| 7 | — | 11 | 4, 5, 6 |
| 8 | 4 | 9, 10 | — |
| 9 | 4, 6, 8 | 12 | 10 |
| 10 | 4, 5, 8 | 12 | 9 |
| 11 | 5, 7, 8 | 12 | — |
| 12 | 9, 10, 11 | F1-F4 | — |

## Todos
> Implementation + Test = ONE todo. Never separate.

- [x] 1. app/feishu_client.py 扩展：list_tables + list_records
  What to do / Must NOT do: `async list_tables(app_token: str) -> list[dict]`——GET `{base}/open-apis/bitable/v1/apps/{app_token}/tables?page_size=100`，`has_more` 翻页循环，返回 `[{table_id, name}]`（合并全页）；`async list_records(app_token: str, table_id: str, page_token: str | None = None) -> dict`——**POST** `{base}/.../records/search`（官方推荐搜索端点；GET list 已废弃——事实核查）。**请求体形状先核对官方文档**（https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/bitable-v1/app-table-record/search 的 body schema：page_size/field_names/filter 等可选参数的位置——query vs body 以文档为准，不预设"空 body {}"）；返回 `{"items": [{record_id, fields}], "has_more": bool, "page_token": str | None}`（单页语义，翻页由调用方驱动）；两者均复用 `_get_tenant_access_token` + per-call AsyncClient；错误 stage="list_tables"/"list_records"。Must NOT：不缓存结果（选择器是交互式调用）；不引入新依赖；**不把未验证的响应形状钉死为实现**——响应解析以 mock 定义的文档形状为准。
  Parallelization: Wave 1 | Blocked by: 无 | Blocks: 4, 9
  References: `app/feishu_client.py:25-105`（现有模式全文）、list_fields 翻页先例（`app/feishu_client.py` 现有实现）、飞书文档 https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table/list 与 .../app-table-record/search（事实核查 High 置信）
  Acceptance criteria (agent-executable): `python -m pytest test/unit/test_feishu_picker.py -v` 全绿：list_tables 单页/翻页合并；list_records POST 断言（URL、Bearer header、请求体形状与文档核对结论一致）→ items/has_more/page_token 解析（**响应 mock 按文档 schema 定义，不钉死未验证字段**）；code!=0/非 200 → FeishuClientError 对应 stage。
  QA scenarios: happy = `python -m pytest test/unit/test_feishu_picker.py -v`；failure = `-k "error"` 用例（stage 正确）。Evidence `.omo/evidence/task-1-frontend-config-ui.txt`
  Commit: Y | Add list_tables and list_records to FeishuClient

- [x] 2. app/toml_writer.py：确定性 TOML 序列化器
  What to do / Must NOT do: 新模块，两个纯函数：`dump_targets(data: dict) -> str`（输入与 feishu-targets.toml 解析后同构：default_alias + targets dict→ 输出 `default_alias = "..."` + 每别名 `[targets."{alias}"]` 块，键序固定：app_token/table_id/record_id/original_field_name/year/enabled）与 `dump_profile(profile: dict) -> str`（顶键 prompt_header **在最前**——TOML 段前裸键规则 + `[extract]` + `[bill]` + 顺序 `[[fields]]`，每条键序：ai_key/feishu_field/type/target/fallback/source/prompt，None 值键省略）。字符串转义：`"` 与 `\` 转义，含双引号/反斜杠/换行的值安全序列化。Must NOT：不做注释保留（接受丢失，.example 是参考）；不引第三方库。
  Parallelization: Wave 1 | Blocked by: 无 | Blocks: 5, 6
  References: `runtime/feishu-targets.toml.example`（targets 结构）、`runtime/ai-profile.toml.example`（profile 结构与键序）、`app/target_registry.py:202-241`（动态模式解析出的 dict 形状）、`app/ai_profile.py:40-175`（profile 解析）
  Acceptance criteria: `python -m pytest test/unit/test_toml_writer.py -v` 全绿：两种 dump 输出经 `tomllib.loads` round-trip 后与输入 dict 深相等（含中文值/含引号值/含换行 prompt/None 省略/enabled=false/year=None）；输出确定性（同输入两次 dump 字节相同）；prompt_header 位于首个 section 之前（字符串断言）。
  QA scenarios: happy = round-trip 对拍全绿；failure = 含特殊字符用例（`"`/`\`/换行）不炸且还原。Evidence `.omo/evidence/task-2-frontend-config-ui.txt`
  Commit: Y | Add deterministic TOML writer for config files

- [x] 3. 校验重构：parse_profile_text + validate_profile_candidate（提取表校验缺口修复）
  What to do / Must NOT do: `app/ai_profile.py`：`parse_profile(path)` 重构为 `parse_profile_text(text: str) -> AiProfile` 的包装（单一事实源；**错误消息字符串必须逐字节不变**——纯委托提取，现有 106 测试的 golden 断言零回归）；新增 `async validate_profile_candidate(profile_text: str, feishu: FeishuClient, extract_app_token: str, extract_table_id: str) -> tuple[AiProfile, dict[str, set[str]], list[dict]]`——**提取表三元组是显式参数**（调用方 todo 5 的路由从 target_registry resolve 后传入；本函数不读 registry——Wave 1 单元测试用 stub 三元组即可，无需动态模式 fixture）——parse → `list_fields(bill)` → 现有 `_validate_whitelists` → **新增**：`list_fields(extract_app_token, extract_table_id)` → summary_field 必须在提取表字段中 → 返回 (profile, whitelists, errors)；errors 为 `[{path, message}]`（path 如 "fields[2].fallback"/"extract.summary_field"），全部收集而非首个即抛（供 UI 逐字段标红）；`parse_profile` 与现有 registry 行为完全不变（回归）。Must NOT：不改变现有 ProfileConfigError 语义与消息文本；不动 registry 公开接口。
  Parallelization: Wave 1 | Blocked by: 无 | Blocks: 5
  References: `app/ai_profile.py:40-175`（parse_profile 全文）、`app/ai_profile.py` registry `_load_snapshot`/`_validate_whitelists`（校验先例）、`app/target_registry.py:111-147`（resolve 取提取表三元组）
  Acceptance criteria: `python -m pytest test/unit/test_profile_validation.py -v` 全绿：合法候选 → 零 errors；summary_field 不在提取表字段 → errors 含 extract.summary_field 条目；多错误并发收集（fallback 不在选项 + 字段名不存在同时报）；list_fields 网络失败 → errors 含网络条目（或抛出——按现有 fail-closed 语义设计并测试锁定）；**现有 test_ai_profile_parse/registry 测试零回归（ProfileConfigError 消息 golden 逐字节不变）**。
  QA scenarios: happy = 全绿；failure = `-k "gap or missing"` 用例（提取表缺口场景）。Evidence `.omo/evidence/task-3-frontend-config-ui.txt`
  Commit: Y | Add profile candidate validation with extract-table field check

- [x] 4. 选择器端点：GET /admin/feishu/tables|fields|records + POST parse-url
  What to do / Must NOT do: `app/main.py` 新增四路由（全部逐字镜像 reload_config 鉴权：X-Admin-Token + secrets.compare_digest + CONFIG_RELOAD_TOKEN 未设 404 RELOAD_DISABLED）：`GET /admin/feishu/tables?app_token=` → `{"tables": [{table_id, name}]}`；`GET /admin/feishu/fields?app_token=&table_id=` → `{"fields": [{name, type, options: [str] | null, is_primary: bool}]}`（type 映射为飞书 ui 语义标签：single_select 时带 options）；`GET /admin/feishu/records?app_token=&table_id=&page_token=` → `{"items": [{record_id, preview}]}`（preview = is_primary 字段值，无则第一个文本字段值，均为 str 截断 80 字符）+ `has_more` + `next_page_token`；`POST /admin/feishu/parse-url` body `{"url": str}` → 服务端解析（正则 `/base\/([A-Za-z0-9]+)/` + table 参数提取；域名白名单 `*.feishu.cn`/`*.larksuite.com`；**URL 解析逻辑放后端而非 JS**——pytest 可直接测，客户端只调端点）→ `{"app_token", "table_id"}` 或 422 `{"error": "UNSUPPORTED_URL", "message": "请从浏览器地址栏复制 /base/ 链接（暂不支持 wiki 链接）"}`。FeishuClientError → 502 FEISHU_UPSTREAM_ERROR（镜像现有分支）。Must NOT：不缓存；不加 CORS；parse-url 不调用飞书 API（纯正则）。
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 9, 10
  References: `app/main.py` reload_config（鉴权模板全文）、todo 1 的两个新方法、`app/feishu_client.py list_fields`（字段结构）
  Acceptance criteria: `python -m pytest test/unit/test_picker_endpoints.py -v` 全绿：无 token 401；未设 CONFIG_RELOAD_TOKEN 404；三 GET 端点 happy（mock client 断言参数透传 + 响应形状）；records preview 取 is_primary 字段；上游错误 502；parse-url：/base/ 链接 happy 解析、wiki 链接 → 422 UNSUPPORTED_URL、非白名单域名 → 422、缺 table 参数 → 422（提示走表下拉）、无 app_token → 422。
  QA scenarios: happy = 全绿；failure = `-k "auth or upstream"`。Evidence `.omo/evidence/task-4-frontend-config-ui.txt`
  Commit: Y | Add admin picker endpoints for tables, fields, and records

- [x] 5. GET/PUT /admin/config/profile：校验→原子写→强制重载
  What to do / Must NOT do: GET → 从 registry 快照返回可编辑形状 `{prompt_header, summary_field, bill: {app_token, table_id}, fields: [8 条完整 dict]}` + `generation`；PUT body `{profile: {...}, base_generation: int}`——流程：`asyncio.Lock` 内 → generation 守卫（`ai_registry.get_status()["generation"] != base_generation` → 409 STALE_WRITE）→ 提取表三元组从 `target_registry.resolve(None, None)` 取得（**动态模式**；legacy 模式用 legacy 三元组）→ `validate_profile_candidate`（todo 3，传入三元组）→ 有 errors → 422 `{"errors": [...]}` 不写文件 → 通过 → 写 `.bak`（旧内容）→ `toml_writer.dump_profile` → tmp 文件 `target.with_suffix(".toml.tmp")`（**同目录**——os.replace 跨文件系统不原子；:ro 时 tmp 创建即抛 EROFS，被统一 409 捕获）→ `os.replace` → `await ai_registry.reload(force=True)` → 返回 `{success, generation: 新, warnings}`；`OSError` 含 EROFS/EACCES → 409 RUNTIME_READONLY（附指引文案）；AI_ENABLED=false → 404 AI_DISABLED。**测试模式注意**：conftest 钉死 legacy 模式——测试须 monkeypatch `FEISHU_TARGETS_FILE` 指向 tmp fixture（动态模式）或 stub resolve，使提取表三元组可用。Must NOT：不写 .env；不做部分 patch（全量替换）；校验失败绝不写文件。
  Parallelization: Wave 2 | Blocked by: 2, 3 | Blocks: 11
  References: `app/main.py` reload_config（鉴权+响应模式）、`app/ai_profile.py` registry（get_status/reload/get_snapshot）、todo 2/3 产出、`app/target_registry.py resolve`
  Acceptance criteria: `python -m pytest test/unit/test_config_profile_api.py -v` 全绿：GET 形状；PUT happy（断言：.bak 生成、文件内容= dump 输出、reload 被调用、返回新 generation）；校验失败 422 且文件未被修改（mtime/内容断言）；STALE_WRITE 409；:ro 模拟（monkeypatch os.replace 抛 EROFS）409；404 AI_DISABLED。
  QA scenarios: happy = 全绿；failure = `-k "readonly or stale or invalid"`。Evidence `.omo/evidence/task-5-frontend-config-ui.txt`
  Commit: Y | Add config profile read/write API with validation and atomic save

- [x] 6. GET/PUT /admin/config/targets：alias 完整 CRUD（全量替换式）
  What to do / Must NOT do: GET → `{default_alias, targets: [{alias, year, app_token, table_id, record_id, original_field_name, enabled}], generation, config_valid}`（从 target_registry 快照；**注意 generation 键名不对称：describe() 返回 `reload_generation`，AI registry get_status() 返回 `generation`——本端点守卫读 describe()["reload_generation"]，响应统一输出为 `generation` 键**）；PUT body 同形状 + `base_generation`——流程：Lock → generation 守卫 → 逐 alias 校验（复用 `_parse_target` 逻辑——重构 `_build_dynamic_snapshot` 的解析循环为模块级 `validate_targets(data) -> tuple[...]`，registry 与本端点共用）→ 错误 → 422 逐条目 errors（path 如 "targets[2].alias"）→ 通过 → .bak → dump_targets → 同目录 tmp + os.replace 原子写（同 todo 5 模式）→ `target_registry.reload(force=True)` → 返回新 generation。AI_ENABLED 无关（targets 独立于 AI）。Must NOT：不做单 alias 增删端点（CRUD 由 UI 组装全量）；不破坏 legacy 模式（FEISHU_TARGETS_FILE 未设时 GET 返回 mode=legacy + PUT 409 LEGACY_MODE 指引）。
  Parallelization: Wave 2 | Blocked by: 2 | Blocks: 9
  References: `app/target_registry.py:57-283`（registry 全文 + _parse_target + _build_dynamic_snapshot）、todo 2、reload_config 鉴权模板
  Acceptance criteria: `python -m pytest test/unit/test_config_targets_api.py -v` 全绿：GET happy（动态模式）；PUT 新增 alias + 修改 + 删除（全量替换语义断言文件内容）；alias 正则违规/年份冲突/默认项缺失 → 422 逐条；STALE_WRITE 409；legacy 模式 PUT 409；原子写 + .bak；reload 调用。
  QA scenarios: happy = 全绿；failure = `-k "invalid or legacy"`。Evidence `.omo/evidence/task-6-frontend-config-ui.txt`
  Commit: Y | Add config targets read/write API with full alias CRUD

- [x] 7. GET/PUT /admin/config/env：AI 连接编辑（env 文件行式读写）
  What to do / Must NOT do: 定位 env 文件：`settings` 增加 `env_file_path` 探测（FEISHU_ENV_FILE 值或 PROJECT_ROOT/.env，均不存在 → None）；GET → `{env_file: path|null, ai: {ai_enabled, ai_provider, ai_base_url, ai_model, ai_timeout_seconds, ai_api_key_set}, other: {feishu_app_id, feishu_app_secret_set, webhook_shared_token_set, config_reload_token_set}, restart_required: true}`——**密钥与令牌只 set/unset，永不回值**；PUT body `{ai: {...}, api_key: str|null}`（api_key 非空才写，空/null=保持不变）——行式读写：读文件逐行，匹配 `^(export )?AI_(\w+)=` 的行替换为新值（保持其余行与注释原样），文件不存在则创建只含 AI_* 段；**值引号规则**：值含 `#`、`=`、空格、`"`、`'`、`\` 任一字符时必须双引号包裹并转义（对齐 `app/config.py:36` 加载器的剥离规则——round-trip 测试钉死此契约）；写后返回 `{restart_required: true}`；env_file 为 None（环境变量直设无文件）→ PUT 409 ENV_FILE_NOT_FOUND + 指引。写入需 :ro 探测同 todo 5。Must NOT：**任何响应体不得包含密钥值**（api_key 只入不出）；不修改非 AI_* 行（FEISHU_* / WEBHOOK_* 保持原样）；不做 env 热重载承诺（显式 restart_required）。
  Parallelization: Wave 2 | Blocked by: 无 | Blocks: 11
  References: `app/config.py:12-52`（env 文件加载器——写入格式必须与之兼容：KEY=VALUE、引号剥离）、`app/config.py get_settings`（AI_* 键清单）、reload_config 鉴权模板
  Acceptance criteria: `python -m pytest test/unit/test_config_env_api.py -v` 全绿：GET 无密钥值（全文序列化断言不含 sk- 测试值与 secret 值）；PUT 行式替换保留注释与非 AI 行（tmp 文件对拍）；api_key 空=不变、非空=写入；**含特殊字符值（`#`/`=`/空格/引号）round-trip：写入→config.py 加载器语义还原**；无 env 文件 409；:ro 409；GET other 段 set/unset 正确。
  QA scenarios: happy = 全绿；failure = `-k "secret or preserve"`。Evidence `.omo/evidence/task-7-frontend-config-ui.txt`
  Commit: Y | Add admin env editing API for AI connection settings

- [x] 8. 前端骨架：静态资产整体迁移（admin.html/css/js 三文件拆分）+ 状态条 + 通用组件
  What to do / Must NOT do: **这是现有 376 行 admin.html 的整体迁移**（不只是骨架）——`app/static/` 拆为 admin.html（结构）/admin.css（迁移现有内联样式）/admin.js（迁移现有查看器+干跑逻辑）；`app/main.py` 加 `from fastapi.staticfiles import StaticFiles` + `app.mount("/admin/ai/assets", StaticFiles(directory=Path(__file__).parent / "static"), name="assets")`；GET /admin/ai 保持返回 HTML（引用 /admin/ai/assets/admin.css|admin.js）；页面骨架：Token 栏（sticky）+ 全局状态条（generation/config_valid/`:ro` 横幅/restart 提示条——数据源 `GET /admin/ai/profile` + 保存响应）+ 通用 JS 工具（fetch 封装带 X-Admin-Token、401 处理、`el(tag, attrs, text)` DOM 构造器**强制 textContent**、错误横幅渲染）+ 各分区占位卡片；**现有只读查看器与干跑测试功能完整迁移保留**为底部 §测试 区（迁移后功能不回退——pytest 锁定关键元素）。Must NOT：不引入框架/CDN（零外链）；innerHTML 仅用于静态模板常量（无插值）；迁移中不改变现有功能行为。
  Parallelization: Wave 3 | Blocked by: 4（需要选择器端点存在供骨架联调）| Blocks: 9, 10
  References: `app/static/admin.html`（现有页面——迁移基础）、todo 4-7 的 API 契约、`app/main.py` 现有 GET /admin/ai 路由
  Acceptance criteria: `python -m pytest test/unit/test_admin_page.py -v` 全绿（更新断言：三资产文件 200（curl /admin/ai/assets/admin.js → 200）、admin.html 引用资产路径、关键元素 id 存在——token-input/status-bar/现有查看器与干跑元素保留）；`curl -fsS http://localhost:2398/admin/ai | grep -q 'id="token-input"'`（启动冒烟，具体可执行断言）；页面在 AI_ENABLED=false 下静态壳可访问（HTTP 200 + grep token-input）。
  QA scenarios: happy = pytest 全绿 + curl /admin/ai/assets/admin.js 200；failure = 资产 404 用例。Evidence `.omo/evidence/task-8-frontend-config-ui.txt`
  Commit: Y | Split admin page into static assets with status bar skeleton

- [x] 9. 前端：提取表配置（alias CRUD + URL 解析 + 表/记录选择器）
  What to do / Must NOT do: admin.js 实现 §提取表：alias 卡片列表（GET /admin/config/targets 驱动；每卡：enabled 开关/alias/year 只读于卡片头、URL 输入+解析按钮（**调 POST /admin/feishu/parse-url 端点**——服务端解析，非 JS 正则；422 → 红字显示服务端 message）、表下拉（解析出 table_id 自动选中，否则 GET tables 列表选择）、记录选择器（GET records 首页 50 条 + "加载更多"按钮最多 5 页，每条 preview 文本点击选中 → record_id 回填，**textContent 渲染**）、原始信息字段下拉（GET fields）、删除按钮（默认 alias 不可删）+ 顶部"新增账本"卡（空表单同结构）；变更后"保存 targets"按钮 → PUT /admin/config/targets（带 base_generation）→ 422 逐字段红框 / 409 提示刷新重载 / 成功绿条 + 新 generation。Must NOT：preview 截断 80 字符由后端负责（前端不重复截断）；不做拖拽排序；wiki 链接错误文案来自服务端 parse-url 响应。
  Parallelization: Wave 3 | Blocked by: 4, 6, 8 | Blocks: 12
  References: todo 4/6 API 契约、todo 8 的 fetch/DOM 工具、`app/target_registry.py`（alias/year 语义）
  Acceptance criteria: `python -m pytest test/unit/test_admin_extract_flow.py -v` 全绿（URL 解析走 parse-url 端点集成测试——见 todo 4；API 调用序列集成：ASGI 测试驱动 targets GET/PUT 往返含 base_generation 守卫）；**JS 语法门**：`node --check app/static/admin.js` 退出码 0（执行环境无 node 则视为该 todo 受阻——安装 node，禁止"目检"替代）；`grep -c "function renderAliasCard\|function renderRecordPicker" app/static/admin.js` ≥ 2（函数存在性 grep 断言）。
  QA scenarios: happy = pytest + node --check + grep 断言全过；failure = parse-url 422 集成用例（wiki/无 table/非白名单域名）。Evidence `.omo/evidence/task-9-frontend-config-ui.txt`
  Commit: Y | Add extract-table config UI with alias CRUD and record picker

- [x] 10. 前端：账单表配置（字段映射 + 自动推导 + prompt_header）
  What to do / Must NOT do: admin.js 实现 §账单表：URL 解析（调 parse-url 端点，同 todo 9 复用组件）+ 表下拉（同上）→ 选表后 GET fields → **8 行映射表**渲染：每行 [ai_key 固定标签 | feishu_field 下拉（全字段）| type 下拉（5 类）| fallback 下拉（仅 type=single_select 且选中 feishu_field 后启用——选项来自该字段 options）| prompt 文本域（1 行高度可展开）]；**自动推导预填**（选表后执行一次，仅填空行）：name 含"金额"→amount+number、含"日期"→bill_date+date（多候选时含"账单"优先，否则第一个匹配）、含"分类"→category、含"类型"且含"收支"→flow_type、含"支付"/"途径"→payment_method、含"描述"→description、含"精简"/"摘要"→summary、含"原始采集"→raw_source+passthrough；feishu 字段类型为 single_select 时 spec type 预选 single_select 且 fallback 预选首选项；prompt_header 文本域（提示"必须位于所有 section 之前"由后端 dump 保证）；summary 行特殊标记（target=extract 唯一行，其 feishu_field 联动 summary_field）；§prompt 编辑区内联。所有动态文本 textContent。Must NOT：自动推导不覆盖用户已选值；不做映射行增删（**8 行固定且全部必填——含 raw_source，低置信度行默认 text 类型占位，绝不缺行**——parse_profile 与 encode_fields 契约要求完整 8 键）。
  Parallelization: Wave 3 | Blocked by: 4, 5, 8 | Blocks: 12
  References: todo 4 fields 端点契约、`app/ai_profile.py parse_profile`（校验规则——前端预校验与之对齐）、`runtime/ai-profile.toml.example`（prompt 默认文案——推导时回填）
  Acceptance criteria: `python -m pytest test/unit/test_admin_bill_flow.py -v` 全绿（推导启发式单元测试——**逻辑放在后端可测处或 Python 侧对拍实现**：给定字段列表 → 期望预填映射；API 集成：GET profile → PUT profile 往返）；`node --check app/static/admin.js` 退出 0；`grep -c "function renderBillRow\|function autoDerive" app/static/admin.js` ≥ 2；页面渲染 8 行断言（pytest 检查 admin.js 中 renderBillRow 循环常量 `AI_KEYS` 长度为 8——grep 断言）。
  QA scenarios: happy = pytest 全绿；failure = 推导冲突用例（两个"日期"字段 → 取首个含"账单"的否则第一个）。Evidence `.omo/evidence/task-10-frontend-config-ui.txt`
  Commit: Y | Add bill-table field mapping UI with auto-derive prefill

- [x] 11. 前端：AI 连接区 + 保存/校验/重载统一流
  What to do / Must NOT do: admin.js 实现：§AI 连接（GET /admin/config/env 驱动：provider 下拉（anthropic/openai）/base_url/model/timeout 输入 + API_KEY 密码框 placeholder="已设置——留空保持不变"或"未设置" + 保存 → PUT → 成功后顶部黄色横幅"AI 连接已更新，重启服务后生效"）；§保存流统一（profile/targets 各自保存按钮 + 保存后自动 `POST /admin/config/reload` 已由 PUT 内含——只刷新状态条）；422 错误渲染器：errors[].path → 定位控件（映射表第 N 行 fallback / summary_field / alias 卡）红框 + 消息；409 RUNTIME_READONLY → 红色横幅 + 文案；409 STALE_WRITE → 提示"配置已被其它修改更新，正在重新加载"+ 自动 GET 刷新表单；干跑测试区（迁移保留现有功能，测试按钮改为先检查未保存变更提示）。Must NOT：401 时不静默——弹 token 重输框；保存按钮不禁用重复点击防抖（500ms）。
  Parallelization: Wave 3 | Blocked by: 5, 7, 8 | Blocks: 12
  References: todo 5/7 API 契约、todo 9/10 的表单结构、现有 admin.html 的 401 处理
  Acceptance criteria: `python -m pytest test/unit/test_admin_save_flow.py -v` 全绿：422 逐字段路径→控件定位逻辑（Python 侧路径解析器单元测试）；env 保存后 restart 横幅状态；STALE_WRITE 刷新流程（集成）。
  QA scenarios: happy = pytest 全绿；failure = 422 路径定位失败用例（未知 path → 全局错误条兜底）。Evidence `.omo/evidence/task-11-frontend-config-ui.txt`
  Commit: Y | Add AI connection editor and unified save-validate-reload flow

- [x] 12. 集成验证 + 双语文档 + AGENTS.md
  What to do / Must NOT do: `test/unit/test_config_ui_integration.py`——ASGI 全链（mock 飞书 + mock AI）：GET targets+profile → PUT targets（新 alias）→ PUT profile（改 prompt）→ 干跑测试成功 → webhook 正常（新配置生效断言：update_record_field 调用参数用了新 summary_field）；:ro 全链（monkeypatch os.replace → PUT 409 → GET 仍旧值）；XSS 断言（记录 preview 含 `<script>` 时 API 响应为纯文本、页面渲染函数不经过 innerHTML——响应内容断言）；generation 竞态（两次 PUT 同 base_generation → 第二次 409）。文档：docs/ai-pipeline.md + docs/en/ai-pipeline.md 增「配置界面」章节（双语逐节对应：URL 格式说明/仅支持 /base/ 链接/AI 连接需重启/注释丢失与 .bak/:ro 模式说明）；README.md + docs/en/README.md 导航更新；AGENTS.md（根）STRUCTURE 加 toml_writer + static 资产说明、CODE MAP 加新符号、COMMANDS 不变；app/AGENTS.md WHERE TO LOOK + Error Taxonomy 加新 stage（list_tables/list_records）与 409 码（RUNTIME_READONLY/STALE_WRITE/ENV_FILE_NOT_FOUND）。Must NOT：中英不同步；不改与本特性无关段落。
  Parallelization: Wave 3 | Blocked by: 9, 10, 11 | Blocks: F1-F4
  References: todo 5-11 全部产出、`docs/ai-pipeline.md`（现有结构）、`AGENTS.md`/`app/AGENTS.md`（更新点）
  Acceptance criteria: `python -m pytest test/unit/test_config_ui_integration.py -v` 全绿；全量 `python -m pytest -v` 0 failed（106 存量 + 新增）；`grep -c "^## " docs/ai-pipeline.md` 较改前 +1 且英文版同步 +1（结构性断言，非短语 grep）；AGENTS.md 含 toml_writer。
  QA scenarios: happy = 上述命令逐条通过；failure = XSS 断言用例（`<script>` 注入预览 → 响应纯文本）。Evidence `.omo/evidence/task-12-frontend-config-ui.txt`
  Commit: Y | Add config UI integration tests and bilingual docs

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit
  验收（agent-executable）：`git diff feature/ai-extraction-pipeline --stat` 逐文件对照 Scope；requirements.txt 零新依赖（仍 4 项：fastapi/uvicorn/httpx/tzdata）；runtime 真实值零提交；每 todo Commit 行与实际提交一一对应。（✓ 12/12 提交匹配；偏差记录：notepad 学习文件被纳入提交 + 11 个测试文件超出计划预估 8 个——同域非阻断）
- [x] F2. Code quality review
  验收（agent-executable）：全量 `python -m pytest -v` 0 failed；`python -m py_compile app/*.py` 0 错误；新模块符合 app/AGENTS.md 约定（stage 填充/日志不打密钥/相对导入/per-call AsyncClient/XSS textContent）。（✓ 248 passed；py_compile OK；node --check OK；innerHTML 18 处全部安全——静态或 escapeHtml 防护；密钥防泄漏断言 4 处存在）
- [~] F3. Real manual QA
  **唯一人机协同门**：用户 Windows 本机打开 http://localhost:2398/admin/ai → 粘真实 /base/ URL → 选表 → 选记录 → 配置账单映射（确认自动推导）→ 保存 → 干跑测试成功 → webhook 端到端一条真实账单（含新配置生效）。
- [x] F4. Scope fidelity
  验收（agent-executable）：`grep -rniE "oauth|sqlite|sqlalchemy|celery|npm|webpack" app/*.py` 0 命中（**排除 app/static/ 与 cdn 模式**——避免 single_select 误报）；`grep -riE "https?://(cdn|unpkg|jsdelivr)" app/static/` 0 命中（外链资源专项检查）；`git diff -- requirements.txt` 为空；WebhookRequest 与 master 一致；任何 GET 端点响应不含密钥值（测试断言已有）。（✓ 全部通过）

## Commit strategy

- 每 todo 恰好一个原子提交，祈使句风格（"Add ..."），无 AI 署名尾注。
- 提交前全量 pytest 0 failed。
- 绝不提交 runtime/ 真实值（.bak 也在 runtime/ 内——已被 gitignore 覆盖）。
- 分支：继续在 `feature/ai-extraction-pipeline` 上（同一特性流的延伸）或另开 `feature/frontend-config-ui`——执行时从当前分支头部切新分支 `feature/frontend-config-ui`（保持前一特性可独立合并）。
- TDD question 确认点不产生提交。

## Success criteria

1. **回归**[agent]：全量 pytest 0 failed；`AI_ENABLED=false` webhook 5 键响应不变；requirements.txt 4 依赖不变。
2. **端到端**[human，经 F3]：Windows 本机 UI 完成：粘 URL → 选表 → 选记录 → 自动推导确认 → 保存 → 干跑成功 → 真实 webhook 走新配置；全程零手改 TOML。
3. **失败语义**[agent]：校验失败 422 逐字段且文件未动；:ro 409 明确指引；STALE_WRITE 409；密钥任何 GET 不可见。
4. **AI 连接**[human，经 F3]：UI 修改 provider/model 保存 → 重启后生效；API_KEY 留空不变。
5. **alias CRUD**[human，经 F3]：UI 新增年度账本 → webhook 用 book_alias 路由成功。
6. **文档**[agent]：双语逐节对应；wiki 链接限制与重启要求显式记载。
