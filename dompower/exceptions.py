"""Exceptions for the dompower library."""

from __future__ import annotations


class DompowerError(Exception):
    """Base exception for all dompower errors."""


class AuthenticationError(DompowerError):
    """Base class for authentication-related errors."""


class InvalidAuthError(AuthenticationError):
    """Raised when authentication credentials or tokens are invalid."""


class TokenExpiredError(AuthenticationError):
    """Raised when tokens have expired and cannot be refreshed."""


class BrowserAuthRequiredError(AuthenticationError):
    """Raised when browser-based authentication is required.

    This occurs on initial login since CAPTCHA prevents automated authentication.
    The user must manually log in via browser and extract tokens.
    """

    def __init__(
        self,
        message: str = "Browser authentication required",
        auth_url: str | None = None,
    ) -> None:
        """Initialize the exception.

        Args:
            message: Error message to display.
            auth_url: URL where the user should authenticate.
        """
        super().__init__(message)
        self.auth_url = auth_url


class CannotConnectError(DompowerError):
    """Raised when unable to connect to the API server."""


class ApiError(DompowerError):
    """Raised when the API returns an error response.

    Attributes:
        status_code: HTTP status code from the response.
        response_text: Raw response text from the API.
        api_code: API-specific error code, if available.
        api_message: API-specific error message, if available.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_text: str | None = None,
        api_code: int | None = None,
        api_message: str | None = None,
    ) -> None:
        """Initialize the exception.

        Args:
            message: Error message to display.
            status_code: HTTP status code from the response.
            response_text: Raw response text from the API.
            api_code: API-specific error code.
            api_message: API-specific error message.
        """
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text
        self.api_code = api_code
        self.api_message = api_message


class RateLimitError(ApiError):
    """Raised when rate limited by the API.

    Attributes:
        retry_after: Seconds to wait before retrying, if provided by the API.
    """

    def __init__(
        self,
        message: str = "Rate limited by API",
        *,
        retry_after: int | None = None,
        **kwargs: int | str | None,
    ) -> None:
        """Initialize the exception.

        Args:
            message: Error message to display.
            retry_after: Seconds to wait before retrying.
            **kwargs: Additional arguments passed to ApiError.
        """
        super().__init__(message, **kwargs)  # type: ignore[arg-type]
        self.retry_after = retry_after


# Gigya Authentication Exceptions


class GigyaError(AuthenticationError):
    """Base class for Gigya authentication errors.

    Attributes:
        error_code: Gigya-specific error code.
        call_id: Gigya call ID for debugging.
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: int | None = None,
        call_id: str | None = None,
    ) -> None:
        """Initialize the exception.

        Args:
            message: Error message to display.
            error_code: Gigya error code.
            call_id: Gigya call ID for debugging.
        """
        super().__init__(message)
        self.error_code = error_code
        self.call_id = call_id


class InvalidCredentialsError(GigyaError):
    """Raised when username or password is invalid.

    Gigya error codes:
    - 403042: Invalid LoginID (email not found)
    - 403043: Invalid Login or Password
    """


class TFARequiredError(GigyaError):
    """Raised when two-factor authentication is required.

    This is not an error condition - it indicates normal flow
    when TFA is enabled on the account. Gigya error code: 403101

    Attributes:
        reg_token: Registration token for TFA flow.
        uid: User ID.
    """

    def __init__(
        self,
        message: str = "Two-factor authentication required",
        *,
        reg_token: str | None = None,
        uid: str | None = None,
        **kwargs: int | str | None,
    ) -> None:
        """Initialize the exception.

        Args:
            message: Error message to display.
            reg_token: Registration token for TFA flow.
            uid: User ID.
            **kwargs: Additional arguments passed to GigyaError.
        """
        super().__init__(message, **kwargs)  # type: ignore[arg-type]
        self.reg_token = reg_token
        self.uid = uid


class TFAVerificationError(GigyaError):
    """Raised when TFA code verification fails."""


class TFAExpiredError(GigyaError):
    """Raised when TFA assertion JWT has expired.

    Gigya error code: 400006. The gigyaAssertion token expires
    after approximately 5 minutes.
    """
