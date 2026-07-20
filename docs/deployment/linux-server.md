# Linux 服务器部署 / Linux server deployment

## 语言切换 / Language

- 中文（默认）: [本页](linux-server.md)
- English: [Linux server](../../en/deployment/linux-server.md)

## 部署方式

- Docker：最简单
- systemd：适合非 Docker 场景
- 反向代理或 Tunnel：负责公网入口

## 推荐命令

```bash
docker compose up -d --build
```

## 外部化配置

年度账本切换时，优先通过以下方式更新配置：

- 环境文件
- `EnvironmentFile=`
- Docker secret

不要通过重建镜像来切换账本。
