"""dompower - Async Python client for Dominion Energy API.

This library provides an async client for interacting with Dominion Energy's
API to retrieve energy usage data. It is designed to work seamlessly with
Home Assistant integrations.

Example:
    import aiohttp
    from dompower import DompowerClient

    async with aiohttp.ClientSession() as session:
        client = DompowerClient(
            session,
            access_token="your_access_token",
            refresh_token="your_refresh_token",
        )
        usage = await client.async_get_interval_usage(
            account_number="123456",
            meter_number="789",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
"""

from .auth import TokenManager, TokenUpdateCallback
from .client import DompowerClient
from .const import LOGIN_URL
from .exceptions import (
    ApiError,
    AuthenticationError,
    BrowserAuthRequiredError,
    CannotConnectError,
    DompowerError,
    GigyaError,
    InvalidAuthError,
    InvalidCredentialsError,
    RateLimitError,
    TFAExpiredError,
    TFARequiredError,
    TFAVerificationError,
    TokenExpiredError,
)
from .gigya_auth import GigyaAuthenticator
from .models import (
    Account,
    AccountInfo,
    BillForecast,
    BillingData,
    BillPeriodData,
    CustomerInfo,
    GigyaSession,
    IntervalUsageData,
    LoginResult,
    MeterDevice,
    MeterType,
    ServiceAddress,
    ServiceType,
    TFAProvider,
    TFATarget,
    TokenPair,
    UsageData,
    UsageResolution,
)

__version__ = "0.1.3"

__all__ = [
    "LOGIN_URL",
    "Account",
    "AccountInfo",
    "ApiError",
    "AuthenticationError",
    "BillForecast",
    "BillPeriodData",
    "BillingData",
    "BrowserAuthRequiredError",
    "CannotConnectError",
    "CustomerInfo",
    "DompowerClient",
    "DompowerError",
    "GigyaAuthenticator",
    "GigyaError",
    "GigyaSession",
    "IntervalUsageData",
    "InvalidAuthError",
    "InvalidCredentialsError",
    "LoginResult",
    "MeterDevice",
    "MeterType",
    "RateLimitError",
    "ServiceAddress",
    "ServiceType",
    "TFAExpiredError",
    "TFAProvider",
    "TFARequiredError",
    "TFATarget",
    "TFAVerificationError",
    "TokenExpiredError",
    "TokenManager",
    "TokenPair",
    "TokenUpdateCallback",
    "UsageData",
    "UsageResolution",
    "__version__",
]
