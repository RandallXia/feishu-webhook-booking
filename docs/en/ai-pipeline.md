# AI extraction pipeline

## Language

- English: [this page](ai-pipeline.md)
- Chinese: [AI 提取管线](../ai-pipeline.md)

## 1. Background

Feishu Bitable's built-in "AI auto-fill" automation stops working once the monthly AI quota is exhausted, so the `精简原始数据` and `账单明细` fields stop being generated and the whole bookkeeping chain breaks.

When `AI_ENABLED=true`, this service takes over that step: it runs a single structured AI call against the OCR text and writes the result back to Feishu. Quota is governed by your own AI provider, not by Feishu's monthly cap.

## 2. Architecture flow

```
iPhone Shortcut OCR
        │
        ▼
POST /v1/webhook/ocr  (original_text)
        │
        ▼
[1] auth X-Webhook-Token
        │
        ▼
[2] registry.resolve(book_alias|year) → target
        │
        ▼
[3] feishu.update_original_text(text, target)        ← writes the 原始信息 field (contract unchanged)
        │
        ▼
(AI stage below only runs when AI_ENABLED=true)
        │
        ▼
[4] ai_registry.maybe_reload() → snapshot             ← mtime hot reload + whitelist snapshot
        │
        ▼
[5] pipeline.run(text, target, profile, whitelists)
        │
        ├─ dedup hit → ai_status="duplicate" (skips AI call)
        │
        ├─ ai_extractor.extract(text) → ExtractionResult (7 fields)
        │
        ├─ field_codec.encode_fields(result, specs, whitelists)
        │     ├─ extract_fields  → writeback to 精简原始数据 (best-effort)
        │     └─ bill_fields     → create a 账单明细 record (client_token idempotent)
        │
        └─ PipelineResult(ai_status, bill_record_id, warnings, extracted)
        │
        ▼
[6] 200 response + ai_status / ai_record_id / ai_extracted / ai_warnings
```

Key invariant: step [3]'s "write 原始信息" semantics are unchanged. The AI stage runs after it, and `AiPipeline.run()` never raises (failures still return 200 + `ai_status="failed"`). The one exception is `AiProfileRegistryUnavailableError` (bad profile / `list_fields` failure) → 503, which prevents dirty `single_select` options from polluting the Feishu table.

## 3. Environment variables

All `AI_*` variables are optional when `AI_ENABLED=false` (`get_settings()` skips validation).

| Variable | Required when | Default | Description |
|----------|---------------|---------|-------------|
| `AI_ENABLED` | — | `false` | Master switch for the AI pipeline. When `true`, the 4 variables below become required |
| `AI_PROVIDER` | `AI_ENABLED=true` | — | Protocol type, only `anthropic` or `openai` |
| `AI_BASE_URL` | optional | see below | AI service base URL. **Concatenation rule in section 11**; wrong config → 404 |
| `AI_API_KEY` | `AI_ENABLED=true` | — | API key. `anthropic` uses `x-api-key` header; `openai` uses `Bearer` |
| `AI_MODEL` | `AI_ENABLED=true` | — | Model name, e.g. `claude-3-5-sonnet-20241022` / `gpt-4o` |
| `AI_TIMEOUT_SECONDS` | optional | `20` | Per-call HTTP timeout; timeout → `AiExtractorError(stage="request")`, no retry |
| `AI_PROFILE_FILE` | `AI_ENABLED=true` | — | Path to `ai-profile.toml`; `:ro` mount is fine, mtime hot reload |
| `AI_PROFILE_RELOAD_INTERVAL_SECONDS` | optional | `10` | Minimum interval between mtime checks for profile hot reload |
| `AI_DEDUP_TTL_SECONDS` | optional | `300` | Dedup window for the same `(alias, original_text)`; a hit returns `ai_status="duplicate"` without calling AI or creating a record |

### `AI_BASE_URL` concatenation rule

This is the easiest variable to get wrong — be sure to differentiate by provider:

- **anthropic**: base **excludes** `/v1`; the service appends `{base}/v1/messages`
  - Official default: `https://api.anthropic.com` (used when left empty)
  - Relay example: `https://your-relay.example.com` (**do not** include `/v1`)
- **openai**: base **includes** `/v1`; the service appends `{base}/chat/completions`
  - Official default: `https://api.openai.com/v1` (used when left empty)
  - Relay example: `https://your-relay.example.com/v1`

See [section 11](#11-ai_base_url-concatenation-rule) for details.

## 4. TOML config schema

Template: [runtime/ai-profile.toml.example](../../runtime/ai-profile.toml.example). Key fields:

```toml
# prompt_header MUST appear BEFORE any [section] header — TOML rule:
# a bare key after a section header attaches to that section, not the top level.
prompt_header = "You are a bill extraction assistant. Extract bill fields from the OCR text."

[extract]
summary_field = "精简原始数据"          # summary field name written back to the original-text record

[bill]
app_token = "bascnXXXXXXXXXXXXXXXX"   # app_token of the Bitable that holds the bill table
table_id = "tblXXXXXXXXXXXXXXXX"       # bill table table_id (no record_id — a new record is created each time)

# Field definition list; order = AI extraction order
[[fields]]
ai_key = "summary"                     # matches an ExtractionResult attribute
feishu_field = "精简原始数据"           # Feishu table field name
type = "text"                          # text | number | single_select | date | passthrough
target = "extract"                     # extract (summary writeback) | bill (new bill record)
prompt = "Distill a concise summary of this OCR bill text, keeping merchant/amount/time/payment method"

[[fields]]
ai_key = "category"
feishu_field = "收支分类"
type = "single_select"
fallback = "其他"                      # single_select must have a fallback, and it must already exist as a Feishu option
target = "bill"
prompt = "Bill category, e.g. 餐饮/交通/购物/日用/娱乐/医疗/其他"

[[fields]]
ai_key = "bill_date"
feishu_field = "账单日期"
type = "date"                          # YYYY-MM-DD or YYYY-MM-DD HH:mm → Asia/Shanghai millisecond timestamp
target = "bill"
prompt = "Bill date in YYYY-MM-DD HH:mm (with time); if unknown, use today"

[[fields]]
ai_key = "raw_source"
feishu_field = "原始采集账单数据"
type = "passthrough"                   # bypasses AI; copies extraction.summary directly
target = "bill"
source = "summary"                     # passthrough requires source="summary"
prompt = ""
```

### Field types

| type | Behavior | Failure handling |
|------|----------|------------------|
| `text` | Stripped and written; empty string skipped with a warning | — |
| `number` | `float()` passthrough | AI layer already validates `amount>0` |
| `single_select` | Value must be in the whitelist; otherwise use `fallback` | `fallback` also missing from whitelist → `ValueError` → `ai_status="failed"` |
| `date` | `YYYY-MM-DD` or `YYYY-MM-DD HH:mm` → Asia/Shanghai ms timestamp (HH:mm preserved when present, midnight when date-only) | Bad format → falls back to today (Shanghai tz) + warning |
| `passthrough` | Copies `extraction.summary` directly | `source` not `"summary"` → `ProfileConfigError` at startup |

### Parse constraints

`parse_profile` enforces these at startup; failure raises `ProfileConfigError` (`lifespan` does not swallow it, the service will not start):

- `prompt_header` non-empty and placed before all `[section]` headers
- `[extract]` / `[bill]` sections required; `[bill].app_token` / `table_id` non-empty
- At least one `[[fields]]` entry; `ai_key` unique
- `single_select` must have `fallback`; `passthrough` must have `source="summary"`
- **Exactly one** field with `target="extract"`, and its `feishu_field` must equal `[extract].summary_field`

### Whitelist validation

After loading the profile, `AiProfileRegistry._load_snapshot` calls `feishu.list_fields(bill_app_token, bill_table_id)` to fetch the real options and validates, for every `single_select` field:

- The field exists in the Feishu table
- The option list is non-empty
- The `fallback` from the profile is present in the option list

Any failure → `config_valid=False` → the route returns 503 `AI_PROFILE_UNAVAILABLE`. This is the pollution-prevention bottom line: it prevents a fallback that is itself a dirty option from triggering Feishu to auto-create an irreversible dirty option.

## 5. Webhook response fields

When `AI_ENABLED=true`, the 200 response of `POST /v1/webhook/ocr` adds 4 `ai_*` fields on top of the original 5 (`response_model_exclude_none=True` keeps the response byte-identical when `AI_ENABLED=false`):

| Field | Type | Present when | Description |
|-------|------|--------------|-------------|
| `ai_status` | `str` | AI stage ran | `succeeded` / `failed` / `duplicate` |
| `ai_record_id` | `str\|null` | succeeded | The newly created 账单明细 record ID |
| `ai_warnings` | `list[str]\|null` | non-empty | Non-fatal encoding issues (empty text skipped, option fallback, date fallback, summary writeback failure) |
| `ai_extracted` | `dict\|null` | succeeded | `{amount, category, flow_type, description}` for Shortcut notifications |

### Shortcut notification example

`ai_extracted` is designed for the iPhone Shortcut notification. Example response:

```json
{
  "success": true,
  "request_id": "req_abc123",
  "record_id": "recXXXX",
  "book_alias": "2026",
  "message": "configured record updated and 原始信息 updated",
  "ai_status": "succeeded",
  "ai_record_id": "recYYYYYYYY",
  "ai_extracted": {
    "amount": 42.0,
    "category": "餐饮",
    "flow_type": "支出",
    "description": "星巴克咖啡"
  }
}
```

The Shortcut can render a "¥42 餐饮 已入账" notification with this text template:

```
¥{{ai_extracted.amount}} {{ai_extracted.category}} 已入账
```

When `ai_status="failed"`, `ai_extracted` is `null`; the Shortcut should branch to "AI extraction failed, original text saved".

## 6. Admin endpoints

All three AI admin endpoints require `CONFIG_RELOAD_TOKEN` (404 when unset, so the endpoint's existence is not leaked). Auth header is `X-Admin-Token`.

### `POST /admin/ai/test` — dry-run extraction

Does not write to Feishu. Runs `AiExtractor.extract` + `encode_fields` once and returns the encoded result for prompt iteration.

Request body:

```json
{ "text": "paste an OCR text sample to test" }
```

Response (success):

```json
{
  "ai_status": "succeeded",
  "extracted": { "summary": "...", "description": "...", "amount": 42.0, ... },
  "bill_fields": { "金额": 42.0, "收支分类": "餐饮", ... },
  "summary_writeback": { "field": "精简原始数据", "value": "..." },
  "warnings": []
}
```

Response (AI failure, still 200):

```json
{ "ai_status": "failed", "error": "Anthropic returned HTTP 401" }
```

`AiProfileRegistryUnavailableError` (bad profile) → 503, same as the webhook route.

### `GET /admin/ai/profile` — profile inspection

Returns the current profile + whitelists + registry status. Still 200 when fail-closed (`profile` / `whitelists` are `null`, but `registry.config_valid=false` + `last_reload_error` are exposed for debugging).

### `GET /admin/ai` — config page

Returns the HTML page at `app/static/admin.html`, which embeds a UI that calls the two endpoints above. The page itself is accessible without auth (the in-page JS still needs `X-Admin-Token` to call the APIs).

## 7. Config page usage

1. Open `http://<host>:2398/admin/ai` in a browser
2. Enter `CONFIG_RELOAD_TOKEN` at the top of the page
3. The page calls `GET /admin/ai/profile` and renders the current profile field mappings + whitelists + registry generation
4. Paste an OCR sample in the "Dry run" box and click test → calls `POST /admin/ai/test`; the extracted 7 fields + encoded `bill_fields` + warnings appear below
5. After editing `ai-profile.toml`, click "Reload" → calls `POST /admin/config/reload` to force-refresh the registry snapshot

Use case: iterate on prompts without triggering a real webhook or hand-writing curl.

## 8. Migration checklist

> ⚠️ These steps **must** be completed before switching to the self-hosted AI pipeline. Missing any step can cause duplicate bookkeeping.

### Before switching

- [ ] **Disable Feishu's "AI auto-fill" automation**: in the Bitable "Automations" entry, find the original "AI auto-fill" flow and disable it
- [ ] **Disable the two AI automation flows**: the two automations in the old chain that depend on Feishu AI (generating `精简原始数据` and generating `账单明细`) — disable both
- [ ] In the Feishu "账单明细" table, confirm that the `single_select` fields (`收支类型` / `收支分类` / `支付途径`) have their options created and that they match the `fallback` values in `ai-profile.toml`
- [ ] Prepare AI provider credentials (`AI_API_KEY` / `AI_MODEL` / `AI_BASE_URL`)
- [ ] Copy `runtime/ai-profile.toml.example` → `runtime/ai-profile.toml` and fill in the real `app_token` / `table_id`
- [ ] Set `AI_ENABLED=true` and the rest of the `AI_*` variables in `runtime/feishu-webhook.env`
- [ ] Restart the service, confirm no `ProfileConfigError` in logs, `GET /admin/ai/profile` returns 200 with `registry.config_valid=true`
- [ ] Run 1-2 real OCR samples through `POST /admin/ai/test` and confirm the `bill_fields` values look reasonable

### After switching

- [ ] Send a real webhook, confirm a new record appears in the 账单明细 table and `精简原始数据` is written back
- [ ] Confirm the Shortcut receives the `ai_extracted` field for notifications

### Risk: Feishu AI quota resets monthly

Feishu AI quota refreshes monthly. **If the old automations are not disabled**, they will re-trigger when the quota recovers at the start of the next month and write **simultaneously** with the self-hosted AI pipeline → duplicate bookkeeping.

Recommend a monthly check of the Feishu automations list to confirm the old flows are still disabled.

### Known limitation: bill table "记账日期" formula

The "记账日期" field in the 账单明细 table should be generated by a formula (`TODATE(账单日期)`); this service does not write that field. **Please manually confirm in Feishu that the formula exists and references the 账单日期 field.** If the formula is missing, the 记账日期 column will be empty — this is a Feishu-side configuration issue, not a service bug; create the formula in the Feishu formula editor.

## 9. Bitable URL key extraction

A Feishu Bitable browser URL looks like:

```
https://xxx.feishu.cn/base/{app_token}?table={table_id}&view=...
```

- `app_token`: the segment after `/base/` in the URL path
- `table_id`: the value of the `table=` query parameter

Fill these into the `[bill]` section of `ai-profile.toml`.

**Note**: Feishu "record share links" use opaque tokens and **cannot** be parsed into a `record_id`. The 账单明细 table is always created via `create_record`, so it does not need a `record_id`; if you need a record's `record_id`, get it from the browser address bar or the Feishu API.

## 10. Whitelist refresh

`AiProfileRegistry` calls `feishu.list_fields` at snapshot load time to fetch the `single_select` option list, and does **not** automatically detect new options added in the Feishu UI afterwards.

After adding options to `收支类型` / `收支分类` / `支付途径` in the Feishu Bitable UI, you must trigger a profile reload to bring the new options into the whitelist; otherwise AI-extracted new values will keep hitting the `fallback`. Two ways to trigger:

1. **Manual API**: `POST /admin/config/reload` (with `X-Admin-Token`) forces a registry snapshot refresh
2. **Touch the TOML**: modify `ai-profile.toml`'s mtime (even just saving with a trailing space), and the next request's `maybe_reload()` will reload automatically

Symptom of a stale whitelist: logs repeatedly show `option fallback: 收支分类: 'xxx' -> '其他'` warnings.

## 11. `AI_BASE_URL` concatenation rule

The service hard-codes the path concatenation per provider; relay/proxy endpoints are easiest to misconfigure here:

| provider | base includes `/v1`? | service appends | official default (when empty) |
|----------|----------------------|-----------------|-------------------------------|
| `anthropic` | **no** | `{base}/v1/messages` | `https://api.anthropic.com` |
| `openai` | **yes** | `{base}/chat/completions` | `https://api.openai.com/v1` |

### Common mistakes

- anthropic relay written as `https://relay.example.com/v1` → actual request `https://relay.example.com/v1/v1/messages` → 404
- openai relay written as `https://relay.example.com` (missing `/v1`) → actual request `https://relay.example.com/chat/completions` → most relays return 404

### How to verify

After configuring, call `POST /admin/ai/test` once:

- Returns `ai_status="succeeded"` → concatenation is correct
- Returns `ai_status="failed"` with `error` containing `HTTP 404` → 99% chance the base URL is mis-concatenated

## 12. Config UI

Beyond the dry-run + manual reload in section 7, the `/admin/ai` page also provides visual editing of targets / profile / env. This section covers the UI's usage contracts and limits.

### URL format

Only **direct** bitable links are supported:

```
https://xxx.feishu.cn/base/{app_token}?table={table_id}&view=...
```

- `app_token`: the segment after `/base/` in the URL path
- `table_id`: the value of the `table=` query parameter

Copy from the browser address bar. **Wiki links are not supported** (the path has no `/base/`); pasting one yields `UNSUPPORTED_URL` — open the bitable itself and copy its address-bar URL.

### Config flow

1. Paste URL → `POST /admin/feishu/parse-url` extracts `app_token` + `table_id`
2. Pick table → `GET /admin/feishu/tables` lists tables under the app
3. Pick record → `GET /admin/feishu/records` paginates records (each with a `preview`)
4. Field mapping → `GET /admin/feishu/fields` fetches field metadata; the UI auto-derives a prefill mapping
5. Save → validate + atomic write (tmp + `os.replace`) + hot reload (registry.reload)

### AI connection

The UI can edit `AI_PROVIDER` / `AI_MODEL` / `AI_BASE_URL` / `AI_TIMEOUT_SECONDS` / `AI_API_KEY`. `AI_API_KEY` is **write-only**: GET ever returns only the `ai_api_key_set` boolean, never the key value.

> ⚠️ **Restart required after save**: env is loaded into `Settings` (a frozen dataclass) at startup; PUT only edits the file, it does NOT refresh in-memory state. GET reflects the running `Settings`, not the just-written file — hence GET's `restart_required` is always `true`.

### TOML comment loss

A UI save **rewrites the whole TOML file** (via `app/toml_writer.py`'s deterministic serializer); hand-written comments are lost. A `.bak` backup (`<file>.toml.bak`) is auto-created before every save (holding the previous version). The `.example` templates are **never written** — different filename; the UI only writes `runtime/feishu-targets.toml` / `runtime/ai-profile.toml`.

### Docker read-only mount

When the `runtime/` volume is mounted read-only (`docker-compose.yml` defaults to `./runtime:/runtime:ro`), `os.replace` raises `PermissionError`, the save button returns `409 RUNTIME_READONLY`, and the UI shows a red banner prompting a host-side edit followed by `POST /admin/config/reload`. The original file is NOT corrupted (tmp + replace is an atomic swap; a failed replace leaves the original byte-identical).

### New admin endpoints

- `GET /admin/feishu/tables` — list bitables under a given app_token
- `GET /admin/feishu/fields` — list field metadata for a table (type + is_primary)
- `GET /admin/feishu/records` — paginate records, each with a `preview` (is_primary field value, truncated to 80 chars)
- `POST /admin/feishu/parse-url` — parse a `/base/{app_token}?table={table_id}` direct link; wiki links → 422 `UNSUPPORTED_URL`
- `GET /admin/config/profile` — read the editable profile shape (degrades 200 + profile=null when fail-closed)
- `PUT /admin/config/profile` — validate + atomic save + hot reload (base_generation guard; concurrent → 409 `STALE_WRITE`)
- `GET /admin/config/targets` — read the targets snapshot (legacy mode returns one; fail-closed degrades 200 + targets=null)
- `PUT /admin/config/targets` — full-replace save + hot reload (legacy mode → 409 `LEGACY_MODE`)
- `GET /admin/config/env` — read AI connection settings (secrets return only `*_set` booleans, never values)
- `PUT /admin/config/env` — line-based env file edit (preserves comments + non-AI lines; no file → 409 `ENV_FILE_NOT_FOUND`)

## 13. Field enable toggle

Each `[[fields]]` entry supports an optional `enabled` boolean key that controls, per field from the config UI, whether its value is written to Feishu. This is a presentation-layer toggle only — it does not change the AI extraction's fixed schema, only whether the result is persisted.

### Semantics

- **Disabled ≠ not extracted**: the AI model still extracts all 7 fields per the fixed schema (keeping the schema stable so re-enabling is cheap), but a disabled field's extracted value is dropped at the `encode_fields` stage — it never enters `extract_fields` / `bill_fields`, so it is never written to Feishu. Re-enabling restores the write without re-running AI.
- **summary row (`target="extract"`)**: the core writeback field (writes back `精简原始数据`); the UI hard-locks it always-on and disables the toggle. The backend still respects `enabled=false` defensively (if disabled via raw TOML, the writeback branch is skipped, but bill-record creation is unaffected).
- **All disabled**: when every `target="bill"` field is disabled, `bill_fields` is empty, and the pipeline skips `create_record` (avoiding a blank record with only the `client_token` idempotency guard), appending an `all bill fields disabled — record not created` warning. `ai_status` stays `succeeded` (the extraction itself succeeded).
- **Response filtering**: the webhook response's `ai_extracted` (4 keys: amount/category/flow_type/description) is also filtered by `enabled` — a disabled field's value is not returned, so the Shortcut notification does not show a value the user turned off.

### TOML shape

```toml
[[fields]]
ai_key = "category"
feishu_field = "收支分类"
type = "single_select"
fallback = "其他"
target = "bill"
prompt = "Bill category, e.g. 餐饮/交通/购物/日用/娱乐/医疗/其他"
enabled = false                  # disable this field: AI still extracts but does not write to Feishu (default = true)
```

The `enabled` key defaults to `true`, so existing TOML profiles (without the key) behave identically — no migration needed on upgrade. A config-UI save serializes the key via `dump_profile` (written explicitly whether true or false, for clean UI roundtrip).
