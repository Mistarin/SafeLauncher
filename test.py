#!/usr/bin/env python3
"""
Test script to verify MGLauncher components work correctly
"""

import sys
import os
import sqlite3
import tempfile
import zipfile

# 1. Test imports
try:
    from database import GameDatabase
    from core.firejail_runner import FirejailSandboxRunner
    from core.zip_backup import ZipBackupManager
    from core.steamgriddb_client import SteamGridDBClient
    from core.archive_extractor import find_executables, extract_archive_sandboxed
    from core.interfaces import ISandboxRunner, IBackupManager
    print("✓ All imports successful")
except ImportError as e:
    print(f"✗ Import error: {e}")
    sys.exit(1)

# 2. Test database operations & schema auto-migration (including playtime)
try:
    with tempfile.TemporaryDirectory() as tmp_dir:
        old_db_path = os.path.join(tmp_dir, "old_library.db")
        # Create an old schema database missing banner_url, steam_id, and playtime_seconds
        conn = sqlite3.connect(old_db_path)
        conn.execute('''
            CREATE TABLE games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                executable TEXT NOT NULL,
                mode TEXT NOT NULL
            )
        ''')
        conn.close()
        
        # Instantiate GameDatabase on the old file (should auto-migrate schema)
        db = GameDatabase(old_db_path)
        print("✓ Database initialized & auto-migrated schema from old library.db")
        
        # Verify banner_url column + add game
        db.add_game("Test Game", "/tmp/test", "test.exe", "wine", "banner.jpg", "12345")
        games = db.get_all_games()
        assert len(games) == 1, "Game not added correctly"
        assert games[0][5] == "banner.jpg", "banner_url column missing or invalid"
        print("✓ Database add operation works with banner_url column")
        
        # Test playtime column was auto-migrated
        game_id = games[0][0]
        assert db.get_playtime(game_id) == 0, "Initial playtime should be 0"
        print("✓ playtime_seconds column auto-migrated (default 0)")
        
        # Test add_playtime accumulates correctly
        db.add_playtime(game_id, 3600)   # 1 hour
        db.add_playtime(game_id, 900)    # +15 min
        total = db.get_playtime(game_id)
        assert total == 4500, f"Expected 4500s playtime, got {total}s"
        print("✓ add_playtime / get_playtime correctly accumulates seconds")
        
        db.remove_game(game_id)
        games = db.get_all_games()
        assert len(games) == 0, "Game not removed correctly"
        print("✓ Database remove operation works")
        db.close()
except Exception as e:
    print(f"✗ Database error: {e}")
    sys.exit(1)

# 3. Test Firejail Sandbox Runner
try:
    runner = FirejailSandboxRunner()
    print("✓ FirejailSandboxRunner initialized")
    # Verify validation on missing path
    try:
        runner.launch("/nonexistent_path_12345", "test.exe", "wine")
        assert False, "Should have raised ValueError for non-existent path"
    except ValueError:
        print("✓ Runner path validation works")
except Exception as e:
    print(f"✗ Runner error: {e}")
    sys.exit(1)

# 4. Test Zip Backup Manager, Executable Scanner, and Zip Slip Prevention
try:
    backup = ZipBackupManager()
    print("✓ ZipBackupManager initialized")
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        save_dir = os.path.join(tmp_dir, "save")
        os.makedirs(save_dir, exist_ok=True)
        with open(os.path.join(save_dir, "game.exe"), "w") as f:
            f.write("exe data")
        with open(os.path.join(save_dir, "save.dat"), "w") as f:
            f.write("save data")
            
        exes = find_executables(save_dir)
        assert "game.exe" in exes, "Executable scanner failed to detect game.exe"
        print("✓ Executable scanner works")
        
        # Test .sandbox-config interoperability
        from core.archive_extractor import save_sandbox_config, load_sandbox_config, scan_sandbox_games
        save_sandbox_config(save_dir, "game.exe")
        assert load_sandbox_config(save_dir) == "game.exe", ".sandbox-config read/write failed"
        print("✓ .sandbox-config interoperability verified")
        
        scanned = scan_sandbox_games(tmp_dir)
        assert len(scanned) == 1, "scan_sandbox_games failed to discover game"
        print("✓ Sandbox game auto-discovery verified")
            
        zip_path = os.path.join(tmp_dir, "backup.zip")
        assert backup.export_save(save_dir, zip_path), "Export failed"
        assert os.path.exists(zip_path), "Zip file was not created"
        print("✓ Zip export works")
        
        dest_dir = os.path.join(tmp_dir, "restored")
        assert backup.import_save(zip_path, dest_dir), "Import failed"
        assert os.path.exists(os.path.join(dest_dir, "save.dat")), "Restored save file missing"
        print("✓ Zip import works")
        
        # Test Zip Slip attack rejection
        malicious_zip = os.path.join(tmp_dir, "malicious.zip")
        with zipfile.ZipFile(malicious_zip, 'w') as zf:
            zf.writestr("../../evil.txt", "hacked")
        
        import_res = backup.import_save(malicious_zip, dest_dir)
        assert import_res is False, "Backup manager failed to block Zip Slip attack!"
        print("✓ Zip Slip security protection verified")
except Exception as e:
    print(f"✗ Backup manager error: {e}")
    sys.exit(1)

# 5. Test SteamGridDB Client (Steam Store API)
try:
    client = SteamGridDBClient()
    print("✓ SteamGridDBClient initialized")
    
    result = client.search_game("Portal 2")
    assert result.get("found") is True, "Game search failed for Portal 2"
    assert len(result.get("results", [])) > 0, "No results returned"
    
    banner_url = result["primary"]["banner_url"]
    banner_path = client.download_banner(banner_url)
    assert banner_path and os.path.exists(banner_path), "Banner download failed"
    print(f"✓ Banner search & download works! (Downloaded: {os.path.basename(banner_path)})")
except Exception as e:
    print(f"✗ SteamGridDB Client error: {e}")
    sys.exit(1)

# 6. Test PyQt UI Instantiation (Headless Offscreen)
try:
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PyQt6.QtWidgets import QApplication
    from ui.main_window import MainWindow, AddGameDialog

    app = QApplication.instance() or QApplication([])
    db_mem = GameDatabase(":memory:")
    mw = MainWindow(db_mem, runner, backup)
    dlg = AddGameDialog(mw, mw.sgdb_client)
    print("✓ UI MainWindow and AddGameDialog instantiated cleanly offscreen")
except Exception as e:
    print(f"✗ UI Instantiation error: {e}")
    sys.exit(1)

print("\n✅ All MGLauncher components tested and working cleanly!")
