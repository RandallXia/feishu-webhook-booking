# Shortcut setup / 快捷指令配置

## 语言切换 / Language

- 中文（默认）: [本页](shortcut-setup.md)
- English: [Shortcut setup](en/shortcut-setup.md)

## 适用范围

本文只聚焦 iPhone Shortcut 里“发送 webhook”这一段。

请保留你现有的菜单、版本检查和其他跳转逻辑，不要顺手重构整条 Shortcut。

## 当前请求契约

当前 webhook 入口：

- `POST /v1/webhook/ocr`

必需请求头：

- `X-Webhook-Token`

当前 JSON 字段：

- `original_text`
- `source`
- `raw_ocr`
- 可选 `book_alias`
- 可选 `year`

**不要**在 Shortcut 里发送飞书敏感凭据或内部标识。

绝不要发送：

- `FEISHU_APP_SECRET`
- `app_token`
- `table_id`
- `record_id`

## 推荐链路

推荐用户链路：

1. 对截图做 OCR
2. 让用户人工确认或修正文案
3. 组装 webhook 请求体
4. 发送到公网 webhook 地址
5. 解析 JSON 返回结果并提示成功消息

## 到底改哪一段

只改 webhook 发送附近的动作。

建议重点检查这些动作：

- 存 webhook URL 的 `Text` 动作
- `Get Contents of URL`
- 组装 JSON body 的动作
- 解析返回值的动作

## webhook 地址

把你的真实公网地址填进保存 URL 的动作里。

例如：

```text
https://hook.example.com/v1/webhook/ocr
```

如果你用的是 NAS、Windows 主机或其他自托管机器，优先使用稳定的 HTTPS 公网地址。

## 为什么删除 `Current Date` / `Format Date`

当前服务不再需要 Shortcut 负责生成时间字段。

原因：

- webhook 只需要原始 OCR 业务文本
- 飞书自动化仍然负责后续衍生字段
- 继续在 Shortcut 中生成时间只会增加耦合和混乱

所以，如果你的旧 Shortcut 还在组装 `time` 或类似字段，请把那段删掉。

## 配置 `Get Contents of URL`

推荐请求设置：

- Method: `POST`
- Headers:
  - `Content-Type: application/json`
  - `X-Webhook-Token: <your real token>`
- Request Body: JSON

### JSON body 示例

```json
{
  "original_text": "OCR extracted text",
  "source": "ios-shortcuts",
  "raw_ocr": "OCR extracted text",
  "book_alias": "2026"
}
```

如果你更偏好按年份路由，也可以发送：

```json
{
  "original_text": "OCR extracted text",
  "source": "ios-shortcuts",
  "raw_ocr": "OCR extracted text",
  "year": 2026
}
```

如果你同时省略 `book_alias` 和 `year`，服务会使用当前配置的 `default_alias`。

## 字段含义

- `original_text`：最终要写入飞书 `原始信息` 的文本
- `source`：简单来源标记，例如 `ios-shortcuts`
- `raw_ocr`：OCR 原始输出，通常是人工清理前或并存的文本
- `book_alias`：非敏感年度目标选择器，例如 `2026`
- `year`：可选的数字型选择器

## Token 要求

`X-Webhook-Token` 必须使用真实运行时值。

不要保留下面这种占位符：

```text
replace_me_with_long_random_secret
```

如果你保留占位符，服务会返回 `401 invalid webhook token`。

## 人工确认推荐放在哪

推荐的人工确认位置是：

- OCR 之后
- 发送 webhook 之前

这样可以保证 Feishu 里的 `原始信息` 更接近用户最终确认后的文本。

## 解析返回 JSON

成功返回示例：

```json
{
  "success": true,
  "request_id": "req_xxxxxxxxxxxx",
  "record_id": "recXXXXXXXX",
  "book_alias": "2026",
  "message": "configured record updated and 原始信息 updated"
}
```

推荐解析方式：

1. `Get Dictionary from Input` 继续保持默认 `Input`
2. `Get Value for Dictionary Value` 手动填写键名 `message`
3. 如有需要，也可以顺便读取 `book_alias` 或 `record_id` 便于调试

之后把 `message` 作为成功提示或结果提示展示给用户。

## 常见错误

### 1. 还在用旧字段 `text` / `time`

修法：

- 把 body 改成 `original_text`、`source`、`raw_ocr`
- 如有需要，再补 `book_alias` 或 `year`

### 2. 从 Shortcut 发送飞书 ID

修法：

- 把飞书凭据和内部标识留在服务端
- 只发送 `book_alias` 这类选择器字段

### 3. Token 占位符没改

修法：

- 把它替换成服务端实际使用的 runtime token

## 推荐验证

1. 先调 `GET /health`
2. 从 Shortcut 发送一次真实请求
3. 确认返回里有 `success: true`
4. 确认飞书更新到了预期账本目标
5. 确认飞书后续自动化仍然触发
