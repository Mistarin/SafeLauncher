from dataclasses import dataclass
from typing import Optional
from core.game_names import local_profile_identity

GAME_COLUMNS = (
    "id, name, path, executable, mode, banner_url, steam_id, "
    "playtime_seconds, is_favorite, last_played, tags, build_id"
    ", proton_path, collection, install_date"
    ", version_override, patch_notes_url"
    ", is_archived, icon_url, env_vars, build_date"
)


@dataclass
class GameRecord:
    id: int
    name: str
    path: str
    executable: str
    mode: str
    banner_url: Optional[str] = ""
    steam_id: Optional[str] = ""
    playtime_seconds: int = 0
    is_favorite: int = 0
    last_played: int = 0
    tags: str = ""
    build_id: str = ""
    proton_path: str = ""
    collection: str = ""
    install_date: int = 0
    version_override: str = ""
    patch_notes_url: str = ""
    is_archived: int = 0
    icon_url: str = ""
    env_vars: str = "{}"
    build_date: int = 0

    def __getitem__(self, idx):
        fields = (
            self.id, self.name, self.path, self.executable, self.mode,
            self.banner_url or "", self.steam_id or "", self.playtime_seconds,
            self.is_favorite, self.last_played, self.tags, self.build_id,
            self.proton_path, self.collection, self.install_date,
            self.version_override, self.patch_notes_url,
            self.is_archived, self.icon_url, self.env_vars or "{}", self.build_date
        )
        return fields[idx]

    def __len__(self):
        return 21

    def __hash__(self):
        return hash(self.id)

    def __iter__(self):
        return iter((
            self.id, self.name, self.path, self.executable, self.mode,
            self.banner_url or "", self.steam_id or "", self.playtime_seconds,
            self.is_favorite, self.last_played, self.tags, self.build_id,
            self.proton_path, self.collection, self.install_date,
            self.version_override, self.patch_notes_url,
            self.is_archived, self.icon_url, self.env_vars or "{}", self.build_date
        ))


def profile_identity(name: str, app_id: str = "") -> str:
    """Return the portable account-profile identity for a game."""
    sid = str(app_id or "").strip()
    if sid and sid not in ("0", "None"):
        return f"steam:{sid}"
    # A prior cloud-only fallback could persist the identity itself as
    # the title (``local:dub-together``), or repeatedly prefix it while
    # rematerializing the same record.  Always collapse those spellings
    # to one stable local identity.
    return local_profile_identity(name)
