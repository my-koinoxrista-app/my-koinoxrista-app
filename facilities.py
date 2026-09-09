"""Validated building facility metadata. No allocation or database logic."""

from dataclasses import asdict, dataclass
from typing import Mapping


class FacilityConfigurationError(ValueError):
    """Invalid or contradictory facility configuration."""


def _choice(value, allowed, field_name):
    if value is not None and (not isinstance(value, str) or value not in allowed):
        raise FacilityConfigurationError(f"Invalid {field_name}: {value!r}")
    return value


def _optional_bool(value, field_name):
    if value is not None and not isinstance(value, bool):
        raise FacilityConfigurationError(f"{field_name} must be true, false or null.")
    return value


def _optional_text(value, field_name):
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise FacilityConfigurationError(f"{field_name} must be non-empty text or null.")
    return value


def _mapping(value, field_name):
    if not isinstance(value, Mapping):
        raise FacilityConfigurationError(f"{field_name} must be an object.")
    return value


def _known_keys(data, allowed, field_name):
    unknown = set(data) - allowed
    if unknown:
        raise FacilityConfigurationError(
            f"Unknown {field_name} fields: {', '.join(sorted(map(str, unknown)))}"
        )


@dataclass(frozen=True)
class BuildingFacilities:
    elevator: bool | None = None
    shared_water: bool | None = None
    sewage: str | None = None
    garden: bool | None = None

    @classmethod
    def from_mapping(cls, value):
        data = _mapping(value, "facilities")
        _known_keys(data, set(cls.__dataclass_fields__), "facilities")
        return cls(
            elevator=_optional_bool(data.get("elevator"), "elevator"),
            shared_water=_optional_bool(data.get("shared_water"), "shared_water"),
            sewage=_choice(data.get("sewage"),
                           {"NETWORK", "SEPTIC", "NONE", "OTHER"}, "sewage"),
            garden=_optional_bool(data.get("garden"), "garden"),
        )

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class HeatingSystem:
    fuel_type: str | None = None
    system_type: str | None = None
    metering_type: str | None = None
    allocation_method: str | None = None
    study_reference: str | None = None

    @classmethod
    def from_mapping(cls, value):
        data = _mapping(value, "heating")
        _known_keys(data, set(cls.__dataclass_fields__), "heating")
        result = cls(
            fuel_type=_choice(data.get("fuel_type"),
                              {"OIL", "NATURAL_GAS", "ELECTRIC", "OTHER", "NONE"},
                              "fuel_type"),
            system_type=_choice(data.get("system_type"),
                                {"CENTRAL", "AUTONOMOUS", "NONE"}, "system_type"),
            metering_type=_choice(data.get("metering_type"),
                                  {"NONE", "HOUR_METER", "HEAT_METER", "OTHER"},
                                  "metering_type"),
            allocation_method=_optional_text(data.get("allocation_method"),
                                              "allocation_method"),
            study_reference=_optional_text(data.get("study_reference"),
                                           "study_reference"),
        )
        if result.system_type == "NONE":
            if result.fuel_type not in (None, "NONE"):
                raise FacilityConfigurationError("A building without heating cannot specify a fuel.")
            if result.metering_type not in (None, "NONE"):
                raise FacilityConfigurationError("A building without heating cannot have heating meters.")
        if result.fuel_type == "NONE" and result.system_type not in (None, "NONE"):
            raise FacilityConfigurationError("A heating system cannot have fuel_type NONE.")
        return result

    def to_dict(self):
        return asdict(self)


def normalize_facilities(value):
    """Normalize legacy or partial facility mappings without inventing missing facts."""
    return BuildingFacilities.from_mapping(value).to_dict()


def normalize_heating(value):
    """Normalize heating metadata; no heating charge is calculated here."""
    return HeatingSystem.from_mapping(value).to_dict()
