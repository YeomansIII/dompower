"""CLI commands for Dominion Energy South Carolina accounts.

These commands let SC users test the backend and send useful output in
bug reports:

    dompower sc-login --username your-username
    dompower sc-usage --days 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, timedelta
from getpass import getpass
from pathlib import Path
from typing import Any

import aiohttp

from ..exceptions import (
    DompowerError,
    InvalidCredentialsError,
    TFARequiredError,
    TFAVerificationError,
)
from ..models import MeterType, TFAProvider
from .auth import SCAuthenticator, create_sc_cookie_jar
from .client import DominionSCClient, registers_to_intervals
from .models import SCRegisterUsage
from .report import ISSUE_URL, async_build_report

DEFAULT_STATE_FILE = Path.home() / ".dompower" / "sc.json"
DEFAULT_REPORT_FILE = "dompower-sc-report.txt"
PASSWORD_ENV = "DOMPOWER_SC_PASSWORD"  # noqa: S105 - variable name, not a secret


def add_sc_parsers(subparsers: Any) -> None:
    """Add the SC subcommands to the main argument parser."""
    login_parser = subparsers.add_parser(
        "sc-login",
        help="South Carolina: log in with TFA and save the TFA token",
    )
    login_parser.add_argument(
        "-u", "--username", help="SC portal username (prompts if not provided)"
    )
    login_parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help=f"File for the username and TFA token (default: {DEFAULT_STATE_FILE})",
    )

    usage_parser = subparsers.add_parser(
        "sc-usage",
        help="South Carolina: show account, register, and bill data",
    )
    usage_parser.add_argument(
        "-u", "--username", help="SC portal username (default: from state file)"
    )
    usage_parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help=f"File from sc-login (default: {DEFAULT_STATE_FILE})",
    )
    usage_parser.add_argument(
        "--days", type=int, default=3, help="Number of past days (default: 3)"
    )
    usage_parser.add_argument(
        "--gas", action="store_true", help="Show gas data instead of electric"
    )
    usage_parser.add_argument("--json", action="store_true", help="Output as JSON")

    report_parser = subparsers.add_parser(
        "sc-report",
        help="South Carolina: write a redacted diagnostic report for a GitHub issue",
    )
    report_parser.add_argument(
        "-u", "--username", help="SC portal username (default: from state file)"
    )
    report_parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help=f"File from sc-login (default: {DEFAULT_STATE_FILE})",
    )
    report_parser.add_argument(
        "--days", type=int, default=3, help="Number of past days (default: 3)"
    )
    report_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path(DEFAULT_REPORT_FILE),
        help=f"Report file (default: {DEFAULT_REPORT_FILE})",
    )


def _load_state(path: Path) -> dict[str, str]:
    """Load the SC state file, or return an empty dict."""
    if not path.exists():
        return {}
    with path.open() as f:
        data: dict[str, str] = json.load(f)
    return data


def _save_state(path: Path, username: str, tfa_token: str) -> None:
    """Save the username and TFA token. The file is readable by the owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump({"username": username, "tfa_token": tfa_token}, f, indent=2)
    path.chmod(0o600)


def _password() -> str:
    """Return the password from the environment or a prompt."""
    return os.environ.get(PASSWORD_ENV) or getpass("Password: ")


def _register_summary(register: SCRegisterUsage) -> dict[str, Any]:
    """Return a short summary of one register for troubleshooting."""
    reads = register.reads
    return {
        "usage_point_id": register.usage_point_id,
        "reads": len(reads),
        "first": reads[0].start.isoformat() if reads else None,
        "last": reads[-1].start.isoformat() if reads else None,
        "interval_seconds": sorted({r.duration_seconds for r in reads}),
        "min_value": min((r.value for r in reads), default=None),
        "max_value": max((r.value for r in reads), default=None),
    }


async def _prompt(text: str) -> str:
    """Read one line from stdin without blocking the event loop."""
    return (await asyncio.to_thread(input, text)).strip()


async def cmd_sc_login(args: argparse.Namespace) -> int:
    """Log in to an SC account, do TFA, and save the TFA token."""
    username = args.username or await _prompt("Username: ")
    password = _password()
    if not username or not password:
        print("Error: Username and password are required", file=sys.stderr)
        return 1

    async with aiohttp.ClientSession(cookie_jar=create_sc_cookie_jar()) as session:
        auth = SCAuthenticator(session)
        saved = _load_state(args.state_file)
        saved_token = None
        if saved.get("username") == username:
            saved_token = saved.get("tfa_token")
        try:
            result = await auth.async_submit_credentials(
                username, password, saved_token
            )
            if result.tfa_required:
                targets = await auth.async_get_tfa_options()
                if not targets:
                    print("Error: No TFA options available", file=sys.stderr)
                    return 1
                print("Select where to send the verification code:")
                for i, target in enumerate(targets, 1):
                    kind = "SMS" if target.provider == TFAProvider.PHONE else "Email"
                    print(f"  {i}. {kind}: {target.obfuscated}")
                choice = await _prompt(f"Enter choice (1-{len(targets)}): ")
                try:
                    target = targets[int(choice) - 1]
                except (ValueError, IndexError):
                    print("Error: Not a valid choice", file=sys.stderr)
                    return 1
                await auth.async_send_tfa_code(target)
                await auth.async_verify_tfa_code(await _prompt("Enter code: "))
            sc_session = await auth.async_complete_login()
        except InvalidCredentialsError:
            print("Error: Invalid username or password", file=sys.stderr)
            return 1
        except TFAVerificationError:
            print("Error: The verification code is not correct", file=sys.stderr)
            return 1

    assert auth.tfa_token is not None
    _save_state(args.state_file, username, auth.tfa_token)
    account = sc_session.account
    print()
    print("Login successful.")
    print(f"  Service address: {account.service_address}")
    print(f"  Meter types:     {', '.join(t.value for t in account.meter_types)}")
    print(f"  TFA token saved: {args.state_file}")
    return 0


async def cmd_sc_usage(args: argparse.Namespace) -> int:
    """Show SC account, register, and bill data for the last days."""
    state = _load_state(args.state_file)
    username = args.username or state.get("username")
    if not username:
        print("Error: Run 'dompower sc-login' first", file=sys.stderr)
        return 1
    meter_type = MeterType.GAS if args.gas else MeterType.ELECTRIC
    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=max(args.days, 1) - 1)

    async with aiohttp.ClientSession(cookie_jar=create_sc_cookie_jar()) as session:
        client = DominionSCClient(
            session, username, _password(), tfa_token=state.get("tfa_token")
        )
        try:
            sc_session = await client.async_login()
            registers = await client.async_get_register_usage(
                start_date, end_date, meter_type
            )
            try:
                forecast: Any = await client.async_get_bill_forecast()
            except DompowerError as err:
                forecast = f"not available: {err}"
        except TFARequiredError:
            print("Error: TFA is required. Run 'dompower sc-login'", file=sys.stderr)
            return 1

    intervals = registers_to_intervals(registers, meter_type)
    daily: dict[str, dict[str, float]] = {}
    for interval in intervals:
        day = daily.setdefault(
            interval.timestamp.date().isoformat(),
            {"consumption": 0.0, "generation": 0.0},
        )
        day["consumption"] += interval.consumption
        day["generation"] += interval.generation

    output: dict[str, Any] = {
        "account": {
            "service_address": sc_session.account.service_address,
            "meter_types": [t.value for t in sc_session.account.meter_types],
        },
        "range": [start_date.isoformat(), end_date.isoformat()],
        "unit": intervals[0].unit if intervals else None,
        "registers": [_register_summary(register) for register in registers],
        "daily": {
            day: {k: round(v, 3) for k, v in totals.items()}
            for day, totals in daily.items()
        },
        "bill_forecast": (
            forecast
            if isinstance(forecast, str)
            else {
                "period_start": forecast.period_start.isoformat(),
                "period_end": forecast.period_end.isoformat(),
                "data_through": forecast.data_through.isoformat(),
                "cost_to_date": forecast.cost_to_date,
                "forecasted_cost": forecast.forecasted_cost,
                "last_year_cost": forecast.last_year_cost,
            }
        ),
    }

    if args.json:
        print(json.dumps(output, indent=2))
        return 0

    print(f"Service address: {output['account']['service_address']}")
    print(f"Meter types:     {', '.join(output['account']['meter_types'])}")
    print(f"Range:           {start_date} to {end_date} ({meter_type.value})")
    print()
    print("Registers (raw values; electric is Wh, gas is ft³):")
    for reg in output["registers"]:
        print(f"  {reg['usage_point_id'] or '(no id)'}")
        print(f"    reads: {reg['reads']}, interval: {reg['interval_seconds']} s")
        print(f"    first: {reg['first']}, last: {reg['last']}")
        print(f"    min: {reg['min_value']}, max: {reg['max_value']}")
    print()
    print(f"Daily totals ({output['unit']}):")
    for day, totals in output["daily"].items():
        print(
            f"  {day}: consumption {totals['consumption']:.3f}, "
            f"generation {totals['generation']:.3f}"
        )
    print()
    print(f"Bill forecast: {json.dumps(output['bill_forecast'], indent=2)}")
    return 0


async def cmd_sc_report(args: argparse.Namespace) -> int:
    """Write a redacted diagnostic report that users can attach to an issue."""
    state = _load_state(args.state_file)
    username = args.username or state.get("username") or await _prompt("Username: ")
    password = _password()
    if not username or not password:
        print("Error: Username and password are required", file=sys.stderr)
        return 1
    tfa_token = state.get("tfa_token") if state.get("username") == username else None

    print("Collecting data. This can take a minute...")
    async with aiohttp.ClientSession(cookie_jar=create_sc_cookie_jar()) as session:
        report = await async_build_report(
            session, username, password, tfa_token, days=args.days
        )

    args.output.write_text(report, encoding="utf-8")
    print()
    print(f"Report written to: {args.output.resolve()}")
    print("Open the file and read it before you share it. It is plain text.")
    print(f"Then attach it to {ISSUE_URL}")
    return 0
