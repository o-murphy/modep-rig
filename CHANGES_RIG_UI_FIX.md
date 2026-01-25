# Changes Made to Fix qrack Integration

## Problem
New make-before-break logic wasn't working with qrack.py - full board rebuild was still happening and old routes weren't being disconnected.

## Root Causes
1. **qrack.py `_on_add_slot()`** - Called `self.rack.reconnect()` after adding slot
2. **`_reconnect_slot()` missing decorator** - Wasn't suppressing structural callbacks
3. **Wrong order of operations** - Was disconnecting old before connecting new
4. **Missing `_disconnect_pair()` method** - Couldn't disconnect specific route pairs

## Changes Made

### 1. rack.py - `_reconnect_slot()` Algorithm (Line 663)
**Make-Before-Break Implementation:**
```python
# Old broken order:
disconnect_old_path()
disconnect_slot_connections()
connect_new_slot()  # Audio interrupts here!

# New correct order:
connect_new_slot_incoming()  # Audio starts through new
connect_new_slot_outgoing()  # Audio established
disconnect_old_path()        # Then remove old (no interruption!)
```

**Added:**
- `@suppress_structural` decorator (line 663)
- Step 1: Connect new slot to chain (src → slot → dst)
- Step 2: Disconnect old path (src → dst)

### 2. rack.py - New Method `_disconnect_pair()` (Line 750)
Disconnects specific source → destination connections:
```python
def _disconnect_pair(self, src: Slot, dst: Slot):
    """Роз'єднує конкретний зв'язок src -> dst"""
    # Get ports from src and dst
    # Disconnect all port combinations
```

### 3. qrack.py - Line 595-597
**Before:**
```python
slot.load(dialog.selected_uri)
self.rack.reconnect()
```

**After:**
```python
slot.load(dialog.selected_uri)
self.rack._reconnect_slot(slot)
```

### 4. rack.py - `remove_slot()` 
Uses `unload()` which implements make-before-break.

## Result
- **Adding slot:** Connects new, THEN disconnects old (no interrupt)
- **Removing slot:** Connects neighbors, THEN removes (no interrupt)  
- **Replacing slot:** Loads new, connects, THEN removes old (no interrupt)
- **No full board rebuilds** for single slot operations
- **Old routes properly cleaned** when new ones established
