"""Tests for the curl_cffi-backed BrowserSession adapter (no network)."""

from __future__ import annotations

from http.cookiejar import Cookie, CookieJar
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from dompower import BrowserSession, GigyaAuthenticator
from dompower.browser_session import BrowserCookieJar


class _FakeCookies:
    """Mimics curl_cffi's Cookies: a CookieJar plus set()."""

    def __init__(self) -> None:
        self.jar = CookieJar()

    def set(self, name: str, value: str, domain: str = "", path: str = "/") -> None:
        self.jar.set_cookie(
            Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=bool(domain),
                domain_initial_dot=False,
                path=path,
                path_specified=True,
                secure=False,
                expires=None,
                discard=True,
                comment=None,
                comment_url=None,
                rest={},
            )
        )


def _fake_response(
    status: int = 200, text: str = "{}", content: bytes = b""
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"content-type": "application/json"}
    resp.url = "https://example.test/"
    resp.text = text
    resp.content = content or text.encode()
    resp.json = MagicMock(side_effect=lambda: __import__("json").loads(text))
    return resp


@pytest.fixture
def fake_curl_session() -> MagicMock:
    session = MagicMock()
    session.cookies = _FakeCookies()
    session.request = AsyncMock(return_value=_fake_response(text='{"ok": 1}'))
    session.close = AsyncMock()
    return session


class TestBrowserSession:
    async def test_request_is_async_context_manager(
        self, fake_curl_session: MagicMock
    ) -> None:
        async with BrowserSession(session=fake_curl_session) as session:
            async with session.post(
                "https://example.test/x", json={"a": 1}, headers={"h": "v"}
            ) as response:
                assert response.status == 200
                assert await response.json(content_type=None) == {"ok": 1}
                assert await response.text() == '{"ok": 1}'
                assert await response.read() == b'{"ok": 1}'

        fake_curl_session.request.assert_awaited_once_with(
            "POST", "https://example.test/x", json={"a": 1}, headers={"h": "v"}
        )
        fake_curl_session.close.assert_awaited_once()

    async def test_aiohttp_only_kwargs_are_dropped(
        self, fake_curl_session: MagicMock
    ) -> None:
        session = BrowserSession(session=fake_curl_session)
        async with session.get("https://example.test/", ssl=False, params={"q": 1}):
            pass
        _, kwargs = fake_curl_session.request.await_args
        assert "ssl" not in kwargs
        assert kwargs["params"] == {"q": 1}

    def test_cookie_jar_roundtrip(self, fake_curl_session: MagicMock) -> None:
        jar = BrowserCookieJar(fake_curl_session)
        jar.update_cookies({"gmid": "abc", "other": "x"})
        cookies = {c.key: c for c in jar}
        assert cookies["gmid"].value == "abc"
        assert cookies["gmid"].get("domain", "") == ""
        assert cookies["gmid"]["path"] == "/"

    def test_gigya_cookie_export_import_works_with_browser_session(
        self, fake_curl_session: MagicMock
    ) -> None:
        session: Any = BrowserSession(session=fake_curl_session)
        auth = GigyaAuthenticator(session)
        session.cookie_jar.update_cookies({"gmid": "abc", "unrelated": "zzz"})
        exported = auth.export_cookies()
        assert [c["name"] for c in exported["cookies"]] == ["gmid"]

        fresh: Any = BrowserSession(session=MagicMock(cookies=_FakeCookies()))
        assert GigyaAuthenticator(fresh).import_cookies(exported) is True
        assert {c.key for c in fresh.cookie_jar} == {"gmid"}
