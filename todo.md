now
* if we connecting slots according to it's order, so then slot id can be any or kinda uuid4 and we'll be able insert new slots between others
* find a way to make plugin switch with no full rig reconnect (rebuild) and make it smooth with "Make-before-break"
* bypass feedback not displaying in rig_ui.py
* can we load/save rig state from/to file
* structural changes should call Client.reset and rebuild board from current stored rig state

future
* proxy rig state for external devices like esp32/rp2040 via websocket or kinda
* can we support midi routing, how?

done
* ~~we have to detect hardware ports by rest api~~
* ~~we should have hardware ports overrides/routing configuration same as for plugins, i mean join_outs join_inputs~~
* ~~CRITICAL: client can't resolve diverence between once who triggered the structural change, and reacts on events created by itself!~~ 
