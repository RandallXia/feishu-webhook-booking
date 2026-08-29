# Learnings — ai-bill-extraction-pipeline

Conventions, patterns, and successful approaches discovered during work on this plan.

_Auto-scaffolded by /start-work. Append new entries below - never overwrite._

---

- 2026-08-28: Test infrastructure setup (branch feature/ai-extraction-pipeline)
  - .gitignore: removed test/ and requirements-dev.txt lines
  - requirements-dev.txt: pytest==8.3.4, pytest-asyncio==0.24.0, asgi-lifespan==0.1.0, tomli==2.2.1
  - conftest.py: env pinning BEFORE any app import (config.py:55 side effect), plus autouse fixture clearing get_settings cache
  - pytest.ini: asyncio_mode=auto, testpaths=test/unit
  - test/unit/test_smoke.py: 2 smoke tests (app imports, legacy mode pinned)
  - tomli added as dev dep for Python 3.10 compat (tomllib is 3.11+ stdlib)
  - conftest includes sys.modules shim: `import tomli as tomllib; sys.modules["tomllib"] = tomllib` for Python 3.10
  - All 2 tests pass on Python 3.10

- 2026-08-28: FeishuClient extension (Phase B) — commit 7c79c90
  - Added `update_record_field(field_name, value, target)` — generalized PUT, delegates the existing `update_original_text` logic
  - Refactored `update_original_text` to delegate to `update_record_field` — preserves signature, no behavior change
  - Added `create_record(fields, app_token, table_id, client_token)` — POST with idempotent client_token query param; duplicate detection via code 1214004/1121004 or "client_token" in msg
  - Added `list_fields(app_token, table_id)` — GET with pagination, returns merged dict of field_name -> field_def
  - Added `import logging` + `logger = logging.getLogger("feishu_webhook_service.feishu_client")`
  - 11 tests in test/unit/test_feishu_client_ext.py using httpx.MockTransport + monkeypatch to inject transport into per-call AsyncClient
  - All 24 tests pass (11 feishu_client_ext + 11 config_ai + 2 smoke)

- 2026-08-28: AiExtractor dual-protocol adapter (Phase C — todo 3)
  - app/ai_extractor.py: AiExtractor class, prompt-stateless (prompts passed per-call to extract(), NOT stored in __init__)
  - Dual dispatch on settings.ai_provider: "anthropic" → POST {base}/v1/messages (x-api-key header, content[].tool_use.input); "openai" → POST {base}/chat/completions (Bearer header, choices[0].message.tool_calls[0].function.arguments JSON string)
  - base_url fallback: None → anthropic=https://api.anthropic.com, openai=https://api.openai.com/v1
  - Forced tool_call via tool_choice (anthropic: {"type":"tool","name":"submit_bill"}; openai: {"type":"function","function":{"name":"submit_bill"}}) — guarantees structured output
  - ExtractionResult: frozen dataclass, 7 fields (summary/description/flow_type/amount:float/category/payment_method/bill_date YYYY-MM-DD)
  - _validate_input(): all 7 keys present, amount>0 (float), bill_date regex ^\d{4}-\d{2}-\d{2}$, 5 string fields non-empty → else AiExtractorError(stage="validate")
  - Error stages: request (httpx.TimeoutException/HTTPError/non-200), parse (JSON decode / unexpected shape), validate (field checks)
  - Single call, NO retry (test asserts call_count==1 on timeout)
  - Per-call httpx.AsyncClient(timeout=self._timeout) — matches feishu_client.py:46,90 pattern
  - logger.info logs ONLY provider/model/stage=success/elapsed_ms — never original_text or ai_api_key (grep-verified)
  - 14 tests in test/unit/test_ai_extractor.py using httpx.MockTransport + _patch_httpx monkeypatch (same as test_feishu_client_ext.py)
  - Test Settings built via dataclasses.replace(get_settings(), ai_*=...) to bypass env validation (conftest pins AI_ENABLED=false)
  - All 38 tests pass (14 ai_extractor + 11 feishu_client_ext + 11 config_ai + 2 smoke)

- 2026-08-28: FieldCodec (todo 5) — commit 34f4053
  - app/field_codec.py: FieldSpec (frozen dataclass, 7 attrs) + encode_fields(extraction, specs, option_whitelists) → (extract_fields, bill_fields, warnings)
  - 5 field types: text (strip, skip empty → warning), number (float passthrough), single_select (whitelist guard → fallback → raise), date (YYYY-MM-DD → Shanghai midnight ms timestamp, bad format → today fallback), passthrough (source="summary" → extraction.summary)
  - single_select uses exact string match (no strip) — trailing space → fallback, preventing Feishu auto-creating dirty options
  - date always uses Asia/Shanghai, never UTC; bad format fallback to today in Shanghai
  - Routing: spec.target "extract" vs "bill" → separate dicts
  - 12 tests in test/unit/test_field_codec.py — pure function tests, no fixtures, no IO
  - 12 tests use _shanghai_midnight_ms() helper to compute expected timestamps from ZoneInfo (no magic numbers)
  - All 50 tests pass (12 field_codec + 14 ai_extractor + 11 feishu_client_ext + 11 config_ai + 2 smoke)

- 2026-08-28: AiProfile static parser + AiPipeline orchestrator (todo 6) — commit 707ebf2
  - app/ai_profile.py: parse_profile(path: Path) -> AiProfile. Stateless — no registry, no hot reload (todo 8 adds AiProfileRegistry on top)
  - TOML structure: top-level `prompt_header` (MUST appear before [extract]/[bill]/[[fields]] — TOML rule: bare keys attach to current table; a key after [bill] becomes bill.prompt_header, NOT top-level)
  - Validation order: extract section → bill section → prompt_header → fields list → per-field (ai_key unique, type in {text,number,single_select,date,passthrough}, target in {extract,bill}, single_select must have fallback, passthrough must have source="summary") → exactly one target="extract" → summary_field == extract spec's feishu_field
  - ProfileConfigError messages always include the offending key name (ai_key/field name) for the regex match in tests
  - app/pipeline.py: AiPipeline.run() — 6-step orchestration (dedup check → extract → encode → summary writeback → bill create → record dedup). NEVER raises (top-level try/except Exception → PipelineResult(ai_status="failed") + logger.exception)
  - _OptionWhitelistCache: Wave 1 bridge, lazy fetches bill-table single_select options via feishu.list_fields OUTSIDE the lock, swaps the dict ref INSIDE the lock. property.options[].name extraction. Deleted in todo 8 when registry exposes pre-loaded whitelists
  - Dedup: in-memory dict[str, float] keyed by sha256(f"{alias}:{original_text}")[:full], TTL = settings.ai_dedup_ttl_seconds (300s default). Dedup recorded ONLY on success (failed AI / failed create → no dedup entry, legal retry allowed). Dedup check reads stored_ts under lock, compares outside lock
  - Lock discipline: threading.Lock guards ONLY dict read (line 145) and dict write (line 246). All awaits (extract, list_fields, update_record_field, create_record) happen OUTSIDE the lock
  - client_token = "ai-bill-" + dedup_key[:40] (deterministic, enables Feishu idempotent create — duplicate client_token returns existing record_id)
  - Summary writeback is best-effort: FeishuClientError on update_record_field → append warning, continue to bill create (bill is primary, summary is secondary)
  - encode_fields ValueError (single_select double-miss = config error) → ai_status="failed", create_record NOT called
  - Logging: dedup hit, AI elapsed_ms, bill_record_id created, warning count. NEVER logs original_text (grep-verified: original_text appears only in params, hash, extract call — never in logger.* calls)
  - PipelineResult: frozen dataclass (ai_status, bill_record_id, warnings, extracted, dedup_hit). extracted dict = {amount, category, flow_type, description} for iPhone Shortcut notification
  - 17 tests (8 ai_profile_parse + 9 pipeline). test_pipeline uses unittest.mock.AsyncMock/MagicMock — no httpx, no real network. TTL expiry test monkeypatches pipeline_mod.time.time. encode_fields ValueError test monkeypatches pipeline_mod.encode_fields (module-level import, not from-import)
  - Mock call_args: create_record called positionally (not kwargs), so token is in call_args.args[3], NOT call_args.kwargs["client_token"]
  - All 67 tests pass (17 new + 50 existing)

- 2026-08-28: main.py AI wiring — Wave 1 final task (todo 7) — commit 805be46
  - app/main.py: 4 new Optional fields on WebhookSuccessResponse (ai_status, ai_record_id, ai_warnings, ai_extracted), all default None
  - Route decorator: `response_model_exclude_none=True` on `@app.post("/v1/webhook/ocr")` — drops ai_* keys when None, so disabled-mode response JSON is byte-identical to master (exactly 5 keys: success/request_id/record_id/book_alias/message). Regression-locked by test_regression_disabled_response_has_5_keys.
  - lifespan: AI components constructed ONLY when settings.ai_enabled. parse_profile(settings.ai_profile_file) raises → startup fails (aligns with target_registry.load_initial fail-fast). AiExtractor(settings) is prompt-stateless (no profile arg). AiPipeline(settings, extractor, feishu_client). When disabled: app.state.ai_extractor/ai_profile/ai_pipeline all set to None (not omitted — explicit None lets getattr fall back cleanly).
  - ingest_ocr AI gate: runs AFTER update_original_text succeeds, BEFORE WebhookSuccessResponse construction. `if settings.ai_enabled and ai_pipeline is not None:` — double guard (settings flag + state presence) so a misconfigured state still can't crash. PipelineResult fields mapped to ai_* response fields; `warnings if warnings else None` and `extracted if extracted else None` coerce empty list/dict to None so exclude_none drops them.
  - CRITICAL invariant: AiPipeline.run() never raises (top-level try/except in pipeline.py), so the AI block cannot introduce a new 5xx path. AI failure → ai_status="failed" + HTTP 200 (original text was already written). Verified by test_enabled_ai_failure_returns_200.
  - New log line: `ai stage done request_id=%s ai_status=%s ai_record_id=%s warnings=%s` — logs only ids/status/counts, NEVER original_text or ai_api_key (grep-verified).
  - WebhookRequest untouched: extra="forbid" stays (credential-leak guard). No new request fields. No existing error branches (401/422/502/503/500) changed. No HTTP status code changes for AI reasons.
  - 5 tests in test/unit/test_webhook_ai.py (ASGI integration via httpx.AsyncClient + ASGITransport):
    1. test_regression_disabled_response_has_5_keys — monkeypatch.delenv AI_ENABLED + cache_clear → response has EXACTLY 5 keys, no ai_* (regression lock vs master)
    2. test_enabled_succeeded_response_has_ai_fields — mock ai_pipeline returns succeeded PipelineResult → 200, ai_status/ai_record_id/ai_extracted populated
    3. test_enabled_ai_failure_returns_200 — mock returns failed → 200 (NOT 5xx), ai_status="failed", record_id present
    4. test_unauthorized_no_ai_triggered — no X-Webhook-Token → 401, pipeline.run NOT called (await_count==0)
    5. test_validation_error_no_ai_triggered — empty original_text → 422, pipeline.run NOT called
  - Test infrastructure discoveries:
    - asgi-lifespan==0.1.0 (pinned in requirements-dev.txt) does NOT export LifespanManager — it's the old startup/shutdown event model, incompatible with FastAPI's modern lifespan contextmanager. NOTEPAD CLAIM WAS WRONG. Fix: run the lifespan async context manager directly: `async with lifespan(app):` (imported from app.main). No extra dep needed.
    - ASGITransport requires an absolute base_url, not a bare path. Fix: `httpx.AsyncClient(transport=ASGITransport(app), base_url="http://testserver")` then `client.post("/v1/webhook/ocr")`.
    - Settings is frozen (frozen=True, slots=True) — cannot mutate `app.state.settings.ai_enabled = True` per-test. Fix: `app.state.settings = dataclasses.replace(get_settings(), ai_enabled=True)`. AI components themselves stay mocked on app.state, so the None ai_provider/ai_api_key in the replaced Settings never get exercised by the route.
    - For AI-enabled tests: mock app.state.feishu_client (AsyncMock, update_original_text returns record_id) AND app.state.ai_pipeline (AsyncMock, run returns PipelineResult) AFTER lifespan startup. This avoids real Feishu network calls and real AI config.
    - response_model_exclude_none drops keys whose value is None — so a PipelineResult with bill_record_id=None + extracted={} produces a response where ai_record_id and ai_extracted KEYS ARE ABSENT (not present-with-None). Test must use `body.get("ai_record_id") is None`, not `body["ai_record_id"] is None`.
  - All 72 tests pass (5 new + 67 existing). Wave 1 complete; Wave 2 (todo 8: AiProfileRegistry) can start.

- 2026-08-28: AiProfileRegistry hot-reload + whitelist snapshot (todo 8) — commit ebe0b34
  - app/ai_profile.py: AiProfileRegistry wraps parse_profile with mtime-based hot reload + pre-loaded single_select option whitelist snapshot. Mirrors TargetRegistry's snapshot/generation/mtime pattern but ASYNC (snapshot assembly needs `await feishu.list_fields`, so all IO methods are `async def`).
  - AiProfileSnapshot: frozen dataclass (profile, option_whitelists: dict[str,set[str]], loaded_at, source_mtime, generation). Whitelist keyed by feishu_field name, built from list_fields response property.options[].name.
  - Lock discipline CRITICAL: `threading.Lock` guards ONLY the snapshot reference swap (lines for _snapshot/_config_valid/_last_reload_error assignment). Network IO (`await feishu.list_fields`) happens OUTSIDE the lock — same pattern as TargetRegistry and pipeline._OptionWhitelistCache.
  - Fail-closed pollution prevention: _load_snapshot catches (ProfileConfigError, FeishuClientError), sets _config_valid=False + _last_reload_error, re-raises. get_snapshot() raises AiProfileRegistryUnavailableError when fail-closed. get_status() is the NON-raising diagnostic analog (used by admin/health endpoints in fail-closed state — mirrors TargetRegistry.describe()).
  - _validate_whitelists: for every single_select spec, fail if (a) feishu_field not in list_fields response, (b) options empty, (c) fallback not in options. This is the pollution-prevention guarantee — a single_select value not in the whitelist would trigger the spec's fallback, but if the fallback ITSELF isn't a real option, Feishu would auto-create a dirty option (irreversible). So we refuse to serve.
  - CRITICAL contract asymmetry: `load_initial()` PROPAGATES errors (fail-fast at startup, mirrors TargetRegistry.load_initial). `maybe_reload()` SWALLOWS (ProfileConfigError, FeishuClientError) — a hot-reload glitch must NOT crash a running server; it leaves the registry fail-closed and the route's get_snapshot() raises the typed 503. This is the ONE place maybe_reload differs from load_initial.
  - maybe_reload reload trigger: mtime changed OR config_valid=False (the latter lets a fixed-on-disk config error self-heal on the next request).
  - app/main.py lifespan: replaces static `parse_profile` with `AiProfileRegistry(settings, feishu_client)` + `await load_initial()`. Deleted `app.state.ai_profile` (pipeline now gets profile from registry snapshot per-request). `app.state.ai_registry` added.
  - app/main.py ingest_ocr AI block: NEW 503 branch. Before pipeline.run: `await ai_registry.maybe_reload()` + `snapshot = ai_registry.get_snapshot()`. The route's except catches `AiProfileRegistryUnavailableError` → 503 AI_PROFILE_UNAVAILABLE. This is the ONLY 5xx the AI block can now introduce (pipeline.run still never raises, so AI extraction failures stay 200). Pipeline.run now ALWAYS receives option_whitelists from the snapshot (never None) — the old _OptionWhitelistCache in pipeline.py is now dead code (only triggered when option_whitelists=None, which no longer happens); left in place to avoid touching pipeline tests.
  - app/main.py reload_config: extended to force-reload the AI registry (`await ai_registry.reload(force=True)`) and adds an `ai_profile` sub-dict to the response (generation/config_valid/last_reload_error via get_status()). Backward compatible — just adds keys. The except swallows (AiProfileRegistryUnavailableError, ProfileConfigError, FeishuClientError) because _load_snapshot already set config_valid=False before re-raising, so get_status() returns fail-closed diagnostics without raising. AI_ENABLED=false → ai_registry is None → emits `ai_profile: null` (key still present).
  - runtime/ai-profile.toml.example: 8-field template. CRITICAL TOML gotcha (already documented in todo 6 learnings): `prompt_header` MUST appear BEFORE any [section] header — a bare key after [bill] becomes bill.prompt_header, not top-level. 3 single_select fields (flow_type/category/payment_method) each with a fallback that matches a real Feishu option.
  - test/unit/test_ai_profile_registry.py: 9 behavior tests, all use AsyncMock(spec=FeishuClient) + tmp_path TOML. Tests 1-7 are pure registry tests (no ASGI). Tests 8-9 are ASGI integration via httpx.AsyncClient + ASGITransport + lifespan.
  - Test infra discovery: lifespan OVERWRITES `app.state.feishu_client` with a real FeishuClient (main.py:67), so setting `app.state.feishu_client = mock` BEFORE `async with lifespan(app):` gets clobbered. Fix: monkeypatch `app.main.AiProfileRegistry` with a factory that ignores the real feishu arg and injects the mock (`monkeypatch.setattr(main_mod, "AiProfileRegistry", lambda s, _: real_cls(s, mock_feishu))`). This lets lifespan's `load_initial` run against the mock.
  - Test 9 (503 fail-closed) discovery: `maybe_reload` initially propagated `ProfileConfigError` (re-raised by `_load_snapshot` via `reload(force=True)`), but the route's except only caught `AiProfileRegistryUnavailableError` → the ProfileConfigError escaped uncaught. Fix: `maybe_reload` now catches (ProfileConfigError, FeishuClientError) and swallows (leaves fail-closed). The route then calls `get_snapshot()` which raises the typed `AiProfileRegistryUnavailableError` → caught → 503. Test 9 builds a fail-closed registry by calling `load_initial()` (which raises, caught by pytest.raises) then swaps it onto app.state.
  - Test 9 response shape: FastAPI's `HTTPException(detail={...})` nests the dict under `body["detail"]`, NOT at the body root. So `body["error"]["code"]` fails with KeyError; correct assertion is `body["detail"]["error"]["code"]`.
  - test/unit/test_webhook_ai.py regression: tests 2 (succeeded) and 3 (failed) broke because the route now requires `ai_registry is not None` before running pipeline.run (the AI block gate changed from `ai_pipeline is not None` to `ai_pipeline is not None and ai_registry is not None`). Fix: added `_mock_registry()` helper (AsyncMock with maybe_reload async no-op + get_snapshot returning a stub snapshot with .profile/.option_whitelists) and set `app.state.ai_registry = _mock_registry()` in tests 2 and 3. Tests 4 (401) and 5 (422) unchanged — auth/validation fail before the AI block, so the missing ai_registry doesn't matter.
  - All 81 tests pass (9 new + 72 existing). Wave 2 todo 8 complete.

- 2026-08-28: Bilingual docs + migration checklist + AGENTS.md rewrite (todo 12 — final implementation todo) — commit 8c75d56
  - docs/ai-pipeline.md (zh) + docs/en/ai-pipeline.md (en): 11-section bilingual docs. Sections: background, architecture flow (ASCII), env vars table (9 AI_* vars), TOML schema (annotated), webhook response fields (ai_* + Shortcut notification example), admin endpoints (3), config page usage, migration checklist (CRITICAL — disable Feishu AI automations + monthly review + known 记账日期 formula limitation), bitable URL key extraction, whitelist refresh, AI_BASE_URL concatenation rule
  - .env.example drift fix: HTTP_TIMEOUT_SECONDS 30→10, FEISHU_TARGET_RELOAD_INTERVAL_SECONDS 30→10 (aligned with app/config.py:173,176 actual defaults). Added AI_* section with AI_BASE_URL concatenation rule inline comment
  - runtime/feishu-webhook.env.example: added AI_* section; AI_PROFILE_FILE=/runtime/ai-profile.toml (docker volume already :ro mounts, no compose change)
  - README.md + docs/en/README.md: added "AI 提取管线 / AI extraction pipeline" section + docs nav link (both mirrors in sync)
  - AGENTS.md (root): STRUCTURE 4→8 modules + app/static/; WHERE TO LOOK added 4 AI modules + AI docs row; CODE MAP added 13 new symbols (AiExtractor/ExtractionResult/AiExtractorError/FieldSpec/encode_fields/AiProfile/parse_profile/AiProfileRegistry/AiProfileSnapshot/AiProfileRegistryUnavailableError/ProfileConfigError/AiPipeline/PipelineResult); ANTI-PATTERNS rewrote "NEVER add bill parsing" to bounded contract ("allowed ONLY in ai_extractor/field_codec/ai_profile/pipeline, AI_ENABLED-gated, webhook contract unchanged"); COMMANDS added pytest + requirements-dev.txt; NOTES removed "requirements-dev.txt doesn't exist" + "doc/code drift" + "No tests" entries (all fixed); CONVENTIONS replaced "Zero tests" with actual test convention line
  - app/AGENTS.md: WHERE TO LOOK 4→8 modules + static/; Request Flow added AI stage branch (maybe_reload → get_snapshot → pipeline.run → ai_* fields); Error Taxonomy added AiExtractorError (200, internalized) + AiProfileRegistryUnavailableError (503 AI_PROFILE_UNAVAILABLE) rows; CONVENTIONS added AI registry lock discipline + contract asymmetry (load_initial propagates / maybe_reload swallows) + pipeline never-raises + dedup + single_select pollution prevention + response_model_exclude_none regression lock
  - .omo/ is NOT gitignored — must explicitly stage only deliverable files, NOT `git add .` (would commit internal notepad)
  - All 100 existing tests pass; commit 8c75d56 on feature/ai-extraction-pipeline
  - Acceptance commands all green: ls (3 files exist), grep -c AI_ENABLED (2/2/11/10 all ≥1), HTTP_TIMEOUT_SECONDS=10, 迁移 count=1 (≥1), ai_extractor in AGENTS.md=6 (≥1), pytest 100 passed

- 2026-08-29: Three related fixes for Windows relay compatibility (3 commits, +106 tests)
  - **Commit 1: Remove forced tool_choice and accept HH:mm dates for relay compatibility**
    - `app/ai_extractor.py`: removed `tool_choice` from anthropic request body (Aliyun Qwen thinking mode rejects forced tool_choice with 400). Comment says "relay compat: forced tool_choice rejected by some backends". Date regex `_BILL_DATE_RE` relaxed to `^\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?$` to accept HH:mm suffix.
    - `app/field_codec.py`: date parsing tries `%Y-%m-%d %H:%M` first, falls back to `%Y-%m-%d`, then today. When HH:mm is present, the parsed datetime gets `tzinfo=_SHANGHAI` (not midnight — preserves the time).
    - `test/unit/test_ai_extractor.py`: `test_anthropic_happy_path` changed from `assert body["tool_choice"] == ...` to `assert "tool_choice" not in body`. Added `test_bill_date_with_hhmm_accepts` verifying `"2026-08-28 09:57"` passes validation.
    - `test/unit/test_field_codec.py`: added `test_date_with_hhmm_parse` verifying `"2026-08-28 09:57"` encodes to Shanghai-tz ms timestamp (not midnight).
    - `runtime/ai-profile.toml.example`: bill_date prompt updated to "格式 YYYY-MM-DD HH:mm（带时间）".
    - `docs/ai-pipeline.md` + `docs/en/ai-pipeline.md`: date type descriptions updated to mention both YYYY-MM-DD and YYYY-MM-DD HH:mm.
  - **Commit 2: Add tzdata for Windows timezone support**
    - `requirements.txt`: added `tzdata==2026.3`. Windows has no system tzdata, so `zoneinfo` can't find `Asia/Shanghai` without it. Harmless on Linux (approved deviation from the 3-dep rule).
  - **Commit 3: Inject single-select options into AI prompts**
    - `app/ai_profile.py`: added `build_field_prompts(profile, option_whitelists)` shared helper. For each single_select spec, appends `。必须从以下选项中选择一个值返回：【选项1/选项2】` to the prompt. Non-single_select and passthrough fields unchanged. Options sorted by Unicode codepoint for deterministic output.
    - `app/pipeline.py`: `_run_impl` reordered to resolve whitelists BEFORE building field_prompts (option injection needs the whitelist). The old inline dict comprehension replaced with `build_field_prompts`. The `_OptionWhitelistCache` fallback (`None` path) is preserved for test backward compat.
    - `app/main.py`: `ai_test_dry_run` endpoint replaced inline dict comprehension with `build_field_prompts(snapshot.profile, snapshot.option_whitelists)`.
    - `test/unit/test_ai_profile_parse.py`: added 4 tests for `build_field_prompts` (option injection, empty whitelist, partial whitelist, deterministic order). `FieldSpec` import added.
  - Key discovery: Chinese characters sort by Unicode codepoint, not pinyin, when using `sorted()` — the test for deterministic order had to use the correct codepoint order.
  - All 106 tests pass (0 failed).

- 2026-08-30: FeishuClient picker methods (branch feature/frontend-config-ui, todo 1)
  - Added `list_tables(app_token)` + `list_records(app_token, table_id, page_token=None)` to `app/feishu_client.py`; no changes to existing methods.
  - Feishu search records API doc verification (POST /open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search):
    - `page_token` and `page_size` are QUERY parameters, NOT body fields.
    - Request body fields (view_id / field_names / sort / filter / automatic_fields) are ALL optional → minimum legal body is `{}`.
    - Response: `data.items[].{record_id, fields}`, `data.has_more`, `data.page_token` (only present when has_more=true), `data.total`.
    - GET /records/list is deprecated — search endpoint is the official replacement.
  - `list_tables` mirrors `list_fields` pagination pattern (while has_more, carry page_token); returns flat `[{table_id, name}]`.
  - `list_records` is single-page by design (caller passes page_token to drive next page); page_size fixed at 50; returns `{items, has_more, page_token|None}`.
  - Test pattern: copy `_patch_transport` + `_token_response` + `_is_token_request` helpers from `test_feishu_client_ext.py` (MockTransport injection via `httpx.AsyncClient.__init__` monkeypatch). Gherkin Given/When/Then as inline `#` comments matching `test_ai_extractor.py` style.
  - All 114 tests pass (106 baseline + 8 new in test_feishu_picker.py).
