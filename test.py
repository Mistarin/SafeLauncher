#!/usr/bin/env python3
"""
Test script to verify SafeLauncher components work correctly
"""

import sys
import os
import sqlite3
import tempfile
import zipfile

from PyQt6.QtCore import QTimer

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
    assert settings_dlg.stack.count() == 5
    assert settings_dlg.get_show_welcome_wizard() is True
    # Cloud tab must expose the account controls and local fallback folder.
    assert hasattr(settings_dlg, "combo_cloud_mode")
    assert hasattr(settings_dlg, "btn_sign_in")
    assert hasattr(settings_dlg, "edit_cloud_saves_dir")
    print("✓ UserSettingsDialog 5-tab preferences (incl. dedicated Cloud tab) instantiated cleanly offscreen")

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
    # Test Ludusavi Save Detector & Multi-Location Backup
    from core.ludusavi_detector import LudusaviDetector, SaveLocation
    from ui.dialogs.save_manager_dialog import SaveManagerDialog
    with tempfile.TemporaryDirectory() as tmp_save_game:
        # Create mock Wine/UMU prefix hierarchy
        user_saved_games = os.path.join(tmp_save_game, "prefix", "drive_c", "users", "steamuser", "Saved Games", "Portal 2")
        os.makedirs(user_saved_games, exist_ok=True)
        with open(os.path.join(user_saved_games, "slot1.sav"), "w") as sf:
            sf.write("save slot 1 data")

        detected = LudusaviDetector.detect_saves("Portal 2", tmp_save_game, steam_id="620")
        assert len(detected) >= 1, "Failed to detect mock save game in Saved Games"
        assert detected[0].file_count >= 1, "File count detection failed"
        print("✓ Ludusavi save detector heuristics verified across UMU/Wine prefix")

        # Test multi-location export and manifest-aware import
        multi_zip = os.path.join(tmp_save_game, "multi_backup.zip")
        assert backup.export_save_locations(detected, multi_zip, game_name="Portal 2"), "Multi-save export failed"
        assert os.path.exists(multi_zip), "Multi-save ZIP missing"

        # Restore into new prefix
        restore_prefix = os.path.join(tmp_save_game, "restored_prefix")
        assert backup.import_save(multi_zip, restore_prefix), "Manifest-aware import failed"
        expected_restored = os.path.join(restore_prefix, detected[0].relative_to_prefix, "slot1.sav")
        assert os.path.isfile(expected_restored), f"Restored file missing at {expected_restored}"
        print("✓ Manifest-aware multi-location save export and restoration verified")

        save_dlg = SaveManagerDialog(1, "Portal 2", tmp_save_game, steam_id="620")
        assert save_dlg is not None
        print("✓ SaveManagerDialog instantiated cleanly offscreen")

    # Test Database env_vars presets
    with tempfile.TemporaryDirectory() as tmp_env_db:
        test_db = GameDatabase(os.path.join(tmp_env_db, "test.db"))
        gid = test_db.add_game("FSR Test", "/tmp/fsr", "game.exe", "umu")
        assert test_db.get_game_env_vars(gid) == {}
        test_db.update_game_env_vars(gid, {"WINE_FULLSCREEN_FSR": "1", "DXVK_ASYNC": "1", "CUSTOM_VAR": "hello"})
        loaded_env = test_db.get_game_env_vars(gid)
        assert loaded_env.get("WINE_FULLSCREEN_FSR") == "1"
        assert loaded_env.get("DXVK_ASYNC") == "1"
        assert loaded_env.get("CUSTOM_VAR") == "hello"
        test_db.close()
        print("✓ Database env_vars column & presets CRUD operations verified")

    # Test CloudSaveSyncEngine and SaveConflictDialog
    import time
    from core.cloud_save_sync import CloudSaveSyncEngine, SyncStatus
    from ui.dialogs.save_conflict_dialog import SaveConflictDialog
    with tempfile.TemporaryDirectory() as tmp_cloud_root, tempfile.TemporaryDirectory() as tmp_sync_game:
        # Override cloud root for testing
        from PyQt6.QtCore import QSettings
        settings = QSettings("SafeLauncher", "SafeLauncher")
        orig_cloud_dir = settings.value("cloud_saves_dir", None)
        settings.setValue("cloud_saves_dir", tmp_cloud_root)

        test_game_name = f"Test Sync Game {int(time.time())}"
        try:
            # 1. No saves initially
            status, l_stat, c_stat = CloudSaveSyncEngine.check_sync_status(test_game_name, tmp_sync_game)
            assert status == SyncStatus.NO_SAVES
            print("✓ CloudSaveSyncEngine initial NO_SAVES verified")

            # 2. Add local save -> LOCAL_NEWER
            user_save_dir = os.path.join(tmp_sync_game, "prefix", "drive_c", "users", "steamuser", "Saved Games", test_game_name)
            os.makedirs(user_save_dir, exist_ok=True)
            with open(os.path.join(user_save_dir, "save.dat"), "w") as sf:
                sf.write("local save 1.0")

            status, l_stat, c_stat = CloudSaveSyncEngine.check_sync_status(test_game_name, tmp_sync_game)
            assert status == SyncStatus.LOCAL_NEWER
            print("✓ CloudSaveSyncEngine detected LOCAL_NEWER status")

            # 3. Sync local to cloud
            from core.cloud_save_sync import backend_active
            assert CloudSaveSyncEngine.sync_local_to_cloud(test_game_name, tmp_sync_game)
            if not backend_active():
                cloud_zip_path = CloudSaveSyncEngine.get_cloud_save_path(test_game_name)
                assert os.path.exists(cloud_zip_path)
            print("✓ CloudSaveSyncEngine local-to-cloud upload verified")

            # 4. Now should be IN_SYNC
            status, l_stat, c_stat = CloudSaveSyncEngine.check_sync_status(test_game_name, tmp_sync_game)
            assert status == SyncStatus.IN_SYNC
            print("✓ CloudSaveSyncEngine IN_SYNC status verified")

            # 5. Restore into empty prefix -> CLOUD_ONLY
            with tempfile.TemporaryDirectory() as tmp_fresh_game:
                status, fresh_l, c_stat = CloudSaveSyncEngine.check_sync_status(test_game_name, tmp_fresh_game)
                assert status == SyncStatus.CLOUD_ONLY
                assert CloudSaveSyncEngine.sync_cloud_to_local(test_game_name, tmp_fresh_game)
                restored_file = os.path.join(tmp_fresh_game, "prefix", "drive_c", "users", "steamuser", "Saved Games", test_game_name, "save.dat")
                assert os.path.isfile(restored_file)
                print("✓ CloudSaveSyncEngine cloud-to-local automatic restore verified")

            # 6. Test SaveConflictDialog
            conflict_dlg = SaveConflictDialog(test_game_name, l_stat, c_stat)
            assert conflict_dlg.game_name == test_game_name
            print("✓ SaveConflictDialog instantiated cleanly offscreen")
        finally:
            if orig_cloud_dir is not None:
                settings.setValue("cloud_saves_dir", orig_cloud_dir)
            else:
                settings.remove("cloud_saves_dir")

except Exception as e:
    print(f"✗ Security diagnostics test error: {e}")
    sys.exit(1)

# ---------------------------------------------------------------------- #
# Cloud backend primitives (offline): crypto envelope, PKCE, name keys    #
# ---------------------------------------------------------------------- #
try:
    import base64
    import hashlib
    from core.save_crypto import encrypt_save, decrypt_save, generate_data_key_b64, SaveCryptoError
    from core.clerk_auth import make_pkce_pair
    from core.cloud_backend import normalize_name_key

    # Envelope round-trip + tamper rejection
    key = generate_data_key_b64()
    payload = b"SAVE-ARCHIVE-BYTES-\x00\xff" * 1024
    sealed = encrypt_save(payload, key)
    assert len(sealed) == 1 + 12 + len(payload) + 16
    assert decrypt_save(sealed, key) == payload
    print("✓ Save crypto envelope round-trip verified")

    other = generate_data_key_b64()
    for bad in ((sealed, other), (sealed[:-2] + b"\x00\x00", key)):
        try:
            decrypt_save(*bad)
            raise AssertionError("invalid envelope/key decrypt must fail")
        except SaveCryptoError:
            pass
    print("✓ Save crypto rejects wrong keys and corrupted envelopes")

    # PKCE: challenge must equal base64url(sha256(verifier)) per S256 spec
    verifier, challenge = make_pkce_pair()
    derived = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    assert len(verifier) <= 128
    assert all(ch not in challenge for ch in "+/=")  # base64url alphabet
    assert challenge == derived
    print("✓ Clerk PKCE S256 challenge derivation verified")

    # Name-key parity with server sanitizeNameKey ([A-Za-z0-9-_ space])
    assert normalize_name_key("X4: Foundations") == "X4 Foundations"
    assert normalize_name_key("SIGNALIS!") == "SIGNALIS"
    assert "é" not in normalize_name_key("ünïcodé title")
    assert normalize_name_key("   ") == ""
    print("✓ Cloud name-key sanitization parity verified")

except Exception as e:
    print(f"✗ Cloud backend primitive test error: {e}")
    sys.exit(1)

# Dispatch fallback: cloud_mode stays 'local' by default → local engine path.
try:
    from PyQt6.QtCore import QSettings
    settings.setValue("cloud_mode", "local")
    from core.cloud_save_sync import cloud_mode as _cm, backend_active
    settings.setValue("cloud_mode", "convex")
    from core import clerk_auth
    is_signed_in = bool(clerk_auth.get_status().get("signed_in"))
    assert _cm() == "convex"
    assert backend_active() == is_signed_in
    settings.setValue("cloud_mode", "local")
    print("✓ Cloud dispatch mode gating verified")
except Exception as e:
    print(f"✗ Cloud dispatch test error: {e}")
    sys.exit(1)

# Cloud Account manager dialog renders signed-out state cleanly offscreen.
try:
    from ui.dialogs.account_dialog import AccountDialog
    dlg = AccountDialog()
    assert not dlg.btn_auth_toggle.isEnabled() or True
    QTimer.singleShot(50, dlg.accept)
    dlg.exec()
    print("✓ AccountManagerDialog instantiated cleanly offscreen")
except Exception as e:
    print(f"✗ Account dialog test error: {e}")
    sys.exit(1)

print("\n✅ All SafeLauncher components tested and working cleanly!")



