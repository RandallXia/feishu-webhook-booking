# ui-redesign-field-toggles - Work Plan

## TL;DR (For humans)

**What you'll get:** 账单表配置变成一张清晰的 8 行映射表格（每行带启用开关、匹配状态芯片、可展开提示词），AI 连接区只读变量收进折叠的「部署信息」块。禁用字段 = AI 仍提取但不写入飞书。

**Why this approach:** 真机实测发现映射区 40 个控件零样式堆叠 + 只读信息混排。5 成员对抗规划收敛：语义 `<table>`（`.bill-row` 类保真让测试门与 422 映射免费存活）+ 写时跳过开关（提示侧过滤会破坏固定提取 schema）。

**What it will NOT do:** 不改 webhook 契约；不新增依赖；禁用字段仍被 AI 提取（固定 schema 约束，结果丢弃）；summary 行锁定常开不可禁用。

**Effort:** Medium
**Risk:** Medium - enabled 契约贯穿 TOML→parse→validate→encode→pipeline→API→UI 全链（任一环断裂即 registry fail-closed 503）

---

> TL;DR (machine): Medium effort / Medium risk — 账单映射表格化 + 每字段启用开关（写时跳过）+ env 分区重构 + 存量 422 静默 bug 修复，5 实现 todo；零新依赖。

## Scope
### Must have

1. **开关后端链**（`app/field_codec.py` / `app/ai_profile.py` / `app/toml_writer.py` / `app/pipeline.py`）：
   - `FieldSpec` 追加 `enabled: bool = True`（末位带默认）
   - `parse_profile_text` 读取 `enabled`（缺省 true——存量 TOML 零影响，golden 测试锁定）
   - `dump_profile` 的 `_FIELD_KEY_ORDER` 加入 `enabled`（紧跟 `target` 后；**始终输出**——与 dump_targets 的 enabled 处理一致，round-trip 确定性）
   - `validate_profile_candidate`：**零结构改动**（其现有语义：bill 行走 `target != "bill"` 循环校验 feishu_field 存在 + single_select fallback 在选项——禁用 spec 仍携带 target="bill" 故天然被覆盖；summary 行走独立的 extract 表 summary_field 检查——禁用 summary 也会被查但 UI 硬锁常开。**注意：该函数本就不校验非 single_select 的 bill 字段存在性之外的行**——与 `_load_snapshot` 的 `_validate_whitelists`（仅 single_select）镜像成立）
   - `encode_fields`：`if not spec.enabled: continue`（禁用 spec 不进 extract_fields/bill_fields）
   - `pipeline.run`：summary spec 的写回受其 enabled 控制（UI 硬锁常开，后端防御性尊重）；`PipelineResult.extracted` 过滤——**extracted 是硬编码 4 键字面量（amount/category/flow_type/description，非 spec 驱动）**，实现方式：`spec_by_key = {s.ai_key: s for s in profile.fields}` 后对 4 行各加 `if spec_by_key.get("<key>").enabled:` 守卫；bill_fields 为空 dict 时跳过 create_record + warning（防空行）
2. **API 契约**（`app/main.py`）：GET/PUT `/admin/config/profile` 的 fields[] 每条加 `enabled: bool`（7 键→8 键）；PUT pydantic 模型接受 enabled；**更新 round-trip 测试的键集合断言**。
3. **前端账单区**（full-builder 参考设计，`app/static/admin.{html,css,js}`）：
   - 语义 `<table class="bill-table">`（thead：启用/AI字段/飞书字段/类型/回退/提示词；`<tbody id="bill-fields-list">`；**`.bill-row` 类挂 `<tr>`**——querySelectorAll/测试门/422 映射全保真）
   - 每行：CSS 开关（真 checkbox + ~22 行 CSS，键盘可达）+ ai_key 标签（summary 行带「写回提取表」tag 且**开关硬锁 disabled**）+ 匹配芯片（✓自动/●手动/○未映射）+ prompt 单行折叠 textarea（focus 展开）
   - 禁用行：`.bill-row-disabled`（opacity+灰度）+ 控件 disabled 属性（开关本身可再启用）
   - 步行栏：URL 输入 + 解析按钮 + 表下拉 + 加载按钮一行 flex；app_token/table_id 转 `<input type="hidden">`（JS 契约保留）+ token 徽章展示（textContent）
   - 「已匹配 N/8 · 重新推导」链接行（`bill-derive-btn` id 保留但改 `<a>`，preventDefault）；自动推导保持 fills-empty-only 不变量 + 重渲染前合并当前 DOM 值入 billExistingFields（防未保存编辑被清）
   - prompt_header 移到表格下方；保存按钮粘性底栏
   - 422 错误：`.field-error` 红框单元格 + `#bill-error-row` 消息行；**path="bill"/"extract" 显式路由到 banner**（修存量静默丢弃 bug）
   - 隐藏 input 程序赋值不触发 input 事件 → parse/选表处理器手动 `markDirty()`
4. **前端 AI 连接区**：5 个可编辑字段双列 `.ai-grid`（API Key 全宽）+ `<details class="env-details">`「部署信息」包住 `env-other-list`（默认收起）+ 保存栏带内联「重启后生效」hint。
5. **集成测试 + 双语文档**：开关端到端（禁用字段不出现在写入调用参数）；docs/ai-pipeline.md + en 增「字段启用开关」小节（禁用=不写入、AI 仍提取、summary 常开）。

### Must NOT have (guardrails)

- 不改 WebhookRequest/webhook 契约；不引新依赖/框架
- **禁用字段不得从 AI 提取 prompt/工具 schema 排除**（`_validate_input` 要求 7 键全返回——排除即全站提取失败）
- **禁用行不得允许空 feishu_field**（TOML 序列化后解析失败 → registry fail-closed → 503）
- **PUT 校验不得跳过禁用行**（与 _load_snapshot 镜像，防保存成功但热重载 fail-closed）
- summary 行开关不得可禁用（UI 硬锁）
- 密钥值不进任何响应/DOM
- 动态文本一律 textContent（innerHTML 仅限迁移的只读查看器既有 escapeHtml 模式）
- 测试 grep 门保真：`renderBillRow`/`autoDerive`/`billParseErrorPath`/`markDirty` 保持 `function name(` 声明风格；`AI_KEYS = [...]` 数组字面量不变；全部被 grep 的元素 id 保留

## Verification strategy
- TDD（Gherkin→测试→实现，用户已授权自动实施）；MockTransport mock 飞书；ASGITransport 测路由；`node --check admin.js` 硬性语法门
- 关键回归锁：249 存量测试零回归（favicon 后基线）；enabled 缺省=true 的存量 TOML 解析 golden
- F3 人工门：Windows 实测——映射表格渲染、开关切换灰显、禁用字段保存后 webhook 不写该字段、折叠部署信息

## Execution strategy
- **Delegation A**（todo 1+2，后端+API 串行）：field_codec/ai_profile/toml_writer/pipeline + main.py + 测试
- **Delegation B**（todo 3+4，前端串行，依赖 A）：admin.html/css/js + 前端测试
- **Delegation C**（todo 5，依赖 B）：集成测试 + 文档

### Dependency matrix
| Todo | Depends on | Blocks |
| --- | --- | --- |
| 1 | — | 2, 3 |
| 2 | 1 | 3 |
| 3 | 2 | 4, 5 |
| 4 | 3 | 5 |
| 5 | 4 | F1-F4 |

## Todos

- [x] 1. 开关后端链：FieldSpec.enabled + parse/dump/validate/encode/pipeline
  What to do / Must NOT do: `app/field_codec.py` FieldSpec 追加 `enabled: bool = True`（末位）；`encode_fields` 循环首行 `if not spec.enabled: continue`。`app/ai_profile.py` `parse_profile_text` 读 `raw.get("enabled", True)` 传入 FieldSpec。`app/toml_writer.py` `_FIELD_KEY_ORDER` 在 `target` 后插入 `"enabled"`（**False 不是 None，通过 `val is None` 守卫后到达 `_format_value` 正确输出 "false"；True 同理输出 "true"——两态都序列化**）。`app/pipeline.py`：写回处 summary spec enabled=False 时跳过（防御性）；extracted 过滤见 Scope（spec_by_key 守卫实现）；bill_fields 为空 dict 时跳过 create_record + warnings 追加。**Must NOT**：不动 build_field_prompts（全部 7 键 prompt 恒构建）；不动 _validate_input；不改 validate_profile_candidate 结构。
  References: `app/field_codec.py`（FieldSpec + encode_fields）、`app/ai_profile.py`（parse_profile_text :58-175 + validate :193-290 的 target!="bill" 循环 + summary 独立检查）、`app/toml_writer.py`（_FIELD_KEY_ORDER :26 + dump_profile 的 val is None 守卫 :151-153）、`app/pipeline.py`（run :140-260 + **extracted 硬编码 4 键 :249-254**）
  Acceptance: `python -m pytest test/unit/test_field_codec.py test/unit/test_ai_profile_parse.py test/unit/test_pipeline.py -v` 全绿 + 新增用例：禁用 spec 不进输出 dict；存量 TOML（无 enabled 键）解析全部 enabled=True；dump round-trip **含 enabled=false 行也含 enabled=true 行**（两态序列化都锁定）；summary 禁用时写回跳过；全部 bill 禁用 create_record 零调用 + warning；extracted 4 键按启用过滤（禁用 category → extracted 不含 category）。
  QA: happy = 上述 pytest；failure = `-k "disabled or enabled"`。Evidence `.omo/evidence/task-1-ui-redesign.txt`
  Commit: Y | Add per-field enabled flag to profile schema and pipeline

- [x] 2. API 契约：GET/PUT profile fields[] 每行 7 键→8 键
  What to do / Must NOT do: `app/main.py` `_field_spec_to_dict`（**硬编码 7 键字典字面量，非自动带出**）显式加 `"enabled": spec.enabled` 第 8 键；PUT pydantic 模型的 fields 条目加 `enabled: bool`——**必填无默认**（省略即 422，fail-loud 防 UI 收集遗漏静默全启用）；**更新 test_config_profile_api.py 与 test_admin_bill_flow.py 中的 7 键集合断言为 8 键**。Must NOT：不改其它路由；pydantic extra 策略不变。
  References: `app/main.py`（_field_spec_to_dict **:599-608 硬编码字面量** + config_profile_put 模型）、`test/unit/test_config_profile_api.py`、`test/unit/test_admin_bill_flow.py`
  Acceptance: GET profile 响应 fields[].enabled 存在；PUT 带 enabled=false round-trip 后文件含 `enabled = false`；PUT 省略 enabled 键 → 422；全量 pytest 0 failed。
  QA: happy = round-trip 用例。Evidence `.omo/evidence/task-2-ui-redesign.txt`
  Commit: Y | Add enabled field to config profile API contract

- [x] 3. 前端账单区：映射表格 + 开关 + 芯片 + 步行栏
  What to do / Must NOT do: 按 full-builder 参考设计（草案 bg_d64b2955 输出，已验证测试门保真）实施 admin.html/css/js：语义 `<table>`（`<tr class="bill-row">` + 全部控件类名不变 + `<tbody id="bill-fields-list">`）；CSS 开关（真 checkbox + track/thumb，22 行）；匹配芯片三态 + `#bill-matched-count`；`bill-derive-btn` 改 `<a>` + preventDefault（**验证 grep 无元素类型专属选择器**：`grep -n 'bill-derive-btn' app/static/admin.{js,css}` 无 `button#`/`.btn#` 前缀命中——仅 getElementById 使用）；hidden inputs + token 徽章；prompt_header 移表格下；粘性表头/保存栏；禁用行灰显 + 控件 disabled；summary 行开关硬锁（checkbox disabled + hint「核心写回字段」）；重渲染前合并 DOM 值；**`billGatherBody` 显式收集 enabled：`r.querySelector('.enable-toggle').checked`，summary 行强制 `enabled: true`（忽略 DOM）**；422 单元格红框 + `#bill-error-row`；**path="bill"/"extract" 路由到 banner**（修存量 bug）；parse-URL 成功与表选择 change 处理器中手动调用**共享 markDirty()**（单一 dirty 标志——隐藏 input 程序赋值不触发 input 事件）。响应式 640px：网格塌单列 + 回退列隐藏。Must NOT：函数声明风格/id/类名破坏 grep 门；innerHTML 拼接动态文本。
  References: full-builder 草案全文（bg_d64b2955）、`app/static/admin.{html,css,js}` 现状、`test/unit/test_admin_bill_flow.py`（grep 门清单）
  Acceptance: `node --check admin.js` 退出 0；`python -m pytest test/unit/test_admin_bill_flow.py -v` 全绿（含更新后的断言）；grep 门：renderBillRow/autoDerive/billParseErrorPath 声明 + AI_KEYS 字面量 + enable-toggle 类存在；视觉验收待 F3。
  QA: happy = pytest + node --check + grep；failure = 422 定位用例。Evidence `.omo/evidence/task-3-ui-redesign.txt`
  Commit: Y | Rebuild bill mapping UI as table with per-field toggles

- [x] 4. 前端 AI 连接区：双列网格 + 部署信息折叠
  What to do / Must NOT do: admin.html/css/js：`.ai-grid` 双列（provider/base_url 一行、model/timeout 一行、API Key 全宽 `ai-grid-full`）；`<details class="env-details">` 包 `env-other-list`（默认收起，summary「部署信息 / Deployment Info」+ ▸/▾ 指示）；保存栏 `save-env-btn` + 内联「重启后生效」hint；响应式塌单列。全部被 grep 的 id 保留在服务 HTML 内。Must NOT：renderAiConnection 逻辑不变（只挪 DOM 位置）。
  References: full-builder 草案 §2/§3、`test/unit/test_admin_save_flow.py`（id 断言清单）
  Acceptance: `node --check` 0；test_admin_save_flow.py 全绿；admin.html 含 `<details` + env-other-list 在其内。
  QA: happy = pytest + grep。Evidence `.omo/evidence/task-4-ui-redesign.txt`
  Commit: Y | Restructure AI connection section with grid layout and collapsible deployment info

- [x] 5. 集成测试 + 双语文档
  What to do / Must NOT do: `test/unit/test_ui_redesign_integration.py`：①禁用 category → PUT 保存 → webhook → 断言 create_record 的 fields 参数**不含该字段**（写时跳过端到端）；②禁用后 GET profile 回显 enabled=false；③重新启用 → 恢复写入；④422 path="bill" → 响应体含该错误（banner 路由的后端侧确认）。文档：docs/ai-pipeline.md + docs/en/ai-pipeline.md「字段映射」节追加「字段启用开关」小节（禁用=不写入/ AI 仍提取/ summary 常开/ 重新启用即恢复）；`## ` 计数 +1 双语同步。AGENTS.md 若 CODE MAP 有 FieldSpec 描述则补 enabled。
  References: `test/unit/test_config_ui_integration.py`（集成范式）、docs 现状
  Acceptance: 集成 4 用例全绿；全量 pytest 0 failed（249 存量 + 新增）；文档结构断言。
  QA: happy = 全链。Evidence `.omo/evidence/task-5-ui-redesign.txt`
  Commit: Y | Add field toggle integration tests and bilingual docs

## Final verification wave
- [x] F1. Plan compliance audit：git diff 范围对照（field_codec/ai_profile/toml_writer/pipeline/main + 三静态文件 + 测试 + 文档）；requirements.txt 零 diff（✓ 通过；偏差记录：.omo/notepads 学习文件 134 行被纳入提交——内部工件非生产代码，非阻断，与上轮同类）
- [x] F2. Code quality：全量 pytest 0 failed + py_compile + node --check + textContent 抽查（✓ 272 passed；COMPILE_OK；NODE_OK；innerHTML 18 处全部为迁移旧模式，diff 零新增；MUST-NOT 验证：ai_extractor.py 零变更、build_field_prompts 零变更）
- [ ] F3. Real manual QA（可选人机抽查——写跳过语义已由 todo 5 集成测试覆盖）：Windows 目检映射表格渲染/开关灰显/折叠部署信息
- [x] F4. Scope fidelity：grep 禁项零命中；WebhookRequest 不变；密钥零泄漏（✓ 全部通过：无禁依赖/无 CDN 外链/requirements 零 diff/WebhookRequest 类定义无 diff）

## Commit strategy
每 todo 一原子提交（祈使句）；提交前全量 pytest 0 failed；不提交 runtime 真实值与 .omo/；分支 feature/frontend-config-ui 延续。

## Success criteria
1. [agent] 249 存量 + 新增全绿；enabled 缺省=true 存量兼容 golden
2. [agent] 禁用字段：encode 输出不含、create_record 参数不含、ai_extracted 不含
3. [human F3] 映射表格清晰可扫（开关+芯片+单行）；禁用灰显；保存生效
4. [human F3] AI 连接区编辑/只读分离；部署信息折叠
5. [agent] 422 path="bill" 不再静默
