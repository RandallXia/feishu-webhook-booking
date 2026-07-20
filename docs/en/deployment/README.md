# Deployment overview

## Language

- English: [Deployment overview](README.md)
- Chinese: [部署总览](../../deployment/README.md)

## Quick guidance

- NAS + Cloudflare Tunnel: recommended first choice
- NAS + 8443: temporary fallback
- Windows PC / cloud desktop: good for a machine that stays on
- Linux VPS / private server: good for long-running standard deployment
- Android / Termux: experimental or fallback only

## Shared principle

Regardless of platform:

- keep `WEBHOOK_SHARED_TOKEN` and Feishu secrets outside the image
- keep target routing in external runtime config
- switch annual books by updating config, not rebuilding the image

## What to read next

- [Runtime config](../runtime-config.md)
- [Architecture](../architecture.md)
- [Shortcut setup](../shortcut-setup.md)
- [Feishu setup](../feishu-setup.md)
- [Troubleshooting](../troubleshooting.md)
- [Release checklist](../release-checklist.md)
