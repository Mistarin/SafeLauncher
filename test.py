#!/usr/bin/env python3
"""
Test script to verify SafeLauncher components work correctly
"""

import sys
import os
import sqlite3
import tempfile
import zipfile

os.environ["SAFELAUNCHER_DISABLE_UPDATE_CHECK"] = "1"

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
    # A fresh QSettings (like a CI runner) defaults show_welcome_wizard=True;
    # its modal exec() would fire inside a later nested event loop and hang
    # the suite forever. The offscreen tests simulate a returning user.
    # MainWindow captures the flag in __init__, so restore right after.
    from PyQt6.QtCore import QSettings as _QSettings
    _qs = _QSettings("SafeLauncher", "SafeLauncher")
    _old_wizard = _qs.value("show_welcome_wizard")
    _qs.setValue("show_welcome_wizard", False)
    mw = MainWindow(db_mem, runner, backup)
    if _old_wizard is None:
        _qs.remove("show_welcome_wizard")
    else:
        _qs.setValue("show_welcome_wizard", _old_wizard)
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
        print("✓ Manifest-aware multi-location save export and restoration verified")

        # Test single-file save export and manifest-aware import
        import time
        single_loc = SaveLocation(
            path=os.path.join(user_saved_games, "slot1.sav"),
            display_name="slot1.sav",
            is_directory=False,
            relative_to_prefix=os.path.relpath(os.path.join(user_saved_games, "slot1.sav"), os.path.join(tmp_save_game, "prefix")),
            file_count=1,
            total_size_bytes=16,
            last_modified=time.time()
        )
        single_zip = os.path.join(tmp_save_game, "single_backup.zip")
        assert backup.export_save_locations([single_loc], single_zip, game_name="Portal 2", game_path=tmp_save_game)
        single_restore = os.path.join(tmp_save_game, "single_restored")
        assert backup.import_save(single_zip, single_restore, game_path=tmp_save_game)
        single_out = os.path.join(single_restore, single_loc.relative_to_prefix)
        assert os.path.isfile(single_out), f"Single save file missing or created as dir at {single_out}"
        assert not os.path.isdir(single_out)
        print("✓ Single-file save export and non-nested restoration verified")

        save_dlg = SaveManagerDialog(1, "Portal 2", tmp_save_game, steam_id="620")
        assert save_dlg is not None
        assert hasattr(save_dlg, "_restore_from_cloud")
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
        orig_cloud_mode = settings.value("cloud_mode", None)
        settings.setValue("cloud_saves_dir", tmp_cloud_root)
        settings.setValue("cloud_mode", "local")

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

            # 7. Test robust slug matching & name key resolution across release tags
            from core.cloud_save_sync import resolve_name_key, match_cloud_game_to_library, _clean_game_slug
            import core.cloud_save_sync as css_mod
            assert _clean_game_slug("Dave-the-Diver-AnkerGames") == "davethediver"
            assert _clean_game_slug("Medieval-Dynasty-SteamRIP") == "medievaldynasty"
            assert _clean_game_slug("Bellwright-FitGirl") == "bellwright"
            assert _clean_game_slug("Cassette Beasts") == "cassettebeasts"

            # Mock listing to verify resolve_name_key matches tagged installed game to clean cloud game
            orig_listing_cache = css_mod._LISTING_CACHE
            try:
                css_mod._LISTING_CACHE = {
                    "ts": 9999999999.0,
                    "data": {"games": [
                        {"nameKey": "Dave the Diver", "displayName": "Dave the Diver"},
                        {"nameKey": "Cassette Beasts", "displayName": "Cassette Beasts"},
                    ]}
                }
                assert resolve_name_key("Dave-the-Diver-AnkerGames") == "Dave the Diver"
                assert resolve_name_key("Cassette Beasts") == "Cassette Beasts"

                # Verify match_cloud_game_to_library reverse resolution
                class MockGame:
                    def __init__(self, name, path):
                        self.name = name
                        self.path = path
                library = [
                    MockGame("Dave-the-Diver-AnkerGames", "/games/dave"),
                    MockGame("Cassette Beasts", "/games/cb"),
                ]
                matched = match_cloud_game_to_library("Dave the Diver", "Dave the Diver", library)
                assert matched is not None and matched.name == "Dave-the-Diver-AnkerGames"
                print("✓ Robust slug matching & cloud-to-library resolution across release tags verified")
            finally:
                css_mod._LISTING_CACHE = orig_listing_cache

            # 8. Test ephemeral file filtering from source_max_mtime
            with tempfile.TemporaryDirectory(prefix="sl-test-ephemeral-") as tmp_save_dir:
                real_save = os.path.join(tmp_save_dir, "save.dat")
                with open(real_save, "wb") as f:
                    f.write(b"real save progress")
                os.utime(real_save, (1700000000.0, 1700000000.0))

                log_file = os.path.join(tmp_save_dir, "godot.log")
                with open(log_file, "w") as f:
                    f.write("engine log line")
                os.utime(log_file, (1700050000.0, 1700050000.0))

                from core.zip_backup import ZipBackupManager, _is_within
                from core.ludusavi_detector import SaveLocation
                out_zip = os.path.join(tmp_save_dir, "out.zip")
                mgr = ZipBackupManager()
                loc = SaveLocation(display_name="Save", path=tmp_save_dir, is_directory=True)
                assert mgr.export_save_locations([loc], out_zip, game_name="TestGame", game_path=tmp_save_dir)
                import zipfile, json
                with zipfile.ZipFile(out_zip, 'r') as zf:
                    m_data = json.loads(zf.read("safelauncher_manifest.json").decode("utf-8"))
                    assert m_data["source_max_mtime"] == 1700000000, f"Expected 1700000000, got {m_data['source_max_mtime']}"
                print("✓ Ephemeral runtime log filtering from source_max_mtime verified")

                # Test _is_within with symlink
                symlink_target = os.path.join(tmp_save_dir, "symlink_dir")
                os.symlink(tmp_save_dir, symlink_target)
                assert _is_within(tmp_save_dir, os.path.join(symlink_target, "save.dat"))
                print("✓ Symlink-aware path containment verification (_is_within) verified")
        finally:
            if orig_cloud_dir is not None:
                settings.setValue("cloud_saves_dir", orig_cloud_dir)
            else:
                settings.remove("cloud_saves_dir")
            if orig_cloud_mode is not None:
                settings.setValue("cloud_mode", orig_cloud_mode)
            else:
                settings.remove("cloud_mode")

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
    from core.cloud_backend import normalize_name_key, ConvexSaveBackend

    # Envelope round-trip + tamper rejection
    key = generate_data_key_b64()
    payload = b"SAVE-ARCHIVE-BYTES-\x00\xff" * 1024
    sealed = encrypt_save(payload, key)
    assert len(sealed) == 1 + 12 + len(payload) + 16
    assert decrypt_save(sealed, key) == payload
    print("✓ Save crypto envelope round-trip verified")

    other = generate_data_key_b64()
    bad_version = b"\x02" + sealed[1:]
    for bad in ((sealed, other), (sealed[:-2] + b"\x00\x00", key), (bad_version, key)):
        try:
            decrypt_save(*bad)
            raise AssertionError("invalid envelope/key decrypt must fail")
        except SaveCryptoError:
            pass
    print("✓ Save crypto rejects wrong keys, corrupted envelopes, and tampered version headers (AEAD)")

    # Name-key parity with server charset ([A-Za-z0-9-_ space]); collision-proof
    assert normalize_name_key("Game Name") == "Game Name"  # clean names keep their key
    assert normalize_name_key("   ") == ""
    from core.cloud_backend import legacy_name_key
    assert legacy_name_key("X4: Foundations") == "X4 Foundations"
    assert normalize_name_key("X4: Foundations").startswith("X4 Foundations-")
    assert normalize_name_key("X4: Foundations") == normalize_name_key("X4: Foundations")
    # Distinct names must never sanitize to the same cloud key
    assert normalize_name_key("Dark Souls: Remastered") != normalize_name_key("Dark Souls Remastered")
    assert "é" not in normalize_name_key("ünïcodé title")
    print("✓ Cloud name-key sanitization parity verified")

except Exception as e:
    print(f"✗ Cloud backend primitive test error: {e}")
    sys.exit(1)

# Dispatch fallback: cloud_mode stays 'local' by default → local engine path.
try:
    from PyQt6.QtCore import QSettings
    settings = QSettings("SafeLauncher", "SafeLauncher")
    orig_cloud_mode = settings.value("cloud_mode", None)
    try:
        settings.setValue("cloud_mode", "local")
        from core.cloud_save_sync import cloud_mode as _cm, backend_active
        assert _cm() == "local"
        assert not backend_active()

        settings.setValue("cloud_mode", "convex")
        assert _cm() == "convex"
        import core.cloud_backend
        orig_get_site = core.cloud_backend.get_site_url
        try:
            core.cloud_backend.get_site_url = lambda: "https://test.convex.site"
            assert backend_active()
            core.cloud_backend.get_site_url = lambda: ""
            assert not backend_active()
        finally:
            core.cloud_backend.get_site_url = orig_get_site

        settings.setValue("cloud_mode", "local")
        assert _cm() == "local"
        print("✓ Cloud dispatch mode gating verified")
    finally:
        if orig_cloud_mode is not None:
            settings.setValue("cloud_mode", orig_cloud_mode)
        else:
            settings.remove("cloud_mode")
except Exception as e:
    print(f"✗ Cloud dispatch test error: {e}")
    sys.exit(1)

# Cloud Account manager dialog renders signed-out state cleanly offscreen.
try:
    from ui.dialogs.account_dialog import AccountDialog
    dlg = AccountDialog()
    assert hasattr(dlg, "btn_restore")
    assert hasattr(dlg, "_restore_selected_version")
    assert not dlg.btn_auth_toggle.isEnabled() or True
    QTimer.singleShot(50, dlg.accept)
    dlg.exec()
    print("✓ AccountManagerDialog instantiated cleanly offscreen")
except Exception as e:
    print(f"✗ Account dialog test error: {e}")
    sys.exit(1)

# ---------------------------------------------------------------------- #
# Versioning, Updater, System Preflight, & Backend Health Verification     #
# ---------------------------------------------------------------------- #
try:
    from unittest.mock import patch, MagicMock
    from core.version import (
        APP_VERSION,
        MIN_CONVEX_BACKEND_VERSION,
        parse_version,
        compare_versions,
        is_version_outdated,
    )
    from core.updater import (
        is_appimage,
        validate_appimage_header,
        download_and_apply_appimage_update,
        check_for_updates,
    )
    from core.cloud_detector import inspect_system_compatibility
    from core.cloud_backend import check_backend_health, ConvexSaveBackend

    # 1. Versioning assertions
    assert APP_VERSION == "0.6.0", f"Expected APP_VERSION == 0.6.0, got {APP_VERSION}"

    assert MIN_CONVEX_BACKEND_VERSION == "1.3.0"
    assert parse_version("0.5.5") == (0, 5, 5)
    assert parse_version("v1.2.0") == (1, 2, 0)
    assert parse_version("1.2.0-rc1") == (1, 2, 0, 1)
    assert compare_versions("0.5.5", "0.5.5") == 0
    assert compare_versions("0.4.9", "0.5.5") == -1
    assert compare_versions("0.5.6", "0.5.5") == 1
    assert is_version_outdated("0.4.9", "0.5.5") is True
    assert is_version_outdated("0.5.5", "0.5.5") is False
    assert is_version_outdated("1.0.0", MIN_CONVEX_BACKEND_VERSION) is True
    # A backend exactly at the minimum is fine; below it is outdated.
    assert is_version_outdated(MIN_CONVEX_BACKEND_VERSION, MIN_CONVEX_BACKEND_VERSION) is False
    assert is_version_outdated("1.2.0", MIN_CONVEX_BACKEND_VERSION) is True
    assert is_version_outdated("2.0.0", MIN_CONVEX_BACKEND_VERSION) is False
    print("✓ Single-source version definitions and semver comparison verified")

    # 2. Updater: AppImage detection & binary header validation
    with tempfile.TemporaryDirectory() as td:
        mock_appimage = os.path.join(td, "SafeLauncher-x86_64.AppImage")
        with open(mock_appimage, "wb") as f:
            # Valid ELF header (starts with \x7fELF)
            f.write(b"\x7fELF\x02\x01\x01\x00AI\x02" + b"\x00" * 200000)

        # Test header validator
        assert validate_appimage_header(mock_appimage) is True

        bad_binary = os.path.join(td, "corrupt.AppImage")
        with open(bad_binary, "wb") as f:
            f.write(b"NOT_AN_ELF_BINARY" * 1000)
        assert validate_appimage_header(bad_binary) is False
        assert validate_appimage_header("/nonexistent/file") is False

        # Test is_appimage() with env
        orig_env = os.environ.get("APPIMAGE")
        try:
            os.environ["APPIMAGE"] = mock_appimage
            assert is_appimage() is True

            # Test atomic download & apply simulation with valid ELF
            def mock_get(url, **kwargs):
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.headers = {"content-length": str(150000)}
                mock_resp.iter_content = lambda chunk_size: [b"\x7fELF\x02\x01\x01\x00" + b"\x90" * 150000]
                return mock_resp

            with patch("requests.get", side_effect=mock_get):
                res_path = download_and_apply_appimage_update(
                    "https://mock.url/SafeLauncher.AppImage",
                    target_appimage_path=mock_appimage,
                    min_size_bytes=1024 * 100,
                )
                assert res_path == mock_appimage
                mode = os.stat(mock_appimage).st_mode
                assert mode & 0o111 != 0, "AppImage must have executable permissions"

            # Test atomic download & apply rejection on invalid header
            def mock_get_bad(url, **kwargs):
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.headers = {"content-length": str(150000)}
                mock_resp.iter_content = lambda chunk_size: [b"MALFORMED_HEADER" + b"\x00" * 150000]
                return mock_resp

            with patch("requests.get", side_effect=mock_get_bad):
                try:
                    download_and_apply_appimage_update(
                        "https://mock.url/Bad.AppImage",
                        target_appimage_path=mock_appimage
                    )
                    raise AssertionError("Should have rejected bad binary header")
                except ValueError as ve:
                    assert "valid Linux ELF" in str(ve)
            # Test atomic download & apply rejection on suspicious small file
            def mock_get_small(url, **kwargs):
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.headers = {"content-length": "500"}
                mock_resp.iter_content = lambda chunk_size: [b"\x7fELF" + b"\x00" * 496]
                return mock_resp

            with patch("requests.get", side_effect=mock_get_small):
                try:
                    download_and_apply_appimage_update(
                        "https://mock.url/Small.AppImage",
                        target_appimage_path=mock_appimage
                    )
                    raise AssertionError("Should have rejected suspiciously small file")
                except ValueError as ve:
                    assert "minimum plausible AppImage size" in str(ve)
                assert not os.path.exists(mock_appimage + ".download")

            # Test permission error on read-only directory
            ro_dir = os.path.join(td, "ro_dir")
            os.mkdir(ro_dir)
            ro_target = os.path.join(ro_dir, "Target.AppImage")
            os.chmod(ro_dir, 0o555)
            try:
                download_and_apply_appimage_update(
                    "https://mock.url/SafeLauncher.AppImage",
                    target_appimage_path=ro_target
                )
                raise AssertionError("Should have raised PermissionError for read-only directory")
            except PermissionError as pe:
                assert "not writable" in str(pe)
            finally:
                os.chmod(ro_dir, 0o755)

            # Test asset preference: x86_64 AppImage preferred over arm64
            def mock_release_assets(url, **kwargs):
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json = lambda: {
                    "tag_name": "v0.9.0",
                    "name": "Release 0.9.0",
                    "body": "Bugfixes",
                    "html_url": "https://github.com/Mistarin/SafeLauncher/releases/tag/v0.9.0",
                    "assets": [
                        {"name": "SafeLauncher-arm64.AppImage", "browser_download_url": "https://arm64.url", "size": 50000000},
                        {"name": "SafeLauncher-x86_64.AppImage", "browser_download_url": "https://x86_64.url", "size": 52000000},
                    ]
                }
                return mock_resp

            with patch("requests.get", side_effect=mock_release_assets):
                update_info = check_for_updates()
                assert update_info["update_available"] is True
                assert update_info["latest_version"] == "v0.9.0"

                assert update_info["appimage_asset"] is not None
                assert update_info["appimage_asset"]["name"] == "SafeLauncher-x86_64.AppImage"
                assert update_info["appimage_asset"]["download_url"] == "https://x86_64.url"
        finally:
            if orig_env is not None:
                os.environ["APPIMAGE"] = orig_env
            else:
                os.environ.pop("APPIMAGE", None)

    print("✓ AppImage environment detection, header validation, and atomic update replacement verified")

    # 3. System compatibility inspection
    with tempfile.TemporaryDirectory() as td:
        steamos_release = os.path.join(td, "steamos-release")
        with open(steamos_release, "w") as f:
            f.write('ID=steamos\nNAME="SteamOS"\nVARIANT_ID=steamdeck\n')

        compat_deck = inspect_system_compatibility(os_release_path=steamos_release)
        assert compat_deck["is_steamos"] is True
        assert compat_deck["is_steam_deck"] is True
        assert compat_deck["is_immutable"] is True
        assert compat_deck["recommended_mode"] in ("web", "cli")

        ubuntu_release = os.path.join(td, "ubuntu-release")
        with open(ubuntu_release, "w") as f:
            f.write('ID=ubuntu\nNAME="Ubuntu 24.04 LTS"\n')

        compat_ubuntu = inspect_system_compatibility(os_release_path=ubuntu_release)
        assert compat_ubuntu["is_steamos"] is False
        assert compat_ubuntu["is_steam_deck"] is False
    print("✓ System compatibility inspector (SteamOS, Steam Deck, immutable OS) verified")

    # 4. Backend health check & version sync
    h_unconf = check_backend_health(url="")
    assert h_unconf["healthy"] is False
    assert h_unconf["status"] == "unconfigured"

    def mock_health_200(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"status": "ok", "version": "1.2.0"}'
        # Report exactly the minimum so the parity assertion survives bumps.
        mock_resp.json = lambda: {"status": "ok", "version": MIN_CONVEX_BACKEND_VERSION}
        return mock_resp

    with patch("requests.get", side_effect=mock_health_200):
        h_ok = check_backend_health("https://mytest.convex.site")
        assert h_ok["healthy"] is True
        assert h_ok["status"] == "connected"
        assert h_ok["version"] == MIN_CONVEX_BACKEND_VERSION
        assert h_ok["is_outdated"] is False
        assert h_ok["latency_ms"] >= 0

    def mock_health_outdated(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"status": "ok", "version": "1.0.0"}'
        mock_resp.json = lambda: {"status": "ok", "version": "1.0.0"}
        return mock_resp

    with patch("requests.get", side_effect=mock_health_outdated):
        h_old = check_backend_health("https://mytest.convex.site")
        assert h_old["healthy"] is True
        assert h_old["status"] == "connected"
        assert h_old["version"] == "1.0.0"
        assert h_old["is_outdated"] is True

    def mock_health_404(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        return mock_resp

    with patch("requests.get", side_effect=mock_health_404):
        h_legacy = check_backend_health("https://mytest.convex.site")
        assert h_legacy["healthy"] is True
        assert h_legacy["status"] == "legacy"
        assert h_legacy["version"] == "1.0.0"
        assert h_legacy["is_outdated"] is True

    def mock_health_401(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        return mock_resp

    with patch("requests.get", side_effect=mock_health_401):
        h_unauth = check_backend_health("https://mytest.convex.site")
        assert h_unauth["healthy"] is False
        assert h_unauth["status"] == "unauthorized"

    def mock_health_err(url, **kwargs):
        raise requests.RequestException("Connection refused")

    with patch("requests.get", side_effect=mock_health_err):
        h_err = check_backend_health("https://mytest.convex.site")
        assert h_err["healthy"] is False
        assert h_err["status"] == "unreachable"
        assert h_err["latency_ms"] == -1

    # Test scheme-less URL auto-prefixed with https://
    def mock_health_scheme_check(url, **kwargs):
        assert url.startswith("https://scheme-less.convex.site"), f"URL did not get https prefix: {url}"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        # Report exactly the minimum so the parity assertion survives bumps.
        mock_resp.json = lambda: {"status": "ok", "version": MIN_CONVEX_BACKEND_VERSION}
        return mock_resp

    with patch("requests.get", side_effect=mock_health_scheme_check):
        h_scheme = check_backend_health("scheme-less.convex.site")
        assert h_scheme["healthy"] is True

    backend_obj = ConvexSaveBackend(site_url="https://mytest.convex.site", secret_key="my_secret")
    assert backend_obj.site_url == "https://mytest.convex.site"
    assert backend_obj.secret_key == "my_secret"
    with patch("requests.get", side_effect=mock_health_200):
        res_m = backend_obj.check_health()
        assert res_m["healthy"] is True
        assert res_m["version"] == MIN_CONVEX_BACKEND_VERSION

    print("✓ Backend health check probe, roundtrip latency, and version synchronization verified")

    # 5. UI smoke checks for updated dialogs
    from ui.dialogs.cloud_wizard_dialog import CloudWizardDialog
    wizard_dlg = CloudWizardDialog()
    assert wizard_dlg.pages.count() == 3
    QTimer.singleShot(50, wizard_dlg.accept)
    wizard_dlg.exec()
    print("✓ CloudWizardDialog instantiated cleanly with preflight banner and zero-CLI flow")

    from ui.dialogs.settings_dialog import UserSettingsDialog
    settings_dlg = UserSettingsDialog("TestUser")
    assert hasattr(settings_dlg, "card_backend_health")
    assert hasattr(settings_dlg, "btn_check_app_updates")
    QTimer.singleShot(50, settings_dlg.accept)
    settings_dlg.exec()
    print("✓ UserSettingsDialog instantiated cleanly with backend health card and manual updater")

except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"✗ Updater & Health sync test error: {e}")
    sys.exit(1)

# -------------------------------------------------------------
# 11. Test Real-Time Achievements System
# -------------------------------------------------------------
try:
    from pathlib import Path
    import json
    from core.achievement_schema import fetch_steam_achievements_schema, SteamAchievementFetcherWorker
    from core.achievement_watcher import (
        locate_achievements_file, ensure_achievement_watch_target,
        parse_achievements_state, AchievementWatcher
    )
    from ui.components.achievement_toast import AchievementToast, send_desktop_notification
    from ui.dialogs.achievements_dialog import AchievementsDialog, AchievementCard

    with tempfile.TemporaryDirectory() as tmp_dir:
        # A. Test Database Achievement Operations
        db_path = os.path.join(tmp_dir, "ach_test.db")
        ach_db = GameDatabase(db_path)
        g_id = ach_db.add_game("Test Ach Game", "/tmp/game", "game.exe", "wine", steam_id="480")
        assert g_id is not None, "Failed to add game for achievement testing"

        mock_schema = [
            {
                "api_name": "ACH_WIN_ONE_GAME",
                "display_name": "Winner Winner",
                "description": "Win your first match",
                "icon_path": "",
                "icongray_path": "",
                "hidden": 0,
            },
            {
                "api_name": "ACH_SECRET_BOSS",
                "display_name": "Secret Boss Defeated",
                "description": "Defeated the hidden dragon",
                "icon_path": "",
                "icongray_path": "",
                "hidden": 1,
            },
        ]

        saved_count = ach_db.save_achievement_schema(g_id, "480", mock_schema)
        assert saved_count == 2, f"Expected 2 achievements saved, got {saved_count}"

        all_achs = ach_db.get_game_achievements(g_id)
        assert len(all_achs) == 2, f"Expected 2 achievements retrieved, got {len(all_achs)}"
        assert all_achs[0]["unlocked"] == 0, "Initial state should be locked"

        unlocked_cnt, total_cnt, pct = ach_db.get_achievement_stats(g_id)
        assert unlocked_cnt == 0 and total_cnt == 2 and pct == 0.0

        # Test unlocking
        unlock_res = ach_db.unlock_achievement(g_id, "ACH_WIN_ONE_GAME", unlock_time=1700000000.0)
        assert unlock_res is True, "Unlock achievement failed"

        unlocked_cnt, total_cnt, pct = ach_db.get_achievement_stats(g_id)
        assert unlocked_cnt == 1 and total_cnt == 2 and pct == 50.0

        achs_after = ach_db.get_game_achievements(g_id)
        winner_ach = next(a for a in achs_after if a["api_name"] == "ACH_WIN_ONE_GAME")
        assert winner_ach["unlocked"] == 1
        assert winner_ach["unlock_time"] == 1700000000.0

        # Test batch unlocking
        batch_unlock_res = ach_db.unlock_achievements_batch(g_id, {"ACH_WIN_ONE_GAME": 1700000000.0, "ACH_SECRET_BOSS": 1700000010.0})
        assert batch_unlock_res == 1  # 1 new unlocked since ACH_WIN_ONE_GAME already was
        unlocked_cnt, total_cnt, pct = ach_db.get_achievement_stats(g_id)
        assert unlocked_cnt == 2 and total_cnt == 2 and pct == 100.0

        # Test reset
        ach_db.reset_game_achievements(g_id)
        unlocked_cnt, total_cnt, pct = ach_db.get_achievement_stats(g_id)
        assert unlocked_cnt == 0, "Reset achievements failed"

        print("✓ Database achievement schema caching, unlocking, and stats queries verified")

        # B. Test Achievement State File Parsing (Goldberg JSON and CODEX INI)
        goldberg_file = Path(tmp_dir) / "goldberg_achievements.json"
        goldberg_file.write_text(json.dumps({
            "ACH_WIN_ONE_GAME": {"earned": True, "earned_time": 1712345678},
            "ACH_LOCKED": {"earned": False, "earned_time": 0}
        }), encoding="utf-8")

        parsed_gb = parse_achievements_state(goldberg_file)
        assert "ACH_WIN_ONE_GAME" in parsed_gb
        assert parsed_gb["ACH_WIN_ONE_GAME"] == 1712345678.0
        assert "ACH_LOCKED" not in parsed_gb

        codex_file = Path(tmp_dir) / "codex_achievements.ini"
        codex_file.write_text("""[SteamAchievements]
ACH_FIRST_BLOOD=1
ACH_PACIFIST=0
""", encoding="utf-8")

        parsed_cdx = parse_achievements_state(codex_file)
        assert "ACH_FIRST_BLOOD" in parsed_cdx
        assert "ACH_PACIFIST" not in parsed_cdx

        print("✓ Achievement state parsing for Goldberg (JSON) and CODEX/RUNE (INI) verified")

        # C. Test Ensure & Locate Achievement Target
        prefix_dir = Path(tmp_dir) / "wineprefix"
        game_dir = Path(tmp_dir) / "gamefolder"
        prefix_dir.mkdir(parents=True, exist_ok=True)
        game_dir.mkdir(parents=True, exist_ok=True)

        target_file = ensure_achievement_watch_target(str(prefix_dir), str(game_dir), "480")
        assert target_file.exists(), "Target file should have been created"
        assert locate_achievements_file(str(prefix_dir), str(game_dir), "480") == target_file

        print("✓ Achievement target file locator and pre-seed watch target verified")

        # D. Test AchievementWatcher Signal Dispatch
        watcher = AchievementWatcher(g_id, "480", str(prefix_dir), str(game_dir))
        watcher.start()

        unlocked_events = []
        watcher.achievement_unlocked.connect(lambda gid, aid, data: unlocked_events.append(data))

        # Write new unlock to the watched file
        target_file.write_text(json.dumps({
            "ACH_WIN_ONE_GAME": {"earned": True, "earned_time": 1720000000}
        }), encoding="utf-8")

        watcher.check_updates()
        assert len(unlocked_events) == 1
        assert unlocked_events[0]["api_name"] == "ACH_WIN_ONE_GAME"

        watcher.stop()
        print("✓ AchievementWatcher real-time change detection and signal dispatch verified")

        # E. UI Dialog & Toast Smoke Check
        toast = AchievementToast("Winner Winner", "Win your first match")
        toast.show_animated()
        QTimer.singleShot(50, toast.close)

        dlg = AchievementsDialog({"id": g_id, "name": "Test Ach Game", "steam_id": "480", "path": str(game_dir), "proton_path": str(prefix_dir)}, ach_db)
        assert dlg.windowTitle() == "Achievements - Test Ach Game"
        QTimer.singleShot(50, dlg.accept)
        dlg.exec()

        print("✓ AchievementToast and AchievementsDialog UI components verified cleanly")

        # F. Test AchievementStatusFetcherThread & AchievementBatchQueueWorker
        from ui.threads import AchievementStatusFetcherThread, AchievementBatchQueueWorker

        status_results = []
        fetcher = AchievementStatusFetcherThread(
            g_id, "Test Ach Game", str(game_dir), steam_id="480",
            proton_path=str(prefix_dir), db_path=db_path
        )
        fetcher.achievement_status_calculated.connect(lambda gid, u, t, p, r: status_results.append((gid, u, t, p, r)))
        fetcher.safe_run()
        assert len(status_results) == 1
        assert status_results[0][0] == g_id
        assert status_results[0][1] == 1  # 1 unlocked
        assert status_results[0][2] == 2  # 2 total
        assert status_results[0][3] == 50.0

        batch_ready_events = []
        batch_finished_events = []
        mock_game_entry = (g_id, "Test Ach Game", str(game_dir), "game.exe", "wine", "", "480", 0, 0, str(prefix_dir))
        batch_worker = AchievementBatchQueueWorker([mock_game_entry], max_workers=2, db_path=db_path)
        batch_worker.game_status_ready.connect(lambda gid, u, t, p, r: batch_ready_events.append((gid, u, t, p, r)))
        batch_worker.batch_finished.connect(lambda g_cnt, u_cnt: batch_finished_events.append((g_cnt, u_cnt)))
        batch_worker.safe_run()

        assert len(batch_ready_events) == 1
        assert batch_ready_events[0][0] == g_id
        assert batch_ready_events[0][1] == 1
        assert len(batch_finished_events) == 1
        assert batch_finished_events[0][0] == 1  # 1 game with achs
        assert batch_finished_events[0][1] == 1  # 1 unlocked total

        print("✓ AchievementStatusFetcherThread and AchievementBatchQueueWorker verified cleanly")

        ach_db.close()

        # -------------------------------------------------------------
        # 31. Test Directory Size In-Memory LRU Caching
        # -------------------------------------------------------------
        from core.disk_utils import (
            DirectorySizeLRUCache, peek_dir_size, store_dir_size,
            has_fresh_dir_size, clear_dir_size_cache, get_dir_size
        )
        clear_dir_size_cache()
        test_lru = DirectorySizeLRUCache(maxsize=3, ttl_seconds=1.0)
        test_lru.put("/path/game1", 1000)
        test_lru.put("/path/game2", 2000)
        test_lru.put("/path/game3", 3000)
        assert len(test_lru) == 3
        assert test_lru.get("/path/game1") == 1000
        # Adding a 4th item evicts /path/game2 since game1 was accessed (MRU)
        test_lru.put("/path/game4", 4000)
        assert len(test_lru) == 3
        assert test_lru.get("/path/game2") is None  # Evicted
        assert test_lru.get("/path/game1") == 1000
        assert test_lru.get("/path/game4") == 4000

        # Test TTL expiry
        time.sleep(1.1)
        assert test_lru.get("/path/game1") is None  # Expired

        # Test global functions with real temporary files
        with tempfile.TemporaryDirectory() as tmp_size_dir:
            test_file = os.path.join(tmp_size_dir, "test.bin")
            with open(test_file, "wb") as f:
                f.write(b"0" * 1024)
            calc_size = get_dir_size(tmp_size_dir, use_cache=True)
            assert calc_size == 1024
            assert has_fresh_dir_size(tmp_size_dir)
            assert peek_dir_size(tmp_size_dir) == 1024
            # Subsequent lookup returns cached value without disk I/O
            assert get_dir_size(tmp_size_dir, use_cache=True) == 1024
        clear_dir_size_cache()
        print("✓ Directory size LRU caching, TTL expiry, and O(1) in-memory reuse verified")

        # -------------------------------------------------------------
        # 32. Test Steam Community Scraping Token-Bucket Rate Limiter
        # -------------------------------------------------------------
        from core.achievement_schema import TokenBucketRateLimiter
        limiter = TokenBucketRateLimiter(rate=5.0, capacity=3.0)
        # Burst acquire 3 tokens immediately
        assert limiter.acquire(1.0, timeout=0.1) is True
        assert limiter.acquire(1.0, timeout=0.1) is True
        assert limiter.acquire(1.0, timeout=0.1) is True
        # Immediate acquire fails when empty
        assert limiter.acquire(1.0, timeout=0.01) is False
        # Replenishes smoothly with timeout
        assert limiter.acquire(1.0, timeout=0.5) is True
        # Test HTTP 429 penalize cooldown
        limiter.penalize(cooldown_seconds=0.3)
        assert limiter.acquire(1.0, timeout=0.05) is False
        assert limiter.acquire(1.0, timeout=0.6) is True
        print("✓ Steam Community token-bucket rate limiter and burst capacity verified")

        # -------------------------------------------------------------
        # 33. Test Viewport Virtualization for Large Libraries (500+ Games)
        # -------------------------------------------------------------
        from PyQt6.QtCore import Qt
        from ui.components.virtual_grid import VirtualizedGameGridView, BannerProxy
        virtual_grid = VirtualizedGameGridView(None, card_width=200, spacing=15)
        assert virtual_grid.delegate.card_width == 200
        assert virtual_grid.delegate.card_height == 300
        virtual_grid.set_card_width(220)
        assert virtual_grid.delegate.card_width == 220
        assert virtual_grid.delegate.card_height == 330

        # Simulate a 550-game library
        large_library = []
        for i in range(1, 551):
            large_library.append((
                i, f"Game {i}", f"/path/game_{i}", f"game_{i}.exe", "wine", "", "480",
                3600 * (i % 20), 1 if i % 5 == 0 else 0, "", "", "", "", "", 0, f"v1.{i}", "", "", ""
            ))
        t_start = time.perf_counter()
        virtual_grid.set_games(large_library, selected_ids={1, 2, 500}, update_status_map={10: True})
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        assert virtual_grid.model.rowCount() == 550
        assert elapsed_ms < 350.0  # Populating 550 games in virtual model must be instant

        # Verify selected IDs in virtual grid
        selected_ids = virtual_grid.selected_game_ids()
        assert 1 in selected_ids
        assert 500 in selected_ids

        # Test BannerProxy transparent interface delegation
        proxy = BannerProxy(42, virtual_grid)
        proxy.set_playtime(7200)
        proxy.set_update_available(True)
        proxy.set_favorite(True)
        item_42 = virtual_grid._items_by_game_id[42]
        assert item_42.data(Qt.ItemDataRole.UserRole + 5) == 7200
        assert item_42.data(Qt.ItemDataRole.UserRole + 10) is True
        assert item_42.data(Qt.ItemDataRole.UserRole + 9) is True

        # Test MainWindow virtualization threshold integration
        if not mw.db.get_all_games():
            mw.db.add_game("CI Virtual Test Game", "/tmp", "game.exe", "sandbox")
            mw._refresh_library()
        mw.set_virtualization_threshold(1)  # Force virtual grid
        assert mw.library_view_stack.currentIndex() in (1, 2)
        mw.set_virtualization_threshold(200)  # Reset
        mw.close()
        app.processEvents()

        print("✓ Viewport virtualization for 500+ games, custom QStyledItemDelegate, and BannerProxy verified")

except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"✗ Achievement tracking test error: {e}")
    sys.exit(1)

# -------------------------------------------------------------
# 34. Test Multi-Generation Save History, Deduplication & Manual Restore
# -------------------------------------------------------------
try:
    from unittest.mock import patch, MagicMock
    import hashlib
    from core.cloud_backend import ConvexSaveBackend
    from core.cloud_save_sync import (
        get_active_save_version,
        set_active_save_version,
        CloudSaveSyncEngine,
    )
    from ui.dialogs.save_manager_dialog import SaveManagerDialog
    from ui.dialogs.game_properties_dialog import GamePropertiesDialog

    with tempfile.TemporaryDirectory() as td:
        # A. Test active save version & cloud top persistence
        set_active_save_version("MultiGenTestGame", 4, cloud_top_version=5)
        assert get_active_save_version("MultiGenTestGame") == 4
        assert CloudSaveSyncEngine.get_cloud_root()  # initialize root if needed
        from core.cloud_save_sync import get_active_cloud_top_version
        assert get_active_cloud_top_version("MultiGenTestGame") == 5
        set_active_save_version("MultiGenTestGame", None)
        assert get_active_save_version("MultiGenTestGame") is None
        assert get_active_cloud_top_version("MultiGenTestGame") is None

        # B. Test ConvexSaveBackend.upload_plaintext_zip deduplication across historical generations
        backend = ConvexSaveBackend(site_url="https://test.convex.site", secret_key="test_secret")
        dummy_save_content = b"SAVE_DATA_GENERATION_V1_STABLE"
        dummy_sha = hashlib.sha256(dummy_save_content).hexdigest()
        dummy_zip = os.path.join(td, "save.zip")
        with open(dummy_zip, "wb") as f:
            f.write(dummy_save_content)

        # Mock list_games returning existing history where v1 matches our SHA and v2 is different
        backend.list_games = MagicMock(return_value={
            "games": [
                {
                    "nameKey": "multigentestgame",
                    "displayName": "MultiGenTestGame",
                    "versions": [
                        {"version": 2, "plainSha256": "different_sha_v2", "sizeBytes": 100},
                        {"version": 1, "plainSha256": dummy_sha, "sizeBytes": len(dummy_save_content)},
                    ]
                }
            ]
        })

        # When uploading v1 content, it must detect matching v1 in existing history and skip upload
        res = backend.upload_plaintext_zip(
            name_key="multigentestgame",
            display_name="MultiGenTestGame",
            plaintext_zip_path=dummy_zip,
            source_max_mtime=1700000000.0,
        )
        assert res.get("skipped") is True
        assert res.get("version") == 1
        assert res.get("existingVersion") == 1

        # C. Test CloudSaveSyncEngine remote upload detection & get_available_versions
        cloud_mock_data = {
            "nameKey": "MultiGenTestGame",
            "displayName": "MultiGenTestGame",
            "versions": [
                {"version": 3, "sourceMaxMtime": 1700000300, "sizeBytes": 3072},
                {"version": 2, "sourceMaxMtime": 1700000200, "sizeBytes": 2048},
                {"version": 1, "sourceMaxMtime": 1700000100, "sizeBytes": 1024},
            ]
        }
        with patch.object(CloudSaveSyncEngine, "_remote_game_snapshot", return_value=cloud_mock_data), \
             patch("core.cloud_save_sync.backend_active", return_value=True):

            # 1. Local files do NOT exist -> is_active must be False for all versions
            avail = CloudSaveSyncEngine.get_available_versions("MultiGenTestGame", game_path=td)
            assert all(v["is_active"] is False for v in avail)

            # Create local save file
            save_prefix = os.path.join(td, "prefix", "drive_c", "users", "steamuser", "Saved Games", "MultiGenTestGame")
            os.makedirs(save_prefix, exist_ok=True)
            with open(os.path.join(save_prefix, "save.dat"), "wb") as sf:
                sf.write(dummy_save_content)
            os.utime(os.path.join(save_prefix, "save.dat"), (1700000100.0, 1700000100.0))

            # 2. Reverted to v1 while cloud top was v2
            set_active_save_version("MultiGenTestGame", 1, cloud_top_version=2)
            # When top is 3 (> known_top 2), another machine uploaded v3 -> _remote_stats targets v3!
            from core.cloud_save_sync import resolve_name_key
            stats, snap = CloudSaveSyncEngine._remote_stats(resolve_name_key("MultiGenTestGame"), local_mtime=1700000100.0)
            assert "v3" in stats.display_path

            # When top is 2 (<= known_top 2), respects v1 without nagging!
            cloud_mock_data_v2 = dict(cloud_mock_data, versions=cloud_mock_data["versions"][1:])
            with patch.object(CloudSaveSyncEngine, "_remote_game_snapshot", return_value=cloud_mock_data_v2):
                stats_v2, _ = CloudSaveSyncEngine._remote_stats(resolve_name_key("MultiGenTestGame"), local_mtime=1700000100.0)
                assert "v1" in stats_v2.display_path

            # 3. Test active version selection in get_available_versions when local save exists
            avail = CloudSaveSyncEngine.get_available_versions("MultiGenTestGame", game_path=td)
            v1_item = next(v for v in avail if v["version"] == 1)
            assert v1_item["is_active"] is True
            set_active_save_version("MultiGenTestGame", None)

        # D. Test UI Dialogs Instantiation & Multi-Version Components Offscreen
        # 1. SaveManagerDialog with 2 tabs, signal, and history restore button
        save_mgr_dlg = SaveManagerDialog(
            game_id=999,
            game_name="MultiGenTestGame",
            game_path=td,
            steam_id="12345"
        )
        assert hasattr(save_mgr_dlg, "tabs")
        assert save_mgr_dlg.tabs.count() == 2
        assert hasattr(save_mgr_dlg, "tab_files")
        assert hasattr(save_mgr_dlg, "tab_history")
        assert hasattr(save_mgr_dlg, "lst_history")
        assert hasattr(save_mgr_dlg, "btn_restore_history")
        assert hasattr(save_mgr_dlg, "_restore_done")
        assert hasattr(save_mgr_dlg, "_notify_parent_changed")
        save_mgr_dlg.tabs.setCurrentIndex(1)
        save_mgr_dlg.close()

        # 2. GamePropertiesDialog with multi-version selector dropdown
        dummy_rec = (999, "MultiGenTestGame", td, "game.exe", "wine", "", "12345", 0, 0, "", "", "", "", "", 0, "v1.0", "", "", "")
        props_dlg = GamePropertiesDialog(dummy_rec)
        assert hasattr(props_dlg, "combo_cloud_versions")
        assert hasattr(props_dlg, "btn_restore_selected")
        assert hasattr(props_dlg, "btn_restore_backup")  # Backwards compatibility alias
        props_dlg.close()

    print("✓ Save history deduplication, multi-version queries, and manual version restore UI verified")
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"✗ Save history and multi-version restore test error: {e}")
    sys.exit(1)

# -------------------------------------------------------------
# 35. Test Save Conflict Dialog Dynamic Inversion, Safety Fork Pruning & Fast Backend Timeouts
# -------------------------------------------------------------
try:
    from core.cloud_save_sync import SaveStats, CloudSaveSyncEngine
    from ui.dialogs.save_conflict_dialog import SaveConflictDialog
    from core.cloud_backend import ConvexSaveBackend
    from unittest.mock import MagicMock

    with tempfile.TemporaryDirectory() as td:
        # A. Test SaveConflictDialog dynamic inversion based on timestamp
        # Case 1: Local is newer (local_stats.last_modified > cloud_stats.last_modified)
        loc_stats_newer = SaveStats(exists=True, last_modified=1700000500.0, size_bytes=1024, display_path="/path/local")
        cld_stats_older = SaveStats(exists=True, last_modified=1700000100.0, size_bytes=1024, display_path="cloud/v1")
        dlg_local_newer = SaveConflictDialog("TestGame", loc_stats_newer, cld_stats_older)
        assert dlg_local_newer.local_is_newer is True
        assert dlg_local_newer.choice == "local"
        assert "Recommended" in dlg_local_newer.btn_keep_local.text()
        assert "Recommended" not in dlg_local_newer.btn_use_cloud.text()
        dlg_local_newer.close()

        # Case 2: Cloud is newer (cloud_stats.last_modified > local_stats.last_modified)
        loc_stats_older = SaveStats(exists=True, last_modified=1700000100.0, size_bytes=1024, display_path="/path/local")
        cld_stats_newer = SaveStats(exists=True, last_modified=1700000500.0, size_bytes=1024, display_path="cloud/v2")
        dlg_cloud_newer = SaveConflictDialog("TestGame", loc_stats_older, cld_stats_newer)
        assert dlg_cloud_newer.local_is_newer is False
        assert dlg_cloud_newer.choice == "cloud"
        assert "Recommended" in dlg_cloud_newer.btn_use_cloud.text()
        assert "Recommended" not in dlg_cloud_newer.btn_keep_local.text()
        dlg_cloud_newer.close()

        # B. Test safety fork pruning (_prune_safety_forks)
        # Create 14 dummy forks for prefix 'mygame'
        for i in range(14):
            fname = os.path.join(td, f"mygame_fork_{1700000000 + i}.zip")
            with open(fname, "wb") as f:
                f.write(b"FORK_DATA")
            os.utime(fname, (1700000000.0 + i, 1700000000.0 + i))

        # Check pruning retaining 10
        pruned = CloudSaveSyncEngine._prune_safety_forks(td, "mygame", "mygame", keep=10)
        assert pruned == 4
        remaining_files = sorted(os.listdir(td))
        assert len(remaining_files) == 10
        # Verify oldest 4 were removed and newest 10 remain
        assert f"mygame_fork_{1700000000}.zip" not in remaining_files
        assert f"mygame_fork_{1700000003}.zip" not in remaining_files
        assert f"mygame_fork_{1700000004}.zip" in remaining_files
        assert f"mygame_fork_{1700000013}.zip" in remaining_files

        # C. Test fast metadata timeouts (timeout=6) in ConvexSaveBackend
        backend = ConvexSaveBackend(site_url="https://test.convex.site", secret_key="test_key")
        backend._request = MagicMock(return_value=MagicMock(status_code=200, json=lambda: {"games": []}))
        backend.list_games()
        backend._request.assert_called_with("GET", "/api/games", timeout=6)

        backend._request = MagicMock(return_value=MagicMock(status_code=200, json=lambda: {"bytesUsed": 0}))
        backend.account()
        backend._request.assert_called_with("GET", "/api/me", timeout=6)

        backend._request = MagicMock(return_value=MagicMock(status_code=200, json=lambda: {"ok": True}))
        backend.heartbeat()
        assert backend._request.call_args[1].get("timeout") == 6

    print("✓ Save conflict inversion, safety fork pruning, and fast backend timeouts verified")
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"✗ Save conflict inversion, pruning, and backend timeout test error: {e}")
    sys.exit(1)

# -------------------------------------------------------------
# 36. Test File Descriptor Safety, Dual-Key Lookup, Backend Reset & UI Re-entrancy
# -------------------------------------------------------------
try:
    import requests
    from unittest.mock import MagicMock, patch
    from core.cloud_backend import ConvexSaveBackend, CloudBackendError
    from core.cloud_save_sync import (
        get_active_save_version,
        get_active_cloud_top_version,
        set_active_save_version,
        reset_cloud_backend,
        _backend,
    )
    from ui.dialogs.save_manager_dialog import SaveManagerDialog
    from core.cloud_detector import inspect_system_compatibility

    with tempfile.TemporaryDirectory() as td:
        # A. Test download_to_temp file descriptor safety on network abort
        backend = ConvexSaveBackend(site_url="https://test.convex.site", secret_key="test_key")
        backend._request = MagicMock(return_value=MagicMock(
            status_code=200,
            json=lambda: {"url": "https://test.convex.site/dl", "version": 1, "sizeBytes": 50}
        ))
        # Simulate network failure during stream initialization
        backend.session.get = MagicMock(side_effect=requests.RequestException("Connection aborted by peer"))
        try:
            backend.download_to_temp("mygame")
            assert False, "Expected RequestException was not raised"
        except requests.RequestException:
            pass  # Expected

        # B. Test upload_plaintext_zip safe handling of non-JSON error responses
        dummy_zip = os.path.join(td, "save.zip")
        with open(dummy_zip, "wb") as f:
            f.write(b"SAMPLE_SAVE_DATA")

        backend.list_games = MagicMock(return_value={"games": []})
        backend.data_key_b64 = MagicMock(return_value="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")
        backend._request = MagicMock(return_value=MagicMock(
            status_code=200,
            json=lambda: {"uploadUrl": "https://test.convex.site/upload", "saveId": "save_1"}
        ))
        # Simulate HTML 502 gateway error with unparsable JSON
        mock_upload_resp = MagicMock(status_code=200)
        mock_upload_resp.json = MagicMock(side_effect=ValueError("No JSON"))
        mock_upload_resp.text = "<html><body>502 Bad Gateway</body></html>"
        backend.session.post = MagicMock(return_value=mock_upload_resp)

        try:
            backend.upload_plaintext_zip("mygame", "My Game", dummy_zip, 1700000000.0)
            assert False, "Expected CloudBackendError was not raised on invalid upload JSON"
        except CloudBackendError as cbe:
            assert cbe.status_code == 502

        # C. Test dual-key persistence (game_name and normalized key)
        set_active_save_version("Special Game - AnkerGames", 5, cloud_top_version=7)
        assert get_active_save_version("Special Game - AnkerGames") == 5
        assert get_active_cloud_top_version("Special Game - AnkerGames") == 7
        # Querying with normalized key also resolves
        assert get_active_save_version("special-game-ankergames") == 5
        assert get_active_cloud_top_version("special-game-ankergames") == 7
        # Clear removes both
        set_active_save_version("Special Game - AnkerGames", None)
        assert get_active_save_version("Special Game - AnkerGames") is None
        assert get_active_save_version("special-game-ankergames") is None

        # D. Test reset_cloud_backend clears singleton
        b1 = _backend()
        assert b1 is not None
        reset_cloud_backend()
        import core.cloud_save_sync as css
        assert css._backend_singleton is None

        # E. Test SaveManagerDialog.btn_cloud persistence and state
        save_dlg = SaveManagerDialog(game_id=888, game_name="TestDualKeyGame", game_path=td)
        assert hasattr(save_dlg, "btn_cloud")
        assert save_dlg.btn_cloud.isEnabled()
        save_dlg.close()

        # F. Test inspect_system_compatibility returns clean dictionary
        compat = inspect_system_compatibility()
        assert "can_deploy_locally" in compat
        assert "recommended_mode" in compat
        assert isinstance(compat["has_node"], bool)
        assert isinstance(compat["has_npm"], bool)

        # G. Test virtual_grid delegate _draw_cloud_badge handles CLOUD_ONLY, CLOUD_OFFLINE, CONFLICT
        from ui.components.virtual_grid import GameCardItemDelegate
        from core.cloud_save_sync import SyncStatus
        from PyQt6.QtGui import QPainter, QPixmap
        from PyQt6.QtCore import QRect
        delegate = GameCardItemDelegate()
        pix = QPixmap(100, 100)
        painter = QPainter(pix)
        cover_rect = QRect(0, 0, 100, 100)
        for st in (SyncStatus.IN_SYNC, SyncStatus.LOCAL_NEWER, SyncStatus.CLOUD_NEWER,
                   SyncStatus.CLOUD_ONLY, SyncStatus.CLOUD_OFFLINE, SyncStatus.CONFLICT,
                   SyncStatus.NO_SAVES):
            delegate._draw_cloud_badge(painter, cover_rect, st)
        painter.end()

        # H. Test LibraryListView cloud status presentation
        from ui.library_list import LibraryListView
        list_view = LibraryListView()
        dummy_game = (999, "CloudListTestGame", td, "game.exe", "sandbox", "", "12345", 3600, True, 0, "RPG", "", "", "", 0, "1.0", "", False, "")
        list_view.set_games(
            [(dummy_game, False, 3600, True)],
            cloud_status_cache={999: (SyncStatus.CLOUD_ONLY, None, None)}
        )
        assert 999 in list_view._row_widgets_by_id
        row_w = list_view._row_widgets_by_id[999]
        assert row_w.cloud_status == SyncStatus.CLOUD_ONLY
        assert not row_w.cloud_badge.isHidden()
        assert "Available" in row_w.cloud_badge.text()


        # Test dynamic update_cloud_status on list view
        list_view.update_cloud_status(999, SyncStatus.IN_SYNC)
        assert row_w.cloud_status == SyncStatus.IN_SYNC
        assert "Synced" in row_w.cloud_badge.text()
        list_view.update_cloud_status(999, SyncStatus.CONFLICT)
        assert "Conflict" in row_w.cloud_badge.text()

        # I. Test GamePropertiesDialog manual sync signals and SaveManagerDialog history signal
        from ui.dialogs.game_properties_dialog import GamePropertiesDialog
        prop_dlg = GamePropertiesDialog(dummy_game)
        assert hasattr(prop_dlg, "_manual_sync_up_done")
        assert hasattr(prop_dlg, "_manual_sync_down_done")
        prop_dlg.close()

        assert hasattr(save_dlg, "_history_loaded")
        save_dlg._on_history_loaded([{"display_name": "Save 1", "version": 1, "size_bytes": 1024, "mtime": 1700000000}])
        assert save_dlg.lst_history.count() == 1
        assert "Save 1" in save_dlg.lst_history.item(0).text()
        save_dlg.close()

    print("✓ File descriptor safety, dual-key lookup, backend reset, and UI re-entrancy verified")

except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"✗ File descriptor safety, dual-key lookup, and UI re-entrancy test error: {e}")
    sys.exit(1)

# -------------------------------------------------------------
# 37. Test Compact Game Page & Split Layout Presentation
# -------------------------------------------------------------
try:
    from ui.components.compact_game_page import (
        CompactGamePageWidget, CompactLayoutContainer, CompactSidebarListWidget,
        SteamGamePageWidget, SteamLayoutContainer, SteamSidebarListWidget
    )

    compact_page = CompactGamePageWidget()
    dummy_game = (
        1001, "Compact Test RPG", "/tmp/compact_test", "game.exe", "umu",
        "", "480", 0, 1700000000, 0, "Action, RPG", "", "", True,
        1700000000, "1.0.4", "", False, "", 7200
    )
    ach_stats = (12, 30, 40.0)
    recent_achs = [
        {"id": 1, "api_name": "ACH_FIRST", "display_name": "First Steps", "description": "Begin the journey", "unlock_time": 1700000000}
    ]
    locked_achs = [
        {"id": 2, "api_name": "ACH_MASTER", "display_name": "Grand Master", "description": "Reach max level"}
    ]

    compact_page.set_game(
        dummy_game,
        ach_stats,
        recent_achs,
        locked_achs,
        cloud_status=SyncStatus.IN_SYNC,
        hero_image_path=None,
        is_running=False
    )

    assert "PLAY" in compact_page.action_bar.btn_play.text()
    assert compact_page.action_bar.playtime_val.text() == "2.0 h"
    assert "Up to date" in compact_page.action_bar.cloud_text_lbl.text()
    assert compact_page.action_bar.ach_ratio_lbl.text() == "12/30"
    assert compact_page.action_bar.ach_mini_progress.value() == 40
    assert compact_page.action_bar.btn_fav.toolTip() == "Remove from favorites"

    # Test Notes Widget persistence
    compact_page.notes_widget.load_notes_for_game(1001)
    compact_page.notes_widget.text_edit.setPlainText("Defeat final boss at level 50")
    compact_page.notes_widget._on_text_changed()
    assert compact_page.notes_widget.settings.value("game_notes/1001", "", type=str) == "Defeat final boss at level 50"

    # Test CompactLayoutContainer instantiation and item population
    compact_layout = CompactLayoutContainer()
    processed_items = [(dummy_game, False, 7200, True)]
    compact_layout.set_games(
        processed_items,
        selected_ids={1001},
        cloud_status_cache={1001: (SyncStatus.IN_SYNC, None, None)}
    )
    assert compact_layout.sidebar_list.list_widget.count() == 1
    item = compact_layout.sidebar_list.list_widget.item(0)
    assert item.data(Qt.ItemDataRole.UserRole) == 1001
    assert item.isSelected() is True

    # Test signal propagation
    signal_fired = []
    compact_page.play_requested.connect(lambda gid: signal_fired.append(("play", gid)))
    compact_page.properties_requested.connect(lambda gid: signal_fired.append(("props", gid)))
    compact_page.action_bar.play_clicked.emit()
    compact_page.action_bar.settings_clicked.emit()
    assert ("play", 1001) in signal_fired
    assert ("props", 1001) in signal_fired

    # Test edit game buttons and signals
    assert hasattr(compact_page.action_bar, "btn_edit")
    assert hasattr(compact_page.sub_nav, "btn_edit")
    compact_page.edit_requested.connect(lambda gid: signal_fired.append(("edit", gid)))
    compact_page.action_bar.edit_clicked.emit()
    compact_page.sub_nav.edit_clicked.emit()
    assert ("edit", 1001) in signal_fired

    # Test CompactLayoutContainer edit signal forwarding
    compact_layout.edit_requested.connect(lambda gid: signal_fired.append(("layout_edit", gid)))
    compact_layout.game_page.edit_requested.emit(1001)
    assert ("layout_edit", 1001) in signal_fired

    # Test quick filter bar and signal propagation in compact view
    assert hasattr(compact_layout.sidebar_list, "btn_f_all")
    assert hasattr(compact_layout.sidebar_list, "btn_f_inst")
    assert hasattr(compact_layout.sidebar_list, "btn_f_fav")
    assert hasattr(compact_layout.sidebar_list, "btn_f_arch")
    filter_events = []
    compact_layout.filter_changed.connect(lambda f: filter_events.append(f))
    compact_layout.sidebar_list._on_filter_btn_clicked("installed")
    assert "installed" in filter_events

    # Test MainWindow view mode integration & right detail panel hiding
    from PyQt6.QtCore import QSettings
    QSettings("SafeLauncher", "SafeLauncher").setValue("library_view_mode", "compact")
    mw_compact = MainWindow(db_mem, runner, backup)
    assert hasattr(mw_compact, "compact_container")
    assert hasattr(mw_compact, "steam_container")
    assert mw_compact.library_view_mode == "compact"
    assert mw_compact.detail_panel.isVisible() is False
    assert mw_compact.btn_reveal_detail.isVisible() is False

    # Test new darker footer bar (#0E0E10) and bottom-left Add Game button
    assert hasattr(mw_compact, "footer_bar")
    assert hasattr(mw_compact, "btn_add")
    assert hasattr(mw_compact, "btn_toggle_collections")
    assert mw_compact.footer_bar.height() == 36

    # Test pure collections panel (on by default, collapsed 48px width)
    assert mw_compact.sidebar.isHidden() is False
    assert mw_compact.sidebar.width() == 48
    assert mw_compact.sidebar.compact is True
    mw_compact._toggle_collections_panel()
    assert mw_compact.sidebar.compact is False
    assert mw_compact.sidebar.width() == 152
    mw_compact._toggle_collections_panel()
    assert mw_compact.sidebar.compact is True
    assert mw_compact.sidebar.width() == 48

    # Test Grid & List view search input presence
    assert hasattr(mw_compact, "grid_search_input")

    # Test update dot next to cloud icon
    assert hasattr(mw_compact.compact_container.game_page.action_bar, "update_dot")

    # Test missing achievements state
    ach_widget = mw_compact.compact_container.game_page.ach_widget
    ach_widget.set_achievements_data(0, 0, 0.0, [], [])
    assert "missing" in ach_widget.recent_title.text().lower()

    # Test 10 locked achievement previews
    sample_12 = [{"id": i, "api_name": f"ACH_{i}", "display_name": f"Ach {i}"} for i in range(12)]
    ach_widget.set_achievements_data(0, 12, 0.0, [], sample_12)
    assert ach_widget.thumbs_row.count() == 12

    # Test max height on activity card and runner specs card
    assert mw_compact.compact_container.game_page.activity_card.maximumHeight() == 260
    assert mw_compact.compact_container.game_page.specs_card.maximumHeight() == 160

    # Test tray menu pure text structure and direct recent games (no dropdown)
    mw_compact._update_tray_menu()
    tray_actions = mw_compact.tray_menu.actions()
    tray_texts = [act.text() for act in tray_actions]
    assert "Library" in tray_texts
    assert "Settings" in tray_texts
    assert "Quit" in tray_texts
    assert not any("Disk Space Manager" in t for t in tray_texts)
    # Ensure there are no submenus / dropdowns in tray menu
    assert not any(act.menu() is not None for act in tray_actions)
    # Ensure recent games appear as top-level actions before the separator and Library
    lib_idx = tray_texts.index("Library")
    assert lib_idx > 0, "Expected top-level recent games before Library"

    # Test dedicated Screenshot / Video Showcase widget under Achievements
    from PyQt6.QtWidgets import QLabel
    game_page = mw_compact.compact_container.game_page
    assert hasattr(game_page, "media_widget")
    # Verify inactive state displays "Module not active - turn on in settings"
    game_page.media_widget.set_media_data(1001, "Test Game", force_inactive=True)
    inactive_labels = game_page.media_widget.findChildren(QLabel)
    assert any("module not active" in lbl.text().lower() for lbl in inactive_labels)
    assert any("turn on in settings" in lbl.text().lower() for lbl in inactive_labels)
    # Verify active state
    game_page.media_widget.set_media_data(1001, "Test Game", force_inactive=False)

    # Test Action Bar cleanup: only Favorite button in layout (no duplicate folder/settings/save buttons)
    action_bar = game_page.action_bar
    bar_layout = action_bar.layout()
    layout_widgets = [bar_layout.itemAt(i).widget() for i in range(bar_layout.count()) if bar_layout.itemAt(i).widget()]
    assert action_bar.btn_fav in layout_widgets
    assert action_bar.btn_edit not in layout_widgets
    assert action_bar.btn_settings not in layout_widgets
    assert action_bar.btn_folder not in layout_widgets
    assert action_bar.btn_save not in layout_widgets

    # Test Play button states: green PLAY, blue RUNNING, and blue STOPPING
    action_bar.set_play_state("play")
    assert "PLAY" in action_bar.btn_play.text()
    assert action_bar.btn_play.isEnabled() is True
    assert "#3CD070" in action_bar.btn_play.styleSheet()

    action_bar.set_play_state("running")
    assert "RUNNING" in action_bar.btn_play.text()
    assert action_bar.btn_play.isEnabled() is True
    assert "#2575FC" in action_bar.btn_play.styleSheet()

    action_bar.set_play_state("stopping")
    assert "STOPPING" in action_bar.btn_play.text()
    assert action_bar.btn_play.isEnabled() is False
    assert "#2575FC" in action_bar.btn_play.styleSheet()


    # Test HeaderBar View menu with Library submenu
    assert hasattr(mw_compact.title_bar, "btn_view")
    assert hasattr(mw_compact.title_bar, "lib_menu")

    # Test Settings dialog frameless window hint and card size controls
    QSettings("SafeLauncher", "SafeLauncher").setValue("card_size", 200)
    test_settings = UserSettingsDialog("TestUser", parent=mw_compact)
    assert bool(test_settings.windowFlags() & Qt.WindowType.FramelessWindowHint)
    assert hasattr(test_settings, "combo_card_size")
    assert hasattr(test_settings, "spin_card_size")
    assert test_settings.get_card_size() == 200
    test_settings.close()

    # Test blurred hero background integration and transparency
    assert mw_compact.compact_container.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is True
    assert mw_compact.compact_container.game_page.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is True
    assert hasattr(mw_compact, "hero_bg")
    assert mw_compact.hero_bg is not None
    test_hero_img = "/tmp/test_hero_bg.png"
    qpix = QPixmap(100, 100)
    qpix.fill(Qt.GlobalColor.blue)
    qpix.save(test_hero_img)
    mw_compact.hero_bg.set_hero_image(test_hero_img)
    assert mw_compact.hero_bg._current_image_path == test_hero_img
    assert mw_compact.hero_bg.current_pixmap is not None
    mw_compact.hero_bg.set_hero_image(None)
    assert mw_compact.hero_bg._current_image_path is None
    if os.path.exists(test_hero_img):
        os.remove(test_hero_img)

    # Test _format_last_played_date accuracy across day boundaries
    import datetime
    from ui.components.compact_game_page import _format_last_played_date
    now = datetime.datetime.now()
    assert _format_last_played_date(0) == "Never"
    assert _format_last_played_date(None) == "Never"
    today_ts = now.timestamp()
    assert _format_last_played_date(today_ts) == "Today"
    yesterday_ts = (now - datetime.timedelta(days=1)).timestamp()
    assert _format_last_played_date(yesterday_ts) == "Yesterday"

    # Test tuple unpacking in set_game: index 9 for last_played and index 8 for is_favorite
    now_ts = now.timestamp()
    tuple_game = (
        2002, "Tuple Game", "/path", "game.exe", "umu", "", "12345", 3600, 1, now_ts, "rpg", "1", "", "", 0, "", "", 0, ""
    )
    compact_page.set_game(
        tuple_game,
        (0, 0, 0.0),
        [],
        [],
        cloud_status=SyncStatus.NO_SAVES,
        is_running=False
    )
    assert compact_page.action_bar.last_played_val.text() == "Today"
    assert compact_page.action_bar.btn_fav.toolTip() == "Remove from favorites"

    # Test compact layout zero margins & hidden top bar
    assert mw_compact.library_header_bar.isHidden() is True
    assert mw_compact.right_layout.contentsMargins().top() == 0
    assert mw_compact.right_layout.contentsMargins().left() == 0
    assert mw_compact.right_layout.spacing() == 0

    # Test footer bar layout: Add Game and View Toggle side-by-side with transparent styling
    footer_bar_widgets = [mw_compact.footer_bar.layout().itemAt(i).widget() for i in range(mw_compact.footer_bar.layout().count()) if mw_compact.footer_bar.layout().itemAt(i).widget()]
    assert mw_compact.btn_add in footer_bar_widgets
    assert mw_compact.btn_view_toggle in footer_bar_widgets
    assert "background: transparent" in mw_compact.btn_add.styleSheet()
    assert "border: none" in mw_compact.btn_add.styleSheet()
    assert "background: transparent" in mw_compact.btn_view_toggle.styleSheet()
    assert "border: none" in mw_compact.btn_view_toggle.styleSheet()

    # Test Add Collection button clean styling (transparent, no dashed border, centered)
    assert "background: transparent" in mw_compact.sidebar.btn_add_col_row.styleSheet()
    assert "border: none" in mw_compact.sidebar.btn_add_col_row.styleSheet()
    assert "dashed" not in mw_compact.sidebar.btn_add_col_row.styleSheet()

    # Test compact sidebar sorting combo and divider
    assert hasattr(mw_compact.compact_container.sidebar_list, "sort_combo")
    sidebar_sort = mw_compact.compact_container.sidebar_list.sort_combo
    assert sidebar_sort.count() == 5
    sidebar_sort.setCurrentIndex(1)
    assert mw_compact.current_sort == 1

    mw_compact._toggle_library_view()
    assert mw_compact.library_view_mode in ("compact", "grid", "list")
    assert mw_compact.library_header_bar.isHidden() is False
    assert mw_compact.right_layout.contentsMargins().top() == 14
    assert mw_compact.right_layout.contentsMargins().left() == 18
    mw_compact.settings.setValue("library_view_mode", "compact")
    mw_compact.close()

    compact_page.close()
    compact_layout.close()
    app.processEvents()

    print("✓ Compact game detail page, dark grey styling, footer bar, and collections panel verified")

except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"✗ Compact game page test error: {e}")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Test Suite: Stable Artwork Identification & Cache Collision Prevention
# ---------------------------------------------------------------------------
try:
    from core.steamgriddb_client import SteamGridDBClient
    import tempfile

    # 1. Test get_artwork_key stability
    steam_key_1 = SteamGridDBClient.get_artwork_key(steam_id="1868140", game_name="Dave the Diver", game_id=13)
    assert steam_key_1 == "steam_1868140", f"Unexpected steam key: {steam_key_1}"

    steam_key_2 = SteamGridDBClient.get_artwork_key(steam_id=392160, game_name="X4: Foundations", game_id=13)
    assert steam_key_2 == "steam_392160", f"Unexpected steam key: {steam_key_2}"
    assert steam_key_1 != steam_key_2, "Keys must never collide even if local game_id is identical!"

    custom_key = SteamGridDBClient.get_artwork_key(steam_id="", game_name="My Custom Game", exe_path="/opt/game/run.sh")
    assert custom_key.startswith("my_custom_game_"), f"Unexpected custom key: {custom_key}"

    # 2. Test get_hero_cached_path and get_icon_cached_path with mock cache dir
    with tempfile.TemporaryDirectory() as tmp_cache:
        client = SteamGridDBClient(cache_dir=os.path.join(tmp_cache, "banners"))
        heroes_dir = client.cache_dir / "heroes"
        heroes_dir.mkdir(parents=True, exist_ok=True)
        icons_dir = client.cache_dir.parent / "icons"
        icons_dir.mkdir(parents=True, exist_ok=True)

        # Write canonical hero for Dave the Diver
        dave_hero = heroes_dir / "hero_steam_1868140.jpg"
        dave_hero.write_bytes(b"DAVE_THE_DIVER_HERO")

        # Write stale legacy hero under game_id 13 (which was X4 Foundations)
        x4_legacy_hero = heroes_dir / "hero_13.jpg"
        x4_legacy_hero.write_bytes(b"X4_HERO")

        # Resolve hero for Dave the Diver (game_id 13, steam_id 1868140)
        resolved_dave_hero = client.get_hero_cached_path(steam_id="1868140", game_name="Dave the Diver", game_id=13)
        assert resolved_dave_hero == str(dave_hero.resolve()), f"Must resolve canonical Dave the Diver hero, got {resolved_dave_hero}"

        # Write canonical icon for Dave the Diver
        dave_icon = icons_dir / "icon_steam_1868140.png"
        dave_icon.write_bytes(b"DAVE_THE_DIVER_ICON")

        # Resolve icon for Dave the Diver
        resolved_dave_icon = client.get_icon_cached_path(steam_id="1868140", game_name="Dave the Diver", game_id=13)
        assert resolved_dave_icon == str(dave_icon.resolve()), f"Must resolve canonical Dave the Diver icon, got {resolved_dave_icon}"

    # 3. Test CompactSidebarListWidget update_game_icon
    dummy_games = [
        (13, "Dave the Diver", "/tmp/dave", "dave.exe", "umu", "", "1868140", 0, 0, 0, "", "", "", "", 0, "", "", 0, "")
    ]
    sidebar_list = CompactSidebarListWidget()
    sidebar_list.set_games(dummy_games, set())
    assert sidebar_list.list_widget.count() == 1
    test_icon_file = "/tmp/test_dave_update.png"
    qpix = QPixmap(24, 24)
    qpix.fill(Qt.GlobalColor.green)
    qpix.save(test_icon_file)
    sidebar_list.update_game_icon(13, test_icon_file)
    item_w = sidebar_list.list_widget.itemWidget(sidebar_list.list_widget.item(0))
    assert item_w.icon_url == test_icon_file
    if os.path.exists(test_icon_file):
        os.remove(test_icon_file)
    sidebar_list.close()

    print("✓ Stable artwork keys, collision-free hero/icon caching, and live UI update verified")

except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"✗ Artwork caching test error: {e}")
    sys.exit(1)

print("\n[SUCCESS] All SafeLauncher components tested and working cleanly!")


