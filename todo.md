# TODO

## Future

- [ ] Output parameters
- [ ] Replace plugin with position preservation


## Done

- [x] **Reactive Architecture (Server-as-Source-of-Truth)**
  - Slot завжди містить плагін (немає пустих слотів)
  - Slot ідентифікується по label
  - WS feedback = source of truth
  - Клієнт контролює порядок і routing
  - `request_add_plugin()` / `request_remove_plugin()` - тільки REST запит
  - `_on_plugin_added()` / `_on_plugin_removed()` - реакція на WS
  - UI callbacks: `on_slot_added`, `on_slot_removed`
- [x] Make-before-break plugin switching
- [x] Bypass feedback in UI
- [x] Hardware port auto-detection
- [x] Hardware join config
- [x] WebSocket callbacks for params/bypass
- [x] Smart channel routing (mono/stereo conversion)
- [x] Plugin whitelist with port override
- [x] MIDI routing (ports, routing modes: linear, hard_bypass, dual_track)
