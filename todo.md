now
* ~~make plugin switch with no full rig reconnect ("Make-before-break") - new plugin loaded first, then old removed~~ ✓ DONE
  * ~~We have to guarantee path through so that the sound is not interrupted.~~ ✓
  * ~~On add plugin - firstly add it to the rig - connect to the chain - if success - remove old connection between it's neighbours~~ ✓
  * ~~On remove plugin - connect it's neghbours and only then remove the plugin~~ ✓
  * ~~On replace (Slot.load) - load new plugin, connect it - if success - remove old plugin~~ ✓
  * ~~Fixed rig_ui.py integration - uses _reconnect_slot() instead of reconnect()~~ ✓
  * ~~Add Load button to ControlsPanel for empty slots~~ ✓
  * ~~Add Replace button to ControlsPanel for filled slots~~ ✓
  * ~~Implement Slot.load_and_connect() for loading plugins into empty slots~~ ✓
  * ~~Implement Slot.replace() with make-before-break semantics~~ ✓
  * ~~Move Load/Replace buttons to context menu~~ ✓
  
  Implementation complete:
  - Method `Slot.load(uri)`: loads plugin (remove old first, no reconnect)
  - Method `Slot.load_and_connect(uri)`: loads plugin into empty slot and connects
  - Method `Slot.replace(uri)`: make-before-break replace (load new, connect new, remove old)
  - Method `Slot.unload()`: removes entire slot (connect neighbors first)
  - Method `Slot.unload_plugin()`: removes plugin only, keeps empty slot
  - Method `Rig._reconnect_slot(slot)`: partial reconnection for single slot with @suppress_structural
  - Method `Rig._disconnect_pair(src, dst)`: disconnect specific route pairs
  - Updated `SlotWidget`: context menu with Load/Replace/Unload/Remove options
  - Updated `ControlsPanel`: simple display of plugin controls without action buttons
* saving/restoring rig state to/from JSON is not comletely done, not tested, we have to comlete it. 
  * do we able to use .pedalboard files?
  * should we first make a server state syncing?
* outside structural changes: monitor and react to external changes (plugin removed/pedalboard loaded etc)
  * for now we can't completely know who controls the chain and what was added / removed from outside
  * the client now will be a source of true, so
    * if plugun added from outside client - remove it
    * if plugin removed from outside client - remove the to be consistent with the server

future
* make api client more statefull and syncronized, we have few ways and proposals:
  1. do we can to make full realtime mirror of server state to sync many clients with it in a same time? 
  2. If we can't mirror the server state and the client should be a source of true, the only way is master-slave pattern, where one client is a master and it proxyfies the original server. The other clients connect to the master as slaves and monitor/control it with the two way websocket or maybe BLE in future.
  * we be good if the client will be compatible with micropython or better the circuitpython (esp32/rp2040 etc) to be able create standalone controllers for the server or the rig.
* can we support midi routing, how?

done
* ~~dynamic slots with UUID - slots created on-demand, no fixed slot_count~~
* ~~bypass feedback not displaying in rig_ui.py~~
* ~~we have to detect hardware ports by rest api~~
* ~~we should have hardware ports overrides/routing configuration same as for plugins, i mean join_outs join_inputs~~
* ~~CRITICAL: client can't resolve diverence between once who triggered the structural change, and reacts on events created by itself!~~
