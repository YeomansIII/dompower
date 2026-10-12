"""Dominion Energy South Carolina backend.

SC accounts use a different portal (account.dominionenergysc.com) and a
different data vendor (Bidgely) from VA and NC accounts.
"""

from .auth import SCAuthenticator, create_sc_cookie_jar
from .client import DominionSCClient, parse_bill_forecast, registers_to_intervals
from .const import BIDGELY_PILOT_ID
from .greenbutton import parse_registers
from .models import (
    SCAccount,
    SCBillForecast,
    SCIntervalRead,
    SCRegisterUsage,
    SCSession,
)

__all__ = [
    "BIDGELY_PILOT_ID",
    "DominionSCClient",
    "SCAccount",
    "SCAuthenticator",
    "SCBillForecast",
    "SCIntervalRead",
    "SCRegisterUsage",
    "SCSession",
    "create_sc_cookie_jar",
    "parse_bill_forecast",
    "parse_registers",
    "registers_to_intervals",
]
