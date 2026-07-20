# Troubleshooting

## Language

- English: [Troubleshooting](troubleshooting.md)
- Chinese: [排障指南](../troubleshooting.md)

This document is organized by symptom.

Each section uses the same structure:

- Symptom
- Root cause
- Fix
- Prevention

## 1. `401 invalid webhook token`

### Symptom
- The webhook returns `401 invalid webhook token`

### Root cause
- `X-Webhook-Token` is missing
- the token still uses a placeholder
- the Shortcut or test client is sending an old token

### Fix
- compare the real runtime `WEBHOOK_SHARED_TOKEN` with the request header value
- replace placeholder values in Shortcut, curl, PowerShell, or other clients

### Prevention
- store the real token in one trusted runtime location
- do not duplicate stale test snippets without updating the token

## 2. Feishu `403` / `91403 Forbidden`

### Symptom
- the service reaches Feishu but the update fails with `403` or `91403 Forbidden`

### Root cause
- the app lacks required Feishu permission scopes
- the Bitable itself does not grant the app access
- the app version was not published if publishing is required
- the selected `app_token`, `table_id`, or `record_id` is wrong

### Fix
- verify app permissions in Feishu Open Platform
- verify Bitable sharing/authorization
- verify the app has been published if needed
- re-check target identifiers in runtime config

### Prevention
- document the exact source of every target identifier
- verify one real request after any annual target switch

## 3. Confusing `app_token`, `table_id`, and `record_id`

### Symptom
- the target looks right at first glance, but updates hit the wrong place or fail

### Root cause
- `app_token`, `table_id`, and `record_id` were mixed up

### Fix
- remember the mapping:
  - `app_token` = base / bitable app
  - `table_id` = one table inside the base
  - `record_id` = one record inside the table
- update the runtime registry or env values accordingly

### Prevention
- keep a small note of where each value came from
- when adding a new yearly target, record all three together

## 4. Windows PowerShell 5.1 Chinese body becomes garbled

### Symptom
- Chinese text becomes `????` or malformed on the server side

### Root cause
- PowerShell 5.1 default encoding behavior is unfriendly to UTF-8 JSON bodies

### Fix
- prefer `Invoke-RestMethod`
- ensure the body is UTF-8 friendly
- when needed, explicitly control encoding or use a newer shell/runtime

### Prevention
- keep one known-good PowerShell example in the docs
- verify one Chinese payload before declaring deployment done

## 5. DSM says `The domain name is already used`

### Symptom
- Synology DSM reverse proxy refuses to create a rule because the domain is already in use

### Root cause
- another reverse-proxy entry or service already occupies that domain/host combination

### Fix
- inspect existing DSM reverse proxy entries
- reuse the existing route carefully or choose a different host name
- remove or merge conflicting rules only after confirming ownership

### Prevention
- keep a simple inventory of public entrypoints
- avoid creating duplicate host rules without checking existing ones first

## 6. Home broadband blocks inbound 443

### Symptom
- everything looks fine locally, but public HTTPS access on 443 still fails

### Root cause
- the ISP blocks or interferes with inbound 443 traffic

### Fix
- do not keep fighting DSM reverse proxy alone
- switch to Cloudflare Tunnel first
- use 8443 only as a temporary fallback if needed

### Prevention
- assume network ingress may be the real bottleneck, not the app
- validate one public path early in deployment

## 7. `plain http request was sent to https port`

### Symptom
- the request reaches something, but you get `plain http request was sent to https port`

### Root cause
- protocol and port layers are mismatched
- for example, HTTP is sent to an HTTPS listener, or reverse-proxy source/destination protocols do not align

### Fix
- verify the full mapping chain
- distinguish external port replacement from internal protocol handling
- on NAS, confirm source HTTPS and destination HTTP/port settings carefully

### Prevention
- diagram the ingress chain when debugging
- avoid changing multiple layers blindly at once

## 8. Shortcut still sends old `text` / `time` fields

### Symptom
- the Shortcut looks like it sends data, but the contract does not match the current API

### Root cause
- the Shortcut still uses an older payload shape

### Fix
- update the body to:
  - `original_text`
  - `source`
  - `raw_ocr`
- optionally add `book_alias` or `year`

### Prevention
- keep the Shortcut doc aligned with the current API
- after any API evolution, verify the body field names end to end

## 9. `Get Dictionary from Input` / `Get Value for message` is wrong

### Symptom
- the request succeeds, but the Shortcut cannot extract the success message correctly

### Root cause
- the Shortcut response-parsing actions were configured with the wrong source or key

### Fix
- keep `Get Dictionary from Input` on the default `Input`
- set `Get Value for Dictionary Value` to the key `message`

### Prevention
- avoid over-customizing the response parsing unless needed
- verify parsing right after the first successful request

## 10. Missing `.env`, port, cert, or `record_id`

### Symptom
- the service will not start, or starts but fails later when handling requests

### Root cause
- required runtime config is missing
- port mapping is wrong
- certificate setup is incomplete
- the selected target lacks a valid `record_id`

### Fix
- verify `.env` or external env file loading
- verify `FEISHU_TARGETS_FILE` path if using dynamic mode
- verify container port exposure and reverse-proxy mapping
- verify the target record exists in Feishu

### Prevention
- use the checked-in examples as templates
- verify health and one real webhook request after every config change

## 11. Annual switching still writes to the old target

### Symptom
- you updated a config file, but requests still update the old annual book

### Root cause
- the service is reading a different env file than expected
- container environment variables override file values
- the TOML registry file was not reloaded yet
- the wrong `default_alias` or wrong target entry is still active

### Fix
- inspect the active `FEISHU_ENV_FILE` and `FEISHU_TARGETS_FILE`
- inspect container environment variables
- check `/health` and logs for `config_valid` / reload behavior
- call `POST /admin/config/reload` if configured
- verify the current `default_alias` and target entry contents

### Prevention
- keep one clear runtime source of truth
- update the target registry and verify immediately with one real request
- avoid mixing old legacy env target values with dynamic mode unless intentional
