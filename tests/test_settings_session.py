import unittest
from unittest.mock import Mock
from core.settings_session import CloudSettingsSession


class _Settings:
    def __init__(self):
        self.values = {'cloud_mode': 'convex', 'cloud_device_name': 'Desktop'}

    def value(self, key, default=None, type=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


class SettingsSessionTests(unittest.TestCase):
    def make_session(self):
        settings = _Settings()
        account = Mock()
        account.mode.return_value = 'convex'
        secrets = {'cloud_secret_key': 'fixture-only-key'}
        session = CloudSettingsSession(settings, account,
            get_secret=lambda key, **kwargs: secrets.get(key, ''),
            set_secret=lambda key, value: secrets.__setitem__(key, value),
            delete_secret=lambda key: secrets.pop(key, None))
        return session, settings, account, secrets

    def test_cancel_restores_only_scoped_preferences_and_secrets(self):
        session, settings, account, secrets = self.make_session()
        settings.setValue('cloud_mode', 'local')
        settings.setValue('unrelated', 'keep')
        secrets['cloud_secret_key'] = 'changed-fixture'
        secrets['convex_deploy_key'] = 'new-fixture'
        session.rollback()
        self.assertEqual(settings.values['cloud_mode'], 'convex')
        self.assertEqual(settings.values['unrelated'], 'keep')
        self.assertEqual(secrets, {'cloud_secret_key': 'fixture-only-key'})
        account.set_mode.assert_called_once_with('convex')

    def test_explicit_nested_accept_is_new_cancel_baseline(self):
        session, settings, account, secrets = self.make_session()
        settings.setValue('cloud_device_name', 'Laptop')
        session.remember()
        settings.setValue('cloud_device_name', 'Wrong')
        session.rollback()
        self.assertEqual(settings.values['cloud_device_name'], 'Laptop')

    def test_committed_session_does_not_rollback(self):
        session, settings, account, secrets = self.make_session()
        session.committed = True
        settings.setValue('cloud_mode', 'local')
        session.rollback()
        self.assertEqual(settings.values['cloud_mode'], 'local')
        account.reset_backend.assert_not_called()
