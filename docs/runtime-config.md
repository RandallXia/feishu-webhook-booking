# 运行时配置 / Runtime config

## 语言切换 / Language

- 中文（默认）: [本页](runtime-config.md)
- English: [Runtime config](en/runtime-config.md)

This service reads secrets from runtime config, not from the Docker image.

## Loading order

1. Process environment variables
2. External env file pointed to by `FEISHU_ENV_FILE`
3. Project-root `.env` as a local development fallback

Process environment variables always win over file values.

## Config split

This project separates runtime config into two layers:

1. **Static service config** in env or the external env file
2. **Dynamic yearly target registry** in a mounted TOML file pointed to by `FEISHU_TARGETS_FILE`

## Static service config

Required service-wide values:

- `WEBHOOK_SHARED_TOKEN`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

Optional service-wide values with defaults:

- `FEISHU_BASE_URL` (default `https://open.feishu.cn`)
- `HOST`
- `PORT`
- `LOG_LEVEL`
- `HTTP_TIMEOUT_SECONDS`
- `TOKEN_REFRESH_SKEW_SECONDS`

Dynamic-mode-specific values:

- `FEISHU_TARGETS_FILE`
- `FEISHU_TARGET_RELOAD_INTERVAL_SECONDS`
- `CONFIG_RELOAD_TOKEN`

Legacy compatibility values when `FEISHU_TARGETS_FILE` is not configured:

- `FEISHU_APP_TOKEN`
- `FEISHU_TABLE_ID`
- `FEISHU_RECORD_ID`
- `FEISHU_ORIGINAL_FIELD_NAME`

## Dynamic target registry

Recommended file format: TOML.
Recommended checked-in template: [runtime/feishu-targets.toml.example](runtime/feishu-targets.toml.example)

Example runtime file:

```toml
default_alias = "2026"

[targets."2025"]
year = 2025
app_token = "bascn_replace_me_2025"
table_id = "tbl_replace_me_2025"
record_id = "rec_replace_me_2025"
original_field_name = "原始信息"
enabled = true

[targets."2026"]
year = 2026
app_token = "bascn_replace_me_2026"
table_id = "tbl_replace_me_2026"
record_id = "rec_replace_me_2026"
original_field_name = "原始信息"
enabled = true
```

### Registry rules enforced by the code

`app/target_registry.py` currently enforces these rules:

- `default_alias` must exist in the `targets` section
- the `default_alias` target must be `enabled = true`
- alias names must match a safe pattern such as letters, digits, `_`, and `-`
- each target must provide non-empty `app_token`, `table_id`, and `record_id`
- `year` is optional, but if present it must be a positive integer
- each `year` must be unique across targets
- if both `book_alias` and `year` are provided in a request, they must resolve to the same target
- when a changed registry becomes invalid, the service fails closed and blocks writes until the config is fixed

The client request may send only a non-secret selector such as `book_alias` or `year`.
It must **not** send `app_token`, `table_id`, `record_id`, or `FEISHU_APP_SECRET`.

## Recommended modes

### Local development

Use the project-root `.env` file for local fallback, and copy the example targets file into `runtime/` for dynamic testing.

### Docker / Synology NAS / Linux / Windows / Termux

Mount or place both files outside the image:

- runtime env file for static service config
- TOML targets file for yearly target mapping

Example pattern:

```text
host runtime file               ->  container path                 ->  env key
./runtime/feishu-webhook.env    ->  /runtime/feishu-webhook.env    ->  FEISHU_ENV_FILE
./runtime/feishu-targets.toml   ->  /runtime/feishu-targets.toml   ->  FEISHU_TARGETS_FILE
```

## Dynamic annual book switching

When the new year’s book is already preconfigured in `feishu-targets.toml`, you can switch routing without rebuilding the image and without restarting the service.

Typical flow:

1. Add the new yearly target entry to `feishu-targets.toml`
2. Change `default_alias` if requests without selector should move to the new year
3. Wait for lazy reload or call `POST /admin/config/reload`
4. Call `GET /health`
5. Send one real webhook request
6. Confirm the expected record updates in the target annual book

## Legacy compatibility mode

If `FEISHU_TARGETS_FILE` is not configured, the service still supports the old single-target mode:

- `FEISHU_APP_TOKEN`
- `FEISHU_TABLE_ID`
- `FEISHU_RECORD_ID`
- `FEISHU_ORIGINAL_FIELD_NAME`

In that mode, the service behaves like the previous fixed-record implementation.

## Security guidance

- Keep `.env.example` and `runtime/feishu-targets.toml.example` as templates only
- Do not commit real env files or real TOML target files
- Prefer host environment variables, mounted env files, Docker secret, NAS configuration center, or equivalent external mechanisms for production secrets
- Treat the target registry file as operationally sensitive even though requests see only aliases
- Do not put `FEISHU_APP_SECRET`, `app_token`, `table_id`, or `record_id` into Shortcut request bodies
