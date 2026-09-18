# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

dompower is an async Python client library for the Dominion Energy API. It retrieves 30-minute interval electricity usage data and is designed for integration with Home Assistant.

## Commands

```bash
# Install dependencies (uses uv)
uv sync --dev

# Run tests
uv run pytest

# Run a single test
uv run pytest tests/test_file.py::test_function_name

# Type checking
uv run mypy dompower

# Linting
uv run ruff check dompower

# Format code
uv run ruff format dompower
```

## Architecture

### Core Components

- **`client.py`** - `DompowerClient`: Main API client. Handles authenticated requests, token refresh on 401, and parsing API responses. Entry point for all usage data fetching.

- **`auth.py`** - `TokenManager`: Manages access/refresh token lifecycle. Tokens expire every 30 minutes; automatically refreshes and invokes callback to persist new tokens.

- **`gigya_auth.py`** - `GigyaAuthenticator`: Handles the multi-step Gigya SSO authentication flow with two-factor authentication (TFA) support. Used for initial login (not token refresh).

- **`sc/`** - Dominion Energy South Carolina backend. SC uses an ASP.NET portal (`account.dominionenergysc.com`) and Bidgely for interval data, not Gigya/SEW.
  - `sc/auth.py` - `SCAuthenticator`: portal login, TFA (`InitAuthentication`, `SendPINCode`, `VerifyPIN`), saved TFA token (`Verify2FAToken`), Bidgely bearer token. Step method names match `GigyaAuthenticator`.
  - `sc/client.py` - `DominionSCClient`: interval usage and bill forecast. Logs in again once on 401/403.
  - `sc/greenbutton.py` - Pure Green Button XML parser. One `SCRegisterUsage` per UsagePoint.
  - `sc/cli.py` - `sc-login` and `sc-usage` CLI commands.

### SC Notes

- Use a session with `create_sc_cookie_jar()` (`quote_cookie=False`). The portal rejects quoted cookie values.
- Bidgely epochs are local wall time encoded as UTC. The parser attaches `America/New_York` to the UTC clock time.
- Solar export is a separate register with negative values. `registers_to_intervals` puts positive values in `consumption` and negative values in `generation`.
- Electric values are Wh (converted to kWh). Gas values are ft³ (not converted).
- The `X-Bidgely-Pilot-Id` header must be `10078`. Other values give estimated data.
- The SC code is tested with fixtures only. The maintainer has no SC account.

### Authentication Flow

1. Initial auth requires manual browser login (CAPTCHA protected) OR programmatic Gigya flow with TFA
2. Both `access_token` and `refresh_token` are needed for API calls
3. Tokens rotate on every refresh (both change) - client notifies via `token_update_callback`
4. Access tokens expire in 30 minutes; refresh tokens expire after extended inactivity

### Data Models (`models.py`)

Key frozen dataclasses:
- `IntervalUsageData` - 30-minute consumption data (timestamp, consumption, unit)
- `TokenPair` - Access/refresh token pair
- `BillForecast` / `BillPeriodData` - Billing data with `derived_rate` property for $/kWh
- `CustomerInfo` / `AccountInfo` / `MeterDevice` - Account hierarchy
- `GigyaSession` / `TFATarget` - TFA authentication state

### Exception Hierarchy (`exceptions.py`)

```
DompowerError
├── AuthenticationError
│   ├── InvalidAuthError
│   ├── TokenExpiredError
│   ├── BrowserAuthRequiredError
│   └── GigyaError
│       ├── InvalidCredentialsError
│       ├── TFARequiredError
│       ├── TFAVerificationError
│       └── TFAExpiredError
├── CannotConnectError
└── ApiError
    └── RateLimitError
```

## Testing

- Uses pytest-asyncio with `asyncio_mode = "auto"`
- Uses aresponses for mocking aiohttp requests
- Test fixtures in `tests/conftest.py`

## Code Style

- Python 3.12+ with strict mypy
- Ruff for linting (includes isort, pycodestyle, flake8-bugbear, flake8-bandit)
- All public classes/methods have docstrings
- Async methods prefixed with `async_`
- Private methods prefixed with `_`
