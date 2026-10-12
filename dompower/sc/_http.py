"""HTTP helpers shared by the SC authenticator and client."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

import aiohttp

from ..exceptions import ApiError, CannotConnectError, InvalidAuthError
from .const import (
    BIDGELY_PILOT_ID,
    SC_PORTAL_URL,
    SC_USER_AGENT,
)

if TYPE_CHECKING:
    from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=60)

# Called with (method, url, status, body) after each response. The sc-report
# command sets this to record the responses. It is None in normal use.
RESPONSE_RECORDER: ContextVar[Callable[[str, str, int, str], None] | None] = ContextVar(
    "sc_response_recorder", default=None
)


def cache_buster() -> str:
    """Return a millisecond timestamp for the "_" query parameter."""
    return str(int(time.time() * 1000))


def page_headers(referer: str | None = None) -> dict[str, str]:
    """Return headers for a normal (not AJAX) portal page request."""
    headers = {"User-Agent": SC_USER_AGENT}
    if referer:
        headers["Referer"] = referer
    return headers


def ajax_headers(
    verification_token: str,
    referer: str,
    *,
    include_origin: bool = False,
) -> dict[str, str]:
    """Return headers for an AJAX call to the portal fusionapi."""
    headers = {
        "User-Agent": SC_USER_AGENT,
        "__RequestVerificationToken": verification_token,
        "IsAjax": "true",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": referer,
    }
    if include_origin:
        headers["Origin"] = SC_PORTAL_URL
    return headers


def bidgely_headers(
    access_token: str | None = None,
    pilot_id: str = BIDGELY_PILOT_ID,
) -> dict[str, str]:
    """Return headers for a Bidgely request."""
    headers = {
        "User-Agent": SC_USER_AGENT,
        "IsAjax": "true",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": SC_PORTAL_URL,
        "Referer": f"{SC_PORTAL_URL}/",
        "X-Bidgely-Client-Type": "WIDGETS",
        "X-Bidgely-Pilot-Id": pilot_id,
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


async def async_request_text(
    session: ClientSession,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_data: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> str:
    """Send a request and return the response body as text.

    Raises:
        InvalidAuthError: If the server returns 401 or 403.
        CannotConnectError: For network errors and 5xx responses.
        ApiError: For other 4xx responses.
    """
    _LOGGER.debug("SC request: %s %s", method, url)
    try:
        async with session.request(
            method,
            url,
            headers=headers,
            json=json_data,
            params=params,
            timeout=_TIMEOUT,
        ) as resp:
            text = await resp.text(encoding="utf-8")
            status = resp.status
    except (aiohttp.ClientError, TimeoutError) as err:
        raise CannotConnectError(f"Failed to connect to {url}: {err}") from err

    recorder = RESPONSE_RECORDER.get()
    if recorder is not None:
        recorder(method, str(resp.url), status, text)

    if status in (401, 403):
        raise InvalidAuthError(f"Authentication failed: {status} for {url}")
    if status >= 500:
        raise CannotConnectError(f"Server error {status} for {url}")
    if status >= 400:
        raise ApiError(
            f"API error: {status} for {url}",
            status_code=status,
            response_text=text[:500],
        )
    return text


def decode_json(text: str, url: str) -> dict[str, Any]:
    """Decode a JSON object from a response body.

    Raises:
        ApiError: If the body is not a JSON object.
    """
    try:
        data = json.loads(text)
    except ValueError as err:
        raise ApiError(
            f"Response from {url} is not JSON",
            response_text=text[:500],
        ) from err
    if not isinstance(data, dict):
        raise ApiError(
            f"Response from {url} is not a JSON object",
            response_text=text[:500],
        )
    return data
