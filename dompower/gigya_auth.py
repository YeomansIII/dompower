"""Gigya authentication for the dompower library.

This module implements the complete Dominion Energy authentication flow
using SAP Customer Data Cloud (Gigya) for authentication, including
two-factor authentication (TFA) support.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .const import (
    BASE_URL,
    DEFAULT_HEADERS,
    ENDPOINT_LOGIN,
    GIGYA_ACCOUNT_INFO,
    GIGYA_API_KEY,
    GIGYA_BASE_URL,
    GIGYA_BOOTSTRAP,
    GIGYA_ERROR_INVALID_JWT,
    GIGYA_ERROR_INVALID_LOGIN,
    GIGYA_ERROR_INVALID_PASSWORD,
    GIGYA_ERROR_TFA_PENDING,
    GIGYA_FINALIZE_REGISTRATION,
    GIGYA_LOGIN,
    GIGYA_SDK_BUILD,
    GIGYA_SDK_VERSION,
    GIGYA_TFA_EMAILS,
    GIGYA_TFA_FINALIZE,
    GIGYA_TFA_INIT,
    GIGYA_TFA_PHONE_NUMBERS,
    GIGYA_TFA_PROVIDERS,
    GIGYA_TFA_SEND_EMAIL,
    GIGYA_TFA_SEND_PHONE,
    GIGYA_TFA_VERIFY_EMAIL,
    GIGYA_TFA_VERIFY_PHONE,
    LOGIN_URL,
)
from .exceptions import (
    CannotConnectError,
    GigyaError,
    InvalidCredentialsError,
    TFAExpiredError,
    TFARequiredError,
    TFAVerificationError,
)
from .models import (
    GigyaSession,
    LoginResult,
    TFAProvider,
    TFATarget,
    TokenPair,
)

if TYPE_CHECKING:
    from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)

# Type alias for TFA code callback
TFACodeCallback = Callable[[TFATarget], Awaitable[str]]


class GigyaAuthenticator:
    """Handles Gigya/Dominion Energy authentication with TFA support.

    This class implements the complete authentication flow including:
    - WAF cookie initialization (Incapsula)
    - Gigya SDK bootstrap
    - Credential submission
    - Two-factor authentication (phone SMS or email)
    - Token exchange for Dominion API

    Can be used in two ways:
    1. Full login with callback (for CLI):
       ```
       tokens = await auth.async_login(username, password, tfa_callback)
       ```

    2. Step-by-step (for Home Assistant config flow):
       ```
       await auth.async_init_session()
       result = await auth.async_submit_credentials(username, password)
       if result.tfa_required:
           targets = await auth.async_get_tfa_options()
           await auth.async_send_tfa_code(targets[0])
           tokens = await auth.async_verify_tfa_code(code)
       ```
    """

    def __init__(
        self,
        session: ClientSession,
        cookie_file: Path | None = None,
    ) -> None:
        """Initialize the Gigya authenticator.

        Args:
            session: aiohttp ClientSession for making requests.
            cookie_file: Optional path to persist cookies for TFA bypass.
        """
        self._session = session
        self._cookie_file = cookie_file
        self._gigya_session = GigyaSession()
        self._session_initialized = False

    # -------------------------------------------------------------------------
    # High-level API (CLI usage with callback)
    # -------------------------------------------------------------------------

    async def async_login(
        self,
        username: str,
        password: str,
        tfa_code_callback: TFACodeCallback | None = None,
        preferred_provider: TFAProvider | None = None,
    ) -> TokenPair:
        """Complete login flow and return Dominion API tokens.

        This is the main entry point for authentication. It handles:
        1. Session initialization (WAF + Gigya cookies)
        2. Credential submission
        3. TFA flow (if required)
        4. Token exchange

        Args:
            username: Dominion Energy email address.
            password: Account password.
            tfa_code_callback: Async callback to get TFA code from user.
                Signature: async (target: TFATarget) -> str
                Required if TFA might be needed.
            preferred_provider: Preferred TFA provider (phone or email).

        Returns:
            TokenPair with access_token and refresh_token.

        Raises:
            InvalidCredentialsError: Wrong username or password.
            TFARequiredError: TFA required but no callback provided.
            TFAVerificationError: TFA code verification failed.
            CannotConnectError: Network issues.
        """
        # Load cookies (may skip TFA if valid session)
        self.load_cookies()

        # Initialize session if needed
        if not self._session_initialized:
            await self.async_init_session()

        # Submit credentials
        result = await self.async_submit_credentials(username, password)

        if result.tfa_required:
            if tfa_code_callback is None:
                raise TFARequiredError(
                    "TFA required but no callback provided",
                    reg_token=self._gigya_session.reg_token,
                    uid=self._gigya_session.uid,
                )

            # Get TFA options and select target
            targets = await self.async_get_tfa_options(preferred_provider)
            if not targets:
                raise GigyaError("No TFA targets available")

            # Use first target (or could let callback choose)
            target = targets[0]

            # Send verification code
            await self.async_send_tfa_code(target)

            # Get code from user
            code = await tfa_code_callback(target)

            # Verify code and complete login
            tokens = await self.async_verify_tfa_code(code)
        else:
            # No TFA required - get tokens directly
            tokens = await self._async_complete_login()

        # Save cookies for future TFA bypass
        self.save_cookies()

        return tokens

    # -------------------------------------------------------------------------
    # Step-by-step API (Home Assistant config flow usage)
    # -------------------------------------------------------------------------

    async def async_init_session(self) -> None:
        """Initialize session with WAF and Gigya cookies.

        Must be called before async_submit_credentials().
        """
        _LOGGER.debug("Initializing Gigya session")

        # Step 0: Load login page for WAF cookies
        await self._async_load_login_page()

        # Step 1: Bootstrap Gigya SDK
        await self._async_bootstrap()

        self._session_initialized = True

    async def async_submit_credentials(
        self,
        username: str,
        password: str,
    ) -> LoginResult:
        """Submit login credentials.

        Args:
            username: Dominion Energy email address.
            password: Account password.

        Returns:
            LoginResult indicating whether TFA is required.

        Raises:
            InvalidCredentialsError: Wrong username or password.
        """
        if not self._session_initialized:
            await self.async_init_session()

        _LOGGER.debug("Submitting credentials for %s", username)

        # Step 2: Login with credentials
        data = {
            "loginID": username,
            "password": password,
            "sessionExpiration": "31556952",
            "targetEnv": "jssdk",
            "include": (
                "profile,data,emails,subscriptions,preferences,id_token,groups,loginIDs,"
            ),
            "includeUserInfo": "true",
            "captchaToken": "0",
            "captchaType": "reCaptchaEnterpriseScore",
            "loginMode": "standard",
            "lang": "en",
            "APIKey": GIGYA_API_KEY,
            "source": "showScreenSet",
            "sdk": GIGYA_SDK_VERSION,
            "authMode": "cookie",
            "pageURL": LOGIN_URL,
            "sdkBuild": GIGYA_SDK_BUILD,
            "format": "json",
        }

        response = await self._async_gigya_post(GIGYA_LOGIN, data)
        error_code = response.get("errorCode", 0)

        if error_code == GIGYA_ERROR_TFA_PENDING:
            # TFA required - save tokens for TFA flow
            self._gigya_session.reg_token = response.get("regToken")
            self._gigya_session.uid = response.get("UID")
            self._gigya_session.id_token = response.get("id_token")

            _LOGGER.debug("TFA required, reg_token obtained")

            return LoginResult(
                success=False,
                tfa_required=True,
                reg_token=self._gigya_session.reg_token,
                uid=self._gigya_session.uid,
            )

        if error_code in (GIGYA_ERROR_INVALID_LOGIN, GIGYA_ERROR_INVALID_PASSWORD):
            raise InvalidCredentialsError(
                response.get("errorMessage", "Invalid credentials"),
                error_code=error_code,
                call_id=response.get("callId"),
            )

        if error_code != 0:
            raise GigyaError(
                response.get("errorMessage", f"Login failed: {error_code}"),
                error_code=error_code,
                call_id=response.get("callId"),
            )

        # Login succeeded without TFA
        session_info = response.get("sessionInfo", {})
        self._gigya_session.login_token = session_info.get("login_token")
        self._gigya_session.uid = response.get("UID")
        self._gigya_session.id_token = response.get("id_token")

        _LOGGER.debug("Login succeeded without TFA")

        return LoginResult(success=True, tfa_required=False)

    async def async_get_tfa_options(
        self,
        preferred_provider: TFAProvider | None = None,
    ) -> list[TFATarget]:
        """Get available TFA targets (phones or emails).

        Args:
            preferred_provider: If specified, only return targets for this provider.

        Returns:
            List of TFATarget objects.
        """
        if not self._gigya_session.reg_token:
            raise GigyaError("No reg_token - call async_submit_credentials first")

        # Step 3: Get available TFA providers
        params = {
            "regToken": self._gigya_session.reg_token,
            "APIKey": GIGYA_API_KEY,
            "sdk": GIGYA_SDK_VERSION,
            "pageURL": LOGIN_URL,
            "sdkBuild": GIGYA_SDK_BUILD,
            "format": "json",
        }

        response = await self._async_gigya_get(GIGYA_TFA_PROVIDERS, params)
        active_providers = response.get("activeProviders", [])

        _LOGGER.debug("Active TFA providers: %s", active_providers)

        targets: list[TFATarget] = []

        for provider_info in active_providers:
            provider_name = provider_info.get("name")

            if provider_name == "gigyaPhone":
                provider = TFAProvider.PHONE
            elif provider_name == "gigyaEmail":
                provider = TFAProvider.EMAIL
            else:
                continue

            if preferred_provider and provider != preferred_provider:
                continue

            # Step 4: Init TFA to get assertion
            assertion = await self._async_init_tfa(provider)

            # Step 5: Get registered targets for this provider
            if provider == TFAProvider.PHONE:
                provider_targets = await self._async_get_phone_targets(assertion)
            else:
                provider_targets = await self._async_get_email_targets(assertion)

            targets.extend(provider_targets)

        return targets

    async def async_send_tfa_code(self, target: TFATarget) -> None:
        """Send TFA verification code to the selected target.

        Args:
            target: The TFATarget to send the code to.
        """
        _LOGGER.debug("Sending TFA code to %s", target.obfuscated)

        # Store target for verification
        self._gigya_session.tfa_target = target

        # Need fresh assertion for sending code
        assertion = await self._async_init_tfa(target.provider)
        self._gigya_session.gigya_assertion = assertion

        if target.provider == TFAProvider.PHONE:
            # Step 6: Send phone verification code
            params = {
                "gigyaAssertion": assertion,
                "lang": "en",
                "phoneID": target.id,
                "method": target.last_method or "sms",
                "regToken": self._gigya_session.reg_token,
                "APIKey": GIGYA_API_KEY,
                "sdk": GIGYA_SDK_VERSION,
                "pageURL": LOGIN_URL,
                "sdkBuild": GIGYA_SDK_BUILD,
                "format": "json",
            }

            response = await self._async_gigya_get(GIGYA_TFA_SEND_PHONE, params)
            self._gigya_session.phv_token = response.get("phvToken")

        else:
            # Send email verification code
            params = {
                "gigyaAssertion": assertion,
                "lang": "en",
                "emailID": target.id,
                "regToken": self._gigya_session.reg_token,
                "APIKey": GIGYA_API_KEY,
                "sdk": GIGYA_SDK_VERSION,
                "pageURL": LOGIN_URL,
                "sdkBuild": GIGYA_SDK_BUILD,
                "format": "json",
            }

            await self._async_gigya_get(GIGYA_TFA_SEND_EMAIL, params)

        _LOGGER.debug("TFA code sent successfully")

    async def async_verify_tfa_code(self, code: str) -> TokenPair:
        """Verify TFA code and complete authentication.

        Args:
            code: The verification code entered by the user.

        Returns:
            TokenPair with Dominion API access and refresh tokens.

        Raises:
            TFAVerificationError: If code verification fails.
            TFAExpiredError: If the TFA assertion has expired.
        """
        _LOGGER.debug("Verifying TFA code")

        if not self._gigya_session.gigya_assertion:
            raise GigyaError("No gigya_assertion - call async_send_tfa_code first")

        # Step 7: Complete verification
        target = self._gigya_session.tfa_target

        if target and target.provider == TFAProvider.PHONE:
            # Phone verification
            params = {
                "gigyaAssertion": self._gigya_session.gigya_assertion,
                "phvToken": self._gigya_session.phv_token,
                "code": code,
                "regToken": self._gigya_session.reg_token,
                "APIKey": GIGYA_API_KEY,
                "sdk": GIGYA_SDK_VERSION,
                "pageURL": LOGIN_URL,
                "sdkBuild": GIGYA_SDK_BUILD,
                "format": "json",
            }

            response = await self._async_gigya_get(GIGYA_TFA_VERIFY_PHONE, params)
        else:
            # Email verification - requires emailID
            email_id = target.id if target else ""
            params = {
                "gigyaAssertion": self._gigya_session.gigya_assertion,
                "emailID": email_id,
                "code": code,
                "regToken": self._gigya_session.reg_token,
                "APIKey": GIGYA_API_KEY,
                "sdk": GIGYA_SDK_VERSION,
                "pageURL": LOGIN_URL,
                "sdkBuild": GIGYA_SDK_BUILD,
                "format": "json",
            }

            response = await self._async_gigya_get(GIGYA_TFA_VERIFY_EMAIL, params)

        error_code = response.get("errorCode", 0)

        if error_code == GIGYA_ERROR_INVALID_JWT:
            raise TFAExpiredError(
                "TFA session expired - please restart login",
                error_code=error_code,
                call_id=response.get("callId"),
            )

        if error_code != 0:
            raise TFAVerificationError(
                response.get("errorMessage", "TFA verification failed"),
                error_code=error_code,
                call_id=response.get("callId"),
            )

        provider_assertion = response.get("providerAssertion", "")
        assertion_len = len(provider_assertion) if provider_assertion else 0
        _LOGGER.debug("Got providerAssertion, length: %d", assertion_len)

        # Step 8: Finalize TFA
        await self._async_finalize_tfa(str(provider_assertion))

        # Step 8b: Finalize registration to get login_token and id_token
        await self._async_finalize_registration()

        # Complete login and get tokens
        return await self._async_complete_login()

    # -------------------------------------------------------------------------
    # Cookie management
    # -------------------------------------------------------------------------

    def save_cookies(self) -> None:
        """Save cookies to file for future TFA bypass."""
        if not self._cookie_file:
            return

        try:
            # Get cookies from session
            cookies = []
            for cookie in self._session.cookie_jar:
                cookie_data = {
                    "name": cookie.key,
                    "value": cookie.value,
                    "domain": cookie.get("domain", ""),
                    "path": cookie.get("path", "/"),
                }
                # Only save if it looks like a relevant cookie
                if any(
                    x in cookie.key
                    for x in ("gmid", "ucid", "incap", "visid", "nlbi", "gig_")
                ):
                    cookies.append(cookie_data)

            if cookies:
                self._cookie_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self._cookie_file, "w") as f:
                    json.dump({"version": 1, "cookies": cookies}, f, indent=2)
                _LOGGER.debug("Saved %d cookies to %s", len(cookies), self._cookie_file)

        except Exception as err:
            _LOGGER.warning("Failed to save cookies: %s", err)

    def load_cookies(self) -> bool:
        """Load cookies from file.

        Returns:
            True if cookies were loaded successfully.
        """
        if not self._cookie_file or not self._cookie_file.exists():
            return False

        try:
            with open(self._cookie_file) as f:
                data = json.load(f)

            cookies = data.get("cookies", [])
            for cookie_data in cookies:
                self._session.cookie_jar.update_cookies(
                    {cookie_data["name"]: cookie_data["value"]}
                )

            _LOGGER.debug("Loaded %d cookies from %s", len(cookies), self._cookie_file)
            return bool(cookies)

        except Exception as err:
            _LOGGER.warning("Failed to load cookies: %s", err)
            return False

    def export_cookies(self) -> dict[str, Any]:
        """Export cookies as a dictionary for storage.

        Returns:
            Dictionary with cookie data suitable for JSON serialization.
        """
        cookies = []
        for cookie in self._session.cookie_jar:
            if any(
                x in cookie.key
                for x in ("gmid", "ucid", "incap", "visid", "nlbi", "gig_")
            ):
                cookies.append({
                    "name": cookie.key,
                    "value": cookie.value,
                    "domain": cookie.get("domain", ""),
                })
        return {"version": 1, "cookies": cookies}

    def import_cookies(self, data: dict[str, Any]) -> bool:
        """Import cookies from a dictionary.

        Args:
            data: Dictionary with cookie data (from export_cookies).

        Returns:
            True if cookies were imported successfully.
        """
        try:
            cookies = data.get("cookies", [])
            for cookie_data in cookies:
                self._session.cookie_jar.update_cookies(
                    {cookie_data["name"]: cookie_data["value"]}
                )
            _LOGGER.debug("Imported %d cookies", len(cookies))
            return bool(cookies)
        except Exception as err:
            _LOGGER.warning("Failed to import cookies: %s", err)
            return False

    # -------------------------------------------------------------------------
    # Internal methods - Gigya API calls
    # -------------------------------------------------------------------------

    async def _async_load_login_page(self) -> None:
        """Step 0: Load login page to get WAF (Incapsula) cookies."""
        _LOGGER.debug("Loading login page for WAF cookies")

        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://myaccount.dominionenergy.com/",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            ),
        }

        try:
            async with self._session.get(LOGIN_URL, headers=headers) as response:
                # We don't need the content, just the cookies
                await response.read()
                _LOGGER.debug("WAF cookies obtained, status: %d", response.status)
        except Exception as err:
            raise CannotConnectError(f"Failed to load login page: {err}") from err

    async def _async_bootstrap(self) -> None:
        """Step 1: Bootstrap Gigya SDK to get Gigya cookies."""
        _LOGGER.debug("Bootstrapping Gigya SDK")

        params = {
            "apiKey": GIGYA_API_KEY,
            "pageURL": LOGIN_URL,
            "sdk": GIGYA_SDK_VERSION,
            "sdkBuild": GIGYA_SDK_BUILD,
            "format": "json",
        }

        await self._async_gigya_get(GIGYA_BOOTSTRAP, params)
        _LOGGER.debug("Gigya bootstrap complete")

    async def _async_init_tfa(self, provider: TFAProvider) -> str:
        """Step 4: Initialize TFA to get gigyaAssertion JWT.

        Args:
            provider: The TFA provider to initialize.

        Returns:
            The gigyaAssertion JWT (expires in ~5 minutes).
        """
        _LOGGER.debug("Initializing TFA for provider: %s", provider.value)

        params = {
            "provider": provider.value,
            "mode": "verify",
            "regToken": self._gigya_session.reg_token,
            "APIKey": GIGYA_API_KEY,
            "sdk": GIGYA_SDK_VERSION,
            "pageURL": LOGIN_URL,
            "sdkBuild": GIGYA_SDK_BUILD,
            "format": "json",
        }

        response = await self._async_gigya_get(GIGYA_TFA_INIT, params)
        return str(response.get("gigyaAssertion", ""))

    async def _async_get_phone_targets(self, assertion: str) -> list[TFATarget]:
        """Step 5a: Get registered phone numbers."""
        params = {
            "gigyaAssertion": assertion,
            "APIKey": GIGYA_API_KEY,
            "sdk": GIGYA_SDK_VERSION,
            "pageURL": LOGIN_URL,
            "sdkBuild": GIGYA_SDK_BUILD,
            "format": "json",
        }

        response = await self._async_gigya_get(GIGYA_TFA_PHONE_NUMBERS, params)

        targets = []
        for phone in response.get("phones", []):
            targets.append(
                TFATarget(
                    id=phone.get("id", ""),
                    obfuscated=phone.get("obfuscated", ""),
                    provider=TFAProvider.PHONE,
                    last_method=phone.get("lastMethod"),
                    last_verification=phone.get("lastVerification"),
                )
            )

        _LOGGER.debug("Found %d phone targets", len(targets))
        return targets

    async def _async_get_email_targets(self, assertion: str) -> list[TFATarget]:
        """Step 5b: Get registered emails."""
        params = {
            "gigyaAssertion": assertion,
            "APIKey": GIGYA_API_KEY,
            "sdk": GIGYA_SDK_VERSION,
            "pageURL": LOGIN_URL,
            "sdkBuild": GIGYA_SDK_BUILD,
            "format": "json",
        }

        response = await self._async_gigya_get(GIGYA_TFA_EMAILS, params)

        targets = []
        for email in response.get("emails", []):
            targets.append(
                TFATarget(
                    id=email.get("id", ""),
                    obfuscated=email.get("obfuscated", ""),
                    provider=TFAProvider.EMAIL,
                    last_verification=email.get("lastVerification"),
                )
            )

        _LOGGER.debug("Found %d email targets", len(targets))
        return targets

    async def _async_finalize_tfa(self, provider_assertion: str) -> None:
        """Step 8: Finalize TFA (marks TFA as complete)."""
        _LOGGER.debug("Finalizing TFA")
        gigya_len = len(self._gigya_session.gigya_assertion or "")
        _LOGGER.debug("gigyaAssertion length: %d", gigya_len)
        provider_len = len(provider_assertion) if provider_assertion else 0
        _LOGGER.debug("providerAssertion length: %d", provider_len)

        # finalizeTFA is a GET request with gigyaAssertion in params
        params = {
            "gigyaAssertion": self._gigya_session.gigya_assertion,
            "providerAssertion": provider_assertion,
            "tempDevice": "false",
            "regToken": self._gigya_session.reg_token,
            "APIKey": GIGYA_API_KEY,
            "source": "showScreenSet",
            "sdk": GIGYA_SDK_VERSION,
            "pageURL": LOGIN_URL,
            "sdkBuild": GIGYA_SDK_BUILD,
            "format": "json",
        }

        response = await self._async_gigya_get(GIGYA_TFA_FINALIZE, params)

        error_code = response.get("errorCode", 0)
        if error_code != 0:
            err_msg = response.get("errorMessage")
            _LOGGER.error("finalizeTFA failed: %d - %s", error_code, err_msg)
            _LOGGER.debug("finalizeTFA response: %s", response)

        _LOGGER.debug("TFA finalized successfully")

    async def _async_finalize_registration(self) -> None:
        """Step 8b: Finalize registration to get login_token and id_token."""
        _LOGGER.debug("Finalizing registration")

        params = {
            "regToken": self._gigya_session.reg_token,
            "targetEnv": "jssdk",
            "include": (
                "profile,data,emails,subscriptions,preferences,id_token,groups,loginIDs,"
            ),
            "includeUserInfo": "true",
            "APIKey": GIGYA_API_KEY,
            "source": "showScreenSet",
            "sdk": GIGYA_SDK_VERSION,
            "pageURL": LOGIN_URL,
            "sdkBuild": GIGYA_SDK_BUILD,
            "format": "json",
        }

        response = await self._async_gigya_get(GIGYA_FINALIZE_REGISTRATION, params)

        error_code = response.get("errorCode", 0)
        if error_code != 0:
            err_msg = response.get("errorMessage")
            _LOGGER.error("finalizeRegistration failed: %d - %s", error_code, err_msg)
            _LOGGER.debug("finalizeRegistration response: %s", response)

        session_info = response.get("sessionInfo", {})
        self._gigya_session.login_token = session_info.get("login_token")
        self._gigya_session.id_token = response.get("id_token")

        _LOGGER.debug("Registration finalized, login_token: %s, id_token: %s",
                      bool(self._gigya_session.login_token),
                      bool(self._gigya_session.id_token))

    async def _async_complete_login(self) -> TokenPair:
        """Complete login by getting fresh id_token and exchanging for API tokens."""
        # Use id_token from finalizeRegistration if available
        id_token = self._gigya_session.id_token

        if not id_token:
            # Fallback: Get account info for fresh id_token
            _LOGGER.debug("No id_token from registration, fetching from account info")
            id_token = await self._async_get_account_info()

        if not id_token:
            raise GigyaError("Failed to obtain id_token for token exchange")

        _LOGGER.debug("Using id_token for exchange (length: %d)", len(id_token))

        # Step 10: Exchange for Dominion API tokens
        return await self._async_exchange_token(id_token)

    async def _async_get_account_info(self) -> str:
        """Step 9: Get account info and fresh id_token."""
        _LOGGER.debug("Getting account info")
        has_token = bool(self._gigya_session.login_token)
        _LOGGER.debug("login_token available: %s", has_token)

        # Set login_token as cookie
        if self._gigya_session.login_token:
            self._session.cookie_jar.update_cookies(
                {f"glt_{GIGYA_API_KEY}": self._gigya_session.login_token}
            )

        data = {
            "include": "groups,profile,data,id_token,",
            "lang": "en",
            "APIKey": GIGYA_API_KEY,
            "sdk": GIGYA_SDK_VERSION,
            "login_token": self._gigya_session.login_token,
            "authMode": "cookie",
            "pageURL": LOGIN_URL,
            "sdkBuild": GIGYA_SDK_BUILD,
            "format": "json",
        }

        response = await self._async_gigya_post(GIGYA_ACCOUNT_INFO, data)

        error_code = response.get("errorCode", 0)
        if error_code != 0:
            err_msg = response.get("errorMessage")
            _LOGGER.error("getAccountInfo failed: %d - %s", error_code, err_msg)

        id_token = str(response.get("id_token", ""))

        if not id_token:
            keys = list(response.keys())
            _LOGGER.error("No id_token in getAccountInfo response. Keys: %s", keys)
        else:
            _LOGGER.debug("Fresh id_token obtained (length: %d)", len(id_token))

        return id_token

    async def _async_exchange_token(self, id_token: str) -> TokenPair:
        """Step 10: Exchange Gigya id_token for Dominion API tokens."""
        _LOGGER.debug("Exchanging Gigya token for Dominion API tokens")

        url = f"{BASE_URL}{ENDPOINT_LOGIN}"
        headers = {
            **DEFAULT_HEADERS,
            "Authorization": f"Bearer {id_token}",
            "e2eid": str(uuid.uuid4()),
            "pt": "",
            "st": "PL",
        }
        payload = {
            "username": "",
            "password": "",
            "guestToken": id_token,
            "customattributes": {
                "client": "",
                "version": "",
                "deviceId": "",
                "deviceName": "",
                "os": "",
            },
        }

        try:
            async with self._session.post(
                url, headers=headers, json=payload
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    _LOGGER.error(
                        "Token exchange HTTP error: %d - %s", response.status, text
                    )
                    raise GigyaError(
                        f"Token exchange failed: {response.status} - {text}"
                    )

                data = await response.json()
                _LOGGER.debug("Token exchange response: %s", data)

        except GigyaError:
            raise
        except Exception as err:
            raise CannotConnectError(f"Token exchange failed: {err}") from err

        status = data.get("status", {})
        if status.get("code") != 200:
            _LOGGER.error("Token exchange API error: %s", data)
            raise GigyaError(
                f"Token exchange failed: {status.get('message', 'Unknown error')}"
            )

        token_data = data.get("data", {})
        access_token = token_data.get("accessToken", "")
        refresh_token = token_data.get("refreshToken", "")

        if not access_token or not refresh_token:
            _LOGGER.error("Token exchange missing tokens. Response: %s", data)
            raise GigyaError(
                f"Token exchange response missing tokens. "
                f"Got keys: {list(token_data.keys()) if token_data else 'no data'}"
            )

        _LOGGER.debug("Dominion API tokens obtained")

        return TokenPair(access_token=access_token, refresh_token=refresh_token)

    # -------------------------------------------------------------------------
    # Internal methods - HTTP helpers
    # -------------------------------------------------------------------------

    async def _async_gigya_get(
        self,
        endpoint: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Make a GET request to the Gigya API."""
        url = f"{GIGYA_BASE_URL}{endpoint}"

        try:
            async with self._session.get(url, params=params) as response:
                # Gigya may return text/javascript instead of application/json
                data: dict[str, Any] = await response.json(content_type=None)

                error_code = data.get("errorCode", 0)
                if error_code != 0 and error_code != GIGYA_ERROR_TFA_PENDING:
                    _LOGGER.debug(
                        "Gigya GET %s returned error %d: %s",
                        endpoint,
                        error_code,
                        data.get("errorMessage"),
                    )

                return data

        except Exception as err:
            raise CannotConnectError(f"Gigya request failed: {err}") from err

    async def _async_gigya_post(
        self,
        endpoint: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Make a POST request to the Gigya API."""
        url = f"{GIGYA_BASE_URL}{endpoint}"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        try:
            async with self._session.post(url, data=data, headers=headers) as response:
                # Gigya may return text/javascript instead of application/json
                result: dict[str, Any] = await response.json(content_type=None)

                error_code = result.get("errorCode", 0)
                if error_code != 0 and error_code != GIGYA_ERROR_TFA_PENDING:
                    _LOGGER.debug(
                        "Gigya POST %s returned error %d: %s",
                        endpoint,
                        error_code,
                        result.get("errorMessage"),
                    )

                return result

        except Exception as err:
            raise CannotConnectError(f"Gigya request failed: {err}") from err
