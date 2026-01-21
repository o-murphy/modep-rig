"""
Plugin model with control management.

Plugin instances are created when loading into a Slot and provide
dict-like access to control parameters with automatic API synchronization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator

from pytest import Config

from modep_rig.client import BypassChange, Client, ParamChange
from modep_rig.controls import ControlPort, parse_control_ports


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
            raise KeyError(
                f"Control '{symbol}' not found. Available: {list(self._controls.keys())}"
            )
        return self._controls[symbol]

    def __setitem__(self, symbol: str, value: float) -> None:
        """Set control value and sync to API."""
        if symbol not in self._controls:
            raise KeyError(
                f"Control '{symbol}' not found. Available: {list(self._controls.keys())}"
            )

        control = self._controls[symbol]
        control.value = value  # This clamps the value

        # Sync to API via POST
        self._plugin._set_param(symbol, control.value)

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
        client: Client,
        uri: str,
        label: str,
        name: str,
        inputs: list[Port],
        outputs: list[Port],
    ):
        self.client = client
        self.uri = uri
        self.label = label
        self.name = name
        self.inputs = inputs
        self.outputs = outputs
        self.controls = ControlsProxy(self)
        self._bypassed = False
        # ui pos
        self.ui_x: int = 0
        self.ui_y: int = 0

        self._subscribe()

    def __del__(self):
        self._unsubscribe()

    def _subscribe(self):
        self.client.ws.on(BypassChange, self._on_bypass_change)
        self.client.ws.on(ParamChange, self._on_param_change)

    def _unsubscribe(self):
        self.client.ws.on(BypassChange, self._on_bypass_change)
        self.client.ws.off(ParamChange, self._on_param_change)

    def _on_bypass_change(self, event: BypassChange):
        print("EV", event)
        if self.label == event.label:
            self._bypassed = event.bypassed

    def _on_param_change(self, event: ParamChange):
        if self.label == event.label and event.symbol in self.controls:
            self.set_control_value(event.symbol, event.value)

    @classmethod
    def load_supported(
        cls, client: Client, uri: str, label: str, config: Config
    ) -> Plugin | None:
        # Перевіряємо whitelist
        plugin_config = config.get_plugin_by_uri(uri)
        if not plugin_config:
            print(f"  Plugin {uri} not in whitelist, ignoring")
            return

        effect_data = client.effect_get(uri)
        if not effect_data:
            print(f"  Failed to get effect data for {uri}")
            return

        inputs, outputs = cls._load_plugin_ports(label, uri, effect_data, config)
        print(
            f"  Parsed ports: inputs={[p.symbol for p in inputs]}, outputs={[p.symbol for p in outputs]}"
        )

        plugin = cls(
            client=client,  # Буде встановлено після створення Slot
            uri=uri,
            label=label,
            name=effect_data.get("name", label),
            inputs=inputs,
            outputs=outputs,
        )

        plugin._load_controls(effect_data)

        return plugin

    def update_metadata(self, uri: str, label: str):
        effect_data = self.client.effect_get(uri)
        if not effect_data:
            print(f"  Failed to get effect data for {uri}")
            return

        inputs, outputs = self._load_plugin_ports(label, uri, effect_data)

        # Update existing plugin
        self.uri = uri
        self.name = effect_data.get("name", label)
        self.inputs = inputs
        self.outputs = outputs
        self._load_controls(effect_data)

    @staticmethod
    def _load_plugin_ports(
        label: str, uri: str, effect_data: dict, config: Config
    ) -> tuple[list[Port], list[Port]]:
        """Load and filter plugin ports from effect data.

        Args:
            label: Plugin label for graph paths
            uri: Plugin URI for config lookup
            effect_data: Data from effect_get API

        Returns:
            Tuple of (inputs, outputs) Port lists
        """
        # Parse all ports from effect data
        all_inputs = []
        all_outputs = []

        ports = effect_data.get("ports", {})
        audio_ports = ports.get("audio", {})

        for p in audio_ports.get("input", []):
            all_inputs.append(
                Port(
                    symbol=p["symbol"],
                    name=p.get("name", p["symbol"]),
                    graph_path=f"{label}/{p['symbol']}",
                )
            )
        for p in audio_ports.get("output", []):
            all_outputs.append(
                Port(
                    symbol=p["symbol"],
                    name=p.get("name", p["symbol"]),
                    graph_path=f"{label}/{p['symbol']}",
                )
            )

        # Apply port overrides from config
        plugin_config = config.get_plugin_by_uri(uri)
        if plugin_config and plugin_config.inputs is not None:
            inputs = [p for p in all_inputs if p.symbol in plugin_config.inputs]
        else:
            inputs = all_inputs

        if plugin_config and plugin_config.outputs is not None:
            outputs = [p for p in all_outputs if p.symbol in plugin_config.outputs]
        else:
            outputs = all_outputs

        return inputs, outputs

    def _load_controls(self, effect_data: dict[str, Any]) -> None:
        """Load control metadata from effect_get response."""
        controls = parse_control_ports(effect_data)
        self.controls._populate(controls)

    def _set_param(self, symbol: str, value: float) -> bool:
        """Set parameter via Client API."""
        return self.client.ws.effect_param_set(self.label, symbol, value)

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

    @property
    def bypassed(self) -> bool:
        """Whether plugin is currently bypassed."""
        return self._bypassed

    def bypass(self, enabled: bool = True) -> bool:
        """Enable/disable bypass for this plugin. Set bypass via Client API."""
        return self.client.ws.effect_bypass(self.label, enabled)

    def set_control_value(self, symbol: str, value: float) -> None:
        """Set control value locally without API call (for WS sync)."""
        if symbol in self.controls._controls:
            self.controls._controls[symbol].value = value

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
