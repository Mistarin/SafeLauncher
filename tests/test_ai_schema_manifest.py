import importlib.util
from pathlib import Path
import unittest


class SchemaManifestTests(unittest.TestCase):
    def test_relocated_migrations_remain_in_source_navigation_manifest(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('ai_manifest', root / '.ai/tools/build_manifest.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        path = root / 'core/local_database/schema.py'
        schema = module.database_schema(path, path.read_text())
        tables = {item['table'] for item in schema if 'columns' in item}
        self.assertEqual(tables, {'games', 'collections', 'achievements', 'achievement_profile', 'profile_games', 'playtime_sessions'})
        self.assertTrue(all(item['file'] == 'core/local_database/schema.py' for item in schema))
