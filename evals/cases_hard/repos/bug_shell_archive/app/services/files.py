import shutil
import subprocess
from pathlib import Path

BASE_DIR = Path("/srv/uploads").resolve()
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class FileTooLarge(Exception):
    pass


def _safe_path(name: str) -> Path:
    full = (BASE_DIR / name).resolve()
    if not str(full).startswith(str(BASE_DIR)):
        raise PermissionError("path escapes upload directory")
    return full


def save_upload(name: str, data: bytes) -> Path:
    if len(data) > MAX_UPLOAD_BYTES:
        raise FileTooLarge(name)
    path = _safe_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def read_file(name: str) -> bytes:
    path = _safe_path(name)
    with open(path, "rb") as f:
        return f.read()


def delete_file(name: str) -> None:
    _safe_path(name).unlink(missing_ok=True)


def archive_folder(folder: str, dest: str, compress: bool = True) -> None:
    src = _safe_path(folder)
    flags = "-czf" if compress else "-cf"
    cmd = f"tar {flags} {dest} -C {src.parent} {src.name}"
    subprocess.run(cmd, shell=True, check=True)


def disk_usage() -> int:
    """Bytes used on the volume that holds uploads."""
    return shutil.disk_usage(BASE_DIR).used
