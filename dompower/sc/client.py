"""Client for Dominion Energy South Carolina usage and bill data."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, TypeVar

from ..exceptions import ApiError, InvalidAuthError
from ..models import IntervalUsageData, MeterType
from ._http import (
    ajax_headers,
    async_request_text,
    bidgely_headers,
    cache_buster,
    decode_json,
    page_headers,
)
from .auth import SCAuthenticator, find_verification_token
from .const import (
    BIDGELY_GB_DOWNLOAD,
    BIDGELY_PILOT_ID,
    BIDGELY_URL,
    MEASUREMENT_ELECTRIC,
    MEASUREMENT_GAS,
    SC_AMI_USAGE_ALERTS,
    SC_HOME_PAGE,
    SC_LOGIN_PAGE,
    SC_PORTAL_URL,
    SC_TIMEZONE,
)
from .greenbutton import parse_registers
from .models import SCAccount, SCBillForecast, SCRegisterUsage, SCSession

if TYPE_CHECKING:
    from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T")

_METER_TYPE_TO_MEASUREMENT = {
    MeterType.ELECTRIC: MEASUREMENT_ELECTRIC,
    MeterType.GAS: MEASUREMENT_GAS,
}

# Electric values are Wh. Gas values are ft³ and are not converted.
_UNIT_AND_FACTOR = {
    MeterType.ELECTRIC: ("kWh", 0.001),
    MeterType.GAS: ("ft³", 1.0),
}


def registers_to_intervals(
    registers: list[SCRegisterUsage],
    meter_type: MeterType = MeterType.ELECTRIC,
) -> list[IntervalUsageData]:
    """Merge register readings into one IntervalUsageData for each interval.

    For each interval start time, positive values from all registers add to
    ``consumption``. Negative values add (as absolute values) to
    ``generation``. SC reports solar export as a separate register with
    negative values, so this works for both one and two registers.

    Args:
        registers: Parsed registers for one meter type.
        meter_type: ELECTRIC (values in Wh, output in kWh) or GAS (ft³).

    Returns:
        Intervals sorted by timestamp. Timestamps are in America/New_York.
    """
    unit, factor = _UNIT_AND_FACTOR[meter_type]
    # Key by the UTC instant. The two 01:00 hours on the DST fall-back day
    # differ only in ``fold``, and such local datetimes compare equal.
    consumption: dict[datetime, float] = {}
    generation: dict[datetime, float] = {}
    for register in registers:
        for read in register.reads:
            key = read.start.astimezone(UTC)
            consumption.setdefault(key, 0.0)
            generation.setdefault(key, 0.0)
            if read.value >= 0:
                consumption[key] += read.value * factor
            else:
                generation[key] += -read.value * factor

    return [
        IntervalUsageData(
            timestamp=key.astimezone(SC_TIMEZONE),
            consumption=round(consumption[key], 6),
            generation=round(generation[key], 6),
            unit=unit,
        )
        for key in sorted(consumption)
    ]


def parse_bill_forecast(payload: dict[str, object]) -> SCBillForecast:
    """Parse a GetAccountAMIUsageAlerts response.

    Raises:
        ApiError: If the response has an unexpected format.
    """
    try:
        alert = payload["data"]["amiUsageAlert"]  # type: ignore[index]
        last_year = (
            float(alert["lastYearTotalAmount"])
            if alert.get("lastYearAmountExists")
            else None
        )
        return SCBillForecast(
            period_start=datetime.fromisoformat(
                alert["currentBillUsageStartDate"]
            ).date(),
            period_end=datetime.fromisoformat(alert["currentBillUsageEndDate"]).date(),
            data_through=datetime.fromisoformat(alert["currentBillThroughDate"]).date(),
            cost_to_date=float(alert["totalCostUnbilledConsumption"]),
            forecasted_cost=round(
                float(alert["currentCostPerDay"])
                * float(alert["numberOfDaysInCurrentBill"]),
                2,
            ),
            last_year_cost=last_year,
        )
    except (KeyError, TypeError, ValueError) as err:
        raise ApiError(f"Unexpected SC bill forecast response: {err}") from err


class DominionSCClient:
    """Async client for Dominion Energy South Carolina.

    The portal session expires after some minutes, so the client logs in
    again when a request fails with an authentication error.

    Example:
        async with aiohttp.ClientSession(cookie_jar=create_sc_cookie_jar()) as s:
            client = DominionSCClient(s, "user", "password", tfa_token=token)
            await client.async_login()
            usage = await client.async_get_interval_usage(start, end)
    """

    def __init__(
        self,
        session: ClientSession,
        username: str,
        password: str,
        *,
        tfa_token: str | None = None,
        pilot_id: str = BIDGELY_PILOT_ID,
    ) -> None:
        """Initialize the client.

        Args:
            session: aiohttp session with ``create_sc_cookie_jar()``.
            username: SC portal username.
            password: SC portal password.
            tfa_token: Saved TFA token from ``SCAuthenticator``.
            pilot_id: Bidgely pilot ID override.
        """
        self._session = session
        self._username = username
        self._password = password
        self._tfa_token = tfa_token
        self._pilot_id = pilot_id
        self._sc_session: SCSession | None = None

    @property
    def account(self) -> SCAccount | None:
        """Return the account from the last login, if any."""
        return self._sc_session.account if self._sc_session else None

    async def async_login(self) -> SCSession:
        """Log in with the saved TFA token.

        Raises:
            InvalidCredentialsError: If the username or password is wrong.
            TFARequiredError: If the TFA token is missing or not accepted.
        """
        auth = SCAuthenticator(self._session, pilot_id=self._pilot_id)
        self._sc_session = await auth.async_login(
            self._username, self._password, self._tfa_token
        )
        return self._sc_session

    async def async_get_register_usage(
        self,
        start_date: date,
        end_date: date,
        meter_type: MeterType = MeterType.ELECTRIC,
    ) -> list[SCRegisterUsage]:
        """Return raw readings for each meter register.

        The result includes only readings with a local date from
        ``start_date`` to ``end_date`` (both included).
        """
        registers = await self._async_with_relogin(
            lambda: self._async_download(start_date, end_date, meter_type)
        )
        filtered = []
        for register in registers:
            reads = [
                read
                for read in register.reads
                if start_date <= read.start.date() <= end_date
            ]
            filtered.append(
                SCRegisterUsage(usage_point_id=register.usage_point_id, reads=reads)
            )
        return filtered

    async def async_get_interval_usage(
        self,
        start_date: date,
        end_date: date,
        meter_type: MeterType = MeterType.ELECTRIC,
    ) -> list[IntervalUsageData]:
        """Return interval usage from ``start_date`` to ``end_date``.

        Electric intervals are 15 minutes in kWh. Solar export is in
        ``generation``. Gas intervals are 1 hour in ft³.
        """
        registers = await self.async_get_register_usage(
            start_date, end_date, meter_type
        )
        return registers_to_intervals(registers, meter_type)

    async def async_get_bill_forecast(self) -> SCBillForecast:
        """Return the current bill forecast.

        The dollar values include electric and gas together.
        """
        return await self._async_with_relogin(self._async_fetch_bill_forecast)

    async def _async_with_relogin(self, func: Callable[[], Awaitable[_T]]) -> _T:
        """Run a request. Log in first if needed, and again once on 401/403."""
        if self._sc_session is None:
            await self.async_login()
        try:
            return await func()
        except InvalidAuthError:
            _LOGGER.debug("SC session expired, logging in again")
            await self.async_login()
            return await func()

    async def _async_download(
        self,
        start_date: date,
        end_date: date,
        meter_type: MeterType,
    ) -> list[SCRegisterUsage]:
        """Download and parse Green Button XML for a date range."""
        assert self._sc_session is not None

        # We do not know if Bidgely reads start/end as real epoch or as local
        # wall time. Ask for one extra day on each side. The caller filters.
        window_start = datetime.combine(
            start_date - timedelta(days=1), datetime.min.time(), SC_TIMEZONE
        )
        window_end = datetime.combine(
            end_date + timedelta(days=2), datetime.min.time(), SC_TIMEZONE
        )
        url = BIDGELY_URL + BIDGELY_GB_DOWNLOAD.format(
            user_id=self._sc_session.account.user_id
        )
        xml_text = await async_request_text(
            self._session,
            "GET",
            url,
            headers=bidgely_headers(
                self._sc_session.access_token, pilot_id=self._pilot_id
            ),
            params={
                "start": str(int(window_start.timestamp())),
                "end": str(int(window_end.timestamp())),
                "measurement-type": _METER_TYPE_TO_MEASUREMENT[meter_type],
                "file-type": "XML",
            },
        )
        return parse_registers(xml_text)

    async def _async_fetch_bill_forecast(self) -> SCBillForecast:
        """Fetch the bill forecast from the portal."""
        home_url = f"{SC_PORTAL_URL}{SC_HOME_PAGE}"
        home_page = await async_request_text(
            self._session,
            "GET",
            home_url,
            headers=page_headers(referer=f"{SC_PORTAL_URL}{SC_LOGIN_PAGE}"),
        )
        url = f"{SC_PORTAL_URL}{SC_AMI_USAGE_ALERTS}"
        payload = decode_json(
            await async_request_text(
                self._session,
                "GET",
                url,
                headers=ajax_headers(find_verification_token(home_page), home_url),
                params={"_": cache_buster()},
            ),
            url,
        )
        return parse_bill_forecast(payload)
