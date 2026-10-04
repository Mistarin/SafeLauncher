import unittest

from ci.architecture_audit import inspect_source


class ArchitectureAuditTests(unittest.TestCase):
    def test_window_cannot_construct_second_request_manager(self):
        source = "class MainWindow:\n    def __init__(self):\n        self.manager = RequestManager()\n"
        self.assertTrue(inspect_source("ui/main_window.py", source))

    def test_explicit_runtime_injection_is_allowed(self):
        source = "class MainWindow:\n    def __init__(self, runtime):\n        self.manager = runtime.request_manager\n"
        self.assertEqual(inspect_source("ui/main_window.py", source), [])

    def test_entrypoint_cannot_reach_private_window_api(self):
        self.assertTrue(inspect_source("main.py", "window._start_managed_task()"))
        self.assertEqual(inspect_source("main.py", "window.start_background_task()"), [])

    def test_controller_cannot_import_direct_sql_or_transport(self):
        self.assertTrue(inspect_source("ui/cloud_workflow_controller.py", "import requests\nimport sqlite3"))
        self.assertTrue(inspect_source("ui/profile_controller.py", "from httpx import Client"))

    def test_window_cannot_reintroduce_workflow_mixin(self):
        source = "class MainWindow(ProfileMixin):\n    def __init__(self):\n        pass\n"
        self.assertTrue(inspect_source("ui/main_window.py", source))

    def test_database_facade_cannot_reintroduce_sql(self):
        self.assertTrue(inspect_source("database.py", "conn.execute('SELECT 1')"))
        self.assertEqual(inspect_source("database.py", "repository.get_all_games()"), [])

    def test_repository_cannot_import_qt_or_transport(self):
        self.assertTrue(inspect_source("core/local_database/games.py", "from PyQt6.QtWidgets import QWidget"))
        self.assertTrue(inspect_source("core/local_database/profiles.py", "from requests import Session"))
