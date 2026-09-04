"""aiohttp-compatible session backed by curl_cffi with a browser TLS fingerprint.

Dominion's API host (prodsvc-dominioncip.smartcmobile.com) sits behind
Cloudflare bot protection. From some networks every request made with
Python's TLS stack, authenticated or not, gets an HTML "Access Denied" 403
(see https://github.com/YeomansIII/dompower/issues/4). The same requests
sent with a browser TLS fingerprint reach the API.

``BrowserSession`` exposes the subset of :class:`aiohttp.ClientSession` that
dompower uses (``request``/``get``/``post`` as async context managers, and
``cookie_jar``), backed by ``curl_cffi``. Install with ``pip install
"dompower[browser]"`` and pass it wherever a ``ClientSession`` is expected::

    async with BrowserSession() as session:
        client = DompowerClient(session, access_token=..., refresh_token=...)
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

DEFAULT_IMPERSONATE = "chrome"


class BrowserCookie:
    """Minimal stand-in for aiohttp's Morsel: ``key``, ``value`` and attributes."""

    def __init__(self, cookie: Any) -> None:
        self.key: str = cookie.name
        self.value: str = cookie.value
        self._attrs: dict[str, str] = {
            "domain": cookie.domain or "",
            "path": cookie.path or "/",
        }

    def get(self, name: str, default: Any = None) -> Any:
        return self._attrs.get(name, default)

    def __getitem__(self, name: str) -> str:
        return self._attrs[name]


class BrowserCookieJar:
    """The parts of aiohttp's CookieJar that GigyaAuthenticator relies on."""

    def __init__(self, session: Any) -> None:
        self._session = session

    def __iter__(self) -> Iterator[BrowserCookie]:
        return (BrowserCookie(c) for c in list(self._session.cookies.jar))

    def update_cookies(self, cookies: dict[str, str], response_url: Any = None) -> None:
        # Cookies set without a domain are sent to whichever host is requested,
        # matching aiohttp's behaviour for update_cookies() without a URL.
        for name, value in cookies.items():
            self._session.cookies.set(name, value)


class BrowserResponse:
    """Adapts a curl_cffi Response to the aiohttp ClientResponse surface used."""

    def __init__(self, response: Any) -> None:
        self._response = response
        self.status: int = response.status_code
        self.headers: Any = response.headers
        self.url: Any = response.url

    async def text(self) -> str:
        return str(self._response.text)

    async def json(self, content_type: str | None = None, **_: Any) -> Any:
        try:
            return self._response.json()
        except ValueError:
            return json.loads(self._response.text)

    async def read(self) -> bytes:
        return bytes(self._response.content)


class BrowserSession:
    """Drop-in replacement for ``aiohttp.ClientSession`` as used by dompower.

    Args:
        impersonate: curl_cffi browser profile (e.g. ``"chrome"``,
            ``"safari"``). Defaults to the latest Chrome profile.
        session: Pre-built ``curl_cffi.requests.AsyncSession``; mainly for
            tests. When omitted one is created with ``impersonate``.
    """

    def __init__(
        self,
        impersonate: str = DEFAULT_IMPERSONATE,
        *,
        session: Any | None = None,
    ) -> None:
        if session is None:
            try:
                from curl_cffi.requests import (  # type: ignore[import-not-found]
                    AsyncSession,
                )
            except ImportError as err:  # pragma: no cover - import guard
                raise ImportError(
                    "BrowserSession requires curl_cffi; install with "
                    '"pip install dompower[browser]"'
                ) from err
            session = AsyncSession(impersonate=impersonate)
        self._session = session
        self.cookie_jar = BrowserCookieJar(session)

    async def __aenter__(self) -> BrowserSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._session.close()

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        # aiohttp-only options that curl_cffi does not understand.
        kwargs.pop("ssl", None)
        kwargs.pop("proxy", None)
        return self._request_cm(method.upper(), url, kwargs)

    def get(self, url: str, **kwargs: Any) -> Any:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        return self.request("POST", url, **kwargs)

    @asynccontextmanager
    async def _request_cm(
        self, method: str, url: str, kwargs: dict[str, Any]
    ) -> AsyncIterator[BrowserResponse]:
        response = await self._session.request(method, url, **kwargs)
        yield BrowserResponse(response)
