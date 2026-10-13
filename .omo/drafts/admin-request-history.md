---
slug: admin-request-history
status: awaiting-approval
intent: clear
review_required: false
plan_path: .omo/plans/admin-request-history.md
pending-action: write .omo/plans/admin-request-history.md
approach: AI 记账请求历史（持久化）—— 新模块 app/history_store.py（stdlib sqlite3 单文件库，无 ORM 零新依赖）+ AI_HISTORY_DB_PATH 门控 + PipelineResult 扩展 summary/error + ingest_ocr 记录钩子 + GET /admin/ai/history + /admin/ai 页面新增历史卡片（混排 + 状态色芯片 + 可展开详情）+ compose 增可写卷 + AGENTS.md 反模式窄化例外
---

# Draft: admin-request-history

## Components (topology ledger)

| id | outcome | status | evidence |
| --- | --- | --- | --- |
| C1 | 历史存储：内存环形缓冲（容量可配，重启即失） | active | app/pipeline.py:105-107（dedup dict 先例）、docker-compose.yml:17（:ro 阻断文件持久化） |
| C2 | 数据采集：PipelineResult 补 summary + error；路由记录钩子（成功/失败/重复/503 四路径） | active | app/pipeline.py:34-40,175-188,201-213,259-272; app/main.py:292-347 |
| C3 | 历史读取 API：GET /admin/ai/history（鉴权镜像现有 admin 路由） | active | app/main.py:449-469（ai_test_dry_run 鉴权模板）、:537-596（AI_DISABLED 404 模式） |
| C4 | 前端历史卡片：混排时间线 + 状态色芯片 + 宽度自适应布局（meta 行 + 可展开详情） | active | app/static/admin.html:166-201; admin.js:442-492（renderOcrFlowResult 渲染先例）; admin.css:20-24（status-tag 色板）,216-227 |
| C5 | 测试 + 双语文档 + AGENTS.md 更新 | active | test/unit/test_webhook_ai.py（mock 范式）; docs/ai-pipeline.md; conftest.py |

## Findings (cited - path:lines)

1. **服务无持久化先例且明确禁止**：AGENTS.md 反模式"Do not introduce DB/ORM/queue deps — state is in-memory (token cache + registry snapshot + AI dedup dict) by design"；dedup 在内存 dict + TTL（app/pipeline.py:105-106）。
2. **Docker runtime 卷只读**：docker-compose.yml:17 `./runtime:/runtime:ro` → 文件式历史落 runtime/ 会被 RUNTIME_READONLY 409 拦截（main.py:1171-1188 同类处理）。文件持久化需改 compose 增加可写卷。
3. **PipelineResult 现有 5 字段**（pipeline.py:34-40）：ai_status/bill_record_id/warnings/extracted/dedup_hit —— 无耗时、无 summary、无错误原因。extract 耗时在 :190 算出但只进日志不返回。
4. **失败路径吞错误**：extract 失败（:175-188）/encode 失败（:201-213）/create 失败（:259-272）均返回 warnings=[]（extract 路径）或不含 exc 文本 —— 失败原因只进日志。顶层 except（:120-130）同理。
5. **精简原始数据不在响应里**：`extracted` 是 4 键通知 dict（amount/category/flow_type/description，:292-308 `_build_extracted`）；summary 字符串只写在 extract_fields[summary_field] 内部（:220-228），不出现在 PipelineResult。
6. **webhook 路由是唯一 AI 记账入口**：ingest_ocr（main.py:163-347）AI 阶段在 :292-347，pipeline.run 调用点 :320-325；registry 503 分支 :307-319（失败于 pipeline 之前）。401/422 请求无 AI 阶段。
7. **admin 鉴权模板**：ai_test_dry_run（main.py:466-469）= CONFIG_RELOAD_TOKEN 未设 404 RELOAD_DISABLED + compare_digest 401；AI_DISABLED 404 形状 :472-479。GET /admin/ai/profile 的 registry fail-closed 200 降级模式（:574-582）也可参考。
8. **前端渲染先例**：renderOcrFlowResult（admin.js:442-492）kv 行 + details 原始 JSON；el() 构造 + textContent 铁律（admin.js:93-111）；status-tag 四色（admin.css:20-24）；env-details 折叠样式（admin.css:198-205）可复用为条目展开。
9. **测试基建**：mock pipeline 范式 test_webhook_ai.py:36-90（AsyncMock PipelineResult + _mock_registry）；页面 grep 门 test_admin_page.py（id 断言）+ test_admin_bill_flow.py:586-620（函数声明 grep）+ node --check（test_admin_page.py:134-153）；conftest.py 钉 AI_ENABLED=false。
10. **Settings 模式**：config.py:101-110 `_int_env`；.env.example AI 段注释风格（:20-38）。
11. **模型名来源**：settings.ai_model（config.py:151,184）；路由内记录时取当时值即可（重启才变）。
12. **文档双语镜像铁律**：AGENTS.md conventions——docs/ 与 docs/en/ 同步改。
13. **隐私现状**：日志明令不打 original_text（pipeline.py:13 docstring + app/AGENTS.md 反模式）；用户要求 UI 展示原文 —— admin token 门控的内存视图是用户明确要求，属日志规则之外的授权例外（不进日志、不落盘）。

## Decisions (with rationale)

1. **混排单列表（用户委托决策）**：时间倒序混排成功/失败/重复，状态芯片着色（✓绿/✗红/⏭橙）—— 时间线调试视角最自然；分两个区域会把时间线打碎。用户原话"你自己决策一下"。
2. **宽度自适应布局（用户第二诉求）**：短字段（时间/状态/模型/耗时/金额分类摘要）组成固定 min-width 的 flex meta 行；长文本（OCR 原文/精简原始数据/失败原因/警告）收进每条目 `<details>` 展开块，全宽 pre-wrap。避免巨型表格列宽互挤。
3. **记录点 = ingest_ocr 路由**（pipeline.run 返回后 + registry 503 分支）：路由层有 request_id/耗时/original_text/model 全套上下文；pipeline 保持纯编排。401/422 无 AI 阶段不记录。
4. **失败原因需数据通路**：PipelineResult 末尾追加 `summary: str | None = None` + `error: str | None = None`（frozen slots 默认值，全部现有构造兼容）；4 个失败路径填 error；成功路径填 summary=extraction.summary。不进日志、不进 webhook 响应（契约零变更），仅历史消费。
5. **耗时 = AI 阶段墙钟**：路由内 monotonic 包住（registry reload + pipeline.run），含 dedup 命中（≈0ms，诚实反映）。
6. **容量默认 200 条 + env 可调**：`AI_HISTORY_MAX_ENTRIES`（_int_env 默认 200）；上限内存 ≈ 200×32KB ≈ 6.5MB 最坏情况，可接受。
7. **历史 API**：GET /admin/ai/history → `{items: [...倒序], capacity}`；字段 ts/ts_iso(Asia/Shanghai)/request_id/model/elapsed_ms/ai_status/error/bill_record_id/original_text/summary/extracted/warnings。AI_ENABLED=false → 404 AI_DISABLED。无分页（个人工具，一次拉全量）。
8. **不做**：webhook 响应不加 ai_error（契约面零增长）；无轮询（手动刷新按钮）；密钥/原文不进日志不变。

## Open questions

- （已裁决）Q1 持久化档位：用户选择**持久化**并采纳推荐——**stdlib sqlite3 单文件库**（非 JSONL、非 DB 服务器）。理由：崩溃安全（原子写入）+ 原生查询（倒序/筛选）+ 零新依赖（sqlite3 是 Python 标准库，不进 requirements.txt）+ 与 JSONL 的部署改动相同（都要可写卷）。AGENTS.md 反模式「Do not introduce DB/ORM/queue deps」需窄化例外：stdlib sqlite3、单文件、无 ORM、仅历史用途。

## Scope IN

- app/history_store.py 新模块（HistoryStore：sqlite3 连接 + WAL 模式 + 建表 + insert/list_recent；线程锁串行化写；失败降级为 no-op + 日志，绝不影响 webhook 主链路）
- Settings: ai_history_db_path（AI_HISTORY_DB_PATH，_path_env；None = 历史功能关闭）+ .env.example 注释
- PipelineResult +summary/error；pipeline 4 失败路径 + 2 成功路径填充
- ingest_ocr 四路径记录（succeeded / failed / duplicate / registry-503-as-failed）
- GET /admin/ai/history（鉴权 + AI_DISABLED 404 + 历史未配置 404 HISTORY_DISABLED + 倒序 items + limit 参数默认 200）
- 前端：admin.html 历史卡片（id=history-section 等）+ admin.js loadHistory/renderHistory + admin.css 样式（meta 行 flex + details 展开块 + 状态芯片）
- docker-compose.yml：./data:/data 可写卷（历史库默认路径建议 /data/history.db）
- AGENTS.md：反模式窄化例外 + 结构/CodeMap 更新
- 测试：Gherkin→测试→实现（TDD）；store 单测（tmp_path 真库）/ route 测试（mock pipeline 范式）/ grep 门 + node --check / PipelineResult 兼容性回归
- 文档：docs/ai-pipeline.md + docs/en/ai-pipeline.md「请求历史」节（配置、隐私边界、运维查看方式）+ README 两镜像段

## Scope OUT (Must NOT have)

- 不改 WebhookRequest / WebhookSuccessResponse（webhook 契约零变更；响应不加 ai_error）
- 不引第三方依赖（sqlite3 = 标准库；无 ORM/无 alembic/无 DB 服务器）；历史库写失败绝不 5xx webhook
- 不进日志：original_text/summary 不写 logger（沿用现有铁律）
- 不做分页 UI/轮询/自动刷新（手动刷新按钮 + limit 参数）
- 不记录 401/422/无 AI 阶段请求
- 不做前端框架/构建（纯 vanilla，沿用三文件）
- 密钥（AI_API_KEY 等）永不进历史条目
- 历史库损坏/锁死不得拖垮 webhook 主链路（所有 store 调用 try/except 吞掉）

## Approval gate

status: approved（用户 2026-10-12 明示"确认"——最终简报方案：sqlite3 单文件持久化 + AI_HISTORY_DB_PATH 门控 + compose 可写卷）
plan_written: true
metis_receipt: ses_ed142e2aaffeMKQe9cfm9SNCMV（2026-10-12，2 BLOCKER / 6 MAJOR / 2 MINOR 全部折叠：①存留容量契约冲突→改 limit 查询上限+存储不设限写入 Scope/TD;②data/ 缺 gitignore→todo 5;③sqlite 阻塞事件循环→to_thread+busy_timeout 进 Must-4/Must-1;④初始化语义未定→degraded 模式定义进 Must-1;⑤error 泄漏面→顶层 except 固定文案+隐私断言;⑥conftest 未钉 AI_HISTORY_DB_PATH→todo 6①;⑦schema 版本戳→user_version=1 进 Must-1;⑧innerHTML 门→todo 4 断言;⑨limit 边界/空态→todo 3;⑩计时起点/时区→monotonic+ts_iso 双字段;⑪PipelineResult 位置构造回归→todo 2;⑫Gherkin 确认点→预授权记入 Verification strategy）
next: 交付计划摘要，问启动方式（start-work / 高精度审查）
