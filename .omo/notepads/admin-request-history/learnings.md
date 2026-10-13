# Learnings — admin-request-history

Conventions, patterns, and successful approaches discovered during work on this plan.

_Auto-scaffolded by /start-work. Append new entries below - never overwrite._

---

## Task 1 — history_store.py + Settings (2026-10-12)

- **conftest.py is at repo ROOT, not test/unit/**: pins env (FEISHU_TARGETS_FILE/AI_ENABLED=false etc.) BEFORE app import + autouse `get_settings.cache_clear()` fixture. Settings tests still do explicit `get_settings.cache_clear()` after monkeypatch.setenv (mirrors test_config_ai.py style).
- **No Gherkin-confirmation gate needed**: plan pre-authorized skipping the AskUserQuestion step; Gherkin docstrings mirror test_pipeline.py (`GIVEN/AND/WHEN/THEN` with aligned AND/THEN columns).
- **Degraded-mode construction pattern that worked**: one big try/except wrapping mkdir→connect→PRAGMAs→schema check; nested try closes the conn before re-raising so a failed PRAGMA never leaks a fd. `user_version` doubles as both schema stamp and "fresh file" detector (0 = brand new).
- **Throttle pitfall**: module-level `_last_error_log_ts` + monotonic clock; first-failure full text, repeats within 60s log one summary line. Kept behind `_error_log_lock` so concurrent inserts don't all claim "first".
- **pytest -k quirk**: `-k "degraded or readonly"` missed `test_future_user_version_degrades_...` (verb form) — evidence needed a second run with `-k "version or readonly"` to cover both failure-branch tests.
- **python is 3.10 in this env** (homebrew), repo targets 3.12; `X | None` annotations + `from __future__ import annotations` kept everything compatible. pytest 8.3.4 runs fine.
- **HistoryEntry test fixture note**: defaults carry real Chinese/emoji/warnings so round-trip deep-equality doubles as an encoding check (`json.dumps ensure_ascii=False` preserves CJK).
- Signature contract locked for todo 3 (main.py wiring): `HistoryStore(db_path: Path)`, `.insert(entry: HistoryEntry) -> None`, `.list_recent(limit: int) -> list[dict]` (rows keyed by the 12 HistoryEntry field names).

## todo 2 — PipelineResult summary/error fields (2026-10-12)

- `PipelineResult` is `@dataclass(frozen=True, slots=True)` — adding fields with defaults at the END preserves backward compat for all 5-positional-arg callers in `test_webhook_ai.py` and `test_failure_matrix.py`.
- `AiPipeline.run()` has 7 return paths; dedup is the only one kept without summary/error (duplicate hit means no new extraction happened).
- Top-level `except Exception` in `run()` catches anything that penetrates `_run_impl`'s precise catches (RuntimeError etc.) — `error="internal error"` is a fixed string, no `str(exc)` to prevent payload leak.
- `_run_impl` catches only `AiExtractorError`, `ValueError`, `FeishuClientError` — anything else (e.g., RuntimeError) hits the top-level handler.
- `test_pipeline.py` already uses BDD docstrings — new tests follow the same GIVEN/WHEN/THEN pattern.

## todo 3 — main.py wiring (2026-10-12)

- **Error envelope reads go through `["detail"]["error"]`**: FastAPI's default HTTPException handler wraps detail — repo-wide assertion convention is `response.json()["detail"]["error"]["code"]` (grep test_config_profile_api.py). No custom exception_handler exists in main.py; don't flatten in tests.
- **`_require_admin_auth` helper EXISTS at main.py (post-todo-3 line ~719)** and inline-duplicates the reload_config gate — new admin routes should call it, not re-inline.
- **Test wiring pattern that worked**: `dataclasses.replace(get_settings(), ai_enabled=True, ai_history_db_path=..., config_reload_token=..., ai_model=...)` + REAL `HistoryStore(tmp_path/"h.db")` attached manually to `app.state` inside `async with lifespan(app)`. Conftest does NOT pin CONFIG_RELOAD_TOKEN → 401/404-path tests MUST replace `config_reload_token` into settings or `_require_admin_auth` 404s first (RELOAD_DISABLED).
- **Assert on rows via an independent `HistoryStore(same_path).list_recent(10)`** — WAL + committed inserts make rows visible across connections; doubles as cross-connection persistence proof.
- **AsyncMock pitfall**: `_mock_registry()` returns AsyncMock whose `get_snapshot` MagicMock-return can be overridden per-test via `app.state.ai_registry.get_snapshot = MagicMock(side_effect=...)` for the 503 path.
- Duplicate-path entry keeps `error=None`/`summary=None` (pipeline fills neither on dedup) — locked by test.

## todo 4 — admin.{html,css,js} history card (2026-10-12)

- **XSS grep-gate anchor subtlety**: the mandated section banner comment itself contains the literal word "innerHTML" (it documents the rule), so `js.index('Request history (todo 4')` → slice would ALWAYS fail the gate. Slice must start after the comment block — `_history_code_slice` finds the closing `// ===...` line of the banner first, then scans to `// Extract-table config`.
- **Edit-anchor hazard at section boundaries**: inserting via the shared `// ===` extract-section header anchor as oldString can duplicate adjacent listener blocks (got a double `historyRefreshBtn.addEventListener`). Grep the new symbol count after every section-boundary insert (addEventListener lines == 1).
- **Plan CSS/HTML mismatch resolved like bill-step-row**: plan CSS ships `.history-limit-input` (class) but the plan HTML snippet only had the id. Existing precedent (admin.html:124 `id="bill-url-input" class="url-input"`) → added `class="history-limit-input"` to the input so the CSS selector actually matches.
- renderHistory mirrors renderOcrFlowResult's kv/blocks ideas but factors per-block title emission into `appendHistoryBlock`/`appendHistoryBlockTitle` + a `hasDetails` flag so the "无详情 / No details" placeholder only appears when a record has nothing at all (error/summary/original_text/extracted/warnings all empty).
- Status chip mapping kept inline in buildHistoryEntry: succeeded→status-ok「✓ 成功」/ failed→status-err「✗ 失败」/ duplicate→status-warn「⏭ 重复」/ else status-info + raw ai_status — mirrors the ai_status labels in renderOcrFlowResult :457-463.
- 404-on-load UX: AI_DISABLED/HISTORY_DISABLED → placeholder「历史未启用 / History disabled — set AI_HISTORY_DB_PATH to enable」via historyList (NOT historyBanner — banner stays empty; no showBanner needed, matches plan's placeholder wording).

## Task 5 — Deploy & Config Surface (2026-10-12 16:35)

- docker-compose.yml: added \ writable volume (separate from runtime :ro)
- .gitignore: \ directory-level ignore covers history.db + -wal/-shm
- AGENTS.md: 3 updates (ANTI-PATTERNS exception, STRUCTURE tree, CODE MAP row)
- app/AGENTS.md: WHERE TO LOOK table entry for history_store.py
- Verification: git check-ignore exit 0, git status silent, grep counts all green
- requirements.txt NOT touched (zero diff — F4 hard gate)

## Task 5 — Deploy & Config Surface (2026-10-12)

- docker-compose.yml: added `- ./data:/data` writable volume (separate from runtime :ro)
- .gitignore: `data/` directory-level ignore covers history.db + -wal/-shm
- AGENTS.md: 3 updates (ANTI-PATTERNS exception, STRUCTURE tree, CODE MAP row)
- app/AGENTS.md: WHERE TO LOOK table entry for history_store.py
- Verification: git check-ignore exit 0, git status silent, grep counts all green
- requirements.txt NOT touched (zero diff — F4 hard gate)

## Task 6 — Integration tests + bilingual docs (2026-10-12)

- **Doc section number is 14, not 13**: task template said "## 13. 请求历史" but docs already had a section 13 (Field enable toggle) — the "顺延" rule resolved it to `## 14. 请求历史 / Request History` (zh) / `## 14. Request History` (en). Always grep `^## ` first.
- **side_effect list is the only delta from test_history_api.py**: `AsyncMock(side_effect=[r1, r2, r3])` makes one mock pipeline yield succeeded/failed/duplicate across 3 POSTs; everything else (replace-settings + manual app.state wiring + independent-store read) is verbatim reuse of the todo-3 pattern.
- **Privacy test rides the same replaced settings object**: webhook auth AND history-API auth both read `request.app.state.settings`, so probe secrets (`ai_api_key`/`webhook_shared_token`) placed in the replace() call are guaranteed to be the only secrets in play — a serialized-response scan is then airtight. No env/monkeypatch needed.
- **Contract-regression hardening**: PipelineResult constructed with BOTH summary and error set on a succeeded result — proves the route never echoes either key even when both are present.
- Restart-semantics test opens the independent store AFTER `async with lifespan(app)` exits (fresh connection, zero shared state with the app's store).
- Full suite after this todo: 327 passed (322 baseline + 5 new).

## History card promoted to global-top + OCR auto-refresh (2026-10-12)

- **Card move = two edits, never one**: cut the history-section block from :203-215 first, then insert before the setup-card comment. Single-edit attempts risk duplicate-id states; grep `id="history-section"` count == 1 after both edits.
- **Test anchor trap**: the spec's suggested anchor `js.index("ai_status === 'succeeded'")` hits admin.js:459 (renderOcrFlowResult's status-label mapping) FIRST, not the OCR flow handler at :519 — a 400-char window from :459 would miss the branch entirely. Stable alternative: anchor on `renderOcrFlowResult(result.status, result.body)` (exactly 1 occurrence, the call site in the handler), slice to the chain's `.catch(` — the assertion then rides a behavior-anchored slice, immune to comment-length changes inside the branch.
- Sticky migration verified safe: `.container` wraps all cards (no overflow ancestors, .container/body clean), so `#history-section { position: sticky; top: 0 }` at container top works for the whole-page scroll — same behavior #token-card had mid-list.
- Auto-refresh deliberately success-branch-only (with a 4-line comment explaining why): failed wrote no bill record, duplicate added no history entry — neither warrants a reload. loadHistory is visible at the call site via function-declaration hoisting (same single IIFE, :15-:2408).
- Baseline moved 327 → 330 passed (3 new static-scan tests in test_admin_history_ui.py); node --check 0.

## 2026-10-12: Card order fix + in-flight pending row (commit e879225)
- DOM order now: h1/subtitle → setup-card (hidden) → token-card (first visible, no sticky) → history-section (keeps sticky top slot) → status-bar → …; history block moved verbatim, zero id/class changes.
- `showHistoryPending()` added near loadHistory(): clears #history-list and renders one `history-entry-pending` row (⏳ status-info chip). Called at top of `loadHistory()` AND at OCR submit, so any fetch/webhook flight shows the pending row immediately.
- Replaced f097cd7's succeeded-branch-only `loadHistory()` with lifecycle-final resync: `.then(function() { ocrFlowBtn.disabled = false; loadHistory(); })` + same call in `.catch`. Rationale: EVERY valid webhook (succeeded/failed/duplicate, even registry-503) writes exactly one history row, so resync-once-at-end is semantically correct for all paths; non-200 writes no row and the resync just restores the real list (clearing the placeholder).
- CSS: zero changes needed — pending row reuses existing `.history-entry`/`.status-tag`/`.status-info`. The optional `.history-entry-pending .history-meta` dim rule was NOT needed.
- Test count 330 → 331 (rewrote order + refresh assertions into `test_admin_html_card_order_token_first_history_second` / `test_ocr_flow_always_resyncs_history_after_lifecycle`, added `test_show_history_pending_defined_and_used`).
- Gotcha for future edits: the OCR handler's `.catch` runs BEFORE the final `.then` in the promise chain, so on network failure loadHistory() is called twice (harmless — idempotent placeholder-clearing + refetch).

## 2026-10-12: zh/EN language toggle (Plan B, commit 02067d6)
- **Test-anchor trap forces 1 authorized residual**: test_admin_history_ui.py's `test_show_history_pending_defined_and_used` anchors the ±300-char window on the literal `'发送中... / Sending...'` (js.index). That test file was outside this task's commit scope, so the literal stays as a REAL string (deliberate, documented in test_admin_i18n.py). If that test is ever migrated to i18n, swap the anchor to `t('ocr.sending')` AND delete the residual together.
- **`var t` shadowing is the #1 hazard when introducing a global t()**: 6 spots in admin.js had `var t` (authHeaders, loadTables, loadExtractTargets, billLoadTablesFromAppToken, billUpdateTableName, renderBillRow typeOpts loop) — hoisted per-function shadows kill t() with cryptic runtime errors. All renamed (token/tbl/tgt/cachedTbl/cachedInfo/ti). Gate: `grep "var t\b" admin.js` must be 0.
- **Bilingual-residual regex must be single-line**: `r"'[^']* / [^']*'"` pairs quotes ACROSS lines ([^'] matches \n) and counts whole comment blocks as literals (10 false hits). Use `r"'[^'\n]* / [^'\n]*'"` — a JS single-quoted literal cannot span lines.
- **I18N parity test trick**: extract I18N.zh / I18N.en blocks via brace counting from `js.index("zh: {")`, then compare `re.findall(r"'([a-zA-Z][\w.]*)'\s*:", block)` key sets — catches missing en entries that would silently leak zh text under EN mode (t() falls back to zh).
- **Toggle wiring must precede the first-run gate**: the IIFE `return`s early in setup mode (no token), so langToggleBtn.addEventListener lives right after applyStaticI18n() at the top. Handler guards loadHistory()/refreshStatus() behind `if (getToken())` — api() 401 path would otherwise flash "令牌不正确" on the setup card during a language switch.
- **Anti-flash = head inline script + IIFE-first applyStaticI18n()**: head script only toggles html.lang-en class (cheap, pre-paint); full text painting happens in the IIFE's first sync pass BEFORE DOM refs. Script is at end of body (DOM ready) so querySelectorAll finds everything.
- en dict had a duplicate `'ocr.flowAiFailed'` + `'extract.fieldFromExtract'` key (harmless, later wins) — left as-is; JS last-wins semantics identical to a single entry.
