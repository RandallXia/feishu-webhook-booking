# app/ — FastAPI Service Package

9 modules + `static/`, no `__init__.py` (implicit namespace package — keep relative imports `from .xxx import`). All logic is here; see root `AGENTS.md` for project-level map.

9 个模块 + `static/`，无 `__init__.py`（隐式命名空间包）。项目级信息见根目录 `AGENTS.md`。

## WHERE TO LOOK

| File | Symbols | Purpose |
|------|---------|---------|
| `main.py` | `WebhookRequest`, `WebhookSuccessResponse`, `lifespan`, `app`, `ingest_ocr`, `reload_config`, `ai_test_dry_run`, `ai_profile_inspect`, 4 picker routes, 6 config routes (`config_profile_get/put`, `config_targets_get/put`, `config_env_get/put`) | 16 routes + error envelopes; single entry point; AI wiring |
| `config.py` | `Settings`, `get_settings`, `_load_runtime_env_files`, `env_file_path` | Env parsing; **executes at import time** (env files loaded as module side effect, line 55) |
| `feishu_client.py` | `FeishuClient`, `FeishuClientError`, `TokenCache` | Feishu Open API: tenant token + bitable record PUT/POST + list_fields/list_tables/list_records |
| `target_registry.py` | `TargetRegistry`, `FeishuTargetConfig`, `TargetRegistrySnapshot`, 4 error classes, `validate_targets` | TOML/env target resolution + hot reload; PUT-side schema checks |
| `ai_extractor.py` | `AiExtractor`, `ExtractionResult`, `AiExtractorError` | Dual-protocol (anthropic/openai) structured extraction; prompt-stateless; forced tool_call |
| `field_codec.py` | `FieldSpec`, `encode_fields` | ExtractionResult → Feishu field dicts; whitelist-guarded single_select; Asia/Shanghai date |
| `ai_profile.py` | `AiProfile`, `parse_profile`, `parse_profile_text`, `validate_profile_candidate`, `build_field_prompts`, `AiProfileRegistry`, `AiProfileSnapshot`, `ProfileConfigError`, `AiProfileRegistryUnavailableError` | TOML profile parser + async hot-reload registry + pre-loaded whitelist snapshot + PUT-side validation |
| `pipeline.py` | `AiPipeline`, `PipelineResult` | AI orchestration: extract → encode → writeback → create; `run()` never raises |
| `toml_writer.py` | `dump_targets`, `dump_profile` | Deterministic TOML serialization for config UI saves (comment-loss expected; `.bak` pre-save) |
| `static/admin.html` + `admin.js` + `admin.css` | — | AI config page served at `GET /admin/ai` (no auth on page; JS calls need `X-Admin-Token`) |

## Request Flow (ingest_ocr)

```
X-Webhook-Token → secrets.compare_digest → 401
→ strip original_text → empty → 422
→ registry.maybe_reload()          # mtime check, throttled (default 10s)
→ registry.resolve(book_alias, year)
→ feishu_client.update_original_text(text, target)
   ├─ _get_tenant_access_token()   # cached, refresh skew 300s, min 60s TTL
   └─ PUT /bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}

# AI stage — runs ONLY when AI_ENABLED=true, AFTER update_original_text succeeds.
# The pipeline never raises; an AI failure still returns 200 + ai_status="failed".
# The ONE 5xx the AI stage can introduce is AiProfileRegistryUnavailableError (503).
if settings.ai_enabled and ai_pipeline is not None and ai_registry is not None:
   → ai_registry.maybe_reload()                     # async mtime hot reload
   → ai_registry.get_snapshot()                     # raises AiProfileRegistryUnavailableError if fail-closed → 503
   → ai_pipeline.run(text, target, profile, whitelists)
      ├─ dedup hit (sha256(alias:text), TTL) → ai_status="duplicate"
      ├─ ai_extractor.extract(text, prompt_header, field_prompts) → ExtractionResult(7 fields)
      ├─ encode_fields(result, specs, whitelists) → (extract_fields, bill_fields, warnings)
      ├─ feishu.update_record_field(summary_field, value, target)   # best-effort 精简原始数据 writeback
      └─ feishu.create_record(bill_fields, bill_app_token, bill_table_id, client_token)  # idempotent
   → map PipelineResult → ai_status / ai_record_id / ai_warnings / ai_extracted response fields
```

## Error Taxonomy (main.py maps exception → HTTP)

| Exception | HTTP | Code |
|-----------|------|------|
| `TargetSelectorError` | 422 | `INVALID_TARGET_SELECTOR` |
| `TargetRegistryUnavailableError` | 503 | `TARGET_REGISTRY_UNAVAILABLE` |
| `TargetRegistryError` (other) | 503 | `TARGET_REGISTRY_UNAVAILABLE` |
| `FeishuClientError` | 502 | `FEISHU_UPSTREAM_ERROR` |
| `AiExtractorError` | — (200) | internalized by `AiPipeline.run` → `ai_status="failed"` (original text already written; never 5xx) |
| `AiProfileRegistryUnavailableError` | 503 | `AI_PROFILE_UNAVAILABLE` (fail-closed registry; prevents single_select option pollution) |
| bare `Exception` | 500 | `INTERNAL_ERROR` (the `# noqa: BLE001` at main.py:200 is deliberate catch-all) |

Config-UI route-level error codes (raised as `HTTPException` directly, not via exceptions; all bodies carry `request_id` + `error.code`/`error.message`):

| HTTP | Code | Route | Trigger |
|------|------|-------|---------|
| 404 | `AI_DISABLED` | config profile/targets GET+PUT, ai/test | `AI_ENABLED=false` or registry missing |
| 409 | `RUNTIME_READONLY` | config profile/targets/env PUT | `os.replace` raises `PermissionError` (Docker `:ro`); `suggested_action="host-edit"` |
| 409 | `STALE_WRITE` | config profile/targets PUT | `base_generation` != registry's current generation (optimistic-concurrency guard) |
| 409 | `LEGACY_MODE` | config targets PUT | `FEISHU_TARGETS_FILE` unset (legacy single-target mode) |
| 409 | `ENV_FILE_NOT_FOUND` | config env PUT | `settings.env_file_path is None` (env set directly, no file) |
| 422 | `UNSUPPORTED_URL` | `/admin/feishu/parse-url` | URL not a `/base/{app_token}?table={table_id}` direct link (e.g. wiki link) |

New failure paths: extend `stage` on `FeishuClientError`, don't invent new response shapes. All error bodies carry `request_id` + `error.code`/`error.message`. The AI pipeline's `AiPipeline.run()` catches `Exception` itself and returns `PipelineResult(ai_status="failed")`, so the only AI-originated 5xx path is `AiProfileRegistryUnavailableError` raised by `get_snapshot()` in the route (before `pipeline.run` is called).

## CONVENTIONS

- `resolve()` rules: both `book_alias` + `year` given → must resolve to the SAME target (else 422); neither → `default_alias` (must be `enabled=true`); disabled target → 422.
- TOML parsing: alias regex `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`; duplicate `year` → config error; `original_field_name` falls back to settings default (`原始信息`).
- Registry state swaps whole immutable snapshots under `threading.Lock`; readers copy the snapshot reference then work lock-free. Keep this pattern — no in-place mutation. The `AiProfileRegistry` mirrors this but async (snapshot assembly calls `await feishu.list_fields`, so all IO methods are `async def`); lock scope = snapshot reference swap ONLY.
- `httpx.AsyncClient` is created per call (no pooled client) — acceptable at this traffic level; don't "fix" without a perf reason. Pattern shared by `feishu_client.py` and `ai_extractor.py`.
- Config errors never crash the running server: `_load_snapshot` sets `config_valid=False` and re-raises; routes then 503. Fail-closed by design. Contract asymmetry: `load_initial()` propagates (fail-fast at startup); `maybe_reload()` swallows `ProfileConfigError`/`FeishuClientError` (a hot-reload glitch must not crash a running server — leaves registry fail-closed, route's `get_snapshot()` raises the typed 503).
- `FeishuClientError` carries `stage` (`get_tenant_access_token` / `update_original_text` / `create_record` / `list_fields`) + optional `record_id` — always fill both when raising.
- AI pipeline never raises: `AiPipeline.run()` top-level `try/except Exception` → `PipelineResult(ai_status="failed")`. AI dedup is in-memory + success-only: `dict[sha256(alias:original_text), ts]` with TTL `AI_DEDUP_TTL_SECONDS`; a failed extraction is NOT recorded (legal retry allowed). `client_token = "ai-bill-" + dedup_key[:40]` makes `create_record` idempotent.
- `single_select` pollution prevention: `encode_fields` checks values against the whitelist snapshot; unknown values hit the spec's `fallback`. `AiProfileRegistry._load_snapshot` pre-validates that every `fallback` is a real Feishu option — refuse to serve (503) if not, since a dirty fallback would trigger Feishu to auto-create an irreversible dirty option.
- `response_model_exclude_none=True` on the webhook route drops `ai_*` keys when `None`, so the disabled-mode response is byte-identical to master (regression-locked by `test_regression_disabled_response_has_5_keys`).

## ANTI-PATTERNS

- Don't widen `WebhookRequest` fields — `extra="forbid"` is the credential-leak guard; new fields need a security reason.
- Don't log `original_text` or tokens; current log lines log only ids/source/alias/year/stage — keep it that way.
- Legacy mode (`FEISHU_TARGETS_FILE` unset) is compatibility-only; don't build new features on it.
