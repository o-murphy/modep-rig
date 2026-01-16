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
inputs = ["capture_1"]      # Hardware inputs (from audio interface)
outputs = ["playback_1"]    # Hardware outputs (to audio interface)

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
