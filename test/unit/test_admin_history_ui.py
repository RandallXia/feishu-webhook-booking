# test_admin_history_ui.py — Static-scan tests for the Request History card
# (todo 4: app/static/admin.html + admin.js + admin.css).
#
# Strategy (mirrors test_admin_page.py / test_admin_bill_flow.py):
#   - Pure static file scans — no app import, no HTTP round-trip. The page is
#     a static shell; its data endpoint contract is covered by
#     test_history_api.py / test_history_integration.py.
#   - HTML: the five element ids the JS section depends on must exist.
#   - JS: `function loadHistory(` / `function renderHistory(` declarations
#     (grep gate), init wiring, and the XSS rule — zero innerHTML inside the
#     history section's executable code.
#   - CSS: the four structural selectors the renderer emits.
#   - node --check: JS syntax gate (hard requirement; explicit failure when
#     node is missing).

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_STATIC_DIR = Path(__file__).resolve().parents[2] / "app" / "static"


def _read_static(name: str) -> str:
    return (_STATIC_DIR / name).read_text(encoding="utf-8")


def _history_code_slice(js: str) -> str:
    """admin.js text for the history section, header comment excluded.

    The slice runs from the end of the section's banner comment to the extract
    section (or EOF). The banner comment itself contains the word "innerHTML"
    (it documents the rule), so the XSS gate must start after the comment and
    scan executable code only.
    """
    start = js.index("Request history (todo 4")
    comment_end = js.index("// ===========================================================================", start)
    extract_start = js.find("// Extract-table config")
    if extract_start == -1 or extract_start < comment_end:
        return js[comment_end:]
    return js[comment_end:extract_start]


def test_admin_html_history_section_ids_present():
    """
    GIVEN app/static/admin.html on disk
    WHEN the source is scanned for the Request History card's element ids
    THEN it contains the section, banner, refresh button, limit input, and
      list container ids the JS section wires up
    """
    html = _read_static("admin.html")
    for element_id in [
        "history-section",
        "history-banner",
        "history-refresh-btn",
        "history-limit-input",
        "history-list",
    ]:
        assert f'id="{element_id}"' in html, f"admin.html missing id: {element_id}"


def test_admin_js_exposes_history_functions():
    """
    GIVEN app/static/admin.js exists
    WHEN the source is scanned for the function declarations the history card
       requires
    THEN it contains function declarations for:
      - loadHistory (fetch + clamp + 404-disabled handling)
      - renderHistory (clears the container and renders the timeline)
    """
    js = _read_static("admin.js")
    for name in ["loadHistory", "renderHistory"]:
        pattern = r"function\s+" + re.escape(name) + r"\s*\("
        assert re.search(pattern, js), f"admin.js missing function declaration: {name}"


def test_admin_css_history_rules_present():
    """
    GIVEN app/static/admin.css exists
    WHEN the source is scanned for the Request History selectors
    THEN it defines .history-meta, .history-details, .history-error, and
      .history-text (the structural rules renderHistory emits)
    """
    css = _read_static("admin.css")
    for selector in [".history-meta", ".history-details", ".history-error", ".history-text"]:
        assert selector in css, f"admin.css missing rule: {selector}"


def test_history_section_zero_innerhtml():
    """
    GIVEN app/static/admin.js with the Request History section
    WHEN the history section's executable code is sliced out (header comment
       excluded — it merely documents the rule)
    THEN no innerHTML usage appears in the slice (XSS gate — OCR text,
       summaries, errors, and extracted values are all rendered via
       el()/textContent)
    """
    js = _read_static("admin.js")
    history_slice = _history_code_slice(js)
    assert "historyList" in history_slice, "history slice did not capture the section code"
    assert re.search(r"innerHTML", history_slice) is None, (
        "history section must not use innerHTML (XSS rule) — render via el()/textContent"
    )


def test_admin_js_node_check_passes():
    """
    GIVEN app/static/admin.js exists on disk
    WHEN `node --check app/static/admin.js` is executed
    THEN the exit code is 0 (JS syntax gate — no parse errors)

    Node is required on the dev host; if absent this test fails explicitly
    rather than silently passing on a visual inspection.
    """
    js_path = _STATIC_DIR / "admin.js"
    assert js_path.is_file(), f"admin.js missing at {js_path}"
    result = subprocess.run(
        ["node", "--check", str(js_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"node --check failed (exit {result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_admin_html_card_order_token_first_history_second():
    """
    GIVEN app/static/admin.html (token restored as first visible card;
       history second with the sticky top slot)
    WHEN the DOM order of the container's cards is scanned
    THEN id="token-card" is the first visible card (after the hidden
       id="setup-card"), id="history-section" follows it, and the status bar
       comes after the history card
    """
    html = _read_static("admin.html")
    assert html.index('id="setup-card"') < html.index('id="token-card"'), (
        "setup-card (hidden) must precede token-card"
    )
    assert html.index('id="token-card"') < html.index('id="history-section"'), (
        "token-card must be the first visible card, before history-section"
    )
    assert html.index('id="history-section"') < html.index('id="status-bar"'), (
        "history-section must sit above the status bar"
    )


def test_admin_css_sticky_moved_to_history_section():
    """
    GIVEN app/static/admin.css
    WHEN the sticky-top rule is scanned
    THEN #history-section carries the sticky rule that previously belonged to
       #token-card, and #token-card no longer has any sticky styling
    """
    css = _read_static("admin.css")
    assert re.search(
        r"#history-section\s*\{[^}]*position:\s*sticky", css
    ), "#history-section must carry the sticky-top rule"
    assert re.search(
        r"#token-card\s*\{[^}]*position:\s*sticky", css
    ) is None, "#token-card must no longer be sticky"


def test_ocr_flow_always_resyncs_history_after_lifecycle():
    """
    GIVEN app/static/admin.js with the OCR flow tester
    WHEN the whole ocrFlowBtn click-handler is scanned (from the
       addEventListener to the handler's final closing brace)
    THEN the succeeded branch no longer carries a dedicated loadHistory()
      AND the lifecycle-final .then block disables the button and then
         resyncs the history list (covers succeeded/failed/duplicate — each
         writes exactly one history row)
      AND the network-failure .catch branch also resyncs the history list
         (non-200 writes no row, so the resync restores the real list)
    """
    js = _read_static("admin.js")
    handler_idx = js.index("ocrFlowBtn.addEventListener")
    close_idx = js.index("\n  });", handler_idx)
    handler = js[handler_idx:close_idx]

    success_idx = handler.index("renderOcrFlowResult(result.status, result.body)")
    catch_idx = handler.index(".catch(function(err) {", success_idx)
    success_branch = handler[success_idx:catch_idx]
    assert re.search(r"\bloadHistory\(\)", success_branch) is None, (
        "succeeded branch must NOT call loadHistory() — the lifecycle-final "
        ".then resync replaces the old dedicated success-only refresh"
    )

    assert re.search(
        r"ocrFlowBtn\.disabled\s*=\s*false;\s*\n\s*loadHistory\(\);", handler
    ), "lifecycle-final .then must disable the button then resync loadHistory()"

    catch_block = handler[catch_idx:]
    assert re.search(r"\bloadHistory\(\)", catch_block), (
        ".catch branch must call loadHistory() to restore the real list"
    )


def test_show_history_pending_defined_and_used():
    """
    GIVEN app/static/admin.js with the in-flight pending placeholder
    WHEN the file is scanned for showHistoryPending
    THEN the function is defined
      AND loadHistory() calls it before its limit parsing
      AND the OCR submit path calls it right after the "Sending..." placeholder
    """
    js = _read_static("admin.js")

    assert re.search(r"function\s+showHistoryPending\s*\(", js), (
        "showHistoryPending() must be defined"
    )

    load_idx = js.index("function loadHistory()")
    render_idx = js.index("function renderHistory(")
    load_body = js[load_idx:render_idx]
    assert re.search(r"\bshowHistoryPending\(\);", load_body), (
        "loadHistory() must show the pending placeholder at its start"
    )

    submit_idx = js.index("t('ocr.sending')")
    submit_slice = js[submit_idx - 300:submit_idx + 300]
    assert re.search(r"\bshowHistoryPending\(\);", submit_slice), (
        "OCR submit path must enter the pending state when the request starts"
    )


def test_history_load_wired_into_init_and_refresh_button():
    """
    GIVEN app/static/admin.js exists
    WHEN the init tail (the loadToken() block at the end of the IIFE) and the
       history section are scanned
    THEN loadHistory() is called on page load
      AND the refresh button listener is bound to loadHistory
    """
    js = _read_static("admin.js")
    init_block = js[js.rindex("loadToken();"):]
    assert re.search(r"\bloadHistory\(\)", init_block), (
        "init tail must call loadHistory() so the history card loads on page load"
    )
    assert re.search(
        r"historyRefreshBtn\.addEventListener\('click', loadHistory\)", js
    ), "history refresh button must be wired to loadHistory"


def test_history_collapsed_bar_and_sentinel_present():
    """
    GIVEN app/static/admin.html with the Request History card
    WHEN the source is scanned for the pinned-mode skeleton
    THEN it contains the 1px sentinel span above the card, the
      collapsed summary bar, and the toggle button inside the card
    """
    html = _read_static("admin.html")
    sentinel_idx = html.index('id="history-pinned-sentinel"')
    card_idx = html.index('id="history-section"')
    bar_idx = html.index('id="history-collapsed-bar"')
    btn_idx = html.index('id="history-toggle-btn"')
    assert sentinel_idx < card_idx, (
        "the pinned sentinel span must sit immediately BEFORE the history card"
    )
    assert card_idx < bar_idx < btn_idx, (
        "collapsed bar and toggle button must live inside the history card"
    )


def test_history_pinned_mode_engine_exists():
    """
    GIVEN app/static/admin.js with the Request History section
    WHEN the source is scanned for the pinned-mode engine
    THEN it uses an IntersectionObserver (no timers) on the sentinel
      AND defines renderHistoryCountsBar / applyHistoryCollapsed
      AND the display state is driven by an explicit historyCollapsed flag
        plus a single toggle button that flips it on click
      AND pin-boundary transitions re-sync historyCollapsed to historyPinned
        via a seenPinned guard so manual toggles between transitions survive
    """
    js = _read_static("admin.js")
    assert "history-pinned-sentinel" in js, "JS must reference the sentinel"
    assert "IntersectionObserver" in js, "pinned detection must use IntersectionObserver"
    assert "var historyPinned" in js and "var historyCollapsed" in js
    assert re.search(r"function\s+renderHistoryCountsBar\s*\(", js)
    assert re.search(r"function\s+applyHistoryCollapsed\s*\(", js)
    assert re.search(r"historyCollapsed\s*=\s*historyPinned", js), (
        "pin transition must re-sync the display state"
    )
    assert "historyCollapsed = !historyCollapsed" in js, (
        "toggle button must flip the collapsed state on click"
    )


def test_history_pending_chip_rendered():
    """
    GIVEN app/static/admin.js with buildHistoryEntry
    WHEN the function body (sliced from its declaration to the next history
       helper) is scanned
    THEN a pending item (ai_status === 'pending' OR item.pending === true)
       renders the status-info pending chip and the pending placeholder in
       the details block, instead of the regular payload blocks
    """
    js = _read_static("admin.js")
    start = js.index("function buildHistoryEntry(")
    end = js.index("function appendHistoryBlock(", start)
    body = js[start:end]
    assert "item.pending === true" in body, (
        "buildHistoryEntry must treat item.pending === true as pending"
    )
    assert "t('history.chipPending')" in body, (
        "pending rows must render the pending chip"
    )
    assert re.search(r"t\('history\.pendingText'\)", body), (
        "pending rows must show the processing placeholder in the details block"
    )
    chip_idx = body.index("item.ai_status === 'pending' || item.pending === true")
    succeeded_idx = body.index("item.ai_status === 'succeeded'")
    assert chip_idx < succeeded_idx, (
        "the pending branch must be evaluated before the succeeded branch"
    )


def test_history_i18n_new_keys_paired():
    """
    GIVEN app/static/admin.js with the I18N dictionary
    WHEN the zh and en history.* blocks are scanned for the pinned-mode keys
    THEN each key (chipSuccessShort / chipFailedShort / chipPendingShort /
       expandBtn / collapseBtn) appears in BOTH blocks — mirrors the zh/en
       parity gate with a targeted assertion so a one-sided edit is caught
       with a clearer message than the full dictionary diff
    """
    js = _read_static("admin.js")
    zh_start = js.index("zh: {")
    zh_end = js.index("en: {", zh_start)
    zh_block = js[zh_start:zh_end]
    en_block = js[zh_end:js.index("};", zh_end)]
    for key in [
        "history.chipSuccessShort",
        "history.chipFailedShort",
        "history.chipPendingShort",
        "history.expandBtn",
        "history.collapseBtn",
    ]:
        assert f"'{key}':" in zh_block, f"zh I18N missing {key}"
        assert f"'{key}':" in en_block, f"en I18N missing {key}"


def test_history_toggle_button_single_position():
    """
    GIVEN app/static/admin.html + admin.js with the refactored history
      collapse/expand interaction
    WHEN the sources are scanned for old and new button elements and the
      toggle wiring
    THEN admin.html has exactly ONE id="history-toggle-btn" and ZERO
       id="history-expand-btn" or class="history-collapse-link"
      AND admin.js defines updateHistoryToggleLabel and calls it from both
        the language-toggle handler and the toggle button click handler
      AND admin.js does NOT contain appendHistoryCollapseLink
    """
    html = _read_static("admin.html")
    assert html.count('id="history-toggle-btn"') == 1, (
        "history-toggle-btn must appear exactly once for single-position toggle"
    )
    assert 'id="history-expand-btn"' not in html, (
        "old history-expand-btn element must be removed"
    )
    assert 'class="history-collapse-link"' not in html, (
        "old history-collapse-link class must be removed from HTML"
    )

    js = _read_static("admin.js")
    assert "function updateHistoryToggleLabel(" in js, (
        "JS must define updateHistoryToggleLabel helper"
    )
    # Called in the language toggle handler
    lang_handler_start = js.index("langToggleBtn.addEventListener")
    lang_handler_end = js.index("});", lang_handler_start) + 1
    lang_handler = js[lang_handler_start:lang_handler_end]
    assert "updateHistoryToggleLabel()" in lang_handler, (
        "language toggle handler must call updateHistoryToggleLabel after applyStaticI18n"
    )
    # Called inside applyHistoryCollapsed
    assert "updateHistoryToggleLabel();" in js, (
        "updateHistoryToggleLabel must be called inside applyHistoryCollapsed"
    )
    assert "appendHistoryCollapseLink" not in js, (
        "appendHistoryCollapseLink must be completely removed"
    )


def test_history_stuck_expanded_internal_scroll():
    """
    GIVEN app/static/admin.css + admin.js with the stuck-and-expanded list mode
    WHEN the sources are scanned for the internal-scroll wiring
    THEN css scopes a viewport-height flex card to .history-stuck with the
      list as a scrollable flex child (min-height 0), so a tall list stays
      fully readable while the card is pinned
      AND js toggles the history-stuck class on pin transitions
    """
    css = _read_static("admin.css")
    assert "#history-section.history-stuck" in css, (
        "css must scope the viewport-capped flex card to the stuck state"
    )
    assert "max-height: 100vh" in css, "stuck card must be capped to the viewport"
    assert "#history-section.history-stuck #history-list" in css, (
        "css must scope the internal-scroll rules to the stuck card's list"
    )
    assert "min-height: 0" in css, "flex child list needs min-height 0 to scroll"
    js = _read_static("admin.js")
    assert "classList.add('history-stuck')" in js, (
        "observer must mark the card stuck on pin"
    )
    assert "classList.remove('history-stuck')" in js, (
        "observer must unmark the card on unpin"
    )
