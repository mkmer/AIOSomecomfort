from __future__ import annotations
import copy
import datetime
import logging
import time
from .exceptions import *

FAN_MODES = ["auto", "on", "circulate", "follow schedule"]
SYSTEM_MODES = ["emheat", "heat", "off", "cool", "auto", "auto"]
HOLD_TYPES = ["schedule", "temporary", "permanent"]
EQUIPMENT_OUTPUT_STATUS = ["off/fan", "heat", "cool"]
_LOG = logging.getLogger("somecomfort")


def _hold_quarter_hours(deadline):
    if deadline.minute not in (0, 15, 30, 45):
        raise SomeComfortError("Invalid time: must be on a 15-minute boundary")
    return int(((deadline.hour * 60) + deadline.minute) / 15)


def _hold_deadline(quarter_hours) -> datetime.time:
    minutes = quarter_hours * 15
    return datetime.time(hour=int(minutes / 60), minute=minutes % 60)


class Device(object):
    """Device class for Honeywell device."""

    def __init__(self, client, location):
        self._client = client
        self._location = location
        self._data = {}
        self._last_refresh = 0
        self._deviceid = None
        self._macid = None
        self._name = None
        self._alive = None
        self._commslost = None

    @classmethod
    async def from_location_response(cls, client, location, response) -> Device:
        """Extract device from location response."""
        self = cls(client, location)
        self._deviceid = response["DeviceID"]
        self._macid = response["MacID"]
        self._name = response["Name"]
        await self.refresh()
        return self

    async def refresh(self) -> None:
        """Refresh the Honeywell device data."""
        data = await self._client.get_thermostat_data(self.deviceid)
        if data is not None:
            if not data["success"]:
                _LOG.error("API reported failure to query device %s" % self.deviceid)
            self._alive = data["deviceLive"]
            self._commslost = data["communicationLost"]
            self._data = data["latestData"]
            self._last_refresh = time.time()

    @property
    def deviceid(self) -> str:
        """The device identifier"""
        return self._deviceid

    @property
    def mac_address(self) -> str:
        """The MAC address of the device"""
        return self._macid

    @property
    def name(self) -> str:
        """The user-set name of this device"""
        return self._name

    @property
    def is_alive(self) -> bool:
        """A boolean indicating whether the device is connected"""
        return self._alive and not self._commslost

    @property
    def fan_running(self) -> bool:
        """Returns a boolean indicating the current state of the fan"""
        if self._data["hasFan"]:
            return self._data["fanData"]["fanIsRunning"]

        return False

    @property
    def fan_mode(self) -> str | None:
        """Returns one of FAN_MODES indicating the current setting"""
        try:
            return FAN_MODES[self._data["fanData"]["fanMode"]]
        except (KeyError, TypeError, IndexError):
            if self._data["hasFan"]:
                raise APIError("Unknown fan mode %s" % self._data["fanData"]["fanMode"])
            else:
                return None

    async def set_fan_mode(self, mode) -> None:
        """Set the fan mode async."""
        try:
            mode_index = FAN_MODES.index(mode)
        except ValueError as ex:
            raise SomeComfortError("Invalid fan mode %s" % mode) from ex

        key = f"fanMode{mode.title()}Allowed"
        if not self._data["fanData"][key]:
            raise SomeComfortError("Device does not support %s" % mode)
        await self._client.set_thermostat_settings(
            self.deviceid, {"FanMode": mode_index}
        )
        self._data["fanData"]["fanMode"] = mode_index

    @property
    def system_mode(self) -> str:
        """Returns one of SYSTEM_MODES indicating the current setting"""
        try:
            return SYSTEM_MODES[self._data["uiData"]["SystemSwitchPosition"]]
        except KeyError as exc:
            raise APIError(
                "Unknown system mode %s"
                % (self._data["uiData"]["SystemSwitchPosition"])
            ) from exc

    async def set_system_mode(self, mode) -> None:
        """Async set the system mode."""
        try:
            mode_index = SYSTEM_MODES.index(mode)
        except ValueError as exc:
            raise SomeComfortError(f"Invalid system mode {mode}") from exc
        if mode == "emheat":
            key = "SwitchEmergencyHeatAllowed"
        else:
            key = f"Switch{mode.title()}Allowed"
        try:
            if not self._data["uiData"][key]:
                raise SomeComfortError(f"Device does not support {mode}")
        except KeyError as exc:
            raise APIError(f"Unknown Key: {key}") from exc
        await self._client.set_thermostat_settings(
            self.deviceid, {"SystemSwitch": mode_index}
        )
        self._data["uiData"]["SystemSwitchPosition"] = mode_index

    @property
    def setpoint_cool(self) -> float:
        """The target temperature when in cooling mode"""
        return self._data["uiData"]["CoolSetpoint"]

    async def set_setpoint_cool(self, temp) -> None:
        """Async set the target temperature when in cooling mode"""
        lower = self._data["uiData"]["CoolLowerSetptLimit"]
        upper = self._data["uiData"]["CoolUpperSetptLimit"]
        if temp > upper or temp < lower:
            raise SomeComfortError(f"Setpoint outside range {lower}-{upper}")
        await self._client.set_thermostat_settings(
            self.deviceid, {"CoolSetpoint": temp}
        )
        self._data["uiData"]["CoolSetpoint"] = temp

    @property
    def setpoint_heat(self) -> float:
        """The target temperature when in heating mode"""
        return self._data["uiData"]["HeatSetpoint"]

    async def set_setpoint_heat(self, temp) -> None:
        """Async set the target temperature when in heating mode"""
        lower = self._data["uiData"]["HeatLowerSetptLimit"]
        upper = self._data["uiData"]["HeatUpperSetptLimit"]
        # HA sometimes doesn't send the temp, so set to current
        if temp is None:
            temp = self._data["uiData"]["HeatSetpoint"]
            _LOG.error("Didn't receive the temp to set. Setting to current temp.")
        if temp > upper or temp < lower:
            raise SomeComfortError(f"Setpoint outside range {lower}-{upper}")
        await self._client.set_thermostat_settings(
            self.deviceid, {"HeatSetpoint": temp}
        )
        self._data["uiData"]["HeatSetpoint"] = temp

    def _get_hold(self, which) -> bool | datetime.time:
        try:
            hold = HOLD_TYPES[self._data["uiData"][f"Status{which}"]]
        except KeyError as exc:
            mode = self._data["uiData"][f"Status{which}"]
            raise APIError(f"Unknown hold mode {mode}") from exc
        period = self._data["uiData"][f"{which}NextPeriod"]
        if hold == "schedule":
            return False
        if hold == "permanent":
            return True
        else:
            return _hold_deadline(period)

    async def _set_hold(self, which, hold, temperature=None) -> None:
        settings = {}
        if hold is True:
            settings = {
                "StatusCool": HOLD_TYPES.index("permanent"),
                "StatusHeat": HOLD_TYPES.index("permanent"),
                # "%sNextPeriod" % which: 0,
            }
        elif hold is False:
            settings = {
                "StatusCool": HOLD_TYPES.index("schedule"),
                "StatusHeat": HOLD_TYPES.index("schedule"),
                # "%sNextPeriod" % which: 0,
            }
        elif isinstance(hold, datetime.time):
            qh = _hold_quarter_hours(hold)
            settings = {
                "StatusCool": HOLD_TYPES.index("temporary"),
                "CoolNextPeriod": qh,
                "StatusHeat": HOLD_TYPES.index("temporary"),
                "HeatNextPeriod": qh,
            }
        else:
            raise SomeComfortError("Hold should be True, False, or datetime.time")
        if temperature:
            lower = self._data["uiData"][f"{which}LowerSetptLimit"]
            upper = self._data["uiData"][f"{which}UpperSetptLimit"]
            if temperature > upper or temperature < lower:
                raise SomeComfortError(f"Setpoint outside range {lower}-{upper}")
            settings.update({f"{which}Setpoint": temperature})
        await self._client.set_thermostat_settings(self.deviceid, settings)
        self._data["uiData"].update(settings)

    @property
    def hold_heat(self) -> bool:
        """Return hold heat mode."""
        return self._get_hold("Heat")

    async def set_hold_heat(self, value, temperature=None) -> None:
        """Async set hold heat mode."""
        await self._set_hold("Heat", value, temperature)

    @property
    def hold_cool(self) -> bool:
        """Return hold cool mode."""
        return self._get_hold("Cool")

    async def set_hold_cool(self, value, temperature=None) -> None:
        """Async set hold cool mode."""
        await self._set_hold("Cool", value, temperature)

    @property
    def current_temperature(self) -> float:
        """The current measured ambient temperature"""
        return self._data["uiData"]["DispTemperature"]

    @property
    def current_humidity(self) -> float | None:
        """The current measured ambient humidity"""
        return (
            self._data["uiData"].get("IndoorHumidity")
            if self._data["uiData"].get("IndoorHumiditySensorAvailable")
            and self._data["uiData"].get("IndoorHumiditySensorNotFault")
            else None
        )

    @property
    def equipment_output_status(self) -> str:
        """The current equipment output status"""
        if self._data["uiData"]["EquipmentOutputStatus"] in (0, None):
            if self.fan_running:
                return "fan"
            else:
                return "off"
        return EQUIPMENT_OUTPUT_STATUS[self._data["uiData"]["EquipmentOutputStatus"]]

    @property
    def outdoor_temperature(self) -> float | None:
        """The current measured outdoor temperature"""
        if self._data["uiData"]["OutdoorTemperatureAvailable"]:
            return self._data["uiData"]["OutdoorTemperature"]
        return None

    @property
    def outdoor_humidity(self) -> float | None:
        """The current measured outdoor humidity"""
        if self._data["uiData"]["OutdoorHumidityAvailable"]:
            return self._data["uiData"]["OutdoorHumidity"]
        return None

    @property
    def temperature_unit(self) -> str:
        """The temperature unit currently in use. Either 'F' or 'C'"""
        return self._data["uiData"]["DisplayUnits"]

    @property
    def raw_ui_data(self) -> dict:
        """The raw uiData structure from the API.

        Note that this is read only!
        """
        return copy.deepcopy(self._data["uiData"])

    @property
    def raw_fan_data(self) -> dict:
        """The raw fanData structure from the API.

        Note that this is read only!
        """
        return copy.deepcopy(self._data["fanData"])

    @property
    def raw_dr_data(self) -> dict:
        """The raw drData structure from the API.

        Note that this is read only!
        """
        return copy.deepcopy(self._data["drData"])

    def __repr__(self) -> str:
        return f"Device<{self.deviceid}:{self.name}>"
