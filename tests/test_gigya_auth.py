"""Tests for the Gigya authentication module."""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientSession
from aiohttp import CookieJar as AiohttpCookieJar
from aresponses import ResponsesMockServer

from dompower import (
    GigyaAuthenticator,
    GigyaError,
    InvalidCredentialsError,
    TFAVerificationError,
)
from dompower.const import (
    GIGYA_ERROR_INVALID_JWT,
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


# ---------------------------------------------------------------------------
# Secret redaction — credentials must never reach the log
# ---------------------------------------------------------------------------

# Recognisable stand-ins for live credentials. If any of these turn up in
# caplog.text, the real thing would turn up in a user's Home Assistant log.
SENTINEL_ACCESS_TOKEN = "SENTINEL-ACCESS-TOKEN-a1b2c3d4"  # noqa: S105
SENTINEL_REFRESH_TOKEN = "SENTINEL-REFRESH-TOKEN-e5f6a7b8"  # noqa: S105
SENTINEL_REG_TOKEN = "SENTINEL-REG-TOKEN-c9d0e1f2"  # noqa: S105
SENTINEL_ID_TOKEN = "SENTINEL-ID-TOKEN-3a4b5c6d"  # noqa: S105
SENTINEL_LOGIN_TOKEN = "SENTINEL-LOGIN-TOKEN-7e8f9a0b"  # noqa: S105

EXCHANGE_HOST = "prodsvc-dominioncip.smartcmobile.com"
EXCHANGE_PATH = "/UsermanagementAPI/api/1/Login/auth"

GIGYA_LOGGER = "dompower.gigya_auth"


def _token_exchange_body(
    *,
    access_token: str = SENTINEL_ACCESS_TOKEN,
    refresh_token: str = SENTINEL_REFRESH_TOKEN,
) -> str:
    """Build a Dominion token-exchange response body."""
    return json.dumps(
        {
            "status": {
                "type": "success",
                "code": 200,
                "message": "success",
                "error": False,
            },
            "data": {
                "accessToken": access_token,
                "refreshToken": refresh_token,
                "expiresIn": 30,
            },
        }
    )


class TestTokenExchangeRedaction:
    """The token exchange handles live credentials; none may be logged."""

    async def test_success_path_never_logs_tokens_at_debug(
        self,
        aresponses: ResponsesMockServer,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Debug logging must not print the access or refresh token."""
        aresponses.add(
            EXCHANGE_HOST,
            EXCHANGE_PATH,
            "POST",
            response=aresponses.Response(
                body=_token_exchange_body(),
                content_type="application/json",
            ),
        )

        async with ClientSession() as session:
            auth = GigyaAuthenticator(session)

            with caplog.at_level(logging.DEBUG, logger=GIGYA_LOGGER):
                tokens = await auth._async_exchange_token("sentinel-id-token")

        # The tokens really were parsed out of the logged-about response.
        assert tokens.access_token == SENTINEL_ACCESS_TOKEN
        assert tokens.refresh_token == SENTINEL_REFRESH_TOKEN

        assert SENTINEL_ACCESS_TOKEN not in caplog.text
        assert SENTINEL_REFRESH_TOKEN not in caplog.text

        # ...but the exchange must stay traceable.
        assert "Dominion API tokens obtained" in caplog.text

    async def test_missing_token_error_never_logs_the_present_token(
        self,
        aresponses: ResponsesMockServer,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A half-populated response must not leak the token that IS set.

        This fires at ERROR level, i.e. at everybody's default verbosity.
        """
        aresponses.add(
            EXCHANGE_HOST,
            EXCHANGE_PATH,
            "POST",
            response=aresponses.Response(
                body=_token_exchange_body(refresh_token=""),
                content_type="application/json",
            ),
        )

        async with ClientSession() as session:
            auth = GigyaAuthenticator(session)

            # No debug flag: this is what a default install writes to disk.
            with caplog.at_level(logging.ERROR, logger=GIGYA_LOGGER):
                with pytest.raises(GigyaError):
                    await auth._async_exchange_token("sentinel-id-token")

        assert SENTINEL_ACCESS_TOKEN not in caplog.text

        # Diagnostics that make the failure actionable must survive.
        assert "missing tokens" in caplog.text
        assert "status type success" in caplog.text
        assert "refreshToken: missing" in caplog.text
        assert "accessToken: present" in caplog.text

    async def test_http_error_never_logs_the_response_body(
        self,
        aresponses: ResponsesMockServer,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A non-200 body may echo credentials; log/raise the status only."""
        body = json.dumps({"accessToken": SENTINEL_ACCESS_TOKEN})
        aresponses.add(
            EXCHANGE_HOST,
            EXCHANGE_PATH,
            "POST",
            response=aresponses.Response(status=500, body=body),
        )

        async with ClientSession() as session:
            auth = GigyaAuthenticator(session)

            with caplog.at_level(logging.DEBUG, logger=GIGYA_LOGGER):
                with pytest.raises(GigyaError) as exc_info:
                    await auth._async_exchange_token("sentinel-id-token")

        assert SENTINEL_ACCESS_TOKEN not in caplog.text
        # Home Assistant surfaces the exception message in its UI.
        assert SENTINEL_ACCESS_TOKEN not in str(exc_info.value)

        assert "500" in caplog.text
        assert "500" in str(exc_info.value)


class TestGigyaBodyRedaction:
    """Gigya error bodies carry regToken / id_token / login_token."""

    async def test_finalize_tfa_error_never_logs_session_secrets(
        self,
        mock_session: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """finalizeTFA failures must not dump the Gigya body."""
        auth = GigyaAuthenticator(mock_session)
        auth._gigya_session.gigya_assertion = "test-assertion"
        auth._gigya_session.reg_token = SENTINEL_REG_TOKEN

        response = {
            "errorCode": GIGYA_ERROR_INVALID_JWT,
            "errorMessage": "Invalid parameter value",
            "callId": "test-call-id",
            "regToken": SENTINEL_REG_TOKEN,
            "id_token": SENTINEL_ID_TOKEN,
            "sessionInfo": {"login_token": SENTINEL_LOGIN_TOKEN},
        }

        with patch.object(auth, "_async_gigya_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = response

            with caplog.at_level(logging.DEBUG, logger=GIGYA_LOGGER):
                await auth._async_finalize_tfa("provider-assertion")

        for secret in (SENTINEL_REG_TOKEN, SENTINEL_ID_TOKEN, SENTINEL_LOGIN_TOKEN):
            assert secret not in caplog.text

        # The failure must still be diagnosable. Assert on the step name and
        # the error details rather than the exact phrasing - a non-zero code
        # that still carries a session is legitimately worded differently.
        assert "finalizeTFA" in caplog.text
        assert str(GIGYA_ERROR_INVALID_JWT) in caplog.text
        assert "Invalid parameter value" in caplog.text
        assert "test-call-id" in caplog.text

    async def test_finalize_registration_error_never_logs_session_secrets(
        self,
        mock_session: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """finalizeRegistration failures must not dump the Gigya body."""
        auth = GigyaAuthenticator(mock_session)
        auth._gigya_session.reg_token = SENTINEL_REG_TOKEN

        response = {
            "errorCode": GIGYA_ERROR_INVALID_JWT,
            "errorMessage": "Invalid parameter value",
            "callId": "test-call-id",
            "regToken": SENTINEL_REG_TOKEN,
            "id_token": SENTINEL_ID_TOKEN,
            "sessionInfo": {"login_token": SENTINEL_LOGIN_TOKEN},
            "profile": {"email": "leaky@example.com"},
        }

        with patch.object(auth, "_async_gigya_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = response

            with caplog.at_level(logging.DEBUG, logger=GIGYA_LOGGER):
                await auth._async_finalize_registration()

        for secret in (
            SENTINEL_REG_TOKEN,
            SENTINEL_ID_TOKEN,
            SENTINEL_LOGIN_TOKEN,
            "leaky@example.com",
        ):
            assert secret not in caplog.text

        # The failure must still be diagnosable. Assert on the step name and
        # the error details rather than the exact phrasing - a non-zero code
        # that still carries a session is legitimately worded differently.
        assert "finalizeRegistration" in caplog.text
        assert str(GIGYA_ERROR_INVALID_JWT) in caplog.text
        assert "Invalid parameter value" in caplog.text
        assert "test-call-id" in caplog.text

    async def test_submit_credentials_never_logs_the_raw_email(
        self,
        mock_session: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The account's loginID must be obfuscated before it is logged."""
        auth = GigyaAuthenticator(mock_session)

        with patch.object(auth, "async_init_session", new_callable=AsyncMock):
            with patch.object(
                auth, "_async_gigya_post", new_callable=AsyncMock
            ) as mock_post:
                mock_post.return_value = {
                    "errorCode": 0,
                    "UID": "test-uid",
                    "sessionInfo": {"login_token": SENTINEL_LOGIN_TOKEN},
                }

                with caplog.at_level(logging.DEBUG, logger=GIGYA_LOGGER):
                    await auth.async_submit_credentials(
                        "sentineluser@sentineldomain.example",
                        "sentinel-passphrase",
                    )

        assert "sentineluser" not in caplog.text
        assert "sentineldomain" not in caplog.text
        assert "sentinel-passphrase" not in caplog.text
        assert SENTINEL_LOGIN_TOKEN not in caplog.text

        # An obfuscated form is still logged so the account is identifiable.
        assert "Submitting credentials for s***@s***.example" in caplog.text
