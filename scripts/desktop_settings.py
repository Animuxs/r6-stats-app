"""Small machine-local preferences shared by the launcher and Settings UI."""
import json
import os
import tempfile
from pathlib import Path


def setting(folder, name, default=True):
    """A yes/no preference from desktop-settings.json, or `default` when it isn't saved (or can't be read)."""
    try:
        value = json.loads((Path(folder) / "desktop-settings.json").read_text(encoding="utf-8"))
        return value.get(name, default) is not False if isinstance(value, dict) else default
    except (OSError, ValueError):
        return default


def save_setting(folder, name, enabled):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "desktop-settings.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    value[name] = bool(enabled)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, delete=False) as file:
            temporary = file.name
            json.dump(value, file)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def launch_chime_enabled(folder):
    return setting(folder, "launch_chime")


def save_launch_chime(folder, enabled):
    save_setting(folder, "launch_chime", enabled)
