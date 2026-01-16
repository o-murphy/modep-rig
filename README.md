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
inputs = ["capture_1", "capture_2"]   # Hardware inputs (from audio interface)
outputs = ["playback_1", "playback_2"] # Hardware outputs (to audio interface)

[rig]
slot_count = 4              # Number of effect slots in chain

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

### Hardware Join Mode

Similar join routing is available for hardware inputs/outputs:

```toml
[hardware]
inputs = ["capture_1", "capture_2"]
outputs = ["playback_1", "playback_2"]
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

# Load plugin by name (from config)
rig[0] = "DS1"

# Load plugin by URI
rig[1] = "http://distrho.sf.net/plugins/MVerb"

# Clear slot
rig[0] = None

# Access plugin controls
plugin = rig[0].plugin
plugin.set_control("gain", 0.5)

# Bypass via WebSocket (real-time)
rig.client.ws.effect_bypass("DS1_0", True)
```

## API

### Config

- `Config.load(path)` - Load config from TOML file
- `config.is_supported(uri)` - Check if plugin URI is in whitelist
- `config.get_plugin_by_uri(uri)` - Get PluginConfig by URI
- `config.get_plugin_by_name(name)` - Get PluginConfig by name
- `config.get_plugins_by_category(category)` - List plugins by category

### Rig

- `rig[n] = uri/name/None` - Load/unload plugin in slot
- `rig.reconnect()` - Rebuild audio connections
- `rig.clear()` - Clear all slots

### Client (WebSocket)

- `client.ws.effect_parameter_set(label, symbol, value)` - Set parameter
- `client.ws.effect_bypass(label, bypass)` - Toggle bypass
