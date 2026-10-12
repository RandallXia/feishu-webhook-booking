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


def test_admin_html_history_section_is_first_card():
    """
    GIVEN app/static/admin.html (history promoted to the page's first card)
    WHEN the DOM order of the container's cards is scanned
    THEN id="history-section" appears BEFORE both id="setup-card" and
       id="token-card" (the history card is the new global-top surface,
       replacing the token card's top position)
    """
    html = _read_static("admin.html")
    hist = html.index('id="history-section"')
    assert hist < html.index('id="setup-card"'), (
        "history-section must precede setup-card"
    )
    assert hist < html.index('id="token-card"'), (
        "history-section must precede token-card"
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


def test_ocr_flow_success_branch_refreshes_history():
    """
    GIVEN app/static/admin.js with the OCR flow tester
    WHEN the success branch of the webhook result is scanned (from the
       renderOcrFlowResult call to the flow's .catch handler)
    THEN it calls loadHistory() exactly once (auto-refresh on success —
       failed/duplicate branches stay untouched)
    """
    js = _read_static("admin.js")
    success_idx = js.index("renderOcrFlowResult(result.status, result.body)")
    catch_idx = js.index(".catch(function(err) {", success_idx)
    success_branch = js[success_idx:catch_idx]
    assert re.search(r"\bloadHistory\(\)", success_branch), (
        "OCR flow success branch must call loadHistory() after showBanner"
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
