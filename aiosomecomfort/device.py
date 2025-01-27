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
HUMIDITY_STEP = 5 


def _hold_quarter_hours(deadline):
    if deadline.minute not in (0, 15, 30, 45):
        raise SomeComfortError("Invalid time: must be on a 15-minute boundary")
    return int(((deadline.hour * 60) + deadline.minute) / 15)


def _hold_deadline(quarter_hours) -> datetime.time:
    minutes = quarter_hours * 15
    return datetime.time(hour=int(minutes / 60), minute=minutes % 60)

def _humidity_step(value:int) -> int:
    """Round value to steps of 5."""
    return HUMIDITY_STEP * round (value/HUMIDITY_STEP)


class Device(object):
    """Device class for Honeywell device."""

    def __init__(self, client, location):
        self._client = client
        self._location = location
        self._data = {}
        self._gdata = {}
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
        if self._client.next_login > datetime.datetime.now(datetime.timezone.utc):
             raise APIRateLimited(f"Rate limit on login: Waiting {self._client.next_login-datetime.datetime.now(datetime.timezone.utc)}")
        data = await self._client.get_thermostat_data(self.deviceid)
        _LOG.debug("Refresh data %s", data)
        if data is not None:
            if not data.get("success"):
                _LOG.error("API reported failure to query device %s", self.deviceid)
            self._alive = data.get("deviceLive")
            self._commslost = data.get("communicationLost")
            self._data = data.get("latestData")
            if not self._gdata or self._gdata.get("Humidifier") or self._gdata.get("Dehumidifier"):
                self._gdata = await self._client.get_data(self.deviceid)
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
            return self._data["fanData"]["fanIsRunning"]
        return False

    @property
    def fan_mode(self) -> str | None:
        """Returns one of FAN_MODES indicating the current setting"""
        try:
            return FAN_MODES[self._data["fanData"]["fanMode"]]
        except (KeyError, TypeError, IndexError):
            if self._data["hasFan"]:
                raise APIError(f'Unknown fan mode {self._data["fanData"]["fanMode"]}')
            else:
                return None

    async def set_fan_mode(self, mode) -> None:
        """Set the fan mode async."""
        try:
            mode_index = FAN_MODES.index(mode)
        except ValueError as ex:
            raise SomeComfortError(f"Invalid fan mode {mode}") from ex

        key = f"fanMode{mode.title()}Allowed"
        if not self._data["fanData"][key]:
            raise SomeComfortError(f"Device does not support {mode}")
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
                'Unknown system mode {self._data["uiData"]["SystemSwitchPosition"]}'
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
        deadband = self._data["uiData"]["Deadband"]
        heatsp = self._data["uiData"]["HeatSetpoint"]

        if temp > upper or temp < lower:
            raise SomeComfortError(f"Setpoint outside range {lower}-{upper}")
        data = {"CoolSetpoint": temp}

        if deadband > 0 and (heatsp + deadband) >= temp:
            data.update({"HeatSetpoint": temp-deadband})
        else:
            data.update({"HeatSetpoint": heatsp})
        if not self._get_hold("Heat") and not self._get_hold("Cool"):
            data.update( {
                    "StatusCool": HOLD_TYPES.index("temporary"),
                    "StatusHeat": HOLD_TYPES.index("temporary"),
            })

        await self._client.set_thermostat_settings(
            self.deviceid, data
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
        deadband = self._data["uiData"]["Deadband"]
        coolsp = self._data["uiData"]["CoolSetpoint"]
        # HA sometimes doesn't send the temp, so set to current
        if temp is None:
            temp = self._data["uiData"]["HeatSetpoint"]
            _LOG.error("Didn't receive the temp to set. Setting to current temp.")
        if temp > upper or temp < lower:
            raise SomeComfortError(f"Setpoint outside range {lower}-{upper}")
        data = {"HeatSetpoint": temp}

        if deadband > 0 and (coolsp - deadband) <= temp:
            data.update({"CoolSetpoint": temp+deadband})
        else:
            data.update({"CoolSetpoint": coolsp})
            
        if not self._get_hold("Heat") and not self._get_hold("Cool"):
            data.update( {
                    "StatusCool": HOLD_TYPES.index("temporary"),
                    "StatusHeat": HOLD_TYPES.index("temporary"),
            })
        
        await self._client.set_thermostat_settings(
            self.deviceid, data
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
            lower = self._data["uiData"][f"{which}LowerSetptLimit"]
            upper = self._data["uiData"][f"{which}UpperSetptLimit"]
            deadband = self._data["uiData"]["Deadband"]
            coolsp = self._data["uiData"]["CoolSetpoint"]
            heatsp = self._data["uiData"]["HeatSetpoint"]
            if temperature > upper or temperature < lower:
                raise SomeComfortError(f"Setpoint outside range {lower}-{upper}")
            if which == "Heat" and deadband > 0 and (coolsp - deadband) <= temperature:
                settings.update({"CoolSetpoint": temperature+deadband})
            if which == "Cool" and deadband > 0 and (heatsp + deadband) >= temperature:
                settings.update({"HeatSetpoint": temperature-deadband})
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
    def has_humidifier(self) -> bool:
        """System has humdifier."""
        return(self._gdata.get("Humidifier") is not None)
    
    @property
    def humidifier_upper_limit(self) -> int:
        """Humidifier upper limit."""
        return int(self._gdata['Humidifier'].get('UpperLimit'))
    
    @property
    def humidifier_lower_limit(self) -> int:
        """Humidifier lower limit."""
        return int(self._gdata['Humidifier'].get('LowerLimit'))
    
    @property
    def humidifier_setpoint(self) -> int:
        """Humidifier current setpoint."""
        return int(self._gdata['Humidifier'].get('Setpoint'))

    @property
    def humidifier_mode(self) -> int:
        """Humidifier mode: 1 = auto, 0 = off."""
        return int(self._gdata['Humidifier'].get('Mode'))

    @property
    def has_dehumidifier(self) -> bool:
        """System has dehumidifier."""
        return(self._gdata.get("Dehumidifier") is not None)

    @property
    def dehumidifier_upper_limit(self) -> int:
        """Dehumidifier upper limit."""
        return int(self._gdata['Dehumidifier'].get('UpperLimit'))
    
    @property
    def dehumidifier_setpoint(self) -> int:
        """Dehumidifer current setpoint."""
        return int(self._gdata['Dehumidifier'].get('Setpoint'))

    @property
    def dehumidifier_lower_limit(self) -> int:
        """Dehmidifer lower limig."""
        return int(self._gdata['Dehumidifier'].get('LowerLimit'))

    @property
    def dehumidifier_mode(self) -> int:
        """Dehumidifier Mode. 1 = auto, 0 = off."""
        return int(self._gdata['Dehumidifier'].get('Mode'))


    @property
    def current_humidity(self) -> float | None:
        """The current measured ambient humidity"""
        return (
            self._data["uiData"]["IndoorHumidity"]
            if self._data["uiData"]["IndoorHumiditySensorAvailable"]
            and self._data["uiData"]["IndoorHumiditySensorNotFault"]
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

    async def set_humidifier_setpoint(self, humidity: int) -> None:
        """Set humidity settings."""
        data = self._gdata['Humidifier']
        data.update({
            "Setpoint": _humidity_step(humidity),
            })
        _LOG.debug("Sending Data: %s", data)
        url = f"{self._client._baseurl}/portal/Device/Menu/Humidifier"
        result = await self._client._post_json(url, json=data)
        _LOG.debug("Received Humidifier setting response %s", result)
        if result is None or not result.ok:
            raise APIError("API rejected humidity settings")
        
    async def set_humidifier_auto(self) -> None:
        """Set humidity settings."""
        data = self._gdata['Humidifier']
        data.update({"Mode": 1})
        _LOG.debug("Sending Data: %s", data)
        url = f"{self._client._baseurl}/portal/Device/Menu/Humidifier"
        result = await self._client._post_json(url, json=data)
        _LOG.debug("Received Humidifier setting response %s", result)
        if result is None or not result.ok:
            raise APIError("API rejected humidity settings")

    async def set_humidifier_off(self) -> None:
        """Set humidity settings."""
        data = self._gdata['Humidifier']
        data.update({"Mode": 0})
        _LOG.debug("Sending Data: %s", data)
        url = f"{self._client._baseurl}/portal/Device/Menu/Humidifier"
        result = await self._client._post_json(url, json=data)
        _LOG.debug("Received Humidifier setting response %s", result)
        if result is None or not result.ok:
            raise APIError("API rejected humidity settings")

    async def set_dehumidifier_setpoint(self, humidity: int) -> None:
        """Set humidity settings."""
        data = self._gdata['Dehumidifier']
        data.update({
            "Setpoint": _humidity_step(humidity),
            })
        _LOG.debug("Sending Data: %s", data)
        url = f"{self._client._baseurl}/portal/Device/Menu/Dehumidifier"
        result = await self._client._post_json(url, json=data)
        _LOG.debug("Received Dehumidifier setting response %s", result)
        if result is None or not result.ok:
            raise APIError("API rejected humidity settings")
        
    async def set_dehumidifier_auto(self) -> None:
        """Set humidity settings."""
        data = self._gdata['Dehumidifier']
        data.update({"Mode": 1})
        _LOG.debug("Sending Data: %s", data)
        url = f"{self._client._baseurl}/portal/Device/Menu/Dehumidifier"
        result = await self._client._post_json(url, json=data)
        _LOG.debug("Received Dehumidifier setting response %s", result)
        if result is None or not result.ok:
            raise APIError("API rejected humidity settings")

    async def set_dehumidifier_off(self) -> None:
        """Set humidity settings."""
        data = self._gdata['Dehumidifier']
        data.update({"Mode": 0})
        _LOG.debug("Sending Data: %s", data)
        url = f"{self._client._baseurl}/portal/Device/Menu/Dehumidifier"
        result = await self._client._post_json(url, json=data)
        _LOG.debug("Received Dehumidifier setting response %s", result)
        if result is None or not result.ok:
            raise APIError("API rejected humidity settings")


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
    
    @property
    def raw_data(self) -> dict:
        """The raw drData structure from the API.

        Note that this is read only!
        """
        return copy.deepcopy(self._gdata)
        
    def __repr__(self) -> str:
        return f"Device<{self.deviceid}:{self.name}>"
