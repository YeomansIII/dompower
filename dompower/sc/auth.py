"""Authentication for Dominion Energy South Carolina accounts.

The SC portal login has these steps:

1. Load the login page and read the ``__RequestVerificationToken``.
2. POST the username and password to ``Authenticate``. A valid login
   always returns ``status: "twoFA"``.
3. If there is a saved TFA token, send it to ``Verify2FAToken``. If the
   server accepts it, TFA is complete. If not, do interactive TFA:
   ``InitAuthentication`` (options), ``SendPINCode``, ``VerifyPIN``.
   ``VerifyPIN`` returns a new TFA token that can be saved.
4. Load the home page again for a new verification token. Then read the
   account and exchange a portal payload for a Bidgely bearer token.

The portal uses session cookies. Use a dedicated ``aiohttp.ClientSession``
with ``aiohttp.CookieJar(quote_cookie=False)``. See ``create_sc_cookie_jar``.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import aiohttp

from ..exceptions import (
    ApiError,
    InvalidAuthError,
    InvalidCredentialsError,
    TFARequiredError,
    TFAVerificationError,
)
from ..models import LoginResult, MeterType, TFAProvider, TFATarget
from ._http import (
    ajax_headers,
    async_request_text,
    bidgely_headers,
    cache_buster,
    decode_json,
    page_headers,
)
from .const import (
    BIDGELY_CLIENT_ID,
    BIDGELY_PILOT_ID,
    BIDGELY_URL,
    BIDGELY_WC_SESSION,
    MEASUREMENT_ELECTRIC,
    MEASUREMENT_GAS,
    SC_ACCOUNT_LISTING,
    SC_AUTH_STATUS_FAILED,
    SC_AUTH_STATUS_TFA,
    SC_AUTHENTICATE,
    SC_BIDGELY_SDK_INIT,
    SC_HOME_PAGE,
    SC_INIT_ACCOUNT,
    SC_INIT_AUTHENTICATION,
    SC_LOGIN_PAGE,
    SC_PORTAL_URL,
    SC_SEND_PIN_CODE,
    SC_VERIFY_2FA_TOKEN,
    SC_VERIFY_PIN,
)
from .models import SCAccount, SCSession

if TYPE_CHECKING:
    from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)

_VERIFICATION_TOKEN_RE = re.compile(
    r'name="__RequestVerificationToken"[^>]*?value="([^"]+)"'
)
_LOGIN_REFERER = f"{SC_PORTAL_URL}{SC_LOGIN_PAGE}"
_HOME_REFERER = f"{SC_PORTAL_URL}{SC_HOME_PAGE}"

_MEASUREMENT_TO_METER_TYPE = {
    MEASUREMENT_ELECTRIC: MeterType.ELECTRIC,
    MEASUREMENT_GAS: MeterType.GAS,
}


def create_sc_cookie_jar() -> aiohttp.CookieJar:
    """Return a cookie jar that works with the SC portal.

    SC cookie values contain characters such as ``=``. The default jar quotes
    these values, and the portal then rejects the session.
    """
    return aiohttp.CookieJar(quote_cookie=False)


def find_verification_token(html: str) -> str:
    """Return the ``__RequestVerificationToken`` value from a portal page.

    Raises:
        ApiError: If the page does not contain the token.
    """
    match = _VERIFICATION_TOKEN_RE.search(html)
    if not match:
        raise ApiError(
            "Could not find __RequestVerificationToken on the SC portal page"
        )
    return match.group(1)


def _data(payload: dict[str, Any], url: str) -> Any:
    """Return the "data" member of a fusionapi response."""
    if "data" not in payload:
        raise ApiError(f"Response from {url} has no data member")
    return payload["data"]


def _data_object(payload: dict[str, Any], url: str) -> dict[str, Any]:
    """Return the "data" member of a fusionapi response that must be an object.

    Raises:
        ApiError: If "data" is missing or is not a JSON object.
    """
    data = _data(payload, url)
    if not isinstance(data, dict):
        raise ApiError(
            f"Response from {url} has a data member that is not an object "
            f"({type(data).__name__})"
        )
    return data


class SCAuthenticator:
    """Log in to a Dominion Energy SC account.

    The step methods have the same names as ``GigyaAuthenticator``, so a UI
    can use one flow for both regions:

    - ``async_submit_credentials``
    - ``async_get_tfa_options``
    - ``async_send_tfa_code``
    - ``async_verify_tfa_code`` (returns the new TFA token)
    - ``async_complete_login`` (returns an ``SCSession``)

    Example:
        auth = SCAuthenticator(session)
        result = await auth.async_submit_credentials(user, password, tfa_token)
        if result.tfa_required:
            targets = await auth.async_get_tfa_options()
            await auth.async_send_tfa_code(targets[0])
            tfa_token = await auth.async_verify_tfa_code(input("Code: "))
        sc_session = await auth.async_complete_login()
    """

    def __init__(
        self,
        session: ClientSession,
        *,
        pilot_id: str = BIDGELY_PILOT_ID,
    ) -> None:
        """Initialize the authenticator.

        Args:
            session: aiohttp session. Use a dedicated session with
                ``create_sc_cookie_jar()``, because the portal uses cookies.
            pilot_id: Bidgely pilot ID override.
        """
        self._session = session
        self._pilot_id = pilot_id
        self._verification_token: str | None = None
        self._tfa_token: str | None = None
        self._tfa_complete = False

    @property
    def tfa_token(self) -> str | None:
        """Return the TFA token that the last login used or received."""
        return self._tfa_token

    async def async_login(
        self,
        username: str,
        password: str,
        tfa_token: str | None = None,
    ) -> SCSession:
        """Log in without user interaction.

        Raises:
            InvalidCredentialsError: If the username or password is wrong.
            TFARequiredError: If there is no valid TFA token.
        """
        result = await self.async_submit_credentials(username, password, tfa_token)
        if result.tfa_required:
            raise TFARequiredError("SC login requires a new TFA verification")
        return await self.async_complete_login()

    async def async_submit_credentials(
        self,
        username: str,
        password: str,
        tfa_token: str | None = None,
    ) -> LoginResult:
        """Submit the username and password, and try the saved TFA token.

        Returns:
            LoginResult. ``tfa_required`` is True if interactive TFA is needed.

        Raises:
            InvalidCredentialsError: If the username or password is wrong.
            InvalidAuthError: If the portal returns an unknown login status.
            ApiError: If the response has an unexpected format.
        """
        self._tfa_complete = False
        self._tfa_token = None

        login_page = await async_request_text(
            self._session,
            "GET",
            _LOGIN_REFERER,
            headers=page_headers(),
        )
        self._verification_token = find_verification_token(login_page)

        url = f"{SC_PORTAL_URL}{SC_AUTHENTICATE}"
        payload = decode_json(
            await async_request_text(
                self._session,
                "POST",
                url,
                headers=self._login_ajax_headers(),
                json_data={"userName": username, "password": password, "_df": ""},
            ),
            url,
        )
        status = _data_object(payload, url).get("status")
        if status == SC_AUTH_STATUS_FAILED:
            raise InvalidCredentialsError("Invalid SC username or password")
        if status != SC_AUTH_STATUS_TFA:
            # Every valid SC login seen so far asks for TFA. Stop here instead
            # of guessing what another status means.
            raise InvalidAuthError(f"Unexpected SC login status: {status!r}")

        if tfa_token and await self._async_verify_saved_token(tfa_token):
            self._tfa_token = tfa_token
            self._tfa_complete = True
            return LoginResult(success=True, tfa_required=False)

        return LoginResult(
            success=False,
            tfa_required=True,
            tfa_providers=[TFAProvider.PHONE, TFAProvider.EMAIL],
        )

    async def async_get_tfa_options(
        self,
        preferred_provider: TFAProvider | None = None,
    ) -> list[TFATarget]:
        """Return the phone numbers and email addresses for TFA.

        Args:
            preferred_provider: If set, return only targets of this type.

        Raises:
            ApiError: If the response has an unexpected format.
        """
        url = f"{SC_PORTAL_URL}{SC_INIT_AUTHENTICATION}"
        payload = decode_json(
            await async_request_text(
                self._session,
                "GET",
                url,
                headers=self._login_ajax_headers(),
                params={"_": cache_buster()},
            ),
            url,
        )
        try:
            user_info = _data_object(payload, url)["userInfo"]
            phones = user_info.get("phoneNumbers") or []
            emails = user_info.get("emailAddresses") or []
        except (KeyError, TypeError, AttributeError) as err:
            raise ApiError("Unexpected SC InitAuthentication response") from err
        # A string is iterable, so check the type. Each character of a
        # string would otherwise become a TFA target.
        if not isinstance(phones, list) or not isinstance(emails, list):
            raise ApiError("Unexpected SC InitAuthentication response")

        targets = [
            TFATarget(id=str(phone), obfuscated=str(phone), provider=TFAProvider.PHONE)
            for phone in phones
        ] + [
            TFATarget(id=str(email), obfuscated=str(email), provider=TFAProvider.EMAIL)
            for email in emails
        ]
        if preferred_provider is not None:
            targets = [t for t in targets if t.provider == preferred_provider]
        return targets

    async def async_send_tfa_code(self, target: TFATarget) -> None:
        """Send a TFA code to the selected phone number or email address.

        Raises:
            ApiError: If the portal does not accept the request.
        """
        url = f"{SC_PORTAL_URL}{SC_SEND_PIN_CODE}"
        payload = decode_json(
            await async_request_text(
                self._session,
                "POST",
                url,
                headers=self._login_ajax_headers(),
                json_data={"sendMethod": target.id, "_df": ""},
            ),
            url,
        )
        if not _data(payload, url):
            raise ApiError("SC portal did not send the TFA code")

    async def async_verify_tfa_code(self, code: str) -> str:
        """Verify the TFA code and register this device.

        Returns:
            The new TFA token. Save it and pass it to later logins to skip TFA.

        Raises:
            TFAVerificationError: If the code is wrong.
            ApiError: If the response has an unexpected format.
        """
        url = f"{SC_PORTAL_URL}{SC_VERIFY_PIN}"
        payload = decode_json(
            await async_request_text(
                self._session,
                "POST",
                url,
                headers=self._login_ajax_headers(),
                json_data={"PINcode": code, "registerDevice": True, "_df": ""},
            ),
            url,
        )
        data = _data(payload, url) or {}
        if not isinstance(data, dict):
            raise ApiError(f"Unexpected SC VerifyPIN response ({type(data).__name__})")
        if not data.get("isVerified"):
            raise TFAVerificationError("SC TFA code is not correct")
        token = data.get("token")
        if not token:
            raise ApiError("SC VerifyPIN did not return a TFA token")
        self._tfa_token = str(token)
        self._tfa_complete = True
        return self._tfa_token

    async def async_complete_login(self) -> SCSession:
        """Finish the login after TFA and get a Bidgely bearer token.

        Raises:
            InvalidAuthError: If TFA is not complete.
            ApiError: If the account has more than one service account, or
                if a response has an unexpected format.
        """
        if not self._tfa_complete:
            raise InvalidAuthError("SC TFA is not complete")

        home_page = await async_request_text(
            self._session,
            "GET",
            _HOME_REFERER,
            headers=page_headers(referer=_LOGIN_REFERER),
        )
        headers = ajax_headers(find_verification_token(home_page), _HOME_REFERER)

        url = f"{SC_PORTAL_URL}{SC_ACCOUNT_LISTING}"
        listing = _data_object(
            decode_json(
                await async_request_text(
                    self._session,
                    "GET",
                    url,
                    headers=headers,
                    params={"_": cache_buster()},
                ),
                url,
            ),
            url,
        )
        if listing.get("singleAccount") is not True:
            raise ApiError(
                "This SC login has more than one account. "
                "Multiple SC accounts are not supported yet."
            )

        url = f"{SC_PORTAL_URL}{SC_INIT_ACCOUNT}"
        account_data = _data_object(
            decode_json(
                await async_request_text(
                    self._session,
                    "GET",
                    url,
                    headers=headers,
                    params={"_": cache_buster()},
                ),
                url,
            ),
            url,
        )
        try:
            service_address = str(account_data["account"]["serviceAddressAndAccountNo"])
        except (KeyError, TypeError) as err:
            raise ApiError("Unexpected SC InitAccount response") from err

        url = f"{SC_PORTAL_URL}{SC_BIDGELY_SDK_INIT}"
        sdk_init = _data_object(
            decode_json(
                await async_request_text(
                    self._session,
                    "GET",
                    url,
                    headers=headers,
                    params={
                        "service": "E",
                        "serviceAccountType": "R",
                        "_": cache_buster(),
                    },
                ),
                url,
            ),
            url,
        )
        try:
            encrypted_data = sdk_init["payload"]
        except (KeyError, TypeError) as err:
            raise ApiError("Unexpected SC GetBidgelySDKInit response") from err

        url = f"{BIDGELY_URL}{BIDGELY_WC_SESSION}"
        wc_session = decode_json(
            await async_request_text(
                self._session,
                "POST",
                url,
                headers=bidgely_headers(pilot_id=self._pilot_id),
                json_data={
                    "clientId": BIDGELY_CLIENT_ID,
                    "encryptedData": encrypted_data,
                },
            ),
            url,
        )
        try:
            wc_payload = wc_session["payload"]
            access_token = str(wc_payload["tokenDetails"]["accessToken"])
            user_id = str(wc_payload["userProfileDetails"]["userId"])
            mappings = wc_payload["userTypeDetails"]["measurementToUserTypeMappings"]
            meter_types = [
                _MEASUREMENT_TO_METER_TYPE[m["measurementType"]]
                for m in mappings
                if m.get("measurementType") in _MEASUREMENT_TO_METER_TYPE
            ]
        except (KeyError, TypeError, AttributeError) as err:
            raise ApiError("Unexpected Bidgely wc-session response") from err

        _LOGGER.debug("SC login complete, meter types: %s", meter_types)
        return SCSession(
            access_token=access_token,
            account=SCAccount(
                user_id=user_id,
                service_address=service_address,
                meter_types=list(dict.fromkeys(meter_types)),
            ),
        )

    def _login_ajax_headers(self) -> dict[str, str]:
        """Return AJAX headers for the login and TFA steps."""
        if self._verification_token is None:
            raise InvalidAuthError("Call async_submit_credentials first")
        return ajax_headers(
            self._verification_token, _LOGIN_REFERER, include_origin=True
        )

    async def _async_verify_saved_token(self, tfa_token: str) -> bool:
        """Return True if the portal accepts a saved TFA token.

        Only ``"data": true`` accepts the token. Any other answer, a 4xx
        response, or a body that is not as expected means TFA is needed.
        Server errors (5xx) and connection errors still raise, because a new
        TFA code does not help when the portal is down.
        """
        url = f"{SC_PORTAL_URL}{SC_VERIFY_2FA_TOKEN}"
        try:
            payload = decode_json(
                await async_request_text(
                    self._session,
                    "GET",
                    url,
                    headers=self._login_ajax_headers(),
                    params={"token": tfa_token, "_": cache_buster()},
                ),
                url,
            )
        except (ApiError, InvalidAuthError) as err:
            _LOGGER.debug("Could not verify the saved TFA token: %s", err)
            return False
        accepted = payload.get("data") is True
        if not accepted:
            _LOGGER.debug("SC portal did not accept the saved TFA token")
        return accepted
