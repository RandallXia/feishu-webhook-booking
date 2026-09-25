# feishu-webhook-booking

一个面向 [iPhone Shortcuts](https://www.icloud.com/shortcuts/d3e8e955242a49e29348cc3dd21a59e4) + [飞书多维表格](https://my.feishu.cn/base/EZnpbAQ7PannZCsydKtcErmDn0c?table=tblsigCGVKezoL7M&view=vewBy9BPY3)的轻量 FastAPI webhook 桥接器。

## 语言切换 / Language

- 中文（默认）: [README](README.md)
- English: [English README](docs/en/README.md)

## 项目做什么

这个服务只负责一条很窄的链路：

1. iPhone Shortcuts 对截图做 OCR
2. Shortcut 把原始文本发给这个 webhook 服务
3. 服务校验共享 token
4. 服务根据 `book_alias` 或 `year` 解析目标账本
5. 服务调用飞书 Open API，更新目标记录里的 `原始信息`
6. 当 `AI_ENABLED=true` 时，本服务接管后续步骤：AI 提取账单字段 → 写回 `精简原始数据` → 创建 `账单明细` 记录
   当 `AI_ENABLED=false`（默认）时，飞书现有自动化继续生成上述字段

这个服务**不负责**：

- 让客户端发送飞书凭据或内部标识
- 解析账单字段（仅 `AI_ENABLED=false` 时；开启后由自托管 AI 管线接管）

账单字段提取链路详见 [AI 提取管线](docs/ai-pipeline.md)。

## 文档导航 / Docs

- [运行时配置](docs/runtime-config.md)
- [架构说明](docs/architecture.md)
- [Shortcut 配置](docs/shortcut-setup.md)
- [飞书配置](docs/feishu-setup.md)
- [AI 提取管线](docs/ai-pipeline.md)
- [排障指南](docs/troubleshooting.md)
- [发布核对](docs/release-checklist.md)
- [迁移复盘](docs/retrospective.md)
- [部署总览](docs/deployment/README.md)

英文对应文档都放在 [docs/en/](docs/en/)。

## AI 提取管线 / AI extraction pipeline

当 `AI_ENABLED=true` 时，服务在写入 `原始信息` 之后接管飞书原「AI 自动填充」的职责：用自托管 AI 模型对 OCR 文本做一次结构化提取，回写 `精简原始数据`，并在「账单明细」表新建一条记录。配额由你自己的 AI 供应商管理，不再受飞书月度上限限制。配置可通过网页界面完成（`/admin/ai`），详见 [AI 提取管线文档](docs/ai-pipeline.md) 第 12 节。

When `AI_ENABLED=true`, the service takes over Feishu's "AI auto-fill" after writing `原始信息`: a single structured AI call extracts bill fields from the OCR text, writes back `精简原始数据`, and creates a new record in the 账单明细 table. Quota is governed by your own AI provider, not Feishu's monthly cap. Configuration can be done via the web UI (`/admin/ai`); see section 12 of the [AI pipeline docs](docs/ai-pipeline.md).

详细配置、迁移清单、TOML schema、管理端点见 [docs/ai-pipeline.md](docs/ai-pipeline.md)（英文：[docs/en/ai-pipeline.md](docs/en/ai-pipeline.md)）。

## 运行模式

当前仓库支持两种运行模式：

1. **动态选择器模式**：配置 `FEISHU_TARGETS_FILE`，请求通过 `book_alias` 或 `year` 选择目标
2. **旧版兼容模式**：不配置 `FEISHU_TARGETS_FILE`，继续使用 `FEISHU_APP_TOKEN` / `FEISHU_TABLE_ID` / `FEISHU_RECORD_ID`

推荐长期使用动态模式。

## API

### `GET /health`

健康检查接口。

### `POST /v1/webhook/ocr`

请求头：

- `Content-Type: application/json`
- `X-Webhook-Token: <shared-secret>`
- 可选 `X-Request-Id`

请求体示例：

```json
{
  "original_text": "OCR extracted text from the screenshot",
  "source": "ios-shortcuts",
  "raw_ocr": "raw ocr text",
  "book_alias": "2026"
}
```

也可以使用 `year` 代替 `book_alias`。
请求体**不要**包含 `app_token`、`table_id`、`record_id` 或 `FEISHU_APP_SECRET`。

### `POST /admin/config/reload`

可选的管理端 reload 接口，仅在配置了 `CONFIG_RELOAD_TOKEN` 时启用。

## 运行时配置

需要的服务级配置：

- `WEBHOOK_SHARED_TOKEN`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

动态模式配置：

- `FEISHU_TARGETS_FILE`
- `FEISHU_TARGET_RELOAD_INTERVAL_SECONDS`
- `CONFIG_RELOAD_TOKEN`（可选）

AI 提取管线配置（可选，默认关闭）：

- `AI_ENABLED`（默认 `false`；开启后 `AI_PROVIDER` / `AI_API_KEY` / `AI_MODEL` / `AI_PROFILE_FILE` 变为必填）
- 其余 `AI_*` 变量及 `AI_BASE_URL` 拼接规则见 [docs/ai-pipeline.md](docs/ai-pipeline.md)

动态目标文件模板在 [runtime/feishu-targets.toml.example](runtime/feishu-targets.toml.example)。
AI profile 模板在 [runtime/ai-profile.toml.example](runtime/ai-profile.toml.example)。

## 本地启动

```bash
pip install -r requirements-dev.txt
cp .env.example .env
cp runtime/feishu-targets.toml.example runtime/feishu-targets.toml
uvicorn app.main:app --host 0.0.0.0 --port 2398 --reload
```

## Docker / Compose

```bash
docker compose up -d --build
```

Compose 会挂载 `./runtime`，并分别读取：

- `./runtime/feishu-webhook.env`
- `./runtime/feishu-targets.toml`

## 语言对应 / Language mapping

- 中文文档：当前仓库默认路径
- 英文文档：`docs/en/`

如果你想先看英文版，直接从 [English README](docs/en/README.md) 进入即可。
