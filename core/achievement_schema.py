"""
Steam Achievement Schema Fetcher and Badge Asset Cacher.
Retrieves achievement definitions (titles, descriptions, icons, secret flags)
using local game files, public Steam Community endpoints, and Steam Web API
with persistent caching.
"""

from __future__ import annotations

import os
import re
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from concurrent.futures import ThreadPoolExecutor

import requests
import requests.adapters
from PyQt6.QtCore import pyqtSignal

from core.safe_thread import SafeQThread
from core.logger import get_logger

logger = get_logger("AchievementSchema")

try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except ImportError:
    BeautifulSoup = None
    _HAS_BS4 = False

_CACHE_DIR = Path.home() / ".cache" / "safelauncher" / "achievements"
_ICONS_DIR = _CACHE_DIR / "icons"
_HTTP_SESSION: Optional[requests.Session] = None


def _get_http_session() -> requests.Session:
    global _HTTP_SESSION
    if _HTTP_SESSION is None:
        _HTTP_SESSION = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=30, pool_maxsize=30, max_retries=1)
        _HTTP_SESSION.mount("https://", adapter)
        _HTTP_SESSION.mount("http://", adapter)
    return _HTTP_SESSION


def _ensure_cache_dirs(app_id: str) -> Tuple[Path, Path]:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    app_icon_dir = _ICONS_DIR / str(app_id)
    app_icon_dir.mkdir(parents=True, exist_ok=True)
    schema_cache_file = _CACHE_DIR / f"{app_id}_schema.json"
    return schema_cache_file, app_icon_dir


def _download_icon(url: str, target_path: Path, timeout: float = 6.0) -> Optional[str]:
    """Download and cache an achievement badge icon image."""
    if not url or not url.startswith("http"):
        return ""
    if target_path.is_file() and target_path.stat().st_size > 0:
        return str(target_path)
    try:
        session = _get_http_session()
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
        resp = session.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200 and resp.content:
            target_path.write_bytes(resp.content)
            return str(target_path)
    except Exception as e:
        logger.debug(f"Failed to download achievement icon {url}: {e}")
    return ""


def download_achievement_icons_batch(
    achievements: List[Dict[str, Any]],
    app_id: str,
    max_workers: int = 10,
    timeout: float = 6.0
) -> List[Dict[str, Any]]:
    """Download achievement badges concurrently with a fast thread pool."""
    if not achievements or not app_id:
        return achievements

    schema_cache_file, app_icon_dir = _ensure_cache_dirs(app_id)
    session = _get_http_session()

    def _dl_one(item: Dict[str, Any]):
        api_name = item.get("api_name", "ACH")
        clean_name = re.sub(r"[^A-Za-z0-9_.-]", "_", api_name)

        icon_url = item.get("icon_url", "")
        if icon_url and icon_url.startswith("http"):
            target = app_icon_dir / f"{clean_name}_unlocked.png"
            if not target.is_file() or target.stat().st_size == 0:
                try:
                    resp = session.get(icon_url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}, timeout=timeout)
                    if resp.status_code == 200 and resp.content:
                        target.write_bytes(resp.content)
                except Exception:
                    pass
            if target.is_file() and target.stat().st_size > 0:
                item["icon_path"] = str(target)

        gray_url = item.get("icongray_url", "")
        if gray_url and gray_url.startswith("http"):
            target_gray = app_icon_dir / f"{clean_name}_locked.png"
            if not target_gray.is_file() or target_gray.stat().st_size == 0:
                try:
                    resp = session.get(gray_url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}, timeout=timeout)
                    if resp.status_code == 200 and resp.content:
                        target_gray.write_bytes(resp.content)
                except Exception:
                    pass
            if target_gray.is_file() and target_gray.stat().st_size > 0:
                item["icongray_path"] = str(target_gray)

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="SafeLauncher-IconDL") as executor:
        list(executor.map(_dl_one, achievements))

    return achievements


def find_local_achievement_schema(game_path: Optional[str] = None, proton_path: Optional[str] = None, app_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Scan game installation directory and prefix for offline Goldberg/CODEX achievement schema files."""
    candidate_dirs: List[Path] = []
    if game_path and os.path.isdir(game_path):
        candidate_dirs.append(Path(game_path))
        try:
            for entry in Path(game_path).iterdir():
                if entry.is_dir():
                    candidate_dirs.append(entry)
        except Exception:
            pass

    if proton_path and os.path.isdir(proton_path):
        candidate_dirs.append(Path(proton_path))

    for base_dir in candidate_dirs:
        for rel in [
            "steam_settings/achievements.json",
            "achievements.json",
            "steam_settings/achievements/achievements.json",
            "steam_settings/settings/achievements.json"
        ]:
            cand = base_dir / rel
            if cand.is_file():
                try:
                    data = json.loads(cand.read_text(encoding="utf-8"))
                    achs: List[Dict[str, Any]] = []
                    if isinstance(data, list):
                        for item in data:
                            name = str(item.get("name") or item.get("id") or "").strip()
                            if not name:
                                continue
                            achs.append({
                                "api_name": name,
                                "display_name": str(item.get("displayName") or item.get("name") or name).strip(),
                                "description": str(item.get("description") or "").strip(),
                                "icon_url": str(item.get("icon") or "").strip(),
                                "icongray_url": str(item.get("icon_gray") or item.get("icongray") or "").strip(),
                                "icon_path": str(base_dir / "steam_settings" / str(item.get("icon") or "")) if item.get("icon") and (base_dir / "steam_settings" / str(item.get("icon"))).is_file() else "",
                                "icongray_path": str(base_dir / "steam_settings" / str(item.get("icon_gray") or "")) if item.get("icon_gray") and (base_dir / "steam_settings" / str(item.get("icon_gray"))).is_file() else "",
                                "hidden": 1 if item.get("hidden") else 0,
                                "unlocked": 0,
                                "unlock_time": 0.0,
                            })
                    elif isinstance(data, dict):
                        for api_name, item in data.items():
                            if isinstance(item, dict):
                                achs.append({
                                    "api_name": str(api_name).strip(),
                                    "display_name": str(item.get("displayName") or item.get("name") or api_name).strip(),
                                    "description": str(item.get("description") or "").strip(),
                                    "icon_url": str(item.get("icon") or "").strip(),
                                    "icongray_url": str(item.get("icon_gray") or item.get("icongray") or "").strip(),
                                    "icon_path": str(base_dir / "steam_settings" / str(item.get("icon") or "")) if item.get("icon") and (base_dir / "steam_settings" / str(item.get("icon"))).is_file() else "",
                                    "icongray_path": str(base_dir / "steam_settings" / str(item.get("icon_gray") or "")) if item.get("icon_gray") and (base_dir / "steam_settings" / str(item.get("icon_gray"))).is_file() else "",
                                    "hidden": 1 if item.get("hidden") else 0,
                                    "unlocked": 0,
                                    "unlock_time": 0.0,
                                })
                    if achs:
                        logger.info(f"Found local offline achievement schema with {len(achs)} achievements at {cand}")
                        return achs
                except Exception as e:
                    logger.debug(f"Failed parsing local achievement schema {cand}: {e}")
    return []


def _fetch_steam_community_html(app_id: str, timeout: float = 8.0, app_icon_dir: Optional[Path] = None, download_icons: bool = False) -> List[Dict[str, Any]]:
    """Parse public Steam Community achievements page (no API key required)."""
    url = f"https://steamcommunity.com/stats/{app_id}/achievements/"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    session = _get_http_session()
    try:
        resp = session.get(url, headers=headers, timeout=timeout)
        if resp.status_code != 200 or not resp.content:
            return []
    except Exception as e:
        logger.debug(f"Steam Community HTML request failed for AppID {app_id}: {e}")
        return []

    achievements: List[Dict[str, Any]] = []

    # 1. Try BeautifulSoup if available
    if _HAS_BS4 and BeautifulSoup:
        try:
            soup = BeautifulSoup(resp.content, "html.parser")
            rows = soup.find_all("div", class_="achieveRow")
            for i, row in enumerate(rows):
                img = row.find("img")
                txt = row.find("div", class_="achieveTxt")
                h3 = txt.find("h3") if txt else None
                h5 = txt.find("h5") if txt else None

                display_name = h3.text.strip() if h3 else f"Achievement {i+1}"
                desc = h5.text.strip() if h5 else ""
                icon_url = img["src"].strip() if img and "src" in img.attrs else ""

                clean_api_name = re.sub(r"[^A-Za-z0-9_]", "_", display_name.upper().strip()).strip("_")
                if not clean_api_name:
                    clean_api_name = f"ACH_{i+1}"

                achievements.append({
                    "api_name": clean_api_name,
                    "display_name": display_name,
                    "description": desc,
                    "icon_url": icon_url,
                    "icongray_url": icon_url,
                    "icon_path": icon_url,
                    "icongray_path": icon_url,
                    "hidden": 0,
                    "unlocked": 0,
                    "unlock_time": 0.0,
                })
        except Exception as e:
            logger.debug(f"BeautifulSoup parsing failed for AppID {app_id}: {e}")

    # 2. Fallback regex parser
    if not achievements:
        try:
            matches = re.findall(
                r'<div class="achieveImgHolder">\s*<img[^>]*src="([^"]+)"[^>]*>\s*</div>\s*<div class="achieveTxtHolder">.*?<div class="achieveTxt">\s*<h3>(.*?)</h3>\s*<h5>(.*?)</h5>',
                resp.text,
                re.DOTALL
            )
            for i, (icon_url, d_name, d_desc) in enumerate(matches):
                display_name = re.sub(r"<[^>]+>", "", d_name).strip()
                desc = re.sub(r"<[^>]+>", "", d_desc).strip()
                clean_api_name = re.sub(r"[^A-Za-z0-9_]", "_", display_name.upper().strip()).strip("_")
                if not clean_api_name:
                    clean_api_name = f"ACH_{i+1}"

                achievements.append({
                    "api_name": clean_api_name,
                    "display_name": display_name,
                    "description": desc,
                    "icon_url": icon_url,
                    "icongray_url": icon_url,
                    "icon_path": icon_url,
                    "icongray_path": icon_url,
                    "hidden": 0,
                    "unlocked": 0,
                    "unlock_time": 0.0,
                })
        except Exception as e:
            logger.debug(f"Regex HTML parsing failed for AppID {app_id}: {e}")

    if download_icons and achievements:
        download_achievement_icons_batch(achievements, app_id, timeout=timeout)

    return achievements


def fetch_steam_achievements_schema(
    app_id: str,
    game_path: Optional[str] = None,
    proton_path: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: float = 8.0,
    download_icons: bool = False
) -> List[Dict[str, Any]]:
    """
    Fetch all achievement definitions for a given Steam AppID in sub-second time.
    Tries:
    1. Local on-disk cache (~/.cache/safelauncher/achievements/<app_id>_schema.json)
    2. Local game files (steam_settings/achievements.json)
    3. Official Steam Web API (if api_key provided)
    4. Public Steam Community HTML Scraper (keyless)
    5. Public Steam Community XML endpoint
    """
    if not app_id or str(app_id).strip() == "" or str(app_id).strip() == "0":
        return []

    app_id = str(app_id).strip()
    schema_cache_file, app_icon_dir = _ensure_cache_dirs(app_id)

    # 1. Check local cache first (< 0.1ms)
    if schema_cache_file.is_file():
        try:
            cached_data = json.loads(schema_cache_file.read_text(encoding="utf-8"))
            if isinstance(cached_data, list) and len(cached_data) > 0:
                logger.debug(f"Loaded {len(cached_data)} achievements from cache for AppID {app_id}")
                if download_icons:
                    download_achievement_icons_batch(cached_data, app_id, timeout=timeout)
                return cached_data
        except Exception as e:
            logger.debug(f"Could not parse cached achievement schema for {app_id}: {e}")

    # 2. Check local offline game schema files (< 1ms)
    local_achs = find_local_achievement_schema(game_path, proton_path, app_id)
    if local_achs:
        try:
            schema_cache_file.write_text(json.dumps(local_achs, indent=2), encoding="utf-8")
        except Exception:
            pass
        return local_achs

    achievements: List[Dict[str, Any]] = []
    session = _get_http_session()

    # 3. Try official Steam Web API (if API key available)
    if api_key and str(api_key).strip():
        try:
            web_url = f"https://api.steampowered.com/ISteamUserStats/GetSchemaForGame/v2/?key={api_key.strip()}&appid={app_id}"
            headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
            resp = session.get(web_url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                stats = data.get("game", {}).get("availableGameStats", {})
                raw_achs = stats.get("achievements", [])
                for raw in raw_achs:
                    api_name = str(raw.get("name") or "").strip()
                    name = str(raw.get("displayName") or api_name).strip()
                    desc = str(raw.get("description") or "").strip()
                    icon_url = str(raw.get("icon") or "").strip()
                    icongray_url = str(raw.get("icongray") or icon_url).strip()
                    hidden = int(raw.get("hidden") or 0)

                    achievements.append({
                        "api_name": api_name,
                        "display_name": name,
                        "description": desc,
                        "icon_url": icon_url,
                        "icongray_url": icongray_url,
                        "icon_path": icon_url,
                        "icongray_path": icongray_url,
                        "hidden": hidden,
                        "unlocked": 0,
                        "unlock_time": 0.0,
                    })
        except Exception as e:
            logger.debug(f"Steam Web API achievement fetch failed for AppID {app_id}: {e}")

    # 4. Try public Steam Community HTML scraper (100% keyless, public, 1 single HTTP request)
    if not achievements:
        achievements = _fetch_steam_community_html(app_id, timeout=timeout, app_icon_dir=app_icon_dir, download_icons=False)

    # 5. Try Steam Community XML stats endpoint
    if not achievements:
        try:
            xml_url = f"https://steamcommunity.com/stats/{app_id}/achievements/?xml=1"
            headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
            resp = session.get(xml_url, headers=headers, timeout=timeout)
            if resp.status_code == 200 and resp.content and b"<achievement" in resp.content:
                root = ET.fromstring(resp.content)
                ach_nodes = root.findall(".//achievement")
                for node in ach_nodes:
                    api_name = (node.findtext("apiname") or "").strip()
                    name = (node.findtext("name") or api_name).strip()
                    desc = (node.findtext("description") or "").strip()
                    icon_url = (node.findtext("iconClosed") or "").strip()
                    icongray_url = (node.findtext("iconOpen") or "").strip()
                    if not icongray_url:
                        icongray_url = icon_url
                    hidden = int(node.get("hidden", "0"))

                    achievements.append({
                        "api_name": api_name,
                        "display_name": name,
                        "description": desc,
                        "icon_url": icon_url,
                        "icongray_url": icongray_url,
                        "icon_path": icon_url,
                        "icongray_path": icongray_url,
                        "hidden": hidden,
                        "unlocked": 0,
                        "unlock_time": 0.0,
                    })
        except Exception as e:
            logger.debug(f"Steam Community XML achievement fetch failed for AppID {app_id}: {e}")

    # If icons were explicitly requested, download in parallel
    if download_icons and achievements:
        download_achievement_icons_batch(achievements, app_id, timeout=timeout)

    # Save to disk cache if fetched successfully
    if achievements:
        try:
            schema_cache_file.write_text(json.dumps(achievements, indent=2), encoding="utf-8")
            logger.info(f"Successfully fetched and cached {len(achievements)} achievements for AppID {app_id}")
        except Exception as e:
            logger.warning(f"Could not write achievement schema cache: {e}")

    return achievements


class SteamAchievementFetcherWorker(SafeQThread):
    """Background worker thread to fetch and cache achievements without freezing UI."""
    schema_fetched = pyqtSignal(int, str, list)  # game_id, app_id, achievements_list
    failed = pyqtSignal(int, str, str)           # game_id, app_id, error_message

    def __init__(
        self,
        game_id: int,
        app_id: str,
        game_path: Optional[str] = None,
        proton_path: Optional[str] = None,
        api_key: Optional[str] = None,
        download_icons: bool = False,
        parent=None
    ):
        super().__init__(parent)
        self.game_id = game_id
        self.app_id = str(app_id).strip()
        self.game_path = game_path
        self.proton_path = proton_path
        self.api_key = api_key
        self.download_icons = download_icons

    def safe_run(self):
        try:
            if not self.app_id:
                self.failed.emit(self.game_id, "", "No AppID provided")
                return
            achs = fetch_steam_achievements_schema(
                self.app_id,
                game_path=self.game_path,
                proton_path=self.proton_path,
                api_key=self.api_key,
                download_icons=self.download_icons
            )
            if self.isInterruptionRequested():
                return
            if achs:
                self.schema_fetched.emit(self.game_id, self.app_id, achs)
            else:
                self.failed.emit(self.game_id, self.app_id, "No achievements found for this game")
        except Exception as e:
            logger.warning(f"Achievement worker error for AppID {self.app_id}: {e}")
            self.failed.emit(self.game_id, self.app_id, str(e))
