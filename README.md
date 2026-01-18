# modep-rig

Python client for MODEP/MOD-UI audio plugin host.

## Features

- REST API client for MOD-UI
- WebSocket client for real-time parameter control
- Rig management with automatic audio routing
- Plugin whitelist with port override support

## Installation

```bash
pip install -e .
```

## Configuration

Create `config.toml`:

```toml
[server]
url = "http://127.0.0.1:18181/"

[hardware]
# Hardware ports are auto-detected from MOD-UI by default.
# Uncomment to override with specific ports:
# inputs = ["capture_1", "capture_2"]   # Hardware inputs (from audio interface)
# outputs = ["playback_1", "playback_2"] # Hardware outputs (to audio interface)

[rig]
slots_limit = 8             # Max slots allowed (optional, default: unlimited)

# Supported plugins (whitelist)
[[plugins]]
name = "DS1"
uri = "http://moddevices.com/plugins/mod-devel/DS1"
category = "distortion"

# Plugin with port override (use stereo plugin as mono)
[[plugins]]
name = "MVerb"
uri = "http://distrho.sf.net/plugins/MVerb"
category = "reverb"
inputs = ["lv2_audio_in_1"]     # Use only first input
outputs = ["lv2_audio_out_1"]   # Use only first output
```

### Port Override

Use `inputs` and `outputs` arrays to override which ports are used for routing.
This is useful for:
- Using stereo/quad plugins in mono chain
- Selecting specific channels from multi-channel plugins

### Audio Routing

Rig automatically connects plugins in a chain: `Input -> [Slot 0] -> [Slot 1] -> ... -> Output`

Default routing logic (index-based pairing):
- `mono -> mono`: `out[0] -> in[0]`
- `mono -> stereo`: `out[0] -> in[0], out[0] -> in[1]`
- `stereo -> mono`: `out[0] -> in[0], out[1] -> in[0]`
- `stereo -> stereo`: `out[0] -> in[0], out[1] -> in[1]`

When there are more outputs than inputs, extra outputs connect to the last available input.
When there are more inputs than outputs, the last output is duplicated to remaining inputs.

### Join Mode (All-to-All Routing)

For plugins that need all outputs connected to all inputs (e.g., mixers, splitters), use `join_inputs` or `join_outputs`:

```toml
[[plugins]]
name = "Triple chorus"
uri = "http://drobilla.net/plugins/fomp/triple_chorus"
category = "modulator"
join_outputs = true  # All outputs connect to all inputs of next plugin
```

- `join_outputs = true` on source plugin: all its outputs connect to all inputs of the next plugin
- `join_inputs = true` on destination plugin: all outputs from previous plugin connect to all its inputs

### Hardware Port Auto-Detection

By default, hardware ports are automatically detected from MOD-UI via WebSocket on startup.
The detected ports (e.g., `capture_1`, `capture_2`, `playback_1`, `playback_2`) are used for routing.

To override auto-detection, specify ports explicitly in config:

```toml
[hardware]
inputs = ["capture_1"]   # Override: use only first input
outputs = ["playback_1", "playback_2"]
```

### Hardware Join Mode

Similar join routing is available for hardware inputs/outputs:

```toml
[hardware]
join_inputs = true   # All hardware inputs connect to all inputs of first plugin
join_outputs = true  # All outputs of last plugin connect to all hardware outputs
```

This is useful for:
- Summing stereo input to mono plugin
- Duplicating mono output to stereo hardware outputs

## Usage

```python
from modep_rig import Config, Rig

config = Config.load("config.toml")
rig = Rig(config)

# Add empty slot
slot = rig.add_slot()

# Load plugin into empty slot (auto-connects)
slot.load_and_connect("http://moddevices.com/plugins/mod-devel/DS1")

# Replace plugin in occupied slot (make-before-break: no audio interruption)
slot.replace("http://distrho.sf.net/plugins/MVerb")

# Unload plugin but keep empty slot
slot.unload_plugin()

# Remove slot entirely (connects neighbors first for seamless transition)
slot.unload()

# Access plugin controls
plugin = rig[0].plugin
plugin.set_control("gain", 0.5)

# Save/load presets
rig.save_preset("my_preset.json")
rig.load_preset("my_preset.json")

# Clear all slots
rig.clear()
```

## UI Controls

The rig_ui.py GUI provides intuitive controls:

- **Slot Widgets**: Visual representation of each plugin slot in the chain
  - **Right-click context menu**:
    - **Empty slots**: Load Plugin
    - **Filled slots**: Replace Plugin, Unload Plugin (Keep Slot)
    - **All slots**: Remove Slot

- **Controls Panel**: Displays plugin controls for selected slot
  - Plugin name and bypass toggle
  - Parameter controls in grid layout
  - All plugin modifications done via slot context menus (no buttons in panel)

## Make-Before-Break Plugin Switching

The rig implements seamless plugin switching without audio interruption:

- **`slot.load_and_connect(uri)`** - Load plugin into empty slot with automatic connection
- **`slot.replace(uri)`** - Replace plugin with new one: loads new, connects new, removes old
- **`slot.unload_plugin()`** - Remove plugin only, keep empty slot
- **`slot.unload()`** - Remove entire slot, connecting neighbors directly for seamless transition

This ensures smooth audio flow without gaps or artifacts.

## API

### Config

- `Config.load(path)` - Load config from TOML file
- `config.is_supported(uri)` - Check if plugin URI is in whitelist
- `config.get_plugin_by_uri(uri)` - Get PluginConfig by URI
- `config.get_plugin_by_name(name)` - Get PluginConfig by name
- `config.get_plugins_by_category(category)` - List plugins by category

### Rig

- `rig.add_slot(position=None)` - Add new empty slot
- `rig.remove_slot(slot)` - Remove slot from rig (use Slot.unload() instead)
- `rig.get_slot(uuid)` - Find slot by UUID
- `rig.clear()` - Clear all slots
- `rig.save_preset(path)` - Save rig state to JSON
- `rig.load_preset(path)` - Load rig state from JSON
- `rig._reconnect_slot(slot)` - Partial reconnection for single slot (internal, used by make-before-break)

### Slot

- `slot.load(uri)` - Load plugin (removes old first, no auto-connect)
- `slot.load_and_connect(uri)` - Load plugin into empty slot with automatic connection
- `slot.load_by_name(name)` - Load plugin by name from config
- `slot.replace(uri)` - Replace plugin using make-before-break (new connects BEFORE old removes)
- `slot.unload()` - Remove entire slot, connects neighbors first for seamless transition
- `slot.unload_plugin()` - Remove plugin only, keep empty slot for later loading
- `slot.is_empty` - Check if slot has no plugin
- `slot.index` - Get slot position in rig

### Client (WebSocket)

- `client.ws.effect_parameter_set(label, symbol, value)` - Set parameter
- `client.ws.effect_bypass(label, bypass)` - Toggle bypass
