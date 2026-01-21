from typing import Callable, SupportsIndex

import secrets
import string

from modep_rig.config import Config, PluginConfig
from modep_rig.client import Client, PositionChange
from modep_rig.plugin import Plugin, Port


# Type aliases for callbacks
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

    def __init__(self, plugin: Plugin):
        """
        Створює слот з плагіном.

        Args:
            rig: Батьківський Rig
            plugin: Плагін (обов'язковий)
        """
        self.plugin = plugin

    @property
    def label(self) -> str:
        """Унікальний ідентифікатор слота (label плагіна)."""
        return self.plugin.label

    @property
    def inputs(self) -> list[str]:
        return [p.graph_path for p in self.plugin.inputs]

    @property
    def outputs(self) -> list[str]:
        return [p.graph_path for p in self.plugin.outputs]

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

    def __init__(self, ports: list[str], is_input: bool):
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

    def __init__(
        self, config: Config, client: Client = None, reset_on_init: bool = False
    ):
        self.config = config
        # If caller did not provide a Client, create one but delay WebSocket
        # connection until after callbacks are installed to avoid missing messages.
        if client is None:
            self.client = Client(config.server.url, connect=False)
        else:
            self.client = client

        # Determine hardware ports (auto-detect or from config)
        hw_inputs, hw_outputs = self._resolve_hardware_ports()

        self.input_slot = HardwareSlot(ports=hw_inputs, is_input=True)
        self.output_slot = HardwareSlot(ports=hw_outputs, is_input=False)

        # Slots list - порядок визначається по координатах (x, y)
        self.slots: list[Slot] = []

        # Flag to defer reconnections during initial pedalboard loading
        self._initializing = True
        # Flag to prevent recursive position updates during normalization
        self._normalizing = False

        # External callbacks (for UI)
        self._ext_on_slot_added: OnSlotAddedCallback | None = None
        self._ext_on_slot_removed: OnSlotRemovedCallback | None = None
        self._ext_on_order_change: OnOrderChangeCallback | None = None

        # # Setup WebSocket callbacks BEFORE connecting so we don't miss initial messages
        self.client.ws.set_callbacks(
            on_structural_change=self._on_structural_change,
            on_order_change=self._on_order_change,
        )

        self.client.ws.on(PositionChange, self._on_position_change)

        # If the client was created with connect=False we need to start it now so the
        # callbacks will receive the server's initial messages. Otherwise connecting
        # has already happened during Client construction.
        try:
            # Only call connect if the underlying WsClient hasn't connected yet
            if (
                not self.client.ws
                or not self.client.ws.conn
                or not self.client.ws.conn.is_connected
            ):
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

        if self.slots:
            print(f"Loaded {len(self.slots)} slots from server")
            # Sort slots by position and normalize to clean grid
            self.slots = self._sort_slots_by_position(self.slots)
            self.reconnect_seamless()
            self._normalize_positions()
        else:
            print("No slots loaded from server")

        if reset_on_init:
            self.client.reset()
            self.reconnect()

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
        on_slot_added: OnSlotAddedCallback | None = None,
        on_slot_removed: OnSlotRemovedCallback | None = None,
        on_order_change: OnOrderChangeCallback | None = None,
    ):
        """Set external callbacks for UI updates."""
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

    def _on_position_change(self, event: PositionChange):
        """Handle position change from WebSocket.

        Updates slot position and reorders slots based on new coordinates.
        """
        # Skip position updates during normalization (we're sending, not receiving)
        if self._normalizing:
            return

        slot = self._find_slot_by_label(event.label)
        if not slot:
            print(f"  Position change for unknown slot {event.label}, ignoring")
            return

        old_x = slot.plugin.ui_x
        old_y = slot.plugin.ui_y

        slot.plugin.ui_x = event.x
        slot.plugin.ui_y = event.y

        print(
            f"Rig << POSITION: {event.label} ({old_x}, {old_y}) -> ({event.x}, {event.y})"
        )

        # Skip reordering during initialization
        if self._initializing:
            return

        # Reorder slots based on new positions and reconnect if order changed
        self._reorder_by_position()

    def _reorder_by_position(self):
        """Reorder slots based on their UI positions using Y-clustering."""
        if not self.slots:
            return

        old_order = [s.label for s in self.slots]
        self.slots = self._sort_slots_by_position(self.slots)
        new_order = [s.label for s in self.slots]

        if old_order != new_order:
            print(f"  Order changed: {old_order} -> {new_order}")
            self.reconnect_seamless()
            if self._ext_on_order_change:
                self._ext_on_order_change(new_order)
        else:
            print("  Order unchanged after position update")

        # Always normalize positions after any position change
        self._normalize_positions()

    def _normalize_positions(
        self,
        x_step: int = 600,
        y_step: int = 600,
        base_x: int = 200,
        base_y: int = 400,
        max_per_row: int = 4,
    ):
        """Normalize slot positions to a grid with rows first, then columns.

        Places slots in rows of max_per_row plugins each, filling rows
        left-to-right before moving to the next row.

        Args:
            x_step: Horizontal spacing between plugins
            y_step: Vertical spacing between rows
            base_x: X coordinate for first plugin in each row
            base_y: Y coordinate for first row
            max_per_row: Maximum plugins per row (default 5)
        """
        if not self.slots:
            return

        print("  Normalizing positions to grid...")

        self._normalizing = True
        try:
            for idx, slot in enumerate(self.slots):
                row = idx // max_per_row
                col = idx % max_per_row

                new_x = base_x + col * x_step
                new_y = base_y + row * y_step

                old_x = slot.plugin.ui_x
                old_y = slot.plugin.ui_y

                # Only update if position actually changed significantly
                if abs(new_x - old_x) > 10 or abs(new_y - old_y) > 10:
                    slot.plugin.ui_x = new_x
                    slot.plugin.ui_y = new_y
                    self.client.effect_position(slot.label, new_x, new_y)
                    print(f"    {slot.label}: ({old_x}, {old_y}) -> ({new_x}, {new_y})")
        finally:
            self._normalizing = False

    def _sort_slots_by_position(
        self, slots: list["Slot"], y_threshold: float = 150.0
    ) -> list["Slot"]:
        """Sort slots by position using Y-clustering.

        Slots are grouped into rows based on Y-coordinate proximity,
        then sorted left-to-right within each row.

        Args:
            slots: List of slots to sort
            y_threshold: Max Y difference to be considered same row

        Returns:
            Sorted list of slots
        """
        if not slots:
            return []

        # Sort by Y first to find clusters
        sorted_by_y = sorted(slots, key=lambda s: s.plugin.ui_y or 0)

        # Assign row numbers based on Y-clustering
        rows: dict["Slot", int] = {}
        current_row = 0
        prev_y: float | None = None

        for slot in sorted_by_y:
            y = slot.plugin.ui_y or 0
            if prev_y is not None and (y - prev_y) > y_threshold:
                current_row += 1
            rows[slot] = current_row
            prev_y = y

        # Sort by (row, x)
        return sorted(slots, key=lambda s: (rows[s], s.plugin.ui_x or 0))

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

    def _load_plugin_ports(
        self, label: str, uri: str, effect_data: dict
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
        plugin_config = self.config.get_plugin_by_uri(uri)
        if plugin_config and plugin_config.inputs is not None:
            inputs = [p for p in all_inputs if p.symbol in plugin_config.inputs]
        else:
            inputs = all_inputs

        if plugin_config and plugin_config.outputs is not None:
            outputs = [p for p in all_outputs if p.symbol in plugin_config.outputs]
        else:
            outputs = all_outputs

        return inputs, outputs

    def _on_plugin_added(
        self, label: str, uri: str, x: float | None = None, y: float | None = None
    ):
        """
        Handle plugin added via WebSocket feedback.

        Creates Slot, fetches port info, connects to chain.
        """
        # Перевіряємо чи такий слот вже існує
        existing = self._find_slot_by_label(label)
        if existing:
            # Якщо слот вже існує — оновлюємо його метадані
            print(f"  Slot {label} already exists, updating metadata")
            plugin = existing.plugin
            plugin.update_metadata()

            # Update UI position if provided
            if x is not None:
                plugin.ui_x = x
            if y is not None:
                plugin.ui_y = y

            # Notify UI about potential metadata changes
            if self._ext_on_slot_added:
                self._ext_on_slot_added(existing)
            return

        plugin = Plugin.load_supported(
            self.client,
            uri=uri,
            label=label,
            config=self.config,
        )

        if not plugin:
            return

        # Створюємо слот
        slot = Slot(plugin)

        # Store UI position on plugin
        slot.plugin.ui_x = x if x is not None else 0
        slot.plugin.ui_y = y if y is not None else 0

        # Add slot and sort by position
        self.slots.append(slot)
        self.slots = self._sort_slots_by_position(self.slots)
        print(
            f"  Created slot: {slot} at index {self.slots.index(slot)} (pos: {x}, {y})"
        )

        # Connect into chain UNLESS we're still initializing
        if self._initializing:
            print("  Skipping reconnect during initialization")
        else:
            self.reconnect_seamless()
            # Normalize positions to fit the new plugin into the grid
            self._normalize_positions()

        # Сповіщуємо UI
        if self._ext_on_slot_added:
            self._ext_on_slot_added(slot)

    def _on_plugin_removed(self, label: str):
        """
        Handle plugin removed via WebSocket feedback.

        Reconnects neighbors and removes Slot.
        """
        slot = self._find_slot_by_label(label)
        if not slot:
            print(f"  Slot {label} not found, skipping")
            return

        slot_idx = self.slots.index(slot)

        # Знаходимо сусідів
        src = self.input_slot
        for s in self.slots[:slot_idx]:
            src = s

        dst = self.output_slot
        for s in self.slots[slot_idx + 1 :]:
            dst = s
            break

        # Reconnect neighbors UNLESS we're still initializing
        if not self._initializing:
            print(f"  Connecting neighbors: {src} -> {dst}")
            self._connect_pair(src, dst)

        # Видаляємо слот
        self.slots.remove(slot)
        print(f"  Removed slot: {label}")

        # Normalize remaining positions to fill the gap
        if not self._initializing and self.slots:
            self._normalize_positions()

        # Сповіщуємо UI
        if self._ext_on_slot_removed:
            self._ext_on_slot_removed(label)

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
        alphabet = string.ascii_letters + string.digits
        uid = "".join(secrets.choice(alphabet) for _ in range(8))
        return f"{base}_{uid}"

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

    def request_add_plugin_at(self, uri: str, insert_index: int) -> str | None:
        """
        Request to add plugin at a specific chain position.

        Calculates the appropriate X,Y coordinates based on the target index,
        so the plugin will be sorted into the correct position.

        Args:
            uri: Plugin URI
            insert_index: Target position in the chain

        Returns:
            label if REST OK, None if error
        """
        # Calculate position for this index
        x, y = self._calculate_position_for_index(insert_index)

        return self.request_add_plugin(uri, x=int(x), y=int(y))

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

        Reorders locally, reconnects, and normalizes positions on server.

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

        print(f"Moving slot from idx {from_idx} to {to_idx}")

        # Reorder locally
        slot = self.slots.pop(from_idx)
        self.slots.insert(to_idx, slot)

        # Reconnect with new order
        self.reconnect_seamless()

        # Normalize positions (updates server)
        self._normalize_positions()

        # Notify UI
        if self._ext_on_order_change:
            self._ext_on_order_change([s.label for s in self.slots])

    def _calculate_position_for_index(
        self, target_idx: int, exclude_slot: "Slot | None" = None, x_step: float = 500.0
    ) -> tuple[float, float]:
        """Calculate X,Y position for inserting a slot at target index.

        Args:
            target_idx: Target position in the chain
            exclude_slot: Slot to exclude from calculations (the one being moved)
            x_step: Horizontal spacing between plugins

        Returns:
            (x, y) coordinates
        """
        # Get slots excluding the one being moved
        other_slots = [s for s in self.slots if s != exclude_slot]

        if not other_slots:
            return (200.0, 400.0)

        # Sort other slots by current position
        sorted_slots = self._sort_slots_by_position(other_slots)

        if target_idx <= 0:
            # Insert before first slot
            first = sorted_slots[0]
            first_x = first.plugin.ui_x or 200
            first_y = first.plugin.ui_y or 400
            return (first_x - x_step, first_y)

        if target_idx >= len(sorted_slots):
            # Insert after last slot
            last = sorted_slots[-1]
            last_x = last.plugin.ui_x or 200
            last_y = last.plugin.ui_y or 400
            return (last_x + x_step, last_y)

        # Insert between two slots
        prev_slot = sorted_slots[target_idx - 1]
        next_slot = sorted_slots[target_idx]

        prev_x = prev_slot.plugin.ui_x or 0
        prev_y = prev_slot.plugin.ui_y or 400
        next_x = next_slot.plugin.ui_x or 0

        # Position between prev and next
        new_x = (prev_x + next_x) / 2
        return (new_x, prev_y)

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

        print("=== RECONNECT SEAMLESS DONE ===\n")

    def _get_connection_pairs(self, src, dst) -> list[tuple[str, str]]:
        """Calculate connection pairs between src and dst (without connecting)."""
        outputs = src.outputs

        if isinstance(dst, HardwareSlot):
            inputs = dst.hw_inputs
        else:
            inputs = dst.inputs if hasattr(dst, "inputs") else []

        if not outputs or not inputs:
            return []

        # Check join flags
        join_outputs = False
        join_inputs = False

        if isinstance(src, HardwareSlot):
            join_outputs = self.config.hardware.join_inputs
        elif hasattr(src, "plugin") and src.plugin:
            src_config = self.config.get_plugin_by_uri(src.plugin.uri)
            join_outputs = src_config.join_outputs if src_config else False

        if isinstance(dst, HardwareSlot):
            join_inputs = self.config.hardware.join_outputs
        elif hasattr(dst, "plugin") and dst.plugin:
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
                for inp in inputs[len(outputs) :]:
                    connections.append((last_out, inp))

        return connections

    def _reconnect_slot(self, slot: Slot):
        """
        Connect a slot into the chain (make-before-break).

        1. Connect new slot into chain
        2. Disconnect old direct path
        """
        slot_idx = self.slots.index(slot)
        if slot_idx < 0:
            return

        # Find neighbors
        src = self.input_slot if slot_idx == 0 else self.slots[slot_idx - 1]
        dst = (
            self.output_slot
            if slot_idx == len(self.slots) - 1
            else self.slots[slot_idx + 1]
        )

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
        connections = self._get_connection_pairs(src, dst)
        if not connections:
            return

        print(f"    Connecting: {connections}")
        for out_path, in_path in connections:
            self.client.effect_connect(out_path, in_path)

    def _disconnect_pair(self, src, dst):
        """Disconnect connections between src and dst."""
        outputs = src.outputs

        if isinstance(dst, HardwareSlot):
            inputs = dst.hw_inputs
        else:
            inputs = dst.inputs if hasattr(dst, "inputs") else []

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
            slots_state.append(
                {
                    "label": slot.label,
                    "uri": slot.plugin.uri,
                    "controls": slot.plugin.get_state(),
                    "bypassed": slot.plugin.bypassed,
                }
            )

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
                # label = self.request_add_plugin(uri)
                # TODO: restore controls and bypass after WS feedback
                pass

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
