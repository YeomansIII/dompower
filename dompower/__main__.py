"""CLI entry point for dompower."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import aiohttp

from . import (
    LOGIN_URL,
    DompowerClient,
    DompowerError,
    GigyaAuthenticator,
    InvalidCredentialsError,
    TFAExpiredError,
    TFATarget,
    TFAVerificationError,
    TokenExpiredError,
    __version__,
)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="dompower",
        description="CLI for Dominion Energy API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Get usage data for the last 7 days
  dompower --token-file tokens.json usage -a 123456 -m 789 --days 7

  # Save raw Excel file
  dompower --token-file tokens.json usage -a 123456 -m 789 --raw -o usage.xlsx

  # Refresh tokens manually
  dompower --token-file tokens.json refresh
""",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"dompower {__version__}",
    )

    parser.add_argument(
        "--token-file",
        "-t",
        type=Path,
        help="JSON file containing access_token and refresh_token",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (use -vv for debug)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Usage command
    usage_parser = subparsers.add_parser("usage", help="Get usage data")
    usage_parser.add_argument(
        "-a",
        "--account",
        help="Account number (uses selected account if not provided)",
    )
    usage_parser.add_argument(
        "-m",
        "--meter",
        help="Meter number (uses selected meter if not provided)",
    )
    usage_parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days of data to retrieve (default: 7)",
    )
    usage_parser.add_argument(
        "--start-date",
        type=date.fromisoformat,
        help="Start date (YYYY-MM-DD), overrides --days",
    )
    usage_parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        help="End date (YYYY-MM-DD), defaults to today",
    )
    usage_parser.add_argument(
        "--raw",
        action="store_true",
        help="Output raw Excel file instead of parsed data",
    )
    usage_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output file path (for --raw mode)",
    )
    usage_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    # Refresh command
    subparsers.add_parser("refresh", help="Refresh authentication tokens")

    # Auth info command
    subparsers.add_parser("auth-info", help="Show authentication URL for initial setup")

    # Auth helper command (interactive)
    auth_helper_parser = subparsers.add_parser(
        "auth-helper", help="Interactive helper to extract and save tokens"
    )
    auth_helper_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("tokens.json"),
        help="Output file for tokens (default: tokens.json)",
    )
    auth_helper_parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Automatically open login URL in browser",
    )

    # Login command (new - with TFA support)
    login_parser = subparsers.add_parser(
        "login", help="Login with username and password (supports TFA)"
    )
    login_parser.add_argument(
        "-u",
        "--username",
        help="Dominion Energy email (prompts if not provided)",
    )
    login_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("tokens.json"),
        help="Output file for tokens (default: tokens.json)",
    )
    login_parser.add_argument(
        "--cookies",
        type=Path,
        help="Cookie file for TFA bypass (default: ~/.dompower/cookies.json)",
    )
    login_parser.add_argument(
        "--tfa-provider",
        choices=["phone", "email"],
        help="Preferred TFA method",
    )

    # Accounts command
    accounts_parser = subparsers.add_parser(
        "accounts", help="List accounts and meters for the authenticated user"
    )
    accounts_parser.add_argument(
        "--all",
        "-a",
        action="store_true",
        dest="include_inactive",
        help="Include inactive/closed accounts",
    )
    accounts_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    # Select account command
    select_parser = subparsers.add_parser(
        "select-account",
        help="Select default account/meter for usage and bill commands",
    )
    select_parser.add_argument(
        "-a",
        "--account",
        help="Account number to select (interactive if not provided)",
    )
    select_parser.add_argument(
        "-m",
        "--meter",
        help="Meter number to select (uses primary meter if not provided)",
    )

    # Bill forecast command
    bill_parser = subparsers.add_parser(
        "bill-forecast",
        help="Get bill forecast including last bill and current usage",
    )
    bill_parser.add_argument(
        "-a",
        "--account",
        help="Account number (uses selected account if not provided)",
    )
    bill_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    return parser.parse_args()


def load_tokens(token_file: Path) -> dict[str, str | None]:
    """Load tokens from JSON file."""
    if not token_file.exists():
        print(f"Error: Token file not found: {token_file}", file=sys.stderr)
        print(f"\nTo create a token file, log in at:\n  {LOGIN_URL}", file=sys.stderr)
        print("\nThen create a JSON file with:", file=sys.stderr)
        print('  {"access_token": "...", "refresh_token": "..."}', file=sys.stderr)
        sys.exit(1)

    with token_file.open() as f:
        tokens: dict[str, str | None] = json.load(f)

    if "access_token" not in tokens or "refresh_token" not in tokens:
        print("Error: Token file must contain access_token and refresh_token")
        sys.exit(1)

    return tokens


def save_tokens(
    token_file: Path,
    access_token: str,
    refresh_token: str,
    *,
    selected_account: str | None = None,
    selected_meter: str | None = None,
    preserve_selection: bool = True,
) -> None:
    """Save tokens to JSON file.

    Args:
        token_file: Path to the token file.
        access_token: Access token to save.
        refresh_token: Refresh token to save.
        selected_account: Account number to save as default.
        selected_meter: Meter number to save as default.
        preserve_selection: If True, preserve existing selection when not provided.
    """
    tokens: dict[str, str | None] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
    }

    # Preserve existing selection if not explicitly provided
    if preserve_selection and token_file.exists():
        try:
            with token_file.open() as f:
                existing = json.load(f)
            if selected_account is None:
                selected_account = existing.get("selected_account")
            if selected_meter is None:
                selected_meter = existing.get("selected_meter")
        except (json.JSONDecodeError, OSError):
            pass

    if selected_account:
        tokens["selected_account"] = selected_account
    if selected_meter:
        tokens["selected_meter"] = selected_meter

    with token_file.open("w") as f:
        json.dump(tokens, f, indent=2)


def save_selection(
    token_file: Path,
    account_number: str,
    meter_number: str,
) -> None:
    """Save selected account/meter to token file.

    Args:
        token_file: Path to the token file.
        account_number: Account number to select.
        meter_number: Meter number to select.
    """
    if not token_file.exists():
        print(f"Error: Token file not found: {token_file}", file=sys.stderr)
        sys.exit(1)

    with token_file.open() as f:
        tokens = json.load(f)

    tokens["selected_account"] = account_number
    tokens["selected_meter"] = meter_number

    with token_file.open("w") as f:
        json.dump(tokens, f, indent=2)


async def cmd_usage(args: argparse.Namespace) -> int:
    """Handle usage command."""
    tokens = load_tokens(args.token_file)

    # Get account/meter from args or fallback to selected
    account = args.account or tokens.get("selected_account")
    meter = args.meter or tokens.get("selected_meter")

    if not account:
        print(
            "Error: No account specified. Use -a or run 'select-account' first.",
            file=sys.stderr,
        )
        return 1
    if not meter:
        print(
            "Error: No meter specified. Use -m or run 'select-account' first.",
            file=sys.stderr,
        )
        return 1

    end_date = args.end_date or date.today()
    start_date = args.start_date or (end_date - timedelta(days=args.days))

    def token_callback(access: str, refresh: str) -> None:
        save_tokens(args.token_file, access, refresh)
        logging.info("Tokens updated and saved to %s", args.token_file)

    async with aiohttp.ClientSession() as session:
        client = DompowerClient(
            session,
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            token_update_callback=token_callback,
        )

        if args.raw:
            # Get raw Excel file
            excel_data = await client.async_get_raw_excel(
                account_number=account,
                meter_number=meter,
                start_date=start_date,
                end_date=end_date,
            )

            if args.output:
                args.output.write_bytes(excel_data)
                print(f"Saved to {args.output}")
            else:
                sys.stdout.buffer.write(excel_data)
        else:
            # Get parsed usage data
            usage_data = await client.async_get_interval_usage(
                account_number=account,
                meter_number=meter,
                start_date=start_date,
                end_date=end_date,
            )

            if args.json:
                output = [
                    {
                        "timestamp": u.timestamp.isoformat(),
                        "consumption": u.consumption,
                        "unit": u.unit,
                    }
                    for u in usage_data
                ]
                print(json.dumps(output, indent=2))
            else:
                print(f"Usage data from {start_date} to {end_date}")
                print(f"Total records: {len(usage_data)}")
                print()
                print(f"{'Timestamp':<25} {'Consumption':>12} {'Unit':<6}")
                print("-" * 45)
                for u in usage_data[:20]:  # Show first 20
                    print(
                        f"{u.timestamp.strftime('%Y-%m-%d %H:%M'):<25} "
                        f"{u.consumption:>12.2f} {u.unit:<6}"
                    )
                if len(usage_data) > 20:
                    print(f"... and {len(usage_data) - 20} more records")

    return 0


async def cmd_refresh(args: argparse.Namespace) -> int:
    """Handle refresh command."""
    tokens = load_tokens(args.token_file)

    def token_callback(access: str, refresh: str) -> None:
        save_tokens(args.token_file, access, refresh)

    async with aiohttp.ClientSession() as session:
        client = DompowerClient(
            session,
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            token_update_callback=token_callback,
        )

        await client.async_refresh_tokens()
        print(f"Tokens refreshed and saved to {args.token_file}")

    return 0


async def cmd_auth_info(_args: argparse.Namespace) -> int:
    """Handle auth-info command."""
    print("Dominion Energy Authentication")
    print("=" * 40)
    print()
    print("Initial authentication requires logging in via browser")
    print("due to CAPTCHA protection.")
    print()
    print("1. Open this URL in your browser:")
    print(f"   {LOGIN_URL}")
    print()
    print("2. Log in with your Dominion Energy credentials")
    print()
    print("3. After login, open browser DevTools (F12)")
    print("   - Go to Application > Local Storage")
    print("   - Or Network tab, look for requests to smartcmobile.com")
    print()
    print("4. Find and copy:")
    print("   - accessToken")
    print("   - refreshToken")
    print()
    print("5. Create a tokens.json file:")
    print('   {"access_token": "...", "refresh_token": "..."}')
    print()
    print("6. Use with: dompower --token-file tokens.json usage ...")
    return 0


async def cmd_auth_helper(args: argparse.Namespace) -> int:
    """Handle auth-helper command - interactive token extraction."""
    import webbrowser

    print()
    print("=" * 60)
    print("  Dominion Energy Token Helper")
    print("=" * 60)
    print()
    print("This helper will guide you through extracting tokens from")
    print("the Dominion Energy website.")
    print()

    if args.open_browser:
        print("Opening browser...")
        webbrowser.open(LOGIN_URL)
    else:
        print("Please open this URL in your browser:")
        print()
        print(f"  {LOGIN_URL}")
        print()

    print("-" * 60)
    print("INSTRUCTIONS:")
    print("-" * 60)
    print()
    print("1. Log in with your Dominion Energy credentials")
    print()
    print("2. After login, open browser DevTools:")
    print("   - Chrome/Edge: Press F12 or Ctrl+Shift+I")
    print("   - Firefox: Press F12 or Ctrl+Shift+I")
    print("   - Safari: Enable Developer menu, then Cmd+Option+I")
    print()
    print("3. Go to the Network tab")
    print()
    print("4. Look for requests to: prodsvc-dominioncip.smartcmobile.com")
    print("   (You may need to refresh the page after opening DevTools)")
    print()
    print("5. Click on any request to that domain")
    print()
    print("6. In the Headers tab, find the 'Authorization' header")
    print("   It looks like: 'Bearer eyJhbGciOi...'")
    print("   The part after 'Bearer ' is your access_token")
    print()
    print("7. For refresh_token, look in the request/response body")
    print("   or in Application > Local Storage")
    print()
    print("-" * 60)
    print()

    # Interactive prompt for tokens
    print("Enter your tokens below (paste and press Enter):")
    print()

    access_token = input("Access Token: ").strip()
    if access_token.startswith("Bearer "):
        access_token = access_token[7:]

    if not access_token:
        print("Error: Access token is required", file=sys.stderr)
        return 1

    refresh_token = input("Refresh Token: ").strip()
    if not refresh_token:
        print("Error: Refresh token is required", file=sys.stderr)
        return 1

    # Validate tokens by attempting a refresh
    print()
    print("Validating tokens...")

    try:
        async with aiohttp.ClientSession() as session:
            client = DompowerClient(
                session,
                access_token=access_token,
                refresh_token=refresh_token,
            )
            # Try to refresh to validate
            await client.async_refresh_tokens()
            print("Tokens are valid!")

    except TokenExpiredError:
        print(
            "Warning: Refresh token may be expired. Saving anyway...",
            file=sys.stderr,
        )
    except DompowerError as e:
        print(f"Warning: Could not validate tokens: {e}", file=sys.stderr)
        print("Saving tokens anyway - they may still work.", file=sys.stderr)

    # Save tokens
    save_tokens(args.output, access_token, refresh_token)
    print()
    print(f"Tokens saved to: {args.output}")
    print()
    print("You can now use dompower commands:")
    print(f"  dompower --token-file {args.output} usage -a ACCOUNT -m METER")
    print()

    return 0


async def cmd_login(args: argparse.Namespace) -> int:
    """Handle login command with username/password and TFA support."""
    from getpass import getpass

    from .models import TFAProvider

    print()
    print("=" * 60)
    print("  Dominion Energy Login")
    print("=" * 60)
    print()

    # Get credentials
    username = args.username
    if not username:
        username = input("Email: ").strip()
        if not username:
            print("Error: Email is required", file=sys.stderr)
            return 1

    password = getpass("Password: ")
    if not password:
        print("Error: Password is required", file=sys.stderr)
        return 1

    # Determine cookie file
    cookie_file = args.cookies
    if cookie_file is None:
        cookie_file = Path.home() / ".dompower" / "cookies.json"

    print()
    print("Logging in...")

    try:
        async with aiohttp.ClientSession() as session:
            auth = GigyaAuthenticator(session, cookie_file=cookie_file)

            # Load existing cookies (may bypass TFA)
            auth.load_cookies()

            # Initialize session
            await auth.async_init_session()

            # Submit credentials
            result = await auth.async_submit_credentials(username, password)

            if result.tfa_required:
                print()
                print("Two-factor authentication required.")

                # Get all TFA targets (both phone and email)
                all_targets: list[TFATarget] = []

                # Try to get phone targets
                try:
                    phone_targets = await auth.async_get_tfa_options(TFAProvider.PHONE)
                    all_targets.extend(phone_targets)
                except Exception:
                    logging.debug("Could not get phone TFA options")

                # Try to get email targets
                try:
                    email_targets = await auth.async_get_tfa_options(TFAProvider.EMAIL)
                    all_targets.extend(email_targets)
                except Exception:
                    logging.debug("Could not get email TFA options")

                if not all_targets:
                    print("Error: No TFA options available", file=sys.stderr)
                    return 1

                # Display options
                print()
                print("Select verification method:")
                for i, target in enumerate(all_targets, 1):
                    is_phone = target.provider == TFAProvider.PHONE
                    provider_name = "SMS" if is_phone else "Email"
                    print(f"  {i}. {provider_name}: {target.obfuscated}")

                # Get selection
                print()
                while True:
                    selection = input(f"Enter choice (1-{len(all_targets)}): ").strip()
                    try:
                        idx = int(selection) - 1
                        if 0 <= idx < len(all_targets):
                            selected_target = all_targets[idx]
                            break
                        print(f"Please enter a number between 1 and {len(all_targets)}")
                    except ValueError:
                        print("Please enter a valid number")

                # Send verification code
                is_phone = selected_target.provider == TFAProvider.PHONE
                provider_name = "SMS" if is_phone else "email"
                print(f"\nSending verification code via {provider_name}...")
                await auth.async_send_tfa_code(selected_target)

                # Get code from user
                print()
                print("-" * 40)
                print(f"Code sent to: {selected_target.obfuscated}")
                print("-" * 40)
                code = input("Enter code: ").strip()

                # Verify code
                print("\nVerifying...")
                tokens = await auth.async_verify_tfa_code(code)
            else:
                # No TFA required - complete login
                tokens = await auth._async_complete_login()

            # Save cookies for future TFA bypass
            auth.save_cookies()

            # Save tokens
            save_tokens(args.output, tokens.access_token, tokens.refresh_token)

            print()
            print("Login successful!")
            print(f"Tokens saved to: {args.output}")
            print(f"Cookies saved to: {cookie_file}")
            print()
            print("You can now use dompower commands:")
            print(f"  dompower --token-file {args.output} usage -a ACCOUNT -m METER")
            print()

            return 0

    except InvalidCredentialsError:
        print()
        print("Error: Invalid email or password", file=sys.stderr)
        return 1

    except TFAVerificationError as e:
        print()
        print(f"Error: TFA verification failed: {e}", file=sys.stderr)
        return 1

    except TFAExpiredError:
        print()
        print("Error: TFA session expired. Please try again.", file=sys.stderr)
        return 1


async def cmd_accounts(args: argparse.Namespace) -> int:
    """Handle accounts command - list accounts and meters."""
    tokens = load_tokens(args.token_file)

    def token_callback(access: str, refresh: str) -> None:
        save_tokens(args.token_file, access, refresh)
        logging.info("Tokens updated and saved to %s", args.token_file)

    async with aiohttp.ClientSession() as session:
        client = DompowerClient(
            session,
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            token_update_callback=token_callback,
        )

        customer_info = await client.async_get_customer_info(
            include_inactive=args.include_inactive
        )

        if args.json:
            # Output as JSON
            output = {
                "customer_number": customer_info.customer_number,
                "first_name": customer_info.first_name,
                "last_name": customer_info.last_name,
                "email": customer_info.email,
                "accounts": [
                    {
                        "account_number": acct.account_number,
                        "premise_number": acct.premise_number,
                        "service_address": str(acct.service_address),
                        "nickname": acct.nickname,
                        "is_active": acct.is_active,
                        "is_default": acct.is_default,
                        "ebill_enrolled": acct.ebill_enrolled,
                        "closing_date": (
                            acct.closing_date.isoformat() if acct.closing_date else None
                        ),
                        "meters": [
                            {
                                "device_id": m.device_id,
                                "contract_id": m.contract_id,
                                "is_active": m.is_active,
                                "has_ami": m.has_ami,
                                "net_metering": m.net_metering,
                            }
                            for m in acct.meters
                        ],
                    }
                    for acct in customer_info.accounts
                ],
            }
            print(json.dumps(output, indent=2))
        else:
            # Human-readable output
            print()
            cust_num = customer_info.customer_number
            print(f"Customer: {cust_num} ({customer_info.full_name})")
            print(f"Email: {customer_info.email}")
            print()

            for acct in customer_info.accounts:
                # Account header
                status = "Active" if acct.is_active else "Inactive"
                default_marker = " (Default)" if acct.is_default else ""
                nickname = f' "{acct.nickname}"' if acct.nickname else ""
                print(f"Account: {acct.account_number}{default_marker}{nickname}")

                # Address
                print(f"  Address: {acct.service_address}")

                # Meters
                for meter in acct.meters:
                    meter_status = "Active" if meter.is_active else "Inactive"
                    ami_flag = ", AMI" if meter.has_ami else ""
                    net_flag = ", Net Metering" if meter.net_metering else ""
                    flags = f"{meter_status}{ami_flag}{net_flag}"
                    print(f"  Meter: {meter.device_id} ({flags})")

                # Status line
                ebill = "Yes" if acct.ebill_enrolled else "No"
                status_line = f"  Status: {status}, E-Bill: {ebill}"
                if acct.closing_date:
                    status_line += f", Closed: {acct.closing_date}"
                print(status_line)
                print()

    return 0


async def cmd_select_account(args: argparse.Namespace) -> int:
    """Handle select-account command - select default account/meter."""
    tokens = load_tokens(args.token_file)

    def token_callback(access: str, refresh: str) -> None:
        save_tokens(args.token_file, access, refresh)
        logging.info("Tokens updated and saved to %s", args.token_file)

    async with aiohttp.ClientSession() as session:
        client = DompowerClient(
            session,
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            token_update_callback=token_callback,
        )

        # Fetch accounts
        accounts = await client.async_get_accounts()

        if not accounts:
            print("Error: No active accounts found", file=sys.stderr)
            return 1

        # If account specified via arg, find it
        if args.account:
            selected_account = None
            for acct in accounts:
                if acct.account_number == args.account:
                    selected_account = acct
                    break
            if not selected_account:
                print(f"Error: Account {args.account} not found", file=sys.stderr)
                return 1
        elif len(accounts) == 1:
            # Only one account, auto-select
            selected_account = accounts[0]
            print(f"Auto-selected account: {selected_account.account_number}")
        else:
            # Interactive selection
            print()
            print("Select an account:")
            print()
            for i, acct in enumerate(accounts, 1):
                default_marker = " (Default)" if acct.is_default else ""
                nickname = f' "{acct.nickname}"' if acct.nickname else ""
                print(f"  {i}. {acct.account_number}{default_marker}{nickname}")
                print(f"     {acct.service_address}")

            print()
            while True:
                selection = input(f"Enter choice (1-{len(accounts)}): ").strip()
                try:
                    idx = int(selection) - 1
                    if 0 <= idx < len(accounts):
                        selected_account = accounts[idx]
                        break
                    print(f"Please enter a number between 1 and {len(accounts)}")
                except ValueError:
                    print("Please enter a valid number")

        # Determine meter
        if args.meter:
            # Validate meter exists for this account
            meter_found = False
            for m in selected_account.meters:
                if m.device_id == args.meter:
                    meter_found = True
                    selected_meter = args.meter
                    break
            if not meter_found:
                print(
                    f"Error: Meter {args.meter} not found for account",
                    file=sys.stderr,
                )
                return 1
        elif selected_account.primary_meter:
            # Use primary meter
            selected_meter = selected_account.primary_meter.device_id
        elif selected_account.meters:
            # Use first meter
            selected_meter = selected_account.meters[0].device_id
        else:
            print("Error: No meters found for account", file=sys.stderr)
            return 1

        # Save selection
        save_selection(args.token_file, selected_account.account_number, selected_meter)

        print()
        print("Selection saved:")
        print(f"  Account: {selected_account.account_number}")
        print(f"  Address: {selected_account.service_address}")
        print(f"  Meter:   {selected_meter}")
        print()
        print("You can now run commands without specifying account/meter:")
        print(f"  dompower --token-file {args.token_file} usage")
        print(f"  dompower --token-file {args.token_file} bill-forecast")
        print()

    return 0


async def cmd_bill_forecast(args: argparse.Namespace) -> int:
    """Handle bill-forecast command - get bill forecast data."""
    tokens = load_tokens(args.token_file)

    # Get account from args or fallback to selected
    account = args.account or tokens.get("selected_account")

    if not account:
        print(
            "Error: No account specified. Use -a or run 'select-account' first.",
            file=sys.stderr,
        )
        return 1

    def token_callback(access: str, refresh: str) -> None:
        save_tokens(args.token_file, access, refresh)
        logging.info("Tokens updated and saved to %s", args.token_file)

    async with aiohttp.ClientSession() as session:
        client = DompowerClient(
            session,
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            token_update_callback=token_callback,
        )

        forecast = await client.async_get_bill_forecast(account)

        if args.json:
            output = {
                "last_bill": {
                    "charges": forecast.last_bill.charges,
                    "usage": forecast.last_bill.usage,
                    "period_start": forecast.last_bill.period_start.isoformat()
                    if forecast.last_bill.period_start
                    else None,
                    "period_end": forecast.last_bill.period_end.isoformat()
                    if forecast.last_bill.period_end
                    else None,
                },
                "current_period_start": forecast.current_period_start.isoformat(),
                "current_period_end": forecast.current_period_end.isoformat(),
                "current_usage_kwh": forecast.current_usage_kwh,
                "is_tou": forecast.is_tou,
                "derived_rate": forecast.derived_rate,
            }
            print(json.dumps(output, indent=2))
        else:
            print()
            print(f"Bill Forecast for Account: {account}")
            print("=" * 50)
            print()

            # Last bill info
            print("Last Bill:")
            if forecast.last_bill.period_start and forecast.last_bill.period_end:
                start = forecast.last_bill.period_start
                end = forecast.last_bill.period_end
                print(f"  Period: {start} to {end}")
            print(f"  Usage:   {forecast.last_bill.usage:.1f} kWh")
            print(f"  Charges: ${forecast.last_bill.charges:.2f}")
            if forecast.derived_rate:
                print(f"  Rate:    ${forecast.derived_rate:.4f}/kWh")
            print()

            # Current period
            print("Current Period:")
            start = forecast.current_period_start
            end = forecast.current_period_end
            print(f"  Period: {start} to {end}")
            print(f"  Usage:  {forecast.current_usage_kwh:.1f} kWh")
            if forecast.derived_rate:
                estimated_cost = forecast.current_usage_kwh * forecast.derived_rate
                print(f"  Est. Cost: ${estimated_cost:.2f} (based on last bill rate)")
            print()

            if forecast.is_tou:
                print("Note: Account is on time-of-use pricing")
                print()

    return 0


async def async_main() -> int:
    """Async main entry point."""
    args = parse_args()

    # Setup logging
    if args.verbose >= 2:
        logging.basicConfig(level=logging.DEBUG)
    elif args.verbose == 1:
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.WARNING)

    # Commands that don't require token file
    no_token_commands = {"auth-info", "auth-helper", "login"}

    # Validate token-file is provided for commands that need it
    if args.command not in no_token_commands and not args.token_file:
        print(
            f"Error: --token-file is required for '{args.command}' command",
            file=sys.stderr,
        )
        return 1

    try:
        if args.command == "usage":
            return await cmd_usage(args)
        elif args.command == "refresh":
            return await cmd_refresh(args)
        elif args.command == "auth-info":
            return await cmd_auth_info(args)
        elif args.command == "auth-helper":
            return await cmd_auth_helper(args)
        elif args.command == "login":
            return await cmd_login(args)
        elif args.command == "accounts":
            return await cmd_accounts(args)
        elif args.command == "select-account":
            return await cmd_select_account(args)
        elif args.command == "bill-forecast":
            return await cmd_bill_forecast(args)
        else:
            print(f"Unknown command: {args.command}", file=sys.stderr)
            return 1
    except TokenExpiredError:
        print(
            "Error: Tokens have expired. Please re-authenticate via browser.",
            file=sys.stderr,
        )
        print(f"  {LOGIN_URL}", file=sys.stderr)
        return 1
    except DompowerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def main() -> None:
    """Main entry point."""
    sys.exit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
