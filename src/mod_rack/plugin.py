"""
Plugin model with control management.

Plugin instances are created when loading into a Slot and provide
dict-like access to control parameters with automatic API synchronization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from pytest import Config

from mod_rack.client import ParamSetBypass, Client, ParamSet, PluginPos
from mod_rack.config import PluginConfig
from mod_rack.controls import ControlPort, parse_control_ports


__all__ = ["Port", "Plugin"]


@dataclass(frozen=True, slots=True)
class Port:
    """Audio/CV port on a plugin."""

    symbol: str
    name: str
    graph_path: str


class Plugin:
    """
    A loaded plugin instance with control management.

    Provides dict-like access to controls:
        plugin['Dist']          # Get current value

    Attributes:
        uri: Plugin URI
        label: Unique instance label (e.g., "DS1_0")
        name: Display name
        inputs: Audio input ports
        outputs: Audio output ports
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
        self._bypassed = False
        # ui pos
        self.ui_x: int = 0
        self.ui_y: int = 0

        self._controls: dict[str, ControlPort] = {}

        # self._subscribe()

    def __del__(self):
        self._unsubscribe()

    def _subscribe(self):
        self.client.ws.on(ParamSetBypass, self._on_bypass_change)
        self.client.ws.on(ParamSet, self._on_param_change)
        self.client.ws.on(PluginPos, self._on_position_change)

    def _unsubscribe(self):
        self.client.ws.off(ParamSetBypass, self._on_bypass_change)
        self.client.ws.off(ParamSet, self._on_param_change)
        self.client.ws.off(PluginPos, self._on_position_change)

    def _on_bypass_change(self, event: ParamSetBypass):
        print("EV", event)
        if self.label == event.label:
            self._bypassed = event.bypassed

    def _on_param_change(self, event: ParamSet):
        if self.label == event.label and event.symbol in self.controls:
            self.set_cached_value(event.symbol, event.value)

    def _on_position_change(self, event: PluginPos):
        if self.label == event.label:
            old_x = self.ui_x
            old_y = self.ui_y

            self.ui_x = event.x
            self.ui_y = event.y

            print(
                f"PLUGIN << POSITION: {event.label} ({old_x}, {old_y}) -> ({event.x}, {event.y})"
            )

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
        plugin._subscribe()
        return plugin

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

        plugin_config: PluginConfig = config.get_plugin_by_uri(uri)

        for p in audio_ports.get("input", []):
            if p["symbol"] in plugin_config.disable_ports:
                continue
            all_inputs.append(
                Port(
                    symbol=p["symbol"],
                    name=p.get("name", p["symbol"]),
                    graph_path=f"{label}/{p['symbol']}",
                )
            )
        for p in audio_ports.get("output", []):
            if p["symbol"] in plugin_config.disable_ports:
                continue
            all_outputs.append(
                Port(
                    symbol=p["symbol"],
                    name=p.get("name", p["symbol"]),
                    graph_path=f"{label}/{p['symbol']}",
                )
            )

        print(all_inputs)
        return all_inputs, all_outputs

    def _load_controls(self, effect_data: dict[str, Any]) -> None:
        """Load control metadata from effect_get response."""
        controls = parse_control_ports(effect_data)
        self._populate(controls)

    def _populate(self, controls: list[ControlPort]) -> None:
        """Populate controls from parsed data."""
        self._controls = {c.symbol: c for c in controls}

    # --- Dict-like access to control values ---

    @property
    def controls(self):
        return self._controls

    def keys(self):
        return self._controls.keys()

    def values(self):
        return self._controls.values()

    def items(self):
        return self._controls.items()

    def __getitem__(self, symbol: str) -> ControlPort:
        """Get current cached control value."""
        if symbol not in self._controls:
            raise KeyError(
                f"Control '{symbol}' not found. Available: {list(self._controls.keys())}"
            )
        return self._controls[symbol]

    def __contains__(self, symbol: str) -> bool:
        return symbol in self._controls

    def __iter__(self) -> Iterator[str]:
        return iter(self._controls)

    # --- Convenience methods ---

    @property
    def bypassed(self) -> bool:
        """Whether plugin is currently bypassed."""
        return self._bypassed

    def bypass(self, bypass: bool = True) -> bool:
        """Enable/disable bypass for this plugin. Set bypass via Client API."""
        self.client.ws.effect_bypass(self.label, bypass)
        return self.client.effect_bypass(self.label, bypass)

    def param_set(self, symbol: str, value: float) -> bool:
        """Set parameter via Client API."""
        if symbol not in self._controls:
            raise KeyError(
                f"Control '{symbol}' not found. Available: {list(self._controls.keys())}"
            )

        # Sync to API via POST
        self.client.ws.effect_param_set(self.label, symbol, value)
        return self.client.effect_param_set(self.label, symbol, value)

    def set_cached_value(self, symbol: str, value: float) -> None:
        """Set control value locally without API call (for WS sync)."""
        if symbol in self._controls:
            self._controls[symbol].value = value

    def __repr__(self) -> str:
        items = [f"{k}={v.format_value()}" for k, v in self._controls.items()]
        return f"Plugin({self.label}, controls={', '.join(items)})"
