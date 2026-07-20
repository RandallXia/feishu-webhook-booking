# 迁移复盘 / Retrospective

## 语言切换 / Language

- 中文（默认）: [本页](retrospective.md)
- English: [Retrospective](../en/retrospective.md)

## 时间线

1. 旧 Feishu webhook / 机器人入口失效
2. 先做 NAS 本地 FastAPI 桥接器
3. 先尝试 create 记录，再遇到 Feishu `403 / 91403 Forbidden`
4. 修正为使用 `FEISHU_RECORD_ID` 的更新模式
5. 处理公网入口、DSM 反向代理和 443 封禁
6. 遇到 8443、`plain http request was sent to https port`、DNS / AAAA / 反代协议不匹配等问题
7. Shortcut 请求体从旧 `text` / `time` 迁移到 `original_text` / `source` / `raw_ocr`
8. Windows PowerShell 中文 body 编码问题
9. 年度账本切换不能依赖重建镜像，必须通过外部配置切换
10. 最终稳定方案确认并持续运行

## 这次学到什么

- 业务链路要尽量简单
- 敏感配置必须外置
- 年度账本切换应该改配置，不该改镜像
- Shortcut 只做入口，不做飞书内部标识承载
