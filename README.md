# feishu-webhook-booking

一个面向 iPhone Shortcuts + 飞书多维表格的轻量 FastAPI webhook 桥接器。

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
6. 飞书现有自动化继续生成：
   - `记录时间`
   - `精简原始数据`
   - `账单明细`

这个服务**不负责**：

- 解析账单字段
- 生成 `记录时间`
- 生成 `精简原始数据`
- 写 `账单明细`
- 让客户端发送飞书凭据或内部标识

## 文档导航 / Docs

- [运行时配置](docs/runtime-config.md)
- [架构说明](docs/architecture.md)
- [Shortcut 配置](docs/shortcut-setup.md)
- [飞书配置](docs/feishu-setup.md)
- [排障指南](docs/troubleshooting.md)
- [发布核对](docs/release-checklist.md)
- [迁移复盘](docs/retrospective.md)
- [部署总览](docs/deployment/README.md)

英文对应文档都放在 [docs/en/](docs/en/)。

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

动态目标文件模板在 [runtime/feishu-targets.toml.example](runtime/feishu-targets.toml.example)。

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
