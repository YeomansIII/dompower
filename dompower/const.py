"""Constants for the dompower library."""

from typing import Final

# API Base URLs
BASE_URL: Final[str] = "https://prodsvc-dominioncip.smartcmobile.com"

# API Endpoints
ENDPOINT_LOGIN: Final[str] = "/UsermanagementAPI/api/1/Login/auth"
ENDPOINT_REFRESH: Final[str] = "/UsermanagementAPI/api/1/login/auth/refresh"
ENDPOINT_USAGE: Final[str] = "/UsermanagementAPI/api/1/Usage"
ENDPOINT_BILL_FORECAST: Final[str] = "/Service/api/1/bill/billForecast"
ENDPOINT_ACCOUNTS: Final[str] = "/UsermanagementAPI/api/1/Account"
ENDPOINT_USAGE_EXCEL: Final[str] = "/Service/api/1/Usage/DownloadExcelNew"
ENDPOINT_GET_BP_NUMBER: Final[str] = "/Service/api/1/FromDb/GetBpNumber"
ENDPOINT_GET_BUSINESS_MASTER: Final[str] = (
    "/Service/api/1/BusinessMaster/GetBusinessMaster"
)

# Default Headers
DEFAULT_HEADERS: Final[dict[str, str]] = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "uid": "1",
    "pt": "1",
    "channel": "WEB",
    "Origin": "https://myaccount.dominionenergy.com",
    "User-Agent": "dompower/0.1.0",
}

# Token expiration (in minutes)
ACCESS_TOKEN_EXPIRY_MINUTES: Final[int] = 30

# Retry configuration
MAX_RETRIES: Final[int] = 3
RETRY_DELAY_SECONDS: Final[float] = 1.0

# Login page URL (for browser auth instructions)
LOGIN_URL: Final[str] = (
    "https://login.dominionenergy.com/CommonLogin?SelectedAppName=Electric"
)

# Gigya Authentication
GIGYA_API_KEY: Final[str] = "4_6zEg-HY_0eqpgdSONYkJkQ"
GIGYA_BASE_URL: Final[str] = "https://auth.dominionenergy.com"
GIGYA_SDK_VERSION: Final[str] = "js_latest"
GIGYA_SDK_BUILD: Final[str] = "18148"

# Gigya Endpoints
GIGYA_BOOTSTRAP: Final[str] = "/accounts.webSdkBootstrap"
GIGYA_LOGIN: Final[str] = "/accounts.login"
GIGYA_TFA_PROVIDERS: Final[str] = "/accounts.tfa.getProviders"
GIGYA_TFA_INIT: Final[str] = "/accounts.tfa.initTFA"
GIGYA_TFA_FINALIZE: Final[str] = "/accounts.tfa.finalizeTFA"
GIGYA_FINALIZE_REGISTRATION: Final[str] = "/accounts.finalizeRegistration"
GIGYA_TFA_PHONE_NUMBERS: Final[str] = "/accounts.tfa.phone.getRegisteredPhoneNumbers"
GIGYA_TFA_EMAILS: Final[str] = "/accounts.tfa.email.getEmails"
GIGYA_TFA_SEND_PHONE: Final[str] = "/accounts.tfa.phone.sendVerificationCode"
GIGYA_TFA_SEND_EMAIL: Final[str] = "/accounts.tfa.email.sendVerificationCode"
GIGYA_TFA_VERIFY_PHONE: Final[str] = "/accounts.tfa.phone.completeVerification"
GIGYA_TFA_VERIFY_EMAIL: Final[str] = "/accounts.tfa.email.completeVerification"
GIGYA_ACCOUNT_INFO: Final[str] = "/accounts.getAccountInfo"

# Gigya Error Codes
GIGYA_ERROR_TFA_PENDING: Final[int] = 403101
GIGYA_ERROR_INVALID_LOGIN: Final[int] = 403042
GIGYA_ERROR_INVALID_PASSWORD: Final[int] = 403043
GIGYA_ERROR_INVALID_JWT: Final[int] = 400006
