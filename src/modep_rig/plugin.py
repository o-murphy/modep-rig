"""
Plugin model with control management.

Plugin instances are created when loading into a Slot and provide
dict-like access to control parameters with automatic API synchronization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator

from modep_rig.controls import ControlPort, parse_control_ports

if TYPE_CHECKING:
    from modep_rig.rig import Slot


__all__ = ["Port", "Plugin"]


@dataclass(frozen=True, slots=True)
class Port:
    """Audio/CV port on a plugin."""

    symbol: str
    name: str
    graph_path: str


class ControlsProxy:
    """
    Dict-like proxy for plugin controls with API synchronization.

    Supports:
        plugin.controls['Dist']          # Get ControlPort object
        plugin.controls['Dist'] = 0.5    # Set value via API
        plugin.controls.Dist             # Attribute access
        'Dist' in plugin.controls        # Check if control exists
        list(plugin.controls)            # Iterate over symbols
    """

    def __init__(self, plugin: "Plugin"):
        self._plugin = plugin
        self._controls: dict[str, ControlPort] = {}

    def _populate(self, controls: list[ControlPort]) -> None:
        """Populate controls from parsed data."""
        self._controls = {c.symbol: c for c in controls}

    def __getitem__(self, symbol: str) -> ControlPort:
        if symbol not in self._controls:
            raise KeyError(f"Control '{symbol}' not found. Available: {list(self._controls.keys())}")
        return self._controls[symbol]

    def __setitem__(self, symbol: str, value: float) -> None:
        """Set control value and sync to API."""
        if symbol not in self._controls:
            raise KeyError(f"Control '{symbol}' not found. Available: {list(self._controls.keys())}")

        control = self._controls[symbol]
        control.value = value  # This clamps the value

        # Sync to API via POST
        self._plugin._set_parameter(symbol, control.value)

    def __getattr__(self, symbol: str) -> ControlPort:
        """Allow attribute-style access: controls.Dist"""
        if symbol.startswith("_"):
            raise AttributeError(symbol)
        try:
            return self[symbol]
        except KeyError:
            raise AttributeError(f"Control '{symbol}' not found")

    def __contains__(self, symbol: str) -> bool:
        return symbol in self._controls

    def __iter__(self) -> Iterator[str]:
        return iter(self._controls)

    def __len__(self) -> int:
        return len(self._controls)

    def keys(self):
        return self._controls.keys()

    def values(self):
        return self._controls.values()

    def items(self):
        return self._controls.items()

    def __repr__(self) -> str:
        items = [f"{k}={v.format_value()}" for k, v in self._controls.items()]
        return f"Controls({', '.join(items)})"


class Plugin:
    """
    A loaded plugin instance with control management.

    Provides dict-like access to controls:
        plugin['Dist']          # Get current value
        plugin['Dist'] = 0.5    # Set value via API
        plugin.controls['Dist'] # Get ControlPort object with metadata

    Attributes:
        uri: Plugin URI
        label: Unique instance label (e.g., "DS1_0")
        name: Display name
        inputs: Audio input ports
        outputs: Audio output ports
        controls: ControlsProxy for parameter access
        slot: Reference to containing Slot
    """

    def __init__(
        self,
        slot: "Slot",
        uri: str,
        label: str,
        name: str,
        inputs: list[Port],
        outputs: list[Port],
    ):
        self.slot = slot
        self.uri = uri
        self.label = label
        self.name = name
        self.inputs = inputs
        self.outputs = outputs
        self.controls = ControlsProxy(self)

    def _load_controls(self, effect_data: dict[str, Any]) -> None:
        """Load control metadata from effect_get response."""
        controls = parse_control_ports(effect_data)
        self.controls._populate(controls)

    def _set_parameter(self, symbol: str, value: float) -> bool:
        """Set parameter via Client API."""
        return self.slot.rig.client.effect_parameter_set(self.label, symbol, value)

    def _set_bypass(self, enabled: bool) -> bool:
        """Set bypass via Client API."""
        return self.slot.rig.client.effect_bypass(self.label, enabled)

    # --- Dict-like access to control values ---

    def __getitem__(self, symbol: str) -> float:
        """Get current control value."""
        return self.controls[symbol].value

    def __setitem__(self, symbol: str, value: float) -> None:
        """Set control value (syncs to API)."""
        self.controls[symbol] = value

    def __contains__(self, symbol: str) -> bool:
        return symbol in self.controls

    def __iter__(self) -> Iterator[str]:
        return iter(self.controls)

    # --- Convenience methods ---

    def bypass(self, enabled: bool = True) -> bool:
        """Enable/disable bypass for this plugin."""
        return self._set_bypass(enabled)

    def reset_to_defaults(self) -> None:
        """Reset all controls to their default values."""
        for control in self.controls.values():
            self[control.symbol] = control.default

    def get_state(self) -> dict[str, float]:
        """Get current state of all controls."""
        return {symbol: ctrl.value for symbol, ctrl in self.controls.items()}

    def set_state(self, state: dict[str, float]) -> None:
        """Set multiple controls at once."""
        for symbol, value in state.items():
            if symbol in self.controls:
                self[symbol] = value

    def __repr__(self) -> str:
        return f"Plugin({self.label}, controls={len(self.controls)})"
