# 架构说明 / Architecture

## 语言切换 / Language

- 中文（默认）: [本页](architecture.md)
- English: [Architecture](en/architecture.md)

This service is a narrow FastAPI webhook bridge between iPhone Shortcuts OCR and Feishu Bitable.

当前稳定链路：

1. Shortcut performs OCR on a screenshot
2. User may review or correct the OCR result in Shortcut
3. Shortcut sends `POST /v1/webhook/ocr`
4. Service validates `X-Webhook-Token`
5. Service resolves the target book by `book_alias` or `year`
6. Service fetches or reuses a Feishu tenant access token
7. Service updates the selected fixed record field `原始信息`
8. Existing Feishu automation continues to generate follow-up fields

第 8 步仅在 `AI_ENABLED=false`（默认）时成立；`AI_ENABLED=true` 时由本服务的 AI 提取管线接管后续字段生成，见下文「AI 提取管线（可选）」。

## 职责边界

本服务刻意只做三件事：

- 鉴权 webhook 请求
- 解析一个服务端预配置的飞书目标
- 把 `原始信息` 写入该目标记录

本服务**不会**每次请求都创建新记录，也不会允许客户端提供飞书敏感凭据。

账单字段解析按 `AI_ENABLED` 分两种情况：

- `AI_ENABLED=false`（默认）：本服务不解析账单字段，由飞书侧自动化生成后续字段
- `AI_ENABLED=true`：由自托管 AI 管线接管账单字段解析（提取 → 写回 `精简原始数据` → 创建 `账单明细`），见下文「AI 提取管线（可选）」

## 运行时配置模型

当前配置模型分为两层：

### 1. 静态服务配置

由 `app/config.py` 按如下顺序加载：

1. 进程环境变量
2. `FEISHU_ENV_FILE` 指向的外部 env 文件
3. 项目根目录 `.env`

包含这些值：

- `WEBHOOK_SHARED_TOKEN`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_BASE_URL`
- 动态模式下的 `FEISHU_TARGETS_FILE`
- `FEISHU_TARGET_RELOAD_INTERVAL_SECONDS`
- `CONFIG_RELOAD_TOKEN`
- `HOST`
- `PORT`
- `LOG_LEVEL`
- `HTTP_TIMEOUT_SECONDS`
- `TOKEN_REFRESH_SKEW_SECONDS`
- AI 管线变量（可选，默认关闭）：`AI_ENABLED`，以及开启后必填的 `AI_PROVIDER` / `AI_API_KEY` / `AI_MODEL` / `AI_PROFILE_FILE`，可选的 `AI_BASE_URL` / `AI_TIMEOUT_SECONDS` / `AI_FORCE_TOOL_CALL` / `AI_DEDUP_TTL_SECONDS` / `AI_PROFILE_RELOAD_INTERVAL_SECONDS`，完整语义见 [AI 提取管线](ai-pipeline.md)

### 2. 动态目标注册表

由 `app/target_registry.py` 从 `FEISHU_TARGETS_FILE` 指向的 TOML 文件中加载。

每个目标只包含与路由目标相关的值：

- `app_token`
- `table_id`
- `record_id`
- `original_field_name`
- `enabled`
- 可选 `year`

公开请求中只允许携带安全选择器，例如 `book_alias` 或 `year`。

### 旧版兼容模式

如果没有配置 `FEISHU_TARGETS_FILE`，服务会退回到单一固定 env 目标模式。

旧版固定目标配置：

- `FEISHU_APP_TOKEN`
- `FEISHU_TABLE_ID`
- `FEISHU_RECORD_ID`
- `FEISHU_ORIGINAL_FIELD_NAME`

## 请求契约

`POST /v1/webhook/ocr` 请求体：

```json
{
  "original_text": "OCR extracted text from the screenshot",
  "source": "ios-shortcuts",
  "raw_ocr": "raw ocr text",
  "book_alias": "2026"
}
```

支持的选择器字段：

- `book_alias`
- `year`

规则：

- 如果两个选择器都未提供，则使用 `default_alias`
- 如果两个选择器同时存在但冲突，则拒绝请求
- `app_token`、`table_id`、`record_id`、`app_secret` 等额外字段一律禁止

## Token 与客户端行为

`app/feishu_client.py` 将飞书 tenant token 缓存在服务层。

原因：

- 第一版实现中，`FEISHU_APP_ID` 与 `FEISHU_APP_SECRET` 仍保持全局
- 年度切换通常改变的是记录/表/应用 token 目标，而不是应用凭据本身

每次请求先解析一个目标，再基于该目标构建飞书更新 URL。

## 动态重载模型

服务支持两种目标模式：

- **legacy 模式**：没有 `FEISHU_TARGETS_FILE`，直接使用 env 中的固定目标
- **dynamic 模式**：从外部 TOML 注册表加载多个目标

动态模式重载路径：

- 请求路径会通过文件 mtime 和最小间隔调用 `maybe_reload()`
- 可选的 `POST /admin/config/reload` 可以强制立即刷新
- 如果变更后的注册表非法，服务会采用 fail-closed 策略，阻止写入直到修复

### 注册表约束

当前解析器会强制要求：

- `default_alias` 必须存在
- 默认目标必须启用
- alias 名称必须满足安全格式
- 每个目标的必填字段不能为空
- 如果声明了 year，则 year 不能重复

## 时序图

```mermaid
sequenceDiagram
    participant Shortcut as iPhone Shortcut
    participant API as FastAPI webhook
    participant Registry as TargetRegistry
    participant Feishu as Feishu Open API
    participant Bitable as Feishu Bitable

    Shortcut->>API: POST /v1/webhook/ocr\nX-Webhook-Token\noriginal_text + book_alias/year
    API->>API: Validate webhook token
    API->>Registry: maybe_reload()
    Registry-->>API: current target snapshot
    API->>Registry: resolve(book_alias/year)
    Registry-->>API: FeishuTargetConfig
    API->>Feishu: get/reuse tenant access token
    Feishu-->>API: tenant_access_token
    API->>Bitable: update target record 原始信息
    Bitable-->>API: update success
    API-->>Shortcut: success + request_id + record_id + book_alias
```

## AI 提取管线（可选）

当 `AI_ENABLED=true` 时，服务在写入 `原始信息` 之后追加一段 AI 管线：

1. 去重检查（内存 `sha256(alias:original_text)`，TTL 内重复请求直接返回 `ai_status="duplicate"`）
2. `AiExtractor` 对 OCR 文本做一次结构化提取（anthropic / openai 双协议，强制 tool_call）
3. `encode_fields` 按 profile 白名单把提取结果编码为飞书字段，未命中选项回落到 `fallback`
4. 写回目标记录的 `精简原始数据`（summary_field，尽力而为）
5. 在账单明细表 `create_record` 新建一条记录（`client_token` 幂等）

AI 管线永不抛出异常：失败只会体现在响应的 `ai_status="failed"`、`ai_warnings` 与日志中，不影响已完成的 `原始信息` 写入。唯一的 AI 侧 5xx 是 profile 注册表 fail-closed 时的 `503 AI_PROFILE_UNAVAILABLE`。配置与 TOML schema 见 [AI 提取管线](ai-pipeline.md)。

### AI 阶段扩展时序图

```mermaid
sequenceDiagram
    participant Shortcut as iPhone Shortcut
    participant API as FastAPI webhook
    participant Registry as TargetRegistry
    participant Feishu as Feishu Open API
    participant Bitable as Feishu Bitable
    participant Pipe as AiPipeline
    participant AiReg as AiProfileRegistry

    Shortcut->>API: POST /v1/webhook/ocr\nX-Webhook-Token\noriginal_text + book_alias/year
    API->>API: Validate webhook token
    API->>Registry: maybe_reload()
    Registry-->>API: current target snapshot
    API->>Registry: resolve(book_alias/year)
    Registry-->>API: FeishuTargetConfig
    API->>Feishu: get/reuse tenant access token
    Feishu-->>API: tenant_access_token
    API->>Bitable: update target record 原始信息
    Bitable-->>API: update success
    Note over API,Pipe: 以下 AI 阶段仅 AI_ENABLED=true 时执行
    API->>AiReg: maybe_reload() + get_snapshot()
    AiReg-->>API: AiProfileSnapshot (profile + whitelists)
    API->>Pipe: run(original_text, target, profile, whitelists)
    Pipe->>Pipe: dedup check sha256(alias:original_text)
    Pipe->>Pipe: AiExtractor structured extraction (tool_call)
    Pipe->>Pipe: encode_fields (whitelist + fallback)
    Pipe->>Bitable: update 精简原始数据 (summary_field)
    Bitable-->>Pipe: update success
    Pipe->>Bitable: create_record 账单明细 (idempotent client_token)
    Bitable-->>Pipe: record created
    Pipe-->>API: PipelineResult(ai_status, bill_record_id, warnings)
    API-->>Shortcut: success + record_id + book_alias + ai_status + ai_record_id
```

## 安全边界

关键边界：

- Shortcut 只持有 webhook token 和业务载荷
- 服务端持有飞书敏感凭据和目标注册表
- 镜像中只包含代码与依赖

这样既能保持开源部署模型不退化，又能支持动态账本选择。
