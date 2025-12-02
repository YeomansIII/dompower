"""Data models for the dompower library."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class MeterType(Enum):
    """Type of utility meter."""

    ELECTRIC = "electric"
    GAS = "gas"


class UsageResolution(Enum):
    """Resolution for usage data queries."""

    HALF_HOURLY = "HH"  # 30-minute intervals
    HOURLY = "hourly"
    DAILY = "daily"
    MONTHLY = "monthly"


class ServiceType(Enum):
    """Type of utility service."""

    ELECTRICITY = "electricity"
    GAS = "gas"


@dataclass(frozen=True)
class Account:
    """Represents a Dominion Energy account.

    Attributes:
        account_id: Unique identifier for the account.
        account_number: Customer-facing account number.
        meter_number: Meter identifier (needed for usage data queries).
        service_address: Address where service is provided.
        meter_type: Type of meter (electric or gas).
        is_active: Whether the account is currently active.
    """

    account_id: str
    account_number: str
    meter_number: str
    service_address: str
    meter_type: MeterType
    is_active: bool


@dataclass(frozen=True)
class UsageData:
    """Energy usage data for a time period.

    Attributes:
        start_time: Start of the measurement period.
        end_time: End of the measurement period.
        consumption: Amount of energy consumed.
        unit: Unit of measurement (e.g., "kWh", "therms").
        cost: Cost for this usage period, if available.
    """

    start_time: datetime
    end_time: datetime
    consumption: float
    unit: str
    cost: float | None = None


@dataclass(frozen=True)
class BillingData:
    """Billing information for a billing period.

    Attributes:
        billing_period_start: Start date of the billing period.
        billing_period_end: End date of the billing period.
        total_usage: Total energy usage during the period.
        total_cost: Total cost for the billing period.
        due_date: Payment due date.
        is_paid: Whether the bill has been paid.
        statement_date: Date the statement was issued.
    """

    billing_period_start: date
    billing_period_end: date
    total_usage: float
    total_cost: float
    due_date: date
    is_paid: bool
    statement_date: date


@dataclass(frozen=True)
class IntervalUsageData:
    """Energy usage data for a 30-minute interval.

    This is the high-resolution data returned from the Excel download endpoint,
    which provides 30-minute (half-hourly) consumption data.

    Attributes:
        timestamp: Start time of the 30-minute interval.
        consumption: Amount of energy consumed in kWh.
        unit: Unit of measurement (typically "kWh").
    """

    timestamp: datetime
    consumption: float
    unit: str = "kWh"


@dataclass(frozen=True)
class TokenPair:
    """Access and refresh token pair.

    Attributes:
        access_token: JWT access token for API requests.
        refresh_token: Token used to obtain new access tokens.
        expires_at: When the access token expires, if known.
    """

    access_token: str
    refresh_token: str
    expires_at: datetime | None = None


@dataclass(frozen=True)
class BillPeriodData:
    """Billing period data from the bill forecast API.

    Attributes:
        charges: Total charges in dollars.
        usage: Total usage in kWh.
        period_start: Start date of the billing period.
        period_end: End date of the billing period.
    """

    charges: float
    usage: float
    period_start: date | None = None
    period_end: date | None = None


@dataclass(frozen=True)
class BillForecast:
    """Bill forecast data from the Dominion Energy API.

    This data comes from the /Service/api/1/bill/billForecast endpoint
    and includes the last bill, current billing period, and usage data.

    Attributes:
        last_bill: Previous billing period data with charges and usage.
        current_period_start: Start date of the current billing period.
        current_period_end: End date of the current billing period.
        current_usage_kwh: Current usage in kWh for the current period.
        is_tou: Whether the account is on time-of-use pricing.
    """

    last_bill: BillPeriodData
    current_period_start: date
    current_period_end: date
    current_usage_kwh: float
    is_tou: bool

    @property
    def derived_rate(self) -> float | None:
        """Calculate the effective $/kWh rate from the last bill.

        Returns:
            The calculated rate (charges / usage) or None if usage is zero.
        """
        if self.last_bill.usage > 0:
            return self.last_bill.charges / self.last_bill.usage
        return None


# Gigya Authentication Models


class TFAProvider(Enum):
    """Available two-factor authentication providers."""

    PHONE = "gigyaPhone"
    EMAIL = "gigyaEmail"


@dataclass(frozen=True)
class TFATarget:
    """A TFA verification target (phone number or email).

    Attributes:
        id: Provider-specific ID for this target.
        obfuscated: Obfuscated display string (e.g., "+########XXX").
        provider: The TFA provider type.
        last_method: Last method used (e.g., "sms") for phone.
        last_verification: Timestamp of last verification, if available.
    """

    id: str
    obfuscated: str
    provider: TFAProvider
    last_method: str | None = None
    last_verification: str | None = None


@dataclass(frozen=True)
class LoginResult:
    """Result from credential submission.

    Attributes:
        success: Whether login succeeded without TFA.
        tfa_required: Whether TFA is required to complete login.
        tfa_providers: Available TFA providers, if TFA is required.
        reg_token: Registration token for TFA flow, if TFA is required.
        uid: User ID from the login response.
    """

    success: bool
    tfa_required: bool
    tfa_providers: list[TFAProvider] | None = None
    reg_token: str | None = None
    uid: str | None = None


@dataclass
class GigyaSession:
    """Internal session state during Gigya authentication.

    This tracks the various tokens and state needed across the
    multi-step TFA authentication flow.

    Attributes:
        reg_token: Registration token from login (used throughout TFA).
        uid: User identifier.
        gigya_assertion: TFA assertion JWT (~5 min expiry).
        phv_token: Phone verification token (for phone TFA).
        tfa_target: Currently selected TFA target (phone/email).
        login_token: Token from finalize TFA (used for getAccountInfo).
        id_token: Gigya JWT (exchanged for Dominion API tokens).
    """

    reg_token: str | None = None
    uid: str | None = None
    gigya_assertion: str | None = None
    phv_token: str | None = None
    tfa_target: TFATarget | None = None
    login_token: str | None = None
    id_token: str | None = None


# Account and Customer Info Models


@dataclass(frozen=True)
class ServiceAddress:
    """Service location address.

    Attributes:
        street: Street name.
        house_number: House/building number.
        city: City name.
        state: State abbreviation (e.g., "VA").
        zip_code: ZIP code with optional extension.
        country: Country code (default: "US").
    """

    street: str
    house_number: str
    city: str
    state: str
    zip_code: str
    country: str = "US"

    def __str__(self) -> str:
        """Return formatted address string."""
        return (
            f"{self.house_number} {self.street}, "
            f"{self.city}, {self.state} {self.zip_code}"
        )


@dataclass(frozen=True)
class MeterDevice:
    """A meter/device associated with an account.

    Attributes:
        device_id: Device identifier (e.g., "000000000296117800").
        contract_id: Contract identifier.
        is_active: Whether the meter is currently active.
        has_ami: Whether this is an AMI (Advanced Metering Infrastructure) meter.
        net_metering: Whether net metering is enabled, or None if not applicable.
    """

    device_id: str
    contract_id: str
    is_active: bool
    has_ami: bool
    net_metering: bool | None = None


@dataclass(frozen=True)
class AccountInfo:
    """A Dominion Energy service account with detailed information.

    Attributes:
        account_number: Account number (e.g., "123456789123").
        premise_number: Premise/location number.
        service_address: Service location address.
        nickname: User-defined nickname for the account.
        is_active: Whether the account is currently active.
        is_default: Whether this is the default/primary account.
        meters: List of meters/devices associated with this account.
        ebill_enrolled: Whether enrolled in electronic billing.
        closing_date: Account closing date if inactive, or None.
    """

    account_number: str
    premise_number: str
    service_address: ServiceAddress
    nickname: str | None
    is_active: bool
    is_default: bool
    meters: list[MeterDevice]
    ebill_enrolled: bool
    closing_date: date | None = None

    @property
    def display_name(self) -> str:
        """Return a display name for the account."""
        if self.nickname:
            return f"{self.account_number} ({self.nickname})"
        if self.is_default:
            return f"{self.account_number} (Default)"
        return self.account_number

    @property
    def primary_meter(self) -> MeterDevice | None:
        """Return the primary (first active) meter, if any."""
        for meter in self.meters:
            if meter.is_active:
                return meter
        return self.meters[0] if self.meters else None


@dataclass(frozen=True)
class CustomerInfo:
    """Customer profile information including all accounts.

    Attributes:
        customer_number: Business partner/customer number.
        first_name: Customer's first name.
        last_name: Customer's last name.
        email: Primary email address.
        accounts: List of associated service accounts.
    """

    customer_number: str
    first_name: str
    last_name: str
    email: str
    accounts: list[AccountInfo]

    @property
    def full_name(self) -> str:
        """Return the customer's full name."""
        return f"{self.first_name} {self.last_name}"

    @property
    def active_accounts(self) -> list[AccountInfo]:
        """Return only active accounts."""
        return [acct for acct in self.accounts if acct.is_active]

    @property
    def default_account(self) -> AccountInfo | None:
        """Return the default account, if any."""
        for acct in self.accounts:
            if acct.is_default:
                return acct
        # Fall back to first active account
        return self.active_accounts[0] if self.active_accounts else None
