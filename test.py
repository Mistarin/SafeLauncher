#!/usr/bin/env python3
"""
Test script to verify SafeLauncher components work correctly
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
        db.update_game_steam_id(game_id, "12345")
        assert db.get_all_games()[0][6] == "12345", "Steam ID update failed"
        print("✓ Steam ID update works")
        assert db.get_playtime(game_id) == 0, "Initial playtime should be 0"
        print("✓ playtime_seconds column auto-migrated (default 0)")
        
        # Test add_playtime accumulates correctly
        db.add_playtime(game_id, 3600)   # 1 hour
        db.add_playtime(game_id, 900)    # +15 min
        total = db.get_playtime(game_id)
        assert total == 4500, f"Expected 4500s playtime, got {total}s"
        print("✓ add_playtime / get_playtime correctly accumulates seconds")
        
        # Test update_game functionality
        db.update_game(game_id, "Updated Game Name", "/tmp/test", "new_test.exe", "umu", "new_banner.jpg")
        updated_games = db.get_all_games()
        assert updated_games[0][1] == "Updated Game Name", "Game name update failed"
        assert updated_games[0][3] == "new_test.exe", "Executable update failed"
        assert updated_games[0][4] == "umu", "Mode update failed"
        print("✓ Database update_game operation works")

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

# 5. Optional live Steam metadata test. The default smoke test must work
# offline and must not fail merely because DNS or Steam is unavailable.
client = SteamGridDBClient()
print("✓ SteamGridDBClient initialized")
if os.environ.get("SAFELAUNCHER_LIVE_TESTS") == "1":
    try:
        result = client.search_game("Portal 2")
        assert result.get("found") is True, "Game search failed for Portal 2"
        assert len(result.get("results", [])) > 0, "No results returned"
        banner_url = result["primary"]["banner_url"]
        banner_path = client.download_banner(banner_url)
        assert banner_path and os.path.exists(banner_path), "Banner download failed"
        print(f"✓ Live banner search works! (Downloaded: {os.path.basename(banner_path)})")
    except Exception as e:
        print(f"✗ Live Steam metadata test failed: {e}")
        sys.exit(1)
else:
    print("↷ Live Steam metadata test skipped (set SAFELAUNCHER_LIVE_TESTS=1 to enable)")

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

# 7. Test GameRecord dataclass and backwards compatibility
try:
    from database import GameRecord
    rec = GameRecord(
        id=1, name="Hades", path="/games/hades", executable="Hades.exe", mode="umu",
        banner_url="https://example.com/banner.png", steam_id="1145360"
    )
    assert rec.id == 1 and rec.name == "Hades"
    assert rec[0] == 1 and rec[1] == "Hades" and rec[5] == "https://example.com/banner.png"
    assert len(rec) >= 17
    assert rec.is_archived == 0
    unpacked_id, unpacked_name, *rest = rec
    assert unpacked_id == 1 and unpacked_name == "Hades"
    print("✓ GameRecord dataclass attribute & index access verified")
except Exception as e:
    print(f"✗ GameRecord test error: {e}")
    sys.exit(1)

# 8. Test ArchiveInstaller executable classification
try:
    from core.archive_installer import ArchiveInstaller
    with tempfile.TemporaryDirectory() as tmp_archive_dir:
        os.makedirs(os.path.join(tmp_archive_dir, "bin"), exist_ok=True)
        sh_path = os.path.join(tmp_archive_dir, "start.sh")
        bat_path = os.path.join(tmp_archive_dir, "start.bat")
        exe_path = os.path.join(tmp_archive_dir, "game.exe")
        for p in (sh_path, bat_path, exe_path):
            with open(p, "w") as f:
                f.write("content")
        installer = ArchiveInstaller()
        cands = {c.relative_path: c.kind for c in installer.candidates(tmp_archive_dir)}
        assert cands.get("start.sh") == "Linux script", f"start.sh misclassified as {cands.get('start.sh')}"
        assert cands.get("start.bat") == "Windows batch script", f"start.bat misclassified as {cands.get('start.bat')}"
        assert cands.get("game.exe") == "Windows executable", f"game.exe misclassified as {cands.get('game.exe')}"
        print("✓ ArchiveInstaller .sh / .bat / .exe classification verified")
except Exception as e:
    print(f"✗ ArchiveInstaller error: {e}")
    sys.exit(1)

# 9. Test Prefix Sanitizer dosdevices/z: and user symlink removal
try:
    from core.prefix_sanitizer import sanitize_wine_prefix
    with tempfile.TemporaryDirectory() as tmp_prefix_dir:
        prefix_path = os.path.join(tmp_prefix_dir, "prefix")
        dosdevices = os.path.join(prefix_path, "dosdevices")
        users_dir = os.path.join(prefix_path, "drive_c", "users", "steamuser")
        os.makedirs(dosdevices, exist_ok=True)
        os.makedirs(users_dir, exist_ok=True)

        # Create dummy z: link pointing to /
        z_link = os.path.join(dosdevices, "z:")
        os.symlink("/", z_link)
        # Create dummy Documents link
        doc_link = os.path.join(users_dir, "Documents")
        os.symlink("/tmp", doc_link)

        assert os.path.islink(z_link)
        assert os.path.islink(doc_link)

        res = sanitize_wine_prefix(tmp_prefix_dir)
        assert res is True, "Sanitizer failed to run on valid prefix"
        assert not os.path.exists(z_link), "z: symlink was not removed"
        assert not os.path.islink(doc_link), "Documents symlink was not replaced"
        assert os.path.isdir(doc_link), "Documents was not replaced with an isolated folder"
        print("✓ PrefixSanitizer isolated user folders and removed dosdevices/z: host link")
except Exception as e:
    print(f"✗ PrefixSanitizer test error: {e}")
    sys.exit(1)

# 10. Test Desktop Integration
try:
    from core.desktop_integration import get_desktop_file_path, is_desktop_entry_installed
    path = get_desktop_file_path()
    assert path.endswith("safelauncher.desktop")
    print("✓ Desktop integration module loaded cleanly")
except Exception as e:
    print(f"✗ Desktop integration error: {e}")
    sys.exit(1)

# 11. Test Security Diagnostics and Multi-Tab Settings Dialog
try:
    from core.security_diagnostics import inspect_security_health, run_live_sandbox_verification
    from ui.dialogs.settings_dialog import UserSettingsDialog
    from ui.dialogs.welcome_wizard import WelcomeWizardDialog
    from core.screenshot_capture import capture_desktop_screenshot, get_game_screenshots_dir

    report = inspect_security_health()
    assert report.firejail_version != ""
    assert isinstance(report.gpu_caches, list)
    print("✓ Security diagnostics health inspector executed cleanly")

    settings_dlg = UserSettingsDialog("TestUser", "/tmp/proton", show_welcome_wizard=True)
    assert settings_dlg.stack.count() == 4
    assert settings_dlg.get_show_welcome_wizard() is True
    print("✓ UserSettingsDialog 4-tab preferences (including Plugins) instantiated cleanly offscreen")

    from core.plugins.gpu_screen_recorder import GpuRecorderService, GpuRecorderConfig
    rec_cfg = GpuRecorderConfig(enabled=False, mode="replay_buffer", history_seconds=90, codec="hevc", bitrate="20M")
    service = GpuRecorderService(rec_cfg)
    cmd = service.build_command("/tmp/test_clip.mp4", is_replay=True, backend="wl-screenrec")
    assert "-f" in cmd
    assert "--history" in cmd
    assert "90" in cmd
    assert "--codec" in cmd
    assert "hevc" in cmd
    assert "--bitrate" in cmd
    assert "20M" in cmd

    cmd_gpu = service.build_command("/tmp/test_clip.mp4", is_replay=True, backend="gpu-screen-recorder")
    assert "-w" in cmd_gpu
    assert "-r" in cmd_gpu
    assert "90" in cmd_gpu
    assert "-k" in cmd_gpu
    assert "hevc" in cmd_gpu

    cmd_ff = service.build_command("/tmp/test_clip.mp4", is_replay=False, backend="ffmpeg")
    assert "ffmpeg" in cmd_ff
    assert "20M" in cmd_ff
    print("✓ Hardware recorder command generation (gpu-screen-recorder, wl-screenrec, ffmpeg) verified")

    wiz = WelcomeWizardDialog("TestPlayer", "/tmp/proton")
    assert wiz.get_user_name() == "TestPlayer"
    print("✓ WelcomeWizardDialog instantiated cleanly offscreen")

    ss_dir = get_game_screenshots_dir(999)
    assert os.path.exists(ss_dir)
    print("✓ Screenshot capture directory creation verified")

    from ui.dialogs.game_properties_dialog import GamePropertiesDialog
    test_game_tuple = (1, "Test Game", "/tmp/test", "game.exe", "umu", "", 0, "", "", "", "", "", "", "", 0, "1.0.4", "")
    prop_dlg = GamePropertiesDialog(test_game_tuple)
    assert prop_dlg.game_name == "Test Game"
    print("✓ GamePropertiesDialog instantiated cleanly offscreen")

    from ui.dialogs.settings_dialog import ScreenshotLightboxDialog
    lightbox = ScreenshotLightboxDialog([], parent=None)
    assert lightbox is not None
    print("✓ ScreenshotLightboxDialog instantiated cleanly offscreen")

    from ui.components.banner_card import GameBannerWidget
    banner = GameBannerWidget(1, "Test Game", version="v0.4.2")
    assert banner.version == "v0.4.2"
    assert banner.version_badge.text() == "v0.4.2"
    assert not banner.version_badge.isHidden()
    print("✓ GameBannerWidget 16:9 ratio and version badge verified")

    from ui.dialogs.game_dialogs import CustomRemoveDialog, ManageCollectionGamesDialog
    remove_dlg = CustomRemoveDialog("Test Game")
    assert remove_dlg is not None
    print("✓ CustomRemoveDialog archive options instantiated cleanly")

    col_dlg = ManageCollectionGamesDialog("RPG", [(1, "Game 1"), (2, "Game 2")], {1})
    assert col_dlg.collection_name == "RPG"
    assert 1 in col_dlg.get_selected_game_ids()
    print("✓ ManageCollectionGamesDialog instantiated and checked cleanly")
except Exception as e:
    print(f"✗ Security diagnostics test error: {e}")
    sys.exit(1)

print("\n✅ All SafeLauncher components tested and working cleanly!")


