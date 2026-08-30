---
slug: ai-bill-extraction-pipeline
status: approved
intent: clear
review_required: true
plan_path: .omo/plans/ai-bill-extraction-pipeline.md
plan_sha256: 6b830f204457ebb2ca2b0865b907115ea6c7e2bba30aca43dcfe141e93751d54
review_round_id: rev3-20260828-aibill
round_status: approved
pending-action: deliver handoff (plan approved by dual high-accuracy review; execution belongs to worker session via /start-work)
approach: 自建 AI 提取管线（单次结构化调用，anthropic/openai 双协议，零新依赖）替代飞书 AI 配额依赖的自动填充；同步内联执行 + 20s 超时；TOML 配置热重载；最小前端（只读配置页 + prompt 干跑）；内存 TTL 去重 + client_token 幂等后备
---

# Draft: ai-bill-extraction-pipeline

## Components (topology ledger)
<!-- Lock the SHAPE before depth. One row per top-level component that can succeed or fail independently. -->
<!-- id | outcome (one line) | status: active|deferred | evidence path -->

| id | outcome | status | evidence |
|---|---|---|---|
| C1 ai-extractor | 双协议结构化提取适配器（httpx，零新依赖）产出 7 字段提取结果 | active | app/ai_extractor.py（计划新增） |
| C2 field-codec | AI 输出→飞书字段编码：单选白名单校验防污染、日期 Asia/Shanghai 毫秒时间戳、软回退 | active | app/field_codec.py（计划新增） |
| C3 feishu-ext | FeishuClient 扩展：create_record（client_token 幂等）+ list_fields（白名单数据源）+ update_record_field | active | app/feishu_client.py:25-105 |
| C4 pipeline | 编排：写回精简数据→建账单记录；TTL 内存去重；失败语义（AI 失败→200+failed） | active | app/pipeline.py（计划新增） |
| C5 profile-registry | TOML 配置热重载（克隆 TargetRegistry 模式，async 因需拉白名单） | active | app/ai_profile.py（计划新增） |
| C6 admin-surface | POST /admin/ai/test 干跑 + GET /admin/ai/profile + 只读配置页 | active | app/main.py, app/static/（计划新增） |
| C7 docs-migration | 双语文档 + 停用旧飞书 AI 自动化迁移清单 + AGENTS.md 反模式条款重写 | active | docs/, docs/en/, AGENTS.md |

## Open assumptions (announced defaults)
<!-- Record any default you adopt instead of asking, so the user can veto it at the gate. -->
<!-- assumption | adopted default | rationale | reversible? -->

| assumption | adopted default | rationale | reversible? |
|---|---|---|---|
| AI 调用协议形态 | 双协议均走 tool/function calling（非 response_format json_schema） | 对第三方 relay 端点兼容性最好 | 可逆 |
| 精简数据写回失败 | 记 warning 后继续建账单（账单是主目标，精简数据是审计线索） | 单点失败不阻断主链路 | 可逆 |
| 日期解析失败 | 回退为当天（Asia/Shanghai）+ warning | 无日期账单比错日期更糟；月度人工复核兜底 | 可逆 |
| AI 阶段无重试 | 单次调用（20s 超时封顶，重试会撞 Shortcut ~25s 超时） | 延迟预算硬约束 | 可逆 |
| ai-profile.toml 配置粒度 | 连接配置（provider/base_url/key/model/timeout）= env；提取配置（字段/prompt/表映射）= TOML | 密钥不落 TOML；职责分离 | 可逆 |
| AI_PROFILE_FILE | AI_ENABLED=true 时必填 | 默认 profile 无法携带真实表 id | 可逆 |

## Findings (cited - path:lines)

- tenant_token 可列 table/field、建 record（bitable v1 API 全覆盖所需能力）— hyperplan 事实核查 R2
- 非 AI 自动化（发送 HTTP 请求）不消耗 AI 配额且仍在工作 — R6
- iOS Shortcut 超时 ~25s（非 30s）— 事实攻击轮修正
- Anthropic 结构化输出冷 schema 编译存在 33-48s 停滞尾延迟 — 事实攻击轮
- 飞书 create record API 原生支持 client_token 幂等写入 — 事实攻击轮
- 飞书单选字段写入未知选项会自动创建（选项污染，不可逆）— 批判轮验证
- 飞书 AI 配额每月重置 → 旧自动化可能复活双重写入 — 批判轮
- record 分享链接用不透明 token，解析不出 record_id；table URL 形如 /base/{app_token}?table={table_id} — 事实核查 R3
- 现有鉴权模式：X-Webhook-Token / X-Admin-Token + secrets.compare_digest — app/main.py:95-114, 232-264
- 配置错误语义：启动 fail-fast（lifespan load_initial raise），热重载错误 → config_valid=False → 503 — app/target_registry.py:162-172, app/main.py:154-166
- app/config.py 手写 env 模式（_require_env/_optional_env/_int_env/_path_env）— app/config.py:78-141
- test/ 与 requirements-dev.txt 当前被 gitignore（零测试是设计现状）；README 已引用 requirements-dev.txt — AGENTS.md NOTES, README.md 本地启动

## Decisions (with rationale)

用户决策门裁决（hyperplan Phase 6，2026-08-28）：

1. **前端：最小前端（只读配置页）+ 无 OAuth** — 用户初选"完整前端+OAuth"，经二轮确认改选最小前端；事实核查证实 tenant_token 零能力损失，OAuth 纯成本（5:0 批判轮共识）
2. **触发模式：同步内联 + 20s AI 超时兜底** — 用户选推荐项（4:0 共识）
3. **去重：内存 TTL hash（~5min，仅记成功）+ 飞书 client_token 后备** — 用户选推荐项
4. **AI 开关：env 全局开关 AI_ENABLED（默认 false）** — 用户选推荐项；请求体开关被批判轮否决（泄漏 token 可静默切换路由）
5. **实施顺序：垂直切片（端到端最小闭环先行）** — 用户选推荐项

对抗轮收敛裁决（lead 综合，Phase 4-5）：

6. 单次结构化 AI 调用（4:1，非逐字段 prompt — N 倍延迟/失败点 + 跨字段不一致）
7. TOML 宿主机编辑 + :ro 挂载 + mtime 热重载（4:0，克隆 target_registry 模式）
8. 失败语义混合：配置无效→503 fail-closed；AI 阶段失败→200 + ai_status:"failed"（原始信息已提交，非 2xx 诱导 Shortcut 重试→重复行）
9. 选项污染防护：list_fields 实时白名单 + 回退默认值，绝不裸写
10. 日期：毫秒时间戳按 Asia/Shanghai
11. URL 解析 key 降级为文档说明（record 链接不透明）
12. AGENTS.md "禁止账单逻辑"条款重写为有界新契约（AI 管线限定专属模块，webhook 契约不变）
13. UX 彩蛋：webhook 响应携带提取摘要 → Shortcut 可弹通知

## Scope IN

- 新分支 feature/ai-extraction-pipeline
- app/ai_extractor.py、app/field_codec.py、app/ai_profile.py、app/pipeline.py 四个新模块
- app/feishu_client.py：create_record + list_fields + update_record_field
- app/config.py：AI_* env 配置（8+1 项）
- app/main.py：AI_ENABLED 门控接线、响应扩展（exclude_none 保持旧契约）、POST /admin/ai/test、GET /admin/ai/profile、GET /admin/ai 配置页
- app/static/admin.html 只读配置页（vanilla，无构建）
- runtime/ai-profile.toml.example、.env.example/runtime env example 的 AI 段（顺带修复 30/10 默认值漂移）
- requirements-dev.txt + test/ 解封 + pytest 基建（TDD：Gherkin 骨架先行）
- 双语文档 docs/ai-pipeline.md + docs/en/ai-pipeline.md + README 段落 + 迁移清单
- AGENTS.md（根 + app/）更新

## Scope OUT (Must NOT have)

- 无 OAuth/扫码登录/用户级 token
- requirements.txt 保持 3 依赖不动（运行时零新依赖；仅 dev 依赖 pytest 系）
- 无 DB/sqlite/队列/持久化状态（内存 TTL 去重除外）
- 不逐字段独立 AI 调用
- 不加 webhook 请求体字段控制 AI（WebhookRequest 保持 extra="forbid" 原样）
- 配置页不写回任何文件（只读 + 干跑）
- 不处理记账日期字段（飞书公式从账单日期派生）
- 不提交真实 runtime 值
- 不解析 record 分享链接

## Open questions

无 — 全部分叉已裁决（见 Decisions）。

## Approval gate
status: approved
<!-- 用户于 2026-08-28 hyperplan Phase 6 决策门回答全部 5+2 个分叉后，经两次"继续"确认落盘。批准仅授权写计划文件，执行属独立 worker 会话（/start-work）。 -->

## Review receipts
- Metis gap analysis（2026-08-28，ulw-plan Phase 3 强制步，session ses_fb84df07fffeYuFnqht6e0htXS / bg_6ef68e1e）：18 findings（3 BLOCKER / 11 MAJOR / 4 MINOR）——已全部折叠入计划。关键修正：①conftest 钉死 FEISHU_TARGETS_FILE/FEISHU_ENV_FILE 空串（防 .env 翻转测试模式）②todo 4 依赖 todo 3（ExtractionResult import 硬依赖，矩阵与波次修正）③todo 10 冒烟改 AI_ENABLED=false（true 时 lifespan 打真实飞书）④F3 标注唯一人机协同门 + F1-F4 补验收行⑤asgi-lifespan 进 dev 依赖（ASGITransport 不触发 lifespan）⑥client_token 幂等重复语义双形态处理⑦registry 全 async 方法与 await 调用点清单⑧白名单仅 bill 表⑨todo 11 场景⑧节流绕过⑩记账日期公式假设标注。
- 高精度审查（用户于 Phase 4 交付问询中主动选择）：**round rev1-20260828-aibill 已初始化**，双通道并行在途——momus（launch-momus-rev1）+ 独立 oracle（launch-oracle-rev1），目标 .omo/plans/ai-bill-extraction-pipeline.md。
  工件绑定（降级声明）：本会话 planner 工具集无 shell，无法父侧预计算 SHA-256；改为**双审查者各自按 intake contract 计算并回显摘要，两通道摘要互配对**即为本轮 artifact_identity（不一致 → INCONCLUSIVE → 重开新轮）。父侧可验证锚点：总行数 257；首行 `# ai-bill-extraction-pipeline - Work Plan`；末行为 Success criteria 第 6 条（`6. **文档**[agent]：…AGENTS.md 反模式已重写为有界契约。`）。完成 CAS：两通道终局裁决均为无条件 OKAY 且回显摘要一致 + 锚点复核通过，方可在本 draft 记录 receipts 并交付。
  轮内状态（rev1-20260828-aibill，round_status: **terminal**——momus=approved / independent=changes_requested）：
  - momus 通道：launch_id=launch-momus-rev1，session=ses_fb8407ef5ffeE5Go1QzraX5m52（bg_edf96331），result=**OKAY（无条件通过）**，sha256=d41a9475d32419a4d8725ea5d1dbf91cda0a6522e59393958c8f2516f298a020
  - independent 通道：launch_id=launch-oracle-rev1，session=ses_fb8402c01ffee4r1Xi6a17l7zY（bg_61bee23d），result=**CHANGES REQUESTED**（1 BLOCKER + 2 MAJOR + 6 MINOR），sha256=d41a9475…（与 momus 一致，工件配对通过）
  - rev1 处置：oracle 9 项发现全部接受并已修复入计划——①[BLOCKER] 提取器 prompt 无状态化（AiExtractor(settings) + extract(text, prompt_header, field_prompts) 逐调用注入，todos 3/6/7/8/9 连锁修正，prompt 热重载闭环）②[MAJOR] conftest 钉死 AI_ENABLED=false + AI_PROFILE_FILE=""（防根 .env AI 泄漏）+ 回归锁 monkeypatch.delenv 双保险 ③[MAJOR] todos 6/8 增「锁内不得 await」不变量 ④summary_field 相等校验 ⑤白名单刷新操作说明 ⑥admin 端点 registry 失效→503 + CONFIG_RELOAD_TOKEN 前置文档 ⑦client_token 不支持时的已知限制处置 ⑧AI_BASE_URL 拼接规则文档 ⑨TDD 门扩展至 todo 2。
  - **计划文件已变更 → rev1 两通道作废，按修复-重审循环开启 rev2 全新双通道。**

  轮内状态（rev2-20260828-aibill，round_status: **terminal**——momus=approved / independent=changes_requested）：
  - momus 通道：launch_id=launch-momus-rev2，session=ses_fb82d8e79ffeKECsEuUZRqpEGp（bg_4268c54d），result=**OKAY（无条件通过）**，sha256=9ada43a180c434b3d5059f40271c8b33643c8e813ef75600bd235daf75a198ae（与 rev1 d41a9475… 不同 ✓）
  - independent 通道：launch_id=launch-oracle-rev2，session=ses_fb82d4f53ffeET69t2hW7MvdBd（bg_ee4affa1），result=**CHANGES REQUESTED**（仅 1 MINOR：GET /admin/ai/profile 于 registry fail-closed 态行为未指定——get_snapshot 会抛 → 裸 500 风险 / 503 丢失配置页诊断字段），sha256=9ada43a1…（与 momus 一致，工件配对通过）
  - rev2 处置：9 项 rev1 修复全部验证落地（oracle 逐项核对 ✓，momus 逐项核对 ✓）；rev2 唯一残留已修复——todo 8 增 `get_status()` 非抛出诊断访问器、todo 9 规定 GET profile fail-closed 态返回 200 降级体（profile/whitelists=null + get_status 填充 registry）+ 新增测试用例、todo 10 增降级态渲染说明。
  - **计划文件已变更 → rev2 两通道作废，开启 rev3 全新双通道。**

  轮内状态（rev3-20260828-aibill，round_status: **terminal — APPROVED**）：
  - momus 通道：launch_id=launch-momus-rev3，session=ses_fb825ec7effelSiINbP4oGCRab（bg_f52b8493），result=**OKAY（无条件通过）**，sha256=6b830f204457ebb2ca2b0865b907115ea6c7e2bba30aca43dcfe141e93751d54（≠ rev2 的 9ada43a1… ✓）
  - independent 通道：launch_id=launch-oracle-rev3，session=ses_fb825bb74ffeYRF2VhNmu4r3KK（bg_113ffb29），result=**OKAY（无条件通过）**，sha256=6b830f20…1d54（**与 momus 通道一致——工件配对通过**）
  - 交付前活体复核（planner，降级验证）：总行数 257 ✓、首行 ✓、末行 ✓——与批准轮锚点一致，审查后无漂移。
  - **高精度审查完成判定成立**：双通道回执齐备、双终局裁决均为无条件 OKAY、双通道摘要互配对、活体验证通过。
  - 审查历程：rev1（momus OKAY / oracle CHANGES 9 项）→ 全折叠 → rev2（momus OKAY / oracle CHANGES 1 项）→ 折叠 → rev3（双双 OKAY）。共消化 10 项发现（1 BLOCKER + 3 MAJOR + 6 MINOR）。
  - rev3 工件锚点：总行数 257；首行 `# ai-bill-extraction-pipeline - Work Plan`；末行 Success criteria 第 6 条（锚点同 rev1/rev2；内容已再变更——摘要须与 rev2 的 9ada43a1… 不同且双通道互配对）。
  - 终局迁移规则：同前轮（绑定一致完成包才接受；中断 → 整轮 INCONCLUSIVE + 重开；计划再改动 → 两通道齐失效 + 重开 rev4）。
  - rev2 工件锚点：总行数 257；首行 `# ai-bill-extraction-pipeline - Work Plan`；末行 Success criteria 第 6 条（同 rev1 锚点，内容已变更——摘要须与 rev1 不同且双通道互配对）。
  - 终局迁移规则：同 rev1（仅接受绑定一致的完成包；任一通道中断 → 整轮 INCONCLUSIVE + 重开；计划再改动 → 两通道齐失效 + 重开 rev3）。
