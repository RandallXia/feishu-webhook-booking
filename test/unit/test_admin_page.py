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

from __future__ import annotations

import httpx
from httpx import ASGITransport

from app.main import app, lifespan

TEST_BASE_URL = "http://testserver"


async def test_get_admin_ai_returns_html():
    """
    GIVEN the FastAPI app is running
    WHEN GET /admin/ai is requested
    THEN the response status is 200
      AND the content-type is text/html
      AND the body contains expected page elements
    """
    async with lifespan(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get("/admin/ai")

    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    body = response.text
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