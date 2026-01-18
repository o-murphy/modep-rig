# Make-Before-Break Implementation

## Overview
Implemented seamless plugin switching without audio interruption using the "make-before-break" pattern.

## What Changed From Initial Implementation

Initial problem: Even when replacing a single plugin via `rig[0] = "new_plugin"`, 
the entire chain would be rebuilt (`reconnect()` called), causing audio interruption.

**Root causes:**
1. `Rig.__setitem__()` called `self.reconnect()` for all operations
2. `Rig._on_add_slot()` in rig_ui.py called `self.reconnect()` after adding
3. `Rig._reconnect_slot()` lacked `@suppress_structural` decorator

**Solutions implemented:**
1. Updated `__setitem__()` to use `_reconnect_slot()` for partial operations
2. Updated `remove_slot()` to use `unload()` (make-before-break)
3. Updated rig_ui.py `_on_add_slot()` to use `_reconnect_slot()` instead
4. Added `@suppress_structural` decorator to `_reconnect_slot()`

**Example:**
```python
# Before (broken):
rig[0] = "new_plugin"
# Called reconnect(), full rebuild

# After (fixed):
rig[0] = "new_plugin"
# Uses replace() → _reconnect_slot(), partial only
```

### 1. `Slot.replace(uri)` - Replace Plugin
**Location:** `src/modep_rig/rig.py:207`

Atomically replaces plugin in a slot without interrupting audio:

```python
# Old way (interrupts audio):
rig[0].load(new_uri)
rig.reconnect()  # Full disconnect/reconnect cycle

# New way (seamless):
rig[0].replace(new_uri)  # Handles disconnect/reconnect internally
```

**Algorithm:**
1. Keep old plugin in memory
2. Load new plugin via `_load_internal()` (doesn't remove old)
3. Call `_reconnect_slot()` to plug new plugin into chain
4. Remove old plugin from server

**Benefits:**
- Audio continues through new plugin during transition
- Automatic rollback if new plugin fails to connect
- No full rig reconnect needed

---

### 2. `Slot.unload()` - Unload With Neighbor Connection
**Location:** `src/modep_rig/rig.py:156`

Removes plugin while maintaining audio path through neighbors:

```python
# Old way (may interrupt audio):
rig.slots[0]._unload_internal()
rig.slots.remove(rig.slots[0])
rig.reconnect()  # Full reconnect

# New way (seamless):
rig.slots[0].unload()  # Handles everything
```

**Algorithm:**
1. Find previous non-empty slot (or input_slot)
2. Find next non-empty slot (or output_slot)
3. Connect previous → next directly
4. Remove this slot from rig
5. Remove plugin from server

**Benefits:**
- Sound path maintained during unload
- No full chain reconstruction
- Automatic error handling

---

### 3. `Slot._load_internal(uri)` - Load Without Unloading
**Location:** `src/modep_rig/rig.py:252`

Internal helper for `replace()`:
- Loads plugin normally
- **Does NOT call `_unload_internal()`**
- Returns Plugin object
- Used only by `replace()` method

---

### 4. `Rig._reconnect_slot(slot)` - Partial Reconnect
**Location:** `src/modep_rig/rig.py:663`

Reconnects single slot without touching rest of chain:

**Algorithm (Make-Before-Break):**
1. **FIRST:** Connect new slot to chain (src → slot → dst)
   - Audio now flows through new slot
2. **THEN:** Disconnect old path (src → dst removed)
   - Audio continues through new slot

```python
# Old broken way:
disconnect_old()
connect_new()    # Audio interruption between disconnect and connect

# New way (make-before-break):
connect_new()     # Audio established through new path
disconnect_old()  # Old path removed, audio uninterrupted
```

---

### 5. `Rig._disconnect_slot_connections(slot)` - Safe Disconnect
**Location:** `src/modep_rig/rig.py:713`

Safely disconnects all I/O of a single slot:

Finds all possible:
- Input sources (hardware + other plugins)
- Output destinations (other plugins + hardware)
- Disconnects all combinations (ignores errors)

**Used by:** `_reconnect_slot()` during partial reconnection

---

### 6. `Rig.__setitem__()` - Smart Assignment
**Location:** `src/modep_rig/rig.py:576`

Updated to use make-before-break:

```python
# Load into empty slot
rig[0] = "DS1"  # Uses load() + reconnect()

# Replace occupied slot
rig[0] = "Reverb"  # Uses replace() (make-before-break)

# Unload
rig[0] = None  # Uses unload() (make-before-break)
```

## Control Flow Examples

### Entry Points That Use Make-Before-Break

1. **`rig[idx] = plugin_name`** (replace or assign)
   - If slot occupied: uses `Slot.replace()` → `_reconnect_slot()`
   - If slot empty: uses `Slot.load()` → `_reconnect_slot()`
   - Never calls full `reconnect()`

2. **`rig[idx] = None`** (unload)
   - Uses `Slot.unload()` → connects neighbors → partial disconnect/connect
   - Never calls full `reconnect()`

3. **`rig.remove_slot(slot)`** (programmatic removal)
   - Uses `Slot.unload()` (same as above)
   - Never calls full `reconnect()`

### Entry Points That Require Full Rebuild

1. **`rig.clear()`** (clear entire chain)
   - Removes all slots, calls full `reconnect()`
   - Appropriate because entire structure is changing

2. **`rig.set_state(state)`** (load from preset)
   - Rebuilds from saved state, calls full `reconnect()`
   - Appropriate because structure can change significantly

3. **`rig.reconnect()`** (manual reconnect)
   - Full rebuild, should be called rarely
   - Used in initialization and when structure significantly changes

### Example 1: Add Plugin (Slot Index 1)
```
Initial:  Input → DS1[0] → Output

Step 1: Add empty slot
        Input → DS1[0] → [1-empty] → Output

Step 2: Load plugin into slot 1
        Input → DS1[0] → [Reverb[1] not connected yet] → Output

Step 3: CONNECT new slot first (make-before-break)
        DS1[0] → Reverb[1] → Output
        (Audio now flows through Reverb)

Step 4: DISCONNECT old path (DS1 → Output)
        Input → DS1[0] → Reverb[1] → Output
        (Old direct connection removed, sound continues)
        
Result: Seamless insertion, audio never interrupted
```

### Example 2: Replace Plugin (Slot Index 0)
```
Initial:  Input → DS1[0] → Output

Step 1: Load new plugin (Delay) without removing DS1
        Input → DS1[0] + Delay[0] (both in slot) → Output

Step 2: CONNECT new plugin first
        Input → Delay[0] → Output
        (Audio now flows through Delay, not DS1)

Step 3: DISCONNECT old plugin
        Input → Delay[0] → Output
        (DS1 removed from server, sound continues)
        
Result: Seamless swap, no audio interruption
```

## Testing

Run test script:
```bash
uv run python tests/test_make_before_break.py
```

Tests:
1. Load plugin into empty slot
2. Replace plugin (make-before-break)
3. Add multiple plugins
4. Unload middle plugin (make-before-break)
5. Replace via __setitem__

## Safety Features

1. **Automatic Rollback:** If new plugin fails to connect, old plugin is restored
2. **Error Handling:** Gracefully handles API failures (continues cleanup even if steps fail)
3. **Atomic Operations:** Each step completes before next begins
4. **No Full Reconnect:** Only modified connections are touched
5. **Structural Suppression:** Wrapped with `@suppress_structural` decorator where needed

## Performance Benefits

- **Faster:** No full chain rebuild (`reconnect()` is expensive with many plugins)
- **Stable:** Audio path maintained during transitions
- **Responsive:** Immediate feedback to UI

## API Compatibility

- Backward compatible: Old `load()` and `unload()` still work
- New high-level API: `replace()` for safe transitions
- Internal helpers: `_load_internal()`, `_reconnect_slot()`, etc.

## Future Enhancements

- Parallel plugin loading (load new while old running)
- Plugin crossfading (gradual volume transition)
- Transaction log for operation history
- Dry-run mode to test transitions before committing
