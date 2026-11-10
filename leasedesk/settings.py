"""User settings saved on this computer: the Anthropic API key and the library folder.

The key is stored in ``data/settings.env``, which is git-ignored and readable
only by the current user. An ``ANTHROPIC_API_KEY`` environment variable, if
set, takes priority.
"""

from __future__ import annotations

import os

from leasedesk import config

_KEY_NAME = "ANTHROPIC_API_KEY"


def _read_file() -> dict[str, str]:
    if not config.SETTINGS_PATH.exists():
        return {}
    values = {}
    for line in config.SETTINGS_PATH.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    return values


def get_api_key() -> str | None:
    return os.environ.get(_KEY_NAME) or _read_file().get(_KEY_NAME) or None


def save_api_key(key: str) -> None:
    config.ensure_data_dirs()
    values = _read_file()
    values[_KEY_NAME] = key.strip()
    config.SETTINGS_PATH.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    config.SETTINGS_PATH.chmod(0o600)


def clear_api_key() -> None:
    values = _read_file()
    values.pop(_KEY_NAME, None)
    if config.SETTINGS_PATH.exists():
        config.SETTINGS_PATH.write_text("".join(f"{k}={v}\n" for k, v in values.items()))


def masked_api_key() -> str | None:
    key = get_api_key()
    return f"{key[:7]}…{key[-4:]}" if key and len(key) > 12 else None


# --------------------------------------------------------------------------- library folder

_LIBRARY_KEY = "LIBRARY_FOLDER"


def get_library_folder() -> str | None:
    """The folder that holds one subfolder per property."""
    return os.environ.get("LEASEDESK_LIBRARY_FOLDER") or _read_file().get(_LIBRARY_KEY) or None


def save_library_folder(path: str) -> None:
    config.ensure_data_dirs()
    values = _read_file()
    values[_LIBRARY_KEY] = path.strip()
    config.SETTINGS_PATH.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    config.SETTINGS_PATH.chmod(0o600)
