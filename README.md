# mod-rack

Python client for MODEP/MOD-UI audio plugin host.

## Features

- REST API client for MOD-UI
- WebSocket client for real-time parameter feedback
- Reactive architecture (Server-as-Source-of-Truth)
- Automatic audio and MIDI routing with smart channel pairing
- Multiple routing modes (linear, hard_bypass, dual_track)
- Plugin whitelist with port override support

## Architecture

The rack uses a **reactive architecture** where the server is the source of truth:

```
Client wants to add plugin:
  orchestrator.request_add_plugin(uri) → REST request
    ├─ OK: do nothing, wait for WS feedback
    └─ ERROR: signal error to UI
  ...
  WS: "add /graph/label ..." → _on_graph_plugin_add() → PluginSlot created, connected

Client wants to remove plugin:
  orchestrator.request_remove_plugin(label) → REST request
    ├─ OK: do nothing, wait for WS feedback
    └─ ERROR: signal error to UI
  ...
  WS: "remove /graph/label" → _on_graph_plugin_remove() → PluginSlot removed
```

This enables:
- Synchronization with MOD-UI browser interface
- Multi-client support (future)
- External changes reflected automatically

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

[rack]
routing_mode = "hard_bypass"

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

### Routing Modes

The `routing_mode` parameter controls how audio and MIDI signals are routed through the plugin chain:

- **`hard_bypass`** (default) — each output seeks the nearest next input of the same type; plugins without matching ports are bypassed. Audio and MIDI are routed independently.
- **`dual_track`** — audio and MIDI form separate parallel chains. Audio-only plugins are skipped in the MIDI chain and vice versa.
- **`linear`** — strict sequential chain 1→2→3, connecting both audio and MIDI between adjacent slots.

### Audio Routing

Rack automatically connects plugins in a chain: `Input -> [Slot 0] -> [Slot 1] -> ... -> Output`

Default routing logic (index-based pairing):
- `mono -> mono`: `out[0] -> in[0]`
- `mono -> stereo`: `out[0] -> in[0], out[0] -> in[1]`
- `stereo -> mono`: `out[0] -> in[0], out[1] -> in[0]`
- `stereo -> stereo`: `out[0] -> in[0], out[1] -> in[1]`

When there are more outputs than inputs, extra outputs connect to the last available input.
When there are more inputs than outputs, the last output is duplicated to remaining inputs.

### Join Mode (All-to-All Routing)

For plugins that need all outputs connected to all inputs (e.g., mixers, splitters), use `join_audio_inputs` or `join_audio_outputs`:

```toml
[[plugins]]
name = "Triple chorus"
uri = "http://drobilla.net/plugins/fomp/triple_chorus"
category = "modulator"
join_audio_outputs = true  # All outputs connect to all inputs of next plugin
```

- `join_audio_outputs = true` on source plugin: all its outputs connect to all inputs of the next plugin
- `join_audio_inputs = true` on destination plugin: all outputs from previous plugin connect to all its inputs

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
join_audio_inputs = true   # All hardware inputs connect to all inputs of first plugin
join_audio_outputs = true  # All outputs of last plugin connect to all hardware outputs
```

This is useful for:
- Summing stereo input to mono plugin
- Duplicating mono output to stereo hardware outputs

## Usage

```python
from mod_rack import Config, Orchestrator

config = Config.load("config.toml")
orch = Orchestrator(config)

# Request to add plugin (async - waits for WS feedback)
label = orch.request_add_plugin("http://moddevices.com/plugins/mod-devel/DS1")

# Request to remove plugin
orch.request_remove_plugin(label)

# Access plugin controls (after slot is created via WS feedback)
slot = orch.get_slot_by_label(label)
if slot:
    plugin = slot.plugin
    plugin.param_set("gain", 0.5)
    plugin.bypass(True)

# Reorder slots
orch.move_slot(from_idx=0, to_idx=2)

# Clear all plugins
orch.clear()
```

## API

### Config

- `Config.load(path)` - Load config from TOML file
- `Config.parse(data)` - Parse config from TOML string
- `config.is_supported(uri)` - Check if plugin URI is in whitelist
- `config.get_plugin_by_uri(uri)` - Get PluginConfig by URI
- `config.get_plugin_by_name(name)` - Get PluginConfig by name
- `config.get_plugins_by_category(category)` - List plugins by category

### Orchestrator

- `orch.request_add_plugin(uri)` - Request to add plugin (returns label or None)
- `orch.request_remove_plugin(label)` - Request to remove plugin (returns bool)
- `orch.move_slot(from_idx, to_idx)` - Reorder slots in chain
- `orch.get_slot_by_label(label)` - Find slot by label
- `orch.clear()` - Request removal of all plugins

### PluginSlot

- `slot.label` - Plugin label (unique identifier)
- `slot.plugin` - The Plugin instance
- `slot.audio_inputs` / `slot.audio_outputs` - Audio port paths
- `slot.midi_inputs` / `slot.midi_outputs` - MIDI port paths

### Plugin

- `plugin.param_set(symbol, value)` - Set parameter value
- `plugin.bypass(state)` - Toggle bypass
- `plugin.bypassed` - Bypass state
- `plugin.controls` - Dict of ControlPort instances
