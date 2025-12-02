# dompower

Async Python client for Dominion Energy API. Retrieves 30-minute interval electricity usage data.

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

Dominion Energy uses CAPTCHA-protected login. Initial authentication requires manual browser login.

### Getting Tokens

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

### Other Commands

```bash
# Manually refresh tokens
dompower --token-file tokens.json refresh

# Show authentication instructions
dompower --token-file tokens.json auth-info
```

### Finding Account and Meter Numbers

Log into myaccount.dominionenergy.com and look for:
- Account Number: Displayed on dashboard/bills
- Meter Number: Found in account details or on your physical meter

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
        json.dump({
            "access_token": access_token,
            "refresh_token": refresh_token
        }, f)

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
- `async_set_tokens(access_token, refresh_token)` - Set tokens manually
- `async_refresh_tokens()` - Force token refresh

### Data Models

```python
@dataclass(frozen=True)
class IntervalUsageData:
    timestamp: datetime  # Start of 30-minute interval
    consumption: float   # kWh consumed
    unit: str           # "kWh"
```

### Exceptions

```python
DompowerError              # Base exception
AuthenticationError        # Authentication issues
  InvalidAuthError         # Invalid tokens
  TokenExpiredError        # Tokens expired, need browser re-auth
  BrowserAuthRequiredError # Initial auth needed
CannotConnectError         # Network issues
ApiError                   # API returned error
  RateLimitError          # Rate limited (429)
```

## Data Format

The API returns 30-minute interval data. Example output:

```
Timestamp                 Consumption    Unit
---------------------------------------------
2024-01-15 00:00          0.45           kWh
2024-01-15 00:30          0.38           kWh
2024-01-15 01:00          0.42           kWh
...
```

## Limitations

- Initial authentication requires manual browser login (CAPTCHA protected)
- Refresh tokens may expire after extended periods of inactivity
- API rate limits are not documented; library does not implement rate limiting

## Development

### With uv (recommended)

```bash
git clone https://github.com/jyeo098/dompower
cd dompower
uv sync --dev
uv run pytest
uv run mypy dompower
uv run ruff check dompower
```

### With pip/venv

```bash
git clone https://github.com/jyeo098/dompower
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
