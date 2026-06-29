import xbmc
import xbmcaddon
import xbmcgui

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')


def string(string_id):
    return ADDON.getLocalizedString(string_id)


# ── Settings ──────────────────────────────────────────────────────────────────
# Always read/write through a fresh Addon instance: the scrobbler service lives
# for the whole Kodi session while the UI plugin writes settings from a separate
# process — a cached Addon object may serve stale values.

def get_setting(key):
    return xbmcaddon.Addon().getSetting(key)


def set_setting(key, value):
    xbmcaddon.Addon().setSetting(key, str(value))


def get_setting_bool(key):
    return get_setting(key) == 'true'


def get_setting_int(key, default=0):
    try:
        return int(get_setting(key))
    except ValueError:
        return default


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ── UI helpers ────────────────────────────────────────────────────────────────

def notify(message, title='MyShows.me', icon=xbmcgui.NOTIFICATION_INFO, time_ms=4000):
    xbmcgui.Dialog().notification(title, message, icon, time_ms)


def notify_error(message):
    notify(message, icon=xbmcgui.NOTIFICATION_ERROR, time_ms=6000)


def log(message, level=xbmc.LOGDEBUG):
    xbmc.log(f'[MyShows] {message}', level)


def log_warning(message):
    log(message, xbmc.LOGWARNING)


def log_error(message):
    log(message, xbmc.LOGERROR)


def ask_rating(title='', allow_fallback=True):
    """Ask for a 1–5 rating; returns int or None.

    allow_fallback=False (used by the unattended scrobbler path) skips the
    plain select() fallback, which has no timeout and would otherwise hang
    open indefinitely if the graphical dialog failed to load.
    """
    heading = string(32022)
    if title:
        heading = f'{title} — {heading}'

    # Graphical star picker (auto-closes after a timeout); see rating_dialog.
    import rating_dialog
    result = rating_dialog.ask_rating_stars(heading, initial=5)
    if result is not False:
        return result  # int 1..5 or None (cancelled / timed out)

    if not allow_fallback:
        return None

    values = [5, 4, 3, 2, 1]
    options = ['★' * v + '☆' * (5 - v) for v in values]
    idx = xbmcgui.Dialog().select(heading, options)
    return values[idx] if idx >= 0 else None


def ask_show_status():
    statuses = [
        ('watching', string(32003)),
        ('later', string(32004)),
        ('cancelled', string(32005)),
        ('remove', string(32036)),  # API enum is 'remove', not 'removed'
    ]
    idx = xbmcgui.Dialog().select(string(32011), [s[1] for s in statuses])
    if idx >= 0:
        return statuses[idx][0]
    return None


def refresh_container():
    xbmc.executebuiltin('Container.Refresh')


def show_image_url(image_path):
    if not image_path:
        return ''
    if image_path.startswith('http'):
        return image_path
    return f'https://myshows.me{image_path}'


def parse_ext_id(raw):
    """Convert an external id ('tt1234567', '1234567', 1234567) to int, None on failure."""
    if not raw:
        return None
    raw = str(raw).strip()
    if raw.startswith('tt'):
        raw = raw[2:]
    try:
        return int(raw)
    except ValueError:
        return None


def format_episode_title(season, episode, title=''):
    label = f'S{safe_int(season):02d}E{safe_int(episode):02d}'
    if title:
        label = f'{label} — {title}'
    return label
