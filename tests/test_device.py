"""Unit tests for aiosomecomfort.device.Device."""

import unittest

from aiosomecomfort.device import Device


def _make_device(equipment_output_status, has_fan=False, fan_is_running=False):
    """Build a Device with a minimal _data payload for property testing."""
    device = Device(client=None, location=None)
    device._data = {
        "uiData": {"EquipmentOutputStatus": equipment_output_status},
        "hasFan": has_fan,
        "fanData": {"fanIsRunning": fan_is_running},
    }
    return device


class TestEquipmentOutputStatus(unittest.TestCase):
    """Tests for the equipment_output_status property."""

    def test_never_reported_returns_none(self):
        """A raw value of None (never reported) should return None, not "off"."""
        device = _make_device(None)
        self.assertIsNone(device.equipment_output_status)

    def test_genuine_idle_returns_off(self):
        """A raw value of 0 (genuinely idle) should still return "off"."""
        device = _make_device(0)
        self.assertEqual(device.equipment_output_status, "off")

    def test_never_reported_with_fan_returns_fan(self):
        """A running fan is a known state even when the raw value is None."""
        device = _make_device(None, has_fan=True, fan_is_running=True)
        self.assertEqual(device.equipment_output_status, "fan")

    def test_idle_with_fan_returns_fan(self):
        """A running fan while idle (raw 0) returns "fan"."""
        device = _make_device(0, has_fan=True, fan_is_running=True)
        self.assertEqual(device.equipment_output_status, "fan")

    def test_heating(self):
        """A raw value of 1 maps to "heat"."""
        device = _make_device(1)
        self.assertEqual(device.equipment_output_status, "heat")

    def test_cooling(self):
        """A raw value of 2 maps to "cool"."""
        device = _make_device(2)
        self.assertEqual(device.equipment_output_status, "cool")


if __name__ == "__main__":
    unittest.main()
