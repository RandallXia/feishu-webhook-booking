# Synology NAS deployment

## Language

- English: [Synology NAS](synology-nas.md)
- Chinese: [群晖 NAS 部署](../../deployment/synology-nas.md)

## Recommended methods

- Docker GUI: good if you prefer a graphical workflow
- `docker compose`: better if you want a reusable config

## Container settings

Keep these principles:

- simple, explicit port mapping
- `WEBHOOK_SHARED_TOKEN` in host env or an external env file
- `FEISHU_TARGETS_FILE` pointing to an external TOML registry
- restart policy set to `unless-stopped`

## Recommended deployment: compose + official image (update without deleting containers)

The DSM Docker GUI flow ("download image → create container from image") requires
manually stopping and deleting the old container on every update, and it is easy
to mistype settings. Switching to a **compose file that references the official
image** reduces updates to two commands: compose recreates the container itself,
while `runtime/` config and the history database stay untouched.

### First deployment

Pick a directory on the NAS (e.g. `/volume1/docker/feishu-webhook-booking`) and
create `docker-compose.yml`:

```yaml
services:
  feishu-webhook-booking:
    image: randallxia/feishu-webhook-booking:latest   # or a pinned tag, e.g. :2.2.2
    container_name: feishu-webhook-booking
    restart: unless-stopped
    ports:
      - "2398:2398"
    environment:
      FEISHU_ENV_FILE: /runtime/feishu-webhook.env
      FEISHU_TARGETS_FILE: /runtime/feishu-targets.toml
      AI_HISTORY_DB_PATH: /data/history.db
    volumes:
      # no :ro: the /admin/ai config UI save feature writes the env/TOML files under runtime/
      - ./runtime:/runtime
      - ./data:/data   # request-history sqlite (needs AI_HISTORY_DB_PATH=/data/history.db)
```

Then prepare the runtime files in the same directory (same structure as the
repo's `runtime/*.example` templates):

- `runtime/feishu-webhook.env` (`WEBHOOK_SHARED_TOKEN`, Feishu credentials, `AI_ENABLED`, ...)
- `runtime/feishu-targets.toml`
- `runtime/ai-profile.toml` (required when `AI_ENABLED=true`)

Start:

First identify which compose command the NAS provides:

```bash
docker compose version || docker-compose --version
```

Older DSM Docker packages usually provide only `docker-compose` (with a
hyphen). If `docker compose` is unavailable, replace every command below with
`docker-compose`. Before replacing the old GUI container, run
`docker-compose config` to validate the YAML, then `docker-compose pull`; stop
the old container only after the image has been pulled successfully.

```bash
cd /volume1/docker/feishu-webhook-booking
docker compose up -d
```

Verify:

```bash
curl http://localhost:2398/health
docker compose logs -f feishu-webhook-booking
```

### Later updates (no container deletion, no config changes)

```bash
cd /volume1/docker/feishu-webhook-booking
docker compose pull     # fetch the new image (latest or a pinned new tag)
docker compose up -d    # compose detects the image change and recreates the container; config and volumes are preserved
docker image prune -f   # optional: remove dangling old image layers
```

`up -d` only recreates services whose image or config changed. All persistent
state lives in the mounted volumes (`./runtime`, `./data`); the container itself
is stateless, so recreation loses nothing. The old container is replaced
automatically — no manual stop/delete in the DSM GUI.

### Migrating from a "GUI-created container" to compose

Using 2.2.1 as an example:

1. Note the old container's key settings (DSM GUI → Container → Details):
   port mapping, env vars, mounted paths. Usually just `2398:2398` and the
   `runtime/` mount.
2. Arrange the host directories from the old container into the compose layout
   above (copy the three files under `runtime/` as-is; no edits needed).
3. Stop and remove the old container (**only the container — no persistent
   data**; the old image can be removed too):

   ```bash
   docker stop feishu-webhook-booking
   docker rm feishu-webhook-booking
   ```

   Or do it in the DSM GUI. The old container is a one-off instance; deleting
   it does not touch any persisted data.
4. Start the compose service per "First deployment" above.
5. `curl http://localhost:2398/health` to confirm, then run one Shortcut
   booking end-to-end to confirm `原始信息` is written.

Migration is one-time: afterwards every version update is
`docker compose pull && up -d`, with no GUI container rebuilds.

### Pinned tag vs latest

- `image: randallxia/feishu-webhook-booking:2.2.2`: change the tag to update,
  then `docker compose up -d`. Fully controlled versions; rollback = switch the
  tag back.
- `image: ...:latest`: `docker compose pull && docker compose up -d` always
  tracks the newest build; convenient for a personal device.

Both can coexist with the DSM GUI (use the GUI for status only), but keep the
update workflow on SSH + compose.

### `docker compose` command is unavailable

If `docker compose up -d` reports `unknown shorthand flag: 'd' in -d`, the
Docker CLI does not have the Compose v2 plugin. This is not a YAML error.
Synology commonly provides the compatible standalone command instead:

```bash
docker-compose config
docker-compose pull
docker-compose up -d
```

If `docker-compose` is also unavailable, check for Container Manager's bundled
binary:

```bash
command -v docker-compose
ls -l /var/packages/ContainerManager/target/usr/bin/docker-compose
```

Do not stop or remove the old container until a working compose command is
available.

## Public ingress

Preferred order:

1. Cloudflare Tunnel
2. DSM reverse proxy + HTTPS
3. 8443 temporary fallback

## Common checks

- is the domain already used?
- is the certificate bound?
- is inbound 443 blocked by the ISP?
- do source and destination protocols match?

## Annual switching

Only change external config:

- `runtime/feishu-webhook.env`
- `runtime/feishu-targets.toml`

Then restart or reload; do not rebuild the image.
