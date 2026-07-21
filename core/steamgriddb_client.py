import os
import requests
import hashlib
from pathlib import Path
from typing import Optional, List, Dict

class SteamGridDBClient:
    """Fetches game banners using Steam Store API (primary) and optional API keys if provided."""
    
    STEAM_STORE_API = "https://store.steampowered.com/api/storesearch"
    RAWG_API = "https://api.rawg.io/api"
    
    def __init__(self, cache_dir: str = ".banner_cache", rawg_api_key: str = None):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.rawg_api_key = rawg_api_key or os.environ.get("RAWG_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'SafeLauncher/1.0 (Game Launcher)'
        })
    
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
    
    def download_banner(self, url: str, game_id: Optional[int] = None) -> Optional[str]:
        """Download and cache banner locally, uniquely keyed by URL MD5 hash to prevent cache collisions."""
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
            
            # Download banner
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                with open(cache_file, 'wb') as f:
                    f.write(response.content)
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
