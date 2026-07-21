import requests
import os
from pathlib import Path
from typing import Optional, List

class SteamGridDBClient:
    """Fetches game banners from SteamGridDB API"""
    
    BASE_URL = "https://www.steamgriddb.com/api/v2"
    
    def __init__(self, cache_dir: str = ".banner_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        # Note: SteamGridDB is free, but consider adding your API key if needed
        # For now, we'll use web scraping via search
    
    def search_game(self, game_name: str) -> Optional[dict]:
        """Search for a game and return first result"""
        try:
            # Using a simple approach - try to construct SteamGridDB URL directly
            safe_name = game_name.replace(" ", "%20").replace(":", "").lower()
            
            # Attempt to fetch from SteamGridDB
            url = f"https://www.steamgriddb.com/search/grids/{safe_name}"
            headers = {
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                # Return a game info dict with banner URL
                return {
                    "name": game_name,
                    "banner_url": self._extract_banner_url(safe_name),
                    "found": True
                }
        except Exception as e:
            print(f"Error searching game: {e}")
        
        return {"name": game_name, "banner_url": None, "found": False}
    
    def _extract_banner_url(self, game_name: str) -> Optional[str]:
        """Extract banner URL from SteamGridDB"""
        try:
            # Construct a direct URL to SteamGridDB grid image
            # This is a simplified approach - in production, you'd use their API
            safe_name = game_name.replace("%20", "-")
            url = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{safe_name}/capsule_231x87.jpg"
            
            # Try to verify URL exists
            response = requests.head(url, timeout=3)
            if response.status_code == 200:
                return url
        except:
            pass
        
        return None
    
    def download_banner(self, url: str, game_id: int) -> Optional[str]:
        """Download and cache banner locally"""
        if not url:
            return None
        
        try:
            cache_file = self.cache_dir / f"game_{game_id}.jpg"
            
            # Return cached path if already downloaded
            if cache_file.exists():
                return str(cache_file)
            
            # Download banner
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                with open(cache_file, 'wb') as f:
                    f.write(response.content)
                return str(cache_file)
        except Exception as e:
            print(f"Error downloading banner: {e}")
        
        return None
    
    def get_default_banner(self) -> str:
        """Return path to default banner (placeholder)"""
        # Create a simple placeholder image if needed
        placeholder = self.cache_dir / "placeholder.jpg"
        if not placeholder.exists():
            # Create a simple colored square as placeholder
            try:
                from PIL import Image
                img = Image.new('RGB', (231, 87), color=(50, 50, 50))
                img.save(placeholder)
            except:
                pass
        return str(placeholder) if placeholder.exists() else None
