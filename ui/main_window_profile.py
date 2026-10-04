"""Deprecated profile mixin adapter for older embedding integrations.

Production MainWindow composes ProfileController and does not inherit this
class. This adapter contains no workflow state, scheduling, or transports.
"""


class MainWindowProfileMixin:
    def _open_public_profile_prompt(self, *args, **kwargs):
        return self.profile_controller.open_friends(focus_find=True)

    def _open_friends_popup(self, *args, **kwargs):
        return self.profile_controller.open_friends(*args, **kwargs)

    def _open_public_profile_handle(self, *args, **kwargs):
        return self.profile_controller.open_public(*args, **kwargs)

    def _on_profile_changed(self, *args, **kwargs):
        return self.profile_controller.profile_changed()

    def _on_private_profile_changed(self, *args, **kwargs):
        return self.profile_controller.profile_changed(private_only=True)

    def _sync_profile_metadata_async(self, *args, **kwargs):
        return self.profile_controller.sync_private()


__all__ = ["MainWindowProfileMixin"]
