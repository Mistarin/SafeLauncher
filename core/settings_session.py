"""Cancel baseline for explicitly mutable cloud preferences and credentials."""


class CloudSettingsSession:
    KEYS = {
        "cloud_mode": ("local", str), "convex_site_url": ("", str),
        "cloud_saves_dir": ("", str), "cloud_device_name": ("", str),
        "cloud_sync_workers": (3, int),
    }
    SECRETS = ("cloud_secret_key", "convex_deploy_key")

    def __init__(self, settings, account, *, get_secret, set_secret, delete_secret):
        self.settings, self.account = settings, account
        self.get_secret, self.set_secret, self.delete_secret = get_secret, set_secret, delete_secret
        self.committed = False
        self.baseline = self.capture()

    def capture(self):
        values = {key: self.settings.value(key, default, type=kind)
                  for key, (default, kind) in self.KEYS.items()}
        values.update({key: self.get_secret(key, legacy_name=key) for key in self.SECRETS})
        values["service_mode"] = self.account.mode()
        return values

    def remember(self):
        self.baseline = self.capture()

    def rollback(self):
        if self.committed:
            return
        for key in self.KEYS:
            self.settings.setValue(key, self.baseline[key])
        for key in self.SECRETS:
            value = self.baseline[key]
            if value:
                self.set_secret(key, value)
            else:
                self.delete_secret(key)
        self.account.reset_backend()
        self.account.set_mode(self.baseline["service_mode"] or "local")
