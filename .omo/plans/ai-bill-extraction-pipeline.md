# ai-bill-extraction-pipeline - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** 记账快捷指令发来的截图文字，服务会自动用你自己的 AI 接口提取出账单字段（消费描述、金额、分类、支付方式、日期等），并直接在飞书账本里创建完整的账单记录——不再依赖飞书的 AI 额度。另外附带一个网页配置面板和调试接口，方便调整提示词和检查提取效果。

**Why this approach:** 飞书 AI 配额用尽导致记账自动化断链；自建提取管线用你已有的 AI 接口完全接管提取工作，一次调用提取全部字段（快、省、字段间一致），并且从第一笔账单起就防止 AI 的"自由发挥"弄脏账本的分类选项（未知分类自动回退到默认值，绝不新建选项）。

**What it will NOT do:** 不做扫码登录（现有飞书应用凭据已够用，扫码纯增加成本）；不新增任何运行时依赖、不引入数据库；关闭总开关时，快捷指令的请求格式和返回结果与现在完全一致，随时可退回旧路线。

**Effort:** Medium
**Risk:** Medium - AI 输出不可控（通过选项白名单 + 回退默认值 + 失败不重试来兜底）
**Decisions to sanity-check:** ① 总开关默认关闭，确认新管线工作正常后再切换，且切换前需手动停用飞书侧旧 AI 自动化（否则配额每月重置后会双重记账）；② AI 提取失败时接口仍返回成功状态（原始文字已保存，返回错误反而会诱发快捷指令重试造成重复账单）；③ 单选字段未知值一律回退默认值并记录警告，绝不写入新选项。

Your next move: 批准后运行 `$start-work`（可加 `--make-pr` 以 PR 交付）。

---

> TL;DR (machine): Medium effort / Medium risk — 自建 AI 账单提取管线（双协议单次结构化调用 + TOML 热重载 + 选项污染防护 + TTL 去重 + 只读配置页），12 个实现 todo + 4 项最终验证；requirements.txt 保持 3 依赖。

## Scope
### Must have

1. **AI 提取管线**（`app/ai_extractor.py`、`app/field_codec.py`、`app/pipeline.py`）：webhook 写入原始信息后，单次结构化 AI 调用（anthropic tool_use / openai function calling 双协议）提取 7 个字段（精简摘要、消费描述、收支类型、金额、收支分类、支付途径、账单日期），写回精简数据到 AI 自动提取表的 record，并在账单明细表创建新记录（含原始采集账单数据 = 精简摘要）。
2. **FeishuClient 扩展**（`app/feishu_client.py`）：`create_record`（带 `client_token` 幂等）、`list_fields`（字段选项白名单数据源）、`update_record_field`（泛化字段写回，`update_original_text` 委托之）。
3. **配置面**：`app/config.py` 新增 9 个 `AI_*` env 项（连接配置）；`app/ai_profile.py` TOML 提取配置（字段映射 + per-field prompt + 单选回退值）+ mtime 热重载注册表（克隆 `TargetRegistry` 模式）；`runtime/ai-profile.toml.example` 模板。
4. **开关与失败语义**：`AI_ENABLED` env 全局开关（默认 false → 行为与 master 完全一致）；配置无效 → 503 fail-closed；AI 阶段失败 → 200 + `ai_status:"failed"`；去重：内存 TTL hash（仅记成功）+ 飞书 `client_token` 幂等后备。
5. **管理与调试面**：`POST /admin/ai/test` 干跑端点（不写飞书）、`GET /admin/ai/profile` 配置查看端点（均 `X-Admin-Token` 门控）、`GET /admin/ai` 只读配置页（vanilla HTML/JS 单文件，无构建）。
6. **响应扩展**：webhook 成功响应追加 `ai_status` / `ai_record_id` / `ai_extracted`（金额+分类+类型+描述，供 Shortcut 弹通知）/ `ai_warnings`；开关关闭时 JSON 与现状逐键一致（`response_model_exclude_none`）。
7. **测试基建**：`requirements-dev.txt`（pytest 系 dev 依赖）、`.gitignore` 解封 `test/`、TDD 全程（Gherkin 骨架先行 → 用户确认 → 正式测试 → 实现），测试位于 `test/unit/`。
8. **文档与迁移**：`docs/ai-pipeline.md` + `docs/en/ai-pipeline.md`（双语镜像）、README（中英）段落、**停用旧飞书 AI 自动化迁移清单**（配额月重置 → 双重写入风险）、多维表格 URL 取 key 说明；`AGENTS.md`（根 + app/）反模式条款重写为有界新契约。

### Must NOT have (guardrails, anti-slop, scope boundaries)

- **无 OAuth / 扫码登录 / user_access_token**——事实核查证实 tenant_token 已覆盖全部所需 API。
- **`requirements.txt` 保持 3 依赖不动**——AI 调用用现有 httpx 裸写；TOML 用标准库 tomllib；时区用标准库 zoneinfo。
- **无 DB / sqlite / 队列 / 任何持久化状态**——去重是内存 TTL dict，重启即清空（可接受）。
- **不逐字段独立 AI 调用**——单次结构化调用返回全部字段。
- **不加 webhook 请求体字段**——`WebhookRequest` 保持 `extra="forbid"` 原样；AI 开关只走 env。
- **配置页不写回任何文件**——runtime 卷保持 `:ro`；页面只读 + 干跑。
- **不处理记账日期字段**——飞书公式从账单日期（TODATE）派生。
- **不提交真实 runtime 值**——只提交 `*.example`。
- **不解析 record 分享链接**——不透明 token 解析不出 record_id（事实核查结论），文档说明手动获取方式。
- **AI 阶段失败不得返回非 2xx**——否则 Shortcut 自动重试 → 重复账单行。
- **未命中白名单的单选值绝不裸写飞书**——未知选项会被飞书自动创建，永久污染账本枚举。

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: **TDD**（仓库主人硬性约定：每个编码 todo 先写 Gherkin Given-When-Then 行为注释骨架 + 空测试函数 → 用 question 工具向用户确认 → 确认后才写正式测试代码与实现）+ 框架 pytest + pytest-asyncio（`asyncio_mode=auto`）+ `asgi-lifespan`（ASGI 测试的 lifespan 管理）；外部依赖全部 mock：飞书 API 与 AI API 用 `httpx.MockTransport`（handler 签名 `def handler(request: httpx.Request) -> httpx.Response`，在 handler 内断言 `request.url.path` / `request.headers` / `json.loads(request.content)`，返回 `httpx.Response(200, json={...})`），路由层用 `httpx.AsyncClient(transport=ASGITransport(app=app))` + `async with LifespanManager(app):`——**ASGITransport 默认不触发 lifespan**，而 `app.state` 装配依赖 lifespan，不套 LifespanManager 则 `app.state.feishu_client` 等不存在。
- 测试隔离两条铁律：① conftest 模块顶层（任何 `app.*` import 之前）`os.environ["FEISHU_TARGETS_FILE"] = ""`、`os.environ["FEISHU_ENV_FILE"] = ""`、`os.environ["AI_ENABLED"] = "false"`、`os.environ["AI_PROFILE_FILE"] = ""`——钉死 legacy 模式 + 屏蔽运行时 env 文件 + **中和根 `.env` 的 AI 变量泄漏**（`app/config.py:52` 无条件加载根 `.env`、`config.py:39` setdefault 不覆盖已存在值：开发者本地 `AI_ENABLED=true` 的 `.env` 若不钉死会静默翻转默认态测试；回归锁测试再叠加 monkeypatch.delenv 双保险）；② function-scoped autouse fixture 每测试前 `get_settings.cache_clear()`（`lru_cache` 泄漏防线——ASGI lifespan 亦调用 `get_settings()`）。
- **F3 是唯一人机协同验证门**（真实 AI key + 真实飞书凭据 + 账本界面目检，由用户执行；agent 预备全部命令脚本与核对清单）。除 F3 外全部验证 agent 可执行。
- Evidence: `<attemptDir>/task-<N>-ai-bill-extraction-pipeline.txt`（attemptDir = `omo ulw-loop status --json` 的 currentAttemptDir；无 ulw-loop 时用 `.omo/evidence/`）。每 todo 的 happy+failure QA 输出（pytest 输出 / curl 响应）存入对应 evidence 文件。
- 关键回归锁：`AI_ENABLED=false` 时 webhook 响应 JSON 恰为现存 5 键（`success/request_id/record_id/book_alias/message`）——存在专门测试锁定。

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

- **Wave 1（端到端最小闭环）**：todo 1 → 2 → {3, 5 并行} → 4 → 6 → 7（todo 4 的 `encode_fields` 以 todo 3 定义的 `ExtractionResult` 为入参类型，必须排在 3 之后）。完成后：真实配置下一笔 OCR 文本即可端到端产出一条账单（含污染防护）。
- **Wave 2（配置化 + 调试回路）**：todo 8 → 9 → {10, 11 并行}。
- **Wave 3（文档收尾）**：todo 12。
- **Final verification wave**：F1-F4（全部 todo 完成后并行）。

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | — | 2-12 | — |
| 2 | 1 | 3, 6, 7, 8, 9 | — |
| 3 | 2 | 4, 6 | 5 |
| 4 | 1, 3 | 6 | 5 |
| 5 | 1 | 6, 8 | 3 |
| 6 | 3, 4, 5 | 7 | — |
| 7 | 6 | 8, 11 | — |
| 8 | 7 | 9, 11 | — |
| 9 | 8 | 10, 12 | 11 |
| 10 | 9 | 12 | 11 |
| 11 | 7, 8 | 12 | 9 |
| 12 | 9, 10, 11 | F1-F4 | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

- [x] 1. 建分支与测试基建（requirements-dev.txt + 解封 test/ + pytest 配置 + conftest + 冒烟测试）
  What to do / Must NOT do: 从 master（c7e7d3c）切出 `feature/ai-extraction-pipeline` 分支；创建 `requirements-dev.txt`（`pytest==8.3.4`、`pytest-asyncio==0.24.0`、`asgi-lifespan==0.1.0`，若安装失败取执行时最新稳定版并精确 pin `==`）；读 `.gitignore`，删除忽略 `test/` 与 `requirements-dev.txt` 的行（AGENTS.md NOTES 记录了此设计现状，本计划按用户 TDD 约定推翻它——仅删这两类行，其余不动）；创建 `pytest.ini`（`[pytest]` + `asyncio_mode = auto` + `testpaths = test/unit`）；创建根级 `conftest.py`，在模块顶层（任何 `app.*` import 之前）**先钉死模式与 AI 变量**——`os.environ["FEISHU_TARGETS_FILE"] = ""`、`os.environ["FEISHU_ENV_FILE"] = ""`、`os.environ["AI_ENABLED"] = "false"`、`os.environ["AI_PROFILE_FILE"] = ""`（空串/假值 → 强制 legacy 模式 + 跳过运行时 env 文件 + 中和根 `.env` 的 AI 泄漏；`app/config.py:39,52` 的 setdefault 不覆盖已存在值——本地 `.env` 有 `AI_ENABLED=true` 也无法翻转测试），再 `os.environ.setdefault` 测试 env：`WEBHOOK_SHARED_TOKEN=test-webhook-token`、`FEISHU_APP_ID=test-app-id`、`FEISHU_APP_SECRET=test-app-secret`、`FEISHU_APP_TOKEN=test-app-token`、`FEISHU_TABLE_ID=test-table-id`、`FEISHU_RECORD_ID=test-record-id`（legacy 三件套必填，见 app/config.py:115-123）；再加 function-scoped autouse fixture：每测试前 `get_settings.cache_clear()`；创建 `test/unit/test_smoke.py`：`test_app_imports` 断言 `from app.main import app` 且 `app.title == "Feishu Webhook Service"`，`test_legacy_mode_pinned` 断言 `get_settings().feishu_targets_file is None`。Must NOT：不改 `requirements.txt`；不动 `runtime/` 任何文件；不写 Gherkin 骨架（冒烟测试本身即本 todo 的测试）。
  Parallelization: Wave 1 | Blocked by: 无 | Blocks: 2-12
  References (executor has NO interview context - be exhaustive): `app/config.py:55`（模块导入副作用加载 env——conftest 必须先于 app import 执行）、`app/config.py:115-123`（legacy 模式必填 env 清单）、`app/main.py:62`（app 实例）、AGENTS.md NOTES（test/ 与 requirements-dev.txt 被 gitignore 的现状）、README.md「本地启动」（已引用 requirements-dev.txt——本 todo 顺带修复该漂移）
  Acceptance criteria (agent-executable): `pip install -r requirements-dev.txt` 成功；`python -m pytest -v` 退出码 0 且 `test_app_imports` 与 `test_legacy_mode_pinned` 均 PASSED；`git status` 显示 `test/`、`requirements-dev.txt`、`pytest.ini`、`conftest.py` 为已跟踪/新增；`git checkout feature/ai-extraction-pipeline` 成功。
  QA scenarios (name the exact tool + invocation): happy = `python -m pytest test/unit/test_smoke.py -v` → 1 passed；failure = 临时注释 conftest 中 `FEISHU_APP_ID` 一行后 `python -m pytest test/unit/test_smoke.py -v` → 因 Settings 缺 env 报错（`RuntimeError: Missing required environment variable: FEISHU_APP_ID`），验证后恢复。Evidence `<attemptDir>/task-1-ai-bill-extraction-pipeline.txt`
  Commit: Y | Add pytest infrastructure and unignore test directory

- [x] 2. app/config.py 新增 AI 管线 env 配置
  What to do / Must NOT do: `Settings` dataclass 追加 9 字段：`ai_enabled: bool`、`ai_provider: str | None`、`ai_base_url: str | None`、`ai_api_key: str | None`、`ai_model: str | None`、`ai_timeout_seconds: int`、`ai_profile_file: Path | None`、`ai_profile_reload_interval_seconds: int`、`ai_dedup_ttl_seconds: int`。新增 `_bool_env(name, default=False)` helper（仿 `app/config.py:90-99` 的 `_int_env`：取值 strip.lower() ∈ {"1","true","yes","on"} → True，否则 False）。`get_settings()` 中：`ai_enabled` 默认 false；**ai_enabled=true 时**：`AI_PROVIDER` 用 `_require_env` 且值必须 ∈ {"anthropic","openai"}（否则 `RuntimeError`），`AI_API_KEY`、`AI_MODEL` 用 `_require_env`，`AI_PROFILE_FILE` 用 `_path_env` 且为 None 时 `RuntimeError("AI_PROFILE_FILE is required when AI_ENABLED=true")`；`AI_BASE_URL` 用 `_optional_env`（None，运行时按 provider 取默认）；`AI_TIMEOUT_SECONDS` 默认 20；`AI_PROFILE_RELOAD_INTERVAL_SECONDS` 默认 10；`AI_DEDUP_TTL_SECONDS` 默认 300。ai_enabled=false 时 AI 连接字段全为 None/默认值，**不得因 AI env 缺失而报错**。Must NOT：不引入 pydantic-settings；不改动任何现有字段语义。
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 3, 6, 7, 8, 9
  References (executor has NO interview context - be exhaustive): `app/config.py:58-76`（Settings dataclass 全文）、`app/config.py:78-110`（_require_env/_optional_env/_int_env/_path_env 模式）、`app/config.py:113-141`（get_settings 装配）；conftest.py（todo 1，测试 env 已就绪）
  Acceptance criteria (agent-executable): `python -m pytest test/unit/test_config_ai.py -v` 全绿，覆盖：默认（无任何 AI env）`ai_enabled is False` 且 provider/key/model/profile_file 均 None；`AI_ENABLED=true` + 完整 env → 各字段正确解析；`AI_ENABLED=true` 缺 `AI_PROVIDER` → RuntimeError；`AI_PROVIDER=openai2`（非法值）→ RuntimeError；`AI_TIMEOUT_SECONDS` 默认 20；`AI_ENABLED=true` 缺 `AI_PROFILE_FILE` → RuntimeError；`AI_ENABLED=1`/`AI_ENABLED=true`/`AI_ENABLED=no` 布尔解析。注意 `get_settings` 有 `lru_cache`——测试用 `get_settings.cache_clear()` + monkeypatch env。
  QA scenarios (name the exact tool + invocation): happy = `python -m pytest test/unit/test_config_ai.py -v` → 全部 PASSED；failure = `AI_ENABLED=true AI_PROVIDER=anthropic python -c "from app.config import get_settings; get_settings()"`（缺其余必填 env）→ RuntimeError 非零退出。Evidence `<attemptDir>/task-2-ai-bill-extraction-pipeline.txt`
  Commit: Y | Add AI pipeline environment settings to config

  **TDD 流程（todo 2-12 每个编码 todo 均强制；todo 1 为纯测试基建、无生产行为，豁免——豁免经审查轮确认）**：先写 `test/unit/<file>.py` 仅含 Gherkin `# Given/When/Then` 行为注释 + 空测试函数（函数名表达行为，如 `test_extract_returns_all_fields_from_anthropic_tool_use`）→ 用 question 工具把骨架呈现给用户确认 → 用户确认后写正式测试代码 → 测试红 → 实现 → 测试绿。禁止在行为注释确认前编写任何测试代码或实现代码。

- [x] 3. app/ai_extractor.py 双协议结构化提取适配器
  What to do / Must NOT do: 新模块。`AiExtractorError(RuntimeError)`（携带 `stage: str`）。`ExtractionResult` frozen dataclass：`summary: str`、`description: str`、`flow_type: str`、`amount: float`、`category: str`、`payment_method: str`、`bill_date: str`（YYYY-MM-DD）。`AiExtractor` 类（`__init__(settings: Settings)`——**提示词不进构造器**：prompt 状态逐调用注入，TOML 热重载后新 prompt 立即生效的前提）。`async extract(original_text: str, prompt_header: str, field_prompts: dict[str, str]) -> ExtractionResult`，按 `settings.ai_provider` 分派：
  - **anthropic**：`POST {base}/v1/messages`（base=`ai_base_url` 或默认 `https://api.anthropic.com`），headers `x-api-key` + `anthropic-version: 2023-06-01`，body：`model`、`max_tokens=1024`、`system=prompt_header + 每字段提取要求（field_prompts 拼接）`、`messages=[{"role":"user","content":original_text}]`、`tools=[{"name":"submit_bill","description":"提交提取的账单字段","input_schema":{"type":"object","properties":{<7 键: {type, description=field_prompts[键]}>},"required":[全部 7 键]}}]`、`tool_choice={"type":"tool","name":"submit_bill"}`。响应解析：`content[]` 中 `type=="tool_use"` 的 `input` dict。
  - **openai**：`POST {base}/chat/completions`（base=`ai_base_url` 或默认 `https://api.openai.com/v1`），headers `Authorization: Bearer {key}`，body：`model`、`messages=[{"role":"system","content":prompt_header+字段要求},{"role":"user","content":original_text}]`、`tools=[{"type":"function","function":{"name":"submit_bill","description":...,"parameters":<同上 json_schema>}}]`、`tool_choice={"type":"function","function":{"name":"submit_bill"}}`。响应解析：`choices[0].message.tool_calls[0].function.arguments`（JSON 字符串 → `json.loads`）。
  - 校验：input 含全部 7 键且 `amount` 可转 float 且 > 0、`bill_date` 匹配 `^\d{4}-\d{2}-\d{2}$`、其余为非空 str——任一不满足 → `AiExtractorError(stage="validate")`。HTTP 非 200 / code 类错误 / `httpx.TimeoutException` / `httpx.HTTPError` / JSON 解析失败 → `AiExtractorError(stage="request"|"parse")`。**单次调用，无重试**。httpx 超时 = `httpx.Timeout(settings.ai_timeout_seconds)`。每调用新建 `httpx.AsyncClient`（对齐 `app/feishu_client.py:42,87` 的 per-call 惯例）。
  Must NOT：不引入 anthropic/openai SDK（裸 httpx）；日志不得打印 `original_text` 与 `ai_api_key`（只打 provider/model/stage/耗时 ms）；不做多次 AI 调用。
  Parallelization: Wave 1 | Blocked by: 2 | Blocks: 6
  References (executor has NO interview context - be exhaustive): `app/config.py`（Settings.ai_* 字段，todo 2）、`app/feishu_client.py:42-48`（httpx AsyncClient + 超时惯例）、`app/feishu_client.py:90-104`（非 200 与业务 code 错误处理模式）；Anthropic Messages API（tools/tool_use）；OpenAI Chat Completions（function calling）；httpx.MockTransport 用法——`def handler(request: httpx.Request) -> httpx.Response`，handler 内断言 `request.url.path` / `request.headers` / `json.loads(request.content)` 并返回 `httpx.Response(200, json={...})`
  Acceptance criteria (agent-executable): `python -m pytest test/unit/test_ai_extractor.py -v` 全绿，覆盖：**prompt 无状态**（构造器仅收 settings；prompt_header/field_prompts 逐调用注入并出现在请求体 system/tools schema 中——两次不同 prompt 调用产生不同请求体，热重载生效前提）；anthropic happy（MockTransport 断言 URL `/v1/messages`、`x-api-key` header、`tool_choice`——返回 tool_use → ExtractionResult 全字段）；openai happy（断言 `/chat/completions`、Bearer header、tool_calls 解析）；anthropic 超时（handler raise `httpx.TimeoutException`）→ AiExtractorError(stage="request")；缺 required 键 → stage="validate"；HTTP 500 → stage="request"；openai arguments 非法 JSON → stage="parse"；`AI_BASE_URL` 自定义值拼接正确（用户 relay 端点场景）。
  QA scenarios (name the exact tool + invocation): happy = `python -m pytest test/unit/test_ai_extractor.py -v` → 全部 PASSED；failure = `python -m pytest test/unit/test_ai_extractor.py -k "timeout or invalid" -v` → 失败路径断言通过（错误 stage 正确、无重试：MockTransport 计数 == 1）。Evidence `<attemptDir>/task-3-ai-bill-extraction-pipeline.txt`
  Commit: Y | Add dual-protocol structured AI extraction adapter

- [x] 4. app/field_codec.py 飞书字段编码器（白名单防污染 + 时区日期 + 软回退）
  What to do / Must NOT do: 新模块，纯函数零 IO。`FieldSpec` frozen dataclass：`ai_key: str`、`feishu_field: str`、`type: str`（"text"|"number"|"single_select"|"date"|"passthrough"）、`target: str`（"extract"|"bill"）、`fallback: str | None`、`prompt: str`、`source: str | None`（passthrough 专用，"summary"）。`encode_fields(extraction: ExtractionResult, specs: list[FieldSpec], option_whitelists: dict[str, set[str]]) -> tuple[dict[str, object], dict[str, object], list[str]]` 返回 `(extract_fields, bill_fields, warnings)`：
  - `text`：`str.strip()`；空串 → **跳过该字段不写入** + warning `"empty text skipped: {feishu_field}"`。
  - `number`：`float(value)`（ExtractionResult 已保证类型，防御性转换）。
  - `single_select`：`value.strip()`；**精确匹配** whitelist（`feishu_field` 键）→ 用原值；未命中 → `fallback` + warning `"option fallback: {feishu_field}: {value!r} -> {fallback!r}"`（覆盖尾空格场景 `"餐饮 "` ≠ `"餐饮"`——防污染核心）；fallback 亦不在 whitelist → `ValueError`（配置错误，profile 加载时已前置校验，此处防御性兜底）。
  - `date`：`datetime.strptime(value, "%Y-%m-%d")` → `ZoneInfo("Asia/Shanghai")` 当日 00:00 → `int(ts * 1000)` 毫秒时间戳；解析失败 → **当天日期**（Asia/Shanghai）+ warning `"date fallback to today: {feishu_field}: {value!r}"`。
  - `passthrough`：`source=="summary"` → 取 `extraction.summary`。
  - 按 `target` 分拣进 extract_fields / bill_fields 两个 dict。
  Must NOT：不做任何网络 IO；时区一律 `ZoneInfo("Asia/Shanghai")`（禁用 UTC/naive datetime）；未命中白名单的值绝不放入输出 dict。
  Parallelization: Wave 1 | Blocked by: 1, 3（`encode_fields` 入参类型 `ExtractionResult` 定义于 ai_extractor.py， import 硬依赖） | Blocks: 6
  References (executor has NO interview context - be exhaustive): todo 3 的 `ExtractionResult`；飞书日期字段毫秒时间戳约定（批判轮验证）；单选未知选项自动创建的污染风险（hyperplan 事实核查轮依据飞书 bitable 字段文档验证；即使飞书未来改为拒绝未知选项，白名单+回退依然正确无害——双向安全设计）；Python 标准库 `zoneinfo`
  Acceptance criteria (agent-executable): `python -m pytest test/unit/test_field_codec.py -v` 全绿，覆盖：text 正常/空串跳过；number 透传；单选命中/未命中回退（断言 warning 文案与值）/尾空格未命中/双未命中 ValueError；date 解析（固定日期 → 用 ZoneInfo 同源计算的期望毫秒值，禁止硬编码魔法数字）/坏日期回退今天/跨时区语义（UTC 16:00 = 上海次日 0 点场景，账单日期按上海日历）；passthrough=summary 分拣到 bill；extract/bill 双 dict 分拣正确。
  QA scenarios (name the exact tool + invocation): happy = `python -m pytest test/unit/test_field_codec.py -v` → 全部 PASSED；failure = `python -m pytest test/unit/test_field_codec.py -k "pollution or fallback" -v` → 污染防护用例通过（输出 dict 中不含未白名单值）。Evidence `<attemptDir>/task-4-ai-bill-extraction-pipeline.txt`
  Commit: Y | Add feishu field codec with option whitelist and timezone handling

- [x] 5. app/feishu_client.py 扩展：create_record / list_fields / update_record_field
  What to do / Must NOT do: 现有类追加三方法，全部复用 `_get_tenant_access_token`（`app/feishu_client.py:31-69`）与 per-call AsyncClient 惯例：
  - `async update_record_field(field_name: str, value: str, target: FeishuTargetConfig) -> str`：泛化 PUT（现有 `update_original_text` 的 body 换成 `{field_name: value}`，其余不变并委托本方法——`update_original_text` 对外签名与语义保持不变，`app/feishu_client.py:71-105`）。错误 `stage="update_record_field"`。
  - `async create_record(fields: dict, app_token: str, table_id: str, client_token: str) -> str`：`POST {base}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records`（`client_token` 传递位置——query param 或 body——**先读飞书官方文档再定**：https://open.feishu.cn/document/server-docs/docs/bitable-v1/bitable-record/create，实现与测试断言均以文档结论为准并在提交说明记录）；body `{"fields": fields}`；成功（code=0）返回 `data.record.record_id`；**重复 client_token 幂等语义**：先按文档核对重复 token 的响应形态——若返回特定错误码而非复用 record_id，则捕获该错误码视为幂等命中成功（不抛 FeishuClientError，由调用方记 warning "duplicate client_token accepted"）；两种形态（错误码 / code=0 复用原 record_id）都要有测试；其余非 200 或 code!=0 → `FeishuClientError(stage="create_record")`。若文档证实**不支持 client_token** 或其去重窗口短于 `AI_DEDUP_TTL_SECONDS`：不做替代实现，在 docs/ai-pipeline.md 记录为已知限制（竞态窗口转而依赖内存去重 + 单用户流量特征兜底）。
  - `async list_fields(app_token: str, table_id: str) -> dict[str, dict]`：`GET {base}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields?page_size=100`，`while data.has_more` 循环带 `page_token` 翻页；返回 `{field_name: field_def}`（field_def 含 `property.options[].name`）；错误 `stage="list_fields"`。
  Must NOT：不引入连接池/全局 client；不改 `_get_tenant_access_token`；错误一律走 `FeishuClientError`（带 stage，对齐 `app/AGENTS.md` 错误分类约定——新失败路径扩展 stage 而非新造响应形状）。
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 6, 8
  References (executor has NO interview context - be exhaustive): `app/feishu_client.py:25-105`（全文——token 缓存、错误模式、PUT 现状）、`app/target_registry.py:34-42`（FeishuTargetConfig 字段）、飞书 bitable v1 文档（create record / list fields）
  Acceptance criteria (agent-executable): `python -m pytest test/unit/test_feishu_client_ext.py -v` 全绿，覆盖：create_record happy（断言 POST URL、`client_token` 在请求中的位置与文档核对结论一致、body fields → 返回 record_id）；create 重复 client_token（按文档核对的响应形态分别覆盖：错误码形态 → 幂等命中不抛错 / code=0 复用形态 → 返回原 record_id）；create 其它 code!=0 → FeishuClientError(stage="create_record")；list_fields 单页与翻页（两次响应 has_more true/false → 合并 dict）；update_record_field 断言 PUT body `{field_name: value}` 且 URL 用 target 三元组；update_original_text 委托后行为不变（既有调用路径回归）。
  QA scenarios (name the exact tool + invocation): happy = `python -m pytest test/unit/test_feishu_client_ext.py -v` → 全部 PASSED；failure = `python -m pytest test/unit/test_feishu_client_ext.py -k "error" -v` → 错误路径（code!=0 / HTTP 500）抛 FeishuClientError 且 stage 正确。Evidence `<attemptDir>/task-5-ai-bill-extraction-pipeline.txt`
  Commit: Y | Add create_record, list_fields, and update_record_field to FeishuClient

- [x] 6. app/ai_profile.py（静态部分）+ app/pipeline.py 编排管线
  What to do / Must NOT do:
  - **ai_profile.py 静态部分**：`ProfileConfigError(RuntimeError)`；`AiProfile` frozen dataclass：`prompt_header: str`、`summary_field: str`、`bill_app_token: str`、`bill_table_id: str`、`fields: tuple[FieldSpec, ...]`；`parse_profile(path: Path) -> AiProfile`：tomllib 解析（仿 `app/target_registry.py:206`），校验：`[extract] summary_field` 非空且与 `target="extract"` 字段的 `feishu_field` **相等**（不等 → `ProfileConfigError`——两处命名仅作可读性冗余，以相等校验锁定权威）、`[bill] app_token/table_id` 非空、`[[fields]]` 每项 ai_key 唯一、type/target 合法枚举、`single_select` 必有 `fallback`、`passthrough` 必有 `source="summary"`、`target="extract"` 的字段唯一（即 summary 写回字段）；违规 → `ProfileConfigError`（信息含具体键名）。
  - **pipeline.py**：`AiPipeline.__init__(settings, extractor: AiExtractor, feishu: FeishuClient)`；内部 `_dedup: dict[str, float]` + `threading.Lock`（TTL 清理惰性执行）；`async run(original_text: str, target: FeishuTargetConfig, profile: AiProfile, option_whitelists: dict[str, set[str]]) -> PipelineResult`：
    1. `dedup_key = sha256(f"{target.alias}:{original_text}")`；锁内查 TTL（`time.time() - ts <= settings.ai_dedup_ttl_seconds`）→ 命中 → `PipelineResult(ai_status="duplicate", ...)`，**不调 AI 不调飞书**。
    2. `field_prompts = {spec.ai_key: spec.prompt for spec in profile.fields if spec.type != "passthrough"}`；`await extractor.extract(original_text, profile.prompt_header, field_prompts)` → AiExtractorError → `ai_status="failed"`（返回，不抛）。
    3. `encode_fields(...)` → ValueError（双未命中）→ `ai_status="failed"`。
    4. extract_fields 非空 → `feishu.update_record_field(profile.summary_field, 值, target)`；失败（FeishuClientError）→ **记 warning 继续**（账单创建是主目标）。
    5. `client_token = "ai-bill-" + dedup_key[:40]`；`bill_fields` → `feishu.create_record(bill_fields, profile.bill_app_token, profile.bill_table_id, client_token)` → record_id；失败 → `ai_status="failed"`。
    6. 成功 → 锁内记 `_dedup[dedup_key] = time.time()` → `ai_status="succeeded"`。
    `PipelineResult` frozen dataclass：`ai_status: str`（succeeded|failed|duplicate）、`bill_record_id: str | None`、`warnings: list[str]`、`extracted: dict[str, object]`（{amount, category, flow_type, description} 供 Shortcut 通知）、`dedup_hit: bool`。`run` 全程 `try/except Exception` 兜底 → `ai_status="failed"` + `logger.exception`（**run 永不向上抛**——AI 阶段任何意外不得破坏 webhook 200 语义）。Wave 1/2 过渡契约：`run(..., option_whitelists: dict[str, set[str]] | None = None)`——Wave 1 中 main.py 调用不传（None）→ pipeline 用内部 `_OptionWhitelistCache`（首用 lazy `await feishu.list_fields(profile.bill_app_token, profile.bill_table_id)`，dict 缓存 + 锁，**仅为 bill 表拉取**——extract 表写回恒为 text 字段无需白名单）；todo 8 落地后 main.py 改为恒传 registry 快照白名单并删除该缓存（参数即 Wave 2 正式契约，缓存仅为 Wave 1 桥接）。日志打 request 关键节点（dedup 命中/AI 耗时 ms/建账单 record_id/警告数），不打 original_text。
  Must NOT：不写任何飞书调用于 codec/extractor（分层：本模块才碰飞书）；去重只记**成功**创建（AI 失败后的合法重试不得被拦）；不做持久化去重；**不得在持有 threading.Lock 期间 await**（网络 IO 一律锁外执行，锁内只做 dict/引用原子交换——锁跨 await 会死锁事件循环，且顺序化测试测不出来）。
  Parallelization: Wave 1 | Blocked by: 3, 4, 5 | Blocks: 7
  References (executor has NO interview context - be exhaustive): todo 3/4/5 产出的 ExtractionResult/encode_fields/FeishuClient 新方法；`app/target_registry.py:57-109`（锁 + 快照并发模式参照）；`app/config.py`（ai_dedup_ttl_seconds）
  Acceptance criteria (agent-executable): `python -m pytest test/unit/test_pipeline.py -v` 全绿，覆盖：happy 全链（mock extractor+feishu：断言调用顺序 update_record_field → create_record、client_token 前缀 `ai-bill-` 且确定性（同输入同 token）、dedup 已记入）；AI 失败 → failed 且 feishu.create_record **零调用**；dedup 命中 → duplicate 且 extractor **零调用**；TTL 过期（monkeypatch time）→ 全链重跑；写回失败（update_record_field raise）→ 账单仍创建 + warning；encode ValueError → failed；run 对任意内部异常兜底不抛。另 `test/unit/test_ai_profile_parse.py`：合法 TOML → AiProfile 全字段；缺 bill.app_token → ProfileConfigError；ai_key 重复 / single_select 无 fallback → ProfileConfigError。
  QA scenarios (name the exact tool + invocation): happy = `python -m pytest test/unit/test_pipeline.py test/unit/test_ai_profile_parse.py -v` → 全部 PASSED；failure = `python -m pytest test/unit/test_pipeline.py -k "failed or duplicate" -v` → 失败语义用例通过（failed 不抛异常、duplicate 零 AI 调用）。Evidence `<attemptDir>/task-6-ai-bill-extraction-pipeline.txt`
  Commit: Y | Add AI extraction pipeline with TTL dedup and profile parsing

- [x] 7. app/main.py 接线：AI_ENABLED 门控 + 响应扩展 + 生命周期装配
  What to do / Must NOT do:
  - `WebhookSuccessResponse` 追加 4 个 `Optional` 字段（default None）：`ai_status: str | None`、`ai_record_id: str | None`、`ai_warnings: list[str] | None`、`ai_extracted: dict | None`。
  - `ingest_ocr` 路由装饰器加 `response_model_exclude_none=True`（`app/main.py:81-91`）——**关闭开关时响应 JSON 与 master 逐键一致**（None 字段被剔除）。
  - `lifespan`（`app/main.py:47-59`）：`settings.ai_enabled` 为 true 时：`profile = parse_profile(settings.ai_profile_file)`（异常 → 启动失败，对齐 `load_initial` fail-fast 语义）、构造 `AiExtractor(settings)`（**prompt 无状态，不收 profile**——热重载前提，见 todo 3）、`AiPipeline` → `app.state.ai_pipeline`；false 时全部不构造。
  - `ingest_ocr` 在 `update_original_text` 成功后（`app/main.py:214-225` 之前）：`ai_enabled` 且 pipeline 存在 → `result = await ai_pipeline.run(...)` → 填充 ai_* 字段（failed 也 200——message 不变）。**AI 阶段不得引入任何新的 5xx 路径**。
  - 失败语义对照：飞书写原始信息本身失败仍走现有 502（`app/main.py:180-199`）——AI 只在成功之后追加。
  Must NOT：不加 `WebhookRequest` 字段；不改现有错误分支；不因 AI 异常改变 HTTP 状态码。
  Parallelization: Wave 1 | Blocked by: 6 | Blocks: 8, 11
  References (executor has NO interview context - be exhaustive): `app/main.py:39-44`（响应模型）、`app/main.py:47-59`（lifespan）、`app/main.py:81-141`（路由装饰器与鉴权）、`app/main.py:214-225`（成功响应构造）；todo 6 的 AiPipeline/PipelineResult
  Acceptance criteria (agent-executable): `python -m pytest test/unit/test_webhook_ai.py -v` 全绿（httpx.AsyncClient + ASGITransport，mock Feishu/AI transport），覆盖：**回归锁**——`AI_ENABLED` 未设（测试内 `monkeypatch.delenv("AI_ENABLED", raising=False)` + `get_settings.cache_clear()` 双保险，防本地 `.env` 泄漏翻转）→ 响应 JSON 恰为 5 键 `{success, request_id, record_id, book_alias, message}`（排序无关的键集合断言）；enabled+succeeded → 200 且 ai_status/ai_record_id/ai_extracted 存在、ai_extracted 含 amount/category；enabled+AI 失败 → **200** + `ai_status=="failed"` + record_id 正常（原始信息已写）；401（无/错 X-Webhook-Token）→ 不触发 AI；422 校验路径不触发 AI。
  QA scenarios (name the exact tool + invocation): happy = `python -m pytest test/unit/test_webhook_ai.py -v` → 全部 PASSED；failure = `python -m pytest test/unit/test_webhook_ai.py -k "regression or failed" -v` → 回归锁与失败语义用例通过。Evidence `<attemptDir>/task-7-ai-bill-extraction-pipeline.txt`
  Commit: Y | Wire AI pipeline behind AI_ENABLED gate with extended webhook response

- [x] 8. app/ai_profile.py 注册表（mtime 热重载 + 白名单快照）+ runtime/ai-profile.toml.example
  What to do / Must NOT do:
  - `AiProfileRegistry`（**全部 IO 方法 async**——`async def load_initial / maybe_reload / reload`；`get_snapshot()` 为 sync 只读。与 TargetRegistry 的 sync 不同，因快照装配需 `await feishu.list_fields`；调用点相应全 await：lifespan `await registry.load_initial()`、`ingest_ocr` 中 `await registry.maybe_reload()`、`reload_config` 处理器 `await registry.reload(force=True)`）：克隆 `app/target_registry.py:57-109` 骨架——`_lock/_snapshot/_generation/_last_reload_check_at/_last_reload_error/_config_valid`；`AiProfileSnapshot` frozen dataclass：`profile: AiProfile`、`option_whitelists: dict[str, set[str]]`、`loaded_at/source_mtime/generation`。`load_initial()`（异常 → 启动失败）；`maybe_reload()`（`ai_profile_reload_interval_seconds` 节流 + mtime 对比）；`reload(force=...)`；`get_snapshot()`（config_valid=false → raise `AiProfileRegistryUnavailableError`）；**`get_status()`（sync 非抛出诊断访问器：无论快照是否有效均返回 `{generation, config_valid, last_reload_error}`——供 admin 端点在 fail-closed 态渲染诊断信息，TargetRegistry.describe() 的对应物）**。快照装配：`parse_profile` + `await feishu.list_fields(bill_app_token, bill_table_id)`（**只拉 bill 表**——extract 表写回恒为 text 字段，无需白名单，不重复拉取）→ 对每个 `single_select` spec 抽 `property.options[].name` 集合；**校验**：配置的 feishu_field 不在 list_fields 返回中 / options 为空 / fallback 不在 options → `ProfileConfigError` → `_config_valid=False`（fail-closed，防污染保证）；list_fields 网络失败同样 fail-closed。
  - `main.py` 替换装配：lifespan 用 registry 取代 todo 6 的静态 parse + pipeline 内部白名单缓存（`_OptionWhitelistCache` 删除，whitelists 改从快照传入；提取器无需重建——todo 3 起即为 prompt 无状态，profile/prompt 逐请求从快照取，**prompt 热重载由此闭环**：改 TOML → mtime 变化 → 新快照 → 下一请求即用新 prompt）；`ingest_ocr` 调 pipeline 前先 `registry.maybe_reload()` + 取快照（`AiProfileRegistryUnavailableError` → 503，错误码 `AI_PROFILE_UNAVAILABLE`，错误包装完全镜像 `app/main.py:154-166` 的 TargetRegistryUnavailableError 分支）；`/admin/config/reload`（`app/main.py:228-286`）扩展：同时 force-reload AI registry（存在时），响应 dict 追加 `ai_profile` 子 dict（generation/config_valid/last_reload_error）——向后兼容（追加键）。
  - `runtime/ai-profile.toml.example`：完整模板（语言风格参照 `runtime/feishu-targets.toml.example`）：`[extract] summary_field="精简原始数据"`；`[bill] app_token/table_id` 占位；`prompt_header`；`[[fields]]` 8 条目——summary(→extract,text)、description→消费描述(text)、flow_type→收支类型(single_select,fallback="支出")、amount→金额(number)、category→收支分类(single_select,fallback="其他")、payment_method→支付途径(single_select,fallback="未知")、bill_date→账单日期(date)、raw_source→原始采集账单数据(passthrough,source="summary")，每条含中文 prompt 示例。
  Must NOT：TOML 中不出现任何密钥（连接配置只走 env）；热重载坏 TOML 不得 crash 运行中服务（config_valid=False → 503）；快照不可变（对齐 `app/AGENTS.md` 快照约定）；**不得在持有 threading.Lock 期间 await**（`await feishu.list_fields` 等网络 IO 一律锁外执行，锁内仅快照原子交换——忠实保留 TargetRegistry 骨架的「IO 在锁外、交换在锁内」结构，异步化时不得引入锁跨 await）。
  Parallelization: Wave 2 | Blocked by: 7 | Blocks: 9, 11
  References (executor has NO interview context - be exhaustive): `app/target_registry.py:57-109`（注册表骨架与并发模式）、`app/target_registry.py:162-172`（fail-closed 语义）、`app/main.py:47-59`（lifespan）、`app/main.py:154-166`（503 错误分支模板）、`app/main.py:228-286`（reload_config）；todo 6 的 parse_profile/AiProfile
  Acceptance criteria (agent-executable): `python -m pytest test/unit/test_ai_profile_registry.py -v` 全绿，覆盖：合法 TOML+list_fields mock → 快照含 whitelists 与 generation=1；single_select 的 fallback 不在 options → ProfileConfigError 且 config_valid=False；list_fields 抛错 → fail-closed；mtime 未变 → 二次 maybe_reload 不重载（generation 不变）；TOML 修改后（tmp_path 重写 + mtime 推移）→ reload → generation+1 且新 whitelist 生效；`/admin/config/reload` 响应含 ai_profile 键；registry 不可用 → POST webhook 503 `AI_PROFILE_UNAVAILABLE`。
  QA scenarios (name the exact tool + invocation): happy = `python -m pytest test/unit/test_ai_profile_registry.py -v` → 全部 PASSED；failure = `python -m pytest test/unit/test_ai_profile_registry.py -k "fail_closed" -v` → 坏配置 503 用例通过。Evidence `<attemptDir>/task-8-ai-bill-extraction-pipeline.txt`
  Commit: Y | Add hot-reloading AI profile registry with option whitelists

- [x] 9. 管理端点：POST /admin/ai/test（干跑）+ GET /admin/ai/profile
  What to do / Must NOT do:
  - `POST /admin/ai/test`：请求体 pydantic `{"text": str}`（`extra="forbid"`，min_length=1 max_length=32768）；鉴权**逐字镜像** `reload_config` 模式（`app/main.py:232-264`：`X-Admin-Token` header + `secrets.compare_digest`，`CONFIG_RELOAD_TOKEN` 未设 → 404 `RELOAD_DISABLED`）；`AI_ENABLED=false` 或 registry 不存在 → 404（错误码 `AI_DISABLED`）；行为 = **干跑**：取 registry 快照（`AiProfileRegistryUnavailableError` → 503，镜像 `reload_config` 的 registry 错误分支——干跑端点不得裸 500）→ `extractor.extract(text, snapshot.profile.prompt_header, 从快照派生的 field_prompts)`（保证测的是热重载后的最新 prompt）+ `encode_fields`（用快照 whitelists）→ 返回 `{"ai_status", "extracted": <原始 7 字段>, "bill_fields": <编码后>, "summary_writeback": {"field", "value"}, "warnings"}`；**不写飞书、不记 dedup**；AiExtractorError → 200 + `ai_status:"failed"` + error message（干跑不 5xx）。
  - `GET /admin/ai/profile`：同鉴权；返回 `{"ai_enabled", "provider", "model", "timeout_seconds", "dedup_ttl_seconds", "profile": {summary_field, bill:{app_token, table_id}, fields:[全 FieldSpec]}, "whitelists": {feishu_field: [options]}, "registry": {generation, config_valid, last_reload_error}}`；`AI_ENABLED=false` → 404 `AI_DISABLED`；**registry fail-closed（config_valid=false）时既不得 500 也不得 503**——返回 200 降级体：`profile`/`whitelists` 为 null、`registry` 经非抛出访问器 `get_status()` 填充（配置页正靠此渲染故障原因，见 todo 10）；**响应绝不包含 ai_api_key**。
  Must NOT：不暴露 ai_api_key（任何端点）；干跑不得产生飞书写入与 dedup 副作用；不加新鉴权机制（复用 X-Admin-Token 模式）。
  Parallelization: Wave 2 | Blocked by: 8 | Blocks: 10, 12
  References (executor has NO interview context - be exhaustive): `app/main.py:228-286`（admin 鉴权与响应模式全文）；todo 8 的 registry 快照；todo 3/4 的 extract/encode
  Acceptance criteria (agent-executable): `python -m pytest test/unit/test_admin_ai.py -v` 全绿，覆盖：无 token → 401；`CONFIG_RELOAD_TOKEN` 未设 → 404；`AI_ENABLED=false` → 404 AI_DISABLED；happy 干跑（feishu mock **spy 断言 create_record/update_record_field 零调用**）；AI 失败 → 200 + failed；GET profile 200 且响应 JSON 无 "ai_api_key" 子串（全文序列化断言）；GET profile 于 registry fail-closed 态 → 200 降级体（`profile`/`whitelists` 为 null、`registry.config_valid==false`、`last_reload_error` 有值）；未白名单单选 → 干跑响应 warnings 含回退记录。
  QA scenarios (name the exact tool + invocation): happy = `python -m pytest test/unit/test_admin_ai.py -v` → 全部 PASSED；failure = `python -m pytest test/unit/test_admin_ai.py -k "auth or disabled" -v` → 鉴权与禁用门控用例通过。Evidence `<attemptDir>/task-9-ai-bill-extraction-pipeline.txt`
  Commit: Y | Add AI dry-run test and profile inspection admin endpoints

- [x] 10. 只读配置页 app/static/admin.html（GET /admin/ai）
  What to do / Must NOT do: 单文件 vanilla HTML+内联 CSS+JS，**无框架无构建步骤**；路由 `GET /admin/ai` → 读文件返回 `HTMLResponse`（读取时即渲染，无缓存，方便宿主机改后刷新）；页面（中文标签 + 英文副标，双语对照）：① token 输入框（明示"仅存本机浏览器 localStorage"）+ 加载按钮 → `GET /admin/ai/profile`（`X-Admin-Token` header）渲染：启用状态/provider/model/超时/字段表（ai_key/飞书字段/类型/回退/prompt）/白名单各字段选项数/registry generation 与 config_valid/last_reload_error（**含 fail-closed 降级态渲染**：config_valid=false 时展示 last_reload_error、profile 区显示"配置无效"占位——依赖 todo 9 的降级 200 响应）；② textarea（样例 OCR 文本）+ 测试按钮 → `POST /admin/ai/test` → 渲染 extracted + bill_fields + warnings（警告高亮）；③ fetch 401 → 提示重输 token。无任何内嵌密钥。
  Must NOT：不引入 npm/打包器/CDN 依赖（零外链资源，纯内联）；不提供任何写回/保存配置功能（runtime 卷 :ro）；不在页面硬编码 token 默认值。
  Parallelization: Wave 2 | Blocked by: 9 | Blocks: 12
  References (executor has NO interview context - be exhaustive): todo 9 的两个 API 契约（路径/header/响应形状）；README.md「API」节（接口风格）
  Acceptance criteria (agent-executable): `python -m pytest test/unit/test_admin_page.py -v` 全绿：`GET /admin/ai` → 200 + `text/html` + 响应体含关键元素 id（`token-input`/`load-profile-btn`/`test-prompt-btn`/`result-panel`）与 `X-Admin-Token` 字符串（页面 fetch 用）；`AI_ENABLED=false` 时页面仍可访问（纯静态壳，数据接口自会 404——页面对此有提示态）。
  QA scenarios (name the exact tool + invocation): happy（主验收，agent 可执行）= `python -m pytest test/unit/test_admin_page.py -v` → 全部 PASSED；次级冒烟（agent 可执行，**不依赖真实飞书**——`AI_ENABLED=true` 时 lifespan 的 registry 装配会打真实飞书 API，故冒烟用 false）= 测试 env（`AI_ENABLED=false` + `CONFIG_RELOAD_TOKEN=test-admin-token` + 其余 conftest env）启动 `uvicorn app.main:app --port 2399` 后 `curl -s http://localhost:2399/admin/ai | grep -c "test-prompt-btn"` → ≥1 且 `curl -s -o /dev/null -w "%{http_code}" http://localhost:2399/admin/ai` → 200（页面静态壳在禁用态可访问，数据端点 404 属预期提示态），验后停服；failure = `curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:2399/admin/ai/test`（无 token）→ 401。Evidence `<attemptDir>/task-10-ai-bill-extraction-pipeline.txt`
  Commit: Y | Add read-only AI configuration page with prompt tester

- [x] 11. 跨模块失败矩阵集成套件
  What to do / Must NOT do: `test/unit/test_failure_matrix.py`——ASGITransport 全链（mock 飞书 + mock AI transport），逐行矩阵（每行独立测试函数 + 独立断言，允许与单模块测试重叠但必须自证）：①AI 超时 → 200/`ai_status=failed`/原始信息 PUT 已发生（mock 计数=1）且 create_record 零调用；②AI 返回缺 required 键 → 同上；③未知名单选值（如 `"餐饮 "`尾空格）→ 账单**仍创建** + 该字段值=fallback + ai_warnings 含回退记录 + **飞书收到的 fields 里绝无未白名单值**（断言整个 body 序列化）；④坏日期 → 账单创建 + 日期=当天上海毫秒 + warning；⑤同文本 60s 内二次请求 → `ai_status=duplicate` + AI mock 零新增调用 + create_record 恒定 1 次；⑥TTL 过期后重发 → 全链重跑 create_record=2（合法重试不被拦）；⑦`AI_ENABLED=false` → 响应无 ai_* 键（todo 7 回归锁的矩阵版）；⑧profile 热重载坏 TOML → 下一请求 503 `AI_PROFILE_UNAVAILABLE`，修复 TOML 后自动恢复 200（测试须绕过节流：conftest 设 `AI_PROFILE_RELOAD_INTERVAL_SECONDS=0` 或 monkeypatch registry `_last_reload_check_at=0`，否则 `maybe_reload` 提前返回、快照仍为旧值吃不到 503）；⑨client_token 确定性（同文本两次成功创建的 token 相同——幂等后备语义）。
  Must NOT: 不引入新生产代码（纯测试 todo；发现缺陷则修复对应模块并在该 todo 提交说明记录）。
  Parallelization: Wave 2 | Blocked by: 7, 8 | Blocks: 12
  References (executor has NO interview context - be exhaustive): todo 7/8 的 main 装配与 registry；conftest 测试 env
  Acceptance criteria (agent-executable): `python -m pytest test/unit/test_failure_matrix.py -v` → 9 个矩阵场景全 PASSED；`python -m pytest -v`（全量）→ 0 failed。
  QA scenarios (name the exact tool + invocation): happy = `python -m pytest test/unit/test_failure_matrix.py -v` → 全部 PASSED；failure = 临时将矩阵③的白名单断言改为期望污染值出现 → pytest FAIL（自证断言有效），验证后恢复。Evidence `<attemptDir>/task-11-ai-bill-extraction-pipeline.txt`
  Commit: Y | Add cross-module AI failure matrix integration suite

- [x] 12. 双语文档 + 迁移清单 + AGENTS.md 反模式重写 + env 模板
  What to do / Must NOT do:
  - `docs/ai-pipeline.md`（中文，默认）与 `docs/en/ai-pipeline.md`（英文镜像，内容逐节对应）：背景（飞书 AI 配额断供）、架构流程图（ASCII：webhook → 写原始信息 → AI 单次结构化提取 → 写精简数据 → 建账单）、9 个 env 变量表（名称/默认/说明）、TOML 全 schema（逐字段注释版 example）、webhook 新响应字段说明（含 Shortcut 通知用法建议——展示 ai_extracted 的金额/分类）、`/admin/ai`* 三端点、配置页使用说明、**迁移清单**（切换前必做：在飞书侧停用「AI 自动填充」与两条 AI 自动化工作流——逐条列出用户描述的旧链路节点；风险说明：AI 配额每月重置会复活旧自动化 → 双重记账；建议月度复查一次；**切换时核对**：账单明细表「记账日期」公式确实存在且引用「账单日期」（TODATE）——此为假设而非已验证事实，若公式不存在则在文档记录为已知限制并提示用户补建公式）、**多维表格 URL 取 key 说明**（`/base/{app_token}?table={table_id}` 提取法；record 分享链接为不透明 token 解析不出 record_id——record_id 从浏览器 URL 或飞书 API 获取）；**白名单刷新操作说明**（在飞书侧新增单选选项后白名单不会自动刷新——需调用 `POST /admin/config/reload` 或修改 TOML 触发重载，否则新选项持续被回退到默认值）；**`/admin/ai`* 端点前置条件**（需设置 `CONFIG_RELOAD_TOKEN`，未设时端点 404）；**`AI_BASE_URL` 拼接规则**（anthropic：base **不含** `/v1`，服务追加 `/v1/messages`；openai：base **含** `/v1`，服务追加 `/chat/completions`——配置中转端点时极易踩错，两种协议规则不同）。
  - `README.md` 与 `docs/en/README.md`：各加「AI 提取管线」小节 + 文档导航链接（保持两镜像同步）。
  - `.env.example`：追加 AI_* 变量段（带注释，含 `AI_BASE_URL` 拼接规则说明：anthropic base 不含 `/v1`、openai base 含 `/v1`）；**顺带修复既有漂移**——`HTTP_TIMEOUT_SECONDS=30`→`10`、`FEISHU_TARGET_RELOAD_INTERVAL_SECONDS=30`→`10`（对齐 `app/config.py:137,140` 实际默认值，AGENTS.md NOTES 记录的已知问题）。
  - `runtime/feishu-webhook.env.example`：追加 AI_* 段（`AI_PROFILE_FILE=/runtime/ai-profile.toml` 示例——docker 卷已挂 `:ro` 无需改 compose）。
  - `AGENTS.md`（根）：STRUCTURE 加 4 新模块 + static/；CODE MAP 加新符号（AiExtractor/AiPipeline/AiProfileRegistry 等）；ANTI-PATTERNS 重写「NEVER add bill parsing」条款为**有界新契约**："账单字段提取逻辑只允许存在于 app/ai_extractor.py / app/field_codec.py / app/ai_profile.py / app/pipeline.py（AI_ENABLED 门控内）；webhook 请求契约不变（单点写入原始信息的语义保留）；不得在其它模块添加解析逻辑"；COMMANDS 加 `pip install -r requirements-dev.txt` 与 `python -m pytest`；NOTES 移除"requirements-dev.txt 不存在"条目（已修复）。
  - `app/AGENTS.md`：WHERE TO LOOK 加 4 模块；Request Flow 加 AI 阶段分支；Error Taxonomy 加 `AiExtractorError`(内部消化→ai_status) / `AiProfileRegistryUnavailableError`(503) 两行。
  Must NOT：不提交真实 runtime 值；中英文档必须逐节对应（项目双语镜像硬约定）；不改动与本特性无关的文档段落（漂移修复仅限 .env.example 两行）。
  Parallelization: Wave 3 | Blocked by: 9, 10, 11 | Blocks: F1-F4
  References (executor has NO interview context - be exhaustive): `docs/` 与 `docs/en/` 现有文档结构（README 文档导航节）、`runtime/feishu-targets.toml.example`（example 文件风格）、`AGENTS.md` 全文、`app/AGENTS.md` 全文、`.env.example`（漂移行）、`app/config.py:137,140`（真实默认值）
  Acceptance criteria (agent-executable): `ls docs/ai-pipeline.md docs/en/ai-pipeline.md runtime/ai-profile.toml.example` 全部存在；`grep -c "AI_ENABLED" .env.example runtime/feishu-webhook.env.example docs/ai-pipeline.md docs/en/ai-pipeline.md` 均 ≥1；`grep "HTTP_TIMEOUT_SECONDS" .env.example` 输出含 `=10`；`grep -c "迁移" docs/ai-pipeline.md` ≥1 且英文版含对应 Migration 节；`grep -c "ai_extractor" AGENTS.md` ≥1；两份 AGENTS.md 均含新模块条目；全量 `python -m pytest -v` 仍 0 failed。
  QA scenarios (name the exact tool + invocation): happy = 上述 acceptance 命令逐条执行输出符合预期；failure = `grep "requirements-dev.txt" README.md` 仍指向该文件且文件已存在（README 本地启动指引不再悬空）。Evidence `<attemptDir>/task-12-ai-bill-extraction-pipeline.txt`
  Commit: Y | Add bilingual AI pipeline docs, migration checklist, and update AGENTS.md

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit
  验收（agent-executable）：`git diff master --stat` 逐文件对照 Scope Must have / Must NOT have——无未列文件改动、`requirements.txt` 零 diff、runtime 真实值零提交；每 todo 的 Commit 行与实际提交一一对应。
- [x] F2. Code quality review
  验收（agent-executable）：全量 `python -m pytest -v` → 0 failed；`python -m py_compile app/*.py` → 0 错误；新模块符合 app/AGENTS.md 约定（错误 stage 填充、日志不打 original_text/token、快照不可变、相对导入、per-call AsyncClient）。
- [~] F3. Real manual QA
  **唯一人机协同门**（需用户真实 AI key + 真实飞书凭据 + 账本界面目检）：按 Success criteria 2-5 场景逐条执行——agent 预备 curl 命令脚本与核对清单，用户执行并确认；账本单选选项总数前后一致（防污染最终实证）。
- [x] F4. Scope fidelity
  验收（agent-executable）：`grep -rniE "oauth|sqlite|sqlalchemy|celery" app/` → 0 命中；`git diff master -- requirements.txt` 为空；`WebhookRequest` 字段与 master 逐一比对一致（不加字段）。

## Commit strategy

- 每个 todo 恰好一个原子提交（实现 + 测试 + 相关 example 同提交），祈使句风格对齐仓库历史（"Add ..." / "Fix ..."），不附带 AI 署名尾注。
- 提交前 `python -m pytest -v` 必须 0 failed（todo 12 起全量跑）。
- **绝不提交**：`runtime/feishu-webhook.env`、`runtime/feishu-targets.toml`、`runtime/ai-profile.toml` 真实值、任何含 `AI_API_KEY` 真值的文件。
- 分支 `feature/ai-extraction-pipeline` 不直接 push master；PR 交付由用户决定（`$start-work --make-pr` 或手动）。
- 用户 TDD 硬约定的 question 确认点（每编码 todo 的 Gherkin 骨架确认）不产生提交，只作流程门。

## Success criteria

1. **回归**[agent]：`AI_ENABLED` 未设置时，`python -m pytest -v` 全绿，且 webhook 响应 JSON 与 master 行为逐键一致（5 键锁定测试通过）；`requirements.txt` 3 依赖不变（`git diff master -- requirements.txt` 为空）。
2. **端到端**[human，经 F3]：本地服务（真实 runtime env + `AI_ENABLED=true` + 真实 AI key + 真实 ai-profile.toml）收到一条样例 OCR 账单文本 → 200 + `ai_status="succeeded"`；飞书侧：AI 自动提取表 record 的 `精简原始数据` 已写入 + `账单明细` 表新增一条记录，含 消费描述/收支类型/金额/收支分类/支付途径/原始采集账单数据/账单日期 七字段；`记账日期` 由飞书公式自动生成（假设而非已验证——F3 时核对公式存在，见 todo 12 迁移清单）；无任何新增单选选项（选项总数不变）。
3. **失败语义**[human，经 F3]：错误 AI key（必然失败）→ 200 + `ai_status="failed"` 且原始信息已写入；改回正确 key 重发同一文本 → succeeded；60s 内再重发同一文本 → `ai_status="duplicate"` 且账单明细表无第二条。
4. **管理面**[human，经 F3]：`/admin/ai/test` 干跑返回提取预览且飞书零写入（日志无 create_record）；`/admin/ai` 页面可加载 profile 并完成一次干跑测试。
5. **热重载**[human，经 F3]：修改 `runtime/ai-profile.toml`（如改 prompt）→ ≤10s 内生效（无需重启，`/admin/config/reload` 可立即触发）；写入非法 TOML → 服务 503 fail-closed 而非崩溃，修复后自愈。
6. **文档**[agent]：中英双文档逐节对应；迁移清单明确"停用旧飞书 AI 自动化"步骤；AGENTS.md 反模式已重写为有界契约。
