"""
Plugin model with control management.

Plugin instances are created when loading into a Slot and provide
dict-like access to control parameters with automatic API synchronization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from mod_rack.client import GraphParamSetBypassEvent, Client, GraphParamSetEvent
from mod_rack.config import Config, PluginConfig
from mod_rack.controls import ControlPort, parse_control_ports


__all__ = ["Port", "Plugin"]


@dataclass(frozen=True, slots=True)
class Port:
    """Audio/CV/MIDI port on a plugin."""

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
    """

    def __init__(
        self, client: Client, uri: str, label: str, config: PluginConfig | None
    ):
        self.client = client
        self.uri = uri
        self.label = label

        self._bypassed = False
        self._config = config
        self._controls: dict[str, ControlPort] = {}

        # io setup
        self.audio_inputs: list[Port] = []
        self.audio_outputs: list[Port] = []
        self.midi_inputs: list[Port] = []
        self.midi_outputs: list[Port] = []

        # configuration
        self.join_audio_inputs: bool = (
            config.join_audio_inputs if config is not None else False
        )
        self.join_audio_outputs: bool = (
            config.join_audio_outputs if config is not None else False
        )

        self._effect_data: dict = self.client.effect_get(self.uri)
        self.name = self._effect_data.get("name", self.label)

        self.size: tuple[int, int] = self.client.effect_image_size(
            self.uri, "screenshot.png"
        )
        self._load_plugin_ports()
        self._load_controls()
        self._subscribe()

    def _subscribe(self):
        self.client.ws.on(GraphParamSetBypassEvent, self._on_bypass_change)
        self.client.ws.on(GraphParamSetEvent, self._on_param_change)

    def _on_bypass_change(self, event: GraphParamSetBypassEvent):
        if self.label == event.label:
            self._bypassed = event.bypassed

    def _on_param_change(self, event: GraphParamSetEvent):
        if self.label == event.label and event.symbol in self.controls:
            self.set_cached_value(event.symbol, event.value)

    @classmethod
    def load_supported(
        cls, client: Client, uri: str, label: str, config: Config
    ) -> Plugin | None:
        # Перевіряємо whitelist
        plugin_config = config.get_plugin_by_uri(uri)
        if not plugin_config:
            print(f"  Plugin {uri} not in whitelist, ignoring")
            return None

        plugin = cls(
            client=client,  # Буде встановлено після створення Slot
            uri=uri,
            label=label,
            config=plugin_config,
        )
        return plugin

    def _load_plugin_ports(self) -> None:
        """Load and filter plugin ports from effect data.

        Args:
            label: Plugin label for graph paths
            effect_data: Data from effect_get API

        Returns:
            Tuple of (inputs, outputs) Port lists
        """
        # Parse all ports from effect data

        ports: dict = self._effect_data.get("ports", {})
        audio_ports: dict = ports.get("audio", {})
        midi_ports: dict = ports.get("midi", {})

        config = self._config
        label = self.label

        for p in audio_ports.get("input", []):
            if config is not None and p["symbol"] in config.disable_ports:
                continue
            self.audio_inputs.append(
                Port(
                    symbol=p["symbol"],
                    name=p.get("name", p["symbol"]),
                    graph_path=f"{label}/{p['symbol']}",
                )
            )
        for p in audio_ports.get("output", []):
            if config is not None and p["symbol"] in config.disable_ports:
                continue
            self.audio_outputs.append(
                Port(
                    symbol=p["symbol"],
                    name=p.get("name", p["symbol"]),
                    graph_path=f"{label}/{p['symbol']}",
                )
            )
        for p in midi_ports.get("input", []):
            if config is not None and p["symbol"] in config.disable_ports:
                continue
            self.midi_inputs.append(
                Port(
                    symbol=p["symbol"],
                    name=p.get("name", p["symbol"]),
                    graph_path=f"{label}/{p['symbol']}",
                )
            )
        for p in midi_ports.get("output", []):
            if config is not None and p["symbol"] in config.disable_ports:
                continue
            self.midi_outputs.append(
                Port(
                    symbol=p["symbol"],
                    name=p.get("name", p["symbol"]),
                    graph_path=f"{label}/{p['symbol']}",
                )
            )

        print(
            f"Parsed audio ports: inputs={self.audio_inputs}, outputs={self.audio_outputs}"
        )
        print(
            f"Parsed midi ports: inputs={self.midi_inputs}, outputs={self.midi_outputs}"
        )

    def _load_controls(self) -> None:
        """Load control metadata from effect_get response."""
        controls = parse_control_ports(self._effect_data)
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
