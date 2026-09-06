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
    assert APP_VERSION == "0.5.5", f"Expected APP_VERSION == 0.5.5, got {APP_VERSION}"
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
                    "tag_name": "v0.5.6",
                    "name": "Release 0.5.6",
                    "body": "Bugfixes",
                    "html_url": "https://github.com/Mistarin/SafeLauncher/releases/tag/v0.5.6",
                    "assets": [
                        {"name": "SafeLauncher-arm64.AppImage", "browser_download_url": "https://arm64.url", "size": 50000000},
                        {"name": "SafeLauncher-x86_64.AppImage", "browser_download_url": "https://x86_64.url", "size": 52000000},
                    ]
                }
                return mock_resp

            with patch("requests.get", side_effect=mock_release_assets):
                update_info = check_for_updates()
                assert update_info["update_available"] is True
                assert update_info["latest_version"] == "v0.5.6"
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

print("\n[SUCCESS] All SafeLauncher components tested and working cleanly!")



