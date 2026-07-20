# Linux server deployment

## Language

- English: [Linux server deployment](linux-server.md)
- Chinese: [Linux 服务器部署](../../deployment/linux-server.md)

## Deployment options

- Docker: simplest
- systemd: good for non-Docker setups
- reverse proxy or Tunnel: handles public ingress

## Suggested command

```bash
docker compose up -d --build
```

## Externalized config

For annual switching, prefer updating:

- env files
- `EnvironmentFile=`
- Docker secrets

Do not rebuild the image just to switch books.
