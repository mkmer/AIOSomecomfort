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
        self._deviceid = response.get("DeviceID")
        self._macid = response.get("MacID")
        self._name = response.get("Name")
        await self.refresh()
        return self

    async def refresh(self) -> None:
        """Refresh the Honeywell device data."""
        data = await self._client.get_thermostat_data(self.deviceid)
        if data is not None:
            if not data.get("success"):
                _LOG.error("API reported failure to query device %s" % self.deviceid)
            self._alive = data.get("deviceLive")
            self._commslost = data.get("communicationLost")
            self._data = data.get("latestData")
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
        if self._data.get("hasFan"):
            return self._data.get("fanData", {}).get("fanIsRunning", False)
        return False

    @property
    def fan_mode(self) -> str | None:
        """Returns one of FAN_MODES indicating the current setting"""
        if self._data.get("fanData", {}).get("fanMode") >= len(FAN_MODES):
            return None
        return FAN_MODES[self._data.get("fanData", {}).get("fanMode")]

    async def set_fan_mode(self, mode) -> None:
        """Set the fan mode async."""
        try:
            mode_index = FAN_MODES.index(mode)
        except ValueError as ex:
            raise SomeComfortError("Invalid fan mode %s" % mode) from ex

        key = f"fanMode{mode.title()}Allowed"
        if not self._data.get("fanData", {}).get(key):
            raise SomeComfortError("Device does not support %s" % mode)
        await self._client.set_thermostat_settings(
            self.deviceid, {"FanMode": mode_index}
        )
        self._data["fanData"]["fanMode"] = mode_index

    @property
    def system_mode(self) -> str:
        """Returns one of SYSTEM_MODES indicating the current setting"""
        if self._data.get("uiData", {}).get("SystemSwitchPosition") >= len(
            SYSTEM_MODES
        ):
            return None
        return SYSTEM_MODES[self._data.get("uiData", {}).get("SystemSwitchPosition")]

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
            if not self._data.get("uiData", {}).get(key):
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
        return self._data.get("uiData", {}).get("CoolSetpoint")

    async def set_setpoint_cool(self, temp) -> None:
        """Async set the target temperature when in cooling mode"""
        lower = self._data.get("uiData", {}).get("CoolLowerSetptLimit")
        upper = self._data.get("uiData", {}).get("CoolUpperSetptLimit")
        deadband = self._data.get("uiData", {}).get("Deadband")
        heatsp = self._data.get("uiData", {}).get("ScheduleHeatSp")

        if temp > upper or temp < lower:
            raise SomeComfortError(f"Setpoint outside range {lower}-{upper}")
        data = {"CoolSetpoint": temp}
        if deadband > 0 and (heatsp + deadband) >= temp:
            data.update({"HeatSetpoint": temp-deadband})
        await self._client.set_thermostat_settings(
            self.deviceid, data
        )
        self._data["uiData"]["CoolSetpoint"] = temp

    @property
    def setpoint_heat(self) -> float:
        """The target temperature when in heating mode"""
        return self._data.get("uiData", {}).get("HeatSetpoint")

    async def set_setpoint_heat(self, temp) -> None:
        """Async set the target temperature when in heating mode"""
        lower = self._data.get("uiData", {}).get("HeatLowerSetptLimit")
        upper = self._data.get("uiData", {}).get("HeatUpperSetptLimit")
        deadband = self._data.get("uiData", {}).get("Deadband")
        coolsp = self._data.get("uiData", {}).get("ScheduleCoolSp")
        # HA sometimes doesn't send the temp, so set to current
        if temp is None:
            temp = self._data.get("uiData").get("HeatSetpoint")
            _LOG.error("Didn't receive the temp to set. Setting to current temp.")
        if temp > upper or temp < lower:
            raise SomeComfortError(f"Setpoint outside range {lower}-{upper}")
        data = {"HeatSetpoint": temp}
        if deadband > 0 and (coolsp - deadband) <= temp:
            data.update({"CoolSetpoint": temp+deadband})
        await self._client.set_thermostat_settings(
            self.deviceid, data
        )
        self._data["uiData"]["HeatSetpoint"] = temp

    def _get_hold(self, which) -> bool | datetime.time:
        try:
            hold = HOLD_TYPES(self._data.get("uiData").get(f"Status{which}"))
        except KeyError as exc:
            mode = self._data.get("uiData", {}).get(f"Status{which}")
            raise APIError(f"Unknown hold mode {mode}") from exc
        period = self._data.get("uiData", {}).get(f"{which}NextPeriod")
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
            }
        elif hold is False:
            settings = {
                "StatusCool": HOLD_TYPES.index("schedule"),
                "StatusHeat": HOLD_TYPES.index("schedule"),
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
            lower = self._data.get("uiData", {}).get("HeatLowerSetptLimit")
            upper = self._data.get("uiData", {}).get("HeatUpperSetptLimit")
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
        return self._data.get("uiData", {}).get("DispTemperature")

    @property
    def current_humidity(self) -> float | None:
        """The current measured ambient humidity"""
        return (
            self._data.get("uiData").get("IndoorHumidity")
            if self._data.get("uiData", {}).get("IndoorHumiditySensorAvailable")
            and self._data.get("uiData", {}).get("IndoorHumiditySensorNotFault")
            else None
        )

    @property
    def equipment_output_status(self) -> str:
        """The current equipment output status"""
        if self._data.get("uiData").get("EquipmentOutputStatus") in (0, None):
            if self.fan_running:
                return "fan"
            else:
                return "off"
        return EQUIPMENT_OUTPUT_STATUS.get(
            self._data.get("uiData").get("EquipmentOutputStatus")
        )

    @property
    def outdoor_temperature(self) -> float | None:
        """The current measured outdoor temperature"""
        if self._data.get("uiData").get("OutdoorTemperatureAvailable"):
            return self._data.get("uiData").get("OutdoorTemperature")
        return None

    @property
    def outdoor_humidity(self) -> float | None:
        """The current measured outdoor humidity"""
        if self._data.get("uiData").get("OutdoorHumidityAvailable"):
            return self._data.get("uiData").get("OutdoorHumidity")
        return None

    @property
    def temperature_unit(self) -> str:
        """The temperature unit currently in use. Either 'F' or 'C'"""
        return self._data.get("uiData").get("DisplayUnits")

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
