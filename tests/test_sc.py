"""Tests for the Dominion Energy South Carolina backend."""
# ruff: noqa: S105, S106, S107 - test tokens are not secrets

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from aiohttp import ClientSession
from aresponses import ResponsesMockServer

from dompower import (
    ApiError,
    DominionSCClient,
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
from dompower.sc.const import SC_TIMEZONE

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
