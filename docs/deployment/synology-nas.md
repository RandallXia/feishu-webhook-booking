# 群晖 NAS 部署 / Synology NAS deployment

## 语言切换 / Language

- 中文（默认）: [本页](synology-nas.md)
- English: [Synology NAS](../../en/deployment/synology-nas.md)

## 推荐方式

- Docker GUI：适合习惯图形界面的用户
- `docker compose`：适合希望配置可复用的用户

## 容器配置

建议保持以下原则：

- 端口映射简单清晰
- `WEBHOOK_SHARED_TOKEN` 放在宿主环境或外部 env 文件
- `FEISHU_TARGETS_FILE` 指向外部 TOML 注册表
- 容器重启策略使用 `unless-stopped`

## 公网接入

优先顺序：

1. Cloudflare Tunnel
2. DSM 反向代理 + HTTPS
3. 8443 临时兜底

## 典型检查项

- 域名是否被占用
- 证书是否已绑定
- 443 是否被宽带封禁
- 反代源站与目标协议是否一致

## 年度切账本

只改外部配置：

- `runtime/feishu-webhook.env`
- `runtime/feishu-targets.toml`

然后重启或 reload，不要重建镜像。
