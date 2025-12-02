"""Authentication handling for the dompower library."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from .const import (
    ACCESS_TOKEN_EXPIRY_MINUTES,
    BASE_URL,
    DEFAULT_HEADERS,
    ENDPOINT_REFRESH,
)
from .exceptions import (
    ApiError,
    CannotConnectError,
    InvalidAuthError,
    TokenExpiredError,
)
from .models import TokenPair

if TYPE_CHECKING:
    from aiohttp import ClientSession

# Type alias for the token update callback
TokenUpdateCallback = Callable[[str, str], None]

_LOGGER = logging.getLogger(__name__)


class TokenManager:
    """Manages access and refresh tokens for the Dominion Energy API.

    Handles automatic token refresh when tokens expire and notifies
    the caller via callback when tokens are updated.
    """

    def __init__(
        self,
        session: ClientSession,
        *,
        access_token: str | None = None,
        refresh_token: str | None = None,
        token_update_callback: TokenUpdateCallback | None = None,
    ) -> None:
        """Initialize the token manager.

        Args:
            session: aiohttp ClientSession for making requests.
            access_token: Initial access token, if available.
            refresh_token: Initial refresh token, if available.
            token_update_callback: Callback invoked when tokens are refreshed.
                Signature: (access_token: str, refresh_token: str) -> None
        """
        self._session = session
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._token_update_callback = token_update_callback
        self._token_expires_at: datetime | None = None

        # If we have tokens, assume they're fresh for now
        if access_token:
            self._token_expires_at = datetime.now(UTC) + timedelta(
                minutes=ACCESS_TOKEN_EXPIRY_MINUTES
            )

    @property
    def access_token(self) -> str | None:
        """Get the current access token."""
        return self._access_token

    @property
    def refresh_token(self) -> str | None:
        """Get the current refresh token."""
        return self._refresh_token

    @property
    def has_tokens(self) -> bool:
        """Check if both tokens are available."""
        return self._access_token is not None and self._refresh_token is not None

    @property
    def is_token_expired(self) -> bool:
        """Check if the access token has expired.

        Returns True if token is expired or if we don't know when it expires.
        """
        if self._token_expires_at is None:
            return True
        # Add 30 second buffer to avoid edge cases
        return datetime.now(UTC) >= (
            self._token_expires_at - timedelta(seconds=30)
        )

    def set_tokens(
        self,
        access_token: str,
        refresh_token: str,
        expires_at: datetime | None = None,
    ) -> None:
        """Set new tokens.

        Args:
            access_token: New access token.
            refresh_token: New refresh token.
            expires_at: When the access token expires.
        """
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._token_expires_at = expires_at or (
            datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRY_MINUTES)
        )

        # Notify callback if registered
        if self._token_update_callback:
            self._token_update_callback(access_token, refresh_token)

    def get_token_pair(self) -> TokenPair | None:
        """Get the current token pair as a TokenPair object."""
        if not self.has_tokens:
            return None
        return TokenPair(
            access_token=self._access_token,  # type: ignore[arg-type]
            refresh_token=self._refresh_token,  # type: ignore[arg-type]
            expires_at=self._token_expires_at,
        )

    async def async_refresh_tokens(self) -> TokenPair:
        """Refresh the access token using the refresh token.

        Returns:
            TokenPair with the new tokens.

        Raises:
            InvalidAuthError: If no refresh token is available.
            TokenExpiredError: If the refresh token is invalid/expired.
            CannotConnectError: If unable to connect to the API.
            ApiError: If the API returns an error.
        """
        if not self._refresh_token:
            raise InvalidAuthError("No refresh token available")

        if not self._access_token:
            raise InvalidAuthError("No access token available for refresh")

        url = f"{BASE_URL}{ENDPOINT_REFRESH}"
        headers = {
            **DEFAULT_HEADERS,
            "Authorization": f"Bearer {self._access_token}",
        }
        payload = {"refreshToken": self._refresh_token}

        _LOGGER.debug("Refreshing access token")

        try:
            async with self._session.post(
                url, headers=headers, json=payload
            ) as response:
                if response.status == 401:
                    raise TokenExpiredError(
                        "Refresh token expired - browser authentication required"
                    )

                if response.status != 200:
                    text = await response.text()
                    raise ApiError(
                        f"Token refresh failed: {response.status}",
                        status_code=response.status,
                        response_text=text,
                    )

                data = await response.json()

        except TokenExpiredError:
            raise
        except ApiError:
            raise
        except Exception as err:
            raise CannotConnectError(f"Failed to connect to API: {err}") from err

        # Extract new tokens from response
        # Response: {"status": {...}, "data": {"accessToken": ..., "refreshToken": ...}}
        status = data.get("status", {})
        if status.get("code") != 200:
            raise ApiError(
                f"Token refresh failed: {status.get('message', 'Unknown error')}",
                api_code=status.get("code"),
                api_message=status.get("message"),
            )

        token_data = data.get("data", {})
        new_access_token = token_data.get("accessToken")
        new_refresh_token = token_data.get("refreshToken")

        if not new_access_token or not new_refresh_token:
            raise ApiError("Token refresh response missing tokens")

        # Update stored tokens and notify callback
        self.set_tokens(new_access_token, new_refresh_token)

        _LOGGER.debug("Token refresh successful")

        return TokenPair(
            access_token=new_access_token,
            refresh_token=new_refresh_token,
            expires_at=self._token_expires_at,
        )

    async def async_ensure_valid_token(self) -> str:
        """Ensure we have a valid access token, refreshing if needed.

        Returns:
            Valid access token.

        Raises:
            InvalidAuthError: If no tokens are available.
            TokenExpiredError: If tokens cannot be refreshed.
        """
        if not self.has_tokens:
            raise InvalidAuthError("No tokens available - authentication required")

        if self.is_token_expired:
            await self.async_refresh_tokens()

        return self._access_token  # type: ignore[return-value]
