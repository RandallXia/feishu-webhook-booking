# test_admin_page.py — Tests for GET /admin/ai static admin page (app/main.py)
#
# The admin page is a static HTML shell served by the FastAPI route. It is
# accessible regardless of AI_ENABLED — data endpoints (/admin/ai/profile,
# /admin/ai/test) handle auth and return 404/401 when AI is off.
#
# Strategy:
#   - conftest.py pins AI_ENABLED="false" by default, so all tests use the
#     lifespan default without any AI mocking.
#   - httpx.AsyncClient(transport=ASGITransport(app), base_url="http://testserver")
#     with the lifespan async context manager run directly.
#   - The page is a static file read at request time — no caching.
#   - Assets (admin.css / admin.js) are served via a StaticFiles mount at
#     /admin/ai/assets; tests assert all three 200 + the HTML references them.

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
from httpx import ASGITransport

from app.main import app, lifespan

TEST_BASE_URL = "http://testserver"
_STATIC_DIR = Path(__file__).resolve().parents[2] / "app" / "static"


async def test_get_admin_ai_returns_html():
    """
    GIVEN the FastAPI app is running
    WHEN GET /admin/ai is requested
    THEN the response status is 200
      AND the content-type is text/html
      AND the body contains expected page elements (existing ids preserved)
    """
    async with lifespan(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get("/admin/ai")

    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    body = response.text
    # Existing element ids — zero-regression contract.
    assert "token-input" in body
    assert "load-profile-btn" in body
    assert "test-prompt-btn" in body
    assert "result-panel" in body
    assert "X-Admin-Token" in body


async def test_admin_ai_accessible_when_ai_disabled():
    """
    GIVEN AI_ENABLED=false (conftest default)
    WHEN GET /admin/ai is requested
    THEN the response status is 200
      (the page is a static shell — data endpoints handle auth)
    """
    async with lifespan(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get("/admin/ai")

    assert response.status_code == 200


async def test_admin_html_references_static_assets():
    """
    GIVEN the FastAPI app is running
    WHEN GET /admin/ai is requested
    THEN the HTML body references the split assets via /admin/ai/assets/ paths
      AND the new skeleton section ids are present
    """
    async with lifespan(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get("/admin/ai")

    body = response.text
    # Asset link contract — the page must load the split css + js.
    assert "/admin/ai/assets/admin.css" in body
    assert "/admin/ai/assets/admin.js" in body
    # New skeleton section ids (todo 8).
    assert 'id="status-bar"' in body
    assert 'id="ai-connection-section"' in body
    assert 'id="extract-section"' in body
    assert 'id="bill-section"' in body
    # Summary-field dropdown lives in extract-section (split field mapping):
    # summary is the sole target=extract field, so it is configured against the
    # extract-table field list, not the bill mapping table.
    assert 'id="summary-field-select"' in body


async def test_admin_css_asset_served_200():
    """
    GIVEN the FastAPI app is running
    WHEN GET /admin/ai/assets/admin.css is requested
    THEN the response status is 200
      AND the content-type is text/css
    """
    async with lifespan(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get("/admin/ai/assets/admin.css")

    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")


async def test_admin_js_asset_served_200():
    """
    GIVEN the FastAPI app is running
    WHEN GET /admin/ai/assets/admin.js is requested
    THEN the response status is 200
      AND the content-type is application/javascript (or javascript)
    """
    async with lifespan(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get("/admin/ai/assets/admin.js")

    assert response.status_code == 200
    ctype = response.headers.get("content-type", "")
    assert "javascript" in ctype, f"unexpected content-type: {ctype}"


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


async def test_get_favicon_returns_svg():
    """
    GIVEN the FastAPI app is running
    WHEN GET /admin/ai/assets/favicon.svg is requested
    THEN the response status is 200
      AND the content-type contains svg
      AND the body contains the Feishu brand blue #3370ff
    """
    async with lifespan(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get("/admin/ai/assets/favicon.svg")

    assert response.status_code == 200
    assert "svg" in response.headers.get("content-type", "")
    assert b"#3370ff" in response.content


async def test_admin_html_ocr_flow_section_exists():
    """
    GIVEN the FastAPI app is running
    WHEN GET /admin/ai is requested
    THEN the response status is 200
      AND the body contains the OCR Flow Tester card element ids
    """
    async with lifespan(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get("/admin/ai")

    assert response.status_code == 200
    body = response.text
    assert 'id="ocr-flow-section"' in body
    assert 'id="ocr-flow-text"' in body
    assert 'id="ocr-flow-token"' in body
    assert 'id="ocr-flow-btn"' in body
    assert 'id="ocr-flow-result"' in body
