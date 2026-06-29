import json
import os
import time
import xbmc
import xbmcaddon
import xbmcvfs

ADDON = xbmcaddon.Addon()
_PROFILE_DIR = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))


def _cache_path(name):
    return os.path.join(_PROFILE_DIR, f'cache_{name}.json')


def _ensure_dir():
    os.makedirs(_PROFILE_DIR, exist_ok=True)


class FileCache:
    """Persistent key-value cache with TTL, shared between Kodi processes.

    The UI plugin and the scrobbler service each hold their own instance over
    the same JSON file, so every access re-reads the file to pick up the other
    process's writes. Entry volume is small (dozens of keys), so the extra IO
    is negligible.
    """

    def __init__(self, name, ttl=3600):
        self._path = _cache_path(name)
        self._ttl = ttl
        self._data = {}

    def _load(self):
        try:
            with open(self._path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
        except (OSError, ValueError):
            # OSError: missing file or Windows lock from the other process;
            # ValueError: truncated/corrupt JSON. Either way start clean.
            raw = {}
        now = time.time()
        # Prune expired entries so the file does not grow forever
        self._data = {k: v for k, v in raw.items()
                      if isinstance(v, dict) and v.get('exp', 0) > now}

    def _save(self):
        _ensure_dir()
        tmp = f'{self._path}.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._data, f)
            os.replace(tmp, self._path)  # atomic — readers never see torn JSON
        except OSError as e:
            xbmc.log(f'[MyShows] Cache write error: {e}', xbmc.LOGWARNING)

    def get(self, key):
        self._load()
        entry = self._data.get(str(key))
        if entry and time.time() < entry.get('exp', 0):
            return entry['v']
        return None

    def set(self, key, value, ttl=None):
        self._load()
        self._data[str(key)] = {
            'v': value,
            'exp': time.time() + (ttl or self._ttl),
        }
        self._save()

    def invalidate(self, key):
        self._load()
        if self._data.pop(str(key), None) is not None:
            self._save()

    def clear(self):
        self._data = {}
        self._save()


# Module-level singletons shared across plugin and service
shows_cache = FileCache('shows', ttl=24 * 3600)
movies_cache = FileCache('movies', ttl=24 * 3600)
profile_cache = FileCache('profile', ttl=5 * 60)
