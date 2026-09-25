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
6. When `AI_ENABLED=true`, this service takes over the following steps: AI extracts bill fields → writes back `精简原始数据` → creates a `账单明细` record
   When `AI_ENABLED=false` (default), existing Feishu automations continue to generate those fields

The service intentionally does **not**:

- let clients send Feishu credentials or internal identifiers
- parse accounting fields (only when `AI_ENABLED=false`; once enabled, the self-hosted AI pipeline takes over)

See [ai-pipeline.md](ai-pipeline.md) for the bill-field extraction pipeline.

## Documentation

- [Runtime config](runtime-config.md)
- [Architecture](architecture.md)
- [Shortcut setup](shortcut-setup.md)
- [Feishu setup](feishu-setup.md)
- [AI extraction pipeline](ai-pipeline.md)
- [Troubleshooting](troubleshooting.md)
- [Release checklist](release-checklist.md)
- [Retrospective](retrospective.md)
- [Deployment overview](deployment/README.md)

The Chinese versions are the default canonical docs in the repository root.

## AI extraction pipeline

When `AI_ENABLED=true`, the service takes over Feishu's "AI auto-fill" after writing `原始信息`: a single structured AI call extracts bill fields from the OCR text, writes back `精简原始数据`, and creates a new record in the 账单明细 table. Quota is governed by your own AI provider, not Feishu's monthly cap. Configuration can be done via the web UI (`/admin/ai`); see section 12 of the [AI pipeline docs](ai-pipeline.md).

See [ai-pipeline.md](ai-pipeline.md) for full config, migration checklist, TOML schema, and admin endpoints (Chinese: [../../docs/ai-pipeline.md](../ai-pipeline.md)).

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

AI extraction pipeline config (optional, off by default):

- `AI_ENABLED` (default `false`; when enabled, `AI_PROVIDER` / `AI_API_KEY` / `AI_MODEL` / `AI_PROFILE_FILE` become required)
- Other `AI_*` vars and the `AI_BASE_URL` concatenation rule: see [ai-pipeline.md](ai-pipeline.md)

The target registry template lives in [runtime/feishu-targets.toml.example](../../runtime/feishu-targets.toml.example).
The AI profile template lives in [runtime/ai-profile.toml.example](../../runtime/ai-profile.toml.example).
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
