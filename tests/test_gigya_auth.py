"""Tests for the Gigya authentication module."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientSession
from aiohttp import CookieJar as AiohttpCookieJar

from dompower import (
    GigyaAuthenticator,
    InvalidCredentialsError,
    TFAVerificationError,
)
from dompower.const import (
    GIGYA_ERROR_INVALID_PASSWORD,
    GIGYA_ERROR_TFA_PENDING,
    GIGYA_TFA_INIT,
    GIGYA_TFA_SEND_EMAIL,
    GIGYA_TFA_SEND_PHONE,
    GIGYA_TFA_VERIFY_EMAIL,
    GIGYA_TFA_VERIFY_PHONE,
)
from dompower.models import TFAProvider, TFATarget, TokenPair


@pytest.fixture
def mock_session() -> MagicMock:
    """Create a mock aiohttp session."""
    return MagicMock(spec=ClientSession)


# ---------------------------------------------------------------------------
# Login flow — error paths (high-value boundary tests)
# ---------------------------------------------------------------------------


class TestGigyaAuthenticatorLogin:
    """Tests for the login flow."""

    async def test_login_invalid_credentials(self, mock_session: MagicMock) -> None:
        """Invalid credentials raises InvalidCredentialsError with error_code."""
        auth = GigyaAuthenticator(mock_session)

        with patch.object(auth, "async_init_session", new_callable=AsyncMock):
            with patch.object(
                auth, "_async_gigya_post", new_callable=AsyncMock
            ) as mock_post:
                mock_post.return_value = {
                    "errorCode": GIGYA_ERROR_INVALID_PASSWORD,
                    "errorMessage": "Invalid Login or Password",
                    "callId": "test-call-id",
                }

                with pytest.raises(InvalidCredentialsError) as exc_info:
                    await auth.async_submit_credentials("user@example.com", "wrong")

                assert exc_info.value.error_code == GIGYA_ERROR_INVALID_PASSWORD

    async def test_login_tfa_required(self, mock_session: MagicMock) -> None:
        """TFA-pending response returns LoginResult with tfa_required=True."""
        auth = GigyaAuthenticator(mock_session)

        with patch.object(auth, "async_init_session", new_callable=AsyncMock):
            with patch.object(
                auth, "_async_gigya_post", new_callable=AsyncMock
            ) as mock_post:
                mock_post.return_value = {
                    "errorCode": GIGYA_ERROR_TFA_PENDING,
                    "errorMessage": "Account Pending TFA Verification",
                    "regToken": "test-reg-token",
                    "UID": "test-uid",
                    "id_token": "test-id-token",
                }

                result = await auth.async_submit_credentials(
                    "user@example.com", "password"
                )

                assert result.tfa_required is True
                assert result.success is False
                assert result.reg_token == "test-reg-token"  # noqa: S105

    async def test_login_success_no_tfa(self, mock_session: MagicMock) -> None:
        """Successful login without TFA returns success=True."""
        auth = GigyaAuthenticator(mock_session)

        with patch.object(auth, "async_init_session", new_callable=AsyncMock):
            with patch.object(
                auth, "_async_gigya_post", new_callable=AsyncMock
            ) as mock_post:
                mock_post.return_value = {
                    "errorCode": 0,
                    "UID": "test-uid",
                    "id_token": "test-id-token",
                    "sessionInfo": {
                        "login_token": "test-login-token",
                    },
                }

                result = await auth.async_submit_credentials(
                    "user@example.com", "password"
                )

                assert result.success is True
                assert result.tfa_required is False


# ---------------------------------------------------------------------------
# TFA verification — error paths
# ---------------------------------------------------------------------------


class TestTFAVerification:
    """Tests for TFA verification."""

    async def test_verify_tfa_invalid_code(self, mock_session: MagicMock) -> None:
        """Invalid TFA code raises TFAVerificationError."""
        auth = GigyaAuthenticator(mock_session)
        auth._gigya_session.gigya_assertion = "test-assertion"
        auth._gigya_session.reg_token = "test-reg-token"  # noqa: S105

        with patch.object(auth, "_async_gigya_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {
                "errorCode": 400003,
                "errorMessage": "Invalid code",
                "callId": "test-call-id",
            }

            with pytest.raises(TFAVerificationError):
                await auth.async_verify_tfa_code("000000")


# ---------------------------------------------------------------------------
# Cookie persistence — rewritten with real CookieJar
# ---------------------------------------------------------------------------


class TestCookiePersistence:
    """Tests for cookie export/import."""

    def test_export_cookies_empty(self, mock_session: MagicMock) -> None:
        """Exporting from an empty cookie jar returns empty cookies list."""
        mock_session.cookie_jar = []
        auth = GigyaAuthenticator(mock_session)

        result = auth.export_cookies()
        assert result == {"version": 1, "cookies": []}

    async def test_import_cookies_roundtrip(self) -> None:
        """Cookies imported via import_cookies are accessible in the session."""
        async with ClientSession(cookie_jar=AiohttpCookieJar()) as session:
            auth = GigyaAuthenticator(session)

            data = {
                "version": 1,
                "cookies": [
                    {
                        "name": "gmid",
                        "value": "test-gmid",
                        "domain": ".dominionenergy.com",
                    },
                    {
                        "name": "ucid",
                        "value": "test-ucid",
                        "domain": ".dominionenergy.com",
                    },
                ],
            }

            result = auth.import_cookies(data)
            assert result is True

            # Verify cookies are actually in the jar
            jar = session.cookie_jar
            cookies = {c.key: c.value for c in jar}
            assert cookies["gmid"] == "test-gmid"
            assert cookies["ucid"] == "test-ucid"


# ---------------------------------------------------------------------------
# TFA send/verify — assertion and phvToken handling
# ---------------------------------------------------------------------------


def _tfa_target(provider: TFAProvider) -> TFATarget:
    return TFATarget(id="target-id", obfuscated="***", provider=provider)


def _auth_with_tfa_state(mock_session: MagicMock) -> GigyaAuthenticator:
    auth = GigyaAuthenticator(mock_session)
    auth._gigya_session.reg_token = "test-reg-token"  # noqa: S105
    return auth


def _gigya_responses(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    """Simulate Gigya: initTFA and sendVerificationCode each mint an assertion."""
    if endpoint == GIGYA_TFA_INIT:
        return {"errorCode": 0, "gigyaAssertion": "assertion-from-init"}
    if endpoint in (GIGYA_TFA_SEND_PHONE, GIGYA_TFA_SEND_EMAIL):
        return {
            "errorCode": 0,
            "gigyaAssertion": "assertion-from-send",
            "phvToken": "phv-token-from-send",
        }
    if endpoint in (GIGYA_TFA_VERIFY_PHONE, GIGYA_TFA_VERIFY_EMAIL):
        return {"errorCode": 0, "providerAssertion": "provider-assertion"}
    raise AssertionError(f"unexpected endpoint {endpoint}")


class TestTFASendAndVerifyParams:
    """sendVerificationCode returns a refreshed gigyaAssertion plus a phvToken;
    completeVerification must be called with those, for both SMS and email.
    """

    @pytest.mark.parametrize("provider", [TFAProvider.PHONE, TFAProvider.EMAIL])
    async def test_send_stores_refreshed_assertion_and_phv_token(
        self, mock_session: MagicMock, provider: TFAProvider
    ) -> None:
        auth = _auth_with_tfa_state(mock_session)

        with patch.object(auth, "_async_gigya_get", side_effect=_gigya_responses):
            await auth.async_send_tfa_code(_tfa_target(provider))

        assert auth._gigya_session.gigya_assertion == "assertion-from-send"
        assert auth._gigya_session.phv_token == "phv-token-from-send"  # noqa: S105

    async def test_send_keeps_init_assertion_when_send_returns_none(
        self, mock_session: MagicMock
    ) -> None:
        auth = _auth_with_tfa_state(mock_session)

        def responses(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            if endpoint == GIGYA_TFA_INIT:
                return {"errorCode": 0, "gigyaAssertion": "assertion-from-init"}
            return {"errorCode": 0, "phvToken": "phv-token-from-send"}

        with patch.object(auth, "_async_gigya_get", side_effect=responses):
            await auth.async_send_tfa_code(_tfa_target(TFAProvider.EMAIL))

        assert auth._gigya_session.gigya_assertion == "assertion-from-init"

    @pytest.mark.parametrize(
        ("provider", "verify_endpoint"),
        [
            (TFAProvider.PHONE, GIGYA_TFA_VERIFY_PHONE),
            (TFAProvider.EMAIL, GIGYA_TFA_VERIFY_EMAIL),
        ],
    )
    async def test_verify_uses_send_assertion_and_phv_token(
        self, mock_session: MagicMock, provider: TFAProvider, verify_endpoint: str
    ) -> None:
        auth = _auth_with_tfa_state(mock_session)
        calls: list[tuple[str, dict[str, Any]]] = []

        async def recording(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            calls.append((endpoint, params))
            return _gigya_responses(endpoint, params)

        with (
            patch.object(auth, "_async_gigya_get", side_effect=recording),
            patch.object(auth, "_async_finalize_tfa", new_callable=AsyncMock),
            patch.object(auth, "_async_finalize_registration", new_callable=AsyncMock),
            patch.object(
                auth, "_async_complete_login", new_callable=AsyncMock
            ) as complete,
        ):
            complete.return_value = TokenPair(
                access_token="access",  # noqa: S106
                refresh_token="refresh",  # noqa: S106
            )
            await auth.async_send_tfa_code(_tfa_target(provider))
            await auth.async_verify_tfa_code("123456")

        verify_calls = [p for e, p in calls if e == verify_endpoint]
        assert len(verify_calls) == 1
        params = verify_calls[0]
        assert params["gigyaAssertion"] == "assertion-from-send"
        assert params["phvToken"] == "phv-token-from-send"
        assert params["code"] == "123456"
