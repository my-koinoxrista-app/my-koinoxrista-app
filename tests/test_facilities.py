import json
import unittest
from decimal import Decimal

from configuration import building_json, load_building
from expense_engine import AllocationError, Expense, Period, calculate_period
from facilities import BuildingFacilities, HeatingSystem, normalize_facilities, normalize_heating


def sample():
    return {
        "id": "b1", "name": "Demo", "address": "",
        "apartments": [{"id": "a1", "code": "A1"}, {"id": "a2", "code": "A2"}],
        "tables": [{"id": "general", "name": "General",
                    "weights": {"a1": "600", "a2": "400"}, "expected_total": "1000"}],
        "categories": [{"id": "cleaning", "name": "Cleaning",
                        "rule": {"type": "WEIGHTED", "table_id": "general"}}],
    }


class FacilityModelTests(unittest.TestCase):
    def test_legacy_configuration(self):
        building = load_building(sample())
        self.assertIsNone(building.facilities["shared_water"])
        self.assertIsNone(building.heating["fuel_type"])

    def test_facility_round_trip(self):
        data = sample()
        data["facilities"] = {"elevator": True, "shared_water": False,
                              "sewage": "SEPTIC", "garden": True}
        building = load_building(data)
        self.assertEqual(load_building(building_json(building)), building)

    def test_heating_variants(self):
        for fuel in ("OIL", "NATURAL_GAS", "ELECTRIC", "OTHER"):
            for metering in ("NONE", "HOUR_METER", "HEAT_METER"):
                with self.subTest(fuel=fuel, metering=metering):
                    heating = normalize_heating({
                        "fuel_type": fuel, "system_type": "CENTRAL",
                        "metering_type": metering,
                    })
                    self.assertEqual(heating["fuel_type"], fuel)

    def test_legacy_heating_method(self):
        heating = normalize_heating({
            "fuel_type": "OIL", "system_type": "CENTRAL",
            "metering_type": "NONE",
            "allocation_method": "SIMPLE_WEIGHTED_DEMO",
        })
        self.assertEqual(heating["allocation_method"], "SIMPLE_WEIGHTED_DEMO")

    def test_invalid_facilities(self):
        for value in ({"garden": "yes"}, {"shared_water": 1},
                      {"sewage": "UNKNOWN"}, {"elevator": "false"},
                      {"unexpected": True}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_facilities(value)

    def test_invalid_heating(self):
        for value in (
            {"fuel_type": "COAL"},
            {"system_type": "NONE", "fuel_type": "OIL"},
            {"system_type": "NONE", "metering_type": "HOUR_METER"},
            {"system_type": "CENTRAL", "fuel_type": "NONE"},
            {"metering_type": "MINUTES"},
            {"study_reference": ""},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_heating(value)

    def test_building_rejects_invalid_metadata(self):
        data = sample()
        data["heating"] = {"fuel_type": "UNKNOWN"}
        with self.assertRaises(AllocationError):
            load_building(data)

    def test_metadata_does_not_change_charges(self):
        data = sample()
        original = load_building(data)
        data["facilities"] = {"elevator": True, "garden": True, "sewage": "SEPTIC"}
        data["heating"] = {"fuel_type": "NATURAL_GAS", "system_type": "CENTRAL",
                           "metering_type": "HEAT_METER"}
        configured = load_building(data)
        period = Period("2026-09-01", "2026-09-30",
                        (Expense("e1", "cleaning", Decimal("100.00")),))
        self.assertEqual(calculate_period(original, period)["apartment_totals"],
                         calculate_period(configured, period)["apartment_totals"])

    def test_typed_models(self):
        self.assertEqual(BuildingFacilities.from_mapping({"garden": True}).garden, True)
        self.assertEqual(HeatingSystem.from_mapping({"fuel_type": "OIL"}).fuel_type, "OIL")


if __name__ == "__main__":
    unittest.main()
