#!/usr/bin/env /bin/python
"""
Test script to verify MGLauncher components work correctly
"""

import sys
import os

# Test imports
try:
    from database import GameDatabase
    from core.firejail_runner import FirejailSandboxRunner
    from core.zip_backup import ZipBackupManager
    from core.interfaces import ISandboxRunner, IBackupManager
    print("✓ All imports successful")
except ImportError as e:
    print(f"✗ Import error: {e}")
    sys.exit(1)

# Test database
try:
    db = GameDatabase(":memory:")  # Use in-memory database for testing
    print("✓ Database initialized")
    
    # Add a test game
    db.add_game("Test Game", "/tmp/test", "test.exe", "wine")
    games = db.get_all_games()
    assert len(games) == 1, "Game not added correctly"
    print(f"✓ Database operations work (added 1 test game)")
    
    # Test remove
    game_id = games[0][0]
    db.remove_game(game_id)
    games = db.get_all_games()
    assert len(games) == 0, "Game not removed correctly"
    print(f"✓ Remove game works")
    
except Exception as e:
    print(f"✗ Database error: {e}")
    sys.exit(1)

# Test runner
try:
    runner = FirejailSandboxRunner()
    print("✓ FirejailSandboxRunner initialized")
except Exception as e:
    print(f"✗ Runner error: {e}")
    sys.exit(1)

# Test backup manager
try:
    backup = ZipBackupManager()
    print("✓ ZipBackupManager initialized")
except Exception as e:
    print(f"✗ Backup manager error: {e}")
    sys.exit(1)

print("\n✅ All components working correctly!")
print("\nTo launch the GUI, run:")
print("  python main.py")
print("\nOr use the launcher script:")
print("  bash launcher.sh")
