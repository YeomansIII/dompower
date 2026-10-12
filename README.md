# dompower

Async Python client for Dominion Energy API. Retrieves 30-minute interval electricity usage data for Virginia and North Carolina, and 15-minute electric and hourly gas data for [South Carolina](#south-carolina-accounts).

## Requirements

- Python 3.12+
- Dominion Energy account (Virginia/North Carolina)

## Installation

```bash
pip install dompower
```

Or with [uv](https://docs.astral.sh/uv/):
```bash
uv add dompower
```

## Authentication

### Option 1: Login with Username/Password (Recommended)

The CLI supports direct login with two-factor authentication (TFA):

```bash
dompower login -u your.email@example.com
```

You'll be prompted for your password and then guided through TFA verification (SMS or email). Tokens are saved automatically.

### Option 2: Manual Token Extraction

If the login command doesn't work, you can manually extract tokens from a browser session:

1. Open https://login.dominionenergy.com/CommonLogin?SelectedAppName=Electric
2. Log in with your Dominion Energy credentials
3. Open browser DevTools (F12) > Network tab
4. Look for requests to `prodsvc-dominioncip.smartcmobile.com`
5. Find the `accessToken` and `refreshToken` in request/response headers

Create a `tokens.json` file:
```json
{
  "access_token": "eyJhbGciOiJodHRwOi...",
  "refresh_token": "pd9YAsV9HKNkrECM..."
}
```

### Token Refresh

The library automatically refreshes tokens when they expire (every 30 minutes). Both tokens rotate on each refresh - the library handles this automatically and notifies via callback.

## CLI Usage

### Get Usage Data

```bash
# Last 7 days of 30-minute interval data
dompower --token-file tokens.json usage -a ACCOUNT_NUMBER -m METER_NUMBER

# Custom date range
dompower --token-file tokens.json usage -a 123456 -m 789 \
  --start-date 2024-01-01 --end-date 2024-01-31

# Output as JSON
dompower --token-file tokens.json usage -a 123456 -m 789 --json

# Save raw Excel file
dompower --token-file tokens.json usage -a 123456 -m 789 --raw -o usage.xlsx
```

### Login

```bash
# Interactive login with TFA support
dompower login -u your.email@example.com

# Specify output file and cookie storage
dompower login -u your.email@example.com -o tokens.json --cookies ~/.dompower/cookies.json
```

Cookies are saved to enable TFA bypass on subsequent logins.

### Account Discovery

```bash
# List all accounts and meters
dompower --token-file tokens.json accounts

# Include inactive/closed accounts
dompower --token-file tokens.json accounts --all

# Output as JSON
dompower --token-file tokens.json accounts --json

# Select default account/meter (interactive)
dompower --token-file tokens.json select-account

# Select specific account
dompower --token-file tokens.json select-account -a 123456789
```

After selecting an account, you can omit `-a` and `-m` from usage commands.

### Bill Forecast

```bash
# Get bill forecast with last bill details and current usage
dompower --token-file tokens.json bill-forecast

# For specific account
dompower --token-file tokens.json bill-forecast -a 123456789

# Output as JSON
dompower --token-file tokens.json bill-forecast --json
```

### Other Commands

```bash
# Manually refresh tokens
dompower --token-file tokens.json refresh

# Show authentication instructions
dompower auth-info
```

## Library Usage

### Basic Example

```python
import asyncio
from datetime import date, timedelta
import aiohttp
from dompower import DompowerClient


async def main():
    async with aiohttp.ClientSession() as session:
        client = DompowerClient(
            session,
            access_token="your_access_token",
            refresh_token="your_refresh_token",
        )

        usage = await client.async_get_interval_usage(
            account_number="123456789",
            meter_number="000000000123456789",
            start_date=date.today() - timedelta(days=7),
            end_date=date.today(),
        )

        for record in usage:
            print(f"{record.timestamp}: {record.consumption} {record.unit}")


asyncio.run(main())
```

### With Token Persistence

```python
import json
from pathlib import Path
import aiohttp
from dompower import DompowerClient

TOKEN_FILE = Path("tokens.json")


def load_tokens():
    with TOKEN_FILE.open() as f:
        return json.load(f)


def save_tokens(access_token: str, refresh_token: str):
    with TOKEN_FILE.open("w") as f:
        json.dump({"access_token": access_token, "refresh_token": refresh_token}, f)


async def main():
    tokens = load_tokens()

    async with aiohttp.ClientSession() as session:
        client = DompowerClient(
            session,
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            token_update_callback=save_tokens,  # Called when tokens refresh
        )

        usage = await client.async_get_interval_usage(...)
```

### Login with Username/Password

```python
import aiohttp
from dompower import GigyaAuthenticator, TFAProvider


async def login_with_tfa():
    async with aiohttp.ClientSession() as session:
        auth = GigyaAuthenticator(session)

        # Initialize session (gets WAF and Gigya cookies)
        await auth.async_init_session()

        # Submit credentials
        result = await auth.async_submit_credentials(
            "your.email@example.com", "your_password"
        )

        if result.tfa_required:
            # Get available TFA targets
            targets = await auth.async_get_tfa_options(TFAProvider.PHONE)

            # Send verification code
            await auth.async_send_tfa_code(targets[0])

            # Get code from user and verify
            code = input(f"Enter code sent to {targets[0].obfuscated}: ")
            tokens = await auth.async_verify_tfa_code(code)
        else:
            # No TFA required
            tokens = await auth._async_complete_login()

        print(f"Access token: {tokens.access_token[:20]}...")
        print(f"Refresh token: {tokens.refresh_token[:20]}...")
```

### Home Assistant Integration Pattern

```python
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from dompower import DompowerClient


async def async_setup_entry(hass, entry):
    session = async_get_clientsession(hass)

    def token_callback(access_token: str, refresh_token: str):
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                "access_token": access_token,
                "refresh_token": refresh_token,
            },
        )

    client = DompowerClient(
        session,
        access_token=entry.data["access_token"],
        refresh_token=entry.data["refresh_token"],
        token_update_callback=token_callback,
    )

    # Store client for use in sensors/coordinators
    hass.data[DOMAIN][entry.entry_id] = client
```

## API Reference

### DompowerClient

Main client class for API interaction.

```python
DompowerClient(
    session: ClientSession,
    *,
    access_token: str | None = None,
    refresh_token: str | None = None,
    token_update_callback: Callable[[str, str], None] | None = None,
)
```

**Methods:**

- `async_get_interval_usage(account_number, meter_number, start_date, end_date)` - Get 30-minute usage data
- `async_get_raw_excel(account_number, meter_number, start_date, end_date)` - Get raw Excel file
- `async_get_accounts()` - Get all accounts and meters for the authenticated user
- `async_get_customer_info()` - Get customer profile with all accounts
- `async_get_bill_forecast(account_number)` - Get bill forecast with last bill data
- `async_login(username, password, tfa_code_callback)` - Login with username/password
- `async_set_tokens(access_token, refresh_token)` - Set tokens manually
- `async_refresh_tokens()` - Force token refresh

### GigyaAuthenticator

Handles Gigya/SAP authentication with TFA support. Use for step-by-step login flows.

```python
GigyaAuthenticator(
    session: ClientSession,
    cookie_file: Path | None = None,  # Persist cookies for TFA bypass
)
```

**Methods:**

- `async_init_session()` - Initialize WAF and Gigya cookies
- `async_submit_credentials(username, password)` - Submit login credentials
- `async_get_tfa_options(provider)` - Get available TFA targets (phone/email)
- `async_send_tfa_code(target)` - Send verification code to target
- `async_verify_tfa_code(code)` - Verify TFA code and get tokens
- `async_login(username, password, tfa_callback)` - Complete login with callback
- `save_cookies()` / `load_cookies()` - Persist cookies for TFA bypass

### Data Models

```python
@dataclass(frozen=True)
class IntervalUsageData:
    timestamp: datetime  # Start of 30-minute interval
    consumption: float  # kWh consumed
    generation: float  # kWh excess generation returned to grid
    unit: str  # "kWh"
```

### Exceptions

```python
DompowerError                # Base exception
AuthenticationError          # Authentication issues
  InvalidAuthError           # Invalid tokens
  TokenExpiredError          # Tokens expired, need re-auth
  BrowserAuthRequiredError   # Initial auth needed
  GigyaError                 # Gigya authentication errors
    InvalidCredentialsError  # Wrong username/password
    TFARequiredError         # TFA required (not an error, normal flow)
    TFAVerificationError     # TFA code verification failed
    TFAExpiredError          # TFA session expired (~5 min timeout)
CannotConnectError           # Network issues
ApiError                     # API returned error
  RateLimitError             # Rate limited (429)
```

## South Carolina Accounts

Dominion Energy South Carolina uses a different portal
(`account.dominionenergysc.com`) and a different data vendor (Bidgely).
Use `dompower.sc` for SC accounts. `DompowerClient` and `GigyaAuthenticator`
work only for Virginia and North Carolina.

### CLI

```bash
# Log in once. This does TFA and saves a TFA token to ~/.dompower/sc.json.
# Use your SC portal username. It is not always your email address.
dompower sc-login -u your-username

# Show the account, meter registers, daily totals, and bill forecast.
# Set DOMPOWER_SC_PASSWORD to skip the password prompt.
dompower sc-usage --days 3
dompower sc-usage --days 3 --gas --json

# Write a redacted diagnostic report to dompower-sc-report.txt.
dompower sc-report
```

### Diagnostic report

`dompower sc-report` logs in with the saved TFA token and writes a plain
text file. You can attach it to a GitHub issue. Read the file before you
post it.

The report does not contain your username, password, TFA token, cookies,
email address, phone number, service address, account number, or Bidgely
user ID. It shows:

- Each request (path and HTTP status) and the JSON field names of each
  response. Most values show only as `<string, N chars>` or `<number>`.
- The meter registers, readings per day, and daily and hourly totals.
- The structure of the Green Button XML and some raw readings. Entry titles
  show only if they are generic Green Button titles, such as
  "Interval Consumption".
- The bill forecast amounts.

If a step fails, the report still includes the error and the responses up
to that step.

### Library

The portal uses session cookies. Use a dedicated session with
`create_sc_cookie_jar()`. The default cookie jar quotes cookie values, and the
portal then rejects the session.

```python
import aiohttp
from datetime import date
from dompower import DominionSCClient, SCAuthenticator, create_sc_cookie_jar

async with aiohttp.ClientSession(cookie_jar=create_sc_cookie_jar()) as session:
    # First login: interactive TFA gives a TFA token. Save it.
    auth = SCAuthenticator(session)
    result = await auth.async_submit_credentials(username, password)
    if result.tfa_required:
        targets = await auth.async_get_tfa_options()
        await auth.async_send_tfa_code(targets[0])
        tfa_token = await auth.async_verify_tfa_code(input("Code: "))
    sc_session = await auth.async_complete_login()

    # Later logins: the saved TFA token skips TFA.
    client = DominionSCClient(session, username, password, tfa_token=tfa_token)
    await client.async_login()
    usage = await client.async_get_interval_usage(date(2026, 8, 1), date(2026, 8, 7))
    forecast = await client.async_get_bill_forecast()
```

SC data differs from VA/NC data:

- Electric intervals are 15 minutes (kWh). Gas intervals are 1 hour (ft³).
  Use `meter_type=MeterType.GAS` for gas.
- Solar export is a separate meter register with negative values. The client
  puts positive values in `consumption` and negative values in `generation`.
  `async_get_register_usage()` returns the raw readings for each register.
- Timestamps are in `America/New_York`. On the DST fall-back day, the two
  01:00-01:59 hours have the same wall times. The second hour has `fold=1`
  (EST), so the day has 25 hours (100 electric intervals). Datetimes that
  differ only in `fold` compare equal. To group or deduplicate intervals,
  use `timestamp.astimezone(UTC)` as the key. On the spring-forward day,
  02:00-02:59 does not exist. If Bidgely sends readings for these times,
  the client moves them to 03:00-03:59 EDT and adds them to those intervals.
- `SCBillForecast` has the cost to date, the forecasted cost, and last year's
  cost. The dollar values include electric and gas together.
- A login with more than one account raises `ApiError`.
- If the TFA token is not accepted, `async_login()` raises `TFARequiredError`.

The SC backend is a port of
[hype8912/dominion-sc-power](https://github.com/hype8912/dominion-sc-power)
(Apache-2.0). See `NOTICE`.

## Data Format

The API returns 30-minute interval data. Example output:

```
Timestamp                  Consumption   Generation Unit  
---------------------------------------------------------
2024-01-15 00:00                  0.45         0.01 kWh
2024-01-15 00:30                  0.38         0.00 kWh
2024-01-15 01:00                  0.42         0.01 kWh
...
```

## Limitations

- TFA verification codes expire after ~5 minutes
- Refresh tokens may expire after extended periods of inactivity
- API rate limits are not documented; library does not implement rate limiting

## Development

### With uv (recommended)

```bash
git clone https://github.com/YeomansIII/dompower
cd dompower
uv sync --dev
uv run pytest
uv run mypy dompower
uv run ruff check dompower
```

### With pip/venv

```bash
git clone https://github.com/YeomansIII/dompower
cd dompower
python3 -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
pytest
mypy dompower
ruff check dompower
```

## License

MIT
