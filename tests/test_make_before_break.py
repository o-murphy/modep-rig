#!/usr/bin/env python3
"""
Test script for make-before-break functionality.

This tests the new make-before-break plugin switching mechanism:
1. Loading a plugin
2. Replacing it with another (no full reconnect)
3. Unloading it (connecting neighbors first)
"""

from src.modep_rig.config import Config
from src.modep_rig.rig import Rig

def test_make_before_break():
    """Test make-before-break plugin switching."""
    print("\n" + "="*60)
    print("TESTING: Make-before-break functionality")
    print("="*60 + "\n")
    
    try:
        # Load config
        config = Config("config.toml")
        print(f"✓ Config loaded: {len(config.plugins)} plugins available")
        
        # Create rig
        rig = Rig(config)
        print(f"✓ Rig created, hardware ports detected")
        
        # Test 1: Load first plugin
        print("\n--- Test 1: Load first plugin ---")
        rig[0] = "DS1"
        print(f"✓ Slot 0: {rig[0].plugin}")
        
        # Test 2: Replace with another (make-before-break)
        print("\n--- Test 2: Replace plugin (make-before-break) ---")
        print("  Using replace() method (new sound before old stops)...")
        rig[0].replace("http://moddevices.com/plugins/mod-devel/Reverb")
        print(f"✓ Slot 0: {rig[0].plugin}")
        
        # Test 3: Add more plugins
        print("\n--- Test 3: Add multiple plugins ---")
        rig[1] = "DS1"
        rig[2] = "Reverb"
        print(f"✓ Chain: {rig}")
        
        # Test 4: Unload middle plugin (make-before-break)
        print("\n--- Test 4: Unload middle plugin (make-before-break) ---")
        print("  Using unload() method (connects neighbors first)...")
        rig.slots[1].unload()
        print(f"✓ Slot 1 unloaded, chain: {rig}")
        
        # Test 5: Using __setitem__ with replacement
        print("\n--- Test 5: Replace via __setitem__ ---")
        rig[0] = "Reverb"
        print(f"✓ Slot 0: {rig[0].plugin}")
        
        print("\n" + "="*60)
        print("✓ ALL TESTS PASSED")
        print("="*60 + "\n")
        
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    success = test_make_before_break()
    exit(0 if success else 1)
