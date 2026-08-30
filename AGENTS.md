# PROJECT KNOWLEDGE BASE

**Generated:** 2026-08-28
**Commit:** c7e7d3c
**Branch:** master

## OVERVIEW

Lightweight FastAPI webhook bridge: iPhone Shortcuts OCR text → Feishu Bitable `原始信息` field. Python 3.12 + fastapi/uvicorn/httpx only (3 pinned deps). Feishu-side automations generate all bill fields downstream — this service does one narrow write.

轻量 FastAPI webhook 桥接器：iPhone 快捷指令 OCR 文本 → 飞书多维表格 `原始信息` 字段。账单字段由飞书侧自动化生成，本服务只做单点写入。

## STRUCTURE

```
feishu-webhook-booking/
├── app/                  # All source code (9 modules, no __init__.py — namespace pkg)
│   ├── main.py           # 3 webhook/admin routes + 3 AI admin routes + 4 picker routes + 6 config routes + AI wiring
│   ├── config.py         # Hand-rolled env loading (NOT pydantic-settings)
│   ├── feishu_client.py  # tenant_access_token cache + bitable record PUT/POST + list_fields/list_tables/list_records
│   ├── target_registry.py # dual-mode target routing + hot reload + validate_targets
│   ├── ai_extractor.py   # Dual-protocol (anthropic/openai) structured AI extraction
│   ├── field_codec.py    # ExtractionResult → Feishu field dicts (whitelist-guarded)
│   ├── ai_profile.py     # TOML profile parser + hot-reloading registry + whitelist snapshot + validate_profile_candidate + build_field_prompts
│   ├── pipeline.py       # AI orchestration: extract → encode → writeback → create (never raises)
│   ├── toml_writer.py    # Deterministic TOML serialization (dump_targets/dump_profile) for config UI saves
│   └── static/           # admin.html + admin.js + admin.css — AI config page served at /admin/ai
├── runtime/              # Runtime config mounted read-only in Docker; real files gitignored, only *.example committed
├── docs/                 # Chinese docs (default)
├── docs/en/              # English mirror — must stay in sync with docs/
├── docs/deployment/      # 5 platform guides (NAS/Linux/Windows/Termux/public access)
├── test/unit/            # pytest suite (248 tests); conftest pins env BEFORE app import
├── .github/workflows/docker-publish.yml  # Multi-arch image build → Docker Hub + GHCR + auto Release on v* tags
├── Dockerfile            # python:3.12-alpine, hardcoded Tsinghua PyPI mirror, port 2398
└── docker-compose.yml    # mounts ./runtime:/runtime:ro, injects FEISHU_ENV_FILE/FEISHU_TARGETS_FILE
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Add/modify an API route | `app/main.py` | 16 routes: `/health`, `/v1/webhook/ocr`, `/admin/config/reload`, `/admin/ai`, `/admin/ai/test`, `/admin/ai/profile`, 4 picker (`/admin/feishu/{tables,fields,records,parse-url}`), 6 config (`GET/PUT /admin/config/{profile,targets,env}`) |
| Change config/env vars | `app/config.py` | Hand-rolled env loading (NOT pydantic-settings); `env_file_path` detected from `FEISHU_ENV_FILE` → `.env` fallback |
| Feishu API calls / token cache | `app/feishu_client.py` | tenant_access_token cached in-process, refresh skew 300s; record PUT/POST + list_fields/list_tables/list_records |
| Target routing / hot reload | `app/target_registry.py` | dual-mode: dynamic TOML vs legacy env; `validate_targets` for PUT-side schema checks |
| AI extraction (anthropic/openai) | `app/ai_extractor.py` | Prompt-stateless; dual-protocol; forced tool_call |
| ExtractionResult → Feishu fields | `app/field_codec.py` | Whitelist-guarded single_select; Asia/Shanghai date |
| AI profile TOML + hot-reload registry | `app/ai_profile.py` | parse_profile + `validate_profile_candidate` + `build_field_prompts` + AiProfileRegistry (async, mtime reload) |
| AI pipeline orchestration | `app/pipeline.py` | extract → encode → writeback → create; never raises |
| Deterministic TOML serialization | `app/toml_writer.py` | `dump_targets`/`dump_profile` — config UI save path; comment-loss is expected |
| Runtime config templates | `runtime/*.example` | copy to real files (gitignored) before running |
| AI pipeline docs | `docs/ai-pipeline.md` (+ `docs/en/ai-pipeline.md`) | config, migration checklist, TOML schema, admin endpoints |
| Release process | `docs/release-checklist.md` + CI workflow | tag `v*` triggers Release |
| Deploy guides | `docs/deployment/` | 5 platforms, all doc-driven (zero deploy scripts) |

## CODE MAP

| Symbol | Type | Location | Refs | Role |
|--------|------|----------|------|------|
| `app` (FastAPI) | instance | app/main.py:62 | uvicorn entry `app.main:app` | ASGI app, 6 routes |
| `lifespan` | func | app/main.py:48 | 1 | init logging + `TargetRegistry` + `FeishuClient` + (AI_ENABLED? `AiProfileRegistry`+`AiExtractor`+`AiPipeline`) → `app.state` |
| `ingest_ocr` | route | app/main.py:92 | webhook entry | token check → resolve target → update record → (AI_ENABLED? pipeline.run → ai_* fields) |
| `reload_config` | route | app/main.py:232 | admin | force registry reload + AI registry reload, gated by `CONFIG_RELOAD_TOKEN` |
| `ai_test_dry_run` | route | app/main.py:430 | admin | `POST /admin/ai/test` dry-run extraction, gated by `CONFIG_RELOAD_TOKEN` |
| `ai_profile_inspect` | route | app/main.py:516 | admin | `GET /admin/ai/profile` profile + whitelist inspection |
| `Settings` | dataclass | app/config.py:58 | all modules | frozen config; built once via `get_settings()` (`lru_cache`) |
| `_load_runtime_env_files` | func | app/config.py:42 | module-import side effect | `FEISHU_ENV_FILE` (required if set) → root `.env` → `os.environ.setdefault` |
| `FeishuClient` | class | app/feishu_client.py:25 | main.py | token cache + PUT bitable record + create_record + list_fields |
| `FeishuClientError` | exc | app/feishu_client.py:12 | main.py | carries `stage` + `record_id` for error envelope |
| `TargetRegistry` | class | app/target_registry.py:57 | main.py | snapshot-based resolve; `threading.Lock`; mtime hot reload |
| `resolve` | method | app/target_registry.py:111 | `ingest_ocr` | alias/year → target; alias+year must agree → else 422 |
| `FeishuTargetConfig` | dataclass | app/target_registry.py:35 | feishu_client | one book target (alias/year/app_token/table_id/record_id) |
| `AiExtractor` | class | app/ai_extractor.py:134 | pipeline, main.py | prompt-stateless dual-protocol extractor; forced tool_call; per-call httpx client |
| `ExtractionResult` | dataclass | app/ai_extractor.py:55 | pipeline, field_codec | frozen; 7 fields (summary/description/flow_type/amount/category/payment_method/bill_date) |
| `AiExtractorError` | exc | app/ai_extractor.py:47 | pipeline, main.py | carries `stage` (request/parse/validate); internalized → `ai_status="failed"` (never 5xx) |
| `FieldSpec` | dataclass | app/field_codec.py:28 | ai_profile, main.py | frozen; one field mapping (ai_key/feishu_field/type/target/fallback/prompt/source/enabled); `enabled=false` → `encode_fields` skips the spec entirely (config-UI per-field toggle, defaults true) |
| `encode_fields` | func | app/field_codec.py:50 | pipeline, main.py | ExtractionResult + specs + whitelists → (extract_fields, bill_fields, warnings); whitelist-guarded single_select |
| `AiProfile` | dataclass | app/ai_profile.py:46 | pipeline, main.py | frozen; prompt_header/summary_field/bill_app_token/bill_table_id/fields tuple |
| `parse_profile` | func | app/ai_profile.py:58 | AiProfileRegistry | stateless TOML → AiProfile; raises `ProfileConfigError` on schema violation |
| `AiProfileRegistry` | class | app/ai_profile.py:224 | main.py | async hot-reload (mtime) + pre-loaded whitelist snapshot; mirrors TargetRegistry pattern |
| `AiProfileSnapshot` | dataclass | app/ai_profile.py:179 | main.py | frozen; profile + option_whitelists + loaded_at + source_mtime + generation |
| `AiProfileRegistryUnavailableError` | exc | app/ai_profile.py:37 | main.py | raised by `get_snapshot()` when fail-closed → route 503 `AI_PROFILE_UNAVAILABLE` |
| `ProfileConfigError` | exc | app/ai_profile.py:33 | main.py, ai_profile | profile schema violation; `load_initial` propagates (fail-fast), `maybe_reload` swallows |
| `AiPipeline` | class | app/pipeline.py:95 | main.py | orchestrates extract → encode → writeback → create; `run()` never raises |
| `PipelineResult` | dataclass | app/pipeline.py:35 | main.py | frozen; ai_status/bill_record_id/warnings/extracted/dedup_hit |
| `dump_targets` | func | app/toml_writer.py:64 | main.py (config UI save) | deterministic full-replace TOML serialization of the targets registry |
| `dump_profile` | func | app/toml_writer.py:109 | main.py (config UI save) | deterministic TOML serialization of the AI profile (prompt_header first) |
| `validate_targets` | func | app/target_registry.py:45 | main.py (config UI save) | PUT-side schema checks for the targets body (alias regex / dup year / default-in-targets) |
| `validate_profile_candidate` | func | app/ai_profile.py:193 | main.py (config UI save) | async: parse + list_fields whitelist pre-validation for a PUT profile body |
| `build_field_prompts` | func | app/ai_profile.py:335 | main.py (dry-run) | AiProfile + whitelists → per-field prompt strings for the extractor |
| `parse_profile_text` | func | app/ai_profile.py:58 | toml_writer round-trip tests | stateless raw-text → AiProfile (no file IO) |
| `list_tables` | method | app/feishu_client.py:153 | main.py (picker) | `GET /open-apis/bitable/v1/apps/{app_token}/tables` → table list |
| `list_records` | method | app/feishu_client.py:198 | main.py (picker) | paginated `GET .../records` → items + has_more + page_token |
| `env_file_path` | attr | app/config.py:85 (Settings) | main.py (config env routes) | `FEISHU_ENV_FILE` → `.env` fallback; None → PUT env 409 `ENV_FILE_NOT_FOUND` |

## CONVENTIONS

- Docs are bilingual and mirrored: `docs/` (zh, default) ↔ `docs/en/` (en). Any doc change must update BOTH. 文档双语镜像，改一篇必须同步另一篇。
- Version-pin everything exactly (`==`) in `requirements.txt`; keep the 3-dep minimalism.
- Config = env vars only, loaded by hand in `app/config.py` (deliberately not pydantic-settings). Follow the existing `_require_env`/`_optional_env`/`_int_env`/`_path_env` pattern.
- DI via `request.app.state.*`, not `Depends()`.
- All runtime secrets live in `runtime/` (mounted `:ro`); images contain zero config. Only `*.example` templates are committed.
- Relative imports (`from .config import ...`); `app/` has no `__init__.py`.
- Tokens compared with `secrets.compare_digest` — keep it that way for any new auth.
- Tests live under `test/unit/` (248 tests, pytest); conftest pins env BEFORE app import and clears the `get_settings` cache per test.

## ANTI-PATTERNS (THIS PROJECT)

- NEVER let webhook request bodies carry Feishu credentials (`app_token`/`table_id`/`record_id`/`FEISHU_APP_SECRET`). Enforced by `WebhookRequest.model_config = ConfigDict(extra="forbid")`.
- 账单字段提取逻辑只允许存在于 `app/ai_extractor.py` / `app/field_codec.py` / `app/ai_profile.py` / `app/pipeline.py`（AI_ENABLED 门控内）；webhook 请求契约不变（单点写入原始信息的语义保留）；不得在其它模块添加解析逻辑。Bill-field extraction logic is allowed ONLY in `app/ai_extractor.py` / `app/field_codec.py` / `app/ai_profile.py` / `app/pipeline.py` (gated by AI_ENABLED); the webhook request contract is unchanged (single-write 原始信息 semantics preserved); do not add parsing logic in other modules.
- Do not commit real `runtime/feishu-webhook.env` / `runtime/feishu-targets.toml` / `runtime/ai-profile.toml` values.
- Do not introduce DB/ORM/queue deps — state is in-memory (token cache + registry snapshot + AI dedup dict) by design.

## UNIQUE STYLES

- Dual mode: `FEISHU_TARGETS_FILE` set → dynamic TOML multi-target; unset → legacy single-target env vars (`FEISHU_APP_TOKEN`/`TABLE_ID`/`RECORD_ID` required instead).
- Hot reload: per-request `maybe_reload()` throttled by `FEISHU_TARGET_RELOAD_INTERVAL_SECONDS` (default 10s), mtime-based, plus manual `POST /admin/config/reload`. Config errors set `config_valid=False` → service fail-closes with 503 until config is fixed. The AI profile registry (`AiProfileRegistry`) mirrors this pattern but async (snapshot assembly calls `await feishu.list_fields`).
- Registry snapshots are immutable (`frozen=True, slots=True`) and generation-numbered.
- AI pipeline never raises: `AiPipeline.run()` catches `Exception` → `PipelineResult(ai_status="failed")` + HTTP 200 (original text was already written). The ONE 5xx the AI stage can introduce is `AiProfileRegistryUnavailableError` → 503 `AI_PROFILE_UNAVAILABLE` (fail-closed registry, prevents single_select option pollution).
- AI dedup is in-memory + success-only: `dict[sha256(alias:original_text), ts]` with TTL `AI_DEDUP_TTL_SECONDS` (default 300s); a failed AI extraction is NOT recorded, so a legal retry can run.

## COMMANDS

```bash
# Local dev
pip install -r requirements.txt          # runtime deps (3 pinned)
pip install -r requirements-dev.txt      # dev deps: pytest + pytest-asyncio + asgi-lifespan + tomli
uvicorn app.main:app --host 0.0.0.0 --port 2398 --reload

# Tests
python -m pytest                         # 248 tests under test/unit/
python -m pytest -v                      # verbose

# Docker
docker compose up -d --build             # expects runtime/feishu-webhook.env + feishu-targets.toml (+ ai-profile.toml when AI_ENABLED=true)

# Smoke test
curl http://localhost:2398/health

# Release
git tag vX.Y.Z && git push --tags        # CI builds multi-arch, pushes Docker Hub + GHCR, creates Release
```

## NOTES

- 🐛 CI bug (verified): `.github/workflows/docker-publish.yml:95` writes release notes with `randallxia/feishu-webhook-service`, but the real image (line 47) is `randallxia/feishu-webhook-booking`. Release pull command points to a nonexistent image.
- Dockerfile pins Tsinghua PyPI mirror — slow outside mainland China.
- Docker health: `restart: unless-stopped`; port 2398.
