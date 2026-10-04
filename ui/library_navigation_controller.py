"""One layout transition authority for library, detail and profile surfaces."""
from dataclasses import dataclass
from PyQt6.QtCore import Qt


@dataclass(frozen=True)
class LibraryNavigationViews:
    host: object
    scroll: object
    header: object
    collection: object
    inspector: object
    reveal: object
    sidebar: object
    footer: object
    profile: object
    layout: object


class LibraryNavigationController:
    def __init__(self, views, *, mode, virtual, collection_active, update_detail,
                 update_compact, show_inspector, has_selection):
        self.views = views
        self.mode, self.virtual, self.collection_active = mode, virtual, collection_active
        self.update_detail, self.update_compact = update_detail, update_compact
        self.show_inspector, self.has_selection = show_inspector, has_selection
        self.profile_active = False
        self._sidebar_visible, self._footer_visible = True, True

    def library(self):
        if self.profile_active:
            return
        v = self.views
        compact = self.mode() in {"compact", "steam"}
        v.host.set_mode("compact" if compact else "grid", use_virtual=not compact and self.virtual())
        v.scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff if compact else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        v.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        v.header.setVisible(not compact)
        v.collection.setVisible(not compact and self.collection_active())
        v.layout.setContentsMargins(*(0, 0, 0, 0) if compact else (18, 14, 18, 14))
        v.layout.setSpacing(0 if compact else 12)
        if compact:
            v.inspector.hide()
            v.reveal.hide()
            self.update_compact()

    def detail(self):
        if self.profile_active:
            return
        v = self.views
        v.header.hide()
        v.collection.hide()
        v.host.set_mode("detail")
        v.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def show_profile(self):
        v = self.views
        if self.profile_active:
            v.profile.show_owner()
            return
        self.profile_active = True
        self._sidebar_visible, self._footer_visible = not v.sidebar.isHidden(), not v.footer.isHidden()
        for widget in (v.header, v.collection, v.scroll, v.inspector, v.reveal, v.sidebar, v.footer):
            widget.hide()
        v.layout.setContentsMargins(0, 0, 0, 0)
        v.layout.setSpacing(0)
        v.profile.show_owner()
        v.profile.show()

    def close_profile(self):
        if not self.profile_active:
            return
        self.profile_active = False
        v = self.views
        v.profile.hide()
        v.scroll.show()
        v.sidebar.setVisible(self._sidebar_visible)
        v.footer.setVisible(self._footer_visible)
        self.library()
        if self.mode() not in {"compact", "steam"}:
            if self.has_selection():
                self.show_inspector(True)
            else:
                v.inspector.hide()
                v.reveal.show()
        self.update_detail()
