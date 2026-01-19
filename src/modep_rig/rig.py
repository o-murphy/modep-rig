from typing import Callable, SupportsIndex

from modep_rig.config import Config, PluginConfig
from modep_rig.client import Client
from modep_rig.plugin import Plugin, Port


# Type aliases for callbacks
OnParamChangeCallback = Callable[[str, str, float], None]  # label, symbol, value
OnBypassChangeCallback = Callable[[str, bool], None]  # label, bypassed
OnSlotAddedCallback = Callable[["Slot"], None]  # slot
OnSlotRemovedCallback = Callable[[str], None]  # label
OnOrderChangeCallback = Callable[[list[str]], None]  # order (list of labels)


__all__ = ["Slot", "HardwareSlot", "Rig"]


# =============================================================================
# Slots
# =============================================================================


class Slot:
    """
    Слот для плагіна в ланцюгу ефектів.

    Slot завжди містить плагін (немає пустих слотів).
    Slot ідентифікується по label плагіна.
    """

    def __init__(self, rig: "Rig", plugin: Plugin):
        """
        Створює слот з плагіном.

        Args:
            rig: Батьківський Rig
            plugin: Плагін (обов'язковий)
        """
        self.rig = rig
        self.plugin = plugin

    @property
    def label(self) -> str:
        """Унікальний ідентифікатор слота (label плагіна)."""
        return self.plugin.label

    @property
    def index(self) -> int:
        """Поточна позиція слота в ланцюгу (динамічно обчислюється)."""
        try:
            return self.rig.slots.index(self)
        except ValueError:
            return -1

    @property
    def inputs(self) -> list[str]:
        return [p.graph_path for p in self.plugin.inputs]

    @property
    def outputs(self) -> list[str]:
        return [p.graph_path for p in self.plugin.outputs]

    @property
    def is_stereo(self) -> bool:
        """Визначає чи слот працює в стерео режимі."""
        plugin_config = self.rig.config.get_plugin_by_uri(self.plugin.uri)
        if plugin_config and plugin_config.mode:
            return plugin_config.mode == "stereo"
        return len(self.plugin.outputs) >= 2

    @staticmethod
    def _label_from_uri(uri: str) -> str:
        """Генерує базовий label з URI плагіна."""
        path = uri.split("#")[0].rstrip("/")
        label = path.split("/")[-1]
        return label.replace("#", "_").replace(" ", "_")

    def __repr__(self):
        return f"Slot({self.label})"


class HardwareSlot:
    """Hardware I/O слот (capture/playback)."""

    def __init__(self, rig: "Rig", ports: list[str], is_input: bool):
        self.rig = rig
        self._ports = ports
        self._is_input = is_input

    @property
    def label(self) -> str:
        return "hw_in" if self._is_input else "hw_out"

    @property
    def outputs(self) -> list[str]:
        """Виходи hardware input slot (capture порти)."""
        return self._ports if self._is_input else []

    @property
    def hw_inputs(self) -> list[str]:
        """Входи hardware output slot (playback порти)."""
        return self._ports if not self._is_input else []

    @property
    def is_stereo(self) -> bool:
        return len(self._ports) >= 2

    def __repr__(self):
        kind = "Input" if self._is_input else "Output"
        return f"HardwareSlot({kind}, ports={self._ports})"


# =============================================================================
# Rig
# =============================================================================


class Rig:
    """
    Rig — ланцюг ефектів: Input -> [Slot 0] -> [Slot 1] -> ... -> Output

    Реактивна архітектура (Server-as-Source-of-Truth):
    - Клієнт може ініціювати зміни через request_* методи
    - Локальний стан змінюється ТІЛЬКИ у відповідь на WS feedback
    - WS handlers: _on_plugin_added(), _on_plugin_removed()
    """

    def __init__(self, config: Config, client: Client = None, reset_on_init: bool = False):
        self.config = config
        # If caller did not provide a Client, create one but delay WebSocket
        # connection until after callbacks are installed to avoid missing messages.
        if client is None:
            self.client = Client(config.server.url, connect=False)
        else:
            self.client = client

        # Determine hardware ports (auto-detect or from config)
        hw_inputs, hw_outputs = self._resolve_hardware_ports()

        self.input_slot = HardwareSlot(self, ports=hw_inputs, is_input=True)
        self.output_slot = HardwareSlot(self, ports=hw_outputs, is_input=False)

        # Slots list - порядок контролюється клієнтом
        self.slots: list[Slot] = []

        # Label counter for unique labels
        self._label_counter = 0
        # Pending inserts mapping: label -> desired insert index (used for replace)
        self._pending_inserts: dict[str, int] = {}
        
        # Flag to defer reconnections during initial pedalboard loading
        self._initializing = True

        # External callbacks (for UI)
        self._ext_on_param_change: OnParamChangeCallback | None = None
        self._ext_on_bypass_change: OnBypassChangeCallback | None = None
        self._ext_on_slot_added: OnSlotAddedCallback | None = None
        self._ext_on_slot_removed: OnSlotRemovedCallback | None = None
        self._ext_on_order_change: OnOrderChangeCallback | None = None

        # Setup WebSocket callbacks BEFORE connecting so we don't miss initial messages
        self.client.ws.set_callbacks(
            on_param_change=self._on_param_change,
            on_bypass_change=self._on_bypass_change,
            on_structural_change=self._on_structural_change,
            on_order_change=self._on_order_change,
        )

        # If the client was created with connect=False we need to start it now so the
        # callbacks will receive the server's initial messages. Otherwise connecting
        # has already happened during Client construction.
        try:
            # Only call connect if the underlying WsClient hasn't connected yet
            if not getattr(self.client.ws, 'ws', None) or not getattr(self.client.ws.ws, 'sock', None) or not getattr(self.client.ws.ws, 'sock', 'connected'):
                # best-effort connect (WsClient.connect() is idempotent)
                self.client.ws.connect()
        except Exception:
            # ignore and proceed; connect may have already been started elsewhere
            pass

        # Wait for the initial pedalboard to load (loading_end message)
        # This ensures we don't return before receiving all initial plugin/connection messages
        print("Waiting for WebSocket pedalboard ready signal...")
        self.client.ws.wait_pedalboard_ready(timeout=10.0)

        # Initialization complete - reconnections will now happen normally
        self._initializing = False
        # NOTE: We do NOT call reconnect() here anymore.
        # The server already has correct connections - we just observe.
        # Only move_slot() and explicit user actions should trigger reconnect.
        if self.slots:
            print(f"Loaded {len(self.slots)} slots from server (no reconnect)")
        else:
            print("No slots loaded from server")

        # By default we perform an initial reset and rebuild (preserves previous behaviour).
        # If `reset_on_init` is False, we skip calling `client.reset()` and `reconnect()` so
        # the local `Rig` state will be built reactively from WebSocket `add`/`remove`
        # messages emitted by the server (useful to avoid double connect/disconnects
        # when the server already pushes the current pedalboard on startup).
        if reset_on_init:
            # FIXME: maybe not needed due to auto init on websocket messages
            # Initial reset
            self.client.reset()
            self.reconnect()
        else:
            print("Rig initialization complete")

    def _resolve_hardware_ports(self) -> tuple[list[str], list[str]]:
        """Resolve hardware ports from config or auto-detect from MOD-UI."""
        hw_config = self.config.hardware

        if hw_config.inputs is not None:
            inputs = hw_config.inputs
        else:
            inputs, _ = self.client.get_hardware_ports(timeout=5.0)
            if not inputs:
                print("⚠️ No hardware inputs detected, using defaults")
                inputs = ["capture_1", "capture_2"]

        if hw_config.outputs is not None:
            outputs = hw_config.outputs
        else:
            _, outputs = self.client.get_hardware_ports(timeout=0.1)
            if not outputs:
                print("⚠️ No hardware outputs detected, using defaults")
                outputs = ["playback_1", "playback_2"]

        print(f"Hardware ports: inputs={inputs}, outputs={outputs}")
        return inputs, outputs

    def set_callbacks(
        self,
        on_param_change: OnParamChangeCallback | None = None,
        on_bypass_change: OnBypassChangeCallback | None = None,
        on_slot_added: OnSlotAddedCallback | None = None,
        on_slot_removed: OnSlotRemovedCallback | None = None,
        on_order_change: OnOrderChangeCallback | None = None,
    ):
        """Set external callbacks for UI updates."""
        self._ext_on_param_change = on_param_change
        self._ext_on_bypass_change = on_bypass_change
        self._ext_on_slot_added = on_slot_added
        self._ext_on_slot_removed = on_slot_removed
        self._ext_on_order_change = on_order_change

    # =========================================================================
    # WebSocket event handlers (Server-as-Source-of-Truth)
    # =========================================================================

    def _find_slot_by_label(self, label: str) -> Slot | None:
        """Find slot by its plugin label."""
        for slot in self.slots:
            if slot.label == label:
                return slot
        return None

    def _find_plugin_by_label(self, label: str) -> Plugin | None:
        """Find plugin by its label."""
        slot = self._find_slot_by_label(label)
        return slot.plugin if slot else None

    def _on_param_change(self, label: str, symbol: str, value: float):
        """Handle parameter change from WebSocket."""
        plugin = self._find_plugin_by_label(label)
        if plugin and symbol in plugin.controls:
            plugin.controls._controls[symbol].value = value
            if self._ext_on_param_change:
                self._ext_on_param_change(label, symbol, value)

    def _on_bypass_change(self, label: str, bypassed: bool):
        """Handle bypass change from WebSocket."""
        plugin = self._find_plugin_by_label(label)
        if plugin:
            plugin._bypassed = bypassed
            if self._ext_on_bypass_change:
                self._ext_on_bypass_change(label, bypassed)

    def _on_order_change(self, order: list[str]):
        """Handle order change broadcast from another client.

        Reorders local slots to match the received order.
        Only reorders slots that exist locally.
        """
        print(f"Rig << ORDER: {order}")

        # Build a mapping of label -> slot for quick lookup
        slot_map = {slot.label: slot for slot in self.slots}

        # Reorder slots based on received order
        new_slots = []
        for label in order:
            if label in slot_map:
                new_slots.append(slot_map[label])
                del slot_map[label]

        # Append any remaining slots that weren't in the order
        new_slots.extend(slot_map.values())

        # Check if order actually changed
        if [s.label for s in self.slots] == [s.label for s in new_slots]:
            print("  Order unchanged, skipping")
            return

        self.slots = new_slots
        print(f"  Reordered slots: {[s.label for s in self.slots]}")

        # Rebuild routing (seamless to avoid audio gap)
        self.reconnect_seamless()

        # Notify external callback
        if self._ext_on_order_change:
            self._ext_on_order_change(order)

    def _on_structural_change(self, msg_type: str, raw_message: str):
        """
        Handle structural change from WebSocket.

        Messages:
        - add instance uri x y bypassed pVersion offBuild
        - remove /graph/label
        - connect/disconnect - ignored (we manage routing)
        - load/reset - full rebuild
        """
        print(f"WS structural: {msg_type} - {raw_message}")
        parts = raw_message.split()

        if msg_type == "add" and len(parts) >= 3:
            # add instance uri x y bypassed pVersion offBuild
            # parts[0] = "add"
            # parts[1] = instance (e.g., "/graph/DS1_1")
            # parts[2] = uri
            # parts[3] = x, parts[4] = y (optional)
            instance = parts[1]
            uri = parts[2]
            x = None
            y = None
            if len(parts) >= 5:
                try:
                    x = float(parts[3])
                    y = float(parts[4])
                except Exception:
                    x = None
                    y = None

            if instance.startswith("/graph/"):
                label = instance[7:]
                self._on_plugin_added(label, uri, x, y)

        elif msg_type == "remove" and len(parts) >= 2:
            # remove /graph/label
            graph_path = parts[1]
            if graph_path.startswith("/graph/"):
                label = graph_path[7:]
                self._on_plugin_removed(label)

        elif msg_type in ("load", "reset"):
            # Full pedalboard change - rebuild everything
            self._on_pedalboard_reset()

    def _on_plugin_added(self, label: str, uri: str, x: float | None = None, y: float | None = None):
        """
        Handle plugin added via WebSocket feedback.

        Creates Slot, fetches port info, connects to chain.
        """
        # Перевіряємо чи такий слот вже існує
        existing = self._find_slot_by_label(label)
        if existing:
            # Якщо слот вже існує — оновлюємо його метадані (uri, name, порти, контролі)
            print(f"  Slot {label} already exists, updating metadata")

            # Отримуємо інформацію про порти
            effect_data = self.client.effect_get(uri)
            if not effect_data:
                print(f"  Failed to get effect data for {uri}")
                return

            all_inputs = []
            all_outputs = []
            ports = effect_data.get("ports", {})
            audio_ports = ports.get("audio", {})

            for p in audio_ports.get("input", []):
                all_inputs.append(Port(
                    symbol=p["symbol"],
                    name=p.get("name", p["symbol"]),
                    graph_path=f"{label}/{p['symbol']}"
                ))
            for p in audio_ports.get("output", []):
                all_outputs.append(Port(
                    symbol=p["symbol"],
                    name=p.get("name", p["symbol"]),
                    graph_path=f"{label}/{p['symbol']}"
                ))

            # Apply port overrides from config
            plugin_config = self.config.get_plugin_by_uri(uri)
            if plugin_config and plugin_config.inputs is not None:
                inputs = [p for p in all_inputs if p.symbol in plugin_config.inputs]
            else:
                inputs = all_inputs

            if plugin_config and plugin_config.outputs is not None:
                outputs = [p for p in all_outputs if p.symbol in plugin_config.outputs]
            else:
                outputs = all_outputs

            # Update existing plugin
            plugin = existing.plugin
            plugin.uri = uri
            plugin.name = effect_data.get("name", label)
            plugin.inputs = inputs
            plugin.outputs = outputs
            plugin._load_controls(effect_data)

            # update UI position if provided
            if x is not None:
                plugin.ui_x = x
            if y is not None:
                plugin.ui_y = y

            # Notify UI about potential metadata changes
            if self._ext_on_slot_added:
                self._ext_on_slot_added(existing)
            return

        # Перевіряємо whitelist
        plugin_config = self.config.get_plugin_by_uri(uri)
        if not plugin_config:
            print(f"  Plugin {uri} not in whitelist, ignoring")
            return

        # Отримуємо інформацію про порти
        effect_data = self.client.effect_get(uri)
        if not effect_data:
            print(f"  Failed to get effect data for {uri}")
            return

        # Створюємо порти з effect/get
        all_inputs = []
        all_outputs = []

        # effect/get returns ports.audio.input/output as lists
        ports = effect_data.get("ports", {})
        audio_ports = ports.get("audio", {})

        print(f"  effect_data ports keys: {ports.keys() if ports else 'none'}")
        print(f"  audio_ports: {audio_ports}")

        for p in audio_ports.get("input", []):
            all_inputs.append(Port(
                symbol=p["symbol"],
                name=p.get("name", p["symbol"]),
                graph_path=f"{label}/{p['symbol']}"
            ))
        for p in audio_ports.get("output", []):
            all_outputs.append(Port(
                symbol=p["symbol"],
                name=p.get("name", p["symbol"]),
                graph_path=f"{label}/{p['symbol']}"
            ))

        print(f"  Parsed ports: inputs={[p.symbol for p in all_inputs]}, outputs={[p.symbol for p in all_outputs]}")

        # Застосовуємо port override з конфіга
        if plugin_config.inputs is not None:
            inputs = [p for p in all_inputs if p.symbol in plugin_config.inputs]
        else:
            inputs = all_inputs

        if plugin_config.outputs is not None:
            outputs = [p for p in all_outputs if p.symbol in plugin_config.outputs]
        else:
            outputs = all_outputs

        # Створюємо плагін
        plugin = Plugin(
            slot=None,  # Буде встановлено після створення Slot
            uri=uri,
            label=label,
            name=effect_data.get("name", label),
            inputs=inputs,
            outputs=outputs,
        )
        plugin._load_controls(effect_data)

        # Створюємо слот
        slot = Slot(self, plugin)
        plugin.slot = slot

        # Determine insert index: use pending_inserts (client-requested), else append to preserve server order
        desired_idx = None
        if label in self._pending_inserts:
            # Client explicitly requested a position (via request_add_plugin_at or replace)
            desired_idx = self._pending_inserts.pop(label)
            print(f"  Using pending insert index: {desired_idx}")
        else:
            # No client request - append to end to preserve server's ordering
            # (server sends plugins in the order they should appear)
            print(f"  Appending to preserve server order")
            desired_idx = len(self.slots)
        
        # Clamp index to valid range
        if desired_idx < 0:
            desired_idx = 0
        if desired_idx > len(self.slots):
            desired_idx = len(self.slots)
        
        self.slots.insert(desired_idx, slot)
        print(f"  Created slot: {slot} at index {desired_idx}")

        # Store UI position on plugin for future ordering
        if x is not None:
            slot.plugin.ui_x = x
        if y is not None:
            slot.plugin.ui_y = y

        # Connect into chain UNLESS we're still initializing (in which case we'll do one final reconnect)
        if self._initializing:
            print(f"  Skipping reconnect during initialization (will do final reconnect after loading)")
        else:
            self._reconnect_slot(slot)

        # If there was a pending insert index for this label (replace behavior) we already handled it above

        # Сповіщуємо UI
        if self._ext_on_slot_added:
            self._ext_on_slot_added(slot)
        # Ensure plugins are positioned on UI according to order
        try:
            self._layout_plugins()
        except Exception:
            pass

    def _on_plugin_removed(self, label: str):
        """
        Handle plugin removed via WebSocket feedback.

        Reconnects neighbors and removes Slot.
        """
        slot = self._find_slot_by_label(label)
        if not slot:
            print(f"  Slot {label} not found, skipping")
            return

        slot_idx = slot.index

        # Знаходимо сусідів
        src = self.input_slot
        for s in self.slots[:slot_idx]:
            src = s

        dst = self.output_slot
        for s in self.slots[slot_idx + 1:]:
            dst = s
            break

        # Reconnect neighbors UNLESS we're still initializing
        if not self._initializing:
            print(f"  Connecting neighbors: {src} -> {dst}")
            self._connect_pair(src, dst)

        # Видаляємо слот
        self.slots.remove(slot)
        print(f"  Removed slot: {label}")

        # Сповіщуємо UI
        if self._ext_on_slot_removed:
            self._ext_on_slot_removed(label)
        # Re-layout remaining plugins
        try:
            self._layout_plugins()
        except Exception:
            pass

    def _on_pedalboard_reset(self):
        """Handle full pedalboard reset/load."""
        print("  Full pedalboard reset - clearing local state")
        self.slots.clear()
        # TODO: можливо синхронізувати з /pedalboard/current
        self.reconnect()

    # =========================================================================
    # Request API (ініціювання без локальних змін)
    # =========================================================================

    def _generate_label(self, uri: str) -> str:
        """Generate unique label for plugin."""
        base = Slot._label_from_uri(uri)
        self._label_counter += 1
        return f"{base}_{self._label_counter}"

    def request_add_plugin(self, uri: str, x: int = 500, y: int = 400) -> str | None:
        """
        Request to add plugin via REST.

        Does NOT create local slot - waits for WS feedback.

        Args:
            uri: Plugin URI
            x, y: Position on MOD-UI

        Returns:
            label якщо REST OK, None якщо помилка
        """
        plugin_config = self.config.get_plugin_by_uri(uri)
        if not plugin_config:
            print(f"Plugin not supported: {uri}")
            return None

        label = self._generate_label(uri)

        result = self.client.effect_add(label, uri, x, y)

        if not result or not isinstance(result, dict) or not result.get("valid"):
            print(f"REST error: Failed to add plugin {uri}")
            return None

        print(f"REST OK: Requested add {label}, waiting for WS feedback")
        return label

    def request_add_plugin_at(self, uri: str, insert_index: int, x: int = 500, y: int = 400) -> str | None:
        """
        Request to add plugin and remember desired insert index so that when WS feedback
        arrives we can move the newly created slot into the requested position.
        """
        label = self._generate_label(uri)
        # Record desired index until WS reports the new plugin
        self._pending_inserts[label] = insert_index

        result = self.client.effect_add(label, uri, x, y)

        if not result or not isinstance(result, dict) or not result.get("valid"):
            # cleanup pending
            self._pending_inserts.pop(label, None)
            print(f"REST error: Failed to add plugin {uri}")
            return None

        print(f"REST OK: Requested add {label} at index {insert_index}, waiting for WS feedback")
        return label

    def request_remove_plugin(self, label: str) -> bool:
        """
        Request to remove plugin via REST.

        Does NOT remove local slot - waits for WS feedback.

        Args:
            label: Plugin label

        Returns:
            True якщо REST OK
        """
        result = self.client.effect_remove(label)

        if not result:
            print(f"REST error: Failed to remove plugin {label}")
            return False

        print(f"REST OK: Requested remove {label}, waiting for WS feedback")
        return True

    def move_slot(self, from_idx: int, to_idx: int):
        """
        Move slot to different position in chain.

        This is client-controlled (server doesn't care about order).

        Args:
            from_idx: Current position
            to_idx: New position
        """
        if from_idx < 0 or from_idx >= len(self.slots):
            return
        if to_idx < 0 or to_idx >= len(self.slots):
            return
        if from_idx == to_idx:
            return

        slot = self.slots.pop(from_idx)
        self.slots.insert(to_idx, slot)

        # Rebuild routing (seamless to avoid audio gap)
        self.reconnect_seamless()

        # Broadcast new order to other clients
        self.broadcast_order()

    def broadcast_order(self) -> bool:
        """Broadcast current slot order to all connected clients.

        Uses the first slot's label as carrier (required for the param_set hack).

        Returns:
            True if broadcast was sent successfully
        """
        if not self.slots:
            print("No slots to broadcast order")
            return False

        order = [slot.label for slot in self.slots]
        carrier_label = self.slots[0].label

        return self.client.ws.broadcast_order(order, carrier_label)

    # =========================================================================
    # Routing
    # =========================================================================

    def reconnect(self):
        """Rebuild all connections in the chain (break-before-make, causes audio gap)."""
        print("\n=== RECONNECT ===")

        chain = [self.input_slot] + self.slots + [self.output_slot]
        print(f"Chain: {' -> '.join(repr(s) for s in chain)}")

        self._disconnect_everything()

        for i in range(len(chain) - 1):
            self._connect_pair(chain[i], chain[i + 1])

        # After connections are rebuilt, update UI positions of plugins to reflect order
        try:
            self._layout_plugins()
        except Exception:
            pass

        print("=== RECONNECT DONE ===\n")

    def reconnect_seamless(self):
        """Rebuild all connections using make-before-break (no audio gap).

        1. Calculate desired connections for new chain
        2. Connect all new connections first
        3. Disconnect only connections that are no longer needed
        """
        print("\n=== RECONNECT SEAMLESS ===")

        chain = [self.input_slot] + self.slots + [self.output_slot]
        print(f"Chain: {' -> '.join(repr(s) for s in chain)}")

        # Calculate desired connections
        desired_connections: set[tuple[str, str]] = set()
        for i in range(len(chain) - 1):
            pairs = self._get_connection_pairs(chain[i], chain[i + 1])
            desired_connections.update(pairs)

        print(f"  Desired connections: {len(desired_connections)}")

        # Calculate all possible connections (current state unknown, so we consider all)
        all_possible: set[tuple[str, str]] = set()
        all_outputs = list(self.input_slot.outputs)
        all_inputs = list(self.output_slot.hw_inputs)
        for slot in self.slots:
            all_outputs.extend(slot.outputs)
            all_inputs.extend(slot.inputs)
        for out in all_outputs:
            for inp in all_inputs:
                all_possible.add((out, inp))

        # Connections to remove = all possible minus desired
        to_disconnect = all_possible - desired_connections

        # MAKE: Connect all desired (idempotent - server ignores if already connected)
        print(f"  Connecting {len(desired_connections)} pairs...")
        for out_path, in_path in desired_connections:
            try:
                self.client.effect_connect(out_path, in_path)
            except Exception:
                pass

        # BREAK: Disconnect only what's not needed
        print(f"  Disconnecting {len(to_disconnect)} obsolete pairs...")
        for out_path, in_path in to_disconnect:
            try:
                self.client.effect_disconnect(out_path, in_path)
            except Exception:
                pass

        # Update UI positions
        try:
            self._layout_plugins()
        except Exception:
            pass

        print("=== RECONNECT SEAMLESS DONE ===\n")

    def _get_connection_pairs(self, src, dst) -> list[tuple[str, str]]:
        """Calculate connection pairs between src and dst (without connecting)."""
        outputs = src.outputs

        if isinstance(dst, HardwareSlot):
            inputs = dst.hw_inputs
        else:
            inputs = dst.inputs if hasattr(dst, 'inputs') else []

        if not outputs or not inputs:
            return []

        # Check join flags
        join_outputs = False
        join_inputs = False

        if isinstance(src, HardwareSlot):
            join_outputs = self.config.hardware.join_inputs
        elif hasattr(src, 'plugin') and src.plugin:
            src_config = self.config.get_plugin_by_uri(src.plugin.uri)
            join_outputs = src_config.join_outputs if src_config else False

        if isinstance(dst, HardwareSlot):
            join_inputs = self.config.hardware.join_outputs
        elif hasattr(dst, 'plugin') and dst.plugin:
            dst_config = self.config.get_plugin_by_uri(dst.plugin.uri)
            join_inputs = dst_config.join_inputs if dst_config else False

        connections = []

        if join_outputs or join_inputs:
            # All-to-all
            for out in outputs:
                for inp in inputs:
                    connections.append((out, inp))
        else:
            # Pair by index
            for i, out in enumerate(outputs):
                in_idx = min(i, len(inputs) - 1)
                connections.append((out, inputs[in_idx]))

            if len(inputs) > len(outputs):
                last_out = outputs[-1]
                for inp in inputs[len(outputs):]:
                    connections.append((last_out, inp))

        return connections

    def _reconnect_slot(self, slot: Slot):
        """
        Connect a slot into the chain (make-before-break).

        1. Connect new slot into chain
        2. Disconnect old direct path
        """
        slot_idx = slot.index
        if slot_idx < 0:
            return

        # Find neighbors
        src = self.input_slot if slot_idx == 0 else self.slots[slot_idx - 1]
        dst = self.output_slot if slot_idx == len(self.slots) - 1 else self.slots[slot_idx + 1]

        # Connect new path
        print(f"  Connect: {src} -> {slot}")
        self._connect_pair(src, slot)

        print(f"  Connect: {slot} -> {dst}")
        self._connect_pair(slot, dst)

        # Disconnect old direct path
        print(f"  Disconnect: {src} -> {dst}")
        self._disconnect_pair(src, dst)

    def _connect_pair(self, src, dst):
        """Connect src outputs to dst inputs."""
        outputs = src.outputs

        if isinstance(dst, HardwareSlot):
            inputs = dst.hw_inputs
        else:
            inputs = dst.inputs if hasattr(dst, 'inputs') else []

        if not outputs or not inputs:
            return

        # Check join flags
        join_outputs = False
        join_inputs = False

        if isinstance(src, HardwareSlot):
            join_outputs = self.config.hardware.join_inputs
        elif hasattr(src, 'plugin') and src.plugin:
            src_config = self.config.get_plugin_by_uri(src.plugin.uri)
            join_outputs = src_config.join_outputs if src_config else False

        if isinstance(dst, HardwareSlot):
            join_inputs = self.config.hardware.join_outputs
        elif hasattr(dst, 'plugin') and dst.plugin:
            dst_config = self.config.get_plugin_by_uri(dst.plugin.uri)
            join_inputs = dst_config.join_inputs if dst_config else False

        connections = []

        if join_outputs or join_inputs:
            # All-to-all
            for out in outputs:
                for inp in inputs:
                    connections.append((out, inp))
        else:
            # Pair by index
            for i, out in enumerate(outputs):
                in_idx = min(i, len(inputs) - 1)
                connections.append((out, inputs[in_idx]))

            if len(inputs) > len(outputs):
                last_out = outputs[-1]
                for inp in inputs[len(outputs):]:
                    connections.append((last_out, inp))

        print(f"    Connecting: {connections}")
        for out_path, in_path in connections:
            self.client.effect_connect(out_path, in_path)

    def _disconnect_pair(self, src, dst):
        """Disconnect connections between src and dst."""
        outputs = src.outputs

        if isinstance(dst, HardwareSlot):
            inputs = dst.hw_inputs
        else:
            inputs = dst.inputs if hasattr(dst, 'inputs') else []

        for out in outputs:
            for inp in inputs:
                try:
                    self.client.effect_disconnect(out, inp)
                except Exception:
                    pass

    def _disconnect_everything(self):
        """Disconnect all connections."""
        all_outputs = list(self.input_slot.outputs)
        all_inputs = list(self.output_slot.hw_inputs)

        for slot in self.slots:
            all_outputs.extend(slot.outputs)
            all_inputs.extend(slot.inputs)

        for out in all_outputs:
            for inp in all_inputs:
                try:
                    self.client.effect_disconnect(out, inp)
                except Exception:
                    pass

    def _layout_plugins(self, step: int = 500, base_x: int = 200, y: int = 400):
        """
        Position plugins on the MOD-UI horizontally according to their order.

        Args:
            step: X step between plugins
            base_x: X coordinate for first plugin
            y: Y coordinate for all plugins
        """
        for idx, slot in enumerate(self.slots):
            x = base_x + idx * step
            try:
                self.client.effect_position(slot.label, x, y)
            except Exception:
                pass

    # =========================================================================
    # Convenience API
    # =========================================================================

    def __getitem__(self, key: SupportsIndex) -> Slot:
        return self.slots[key]

    def __len__(self) -> int:
        return len(self.slots)

    def get_slot_by_label(self, label: str) -> Slot | None:
        """Find slot by label."""
        return self._find_slot_by_label(label)

    def list_available_plugins(self) -> list[PluginConfig]:
        """List plugins from config."""
        return self.config.plugins

    def list_categories(self) -> list[str]:
        """List plugin categories."""
        return self.config.list_categories()

    def get_plugins_by_category(self, category: str) -> list[PluginConfig]:
        """Get plugins by category."""
        return self.config.get_plugins_by_category(category)

    def clear(self):
        """Request removal of all plugins."""
        for slot in list(self.slots):
            self.request_remove_plugin(slot.label)

    # =========================================================================
    # State Management (Presets)
    # =========================================================================

    def get_state(self) -> dict:
        """Get current rig state as a serializable dict."""
        slots_state = []
        for slot in self.slots:
            slots_state.append({
                "label": slot.label,
                "uri": slot.plugin.uri,
                "controls": slot.plugin.get_state(),
                "bypassed": getattr(slot.plugin, "_bypassed", False),
            })

        return {"slots": slots_state}

    def set_state(self, state: dict):
        """
        Restore rig state from a saved dict.

        Note: This requests plugins via REST and waits for WS feedback.
        """
        slots_state = state.get("slots", [])

        # Clear existing
        self.clear()

        # Request plugins (will be created via WS feedback)
        for slot_state in slots_state:
            uri = slot_state.get("uri")
            if uri:
                label = self.request_add_plugin(uri)
                # TODO: restore controls and bypass after WS feedback

    def save_preset(self, filepath: str):
        """Save current rig state to a JSON file."""
        import json
        state = self.get_state()
        with open(filepath, "w") as f:
            json.dump(state, f, indent=2)
        print(f"Preset saved to {filepath}")

    def load_preset(self, filepath: str):
        """Load rig state from a JSON file."""
        import json
        with open(filepath, "r") as f:
            state = json.load(f)
        self.set_state(state)
        print(f"Preset loaded from {filepath}")

    def __repr__(self):
        slots_str = ", ".join(f"{i}:{s.label}" for i, s in enumerate(self.slots))
        return f"Rig([{slots_str}])"
