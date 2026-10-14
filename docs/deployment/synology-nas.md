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

## 推荐部署：compose + 官方镜像（免删容器更新）

在 DSM 的 Docker GUI 里「下载镜像 → 用镜像创建容器」的方式，每次更新都要手动
停止、删除旧容器再重建，配置也容易抄漏。改用 **compose 文件引用官方镜像** 后，
更新只需要两条命令，容器由 compose 自动重建，`runtime/` 配置与历史库完全不动。

### 首次部署

在 NAS 上任选一个目录（例如 `/volume1/docker/feishu-webhook-booking`），
创建 `docker-compose.yml`：

```yaml
services:
  feishu-webhook-booking:
    image: randallxia/feishu-webhook-booking:latest   # 或固定版本，如 :2.2.2
    container_name: feishu-webhook-booking
    restart: unless-stopped
    ports:
      - "2398:2398"
    environment:
      FEISHU_ENV_FILE: /runtime/feishu-webhook.env
      FEISHU_TARGETS_FILE: /runtime/feishu-targets.toml
      AI_HISTORY_DB_PATH: /data/history.db
    volumes:
      # 不加 :ro：/admin/ai 配置界面的保存功能需要写 runtime 下的 env/TOML 文件
      - ./runtime:/runtime
      - ./data:/data   # 记账历史 sqlite（需 AI_HISTORY_DB_PATH=/data/history.db）
```

然后在同目录准备运行时配置（与仓库 `runtime/*.example` 结构一致）：

- `runtime/feishu-webhook.env`（含 `WEBHOOK_SHARED_TOKEN`、飞书凭据、`AI_ENABLED` 等）
- `runtime/feishu-targets.toml`
- `runtime/ai-profile.toml`（`AI_ENABLED=true` 时必填）

启动：

先确认 NAS 提供哪一种 compose 命令：

```bash
docker compose version || docker-compose --version
```

DSM 较旧的 Docker 套件通常只有 `docker-compose`（带连字符）；如果
`docker compose` 不可用，下文所有命令都改用 `docker-compose`。首次替换旧 GUI
容器前，先执行 `docker-compose config` 验证 YAML，再执行
`docker-compose pull`，确认镜像已拉取成功后才停止旧容器。

```bash
cd /volume1/docker/feishu-webhook-booking
docker compose up -d
```

验证：

```bash
curl http://localhost:2398/health
docker compose logs -f feishu-webhook-booking
```

### 后续更新（不删除容器、不改配置）

```bash
cd /volume1/docker/feishu-webhook-booking
docker compose pull     # 拉取新镜像（latest 或指定新版本 tag）
docker compose up -d    # compose 检测到镜像变化，自动重建容器；配置与挂载卷原样保留
docker image prune -f   # 可选：清理悬空的旧镜像层
```

`up -d` 只会重建「配置或镜像发生变化」的服务，运行中的数据都在挂载卷里
（`./runtime`、`./data`），容器本身无状态，重建零损失。更新后旧容器被自动
替换，无需在 DSM GUI 里手动停止/删除。

### 从「GUI 手动创建的容器」迁移到 compose

以 2.2.1 版本为例：

1. 记录旧容器的关键配置（DSM GUI → 容器 → 详细信息）：端口映射、环境变量、
   挂载路径。通常只有 `2398:2398` 和 `runtime/` 挂载。
2. 把旧容器挂载的宿主机目录整理到上文的 compose 目录结构中（`runtime/` 下
   三个配置文件原样复制即可，无需改动）。
3. 停止并删除旧容器（**只删容器，不删镜像以外的任何数据**；镜像也可顺手删）：

   ```bash
   docker stop feishu-webhook-booking
   docker rm feishu-webhook-booking
   ```

   或者在 DSM GUI 中操作。旧容器是一次性实例，删除不影响任何持久化数据。
4. 按上文「首次部署」启动 compose 服务。
5. `curl http://localhost:2398/health` 确认服务正常，Shortcut 记账链路走一遍
   确认 `原始信息` 正常写入。

迁移是一次性的：完成后，后续所有版本更新都走 `docker compose pull && up -d`，
不再需要 GUI 重建容器。

### 固定版本 vs latest

- `image: randallxia/feishu-webhook-booking:2.2.2`：更新时改 tag 再
  `docker compose up -d`，版本完全可控，回滚只改回旧 tag。
- `image: ...:latest`：`docker compose pull && docker compose up -d` 即始终
  最新版，适合个人设备快速跟随。

两者都可与 DSM GUI 共存（GUI 只用来查看状态），但更新操作建议统一走 SSH +
compose。

### `docker compose` 命令不可用

如果出现 `unknown shorthand flag: 'd' in -d`，说明 Docker CLI 没有 Compose v2
插件，不是 YAML 配置错误。群晖通常提供兼容的独立命令：

```bash
docker-compose config
docker-compose pull
docker-compose up -d
```

如果 `docker-compose` 也不存在，可检查 Container Manager 自带二进制：

```bash
command -v docker-compose
ls -l /var/packages/ContainerManager/target/usr/bin/docker-compose
```

在找到可用 compose 命令之前，不要停止或删除旧容器。

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
