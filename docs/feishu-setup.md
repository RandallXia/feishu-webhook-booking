# 飞书配置 / Feishu setup

## 语言切换 / Language

- 中文（默认）: [本页](feishu-setup.md)
- English: [Feishu setup](en/feishu-setup.md)

## 适用范围

本文说明如何在飞书侧完成本 webhook 服务所需的配置。

当前架构是“服务端目标路由 + 客户端只传选择器”。

这意味着：

- Shortcut 只发送 OCR 文本以及 `book_alias` / `year`
- 服务端持有飞书凭据与目标注册表
- 本服务只会写入所选记录的 `原始信息` 字段

## 你需要从飞书拿到什么

服务级应用凭据：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

按目标变化的路由值：

- `app_token`
- `table_id`
- `record_id`
- 可选 `original_field_name`

这些按目标变化的值应写进 `runtime/feishu-targets.toml`，而不是写进 Shortcut。

## 第 1 步：创建飞书自建应用

在飞书开放平台中：

1. 创建一个自建应用
2. 打开应用凭据页面
3. 记录 app id 与 app secret

映射到：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

## 第 2 步：配置正确权限

应用必须具备访问并更新目标多维表格记录的权限。

至少检查：

- 应用权限范围包含需要的多维表格读写能力
- 多维表格本身已经授权给该应用
- 如果该应用模型要求发布版本，要确认当前配置已经发布

如果这里配错，常见报错就是 Feishu `403` 或 `91403 Forbidden`。

## 第 3 步：识别 `app_token`、`table_id`、`record_id`

这三个值很容易混淆。

### `app_token`

它标识的是整个多维表格应用 / base。

### `table_id`

它标识的是 base 里的某一张表。

### `record_id`

它标识的是该表中的一条具体记录。

## 为什么 `record_id` 很关键

本服务更新的是“选定的固定记录”，而不是每次请求都新建一条记录。

在旧模式里，这个固定记录来自 env。

在动态模式里，TOML 注册表里的每个年度目标都指向自己的固定记录。

## 字段职责边界

本服务只写：

- `原始信息`

飞书自动化继续负责：

- `记录时间`
- `精简原始数据`
- `账单明细`

这个边界是刻意设计的。

## 推荐的自动化关系

一种常见模式是：

1. webhook 更新 `原始信息`
2. 飞书自动化 1 监听更新并生成中间或清洗字段
3. 飞书自动化 2 继续生成最终结构化字段

自动化名称可以不同，但服务本身仍应只负责第一跳写入。

## 动态年度目标配置

推荐文件：`runtime/feishu-targets.toml`

示例：

```toml
default_alias = "2026"

[targets."2025"]
year = 2025
app_token = "bascn_replace_me_2025"
table_id = "tbl_replace_me_2025"
record_id = "rec_replace_me_2025"
original_field_name = "原始信息"
enabled = true

[targets."2026"]
year = 2026
app_token = "bascn_replace_me_2026"
table_id = "tbl_replace_me_2026"
record_id = "rec_replace_me_2026"
original_field_name = "原始信息"
enabled = true
```

## 年度账本切换

在动态模式下，年度切换现在变成：

1. 在飞书里准备新年度目标记录
2. 收集新的 `app_token`、`table_id`、`record_id`（如有变化）
3. 更新 `runtime/feishu-targets.toml`
4. 如有需要，修改 `default_alias`
5. 等待自动 reload 或调用 `POST /admin/config/reload`
6. 用一条真实 webhook 请求验证

如果你仍停留在旧模式，年度切换依然需要替换固定目标 env 值并重启服务。

## 安全提醒

- `FEISHU_APP_SECRET` 只能保留在服务端
- `app_token`、`table_id`、`record_id` 不要放进 Shortcut 请求体
- 不要把真实运行时配置文件提交到仓库
- 文档、截图、示例里一律使用占位符

## 验证

1. 检查应用权限范围
2. 检查应用已被授权访问目标多维表格
3. 检查目标记录确实存在
4. 发送一条真实 webhook 请求
5. 确认预期目标记录的 `原始信息` 已更新
6. 确认飞书后续自动化仍然触发
