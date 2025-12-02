"""Tests for the Gigya authentication module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientSession

from dompower import (
    GigyaAuthenticator,
    InvalidCredentialsError,
    TFAProvider,
    TFATarget,
    TFAVerificationError,
    TokenPair,
)
from dompower.const import (
    GIGYA_ERROR_INVALID_PASSWORD,
    GIGYA_ERROR_TFA_PENDING,
)


@pytest.fixture
def mock_session() -> MagicMock:
    """Create a mock aiohttp session."""
    return MagicMock(spec=ClientSession)


class TestGigyaAuthenticatorInit:
    """Tests for GigyaAuthenticator initialization."""

    def test_init_without_cookie_file(self, mock_session: MagicMock) -> None:
        """Test initialization without cookie file."""
        auth = GigyaAuthenticator(mock_session)
        assert auth._session is mock_session
        assert auth._cookie_file is None
        assert not auth._session_initialized

    def test_init_with_cookie_file(self, mock_session: MagicMock, tmp_path) -> None:
        """Test initialization with cookie file."""
        cookie_file = tmp_path / "cookies.json"
        auth = GigyaAuthenticator(mock_session, cookie_file=cookie_file)
        assert auth._cookie_file == cookie_file


class TestGigyaAuthenticatorLogin:
    """Tests for the login flow."""

    @pytest.mark.asyncio
    async def test_login_invalid_credentials(self, mock_session: MagicMock) -> None:
        """Test login with invalid credentials raises InvalidCredentialsError."""
        auth = GigyaAuthenticator(mock_session)

        # Mock the internal methods
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

    @pytest.mark.asyncio
    async def test_login_tfa_required(self, mock_session: MagicMock) -> None:
        """Test login that requires TFA returns LoginResult with tfa_required=True."""
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
                assert auth._gigya_session.reg_token == "test-reg-token"  # noqa: S105

    @pytest.mark.asyncio
    async def test_login_success_no_tfa(self, mock_session: MagicMock) -> None:
        """Test successful login without TFA."""
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
                assert auth._gigya_session.login_token == "test-login-token"  # noqa: S105


class TestTFAVerification:
    """Tests for TFA verification."""

    @pytest.mark.asyncio
    async def test_verify_tfa_invalid_code(self, mock_session: MagicMock) -> None:
        """Test TFA verification with invalid code raises TFAVerificationError."""
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


class TestCookiePersistence:
    """Tests for cookie persistence."""

    def test_export_cookies_empty(self, mock_session: MagicMock) -> None:
        """Test exporting cookies when jar is empty."""
        mock_session.cookie_jar = []
        auth = GigyaAuthenticator(mock_session)

        result = auth.export_cookies()

        assert result == {"version": 1, "cookies": []}

    def test_import_cookies(self, mock_session: MagicMock) -> None:
        """Test importing cookies."""
        mock_cookie_jar = MagicMock()
        mock_session.cookie_jar = mock_cookie_jar
        auth = GigyaAuthenticator(mock_session)

        data = {
            "version": 1,
            "cookies": [
                {"name": "gmid", "value": "test-gmid", "domain": ".dominionenergy.com"},
                {"name": "ucid", "value": "test-ucid", "domain": ".dominionenergy.com"},
            ],
        }

        result = auth.import_cookies(data)

        assert result is True
        assert mock_cookie_jar.update_cookies.call_count == 2


class TestModels:
    """Tests for new data models."""

    def test_tfa_target_creation(self) -> None:
        """Test TFATarget dataclass."""
        target = TFATarget(
            id="phone-123",
            obfuscated="+########1234",
            provider=TFAProvider.PHONE,
            last_method="sms",
        )

        assert target.id == "phone-123"
        assert target.obfuscated == "+########1234"
        assert target.provider == TFAProvider.PHONE
        assert target.last_method == "sms"

    def test_tfa_provider_values(self) -> None:
        """Test TFAProvider enum values."""
        assert TFAProvider.PHONE.value == "gigyaPhone"
        assert TFAProvider.EMAIL.value == "gigyaEmail"

    def test_token_pair_creation(self) -> None:
        """Test TokenPair dataclass."""
        pair = TokenPair(
            access_token="access-123",  # noqa: S106
            refresh_token="refresh-456",  # noqa: S106
        )

        assert pair.access_token == "access-123"  # noqa: S105
        assert pair.refresh_token == "refresh-456"  # noqa: S105
        assert pair.expires_at is None
