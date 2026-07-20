# Feishu setup

## Language

- English: [Feishu setup](feishu-setup.md)
- Chinese: [飞书配置](../feishu-setup.md)

## Scope

This document explains how to prepare the Feishu side for this webhook service.

The current architecture is server-side target routing with client-side selectors only.

That means:

- Shortcut sends OCR text plus `book_alias` or `year`
- the server owns Feishu credentials and target registry
- this service writes only the `原始信息` field of the selected record

## What you need from Feishu

Service-wide app credentials:

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

Per-target routing values:

- `app_token`
- `table_id`
- `record_id`
- optional `original_field_name`

These per-target values go into `runtime/feishu-targets.toml`, not into Shortcut.

## Step 1: Create a self-built Feishu app

In Feishu Open Platform:

1. create a self-built app
2. open the app credentials page
3. copy the app id and app secret

Map them to:

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

## Step 2: Grant the right permissions

The app must be allowed to access and update the target Bitable records.

At minimum, verify:

- app permission scopes include the required Bitable read/write capability
- the Bitable itself grants the app access
- if the app model requires publishing, the current version has been published

If this is wrong, you may see Feishu `403` or `91403 Forbidden`.

## Step 3: Identify `app_token`, `table_id`, and `record_id`

These three values are often confused.

### `app_token`

This identifies the Bitable app/base itself.

### `table_id`

This identifies one table inside that base.

### `record_id`

This identifies one specific record inside that table.

## Why `record_id` matters

The service updates a selected fixed record instead of creating a new record per request.

In legacy mode, that fixed record comes from env.

In dynamic mode, each yearly target in the TOML registry points to its own fixed record.

## Field responsibility boundary

This service writes only:

- `原始信息`

Feishu automation remains responsible for:

- `记录时间`
- `精简原始数据`
- `账单明细`

This boundary is intentional.

## Recommended automation relationship

A common pattern is:

1. webhook updates `原始信息`
2. Feishu automation 1 reacts to the update and generates cleaned/intermediate fields
3. Feishu automation 2 continues to derive final structured fields

The exact automation naming can vary, but the service should still only own the first write.

## Dynamic yearly target configuration

Recommended file: `runtime/feishu-targets.toml`

Example:

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

## Annual book switching

In dynamic mode, annual switching is now:

1. create or prepare the new year’s target record in Feishu
2. collect the new `app_token`, `table_id`, and `record_id` if they changed
3. update `runtime/feishu-targets.toml`
4. optionally change `default_alias`
5. wait for reload or call `POST /admin/config/reload`
6. verify with one real webhook request

If you stay in legacy mode, annual switching still requires replacing the old fixed-target env values and restarting the service.

## Security reminders

- keep `FEISHU_APP_SECRET` server-side only
- keep `app_token`, `table_id`, and `record_id` out of Shortcut request bodies
- do not commit real runtime config files into the repository
- use placeholders in docs, screenshots, and examples

## Verification

1. verify app permission scopes
2. verify the app has access to the target Bitable
3. verify the selected target record really exists
4. send one real webhook request
5. confirm `原始信息` was updated in the expected target record
6. confirm downstream Feishu automation still runs
