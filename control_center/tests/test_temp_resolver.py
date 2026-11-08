"""Per-unit climate temperature resolver tests (stdlib unittest, no deps).

Run:  cd control_center && ../.venv/bin/python tests/test_temp_resolver.py
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_CC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_CC))
os.environ.setdefault("USERS_STORE", str(Path(tempfile.mkdtemp(prefix="cc-temp-test-")) / "users.json"))
os.environ.setdefault("MOCK_HA", "1")

import core                       # noqa: E402
import ha                         # noqa: E402
import scheduler                 # noqa: E402
from routes import schedules     # noqa: E402


def _seed(entity_id, *, unit=None, min_temp=None, max_temp=None, temp=None, state="cool"):
    attrs = {"friendly_name": entity_id}
    if unit is not None:
        attrs["temperature_unit"] = unit
    if min_temp is not None:
        attrs["min_temp"] = min_temp
    if max_temp is not None:
        attrs["max_temp"] = max_temp
    if temp is not None:
        attrs["temperature"] = temp
    core.STATE_CACHE[entity_id] = {"entity_id": entity_id, "state": state, "attributes": attrs}


class NormUnit(unittest.TestCase):
    def test_variants(self):
        for raw, want in [("°C", "C"), ("c", "C"), ("C", "C"), ("°F", "F"), ("f", "F"),
                          ("", None), (None, None), ("kelvin", None)]:
            self.assertEqual(scheduler._norm_unit(raw), want, raw)
        # backend + route normalizers agree
        self.assertEqual(schedules._norm_unit("°f"), "F")


class TargetUnit(unittest.TestCase):
    def test_explicit_unit(self):
        _seed("climate.f", unit="°F", min_temp=60, max_temp=86)
        _seed("climate.c", unit="°C", min_temp=16, max_temp=30)
        self.assertEqual(scheduler._target_unit("climate.f"), "F")
        self.assertEqual(scheduler._target_unit("climate.c"), "C")

    def test_infer_from_max_temp_when_no_unit(self):
        # Real HA entities often omit temperature_unit from state, so infer from
        # max_temp (>=50 => F). min_temp is unreliable.
        _seed("climate.hi", min_temp=60, max_temp=86)   # max 86 => F
        _seed("climate.lo", min_temp=16, max_temp=30)   # max 30 => C
        self.assertEqual(scheduler._target_unit("climate.hi"), "F")
        self.assertEqual(scheduler._target_unit("climate.lo"), "C")

    def test_low_min_but_high_max_is_fahrenheit(self):
        # Regression: a °F unit can report min_temp 40 (the old min>40 guess called
        # it °C and would then mis-convert its setpoint). max_temp settles it.
        _seed("climate.kitchen", min_temp=40, max_temp=90)
        self.assertEqual(scheduler._target_unit("climate.kitchen"), "F")
        _seed("climate.metric", min_temp=5, max_temp=35, temp=22)   # genuine °C AC
        self.assertEqual(scheduler._target_unit("climate.metric"), "C")

    def test_live_reading_beats_contradictory_max(self):
        # Some integrations report a metric default max (35) but run in °F (the live
        # reading is ~63). Trust current_temperature over the bogus structural max.
        core.STATE_CACHE["climate.bogus"] = {"entity_id": "climate.bogus", "state": "off",
            "attributes": {"min_temp": 5, "max_temp": 35, "current_temperature": 63, "temperature": 70}}
        self.assertEqual(scheduler._target_unit("climate.bogus"), "F")

    def test_no_range_falls_back_to_instance_unit(self):
        ha._HA_TEMP_UNIT_BY_INST.clear()
        for k in [k for k in os.environ if k.startswith("MOCK_TEMP_UNIT")]:
            del os.environ[k]
        os.environ["MOCK_TEMP_UNIT"] = "°F"               # main instance is °F
        _seed("climate.bare")                             # no unit, no range
        self.assertEqual(scheduler._target_unit("climate.bare"), "F")
        del os.environ["MOCK_TEMP_UNIT"]
        ha._HA_TEMP_UNIT_BY_INST.clear()


class ResolveTemp(unittest.TestCase):
    def setUp(self):
        _seed("climate.c", unit="°C", min_temp=10, max_temp=30, temp=20)
        _seed("climate.f", unit="°F", min_temp=60, max_temp=86, temp=70)

    def test_legacy_number_in_range(self):
        self.assertEqual(scheduler._resolve_temp(22, "climate.c"), 22.0)

    def test_legacy_number_out_of_range_is_dropped(self):
        # A desired value outside the entity's own range is dropped (leave alone).
        # -6.9444 as a °C setpoint is below the 10-30 range; 67 is above it.
        self.assertIsNone(scheduler._resolve_temp(-6.944444444444445, "climate.c"))
        self.assertIsNone(scheduler._resolve_temp(67, "climate.c"))   # 67 > 30
        self.assertIsNone(scheduler._resolve_temp(19.5, "climate.f"))  # 19.5 < 60

    def test_per_unit_list_picks_target_unit(self):
        groups = [{"unit": "C", "temp": 22}, {"unit": "F", "temp": 72}]
        self.assertEqual(scheduler._resolve_temp(groups, "climate.c"), 22.0)
        self.assertEqual(scheduler._resolve_temp(groups, "climate.f"), 72.0)

    def test_list_no_group_for_unit_left_alone(self):
        self.assertIsNone(scheduler._resolve_temp([{"unit": "F", "temp": 72}], "climate.c"))

    def test_list_value_out_of_range_dropped(self):
        # a °C group value that's fine as a number but outside this unit's live range
        self.assertIsNone(scheduler._resolve_temp([{"unit": "C", "temp": 99}], "climate.c"))

    def test_none_and_bool(self):
        self.assertIsNone(scheduler._resolve_temp(None, "climate.c"))
        self.assertIsNone(scheduler._resolve_temp(True, "climate.c"))


class ConvertTemp(unittest.TestCase):
    def test_roundtrip(self):
        self.assertAlmostEqual(scheduler._convert_temp(22, "C", "F"), 71.6)
        self.assertAlmostEqual(scheduler._convert_temp(71.6, "F", "C"), 22.0)
        self.assertEqual(scheduler._convert_temp(22, "C", "C"), 22)      # identity
        self.assertEqual(scheduler._convert_temp(22, "C", None), 22)     # unknown target


class SendTemp(unittest.TestCase):
    """The value actually SENT: HA converts an incoming set_temperature from the
    receiving instance's system unit to the entity unit, so we pre-encode into the
    system unit. These guard the real production incident."""

    def setUp(self):
        self._clear()
        _seed("climate.c", unit="°C", min_temp=10, max_temp=30, temp=20)    # local °C AC
        _seed("remote:climate.ac", unit="°C", min_temp=10, max_temp=30, temp=20)  # remote °C AC
        _seed("climate.f", unit="°F", min_temp=60, max_temp=86, temp=70)    # local °F AC

    def tearDown(self):
        self._clear()

    @staticmethod
    def _clear():
        ha._HA_TEMP_UNIT_BY_INST.clear()
        for k in [k for k in os.environ if k.startswith("MOCK_TEMP_UNIT")]:
            del os.environ[k]

    def test_matching_units_pass_through(self):
        os.environ["MOCK_TEMP_UNIT"] = "°C"                 # main instance is °C
        self.assertEqual(scheduler._send_temp(22, "climate.c"), 22.0)

    def test_c_entity_on_f_system_is_encoded_to_f(self):
        # THE PRODUCTION INCIDENT. Main system °F, entity °C, user wants 19.5°C.
        # Old fix sent 19.5 -> HA did convert(19.5, °F, °C) = -6.94 -> rejected.
        # New: send 67.1°F, which HA converts to 19.5°C, inside 10..30. No reject.
        os.environ["MOCK_TEMP_UNIT"] = "°F"
        self.assertAlmostEqual(scheduler._send_temp(19.5, "climate.c"), 67.1)

    def test_remote_c_entity_on_f_remote_instance(self):
        # The remote instance that actually threw the error runs its own unit system.
        os.environ["MOCK_TEMP_UNIT_remote"] = "°F"
        groups = [{"unit": "C", "temp": 19.5}, {"unit": "F", "temp": 72}]
        self.assertAlmostEqual(scheduler._send_temp(groups, "remote:climate.ac"), 67.1)

    def test_f_entity_on_f_system_pass_through(self):
        os.environ["MOCK_TEMP_UNIT"] = "°F"
        self.assertEqual(scheduler._send_temp(72, "climate.f"), 72.0)

    def test_out_of_range_desired_sends_nothing(self):
        os.environ["MOCK_TEMP_UNIT"] = "°F"
        self.assertIsNone(scheduler._send_temp([{"unit": "C", "temp": 99}], "climate.c"))

    def test_f_entity_on_c_system_is_encoded_to_c(self):
        # Both ACs live on a °C-system instance. The °C AC gets its °C group
        # verbatim; the °F AC's 72°F must be pre-encoded to 22.2°C so HA converts
        # it back to 72°F (HA always reads the number in the system unit, °C here).
        os.environ["MOCK_TEMP_UNIT"] = "°C"
        groups = [{"unit": "C", "temp": 24}, {"unit": "F", "temp": 72}]
        self.assertEqual(scheduler._send_temp(groups, "climate.c"), 24.0)        # °C group, °C sys
        self.assertAlmostEqual(scheduler._send_temp(groups, "climate.f"), 22.2)  # 72°F -> 22.2°C


class ValidateTemp(unittest.TestCase):
    def test_scalar(self):
        self.assertEqual(schedules._validated_temp(22), 22.0)
        self.assertEqual(schedules._validated_temp("21.5"), 21.5)
        self.assertIsNone(schedules._validated_temp(None))
        self.assertIsNone(schedules._validated_temp(True))
        self.assertIsNone(schedules._validated_temp("nope"))

    def test_list_coerce_and_dedupe(self):
        out = schedules._validated_temp(
            [{"unit": "°C", "temp": 22}, {"unit": "f", "temp": "72"},
             {"unit": "C", "temp": 25},              # dup unit -> dropped
             {"unit": "x", "temp": 1}, {"unit": "F"},  # bad unit / no temp -> dropped
             "junk"])
        self.assertEqual(out, [{"unit": "C", "temp": 22.0}, {"unit": "F", "temp": 72.0}])

    def test_empty_list_is_none(self):
        self.assertIsNone(schedules._validated_temp([]))
        self.assertIsNone(schedules._validated_temp([{"unit": "x"}]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
