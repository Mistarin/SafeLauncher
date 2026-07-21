from abc import ABC, abstractmethod
from typing import Optional
import subprocess

class ISandboxRunner(ABC):
    @abstractmethod
    def launch(self, game_path: str, executable: str, mode: str) -> Optional[subprocess.Popen]:
        pass

class IBackupManager(ABC):
    @abstractmethod
    def export_save(self, save_path: str, export_zip_path: str) -> bool:
        pass
    
    @abstractmethod
    def import_save(self, import_zip_path: str, destination_path: str) -> bool:
        pass