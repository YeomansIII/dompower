"""Tests for the Dominion Energy South Carolina backend."""
# ruff: noqa: S105, S106, S107 - test tokens are not secrets

import json
import os
import stat
import sys
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from aiohttp import ClientSession
from aresponses import ResponsesMockServer

from dompower import (
    ApiError,
    DominionSCClient,
    IntervalUsageData,
    InvalidAuthError,
    InvalidCredentialsError,
    MeterType,
    SCAuthenticator,
    TFAProvider,
    TFARequiredError,
    TFAVerificationError,
    create_sc_cookie_jar,
)
from dompower.sc import parse_bill_forecast, parse_registers, registers_to_intervals
from dompower.sc.auth import find_verification_token
from dompower.sc.cli import _save_state
from dompower.sc.const import SC_TIMEZONE
from dompower.sc.report import (
    _ReportState,
    _summarize_registers,
    async_build_report,
    describe_error,
    json_shape,
    redact,
    summarize_green_button,
)

PORTAL = "account.dominionenergysc.com"
BIDGELY = "desc-prodapi.bidgely.com"
FIXTURE = Path(__file__).parent / "fixtures" / "sc_greenbutton_solar.xml"

LOGIN_HTML = (
    "<html><form>"
    '<input name="__RequestVerificationToken" type="hidden" value="login-token" />'
    "</form></html>"
)
HOME_HTML = (
    "<html><form>"
    '<input name="__RequestVerificationToken" type="hidden" value="home-token" />'
    "</form></html>"
)


def _json(aresponses: ResponsesMockServer, data: Any) -> Any:
    """Return a JSON response for aresponses."""
    return aresponses.Response(body=json.dumps(data), content_type="application/json")


def _add_login_start(aresponses: ResponsesMockServer, status: str = "twoFA") -> None:
    """Mock the login page and Authenticate calls."""
    aresponses.add(PORTAL, "/access/", "GET", aresponses.Response(body=LOGIN_HTML))
    aresponses.add(
        PORTAL,
        "/fusionapi/LoginWebApi/Authenticate/",
        "POST",
        _json(aresponses, {"data": {"status": status}}),
    )


def _add_login_finish(
    aresponses: ResponsesMockServer,
    *,
    single_account: bool = True,
    access_token: str = "bearer-1",
) -> None:
    """Mock the calls after TFA, up to the Bidgely bearer token."""
    aresponses.add(PORTAL, "/", "GET", aresponses.Response(body=HOME_HTML))
    aresponses.add(
        PORTAL,
        "/fusionapi/AccountManagementWebApi/GetAccountListing/",
        "GET",
        _json(aresponses, {"data": {"singleAccount": single_account}}),
    )
    if not single_account:
        return
    aresponses.add(
        PORTAL,
        "/fusionapi/AccountSummaryWebApi/InitAccount/",
        "GET",
        _json(
            aresponses,
            {
                "data": {
                    "account": {
                        "serviceAddressAndAccountNo": "1 MAIN ST (*-****-****0-4464)"
                    }
                }
            },
        ),
    )
    aresponses.add(
        PORTAL,
        "/fusionapi/BidgelyWebApi/GetBidgelySDKInit/",
        "GET",
        _json(aresponses, {"data": {"payload": "encrypted"}}),
    )
    aresponses.add(
        BIDGELY,
        "/v2.0/web/wc-session",
        "POST",
        _json(
            aresponses,
            {
                "payload": {
                    "tokenDetails": {"accessToken": access_token},
                    "userProfileDetails": {"userId": "user-123"},
                    "userTypeDetails": {
                        "measurementToUserTypeMappings": [
                            {"measurementType": "ELECTRIC"},
                            {"measurementType": "GAS"},
                            {"measurementType": "WATER"},
                        ]
                    },
                }
            },
        ),
    )


def _add_full_login(aresponses: ResponsesMockServer, access_token: str) -> None:
    """Mock a complete login with an accepted saved TFA token."""
    _add_login_start(aresponses)
    aresponses.add(
        PORTAL,
        "/fusionapi/LoginWebApi/Verify2FAToken/",
        "GET",
        _json(aresponses, {"data": True}),
    )
    _add_login_finish(aresponses, access_token=access_token)


def _entry(
    title: str,
    readings: list[tuple[int | str, int | str]] | None = None,
    usage_point: str = "UP1",
) -> str:
    """Return one Atom entry. Readings are (start, value) with 900 s intervals."""
    reads = "".join(
        "<espi:IntervalReading><espi:timePeriod>"
        f"<espi:duration>900</espi:duration><espi:start>{start}</espi:start>"
        f"</espi:timePeriod><espi:value>{value}</espi:value></espi:IntervalReading>"
        for start, value in readings or []
    )
    content = f"<espi:IntervalBlock>{reads}</espi:IntervalBlock>" if reads else ""
    href = f"https://naapi2.bidgely.com/UsagePoint/{usage_point}/IntervalBlock/1"
    return (
        f'<entry><link href="{href}" rel="self"/><title>{title}</title>'
        f"<content>{content}</content></entry>"
    )


def _green_button_xml(entries: list[str]) -> str:
    """Return a Green Button feed with the entries."""
    return (
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi">'
        f"{''.join(entries)}</feed>"
    )


class TestGreenButtonParser:
    """Tests for the Green Button XML parser."""

    def test_groups_readings_by_register(self) -> None:
        """Readings for each UsagePoint go into one register."""
        registers = parse_registers(FIXTURE.read_text())

        assert [r.usage_point_id for r in registers] == ["UP_GRID", "UP_SOLAR"]
        assert [read.value for read in registers[0].reads] == [100, 209, 328, 412]
        assert [read.value for read in registers[1].reads] == [0, -128]

    def test_epoch_is_local_wall_time(self) -> None:
        """Bidgely epochs are local wall time encoded as UTC."""
        registers = parse_registers(FIXTURE.read_text())
        first = registers[0].reads[1]

        assert first.start == datetime(2026, 8, 13, 0, 0, tzinfo=SC_TIMEZONE)
        assert first.start.utcoffset() is not None
        assert first.duration_seconds == 900

    def test_feed_without_intervals(self) -> None:
        """A feed with no interval entries gives no registers."""
        xml = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
        assert parse_registers(xml) == []

    def test_bad_xml_raises_api_error(self) -> None:
        """XML that cannot be parsed raises ApiError."""
        with pytest.raises(ApiError):
            parse_registers("<feed><not-closed>")


class TestRegistersToIntervals:
    """Tests for merging registers into intervals."""

    def test_splits_consumption_and_generation(self) -> None:
        """Positive values are consumption and negative values are generation."""
        intervals = registers_to_intervals(parse_registers(FIXTURE.read_text()))

        by_time = {i.timestamp: i for i in intervals}
        slot = by_time[datetime(2026, 8, 13, 0, 15, tzinfo=SC_TIMEZONE)]
        assert slot.consumption == pytest.approx(0.328)
        assert slot.generation == pytest.approx(0.128)
        assert slot.unit == "kWh"
        assert [i.timestamp for i in intervals] == sorted(by_time)

    def test_gas_is_not_converted(self) -> None:
        """Gas values stay in ft³."""
        intervals = registers_to_intervals(
            parse_registers(FIXTURE.read_text()), MeterType.GAS
        )
        assert intervals[0].consumption == 100
        assert intervals[0].unit == "ft³"


# Wall-time epochs (local midnight encoded as UTC) for the 2026 DST days.
FALL_BACK_MIDNIGHT = int(datetime(2026, 11, 1, tzinfo=UTC).timestamp())
SPRING_FORWARD_MIDNIGHT = int(datetime(2026, 3, 8, tzinfo=UTC).timestamp())


def _fall_back_epochs() -> list[int]:
    """Return the 100 wall-time epochs of 2026-11-01, in reading order.

    The 01:00-01:45 wall times show two times: first EDT, then EST.
    """
    quarters = [*range(8), *range(4, 96)]
    return [FALL_BACK_MIDNIGHT + q * 900 for q in quarters]


def _hourly_by_utc(
    intervals: list[IntervalUsageData],
) -> dict[datetime, list[IntervalUsageData]]:
    """Group intervals by the UTC hour."""
    hours: dict[datetime, list[IntervalUsageData]] = {}
    for interval in intervals:
        hour = interval.timestamp.astimezone(UTC).replace(minute=0)
        hours.setdefault(hour, []).append(interval)
    return hours


class TestDaylightSavingTime:
    """Tests for the DST change days. Bidgely epochs are wall time."""

    def test_fall_back_day_has_25_hours(self) -> None:
        """The repeated 01:00 hour is EST, and no hour is doubled."""
        readings = list(zip(_fall_back_epochs(), range(1, 101), strict=True))
        xml = _green_button_xml([_entry("Interval Consumption", readings)])
        registers = parse_registers(xml)
        reads = registers[0].reads
        assert len(reads) == 100
        assert len({r.start.astimezone(UTC) for r in reads}) == 100

        intervals = registers_to_intervals(registers)
        assert len(intervals) == 100
        utc_times = [i.timestamp.astimezone(UTC) for i in intervals]
        assert utc_times == sorted(set(utc_times))
        assert {i.timestamp.date() for i in intervals} == {date(2026, 11, 1)}

        hours = _hourly_by_utc(intervals)
        assert len(hours) == 25
        for n, (hour, slots) in enumerate(sorted(hours.items())):
            # Local midnight (EDT) is 04:00 UTC.
            assert hour == datetime(2026, 11, 1, 4, tzinfo=UTC) + timedelta(hours=n)
            assert len(slots) == 4
            # Reading values are 1..100 Wh in reading order.
            expected_wh = sum(range(4 * n + 1, 4 * n + 5))
            consumption = sum(s.consumption for s in slots)
            assert consumption == pytest.approx(expected_wh / 1000)

        first, second = (
            sorted(hours.items())[1][1][0].timestamp,
            sorted(hours.items())[2][1][0].timestamp,
        )
        assert (first.hour, first.fold, first.tzname()) == (1, 0, "EDT")
        assert (second.hour, second.fold, second.tzname()) == (1, 1, "EST")

    def test_fall_back_two_registers(self) -> None:
        """Grid and solar readings of the same repeated hour stay together."""
        epochs = _fall_back_epochs()
        xml = _green_button_xml(
            [
                _entry(
                    "Interval Consumption",
                    [(e, 100) for e in epochs],
                    usage_point="UP_GRID",
                ),
                _entry(
                    "Interval Consumption",
                    [(e, -10 if i < 8 else -20) for i, e in enumerate(epochs)],
                    usage_point="UP_SOLAR",
                ),
            ]
        )
        intervals = registers_to_intervals(parse_registers(xml))

        assert len(intervals) == 100
        assert all(i.consumption == pytest.approx(0.1) for i in intervals)
        hours = sorted(_hourly_by_utc(intervals).items())
        assert len(hours) == 25
        generation = [round(sum(s.generation for s in slots), 3) for _, slots in hours]
        assert generation == [0.04, 0.04] + [0.08] * 23

    def test_fall_back_report_hours(self) -> None:
        """The report shows the two 01:00 hours with their time zone names."""
        xml = _green_button_xml(
            [_entry("Interval Consumption", [(e, 250) for e in _fall_back_epochs()])]
        )
        lines = _summarize_registers(
            parse_registers(xml), MeterType.ELECTRIC, _ReportState()
        )

        assert "    readings: 100" in lines
        assert "  2026-11-01: consumption 25.000, generation 0.000" in lines
        hourly = lines[lines.index("Hourly totals for 2026-11-01 (kWh):") + 1 :]
        assert len(hourly) == 25
        assert hourly[1:3] == [
            "  01:00 EDT consumption 1.000, generation 0.000",
            "  01:00 EST consumption 1.000, generation 0.000",
        ]

    def test_spring_forward_day_has_23_hours(self) -> None:
        """Without 02:00-02:59 readings, the day has 92 readings in 23 hours."""
        quarters = [*range(8), *range(12, 96)]
        epochs = [SPRING_FORWARD_MIDNIGHT + q * 900 for q in quarters]
        xml = _green_button_xml(
            [_entry("Interval Consumption", [(e, 100) for e in epochs])]
        )
        intervals = registers_to_intervals(parse_registers(xml))

        assert len(intervals) == 92
        hours = _hourly_by_utc(intervals)
        assert len(hours) == 23
        assert all(len(slots) == 4 for slots in hours.values())
        assert intervals[8].timestamp == datetime(2026, 3, 8, 3, tzinfo=SC_TIMEZONE)
        assert intervals[8].timestamp.tzname() == "EDT"

    def test_spring_forward_gap_readings(self) -> None:
        """Readings at 02:00-02:59 move to 03:00 EDT. The day total stays."""
        epochs = [SPRING_FORWARD_MIDNIGHT + q * 900 for q in range(96)]
        # A repeated gap time must not become fold=1 (01:xx EST).
        epochs.append(SPRING_FORWARD_MIDNIGHT + 9 * 900)
        xml = _green_button_xml(
            [_entry("Interval Consumption", [(e, 100) for e in epochs])]
        )
        registers = parse_registers(xml)
        for read in registers[0].reads:
            # Every start is a real local time.
            assert read.start.astimezone(UTC).astimezone(SC_TIMEZONE) == read.start
            assert read.start.hour != 2

        intervals = registers_to_intervals(registers)
        assert sum(i.consumption for i in intervals) == pytest.approx(9.7)
        hours = sorted(_hourly_by_utc(intervals).items())
        assert len(hours) == 23
        assert [len(slots) for _, slots in hours][:3] == [4, 4, 4]
        three_am = [s for _, slots in hours for s in slots if s.timestamp.hour == 3]
        assert sum(s.consumption for s in three_am) == pytest.approx(0.9)


class TestBillForecast:
    """Tests for the bill forecast parser."""

    def test_parse(self) -> None:
        """All fields are read from amiUsageAlert."""
        forecast = parse_bill_forecast(
            {
                "data": {
                    "amiUsageAlert": {
                        "currentBillUsageStartDate": "2026-08-01T00:00:00",
                        "currentBillUsageEndDate": "2026-08-31T00:00:00",
                        "currentBillThroughDate": "2026-08-14T00:00:00",
                        "totalCostUnbilledConsumption": 61.25,
                        "currentCostPerDay": 4.5,
                        "numberOfDaysInCurrentBill": 31,
                        "lastYearAmountExists": True,
                        "lastYearTotalAmount": 150.1,
                    }
                }
            }
        )
        assert forecast.period_start == date(2026, 8, 1)
        assert forecast.period_end == date(2026, 8, 31)
        assert forecast.data_through == date(2026, 8, 14)
        assert forecast.cost_to_date == 61.25
        assert forecast.forecasted_cost == 139.5
        assert forecast.last_year_cost == 150.1

    def test_missing_fields(self) -> None:
        """A response without amiUsageAlert raises ApiError."""
        with pytest.raises(ApiError):
            parse_bill_forecast({"data": {}})


class TestVerificationToken:
    """Tests for the verification token scraper."""

    def test_found(self) -> None:
        """The token value is returned."""
        assert find_verification_token(LOGIN_HTML) == "login-token"

    def test_missing(self) -> None:
        """A page without a token raises ApiError."""
        with pytest.raises(ApiError):
            find_verification_token("<html></html>")


class TestSCAuthenticator:
    """Tests for the SC login flow."""

    async def test_saved_token_skips_tfa(self, aresponses: ResponsesMockServer) -> None:
        """A valid saved TFA token gives a complete login."""
        _add_full_login(aresponses, "bearer-1")

        async with ClientSession(cookie_jar=create_sc_cookie_jar()) as session:
            auth = SCAuthenticator(session)
            sc_session = await auth.async_login("user", "pass", "saved-token")

        assert sc_session.access_token == "bearer-1"
        assert sc_session.account.user_id == "user-123"
        assert sc_session.account.service_address == "1 MAIN ST (*-****-****0-4464)"
        assert sc_session.account.meter_types == [MeterType.ELECTRIC, MeterType.GAS]
        assert auth.tfa_token == "saved-token"

    async def test_bad_password(self, aresponses: ResponsesMockServer) -> None:
        """Status "failed" raises InvalidCredentialsError."""
        _add_login_start(aresponses, status="failed")

        async with ClientSession() as session:
            with pytest.raises(InvalidCredentialsError):
                await SCAuthenticator(session).async_submit_credentials("u", "p")

    async def test_unknown_status(self, aresponses: ResponsesMockServer) -> None:
        """An unknown login status raises InvalidAuthError."""
        _add_login_start(aresponses, status="locked")

        async with ClientSession() as session:
            with pytest.raises(InvalidAuthError):
                await SCAuthenticator(session).async_submit_credentials("u", "p")

    async def test_rejected_token_requires_tfa(
        self, aresponses: ResponsesMockServer
    ) -> None:
        """A rejected saved token makes async_login raise TFARequiredError."""
        _add_login_start(aresponses)
        aresponses.add(
            PORTAL,
            "/fusionapi/LoginWebApi/Verify2FAToken/",
            "GET",
            _json(aresponses, {"data": False}),
        )

        async with ClientSession() as session:
            with pytest.raises(TFARequiredError):
                await SCAuthenticator(session).async_login("u", "p", "old-token")

    async def test_interactive_tfa(self, aresponses: ResponsesMockServer) -> None:
        """Options, send, and verify give a new TFA token and a session."""
        _add_login_start(aresponses)
        aresponses.add(
            PORTAL,
            "/fusionapi/LoginWebApi/InitAuthentication/",
            "GET",
            _json(
                aresponses,
                {
                    "data": {
                        "userInfo": {
                            "phoneNumbers": ["***-***-1234"],
                            "emailAddresses": ["j***@example.com"],
                        }
                    }
                },
            ),
        )
        sent: dict[str, Any] = {}

        async def send_pin(request: Any) -> Any:
            sent.update(await request.json())
            return _json(aresponses, {"data": True})

        aresponses.add(PORTAL, "/fusionapi/LoginWebApi/SendPINCode/", "POST", send_pin)
        aresponses.add(
            PORTAL,
            "/fusionapi/LoginWebApi/VerifyPIN/",
            "POST",
            _json(aresponses, {"data": {"isVerified": True, "token": "new-token"}}),
        )
        _add_login_finish(aresponses)

        async with ClientSession() as session:
            auth = SCAuthenticator(session)
            result = await auth.async_submit_credentials("u", "p")
            assert result.tfa_required

            targets = await auth.async_get_tfa_options()
            assert [t.provider for t in targets] == [
                TFAProvider.PHONE,
                TFAProvider.EMAIL,
            ]
            await auth.async_send_tfa_code(targets[1])
            assert await auth.async_verify_tfa_code("123456") == "new-token"
            sc_session = await auth.async_complete_login()

        assert sent["sendMethod"] == "j***@example.com"
        assert sc_session.access_token == "bearer-1"

    async def test_wrong_tfa_code(self, aresponses: ResponsesMockServer) -> None:
        """isVerified false raises TFAVerificationError."""
        _add_login_start(aresponses)
        aresponses.add(
            PORTAL,
            "/fusionapi/LoginWebApi/VerifyPIN/",
            "POST",
            _json(aresponses, {"data": {"isVerified": False}}),
        )

        async with ClientSession() as session:
            auth = SCAuthenticator(session)
            await auth.async_submit_credentials("u", "p")
            with pytest.raises(TFAVerificationError):
                await auth.async_verify_tfa_code("000000")

    async def test_complete_before_tfa(self) -> None:
        """async_complete_login before TFA raises InvalidAuthError."""
        async with ClientSession() as session:
            with pytest.raises(InvalidAuthError):
                await SCAuthenticator(session).async_complete_login()

    async def test_multiple_accounts(self, aresponses: ResponsesMockServer) -> None:
        """A login with more than one account raises ApiError."""
        _add_login_start(aresponses)
        aresponses.add(
            PORTAL,
            "/fusionapi/LoginWebApi/Verify2FAToken/",
            "GET",
            _json(aresponses, {"data": True}),
        )
        _add_login_finish(aresponses, single_account=False)

        async with ClientSession() as session:
            with pytest.raises(ApiError, match="more than one account"):
                await SCAuthenticator(session).async_login("u", "p", "token")


class TestDominionSCClient:
    """Tests for the SC client."""

    async def test_interval_usage_filters_dates(
        self, aresponses: ResponsesMockServer
    ) -> None:
        """Only readings in the requested local dates are returned."""
        _add_full_login(aresponses, "bearer-1")
        seen: dict[str, str] = {}

        async def gb_download(request: Any) -> Any:
            seen.update(request.query)
            seen["auth"] = request.headers["Authorization"]
            seen["pilot"] = request.headers["X-Bidgely-Pilot-Id"]
            return aresponses.Response(body=FIXTURE.read_text())

        aresponses.add(
            BIDGELY, "/v2.0/dashboard/users/user-123/gb-download", "GET", gb_download
        )

        async with ClientSession() as session:
            client = DominionSCClient(session, "u", "p", tfa_token="t")
            intervals = await client.async_get_interval_usage(
                date(2026, 8, 13), date(2026, 8, 13)
            )

        assert [i.timestamp.date() for i in intervals] == [date(2026, 8, 13)] * 2
        assert sum(i.consumption for i in intervals) == pytest.approx(0.537)
        assert sum(i.generation for i in intervals) == pytest.approx(0.128)
        assert seen["measurement-type"] == "ELECTRIC"
        assert seen["auth"] == "Bearer bearer-1"
        assert seen["pilot"] == "10078"
        # One extra day on each side of the requested range
        start = datetime.fromtimestamp(int(seen["start"]), SC_TIMEZONE)
        end = datetime.fromtimestamp(int(seen["end"]), SC_TIMEZONE)
        assert start == datetime(2026, 8, 12, tzinfo=SC_TIMEZONE)
        assert end == datetime(2026, 8, 15, tzinfo=SC_TIMEZONE)

    async def test_relogin_on_expired_session(
        self, aresponses: ResponsesMockServer
    ) -> None:
        """A 401 from Bidgely causes one new login and a retry."""
        path = "/v2.0/dashboard/users/user-123/gb-download"
        _add_full_login(aresponses, "bearer-1")
        aresponses.add(BIDGELY, path, "GET", aresponses.Response(status=401))
        _add_full_login(aresponses, "bearer-2")

        async def gb_download(request: Any) -> Any:
            assert request.headers["Authorization"] == "Bearer bearer-2"
            return aresponses.Response(body=FIXTURE.read_text())

        aresponses.add(BIDGELY, path, "GET", gb_download)

        async with ClientSession() as session:
            client = DominionSCClient(session, "u", "p", tfa_token="t")
            registers = await client.async_get_register_usage(
                date(2026, 8, 13), date(2026, 8, 14)
            )

        assert len(registers) == 2
        assert client.account is not None
        aresponses.assert_plan_strictly_followed()

    async def test_bill_forecast(self, aresponses: ResponsesMockServer) -> None:
        """The bill forecast uses a new verification token from the home page."""
        _add_full_login(aresponses, "bearer-1")
        aresponses.add(PORTAL, "/", "GET", aresponses.Response(body=HOME_HTML))

        async def alerts(request: Any) -> Any:
            assert request.headers["__RequestVerificationToken"] == "home-token"
            return _json(
                aresponses,
                {
                    "data": {
                        "amiUsageAlert": {
                            "currentBillUsageStartDate": "2026-08-01T00:00:00",
                            "currentBillUsageEndDate": "2026-08-31T00:00:00",
                            "currentBillThroughDate": "2026-08-14T00:00:00",
                            "totalCostUnbilledConsumption": 10,
                            "currentCostPerDay": 1,
                            "numberOfDaysInCurrentBill": 31,
                            "lastYearAmountExists": False,
                        }
                    }
                },
            )

        aresponses.add(
            PORTAL,
            "/fusionapi/CommonWebApi/GetAccountAMIUsageAlerts/",
            "GET",
            alerts,
        )

        async with ClientSession() as session:
            client = DominionSCClient(session, "u", "p", tfa_token="t")
            forecast = await client.async_get_bill_forecast()

        assert forecast.forecasted_cost == 31
        assert forecast.last_year_cost is None


ALERTS = {
    "data": {
        "amiUsageAlert": {
            "currentBillUsageStartDate": "2026-08-01T00:00:00",
            "currentBillUsageEndDate": "2026-08-31T00:00:00",
            "currentBillThroughDate": "2026-08-14T00:00:00",
            "totalCostUnbilledConsumption": 42.5,
            "currentCostPerDay": 3,
            "numberOfDaysInCurrentBill": 31,
            "lastYearAmountExists": False,
        }
    }
}
SECRETS = {
    "username": "Tester@Example.com",
    "password": "hunter2-secret",
    "tfa_token": "tfa-token-abc",
}


class TestSCReport:
    """Tests for the redacted diagnostic report."""

    async def test_full_report_has_no_secrets(
        self, aresponses: ResponsesMockServer
    ) -> None:
        """A complete report has the useful data and none of the secrets."""
        _add_full_login(aresponses, "bearer-secret-1")
        path = "/v2.0/dashboard/users/user-123/gb-download"
        for _ in range(2):  # electric and gas
            aresponses.add(
                BIDGELY, path, "GET", aresponses.Response(body=FIXTURE.read_text())
            )
        aresponses.add(PORTAL, "/", "GET", aresponses.Response(body=HOME_HTML))
        aresponses.add(
            PORTAL,
            "/fusionapi/CommonWebApi/GetAccountAMIUsageAlerts/",
            "GET",
            _json(aresponses, ALERTS),
        )

        async with ClientSession() as session:
            report = await async_build_report(
                session,
                SECRETS["username"],
                SECRETS["password"],
                SECRETS["tfa_token"],
                today=date(2026, 8, 15),
            )

        lowered = report.lower()
        for secret in [
            *SECRETS.values(),
            "user-123",
            "1 MAIN ST",
            "bearer-secret-1",
            "login-token",
            "home-token",
            "encrypted",
            "UP_GRID",
            "UP_SOLAR",
        ]:
            assert secret.lower() not in lowered, secret
        assert "Saved TFA token accepted: yes" in report
        assert "Meter types: electric, gas" in report
        assert '"status": "twoFA"' in report
        assert "token=<redacted>" in report
        assert "/users/<user-id>/gb-download" in report
        assert "verification token found" in report
        assert "register-1:" in report
        assert "register-2:" in report
        assert "dstOffset: 3600" in report
        assert "flowDirection: 1" in report
        assert "wall-time reading:" in report
        assert "2026-08-13: consumption 0.537, generation 0.128" in report
        assert "cost to date: $42.50" in report
        assert report.rstrip().endswith("(none)")

    async def test_report_keeps_login_errors(
        self, aresponses: ResponsesMockServer
    ) -> None:
        """A failed login still gives a report with the error and responses."""
        _add_login_start(aresponses, status="mystery")

        async with ClientSession() as session:
            report = await async_build_report(
                session, "user@example.com", "password1", None, today=date(2026, 8, 15)
            )

        assert "Login complete: no" in report
        # The error masks the quoted value. The allowlisted "status" field
        # in the response section still shows it.
        assert (
            "InvalidAuthError: Unexpected SC login status: <string, 7 chars>" in report
        )
        assert '"status": "mystery"' in report
        assert "password1" not in report

    async def test_report_rejected_tfa_token(
        self, aresponses: ResponsesMockServer
    ) -> None:
        """A rejected TFA token tells the user to run sc-login."""
        _add_login_start(aresponses)
        aresponses.add(
            PORTAL,
            "/fusionapi/LoginWebApi/Verify2FAToken/",
            "GET",
            _json(aresponses, {"data": False}),
        )

        async with ClientSession() as session:
            report = await async_build_report(
                session, "user@example.com", "password1", "old-token"
            )

        assert "Saved TFA token accepted: no (run 'dompower sc-login')" in report
        assert "old-token" not in report

    async def test_report_masks_values_in_errors(
        self, aresponses: ResponsesMockServer
    ) -> None:
        """A parse error does not show the text value from the XML."""
        _add_full_login(aresponses, "bearer-1")
        bad_xml = _green_button_xml(
            [_entry("Interval Consumption", [(1786579200, "12 Oak Lane")])]
        )
        path = "/v2.0/dashboard/users/user-123/gb-download"
        for _ in range(2):  # electric and gas
            aresponses.add(BIDGELY, path, "GET", aresponses.Response(body=bad_xml))
        aresponses.add(PORTAL, "/", "GET", aresponses.Response(body=HOME_HTML))
        aresponses.add(
            PORTAL,
            "/fusionapi/CommonWebApi/GetAccountAMIUsageAlerts/",
            "GET",
            _json(aresponses, ALERTS),
        )

        async with ClientSession() as session:
            report = await async_build_report(
                session, "u", "p", "t", today=date(2026, 8, 15)
            )

        assert "Oak Lane" not in report
        assert (
            "electric data: ApiError: Unexpected value in Green Button XML: "
            "could not convert string to float: <string, 11 chars>"
        ) in report

    def test_entry_titles_allowlist(self) -> None:
        """Only generic entry titles are shown. Other titles show the length."""
        xml = _green_button_xml(
            [
                _entry("Green Button Data File"),
                _entry("Time Configuration"),
                _entry("Type of Meter Reading Data"),
                _entry("Interval Consumption. Start: 2026-08-13 00:00:00"),
                _entry("Interval Consumption. Start: 2026-08-14 00:00:00"),
                _entry("12 Oak Lane, Columbia SC"),
                _entry("Meter AB12345: Electric"),
                _entry(""),
            ]
        )
        lines = summarize_green_button(xml, _ReportState())
        text = "\n".join(lines)

        start = lines.index(
            "Entry titles (text before the first '.' or ':', generic only):"
        )
        assert lines[start + 1 : start + 9] == [
            "  (no title): 1",
            "  <other title, 23 chars>: 1",
            "  <other title, 24 chars>: 1",
            "  Green Button Data File: 1",
            "  Interval Consumption: 2",
            "  Time Configuration: 1",
            "  Type of Meter Reading Data: 1",
            "Other content (first entry of each kind):",
        ]
        assert "Oak" not in text
        assert "AB12345" not in text

    def test_raw_reads_hide_text_values(self) -> None:
        """Raw readings show only numbers. Other text shows the length."""
        xml = _green_button_xml(
            [
                _entry(
                    "Interval Consumption",
                    [(1786579200, "209"), ("Oak Lane", "Mr Smith")],
                )
            ]
        )
        text = "\n".join(summarize_green_button(xml, _ReportState()))

        assert "start=1786579200 duration=900 value=209 |" in text
        assert "start=<string, 8 chars> duration=900 value=<string, 8 chars>" in text
        assert "Oak" not in text
        assert "Smith" not in text

    def test_describe_error(self) -> None:
        """Quoted values show only if they are numbers or dates."""
        err = ApiError(
            "Bad value 'Jane Doe', \"12 Oak Lane\", '209', '2026-08-13T00:00:00'"
        )
        assert describe_error(err) == (
            "ApiError: Bad value <string, 8 chars>, <string, 11 chars>, "
            "'209', '2026-08-13T00:00:00'"
        )

    def test_redact(self) -> None:
        """Secrets, email addresses, and phone numbers are replaced."""
        text = (
            "User MySecret logged in as a.b+c@mail.example.org, phone (803) 555-1234 "
            "or 803-555-1234. Epoch 1786579200 on 2026-08-13 12:00, value 209."
        )
        result = redact(text, ["mysecret", None, "abc"])
        assert result == (
            "User <redacted> logged in as <email>, phone <phone> "
            "or <phone>. Epoch 1786579200 on 2026-08-13 12:00, value 209."
        )

    def test_json_shape(self) -> None:
        """Only field names, booleans, dates, and allowlisted keys are shown."""
        shape = json_shape(
            {
                "status": "twoFA",
                "name": "Jane",
                "amount": 12.5,
                "ok": True,
                "date": "2026-08-01T00:00:00",
                "1234567": {"phoneNumbers": ["803-555-1234"]},
                "items": list(range(7)),
            }
        )
        assert shape == {
            "status": "twoFA",
            "name": "<string, 4 chars>",
            "amount": "<number>",
            "ok": True,
            "date": "2026-08-01T00:00:00",
            "<key 6>": {"phoneNumbers": ["<string, 12 chars>"]},
            "items": ["<number>"] * 5 + ["<2 more items>"],
        }


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes only")
class TestSCStateFile:
    """Tests for the file that stores the TFA token."""

    @pytest.fixture(autouse=True)
    def _open_umask(self) -> Iterator[None]:
        """Use umask 0, so a default file mode would be readable by all."""
        old = os.umask(0)
        yield
        os.umask(old)

    def test_new_file_is_owner_only(self, tmp_path: Path) -> None:
        """A new state file is 0600 and a new directory is 0700."""
        path = tmp_path / "new-dir" / "sc.json"
        opened: list[int] = []
        real_open = os.open

        def spy_open(file: Any, flags: int, mode: int = 0o777) -> int:
            opened.append(mode)
            return real_open(file, flags, mode)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(os, "open", spy_open)
            _save_state(path, "user", "tfa-token")

        assert opened == [0o600]
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert json.loads(path.read_text()) == {
            "username": "user",
            "tfa_token": "tfa-token",
        }

    def test_existing_file_is_made_owner_only(self, tmp_path: Path) -> None:
        """An existing state file becomes 0600 before the token is written."""
        path = tmp_path / "sc.json"
        path.write_text('{"username": "old", "tfa_token": "old-token-with-more"}')
        path.chmod(0o644)
        modes: list[int] = []
        real_fdopen = os.fdopen

        def spy_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
            modes.append(stat.S_IMODE(os.fstat(fd).st_mode))
            return real_fdopen(fd, *args, **kwargs)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(os, "fdopen", spy_fdopen)
            _save_state(path, "user", "tfa-token")

        assert modes == [0o600]
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert json.loads(path.read_text())["tfa_token"] == "tfa-token"

    def test_existing_directory_mode_is_kept(self, tmp_path: Path) -> None:
        """The mode of a directory that exists does not change."""
        tmp_path.chmod(0o755)
        _save_state(tmp_path / "sc.json", "user", "tfa-token")
        assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o755
