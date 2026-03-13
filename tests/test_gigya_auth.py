"""Tests for the Gigya authentication module."""

from __future__ import annotations

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
)


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
