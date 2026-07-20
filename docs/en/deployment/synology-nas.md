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
