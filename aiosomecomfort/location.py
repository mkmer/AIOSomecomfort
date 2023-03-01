from __future__ import annotations
from .device import Device


class Location(object):
    """Location class for Honeywell"""

    def __init__(self, client):
        self._client = client
        self._devices = {}
        self._locationid = "unknown"

    @classmethod
    async def from_api_response(cls, client, api_response) -> Location:
        """Process a response from the API."""
        self = cls(client)
        self._locationid = api_response["LocationID"]
        devices = api_response["Devices"]
        _devices = [
            await Device.from_location_response(client, self, dev) for dev in devices
        ]
        self._devices = {dev.deviceid: dev for dev in _devices}
        return self

    @property
    def devices_by_id(self) -> dict:
        """A dict of devices indexed by DeviceID."""
        return self._devices

    @property
    def devices_by_name(self) -> dict:
        """A dict of devices indexed by name.

        Note that if you have multiple devices with the same name,
        this may not return them all!
        """
        return {dev.name: dev for dev in self._devices}

    @property
    def locationid(self) -> str:
        """The location identifier."""
        return self._locationid

    def __repr__(self) -> str:
        return f"Location<{self.locationid}>"
