# feishu-webhook-booking

A minimal FastAPI webhook bridge for [iPhone Shortcuts](https://www.icloud.com/shortcuts/74ac462a4e2e4cf0ac2f33d20b5b4617) -> Feishu Bitable.

## Language

- English: [README](README.md)
- Chinese: [README.md](../../README.md)

## What this service does

This service only handles a very narrow workflow:

1. iPhone Shortcuts OCRs a screenshot
2. Shortcut sends the original text to this webhook service
3. The service validates a shared token
4. The service resolves the target book by `book_alias` or `year`
5. The service calls Feishu Open API to update `原始信息` on the selected record
6. Existing Feishu automations continue to generate:
   - `记录时间`
   - `精简原始数据`
   - `账单明细`

The service intentionally does **not**:

- parse accounting fields
- generate `记录时间`
- generate `精简原始数据`
- write `账单明细`
- let clients send Feishu credentials or internal identifiers

## Documentation

- [Runtime config](runtime-config.md)
- [Architecture](architecture.md)
- [Shortcut setup](shortcut-setup.md)
- [Feishu setup](feishu-setup.md)
- [Troubleshooting](troubleshooting.md)
- [Release checklist](release-checklist.md)
- [Retrospective](retrospective.md)
- [Deployment overview](deployment/README.md)

The Chinese versions are the default canonical docs in the repository root.

## Runtime modes

This repo supports two runtime modes:

1. **Dynamic selector mode**: set `FEISHU_TARGETS_FILE` and let requests select a target by `book_alias` or `year`
2. **Legacy compatibility mode**: omit `FEISHU_TARGETS_FILE` and keep using `FEISHU_APP_TOKEN` / `FEISHU_TABLE_ID` / `FEISHU_RECORD_ID`

The dynamic mode is the recommended long-term path.

## API

### `GET /health`

Health check.

### `POST /v1/webhook/ocr`

Headers:

- `Content-Type: application/json`
- `X-Webhook-Token: <shared-secret>`
- optional `X-Request-Id`

Body example:

```json
{
  "original_text": "OCR extracted text from the screenshot",
  "source": "ios-shortcuts",
  "raw_ocr": "raw ocr text",
  "book_alias": "2026"
}
```

You may use `year` instead of `book_alias`.
Do **not** include `app_token`, `table_id`, `record_id`, or `FEISHU_APP_SECRET` in the request body.

### `POST /admin/config/reload`

Optional admin reload endpoint, enabled only when `CONFIG_RELOAD_TOKEN` is configured.

## Runtime config

Service-wide config:

- `WEBHOOK_SHARED_TOKEN`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

Dynamic-mode config:

- `FEISHU_TARGETS_FILE`
- `FEISHU_TARGET_RELOAD_INTERVAL_SECONDS`
- `CONFIG_RELOAD_TOKEN` (optional)

The target registry template lives in [runtime/feishu-targets.toml.example](../../runtime/feishu-targets.toml.example).
The runtime env template lives in [runtime/feishu-webhook.env.example](../../runtime/feishu-webhook.env.example).

## Local run

```bash
pip install -r requirements-dev.txt
cp .env.example .env
cp runtime/feishu-targets.toml.example runtime/feishu-targets.toml
uvicorn app.main:app --host 0.0.0.0 --port 2398 --reload
```

## Docker / Compose

```bash
docker compose up -d --build
```

Compose mounts `./runtime` and reads:

- `./runtime/feishu-webhook.env`
- `./runtime/feishu-targets.toml`
