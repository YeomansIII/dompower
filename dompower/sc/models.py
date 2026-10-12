"""Data models for the Dominion Energy South Carolina backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from ..models import MeterType


@dataclass(frozen=True)
class SCIntervalRead:
    """One raw interval reading from a Green Button register.

    Attributes:
        start: Start of the interval (timezone-aware, America/New_York).
            The second 01:00 hour of the DST fall-back day has fold=1.
            Use ``start.astimezone(UTC)`` as a dict or set key.
        duration_seconds: Length of the interval in seconds.
        value: Raw value as reported. Electric is Wh and gas is ft³.
            Solar export registers report negative values.
    """

    start: datetime
    duration_seconds: int
    value: float


@dataclass(frozen=True)
class SCRegisterUsage:
    """All readings for one physical meter register (ESPI UsagePoint).

    A net-metered solar account has two registers: grid delivery and solar
    export. Most accounts have one register for each meter type.

    Attributes:
        usage_point_id: Stable ESPI UsagePoint identifier.
        reads: Interval readings, in the order that the API returned them.
    """

    usage_point_id: str
    reads: list[SCIntervalRead] = field(default_factory=list)


@dataclass(frozen=True)
class SCAccount:
    """The account that belongs to an SC login.

    The SC portal gives one account for each login.

    Attributes:
        user_id: Bidgely user ID. Stable for the account.
        service_address: Service address and masked account number, as
            the portal shows it (e.g. "123 MAIN ST (*-****-****0-4464)").
        meter_types: Meter types that have data (electric and/or gas).
    """

    user_id: str
    service_address: str
    meter_types: list[MeterType]

    @property
    def has_electric(self) -> bool:
        """Return True if the account has an electric meter."""
        return MeterType.ELECTRIC in self.meter_types

    @property
    def has_gas(self) -> bool:
        """Return True if the account has a gas meter."""
        return MeterType.GAS in self.meter_types


@dataclass(frozen=True)
class SCSession:
    """Result of a complete SC login.

    Attributes:
        access_token: Bidgely bearer token for interval data requests.
        account: The account for this login.
    """

    access_token: str
    account: SCAccount


@dataclass(frozen=True)
class SCBillForecast:
    """Current bill forecast from the SC portal.

    This data comes from GetAccountAMIUsageAlerts. The dollar values include
    electric and gas together for accounts that have both.

    Attributes:
        period_start: First day of the current billing period.
        period_end: Projected last day of the current billing period.
        data_through: Last day that is included in cost_to_date.
        cost_to_date: Cost from period_start through data_through.
        forecasted_cost: Projected cost for the full billing period.
        last_year_cost: Cost for the same period last year, if known.
    """

    period_start: date
    period_end: date
    data_through: date
    cost_to_date: float
    forecasted_cost: float
    last_year_cost: float | None = None
