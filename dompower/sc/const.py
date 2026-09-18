"""Constants for the Dominion Energy South Carolina backend.

SC accounts do not use the Gigya/SEW platform of VA and NC. The SC customer
portal is an ASP.NET site, and interval data comes from Bidgely.
"""

from typing import Final
from zoneinfo import ZoneInfo

# Customer portal
SC_PORTAL_URL: Final[str] = "https://account.dominionenergysc.com"

SC_LOGIN_PAGE: Final[str] = "/access/"
SC_HOME_PAGE: Final[str] = "/"
SC_AUTHENTICATE: Final[str] = "/fusionapi/LoginWebApi/Authenticate/"
SC_VERIFY_2FA_TOKEN: Final[str] = "/fusionapi/LoginWebApi/Verify2FAToken/"  # noqa: S105
SC_INIT_AUTHENTICATION: Final[str] = "/fusionapi/LoginWebApi/InitAuthentication/"
SC_SEND_PIN_CODE: Final[str] = "/fusionapi/LoginWebApi/SendPINCode/"
SC_VERIFY_PIN: Final[str] = "/fusionapi/LoginWebApi/VerifyPIN/"
SC_ACCOUNT_LISTING: Final[str] = "/fusionapi/AccountManagementWebApi/GetAccountListing/"
SC_INIT_ACCOUNT: Final[str] = "/fusionapi/AccountSummaryWebApi/InitAccount/"
SC_BIDGELY_SDK_INIT: Final[str] = "/fusionapi/BidgelyWebApi/GetBidgelySDKInit/"
SC_AMI_USAGE_ALERTS: Final[str] = "/fusionapi/CommonWebApi/GetAccountAMIUsageAlerts/"

# Bidgely (interval data)
BIDGELY_URL: Final[str] = "https://desc-prodapi.bidgely.com"
BIDGELY_WC_SESSION: Final[str] = "/v2.0/web/wc-session"
BIDGELY_GB_DOWNLOAD: Final[str] = "/v2.0/dashboard/users/{user_id}/gb-download"
BIDGELY_CLIENT_ID: Final[str] = "prod_desc_widget"

# Bidgely is multi-tenant. The pilot ID selects the Dominion SC data pipeline.
# "10106" returns smoothed, hourly estimates. "10078" returns the real
# 15-minute meter reads (confirmed from browser DevTools by SC users).
BIDGELY_PILOT_ID: Final[str] = "10078"

# Bidgely measurement types
MEASUREMENT_ELECTRIC: Final[str] = "ELECTRIC"
MEASUREMENT_GAS: Final[str] = "GAS"

# The portal returns different content to clients that do not look like a
# mainstream browser.
SC_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)

SC_TIMEZONE: Final[ZoneInfo] = ZoneInfo("America/New_York")

# Authenticate "status" values
SC_AUTH_STATUS_FAILED: Final[str] = "failed"
SC_AUTH_STATUS_TFA: Final[str] = "twoFA"
