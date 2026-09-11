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
import threading
import time
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from concurrent.futures import ThreadPoolExecutor

import requests
import requests.adapters
from PyQt6.QtCore import pyqtSignal

from core.safe_thread import SafeQThread
from core.logger import get_logger
from core.network_policy import automatic_network_allowed

logger = get_logger("AchievementSchema")


class TokenBucketRateLimiter:
    """Thread-safe token-bucket rate limiter to prevent HTTP 429 Too Many Requests."""

    def __init__(self, rate: float = 3.5, capacity: float = 7.0):
        self.rate = float(rate)          # Tokens replenished per second
        self.capacity = float(capacity)  # Maximum burst capacity
        self.tokens = float(capacity)    # Initial token balance
        self.last_update = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0, timeout: float = 10.0) -> bool:
        """Acquire tokens, blocking smoothly if necessary until timeout expires."""
        start = time.monotonic()
        while True:
            with self._lock:
                now = time.monotonic()
                if now > self.last_update:
                    elapsed = now - self.last_update
                    self.last_update = now
                    self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)

                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return True

                needed = tokens - self.tokens
                cooldown_remaining = max(0.0, self.last_update - now)
                wait_time = cooldown_remaining + (needed / self.rate)

            remaining = timeout - (time.monotonic() - start)
            if wait_time > remaining or remaining <= 0:
                return False

            time.sleep(min(wait_time, remaining, 0.25))

    def penalize(self, cooldown_seconds: float = 5.0) -> None:
        """Drain tokens and shift the refill clock forward on a 429 / rate-limit signal.

        Thread-safety: protected by ``_lock``; safe to call from any thread.

        Implementation note: rather than sleeping here, we shift ``last_update``
        forward by ``cooldown_seconds``.  The next ``acquire()`` call computes
        the elapsed time as negative (``now < last_update``), so no tokens are
        refilled until the wall clock catches up.  This means ``penalize()``
        returns immediately without blocking the calling thread — the cooldown
        is served lazily inside ``acquire()``'s retry loop.
        """
        with self._lock:
            self.tokens = 0.0
            self.last_update = time.monotonic() + float(cooldown_seconds)


_COMMUNITY_RATE_LIMITER = TokenBucketRateLimiter(rate=3.5, capacity=7.0)

try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except ImportError:
    BeautifulSoup = None
    _HAS_BS4 = False

_CACHE_DIR = Path.home() / ".cache" / "safelauncher" / "achievements"
_ICONS_DIR = _CACHE_DIR / "icons"
_HTTP_SESSION: Optional[requests.Session] = None
_SCHEMA_API_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SCHEMA_APP_RE = re.compile(r"^[0-9]{1,16}$")
_MAX_SCHEMA_BYTES = 8 * 1024 * 1024
_MAX_SCHEMA_RECORDS = 20_000


def close_achievement_http_session() -> None:
    """Release the process-wide schema/icon HTTP pool."""
    global _HTTP_SESSION
    session = _HTTP_SESSION
    _HTTP_SESSION = None
    if session is not None:
        try:
            session.close()
        except Exception:
            pass



def _validated_schema(records: Any) -> List[Dict[str, Any]]:
    """Keep only bounded, renderable achievement definitions."""
    clean: List[Dict[str, Any]] = []
    seen = set()
    if not isinstance(records, list):
        return clean
    for item in records:
        if not isinstance(item, dict):
            continue
        name = str(item.get("api_name") or item.get("name") or item.get("id") or "").strip()
        if not _SCHEMA_API_RE.fullmatch(name) or name in seen:
            continue
        seen.add(name)
        value = dict(item)
        value["api_name"] = name
        value["display_name"] = str(value.get("display_name") or value.get("displayName") or name).strip()[:512]
        value["description"] = str(value.get("description") or "").strip()[:4000]
        value["hidden"] = int(bool(value.get("hidden", 0)))
        clean.append(value)
        if len(clean) >= _MAX_SCHEMA_RECORDS:
            break
    return clean


def _write_schema_cache(path: Path, records: List[Dict[str, Any]]) -> None:
    """Atomically replace a schema cache so a killed worker cannot corrupt it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".schema-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(records, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass


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
    resp = None
    try:
        session = _get_http_session()
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
        resp = session.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200 and resp.content:
            target_path.write_bytes(resp.content)
            return str(target_path)
    except Exception as e:
        logger.debug(f"Failed to download achievement icon {url}: {e}")
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
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
    if not automatic_network_allowed():
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
                resp = None
                try:
                    resp = session.get(icon_url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}, timeout=timeout)
                    if resp.status_code == 200 and resp.content:
                        target.write_bytes(resp.content)
                except Exception:
                    pass
                finally:
                    if resp is not None:
                        try:
                            resp.close()
                        except Exception:
                            pass
            if target.is_file() and target.stat().st_size > 0:
                item["icon_path"] = str(target)

        gray_url = item.get("icongray_url", "")
        if gray_url and gray_url.startswith("http"):
            target_gray = app_icon_dir / f"{clean_name}_locked.png"
            if not target_gray.is_file() or target_gray.stat().st_size == 0:
                resp = None
                try:
                    resp = session.get(gray_url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}, timeout=timeout)
                    if resp.status_code == 200 and resp.content:
                        target_gray.write_bytes(resp.content)
                except Exception:
                    pass
                finally:
                    if resp is not None:
                        try:
                            resp.close()
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
        game_prefix = Path(game_path) / "prefix"
        if game_prefix.is_dir():
            candidate_dirs.append(game_prefix)
            if (game_prefix / "pfx").is_dir():
                candidate_dirs.append(game_prefix / "pfx")
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
                    if cand.stat().st_size > _MAX_SCHEMA_BYTES:
                        continue
                    data = json.loads(cand.read_text(encoding="utf-8"))
                    achs: List[Dict[str, Any]] = []
                    seen = set()

                    def append_item(name, item):
                        if len(achs) >= _MAX_SCHEMA_RECORDS or not isinstance(item, dict):
                            return
                        api_name = str(name or "").strip()
                        if not _SCHEMA_API_RE.fullmatch(api_name) or api_name in seen:
                            return
                        seen.add(api_name)
                        icon = str(item.get("icon") or item.get("icon_url") or "").strip()
                        gray = str(item.get("icon_gray") or item.get("icongray") or item.get("icongray_url") or "").strip()
                        achs.append({
                            "api_name": api_name,
                            "display_name": str(item.get("displayName") or item.get("display_name") or item.get("name") or api_name).strip()[:512],
                            "description": str(item.get("description") or "").strip()[:4000],
                            "icon_url": icon,
                            "icongray_url": gray,
                            "icon_path": str(base_dir / "steam_settings" / icon) if icon and (base_dir / "steam_settings" / icon).is_file() else "",
                            "icongray_path": str(base_dir / "steam_settings" / gray) if gray and (base_dir / "steam_settings" / gray).is_file() else "",
                            "hidden": 1 if item.get("hidden") else 0,
                            "unlocked": 0,
                            "unlock_time": 0.0,
                        })
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                append_item(item.get("api_name") or item.get("apiname") or item.get("name") or item.get("id"), item)
                    elif isinstance(data, dict):
                        items = data.get("achievements") if isinstance(data.get("achievements"), (dict, list)) else data
                        if isinstance(items, list):
                            for item in items:
                                if isinstance(item, dict):
                                    append_item(item.get("api_name") or item.get("apiname") or item.get("name") or item.get("id"), item)
                        elif isinstance(items, dict):
                            for api_name, item in items.items():
                                append_item(api_name, item)
                    if achs:
                        logger.info(f"Found local offline achievement schema with {len(achs)} achievements at {cand}")
                        return achs
                except Exception as e:
                    logger.debug(f"Failed parsing local achievement schema {cand}: {e}")
    return []


def _fetch_steam_community_html(app_id: str, timeout: float = 8.0, app_icon_dir: Optional[Path] = None, download_icons: bool = False) -> List[Dict[str, Any]]:
    """Parse public Steam Community achievements page (no API key required)."""
    if not _COMMUNITY_RATE_LIMITER.acquire(1.0, timeout=timeout):
        logger.warning(f"Steam Community rate limiter capacity exceeded for AppID {app_id}.")
        return []

    url = f"https://steamcommunity.com/stats/{app_id}/achievements/"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    session = _get_http_session()
    resp = None
    try:
        resp = session.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 429:
            logger.warning(f"Steam Community rate limit hit (HTTP 429) for AppID {app_id}. Throttling for 10s cooldown.")
            _COMMUNITY_RATE_LIMITER.penalize(10.0)
            return []
        if resp.status_code != 200 or not resp.content:
            return []
        # requests releases a non-streamed response after consuming it, but
        # explicitly close it before CPU-heavy parsing so the shared session
        # does not retain a connection while BeautifulSoup/regex work runs.
        response_content = resp.content
        response_text = resp.text
    except Exception as e:
        logger.debug(f"Steam Community HTML request failed for AppID {app_id}: {e}")
        return []
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

    achievements: List[Dict[str, Any]] = []

    # 1. Try BeautifulSoup if available
    if _HAS_BS4 and BeautifulSoup:
        try:
            soup = BeautifulSoup(response_content, "html.parser")
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
                response_text,
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
    if not _SCHEMA_APP_RE.fullmatch(str(app_id or "").strip()) or str(app_id).strip() == "0":
        return []

    app_id = str(app_id).strip()
    schema_cache_file, app_icon_dir = _ensure_cache_dirs(app_id)

    # 1. Check local cache first (< 0.1ms)
    if schema_cache_file.is_file():
        try:
            if schema_cache_file.stat().st_size > _MAX_SCHEMA_BYTES:
                raise ValueError("cached schema exceeds safety limit")
            cached_data = _validated_schema(json.loads(schema_cache_file.read_text(encoding="utf-8")))
            if cached_data:
                logger.debug(f"Loaded {len(cached_data)} achievements from cache for AppID {app_id}")
                if download_icons and automatic_network_allowed():
                    download_achievement_icons_batch(cached_data, app_id, timeout=timeout)
                return cached_data
        except Exception as e:
            logger.debug(f"Could not parse cached achievement schema for {app_id}: {e}")

    # 2. Check local offline game schema files (< 1ms)
    local_achs = find_local_achievement_schema(game_path, proton_path, app_id)
    if local_achs:
        try:
            _write_schema_cache(schema_cache_file, _validated_schema(local_achs))
        except Exception:
            pass
        return _validated_schema(local_achs)

    # The local cache and game files above remain useful offline. Once those
    # are exhausted, do not enter the public Steam fallback chain at all.
    if not automatic_network_allowed():
        return []

    achievements: List[Dict[str, Any]] = []
    session = _get_http_session()

    # 3. Try official Steam Web API (if API key available)
    if api_key and str(api_key).strip():
        resp = None
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
            # Never include the request URL in diagnostics: older Steam API
            # callers supplied the Web API key as a query parameter and some
            # requests exceptions echo the prepared URL.
            logger.debug(
                "Steam Web API achievement fetch failed for AppID %s: %s",
                app_id,
                type(e).__name__,
            )
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass

    # 4. Try public Steam Community HTML scraper (100% keyless, public, 1 single HTTP request)
    if not achievements:
        achievements = _fetch_steam_community_html(app_id, timeout=timeout, app_icon_dir=app_icon_dir, download_icons=False)

    # 5. Try Steam Community XML stats endpoint
    if not achievements:
        resp = None
        try:
            if _COMMUNITY_RATE_LIMITER.acquire(1.0, timeout=timeout):
                xml_url = f"https://steamcommunity.com/stats/{app_id}/achievements/?xml=1"
                headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
                resp = session.get(xml_url, headers=headers, timeout=timeout)
                if resp.status_code == 429:
                    logger.warning(f"Steam Community XML rate limit reached (HTTP 429) for AppID {app_id}.")
                elif resp.status_code == 200 and resp.content and b"<achievement" in resp.content:
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
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass

    # If icons were explicitly requested, download in parallel
    achievements = _validated_schema(achievements)
    if download_icons and achievements:
        download_achievement_icons_batch(achievements, app_id, timeout=timeout)

    # Save to disk cache if fetched successfully
    if achievements:
        try:
            _write_schema_cache(schema_cache_file, achievements)
            logger.info(f"Successfully fetched and cached {len(achievements)} achievements for AppID {app_id}")
        except Exception as e:
            logger.warning(f"Could not write achievement schema cache: {e}")

    return _validated_schema(achievements)


class SteamAchievementFetcherWorker(SafeQThread):
    """Background worker thread to fetch and cache achievements without freezing UI."""
    schema_fetched = pyqtSignal(int, str, list)  # game_id, app_id, achievements_list
    resolution_ready = pyqtSignal(int, str, object)  # game_id, app_id, AchievementResolution
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
            # Keep all local-state, authenticated-Steam, schema-cache, and
            # public-schema precedence in the registry.  This worker is only
            # transport/lifecycle glue; callers must not implement their own
            # achievement interpretation beside it.
            from core.achievement_coordinator import coordinated_resolve
            resolution = coordinated_resolve(
                self.app_id,
                self.game_path or "",
                self.proton_path or "",
                download_icons=self.download_icons,
            )
            if self.isInterruptionRequested():
                return
            self.resolution_ready.emit(self.game_id, self.app_id, resolution)
            if resolution.schema:
                self.schema_fetched.emit(self.game_id, self.app_id, resolution.schema)
            elif resolution.state:
                self.failed.emit(self.game_id, self.app_id, resolution.reason or "Achievement schema unavailable; local state was read")
            else:
                self.failed.emit(self.game_id, self.app_id, resolution.reason or "Achievement data unavailable")
        except Exception as e:
            logger.warning(f"Achievement worker error for AppID {self.app_id}: {e}")
            self.failed.emit(self.game_id, self.app_id, str(e))
