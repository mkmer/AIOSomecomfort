import __future__
import aiohttp
import datetime
import logging
from yarl import URL
import urllib.parse as urllib
from .location import Location
from .device import (
    AuthError,
    ConnectionError,
    SomeComfortError,
    APIRateLimited,
    ServiceUnavailable,
    ConnectionTimeout,
    APIError,
)


_LOG = logging.getLogger("somecomfort")


def _convert_errors(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)

        except aiohttp.ClientError:
            _LOG.error("Connection Timeout")
            raise ConnectionError

    return wrapper


class AIOSomeComfort(object):
    def __init__(self, username, password, timeout=30, session=None):
        self._username: str | None = username  # = username
        self._password: str | None = password  # password
        self._session: aiohttp.ClientSession = session
        self._timeout = timeout
        self._headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "*/*",
            "Connection": "keep-alive",
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        self._locations = {}
        self._baseurl = "https://www.mytotalconnectcomfort.com"
        self._default_url = self._baseurl

    @_convert_errors
    async def login(self):
        url = f"{self._baseurl}/portal"
        params = {
            "timeOffset": "480",
            "UserName": self._username,
            "Password": self._password,
            "RememberMe": "false",
        }
        # can't use params because AIOHttp doesn't URL encode like API expects (%40 for @)
        url = URL(f"{url}?{urllib.urlencode(params)}", encoded=True)
        resp = await self._session.post(
            url, timeout=self._timeout, headers=self._headers
        )

        # The TUREHOME cookie is malformed in some way - need to clear the expiration to make it work with AIOhttp
        cookies = resp.cookies
        if ".ASPXAUTH_TRUEHOME" in cookies:
            cookies[".ASPXAUTH_TRUEHOME"]["expires"] = ""
            self._session.cookie_jar.update_cookies(cookies=cookies)

        if resp.status != 200:
            # This never seems to happen currently, but
            # I'll leave it here in case they start doing the
            # right thing.
            _LOG.error(f"Login as {self._username} failed")
            raise AuthError(f"Login as {self._username} failed")
        self._headers.pop("Content-Type")
        resp2 = await self._session.get(
            f"{self._baseurl}/portal", timeout=self._timeout, headers=self._headers
        )  # this should redirect if we're logged in

        # if we get null cookies for this, the login has failed.
        if (
            ".ASPXAUTH_TRUEHOME" in resp2.cookies
            and resp2.cookies[".ASPXAUTH_TRUEHOME"].value == ""
        ):
            _LOG.error(f"Login as {self._username} failed - null cookie")
            raise AuthError(f"Login as {self._username} failed - null cookie")

    @staticmethod
    async def _resp_json(resp, req):
        try:
            return await resp.json()
        except:
            # Any error doing this is probably because we didn't
            # get JSON back (the API is terrible about this).
            _LOG.exception(f"Failed to de-JSON {req} {resp}")

    async def _request_json(self, method, *args, **kwargs):
        if "timeout" not in kwargs:
            kwargs["timeout"] = self._timeout
        kwargs["headers"] = self._headers
        resp = await getattr(self._session, method)(*args, **kwargs)

        # Check again for the deformed cookie
        # API sends a null cookie if really want it to expire
        cookies = resp.cookies
        if ".ASPXAUTH_TRUEHOME" in cookies:
            cookies[".ASPXAUTH_TRUEHOME"]["expires"] = ""
            self._session.cookie_jar.update_cookies(cookies=cookies)

        req = args[0].replace(self._baseurl, "")

        if resp.status == 200:
            return await self._resp_json(resp, req)
        elif resp.status == 401:
            _LOG.error("API Rate Limited at login.")
            raise APIRateLimited("API Rate Limited at login.")

        elif resp.status == 503:
            _LOG.error("Service Unavailable.")
            raise ConnectionError("Service Unavailable.")
        else:
            _LOG.error(f"API returned {resp.status} from {req} request")
            raise SomeComfortError(f"API returned {resp.status} from {req} request")

    def _get_json(self, *args, **kwargs):
        return self._request_json("get", *args, **kwargs)

    async def _post_json(self, *args, **kwargs):
        return await self._request_json("post", *args, **kwargs)

    async def _get_locations(self):
        url = f"{self._baseurl}/portal/Location/GetLocationListData/"
        params = {"page": 1, "filter": ""}
        resp = await self._session.post(url, params=params, headers=self._headers)
        if resp.content_type == "application/json":
            return await resp.json()
        return None

    async def _get_thermostat_data(self, thermostat_id):
        url = f"{self._baseurl}/portal/Device/CheckDataSession/{thermostat_id}"
        return await self._get_json(url)

    async def _set_thermostat_settings(self, thermostat_id, settings):
        data = {
            "SystemSwitch": None,
            "HeatSetpoint": None,
            "CoolSetpoint": None,
            "HeatNextPeriod": None,
            "CoolNextPeriod": None,
            "StatusHeat": None,
            "DeviceID": thermostat_id,
        }
        data.update(settings)
        url = f"{self._baseurl}/portal/Device/SubmitControlScreenChanges"
        result = await self._post_json(url, data=data)
        if result.get("success") != 1:
            raise APIError("API rejected thermostat settings")

    async def keepalive(self):
        """Makes a keepalive request to avoid session timeout.

        Raises SessionTimedOut if the session has timed out.
        """
        url = URL(f"{self._baseurl}/portal", encoded=True)

        try:
            resp = await self._session.get(
                url, timeout=self._timeout, headers=self._headers
            )
        except aiohttp.ClientConnectionError:
            _LOG.error("Connection Error occurred.")
            raise ConnectionError()
        except aiohttp.ClientError:
            _LOG.error("Connection Timed out.")
            raise ConnectionTimeout("Connection Timed out.")
        except Exception as exp:
            _LOG.exception(f"Unexpected Connection Error. {exp}")
            raise SomeComfortError(f"Unexpected Connection Error. {exp}")
        else:
            if resp.status == 401:
                _LOG.error("API Rate Limited at keep alive.")
                raise APIRateLimited("API Rate Limited at keep alive.")
            elif resp.status == 503:
                _LOG.error("Service Unavailable at keep alive.")
                raise ServiceUnavailable("Service Unavailable at keep alive.")
            elif resp.status != 200:
                _LOG.error(f"Session Error occurred: Received {resp.status}")
                raise SomeComfortError(
                    f"Session Error occurred: Received {resp.status}"
                )

    @_convert_errors
    async def discover(self):
        raw_locations = await self._get_locations()
        if raw_locations is not None:
            for raw_location in raw_locations:
                try:
                    location = await Location.from_api_response(self, raw_location)
                except KeyError as ex:
                    _LOG.exception(
                        ("Failed to process location `%s`: missing %s element")
                        % (raw_location.get("LocationID", "unknown"), ex.args[0])
                    )
                self._locations[location.locationid] = location

    @property
    def locations_by_id(self):
        """A dict of all locations indexed by id"""
        return self._locations

    @property
    def default_device(self):
        """This is the first device found.

        It is only useful if the account has only one device and location
        in your account (which is pretty common). It is None if there
        are no devices in the account.
        """
        for location in self.locations_by_id.values():
            for device in location.devices_by_id.values():
                return device
        return None

    def get_device(self, device_id):
        """Find a device by id.

        :returns: None if not found.
        """
        for location in self.locations_by_id.values():
            for ident, device in location.devices_by_id.items():
                if ident == device_id:
                    return device
