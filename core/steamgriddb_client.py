import os
import shutil
import tempfile
import requests
import hashlib
from pathlib import Path
from typing import Any, Optional, List, Dict

# [M2 FIX] XDG-compliant cache directory: ~/.cache/safelauncher/banners/
# Owner-only permissions (700 on dir, 600 on files) to prevent other local users
# from reading cached cover art files.
_XDG_CACHE_HOME = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
_DEFAULT_CACHE_DIR = os.path.join(_XDG_CACHE_HOME, "safelauncher", "banners")
_LEGACY_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".banner_cache")


def _close_response(response) -> None:
    """Close an optional requests response without masking the real result."""
    if response is not None:
        try:
            response.close()
        except Exception:
            pass


class SteamGridDBClient:
    """Fetches game banners using Steam Store API (primary) and optional API keys if provided."""

    BASE_URL = "https://www.steamgriddb.com/api/v2"
    STEAM_STORE_API = "https://store.steampowered.com/api/storesearch"
    RAWG_API = "https://api.rawg.io/api"

    def __init__(self, cache_dir: str = None, rawg_api_key: str = None, api_key: str = None):
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

        self.api_key = api_key or os.environ.get("STEAMGRIDDB_API_KEY")
        self.rawg_api_key = rawg_api_key or os.environ.get("RAWG_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'SafeLauncher/1.0 (Game Launcher)'
        })

    def close(self) -> None:
        """Release the shared artwork HTTP pool during application shutdown."""
        try:
            self.session.close()
        except Exception:
            pass

    def __del__(self):
        self.close()

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
        response = None
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
            print(f"Error querying Steam Store API for '{game_name}': {type(e).__name__}")
        finally:
            _close_response(response)
        
        # 2. Fallback: RAWG API if key is provided
        if self.rawg_api_key:
            response = None
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
                print(f"Error querying RAWG API for '{game_name}': {type(e).__name__}")
            finally:
                _close_response(response)
        
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
        
        response = None
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
                print(f"Refusing banner download: unexpected Content-Type '{content_type}'")
                return None

            # [H1 FIX] Enforce 10 MB size cap while streaming.
            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=65536):
                total += len(chunk)
                if total > self._MAX_BANNER_BYTES:
                    print(f"Refusing banner download: response exceeds {self._MAX_BANNER_BYTES // (1024*1024)} MB")
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
            print(f"Error downloading banner: {type(e).__name__}")
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
        
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

    @staticmethod
    def get_artwork_key(steam_id: Optional[Any] = None, game_name: str = "", exe_path: str = "", game_id: Optional[int] = None) -> str:
        """Derive a stable, collision-free artwork identifier for a game.
        
        Steam games use steam_{steam_id} which is immutable and survives database resets.
        Custom games use a sanitized slug and identity hash derived from name and executable.
        """
        sid = str(steam_id).strip() if steam_id is not None else ""
        if sid and sid not in ("0", "None", ""):
            clean_digits = "".join(c for c in sid if c.isdigit())
            if clean_digits and int(clean_digits) > 0:
                return f"steam_{clean_digits}"
            safe_sid = "".join(c for c in sid if c.isalnum() or c in ("-", "_"))
            if safe_sid:
                return f"steam_{safe_sid}"

        norm_name = (game_name or "").strip().lower().replace(" ", "_")
        clean_name = "".join(c for c in norm_name if c.isalnum() or c in ("-", "_"))[:24]
        ident_src = f"{game_name}|{exe_path}"
        ident_hash = hashlib.sha256(ident_src.encode("utf-8")).hexdigest()[:12]
        if clean_name:
            return f"{clean_name}_{ident_hash}"
        if game_id and game_id > 0:
            return f"id_{game_id}_{ident_hash}"
        return f"custom_{ident_hash}"

    def get_hero_cached_path(self, steam_id: Optional[Any] = None, game_name: str = "", exe_path: str = "", game_id: Optional[int] = None) -> Optional[str]:
        """Return the resolved path to a cached 16:9 hero image if it exists."""
        hero_cache_dir = self.cache_dir / "heroes"
        if not hero_cache_dir.is_dir():
            return None

        art_key = self.get_artwork_key(steam_id=steam_id, game_name=game_name, exe_path=exe_path, game_id=game_id)
        canonical_file = hero_cache_dir / f"hero_{art_key}.jpg"
        if canonical_file.is_file() and canonical_file.stat().st_size > 0:
            return str(canonical_file.resolve())

        sid = str(steam_id).strip() if steam_id is not None else ""
        if sid and sid not in ("0", "None", ""):
            alt_steam = hero_cache_dir / f"hero_steam_{sid}.jpg"
            if alt_steam.is_file() and alt_steam.stat().st_size > 0:
                return str(alt_steam.resolve())
            return None

        if game_id and game_id > 0:
            legacy_file = hero_cache_dir / f"hero_{game_id}.jpg"
            if legacy_file.is_file() and legacy_file.stat().st_size > 0:
                return str(legacy_file.resolve())
        return None

    def get_icon_cached_path(self, steam_id: Optional[Any] = None, game_name: str = "", exe_path: str = "", game_id: Optional[int] = None) -> Optional[str]:
        """Return the resolved path to a cached icon if it exists."""
        icons_dir = self.cache_dir.parent / "icons"
        if not icons_dir.is_dir():
            return None

        art_key = self.get_artwork_key(steam_id=steam_id, game_name=game_name, exe_path=exe_path, game_id=game_id)
        for ext in (".png", ".ico", ".jpg"):
            canonical_file = icons_dir / f"icon_{art_key}{ext}"
            if canonical_file.is_file() and canonical_file.stat().st_size > 0:
                return str(canonical_file.resolve())

        sid = str(steam_id).strip() if steam_id is not None else ""
        if sid and sid not in ("0", "None", ""):
            for ext in (".png", ".ico", ".jpg"):
                alt_steam = icons_dir / f"icon_steam_{sid}{ext}"
                if alt_steam.is_file() and alt_steam.stat().st_size > 0:
                    return str(alt_steam.resolve())
            return None

        if game_id and game_id > 0:
            for ext in (".png", ".ico", ".jpg"):
                legacy_file = icons_dir / f"icon_{game_id}{ext}"
                if legacy_file.is_file() and legacy_file.stat().st_size > 0:
                    return str(legacy_file.resolve())
        return None

    @staticmethod
    def _write_chunks_atomic(destination: Path, chunks: list[bytes]) -> None:
        """Write downloaded artwork atomically so readers never see a partial file."""
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=str(destination.parent),
                prefix=f".{destination.name}.", suffix=".tmp", delete=False
            ) as temp_file:
                temp_path = Path(temp_file.name)
                for chunk in chunks:
                    temp_file.write(chunk)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, destination)
            temp_path = None
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def download_hero_banner(self, steam_id: Optional[Any], game_id: int, game_name: str, exe_path: str = "") -> Optional[str]:
        """Download and cache TRUE 16:9 wide library hero/background artwork (never 9:16 portrait cover art)."""
        try:
            hero_cache_dir = self.cache_dir / "heroes"
            hero_cache_dir.mkdir(exist_ok=True, parents=True)

            art_key = self.get_artwork_key(steam_id=steam_id, game_name=game_name, exe_path=exe_path, game_id=game_id)
            canonical_file = hero_cache_dir / f"hero_{art_key}.jpg"
            legacy_file = hero_cache_dir / f"hero_{game_id}.jpg" if game_id else None

            # If canonical cache file exists, verify it's a 16:9 landscape image (width >= height)
            if canonical_file.exists():
                try:
                    from PIL import Image
                    with Image.open(canonical_file) as img:
                        w, h = img.size
                        if w >= h:
                            if legacy_file and legacy_file != canonical_file:
                                try:
                                    shutil.copyfile(str(canonical_file), str(legacy_file))
                                except Exception:
                                    pass
                            return str(canonical_file.resolve())
                        else:
                            canonical_file.unlink()
                except Exception:
                    return str(canonical_file.resolve())

            # Resolve App ID if missing
            resolved_appid = steam_id
            if not resolved_appid or str(resolved_appid) in ("0", "None"):
                search_res = self.search_game(game_name)
                if search_res.get("found") and search_res.get("primary"):
                    resolved_appid = search_res["primary"].get("appid")
                    if resolved_appid:
                        art_key = self.get_artwork_key(steam_id=resolved_appid, game_name=game_name, exe_path=exe_path, game_id=game_id)
                        canonical_file = hero_cache_dir / f"hero_{art_key}.jpg"

            # Priority 1: Direct 16:9 widescreen Steam CDN endpoints
            urls_to_try = []
            if resolved_appid and str(resolved_appid).isdigit() and int(resolved_appid) > 0:
                urls_to_try.append(f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{resolved_appid}/library_hero.jpg")
                urls_to_try.append(f"https://cdn.cloudflare.steamstatic.com/steam/apps/{resolved_appid}/library_hero.jpg")
                urls_to_try.append(f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{resolved_appid}/page_bg_raw.jpg")
                urls_to_try.append(f"https://cdn.cloudflare.steamstatic.com/steam/apps/{resolved_appid}/page_bg_generated_v6.jpg")
                urls_to_try.append(f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{resolved_appid}/header.jpg")
                urls_to_try.append(f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{resolved_appid}/capsule_616x353.jpg")

            # Priority 2: Query Steam Store App Details API for 1920x1080 Screenshots
            if resolved_appid and str(resolved_appid).isdigit() and int(resolved_appid) > 0:
                app_resp = None
                try:
                    app_url = f"https://store.steampowered.com/api/appdetails?appids={resolved_appid}"
                    app_resp = self.session.get(app_url, timeout=6)
                    if app_resp.status_code == 200:
                        app_data = app_resp.json().get(str(resolved_appid), {}).get("data", {})
                        screenshots = app_data.get("screenshots", [])
                        for ss in screenshots[:3]:
                            ss_url = ss.get("path_full")
                            if ss_url:
                                urls_to_try.append(ss_url)
                except Exception as e:
                    print(f"Steam appdetails query notice: {type(e).__name__}")
                finally:
                    _close_response(app_resp)

            # Priority 3: Query RAWG API for background_image if available
            if self.rawg_api_key and game_name:
                rawg_res = None
                try:
                    rawg_res = self.session.get(f"{self.RAWG_API}/games", params={'search': game_name, 'key': self.rawg_api_key}, timeout=6)
                    if rawg_res.status_code == 200:
                        results = rawg_res.json().get('results', [])
                        if results and results[0].get('background_image'):
                            urls_to_try.append(results[0]['background_image'])
                except Exception:
                    pass
                finally:
                    _close_response(rawg_res)

            for url in urls_to_try:
                response = None
                try:
                    response = self.session.get(url, timeout=6, stream=True)
                    if response.status_code == 200 and response.headers.get('Content-Type', '').startswith('image/'):
                        chunks = []
                        total = 0
                        for chunk in response.iter_content(chunk_size=65536):
                            total += len(chunk)
                            if total > self._MAX_BANNER_BYTES:
                                print(f"Refusing hero download: response exceeds {self._MAX_BANNER_BYTES // (1024*1024)} MB")
                                break
                            chunks.append(chunk)
                        else:
                            self._write_chunks_atomic(canonical_file, chunks)

                            # Verify downloaded file is widescreen landscape (width >= height)
                            try:
                                from PIL import Image
                                with Image.open(canonical_file) as img:
                                    w, h = img.size
                                    if w >= h:
                                        if legacy_file and legacy_file != canonical_file:
                                            try:
                                                shutil.copyfile(str(canonical_file), str(legacy_file))
                                            except Exception:
                                                pass
                                        return str(canonical_file.resolve())
                                    else:
                                        canonical_file.unlink()
                            except Exception:
                                if canonical_file.exists():
                                    if legacy_file and legacy_file != canonical_file:
                                        try:
                                            shutil.copyfile(str(canonical_file), str(legacy_file))
                                        except Exception:
                                            pass
                                    return str(canonical_file.resolve())
                except Exception:
                    continue
                finally:
                    if response is not None:
                        try:
                            response.close()
                        except Exception:
                            pass

        except Exception as e:
            print(f"Error fetching hero banner: {e}")

        return None

    def fetch_and_cache_game_icon(self, game_id: int, steam_id: Optional[str] = None, game_name: str = "", exe_path: Optional[str] = None) -> Optional[str]:
        """Fetch and locally cache a game icon."""
        if not game_id and not steam_id and not game_name:
            return None

        icons_dir = self.cache_dir.parent / "icons"
        icons_dir.mkdir(parents=True, exist_ok=True)
        try:
            icons_dir.chmod(0o700)
        except Exception:
            pass

        art_key = self.get_artwork_key(steam_id=steam_id, game_name=game_name, exe_path=exe_path or "", game_id=game_id)
        cache_file = icons_dir / f"icon_{art_key}.png"
        legacy_file = icons_dir / f"icon_{game_id}.png" if game_id else None

        if cache_file.exists() and cache_file.stat().st_size > 0:
            if legacy_file and legacy_file != cache_file:
                try:
                    shutil.copyfile(str(cache_file), str(legacy_file))
                except Exception:
                    pass
            return str(cache_file.resolve())

        # 1. Direct Windows .exe embedded icon extraction (highest fidelity authentic icon)
        if exe_path and os.path.isfile(exe_path):
            try:
                from core.icon_extractor import extract_exe_icon
                if extract_exe_icon(exe_path, str(cache_file)):
                    if legacy_file and legacy_file != cache_file:
                        try:
                            shutil.copyfile(str(cache_file), str(legacy_file))
                        except Exception:
                            pass
                    return str(cache_file.resolve())
            except Exception:
                pass

        # 2. Check local game folder for loose icon files
        if exe_path and os.path.exists(exe_path):
            folder = os.path.dirname(exe_path)
            for cand in ("icon.png", "icon.ico", "app.png", "app.ico", "logo.png"):
                cand_path = os.path.join(folder, cand)
                if os.path.isfile(cand_path) and os.path.getsize(cand_path) > 0:
                    try:
                        shutil.copyfile(cand_path, cache_file)
                        if legacy_file and legacy_file != cache_file:
                            try:
                                shutil.copyfile(str(cache_file), str(legacy_file))
                            except Exception:
                                pass
                        return str(cache_file.resolve())
                    except Exception:
                        pass

        resolved_appid = steam_id
        if not resolved_appid or str(resolved_appid) in ("0", "", "None"):
            if game_name:
                search_res = self.search_game(game_name)
                if search_res.get('found') and search_res.get('primary'):
                    resolved_appid = search_res['primary'].get('appid')
                    if resolved_appid:
                        art_key = self.get_artwork_key(steam_id=resolved_appid, game_name=game_name, exe_path=exe_path or "", game_id=game_id)
                        cache_file = icons_dir / f"icon_{art_key}.png"

        urls_to_try = []

        # 1. Try SteamGridDB Icons endpoint if API key exists
        if self.api_key and resolved_appid:
            sgdb_resp = None
            try:
                sgdb_resp = self.session.get(
                    f"{self.BASE_URL}/icons/steam/{resolved_appid}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=5
                )
                if sgdb_resp.status_code == 200:
                    data = sgdb_resp.json()
                    for item in data.get('data', []):
                        u = item.get('url')
                        if u:
                            urls_to_try.append(u)
            except Exception:
                pass
            finally:
                _close_response(sgdb_resp)

        if resolved_appid and str(resolved_appid).isdigit() and int(resolved_appid) > 0:
            urls_to_try.append(f"https://cdn.cloudflare.steamstatic.com/steam/apps/{resolved_appid}/logo.png")
            urls_to_try.append(f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{resolved_appid}/capsule_231x87.jpg")
            urls_to_try.append(f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{resolved_appid}/header.jpg")

        max_icon_bytes = 5 * 1024 * 1024  # 5 MB cap; icons are tiny in practice
        for url in urls_to_try:
            resp = None
            try:
                resp = self.session.get(url, timeout=6, stream=True)
                if resp.status_code == 200 and resp.headers.get('Content-Type', '').startswith('image/'):
                    chunks = []
                    total = 0
                    for chunk in resp.iter_content(chunk_size=32768):
                        total += len(chunk)
                        if total > max_icon_bytes:
                            print(f"Refusing icon download: response exceeds {max_icon_bytes // (1024*1024)} MB")
                            break
                        chunks.append(chunk)
                    else:
                        self._write_chunks_atomic(cache_file, chunks)
                        if legacy_file and legacy_file != cache_file:
                            try:
                                shutil.copyfile(str(cache_file), str(legacy_file))
                            except Exception:
                                pass
                        return str(cache_file.resolve())
            except Exception:
                continue
            finally:
                if resp is not None:
                    try:
                        resp.close()
                    except Exception:
                        pass

        return None
