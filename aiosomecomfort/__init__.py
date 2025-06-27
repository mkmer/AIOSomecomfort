from __future__ import annotations
import datetime
import logging
import urllib.parse as urllib
import aiohttp
from yarl import URL
from .location import Location
from .exceptions import *


_LOG = logging.getLogger("somecomfort")

AUTH_COOKIE = ".ASPXAUTH_TRUEHOME"
DOMAIN = "www.mytotalconnectcomfort.com"
MIN_LOGIN_TIME = datetime.timedelta(minutes=10)
MAX_LOGIN_ATTEMPTS = 3


def _convert_errors(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)

        except aiohttp.ClientError as ex:
            _LOG.error("Connection Timeout")
            raise ConnectionError("Connection Timeout") from ex

    return wrapper


class AIOSomeComfort(object):
    """AIOSomeComfort API Class."""

    def __init__(
        self,
        username: str | None,
        password: str | None,
        timeout=30,
        session: aiohttp.ClientSession = None,
    ) -> None:
        self._username = username  # = username
        self._password = password  # password
        self._session = session
        self._timeout = timeout
        self._headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "*/*",
            "Connection": "keep-alive",
            "Accept-Encoding": "gzip, deflate",
        }
        self._locations = {}
        self._baseurl = f"https://{DOMAIN}"
        self._null_cookie_count = 0
        self._next_login = datetime.datetime.now(datetime.timezone.utc)
        self._counter = 1700000000000 # sequnce for polling

    @property
    def next_login(self) -> datetime:
        """Return next allowed login time for rate limit."""
        return self._next_login

    def _set_null_count(self) -> None:
        """Set null cookie count and retry timout."""

        self._null_cookie_count += 1
        if self._null_cookie_count >= MAX_LOGIN_ATTEMPTS:
            self._next_login = datetime.datetime.now(datetime.timezone.utc) + MIN_LOGIN_TIME

    @_convert_errors
    async def login(self) -> None:
        """Login to Honeywell API."""
        url = f"{self._baseurl}/portal"
        params = {
            "timeOffset": "480",
            "UserName": self._username,
            "Password": self._password,
            "RememberMe": "false",
        }
        self._headers["Content-Type"] = "application/x-www-form-urlencoded"
        # can't use params because AIOHttp doesn't URL encode like API expects (%40 for @)
        url = URL(f"{url}?{urllib.urlencode(params)}", encoded=True)

        if self._next_login > datetime.datetime.now(datetime.timezone.utc):
            raise APIRateLimited(f"Rate limit on login: Waiting {MIN_LOGIN_TIME}")

        resp = await self._session.post(
            url, timeout=self._timeout, headers=self._headers
        )
        _LOG.debug("Login Response %s", await resp.text())
        # The TUREHOME cookie is malformed in some way - need to clear the expiration to make it work with AIOhttp
        cookies = resp.cookies
        if AUTH_COOKIE in cookies:
            cookies[AUTH_COOKIE]["expires"] = ''
            self._session.cookie_jar.update_cookies(cookies=cookies, response_url=URL(resp.host) )

        if resp.status == 401:
            # This never seems to happen currently, but
            # I'll leave it here in case they start doing the
            # right thing.
            _LOG.error("Login as %s failed", self._username)
            self._set_null_count()

            raise AuthError(f"Login as %s failed {self._username}")

        elif resp.status != 200:
            _LOG.error("Connection error %s", resp.status)
            raise ConnectionError(f"Connection error {resp.status}")

        self._headers["Content-Type"] = "application/json"
        resp2: aiohttp.ClientResponse = await self._session.get(
            f"{self._baseurl}/portal", timeout=self._timeout, headers=self._headers
        )  # this should redirect if we're logged in

        # if we get null cookies for this, the login has failed.
        if AUTH_COOKIE in resp2.cookies and resp2.cookies[AUTH_COOKIE].value == "":
            _LOG.error("Login null cookie - site may be down")
            self._set_null_count()

            raise AuthError(f"Null cookie connection error {resp2.status}")

        if resp2.status == 401:
            _LOG.error(
                "Login as %s failed - Unauthorized %s",
                self._username,
                resp2.status,
            )

            self._set_null_count()

            raise AuthError(
                f"Login as %s failed - Unauthorized {self._username}, {resp2.status}"
            )

        if resp2.status != 200:
            _LOG.error("Connection error %s", resp2.status)
            raise ConnectionError(f"Connection error {resp2.status}")

    @_convert_errors
    async def logoff(self) -> None:
        """Login to Honeywell API."""
        url = f"{self._baseurl}/portal/Account/LogOff"
        resp = await self._session.post(
            url, timeout=self._timeout, headers=self._headers
        )
        _LOG.debug("LogOff Response %s", await resp.text())


    async def _request_json(self, method: str, *args, **kwargs) -> str | None:
        if "timeout" not in kwargs:
            kwargs["timeout"] = self._timeout
        kwargs["headers"] = self._headers
        resp: aiohttp.ClientResponse = await getattr(self._session, method)(
            *args, **kwargs
        )

        # Check again for the deformed cookie
        # API sends a null cookie if really want it to expire
        cookies = resp.cookies
        if AUTH_COOKIE in cookies:
            cookies[AUTH_COOKIE]["expires"] = ''
            self._session.cookie_jar.update_cookies(cookies=cookies, response_url=URL(resp.host) )

        req = args[0].replace(self._baseurl, "")
        _LOG.debug("request json response %s with payload %s", resp, await resp.text())
        if resp.status == 200 and (resp.content_type in ["application/json","application/octet-stream"]):
            self._null_cookie_count = 0
            if resp.content_type == "application/json":
                return await resp.json()
            return resp

        if resp.status == 401:
            _LOG.error("401 Error at update (Key expired?).")
            raise UnauthorizedError("401 Error at update (Key Expired?).")

        if resp.status == 403:
            _LOG.error("403 Error at update (Key expired?).")
            raise UnauthorizedError("403 Error at update (Key Expired?).")

        if resp.status in [500,502,503] or len(resp.history) > 0:
            _LOG.error("Service Unavailable %s, %s.", resp.status, resp.history)
            raise ConnectionError(f"Service Unavailable {resp.status}.")

        # Some other non 200 status or 200 but not json.
        _LOG.info("API returned %s from %s request", resp.status, req)
        _LOG.debug("request json response %s with payload %s", resp, await resp.text())
        raise UnexpectedResponse(f"API returned {resp.status}, {req}")

    async def _get_json(self, *args, **kwargs) -> str | None:
        return await self._request_json("get", *args, **kwargs)

    async def _post_json(self, *args, **kwargs) -> str | None:
        return await self._request_json("post", *args, **kwargs)

    async def _get_locations(self) -> list:
        json_responses: list = []
        url = f"{self._baseurl}/portal/Location/GetLocationListData/"
        for page in range(1, 5):  # pages 1 - 4
            params = {"page": page, "filter": ""}
            resp = await self._session.post(url, params=params, headers=self._headers)
            if resp.content_type == "application/json":
                json_responses.extend(await resp.json())
            cookies = resp.cookies
            if AUTH_COOKIE in cookies:
                cookies[AUTH_COOKIE]["expires"] = ""
                self._session.cookie_jar.update_cookies(cookies=cookies)
        if len(json_responses) > 0:
            return json_responses
        return None

    async def get_data(self,thermostat_id: str) -> str:
        """Get device total data structure."""
        url = f"{self._baseurl}/portal/Device/Menu/GetData?deviceID={thermostat_id}"
        return await self._post_json(url)

    async def get_thermostat_data(self, thermostat_id: str) -> str:
        """Get thermostat data from API"""
        url = f"{self._baseurl}/portal/Device/CheckDataSession/{thermostat_id}?_={self._counter}"
        self._counter+=1
        return await self._get_json(url)

    async def get_humidifier_data(self, thermostat_id: str) -> str:
        """Get thermostat data from API"""
        url = f"{self._baseurl}/portal/Device/Menu/GetHumData/{thermostat_id}"
        return await self._post_json(url)

    async def get_dehumidifier_data(self, thermostat_id: str) -> str:
        """Get thermostat data from API"""
        url = f"{self._baseurl}/portal/Device/Menu/GetDehumData/{thermostat_id}"
        return await self._post_json(url)

    async def set_thermostat_settings(
        self, thermostat_id: str, settings: dict[str, str]
    ) -> None:
        """Set thermostat settings from a dict."""
        data = {
            "DeviceID": thermostat_id,
            "SystemSwitch": None,
            "HeatSetpoint": None,
            "CoolSetpoint": None,
            "HeatNextPeriod": None,
            "CoolNextPeriod": None,
            "StatusHeat": None,
            "StatusCool": None,
            "FanMode": None,
        }

        data.update(settings)
        _LOG.debug("Sending Data: %s", data)
        url = f"{self._baseurl}/portal/Device/SubmitControlScreenChanges"
        result = await self._post_json(url, json=data)
        _LOG.debug("Received setting response %s", result)
        if result is None or result.get("success") != 1:
            raise APIError("API rejected thermostat settings")

    @_convert_errors
    async def discover(self) -> None:
        """Discover devices on the account."""
        raw_locations = await self._get_locations()
        if raw_locations is not None:
            for raw_location in raw_locations:
                try:
                    location = await Location.from_api_response(self, raw_location)
                except KeyError as ex:
                    _LOG.exception(
                        "Failed to process location `%s`: missing %s element"
                        , raw_location.get("LocationID", "unknown"), ex.args[0]
                    )
                self._locations[location.locationid] = location

    @property
    def locations_by_id(self) -> dict:
        """A dict of all locations indexed by id"""
        return self._locations

    @property
    def default_device(self) -> str | None:
        """This is the first device found.

        It is only useful if the account has only one device and location
        in your account (which is pretty common). It is None if there
        are no devices in the account.
        """
        for location in self.locations_by_id.values():
            for device in location.devices_by_id.values():
                return device
        return None

    def get_device(self, device_id: str) -> str | None:
        """Find a device by id.

        :returns: None if not found.
        """
        for location in self.locations_by_id.values():
            for ident, device in location.devices_by_id.items():
                if ident == device_id:
                    return device
