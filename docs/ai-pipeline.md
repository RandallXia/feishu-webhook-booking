# AI 提取管线 / AI extraction pipeline

## 语言切换 / Language

- 中文（默认）: [本页](ai-pipeline.md)
- English: [AI extraction pipeline](en/ai-pipeline.md)

## 1. 背景 / Background

飞书多维表格原有的「AI 自动填充」自动化在每月 AI 配额耗尽后停止工作，导致 `精简原始数据` 与 `账单明细` 字段不再生成，整条记账链路断裂。

本服务在 `AI_ENABLED=true` 时接管这一步：用自托管 AI 模型对 OCR 文本做单次结构化提取，再写回飞书。配额由你自己的 AI 供应商管理，不再受飞书侧月度上限限制。

This service takes over when Feishu's built-in "AI auto-fill" automation exhausts its monthly quota and stops generating `精简原始数据` / `账单明细`. With `AI_ENABLED=true`, the service runs a single structured AI call against the OCR text and writes the result back to Feishu. AI quota is governed by your own provider, not Feishu's monthly cap.

## 2. 架构流程图 / Architecture flow

```
iPhone Shortcut OCR
        │
        ▼
POST /v1/webhook/ocr  (original_text)
        │
        ▼
[1] 鉴权 X-Webhook-Token
        │
        ▼
[2] registry.resolve(book_alias|year) → target
        │
        ▼
[3] feishu.update_original_text(text, target)        ← 写入「原始信息」字段（契约不变）
        │
        ▼
(仅 AI_ENABLED=true 时进入下面 AI 阶段)
        │
        ▼
[4] ai_registry.maybe_reload() → snapshot             ← mtime 热重载 + 白名单快照
        │
        ▼
[5] pipeline.run(text, target, profile, whitelists)
        │
        ├─ dedup 命中 → ai_status="duplicate" (跳过 AI 调用)
        │
        ├─ ai_extractor.extract(text) → ExtractionResult(7 字段)
        │
        ├─ field_codec.encode_fields(result, specs, whitelists)
        │     ├─ extract_fields  → 回写「精简原始数据」(best-effort)
        │     └─ bill_fields     → 创建「账单明细」记录(client_token 幂等)
        │
        └─ PipelineResult(ai_status, bill_record_id, warnings, extracted)
        │
        ▼
[6] 200 响应 + ai_status / ai_record_id / ai_extracted / ai_warnings
```

关键不变量：步骤 [3] 的「原始信息」写入语义完全不变；AI 阶段在它之后运行，且 `AiPipeline.run()` 永不抛出（失败也返回 200 + `ai_status="failed"`）。唯一例外是 `AiProfileRegistryUnavailableError`（profile 配置坏 / `list_fields` 失败）→ 503，避免脏的 `single_select` 选项污染飞书表。

## 3. 环境变量表 / Environment variables

所有 `AI_*` 变量在 `AI_ENABLED=false` 时全部可省略（`get_settings()` 会跳过校验）。

| 变量 | 必填条件 | 默认值 | 说明 |
|------|----------|--------|------|
| `AI_ENABLED` | — | `false` | AI 管线总开关。`true` 时下方 4 个变量变为必填 |
| `AI_PROVIDER` | `AI_ENABLED=true` | — | 协议类型，仅 `anthropic` 或 `openai` |
| `AI_BASE_URL` | 可选 | 见下 | AI 服务基址。**拼接规则见第 11 节**，配错会 404 |
| `AI_API_KEY` | `AI_ENABLED=true` | — | API 密钥。`anthropic` 走 `x-api-key` 头；`openai` 走 `Bearer` |
| `AI_MODEL` | `AI_ENABLED=true` | — | 模型名，如 `claude-3-5-sonnet-20241022` / `gpt-4o` |
| `AI_TIMEOUT_SECONDS` | 可选 | `20` | 单次 AI 调用 HTTP 超时；超时即 `AiExtractorError(stage="request")`，不重试 |
| `AI_PROFILE_FILE` | `AI_ENABLED=true` | — | 指向 `ai-profile.toml` 路径；`:ro` 挂载即可，mtime 热重载 |
| `AI_PROFILE_RELOAD_INTERVAL_SECONDS` | 可选 | `10` | profile 热重载最小间隔（mtime 检查节流） |
| `AI_DEDUP_TTL_SECONDS` | 可选 | `300` | 同一 `(alias, original_text)` 的去重窗口；命中即 `ai_status="duplicate"`，不调 AI、不建记录 |
| `AI_FORCE_TOOL_CALL` | 可选 | `true` | 是否强制发送 `tool_choice` 参数。某些 relay（如阿里云通义 thinking 模式）不支持 `tool_choice` 会返回 400，将该值设为 `false` 即可关闭 |

### `AI_BASE_URL` 拼接规则 / Concatenation rule

这是最容易配错的变量，务必按 provider 区分：

- **anthropic**：base **不含** `/v1`，服务端拼接 `{base}/v1/messages`
  - 官方默认：`https://api.anthropic.com`（留空即用此值）
  - 中转示例：`https://your-relay.example.com`（**不要**带 `/v1`）
- **openai**：base **包含** `/v1`，服务端拼接 `{base}/chat/completions`
  - 官方默认：`https://api.openai.com/v1`（留空即用此值）
  - 中转示例：`https://your-relay.example.com/v1`

详细说明见 [第 11 节](#11-ai_base_url-拼接规则--ai_base_url-concatenation-rule)。

## 4. TOML 配置 Schema / TOML config schema

模板见 [runtime/ai-profile.toml.example](../runtime/ai-profile.toml.example)。关键字段：

```toml
# prompt_header 必须放在所有 [section] 之前 —— TOML 规则：
# 段落后的裸键会归入该段，而不是顶层。
prompt_header = "你是一个账单信息提取助手。从用户提供的 OCR 文本中提取账单字段。"

[extract]
summary_field = "精简原始数据"          # 回写到原始信息记录的摘要字段名

[bill]
app_token = "bascnXXXXXXXXXXXXXXXX"   # 账单明细表所属的 Bitable app_token
table_id = "tblXXXXXXXXXXXXXXXX"       # 账单明细表 table_id（不含 record_id，每次新建记录）

# 字段定义列表，顺序即 AI 提取顺序
[[fields]]
ai_key = "summary"                     # 对应 ExtractionResult 属性名
feishu_field = "精简原始数据"           # 飞书表字段名
type = "text"                          # text | number | single_select | date | passthrough
target = "extract"                     # extract(回写摘要) | bill(新建账单记录)
prompt = "提炼这段 OCR 账单文本的精简摘要，保留商户、金额、时间、支付方式等关键信息"

[[fields]]
ai_key = "category"
feishu_field = "收支分类"
type = "single_select"
fallback = "其他"                      # single_select 必填 fallback，且必须已存在于飞书选项
target = "bill"
prompt = "账单分类，如：餐饮/交通/购物/日用/娱乐/医疗/其他"

[[fields]]
ai_key = "bill_date"
feishu_field = "账单日期"
type = "date"                          # YYYY-MM-DD 或 YYYY-MM-DD HH:mm → Asia/Shanghai 毫秒时间戳
target = "bill"
prompt = "账单发生日期，格式 YYYY-MM-DD HH:mm（带时间）；如无法判断则给出今天日期"

[[fields]]
ai_key = "raw_source"
feishu_field = "原始采集账单数据"
type = "passthrough"                   # 不经 AI，直接拷贝 extraction.summary
target = "bill"
source = "summary"                     # passthrough 必填 source="summary"
prompt = ""
```

### 字段类型说明 / Field types

| type | 行为 | 失败处理 |
|------|------|----------|
| `text` | 去空格后写入；空串跳过并记 warning | — |
| `number` | `float()` 透传 | AI 层已校验 `amount>0` |
| `single_select` | 值必须在白名单内；不在则用 `fallback` | `fallback` 也不在白名单 → `ValueError` → `ai_status="failed"` |
| `date` | `YYYY-MM-DD` 或 `YYYY-MM-DD HH:mm` → Asia/Shanghai 毫秒时间戳（带时间则保留时分，纯日期为当天 0 点） | 格式非法 → 回退为今天（上海时区）+ warning |
| `passthrough` | 直接拷贝 `extraction.summary` | `source` 非 `"summary"` → 启动期 `ProfileConfigError` |

### 解析约束 / Parse constraints

`parse_profile` 在启动期强制校验，失败即 `ProfileConfigError`（`lifespan` 不吞，服务起不来）：

- `prompt_header` 非空，且在所有 `[section]` 之前
- `[extract]` / `[bill]` 段必填；`[bill].app_token` / `table_id` 非空
- `[[fields]]` 至少一条；`ai_key` 唯一
- `single_select` 必须有 `fallback`；`passthrough` 必须有 `source="summary"`
- **恰好一条** `target="extract"`，且其 `feishu_field` 必须等于 `[extract].summary_field`

### 白名单校验 / Whitelist validation

`AiProfileRegistry._load_snapshot` 在加载 profile 后会调用 `feishu.list_fields(bill_app_token, bill_table_id)` 拉取真实选项，并对每个 `single_select` 字段校验：

- 飞书表里该字段存在
- 选项列表非空
- profile 里的 `fallback` 必须在选项列表内

任一不满足 → `config_valid=False` → 路由 503 `AI_PROFILE_UNAVAILABLE`。这是防污染底线：避免 fallback 本身就是脏选项，导致飞书自动创建不可逆的脏选项。

## 5. Webhook 响应字段 / Webhook response fields

`AI_ENABLED=true` 时，`POST /v1/webhook/ocr` 的 200 响应在原有 5 字段基础上多出 4 个 `ai_*` 字段（`response_model_exclude_none=True` 保证 `AI_ENABLED=false` 时响应字节级不变）：

| 字段 | 类型 | 出现条件 | 说明 |
|------|------|----------|------|
| `ai_status` | `str` | AI 阶段运行过 | `succeeded` / `failed` / `duplicate` |
| `ai_record_id` | `str\|null` | succeeded | 新建的「账单明细」记录 ID |
| `ai_warnings` | `list[str]\|null` | 非空时 | 编码过程中的非致命问题（空文本跳过、选项回退、日期回退、摘要回写失败） |
| `ai_extracted` | `dict\|null` | succeeded | `{amount, category, flow_type, description}`，供 Shortcut 通知使用 |

### Shortcut 通知示例 / Shortcut notification

`ai_extracted` 专为 iPhone Shortcut 通知设计。响应示例：

```json
{
  "success": true,
  "request_id": "req_abc123",
  "record_id": "recXXXX",
  "book_alias": "2026",
  "message": "configured record updated and 原始信息 updated",
  "ai_status": "succeeded",
  "ai_record_id": "recYYYYYYYY",
  "ai_extracted": {
    "amount": 42.0,
    "category": "餐饮",
    "flow_type": "支出",
    "description": "星巴克咖啡"
  }
}
```

Shortcut 可用如下文本模板生成「¥42 餐饮 已入账」通知：

```
¥{{ai_extracted.amount}} {{ai_extracted.category}} 已入账
```

`ai_status="failed"` 时 `ai_extracted` 为 `null`，Shortcut 应分支提示「AI 提取失败，原始信息已保存」。

## 6. 管理端点 / Admin endpoints

三个 AI 管理端点均要求 `CONFIG_RELOAD_TOKEN`（未配置时直接 404，不暴露端点存在性）。鉴权头为 `X-Admin-Token`。

### `POST /admin/ai/test` — dry-run 提取

不写飞书，只跑一次 `AiExtractor.extract` + `encode_fields`，返回编码结果用于调 prompt。

请求体：

```json
{ "text": "粘贴一段 OCR 文本做测试" }
```

响应（成功）：

```json
{
  "ai_status": "succeeded",
  "extracted": { "summary": "...", "description": "...", "amount": 42.0, ... },
  "bill_fields": { "金额": 42.0, "收支分类": "餐饮", ... },
  "summary_writeback": { "field": "精简原始数据", "value": "..." },
  "warnings": []
}
```

响应（AI 失败，仍 200）：

```json
{ "ai_status": "failed", "error": "Anthropic returned HTTP 401" }
```

`AiProfileRegistryUnavailableError`（profile 坏）→ 503，与 webhook 路由一致。

### `GET /admin/ai/profile` — profile 巡检

返回当前 profile + 白名单 + registry 状态。fail-closed 时仍 200（`profile` / `whitelists` 为 `null`，但 `registry.config_valid=false` + `last_reload_error` 透出，便于排障）。

### `GET /admin/ai` — 配置页

返回 `app/static/admin.html` 的 HTML 页面，内嵌调上述两个端点的可视化界面。无需鉴权即可访问页面本身（页面内 JS 调 API 时仍需 `X-Admin-Token`）。

## 7. 配置页使用 / Config page usage

1. 浏览器打开 `http://<host>:2398/admin/ai`
2. 在页面顶部填入 `CONFIG_RELOAD_TOKEN`（首次部署时页面只显示「初始化设置」引导卡，见第 12 节）
3. 页面会调 `GET /admin/ai/profile` 渲染当前 profile 字段映射 + 白名单 + registry generation
4. 在「Dry run」输入框粘贴 OCR 文本，点测试 → 调 `POST /admin/ai/test`，下方显示提取的 7 字段 + 编码后的 `bill_fields` + warnings
5. 改完 `ai-profile.toml` 后点「Reload」→ 调 `POST /admin/config/reload`，强制刷新 registry 快照

用途：迭代 prompt 时无需触发真实 webhook，也无需手写 curl。

## 8. 迁移清单 / Migration checklist

> ⚠️ 这是切换到自托管 AI 管线前**必须**执行的步骤，漏一步可能导致重复记账。

### 切换前 / Before switching

- [ ] **关闭飞书侧「AI 自动填充」自动化**：在多维表格「自动化」入口找到原「AI 自动填充」流程，禁用
- [ ] **关闭两条 AI 自动化流程**：原链路里依赖飞书 AI 的两个 automation（生成 `精简原始数据`、生成 `账单明细`），全部禁用
- [ ] 在飞书「账单明细」表里确认 `single_select` 字段（`收支类型` / `收支分类` / `支付途径`）的选项已创建，且与 `ai-profile.toml` 里的 `fallback` 一致
- [ ] 准备 AI 供应商凭据（`AI_API_KEY` / `AI_MODEL` / `AI_BASE_URL`）
- [ ] 拷贝 `runtime/ai-profile.toml.example` → `runtime/ai-profile.toml`，填入真实 `app_token` / `table_id`
- [ ] 在 `runtime/feishu-webhook.env` 设置 `AI_ENABLED=true` 及其余 `AI_*` 变量
- [ ] 重启服务，确认日志无 `ProfileConfigError`，`GET /admin/ai/profile` 返回 200 且 `registry.config_valid=true`
- [ ] 用 `POST /admin/ai/test` 跑 1-2 段真实 OCR 文本，确认 `bill_fields` 字段值合理

### 切换后 / After switching

- [ ] 发一条真实 webhook，确认「账单明细」表新增记录，`精简原始数据` 已回写
- [ ] 确认 Shortcut 收到的 `ai_extracted` 字段可用于通知

### 风险：飞书 AI 配额每月重置 / Risk: monthly quota reset

飞书 AI 配额按月刷新。**若旧自动化未被禁用**，下个月初配额恢复后，旧自动化会重新触发，与自托管 AI 管线**同时**写入 → 重复记账。

建议每月初检查一次飞书自动化列表，确认旧流程仍处于禁用状态。

### 已知限制：账单明细表「记账日期」公式 / Known limitation: bill table formula

账单明细表的「记账日期」字段应由公式生成（`TODATE(账单日期)`），本服务不写该字段。**请在飞书侧手动确认该公式存在并引用「账单日期」字段**。若公式缺失，`记账日期` 列将为空 —— 这是飞书侧配置问题，非本服务 bug，请按飞书公式编辑器自行补建。

## 9. 多维表格 URL 取 key 说明 / Bitable URL key extraction

飞书多维表格的浏览器 URL 形如：

```
https://xxx.feishu.cn/base/{app_token}?table={table_id}&view=...
```

- `app_token`：URL 路径中 `/base/` 后面那一段
- `table_id`：query 参数 `table=` 的值

填入 `ai-profile.toml` 的 `[bill]` 段即可。

**注意**：飞书的「记录分享链接」使用 opaque token，**无法**从分享 URL 解析出 `record_id`。账单明细表每次都是 `create_record` 新建，不需要 `record_id`；若需要某条记录的 `record_id`，请从浏览器地址栏或飞书 API 获取。

## 10. 白名单刷新操作 / Whitelist refresh

`AiProfileRegistry` 在加载快照时调用 `feishu.list_fields` 拉取 `single_select` 选项列表，之后**不会自动感知**飞书 UI 侧的新增选项。

在飞书多维表格里给 `收支类型` / `收支分类` / `支付途径` 新增选项后，必须触发一次 profile 重载才能让新选项进入白名单，否则 AI 提取到的新值会一直命中 `fallback`。两种触发方式：

1. **手动 API**：`POST /admin/config/reload`（带 `X-Admin-Token`），强制刷新 registry 快照
2. **改 TOML**：修改 `ai-profile.toml` 的 mtime（哪怕只加一个空格再保存），下次请求的 `maybe_reload()` 会自动重载

未刷新的征兆：日志反复出现 `option fallback: 收支分类: 'xxx' -> '其他'` 类 warning。

## 11. `AI_BASE_URL` 拼接规则 / `AI_BASE_URL` concatenation rule

服务端按 provider 硬编码拼接路径，中转/代理端点最容易在这里配错：

| provider | base 是否含 `/v1` | 服务端拼接 | 官方默认（留空时） |
|----------|-------------------|-----------|---------------------|
| `anthropic` | **不含** | `{base}/v1/messages` | `https://api.anthropic.com` |
| `openai` | **包含** | `{base}/chat/completions` | `https://api.openai.com/v1` |

### 常见错误 / Common mistakes

- anthropic 中转写成 `https://relay.example.com/v1` → 实际请求 `https://relay.example.com/v1/v1/messages` → 404
- openai 中转写成 `https://relay.example.com`（漏 `/v1`）→ 实际请求 `https://relay.example.com/chat/completions` → 多数中转会 404

### 判断方法 / How to verify

配好后调一次 `POST /admin/ai/test`：

- 返回 `ai_status="succeeded"` → 拼接正确
- 返回 `ai_status="failed"` 且 `error` 含 `HTTP 404` → 99% 是 base URL 拼接错误

## 12. 配置界面 / Config UI

`/admin/ai` 页面按卡片组织：管理令牌、配置概览、AI 连接、提取表配置、账单表配置、提示词测试（dry-run）、OCR 流程测试。除第 7 节的 dry-run + 手动 reload 外，还提供可视化的 targets / profile / env 编辑能力。本节描述该界面的使用约定与限制。

The `/admin/ai` page is organized as cards: admin token, profile overview, AI connection, extract-table config, bill-table config, prompt tester (dry-run), and OCR flow tester. Beyond the dry-run + manual reload in section 7, it also provides visual editing of targets / profile / env. This section covers the UI's usage contracts and limits.

### 首次部署引导 / First-run onboarding

首次打开页面时（浏览器 localStorage 里没有任何管理令牌），UI 不显示报错，而是只渲染一张「初始化设置」引导卡，其余卡片全部隐藏：

1. 先在运行环境的 env 文件里设置 `CONFIG_RELOAD_TOKEN`（服务侧操作）
2. 把令牌值填入引导卡，点「开始使用」
3. 令牌存入 localStorage 并自动刷新页面 → 进入正常管理界面

令牌只保存在本机浏览器的 localStorage。已保存的令牌失效时（任一 API 返回 401），UI 会清掉它并重新显示引导卡，提示「令牌不正确，请重新输入」。

On first open (no admin token in the browser's localStorage yet), the UI shows a single "Initial Setup" onboarding card instead of errors; all other cards stay hidden:

1. Set `CONFIG_RELOAD_TOKEN` in the runtime env file first (done on the service side)
2. Enter the token value in the card and click "Get Started"
3. The token is stored in localStorage and the page reloads into the normal admin UI

The token lives only in the browser's localStorage. If a stored token becomes invalid (any API returns 401), the UI clears it and shows the setup card again with an "invalid token, please re-enter" hint.

### URL 格式 / URL format

界面只支持多维表格的**直链**：

```
https://xxx.feishu.cn/base/{app_token}?table={table_id}&view=...
```

- `app_token`：URL 路径中 `/base/` 后面那一段
- `table_id`：query 参数 `table=` 的值

从浏览器地址栏复制即可。**wiki 链接不支持**（路径里没有 `/base/`），粘贴后 UI 会提示 `UNSUPPORTED_URL`；请从多维表格本身进入并复制地址栏 URL。

Only direct `/base/{app_token}?table={table_id}` links are supported (copy from the browser address bar). Wiki links are rejected with `UNSUPPORTED_URL` — open the bitable itself and copy its address-bar URL.

### 配置流程简述 / Config flow

配置按「提取表」与「账单表」两张卡片拆分，各自独立保存：

**提取表配置 / Extract table config**

1. 账本别名增删改：每条 alias 一行，行内走 URL 解析 → 选表 → 记录选择器（`GET /admin/feishu/records` 分页拉取，每条带 `preview`）
2. 「保存全部账本」→ `PUT /admin/config/targets`（校验 + 原子写 + 热重载）
3. 「摘要字段」下拉：从默认账本对应提取表的真实字段列表填充（`GET /admin/feishu/fields`）；profile 未指定时按字段名含「精简/摘要」自动预选。这是唯一 `target=extract` 映射的编辑入口（保存随账单表卡片的「保存配置」一并写入 profile）

**账单表配置 / Bill table config**

1. 粘贴 URL → `POST /admin/feishu/parse-url` 解析出 `app_token` + `table_id`
2. 选表 → `GET /admin/feishu/tables` 列出该 app 下的表
3. 字段列表 → `GET /admin/feishu/fields` 拉取该表真实字段，渲染表驱动映射（见下节）
4. 「保存配置」→ 校验 + 原子写（tmp + `os.replace`）+ 热重载（registry.reload）

Config is split across two cards ("extract table" and "bill table"), each saved independently:

**Extract table config**

1. Alias CRUD: one row per alias; each row runs URL parse → table pick → record picker (`GET /admin/feishu/records`, paginated, each with a `preview`)
2. "Save All" → `PUT /admin/config/targets` (validate + atomic write + hot reload)
3. "Summary Field" dropdown: populated from the real field list of the default alias's extract table (`GET /admin/feishu/fields`); auto-prefilled by the 精简/摘要 name heuristic when the profile has none. This is the only editor for the `target=extract` mapping (persisted with the bill card's "Save Profile")

**Bill table config**

1. Paste URL → `POST /admin/feishu/parse-url` extracts `app_token` + `table_id`
2. Pick table → `GET /admin/feishu/tables` lists tables under the app
3. Field list → `GET /admin/feishu/fields` fetches the table's real fields and renders the table-driven mapping (below)
4. "Save Profile" → validate + atomic write (tmp + `os.replace`) + hot reload (registry.reload)

### 表驱动字段映射（反向映射）/ Table-driven field mapping (reverse mapping)

账单字段映射不再以固定的 AI 键为行，而是**以飞书字段为行**（反向映射）：

- 选定账单表后，UI 拉取该表真实字段列表，每个字段渲染一行：只读字段名 + 飞书类型标签
- 每行一个「AI 键映射」下拉：选择该字段映射到哪个提取键（`description` / `flow_type` / `amount` / `category` / `payment_method` / `bill_date` / `raw_source`），或「跳过」；跳过的行不会写入保存的 profile
- 提取表的 summary 字段不在此表中——已分离到「提取表配置」区的「摘要字段」下拉（见上）

Bill-field mapping is no longer a fixed list of AI-key rows; rows are now **real Feishu fields** (reverse mapping):

- After a bill table is selected, the UI fetches its real field list and renders one row per field: read-only field name + Feishu type tag
- Each row carries an "AI key" dropdown: pick which extraction key this field maps to (`description` / `flow_type` / `amount` / `category` / `payment_method` / `bill_date` / `raw_source`) or "Skip"; skipped rows are dropped from the saved profile
- The extract table's summary field is not in this table — it moved to the "Summary Field" dropdown in the extract-table config card (above)

其余列自动预填，均可手动覆盖：

| 列 | 自动预填规则 |
|----|--------------|
| 类型 | 字段带选项或飞书类型 `single_select` → `single_select`；`number` → `number`；`date` / `datetime` → `date`；其余 → `text` |
| 回退 | `single_select` 默认取该字段真实选项的第一个；非 single_select 不可用 |
| 启用 | 默认开启（语义见第 13 节） |

Remaining columns are auto-prefilled and can be overridden:

| Column | Auto-prefill rule |
|--------|-------------------|
| Type | Field has options or Feishu type `single_select` → `single_select`; `number` → `number`; `date` / `datetime` → `date`; otherwise `text` |
| Fallback | For `single_select`, defaults to the field's first real option; N/A for other types |
| Enable | On by default (semantics in section 13) |

**自动推导**：字段列表加载时（以及点「重新推导」时）按字段名关键词自动建立映射——含「金额」→ `amount`（number）；含「日期」→ `bill_date`（「账单日期」优先于普通「日期」）；含「分类」→ `category`；同时含「收支」「类型」→ `flow_type`；含「支付」或「途径」→ `payment_method`；含「描述」→ `description`；含「原始采集」→ `raw_source`（passthrough）。

**匹配芯片**：每行字段名旁的芯片标记映射来源——`✓ 自动匹配`（自动推导命中）、`● 手动`（用户手动改过下拉）、`○ 跳过`（未映射）。表头上方显示「已匹配 N 字段」；点「重新推导」放弃手动修改、按关键词重新推导；手动改过任何一个下拉都会把该行芯片变为「● 手动」。

**Auto-derive**: when the field list loads (and when "Re-derive" is clicked), the UI builds a mapping from field-name keywords — 含「金额」→ `amount` (number); 含「日期」→ `bill_date` (账单日期 beats a plain 日期); 含「分类」→ `category`; 收支+类型 → `flow_type`; 支付 or 途径 → `payment_method`; 含「描述」→ `description`; 含「原始采集」→ `raw_source` (passthrough).

**Match chips**: the chip next to each field name marks where the mapping came from — `✓ 自动匹配` (auto, keyword-derived), `● 手动` (manual, user touched the dropdown), `○ 跳过` (skip, unmapped). A "matched N fields" count sits above the table; "Re-derive" discards manual edits and re-runs the keyword pass; touching any dropdown demotes that row's chip to manual.

保存时（`PUT /admin/config/profile`）：跳过的行被丢弃；summary 行由「摘要字段」下拉的值自动重建（`type=passthrough`、`source=summary`、常开）；`prompt_header` 取表格下方的全局提示词输入框。

On save (`PUT /admin/config/profile`): skipped rows are dropped; the summary spec is rebuilt from the "Summary Field" dropdown (`type=passthrough`, `source=summary`, always enabled); `prompt_header` comes from the prompt-header box below the table.

### OCR 流程测试 / OCR flow tester

「OCR 流程测试」卡片直接调用**真实 webhook 路径** `POST /v1/webhook/ocr`：输入 OCR 文本 + `X-Webhook-Token`（与 iPhone Shortcut 中配置的一致，单独保存在本机 localStorage），点「发送」，请求体为 `{original_text, source: "admin-ocr-flow-test"}`。

与第 7 节的 dry-run 不同，这条链路是**真实写入**：写「原始信息」→ AI 提取 → 写「精简原始数据」→ 创建「账单明细」记录。同一文本在 `AI_DEDUP_TTL_SECONDS`（默认 300s）内重发会命中去重（`ai_status="duplicate"`），不会重复创建账单记录。

结果区展示：

- HTTP 状态标签（200 绿色 / 其他红色）+ 顶部横幅结论：`ai_status="succeeded"` → 「流程成功，请到飞书账本查看新纪录」；`failed` → 「原始信息已写入，但 AI 阶段失败」；非 200 → HTTP 状态 + 错误消息
- 响应字段逐行渲染：`success` / `request_id` / `record_id (原始信息)` / `book_alias` / `ai_status`（✓/✗/⏭ 前缀）/ `ai_record_id (账单明细)` / `ai_extracted` 各键 / `ai_warnings` 各条
- 可折叠的「原始 JSON / Raw JSON」

The "OCR Flow Tester" card calls the **real webhook path** `POST /v1/webhook/ocr`: enter OCR text + the `X-Webhook-Token` (the same token configured in the iPhone Shortcut, stored separately in the browser's localStorage) and click Send. The body is `{original_text, source: "admin-ocr-flow-test"}`.

Unlike the dry-run in section 7, this is a **real write**: it writes 原始信息, runs AI extraction, writes 精简原始数据, and creates a 账单明细 record. Resending the same text within `AI_DEDUP_TTL_SECONDS` (default 300s) hits dedup (`ai_status="duplicate"`) and does not create a second bill record.

The result panel shows:

- An HTTP status tag (green for 200, red otherwise) + a banner verdict: `ai_status="succeeded"` → "flow succeeded, check Feishu for the new record"; `failed` → "原始信息 written but AI stage failed"; non-200 → HTTP status + error message
- Response fields rendered line by line: `success` / `request_id` / `record_id (原始信息)` / `book_alias` / `ai_status` (✓/✗/⏭ prefix) / `ai_record_id (账单明细)` / each `ai_extracted` key / each `ai_warnings` entry
- A collapsible "Raw JSON" block

### AI 连接 / AI connection

UI 可编辑 `AI_PROVIDER` / `AI_MODEL` / `AI_BASE_URL` / `AI_TIMEOUT_SECONDS` / `AI_API_KEY`。`AI_API_KEY` 是 **write-only**：GET 永远只返回 `ai_api_key_set` 布尔值，不返回密钥本身。

The UI can edit `AI_PROVIDER` / `AI_MODEL` / `AI_BASE_URL` / `AI_TIMEOUT_SECONDS` / `AI_API_KEY`. `AI_API_KEY` is **write-only**: GET ever returns only the `ai_api_key_set` boolean, never the key value.

> ⚠️ **保存后需重启服务生效**：env 是启动时加载进 `Settings`（frozen dataclass），PUT 只改文件、不刷新运行时内存。GET 反映的是运行中的 `Settings`，而非刚写入的文件——故 GET 的 `restart_required` 恒为 `true`。
>
> ⚠️ **Restart required after save**: env is loaded into `Settings` (a frozen dataclass) at startup; PUT only edits the file, it does NOT refresh in-memory state. GET reflects the running `Settings`, not the just-written file — hence GET's `restart_required` is always `true`.

### TOML 注释丢失 / TOML comment loss

UI 保存会**重写整个 TOML 文件**（经 `app/toml_writer.py` 的确定性序列化），文件里手写的注释会丢失。每次保存前自动备份为 `<file>.toml.bak`（保留上一版内容）。`.example` 模板文件**永不被写入**——文件名不同，UI 只写 `runtime/feishu-targets.toml` / `runtime/ai-profile.toml`。

A UI save **rewrites the whole TOML file** (via `app/toml_writer.py`'s deterministic serializer); hand-written comments are lost. A `.bak` backup (`<file>.toml.bak`) is auto-created before every save (holding the previous version). The `.example` templates are **never written** — different filename; the UI only writes `runtime/feishu-targets.toml` / `runtime/ai-profile.toml`.

### Docker `:ro` 模式 / Docker read-only mount

当 `runtime/` 卷以只读方式挂载时（`docker-compose.yml` 默认 `./runtime:/runtime:ro`），`os.replace` 会抛 `PermissionError`，保存按钮返回 `409 RUNTIME_READONLY`，UI 显示红色横幅，提示在宿主机编辑文件后调 `POST /admin/config/reload`。原文件不会被破坏（tmp + replace 是原子交换，replace 失败时原文件字节不变）。

When the `runtime/` volume is mounted read-only (`docker-compose.yml` defaults to `./runtime:/runtime:ro`), `os.replace` raises `PermissionError`, the save button returns `409 RUNTIME_READONLY`, and the UI shows a red banner prompting a host-side edit followed by `POST /admin/config/reload`. The original file is NOT corrupted (tmp + replace is an atomic swap; a failed replace leaves the original byte-identical).

### 新增 admin 端点清单 / New admin endpoints

- `GET /admin/feishu/tables` — 列出指定 app_token 下的多维表格
- `GET /admin/feishu/fields` — 列出指定表的字段元信息（含类型 + is_primary）
- `GET /admin/feishu/records` — 分页列出记录，每条带 `preview`（is_primary 字段值，截断 80 字符）
- `POST /admin/feishu/parse-url` — 解析 `/base/{app_token}?table={table_id}` 直链，wiki 链接 → 422 `UNSUPPORTED_URL`
- `GET /admin/config/profile` — 读取可编辑 profile 形状（fail-closed 时降级 200 + profile=null）
- `PUT /admin/config/profile` — 校验 + 原子写 + 热重载（base_generation 守卫，并发 → 409 `STALE_WRITE`）
- `GET /admin/config/targets` — 读取 targets 快照（legacy 模式返回单条；fail-closed 降级 200 + targets=null）
- `PUT /admin/config/targets` — 全量替换写 + 热重载（legacy 模式 → 409 `LEGACY_MODE`）
- `GET /admin/config/env` — 读取 AI 连接设置（密钥只返回 `*_set` 布尔，永不返回值）
- `PUT /admin/config/env` — 按行编辑 env 文件（保留注释 + 非 AI 行；无文件 → 409 `ENV_FILE_NOT_FOUND`）

## 13. 字段启用开关 / Field enable toggle

每个 `[[fields]]` 行支持一个可选的 `enabled` 布尔键，用于在配置界面里单独控制某个字段是否写入飞书。这是纯展示层的开关——不影响 AI 提取的固定 schema，只影响结果是否落地。

Each `[[fields]]` entry supports an optional `enabled` boolean key that controls, per field from the config UI, whether its value is written to Feishu. This is a presentation-layer toggle only — it does not change the AI extraction's fixed schema, only whether the result is persisted.

### 语义 / Semantics

- **禁用 ≠ 停止提取**：AI 模型仍然按固定 schema 提取全部 7 字段（保证 schema 稳定、便于重新启用），但被禁用字段的提取结果在 `encode_fields` 阶段被丢弃，不会进入 `extract_fields` / `bill_fields`，因此不会写入飞书。重新启用即恢复写入，无需重跑 AI。
- **summary 行（`target="extract"`）**：核心写回字段（回写「精简原始数据」），UI 硬锁定为常开、不可禁用。后端仍尊重 `enabled=false`（防御性：万一通过 TOML 直改禁用，写回分支会跳过，但账单记录创建不受影响）。
- **全禁用**：当所有 `target="bill"` 字段都被禁用时，`bill_fields` 为空，pipeline 跳过 `create_record`（避免创建一条只有 `client_token` 的空白记录），并附一条 `all bill fields disabled — record not created` warning。`ai_status` 仍为 `succeeded`（提取本身成功）。
- **响应过滤**：webhook 响应里的 `ai_extracted`（4 键：amount/category/flow_type/description）也会按 `enabled` 过滤——禁用字段的值不返回，避免 Shortcut 通知显示用户已关闭的值。

- **Disabled ≠ not extracted**: the AI model still extracts all 7 fields per the fixed schema (keeping the schema stable so re-enabling is cheap), but a disabled field's extracted value is dropped at the `encode_fields` stage — it never enters `extract_fields` / `bill_fields`, so it is never written to Feishu. Re-enabling restores the write without re-running AI.
- **summary row (`target="extract"`)**: the core writeback field (writes back `精简原始数据`); the UI hard-locks it always-on and disables the toggle. The backend still respects `enabled=false` defensively (if disabled via raw TOML, the writeback branch is skipped, but bill-record creation is unaffected).
- **All disabled**: when every `target="bill"` field is disabled, `bill_fields` is empty, and the pipeline skips `create_record` (avoiding a blank record with only the `client_token` idempotency guard), appending an `all bill fields disabled — record not created` warning. `ai_status` stays `succeeded` (the extraction itself succeeded).
- **Response filtering**: the webhook response's `ai_extracted` (4 keys: amount/category/flow_type/description) is also filtered by `enabled` — a disabled field's value is not returned, so the Shortcut notification does not show a value the user turned off.

### TOML 形态 / TOML shape

```toml
[[fields]]
ai_key = "category"
feishu_field = "收支分类"
type = "single_select"
fallback = "其他"
target = "bill"
prompt = "账单分类，如：餐饮/交通/购物/日用/娱乐/医疗/其他"
enabled = false                  # 禁用此字段：AI 仍提取但不写入飞书（缺省 = true）
```

`enabled` 键缺省为 `true`，因此存量 TOML 配置（无此键）行为零变化——升级后无需迁移。配置界面保存时会经 `dump_profile` 序列化写出该键（无论 true/false 都显式写出，便于界面回显）。
