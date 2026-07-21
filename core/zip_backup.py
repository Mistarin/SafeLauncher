import os
import zipfile
from core.interfaces import IBackupManager

class ZipBackupManager(IBackupManager):
    def export_save(self, save_path: str, export_zip_path: str) -> bool:
        if not os.path.exists(save_path):
            return False

        with zipfile.ZipFile(export_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(save_path):
                for file in files:
                    full_path = os.path.join(root, file)
                    arcname = os.path.relpath(full_path, start=save_path)
                    zipf.write(full_path, arcname)
        return True

    def import_save(self, import_zip_path: str, destination_path: str) -> bool:
        if not os.path.exists(import_zip_path):
            return False

        os.makedirs(destination_path, exist_ok=True)
        with zipfile.ZipFile(import_zip_path, 'r') as zipf:
            zipf.extractall(destination_path)
        return True