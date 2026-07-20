# 排障指南 / Troubleshooting

## 语言切换 / Language

- 中文（默认）: [本页](troubleshooting.md)
- English: [Troubleshooting](en/troubleshooting.md)

本文按“现象”组织，方便按症状快速定位问题。

每节都使用同样结构：

- 现象
- 根因
- 修法
- 预防

## 1. `401 invalid webhook token`

### 现象
- webhook 返回 `401 invalid webhook token`

### 根因
- 没有带 `X-Webhook-Token`
- token 仍然是占位符
- Shortcut 或测试客户端还在发旧 token

### 修法
- 对照真实运行时 `WEBHOOK_SHARED_TOKEN` 与请求头值
- 替换 Shortcut、curl、PowerShell 或其他客户端里的占位符

### 预防
- 把真实 token 保存在一个可信运行时配置位置
- 不要直接复用旧测试片段而忘记更新 token

## 2. Feishu `403` / `91403 Forbidden`

### 现象
- 服务已经请求到了飞书，但写入失败并返回 `403` 或 `91403 Forbidden`

### 根因
- 应用权限范围不足
- 多维表格本身没有授权该应用
- 如果该应用模型要求发布，则当前版本未发布
- 当前 `app_token`、`table_id`、`record_id` 填错

### 修法
- 在飞书开放平台检查应用权限
- 检查多维表格的授权/共享设置
- 如有需要，确认应用已发布
- 重新核对运行时配置中的目标标识

### 预防
- 记录每个目标标识的来源
- 每次年度切换后都发一条真实请求做验证

## 3. 混淆 `app_token`、`table_id` 和 `record_id`

### 现象
- 表面看起来目标写对了，但更新要么写错地方，要么直接失败

### 根因
- 把 `app_token`、`table_id`、`record_id` 混淆了

### 修法
- 记住三者关系：
  - `app_token` = 整个 base / 多维表格应用
  - `table_id` = base 里的某张表
  - `record_id` = 表里的一条记录
- 按此重新修正运行时注册表或 env 值

### 预防
- 记录每个值是从哪里拿到的
- 新增年度目标时，把三者成组记录下来

## 4. Windows PowerShell 5.1 中文 body 乱码

### 现象
- 中文内容在服务端变成 `????` 或乱码

### 根因
- PowerShell 5.1 默认编码行为对 UTF-8 JSON body 不友好

### 修法
- 优先使用 `Invoke-RestMethod`
- 确保请求体以 UTF-8 友好的方式发送
- 必要时显式处理编码，或使用更新版本的 shell/runtime

### 预防
- 文档里保留一份已验证的 PowerShell 示例
- 部署完成前至少验证一条中文请求

## 5. DSM “The domain name is already used”

### 现象
- 群晖 DSM 反向代理提示域名已被使用

### 根因
- 已有其它反向代理规则或服务占用了同一域名/主机组合

### 修法
- 检查现有 DSM 反向代理规则
- 小心复用已有路由，或改用新的主机名
- 确认归属后再删除或合并冲突规则

### 预防
- 保持一份公网入口清单
- 新建 host 规则前先检查现有占用

## 6. 家宽封 443

### 现象
- 本地一切正常，但公网 443 HTTPS 访问还是失败

### 根因
- 运营商封禁或干扰了 443 入站流量

### 修法
- 不要继续只在 DSM 反代里反复折腾
- 优先切换到 Cloudflare Tunnel
- 如有必要，8443 只作为临时兜底路径

### 预防
- 先假设问题可能在入站链路而不是应用本身
- 在部署早期就先验证一条公网访问路径

## 7. `plain http request was sent to https port`

### 现象
- 请求像是打到了某个入口，但返回 `plain http request was sent to https port`

### 根因
- 协议层和端口层不匹配
- 例如把 HTTP 发到了 HTTPS 监听端口，或者反代源/目标协议没对齐

### 修法
- 核对整条端口映射与反代链路
- 分清“外部端口替换”和“内部协议处理”不是一回事
- 在 NAS 上仔细确认反代源 HTTPS 与目标 HTTP/端口配置

### 预防
- 排障时先把入口链路画出来
- 不要同时盲改多层配置

## 8. Shortcut 还在用旧字段 `text` / `time`

### 现象
- Shortcut 看似能发请求，但请求契约已经和当前 API 不匹配

### 根因
- Shortcut 仍然使用旧版 body 结构

### 修法
- 把 body 改成 `original_text`、`source`、`raw_ocr`
- 如有需要，再补 `book_alias` 或 `year`

### 预防
- 保证 Shortcut 文档和当前 API 一致
- 每次 API 演进后都做一次字段名端到端核对

## 9. `Get Dictionary from Input` / `Get Value for message` 配错

### 现象
- 请求明明成功了，但 Shortcut 解析返回消息失败

### 根因
- Shortcut 的返回解析动作使用了错误输入或错误键名

### 修法
- `Get Dictionary from Input` 继续保持默认 `Input`
- `Get Value for Dictionary Value` 手动填写键名 `message`

### 预防
- 除非必要，不要过度复杂化返回解析逻辑
- 第一次成功请求后立刻验证解析是否正常

## 10. 缺少 `.env`、端口、证书或 `record_id`

### 现象
- 服务要么启动不了，要么启动后请求时才失败

### 根因
- 缺少必要运行时配置
- 端口映射错误
- 证书配置不完整
- 所选目标缺少合法 `record_id`

### 修法
- 检查 `.env` 或外部 env 文件是否被正确加载
- 如果是动态模式，检查 `FEISHU_TARGETS_FILE` 路径
- 检查容器端口暴露和反代映射
- 检查飞书目标记录确实存在

### 预防
- 使用仓库提供的模板文件作为起点
- 每次改配置后都验证健康检查和一条真实 webhook 请求

## 11. 年度切换还是写到了旧目标

### 现象
- 你明明更新了配置文件，但请求还是写到了旧账本

### 根因
- 服务实际读取的 env 文件和你以为的不一样
- 容器环境变量覆盖了文件值
- TOML 注册表尚未 reload
- 当前生效的仍是错误的 `default_alias` 或旧目标项

### 修法
- 检查当前生效的 `FEISHU_ENV_FILE` 和 `FEISHU_TARGETS_FILE`
- 检查容器环境变量
- 通过 `/health` 和日志确认 `config_valid` 与 reload 状态
- 如果已配置管理 token，可调用 `POST /admin/config/reload`
- 重新核对当前 `default_alias` 和目标项内容

### 预防
- 保持一套清晰的运行时真相来源
- 更新目标注册表后，立刻用一条真实请求验证
- 除非有意为之，否则不要把旧版固定目标 env 与动态模式混用
