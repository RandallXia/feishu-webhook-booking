# 公网接入 / Public access

## 语言切换 / Language

- 中文（默认）: [本页](public-access.md)
- English: [Public access](../en/deployment/public-access.md)

## 结论

如果家宽 443 被封，优先使用 Cloudflare Tunnel。

8443 只适合临时兜底，不是长期首选。

## 关键点

- 443 被封通常是入站链路问题，不是应用代码问题
- `8443` 只是外部端口替换，不会自动修复协议不匹配
- 如果反代出现 `plain http request was sent to https port`，说明 HTTP/HTTPS 配置层错位了
- DNS 记录、Cloudflare 代理模式、反代源站协议都要一起检查

## 推荐路径

1. 先确认公网入口是否真能通
2. 优先上 Cloudflare Tunnel
3. NAS 反向代理只做内部转发
4. 保持运行时配置外置，不要因为公网方案变化而重建镜像

## 与本项目的关系

本项目的安全边界不变：

- 镜像只放代码和依赖
- 运行时配置放在外部
- Shortcut 只传 webhook token + 业务文本 + 选择器
