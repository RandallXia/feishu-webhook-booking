# 部署总览 / Deployment overview

## 语言切换 / Language

- 中文（默认）: [本页](README.md)
- English: [English deployment overview](../en/deployment/README.md)

## 快速建议

- NAS + Cloudflare Tunnel：当前首选
- NAS + 8443：临时兜底
- Windows PC / 云电脑：适合常开机器
- Linux VPS / 私有服务器：适合长期标准部署
- Android / Termux：仅实验或兜底

## 共同原则

无论哪种平台：

- `WEBHOOK_SHARED_TOKEN` 和飞书敏感配置都必须放在镜像外部
- 目标路由都应由外部运行时配置决定
- 年度账本通过改配置切换，而不是重建镜像

## 下一步阅读

- [运行时配置](../runtime-config.md)
- [架构说明](../architecture.md)
- [Shortcut 配置](../shortcut-setup.md)
- [飞书配置](../feishu-setup.md)
- [排障指南](../troubleshooting.md)
- [发布核对](../release-checklist.md)
