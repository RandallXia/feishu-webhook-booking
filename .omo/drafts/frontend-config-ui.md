---
slug: frontend-config-ui
status: approved
intent: clear
review_required: false
plan_path: .omo/plans/frontend-config-ui.md
pending-action: auto-start execution (user explicitly authorized: 审查完成落盘后自动开始实施)
approach: 完整前端配置编辑器（URL 粘贴解析端点 + 表/字段/记录选择器 + 字段映射自动推导预填 + 配置写回 API validate→原子写→强制重载 + alias 完整 CRUD + env 行式编辑 AI 连接）；零新运行时依赖（手写 TOML 序列化器）
---

# Draft: frontend-config-ui

## Decisions (with rationale)

用户决策门裁决（hyperplan 第二轮，2026-08-29）：
1. **完整编辑器（live-save）**——用户原话"必须回到完全前端功能"；minimal（纯生成器）与 creative（纯向导）的砍需求立场被 owner 否决，但向导式自动推导被吸收为编辑器的预填充
2. **AI 连接可编辑 + 重启提示**——env 行式读写，密钥 write-only
3. **完整 alias CRUD**——按年记账模式需要每年新增账本
4. **v1 仅 /base/ 链接**——wiki 解析需额外 scope，报错提示改用 base 链接

对抗轮裁决：
5. 记录选择器走 list records API（POST /records/search）——事实核查击杀"粘贴记录链接"（不透明 token 无解析 API）
6. 手写 TOML 序列化器（app/toml_writer.py）——不引 tomli_w/tomlkit 第 5 依赖；注释丢失接受 + .bak
7. 校验升级：validate_profile_candidate 两张表都拉 list_fields——修复现有"summary_field 不在提取表"隐性缺口（保存成功但 webhook 静默失败）
8. URL 解析放后端（POST /admin/feishu/parse-url）——Metis Finding 15：JS 正则 pytest 不可测
9. generation 守卫防并发写；asyncio.Lock 串行化 save 流程
10. XSS 铁律：Feishu 来源字符串一律 textContent
11. JS 语法门 = node --check（硬性）；函数存在性 = grep 断言——Metis Finding 13/16：目检不可执行

## Review receipts

- Metis gap analysis（2026-08-29，ses_fb1d1dd3fffeT4VtDeYd9UcMLz）：18 findings（3 BLOCKER / 9 MAJOR / 6 MINOR）全部折叠——①BLOCKER todo3 提取表三元组改显式参数（Wave 1 可单测）②BLOCKER records/search 请求体形状先核对文档不预设空 body ③BLOCKER JS 语法门 node --check 硬性 ④generation 键名不对称注明（describe()=reload_generation）⑤env 值引号规则对齐 config.py:36 加载器 ⑥tmp 同目录（:ro 时创建即败→统一 409）⑦ProfileConfigError 消息 golden 逐字节不变 ⑧todo8 定性为整体迁移 ⑨8 行映射全必填（含 raw_source）⑩依赖矩阵补 4/5（行 9/10）⑪F4 grep 收窄排除 static ⑫文档验收改结构断言。
- 高精度双通道审查：未请求（用户指令为审查完成即实施；Metis 已覆盖缺口分析）。

## Approval gate
status: approved（用户 2026-08-29 明示："计划审查完成，落盘之后，自动开始实施"——跳过交付问询，直接进入执行）
