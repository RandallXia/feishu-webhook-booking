# Issues — admin-request-history

Problems and gotchas encountered during work on this plan.

_Auto-scaffolded by /start-work. Append new entries below - never overwrite._

---

## todo 3 (2026-10-12)

- **(minor, no action)** FastAPI wraps HTTPException detail in a top-level `"detail"` key — the webhook 401/422 bodies raised inside `ingest_ocr` are NOT affected (route raises inside its own try/except with detail dict, still wrapped the same way); all consumers already read `["detail"]["error"]`. No flattening added (would have been an out-of-scope behavior change).
- **(env note)** conftest.py does not pin `AI_HISTORY_DB_PATH` yet — plan gives that to todo 6. Until then, only tests that explicitly attach a HistoryStore exercise persistence; every other test hits the `history_store is None` gate, which is exactly the pre-todo-3 behavior.

## todo 4 (2026-10-12)

- **(fixed in-task)** Intermediate edit duplicated the historyRefreshBtn click-listener block (anchor collision with the extract-section header comment). Caught by grep, deduped, re-verified: node --check 0, targeted 41 passed, full 322 passed.
- **(env note, no action)** `node -e` runtime smoke of renderHistory isn't feasible (functions are private to the single IIFE; extracting them would test a copy, not the code). Covered instead by node --check + static-scan assertions + logic walkthrough per plan's "MUST DO" note.
- **(cosmetic, left as-is)** .omo/start-work/ and run-continuation session files keep appearing untracked — same as todo 1-3 saw; they are never committed (plan rule) and CI ignores them.

## Task 6 (2026-10-12)

- **(doc drift, resolved by the plan's own 顺延 rule)** Task template mandated "## 13. 请求历史" but section 13 already existed (Field enable toggle, todo from an earlier plan). Used section 14; bilingual `##` counts still equal (15/15). Flagged here so F1 (plan compliance) doesn't flag the number mismatch as a deviation.
- **(cosmetic, no action)** Task's README acceptance said grep「请求历史」— zh README uses「记账历史」(verbatim from the mandated markdown snippet and matching the actual admin card title); "Request History" is present in both READMEs. Not a gap.
- **(env note, no action)** 10 pre-existing pytest warnings (raise _unsupported deprecation) — present before this todo, untouched.
