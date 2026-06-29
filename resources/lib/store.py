"""Tiny persistent key-value store for internal data (OAuth tokens, login).

Kept out of settings.xml on purpose: Kodi's v2 settings are for values the
user edits, and declaring hidden technical settings there is fragile. This is
a plain JSON file in the addon profile dir, shared between the UI plugin and
the scrobbler service (each reads before use, writes atomically).
"""
import json
import os
import xbmc
import xbmcaddon
import xbmcvfs

_PROFILE_DIR = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
_PATH = os.path.join(_PROFILE_DIR, 'store.json')


def _read():
    try:
        with open(_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data):
    try:
        os.makedirs(_PROFILE_DIR, exist_ok=True)
        tmp = f'{_PATH}.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        os.replace(tmp, _PATH)  # atomic — readers never see torn JSON
    except OSError as e:
        xbmc.log(f'[MyShows] Store write error: {e}', xbmc.LOGWARNING)


def get(key, default=''):
    return _read().get(key, default)


def set(key, value):
    data = _read()
    data[key] = value
    _write(data)


def delete(*keys):
    data = _read()
    changed = False
    for key in keys:
        if key in data:
            del data[key]
            changed = True
    if changed:
        _write(data)
