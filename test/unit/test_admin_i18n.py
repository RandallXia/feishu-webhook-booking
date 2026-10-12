# test_admin_i18n.py — Static-scan tests for the zh/EN language toggle on the
# admin page (app/static/admin.html + admin.js + admin.css).
#
# Strategy (mirrors test_admin_page.py / test_admin_history_ui.py):
#   - Pure static file scans — no app import, no HTTP round-trip.
#   - HTML: toggle button id, data-i18n / data-i18n-placeholder coverage, and
#     the anti-flash inline script in <head>.
#   - JS: the i18n engine pieces (LANG_KEY / I18N / t / applyStaticI18n) and a
#     HARD CAP on residual「'x / y'」bilingual literals so the concatenated
#     style cannot creep back in.
#   - Dictionary parity: zh and en blocks must define the SAME key set — a
#     missing en entry would silently render Chinese text under EN mode.
#   - node --check: JS syntax gate (hard requirement; explicit failure when
#     node is missing).

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_STATIC_DIR = Path(__file__).resolve().parents[2] / "app" / "static"


def _read_static(name: str) -> str:
    return (_STATIC_DIR / name).read_text(encoding="utf-8")


def _extract_i18n_block(js: str, lang: str) -> str:
    """Raw source text of I18N.<lang>: { ... }, matched by brace counting.

    Starts at ``<lang>: {`` inside the I18N literal and walks forward counting
    braces (string-aware enough: the dictionary values contain no braces).
    """
    start = js.index(lang + ": {")
    depth = 0
    for i in range(start, len(js)):
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
            if depth == 0:
                return js[start : i + 1]
    raise AssertionError(f"I18N.{lang} block not terminated")


def _i18n_keys(js: str, lang: str) -> set[str]:
    block = _extract_i18n_block(js, lang)
    return set(re.findall(r"'([a-zA-Z][\w.]*)'\s*:", block))


def test_admin_html_toggle_button_and_i18n_attrs():
    """
    GIVEN app/static/admin.html
    WHEN the source is scanned for the language toggle wiring
    THEN it contains id="lang-toggle-btn" (btn btn-secondary lang-toggle)
      AND data-i18n= appears at least 40 times (static text coverage)
      AND data-i18n-placeholder= appears at least 5 times (attribute text)
      AND the h1 sits inside .header-row together with the toggle button
    """
    html = _read_static("admin.html")
    assert 'id="lang-toggle-btn"' in html, "admin.html missing the lang toggle button"
    assert re.search(
        r'class="btn btn-secondary lang-toggle"', html
    ), "toggle button must reuse .btn .btn-secondary + .lang-toggle"
    assert html.count("data-i18n=") >= 40, (
        f"expected >=40 data-i18n nodes, found {html.count('data-i18n=')}"
    )
    assert html.count("data-i18n-placeholder=") >= 5, (
        "expected >=5 data-i18n-placeholder nodes"
    )
    header_idx = html.index('class="header-row"')
    h1_idx = html.index("<h1")
    btn_idx = html.index('id="lang-toggle-btn"')
    assert header_idx < h1_idx < btn_idx, (
        ".header-row must wrap the h1 and the toggle button"
    )


def test_admin_html_anti_flash_head_script():
    """
    GIVEN app/static/admin.html
    WHEN the <head> inline script is scanned (before first paint)
    THEN it reads localStorage('admin_ai_lang') and adds lang-en to
       document.documentElement — applying the saved language class BEFORE
       the first paint so no wrong-language flash occurs
    """
    html = _read_static("admin.html")
    head = html[: html.index("</head>")]
    assert "admin_ai_lang" in head, "head script must consult admin_ai_lang"
    assert "lang-en" in head, "head script must set the lang-en class"
    assert "localStorage.getItem" in head, "head script must read localStorage"


def test_admin_js_i18n_engine_present():
    """
    GIVEN app/static/admin.js
    WHEN the source is scanned for the i18n engine pieces
    THEN it defines var LANG_KEY = 'admin_ai_lang', var I18N with zh and en
       blocks, function t(, and function applyStaticI18n(
    """
    js = _read_static("admin.js")
    assert "var LANG_KEY = 'admin_ai_lang';" in js
    assert "var I18N = {" in js
    assert re.search(r"\bzh:\s*\{", js), "I18N missing zh block"
    assert re.search(r"\ben:\s*\{", js), "I18N missing en block"
    assert re.search(r"function\s+t\s*\(\s*key\s*\)", js), "missing t(key)"
    assert re.search(r"function\s+applyStaticI18n\s*\(", js), (
        "missing applyStaticI18n()"
    )


def test_admin_js_bilingual_literal_cap():
    """
    GIVEN app/static/admin.js
    WHEN every「'x / y'」string literal is counted
    THEN at most 2 remain — the Plan-B migration replaced them with t() calls;
        the cap prevents the concatenated bilingual style from creeping back.
        (Current known residual: '发送中... / Sending...' — the literal is
        anchored by test_admin_history_ui.py's ±300-char window assertion,
        which lives outside this change's commit scope.)
    """
    js = _read_static("admin.js")
    # [^'\n]* — a JS single-quoted literal cannot span lines, so the match
    # must stay on one line; otherwise the pattern pairs unrelated quotes
    # across comment blocks and counts whole file regions as "literals".
    residuals = re.findall(r"'[^'\n]* / [^'\n]*'", js)
    assert len(residuals) <= 2, (
        f"bilingual literal cap exceeded ({len(residuals)} > 2): {residuals}"
    )


def test_admin_i18n_dict_zh_en_parity():
    """
    GIVEN app/static/admin.js with the I18N dictionary
    WHEN the zh and en object blocks are extracted and their keys compared
    THEN both blocks define exactly the same key set (no missing en entry —
       t() falls back to zh, which would leak Chinese text into EN mode)
    """
    js = _read_static("admin.js")
    zh_keys = _i18n_keys(js, "zh")
    en_keys = _i18n_keys(js, "en")
    assert zh_keys, "zh block parsed empty"
    assert en_keys, "en block parsed empty"
    assert zh_keys == en_keys, (
        "I18N zh/en key mismatch — missing zh: "
        f"{sorted(zh_keys - en_keys)}, missing en: {sorted(en_keys - zh_keys)}"
    )


def test_admin_css_header_row_and_lang_toggle_rules():
    """
    GIVEN app/static/admin.css
    WHEN the header layout rules are scanned
    THEN .header-row is a flex row (h1 + toggle button on one line, spread
       apart) and .lang-toggle carries its compact padding/font-size
    """
    css = _read_static("admin.css")
    assert re.search(
        r"\.header-row\s*\{[^}]*display:\s*flex", css
    ), ".header-row must be a flex container"
    assert re.search(
        r"\.header-row\s*\{[^}]*justify-content:\s*space-between", css
    ), ".header-row must spread h1 and the button apart"
    assert re.search(r"\.lang-toggle\s*\{[^}]*padding:", css), (
        ".lang-toggle must define its compact padding"
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
