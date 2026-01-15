"""
Control port models for MOD Audio plugins.

Provides typed dataclasses for plugin control ports with support for:
- Knobs (continuous, logarithmic)
- Toggles (on/off switches)
- Enumerations (selectors with scale points)
- Triggers (momentary buttons)
- Integer controls (discrete steps)
"""

import math
from dataclasses import dataclass, field
from enum import Flag, auto
from typing import Any


__all__ = [
    "ControlProperties",
    "ScalePoint",
    "Units",
    "ControlPort",
    "parse_control_ports",
]


class ControlProperties(Flag):
    """LV2 control port properties as flags."""

    NONE = 0
    TOGGLED = auto()  # On/off switch (0.0 or 1.0)
    INTEGER = auto()  # Discrete integer values
    LOGARITHMIC = auto()  # Logarithmic scale
    ENUMERATION = auto()  # Selector with scale points
    TRIGGER = auto()  # Momentary button (resets to default)
    HAS_STRICT_BOUNDS = auto()  # Value must stay within min/max
    NOT_ON_GUI = auto()  # Hidden from GUI

    @classmethod
    def from_list(cls, properties: list[str]) -> "ControlProperties":
        """Parse properties list from API response."""
        result = cls.NONE
        mapping = {
            "toggled": cls.TOGGLED,
            "integer": cls.INTEGER,
            "logarithmic": cls.LOGARITHMIC,
            "enumeration": cls.ENUMERATION,
            "trigger": cls.TRIGGER,
            "hasStrictBounds": cls.HAS_STRICT_BOUNDS,
            "notOnGUI": cls.NOT_ON_GUI,
        }
        for prop in properties:
            if prop in mapping:
                result |= mapping[prop]
        return result


@dataclass(frozen=True, slots=True)
class ScalePoint:
    """A discrete value option for enumeration controls."""

    value: float
    label: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScalePoint":
        return cls(
            value=float(data.get("value", 0.0)),
            label=str(data.get("label", "")),
        )


@dataclass(frozen=True, slots=True)
class Units:
    """Unit information for control display."""

    symbol: str  # e.g., "dB", "Hz", "ms"
    label: str  # e.g., "decibels", "hertz"
    render: str  # Format string for display

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Units | None":
        symbol = data.get("symbol", "")
        if not symbol:
            return None
        return cls(
            symbol=symbol,
            label=data.get("label", ""),
            render=data.get("render", ""),
        )


@dataclass(slots=True)
class ControlPort:
    """
    A plugin control port with full metadata.

    Supports various control types:
    - Knob: continuous value with min/max/default
    - Toggle: on/off (toggled property, values 0.0/1.0)
    - Selector: enumeration with scale_points
    - Trigger: momentary button that resets to default
    """

    # Identity
    symbol: str  # LV2 symbol, used in API calls
    name: str  # Display name
    short_name: str  # Abbreviated name
    index: int  # Port index in plugin

    # Value range
    minimum: float
    maximum: float
    default: float

    # Type information
    properties: ControlProperties
    scale_points: tuple[ScalePoint, ...] = field(default_factory=tuple)
    units: Units | None = None
    range_steps: int = 0  # 0 = continuous

    # Runtime state (mutable)
    _value: float | None = field(default=None, repr=False)

    @property
    def value(self) -> float:
        """Current value (default if not set)."""
        return self._value if self._value is not None else self.default

    @value.setter
    def value(self, val: float) -> None:
        """Set value with bounds checking."""
        self._value = self.clamp(val)

    # --- Type checks ---

    @property
    def is_toggled(self) -> bool:
        """Is this an on/off switch?"""
        return ControlProperties.TOGGLED in self.properties

    @property
    def is_integer(self) -> bool:
        """Does this use discrete integer values?"""
        return ControlProperties.INTEGER in self.properties

    @property
    def is_logarithmic(self) -> bool:
        """Does this use logarithmic scaling?"""
        return ControlProperties.LOGARITHMIC in self.properties

    @property
    def is_enumeration(self) -> bool:
        """Is this a selector with named options?"""
        return ControlProperties.ENUMERATION in self.properties

    @property
    def is_trigger(self) -> bool:
        """Is this a momentary trigger button?"""
        return ControlProperties.TRIGGER in self.properties

    @property
    def is_continuous(self) -> bool:
        """Is this a continuous knob (not toggle/enum/trigger)?"""
        return not (self.is_toggled or self.is_enumeration or self.is_trigger)

    # --- Value helpers ---

    def clamp(self, val: float) -> float:
        """Clamp value to valid range, respecting integer and rangeSteps."""
        clamped = max(self.minimum, min(self.maximum, val))

        # Quantize to discrete steps if rangeSteps > 0
        if self.range_steps > 1:
            step_size = (self.maximum - self.minimum) / (self.range_steps - 1)
            steps = round((clamped - self.minimum) / step_size)
            clamped = self.minimum + steps * step_size

        # Round to integer if integer property
        if self.is_integer:
            clamped = round(clamped)

        return clamped

    def normalize(self, val: float | None = None) -> float:
        """
        Get value as 0.0-1.0 normalized range.
        For logarithmic controls, applies log scaling.
        """
        v = val if val is not None else self.value
        if self.maximum == self.minimum:
            return 0.0

        if self.is_logarithmic and self.minimum > 0:
            # Log scaling: normalized = log(v/min) / log(max/min)
            return math.log(v / self.minimum) / math.log(self.maximum / self.minimum)

        return (v - self.minimum) / (self.maximum - self.minimum)

    def denormalize(self, normalized: float) -> float:
        """
        Convert 0.0-1.0 normalized value to actual range.
        For logarithmic controls, applies exponential scaling.
        """
        normalized = max(0.0, min(1.0, normalized))

        if self.is_logarithmic and self.minimum > 0:
            # Exponential scaling: v = min * (max/min)^normalized
            val = self.minimum * math.pow(self.maximum / self.minimum, normalized)
        else:
            val = self.minimum + normalized * (self.maximum - self.minimum)

        return self.clamp(val)

    def get_scale_point_label(self, val: float | None = None) -> str | None:
        """Get label for current value if enumeration."""
        if not self.scale_points:
            return None
        v = val if val is not None else self.value
        for sp in self.scale_points:
            if sp.value == v:
                return sp.label
        return None

    def format_value(self, val: float | None = None) -> str:
        """Format value for display with units."""
        v = val if val is not None else self.value

        # Enumeration: show label
        if self.is_enumeration:
            label = self.get_scale_point_label(v)
            if label:
                return label

        # Toggle: show On/Off
        if self.is_toggled:
            return "On" if v >= 0.5 else "Off"

        # Numeric with units
        if self.is_integer:
            formatted = str(int(v))
        else:
            formatted = f"{v:.2f}".rstrip("0").rstrip(".")

        if self.units:
            return f"{formatted} {self.units.symbol}"
        return formatted

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ControlPort":
        """Parse control port from API response."""
        ranges = data.get("ranges", {})
        units_data = data.get("units", {})
        scale_points_data = data.get("scalePoints", [])

        return cls(
            symbol=data.get("symbol", ""),
            name=data.get("name", ""),
            short_name=data.get("shortName", data.get("name", "")),
            index=data.get("index", 0),
            minimum=float(ranges.get("minimum", 0.0)),
            maximum=float(ranges.get("maximum", 1.0)),
            default=float(ranges.get("default", 0.0)),
            properties=ControlProperties.from_list(data.get("properties", [])),
            scale_points=tuple(
                ScalePoint.from_dict(sp) for sp in scale_points_data if sp.get("valid", True)
            ),
            units=Units.from_dict(units_data),
            range_steps=data.get("rangeSteps", 0),
        )


def parse_control_ports(effect_data: dict[str, Any]) -> list[ControlPort]:
    """
    Parse all control input ports from effect_get response.

    Args:
        effect_data: Full response from /effect/get API

    Returns:
        List of ControlPort objects for all control inputs
    """
    ports = effect_data.get("ports", {})
    control_ports = ports.get("control", {})
    inputs = control_ports.get("input", [])

    return [
        ControlPort.from_dict(port_data)
        for port_data in inputs
        if port_data.get("valid", True)
    ]
