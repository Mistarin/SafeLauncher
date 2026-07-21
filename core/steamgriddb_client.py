import os
import shutil
import requests
import hashlib
from pathlib import Path
from typing import Optional, List, Dict

# [M2 FIX] XDG-compliant cache directory: ~/.cache/mglauncher/banners/
# Owner-only permissions (700 on dir, 600 on files) to prevent other local users
# from reading cached cover art files.
_XDG_CACHE_HOME = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
_DEFAULT_CACHE_DIR = os.path.join(_XDG_CACHE_HOME, "mglauncher", "banners")
_LEGACY_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".banner_cache")


class SteamGridDBClient:
    """Fetches game banners using Steam Store API (primary) and optional API keys if provided."""

    STEAM_STORE_API = "https://store.steampowered.com/api/storesearch"
    RAWG_API = "https://api.rawg.io/api"

    def __init__(self, cache_dir: str = None, rawg_api_key: str = None):
        # [M2 FIX] Default to XDG cache dir; allow override for tests.
        resolved = Path(cache_dir) if cache_dir else Path(_DEFAULT_CACHE_DIR)
        resolved.mkdir(parents=True, exist_ok=True)
        # Owner-only directory: prevents other local users from listing/reading cache.
        try:
            resolved.chmod(0o700)
        except Exception:
            pass
        self.cache_dir = resolved

        # [M2 FIX] Migrate any cached banners from the old in-project .banner_cache/
        self._migrate_legacy_cache()

        self.rawg_api_key = rawg_api_key or os.environ.get("RAWG_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'MGLauncher/1.0 (Game Launcher)'
        })

    def _migrate_legacy_cache(self) -> None:
        """Move any .jpg files from old project-dir .banner_cache/ to the XDG cache dir."""
        legacy = Path(_LEGACY_CACHE_DIR)
        if not legacy.is_dir():
            return
        try:
            for f in legacy.glob("*.jpg"):
                dest = self.cache_dir / f.name
                if not dest.exists():
                    shutil.move(str(f), str(dest))
                    try:
                        dest.chmod(0o600)
                    except Exception:
                        pass
            # Remove legacy dir if now empty
            remaining = list(legacy.iterdir())
            if not remaining:
                legacy.rmdir()
                print(f"[SteamGridDBClient] Migrated banner cache → {self.cache_dir}")
        except Exception as e:
            print(f"[SteamGridDBClient] Legacy cache migration error: {e}")
    
    def search_game(self, game_name: str) -> Dict:
        """Search for a game banner via Steam Store API (primary) or RAWG API (if key available)."""
        if not game_name or not game_name.strip():
            return {'found': False, 'results': [], 'primary': None}
        
        # 1. Primary: Steam Store Search API (No API key needed, returns 231x87 capsule images)
        try:
            params = {
                'term': game_name.strip(),
                'l': 'english',
                'cc': 'US'
            }
            response = self.session.get(self.STEAM_STORE_API, params=params, timeout=8)
            if response.status_code == 200:
                data = response.json()
                items = data.get('items', [])
                if items:
                    game_results = []
                    for item in items:
                        appid = item.get('id')
                        name = item.get('name', game_name)
                        
                        if appid:
                            banner_url = f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900.jpg"
                        else:
                            banner_url = item.get('tiny_image')
                        
                        if banner_url:
                            game_results.append({
                                'name': name,
                                'banner_url': banner_url,
                                'rating': 0.0,
                                'released': 'Steam Store',
                                'appid': appid
                            })
                    
                    if game_results:
                        return {
                            'found': True,
                            'results': game_results,
                            'primary': game_results[0]
                        }
        except Exception as e:
            print(f"Error querying Steam Store API for '{game_name}': {e}")
        
        # 2. Fallback: RAWG API if key is provided
        if self.rawg_api_key:
            try:
                params = {
                    'search': game_name.strip(),
                    'search_exact': 'false',
                    'page_size': 5,
                    'key': self.rawg_api_key
                }
                response = self.session.get(f"{self.RAWG_API}/games", params=params, timeout=8)
                if response.status_code == 200:
                    data = response.json()
                    results = data.get('results', [])
                    game_results = []
                    for result in results:
                        background_image = result.get('background_image')
                        if background_image:
                            game_results.append({
                                'name': result.get('name', game_name),
                                'banner_url': background_image,
                                'rating': result.get('rating', 0),
                                'released': result.get('released', 'Unknown')
                            })
                    
                    if game_results:
                        return {
                            'found': True,
                            'results': game_results,
                            'primary': game_results[0]
                        }
            except Exception as e:
                print(f"Error querying RAWG API for '{game_name}': {e}")
        
        return {
            'found': False,
            'results': [],
            'primary': None
        }
    
    # [H1 FIX] Maximum banner download size: 10 MB. Prevents memory exhaustion from
    # unexpectedly large responses (e.g. compromised CDN or MITM attack).
    _MAX_BANNER_BYTES = 10 * 1024 * 1024  # 10 MB

    def download_banner(self, url: str, game_id: Optional[int] = None) -> Optional[str]:
        """Download and cache banner locally, uniquely keyed by URL MD5 hash to prevent cache collisions.
        
        Security controls:
        - Validates HTTP Content-Type is an image/* before writing to disk.
        - Caps download size at 10 MB to prevent memory exhaustion.
        """
        if not url:
            return None
        
        try:
            url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()[:12]
            if game_id and game_id > 0:
                filename = f"game_{game_id}_{url_hash}.jpg"
            else:
                filename = f"banner_{url_hash}.jpg"
            
            cache_file = self.cache_dir / filename
            
            # Return cached path if already downloaded
            if cache_file.exists():
                return str(cache_file.resolve())
            
            # Download banner with streaming to enforce size cap
            response = self.session.get(url, timeout=10, stream=True)
            if response.status_code != 200:
                return None

            # [H1 FIX] Validate Content-Type is an image before writing anything to disk.
            content_type = response.headers.get('Content-Type', '')
            if not content_type.startswith('image/'):
                print(f"Refusing banner download: unexpected Content-Type '{content_type}' from {url}")
                return None

            # [H1 FIX] Enforce 10 MB size cap while streaming.
            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=65536):
                total += len(chunk)
                if total > self._MAX_BANNER_BYTES:
                    print(f"Refusing banner download: response exceeds {self._MAX_BANNER_BYTES // (1024*1024)} MB from {url}")
                    return None
                chunks.append(chunk)

            with open(cache_file, 'wb') as f:
                for chunk in chunks:
                    f.write(chunk)
            # [M2 FIX] Restrict cached file to owner-only (rw-------)
            try:
                cache_file.chmod(0o600)
            except Exception:
                pass
            return str(cache_file.resolve())
        except Exception as e:
            print(f"Error downloading banner from {url}: {e}")
        
        return None
    
    def get_default_banner(self) -> Optional[str]:
        """Return path to default banner (placeholder)."""
        placeholder = self.cache_dir / "placeholder.jpg"
        if not placeholder.exists():
            try:
                from PIL import Image
                img = Image.new('RGB', (200, 300), color=(50, 50, 50))
                img.save(placeholder)
            except Exception as e:
                print(f"Could not create placeholder image: {e}")
        return str(placeholder.resolve()) if placeholder.exists() else None
