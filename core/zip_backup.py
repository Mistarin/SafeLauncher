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

        dest_abs = os.path.abspath(destination_path)
        os.makedirs(dest_abs, exist_ok=True)

        with zipfile.ZipFile(import_zip_path, 'r') as zipf:
            # Validate all members against directory traversal (Zip Slip vulnerability)
            for member in zipf.infolist():
                target_path = os.path.abspath(os.path.join(dest_abs, member.filename))
                if not target_path.startswith(dest_abs + os.sep) and target_path != dest_abs:
                    print(f"Refusing to extract malicious file outside target directory: {member.filename}")
                    return False
            
            # Python 3.12+ safe extraction filter support
            if hasattr(zipfile.ZipFile, 'extractall') and 'filter' in zipfile.ZipFile.extractall.__code__.co_varnames:
                zipf.extractall(dest_abs, filter='data')
            else:
                zipf.extractall(dest_abs)
        return True