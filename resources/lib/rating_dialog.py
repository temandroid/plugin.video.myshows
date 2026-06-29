import threading

import xbmc
import xbmcaddon
import xbmcgui

ADDON = xbmcaddon.Addon()

_ACTION_LEFT = 1
_ACTION_RIGHT = 2
_ACTION_BACK = frozenset((9, 10, 92))  # back / escape / nav_back

_FIRST_STAR_ID = 3001
_HEADING_ID = 3100
_GOLD = 'FFFFC107'
_GREY = '55FFFFFF'
_STAR = '★'  # ★
# Auto-close so an unattended dialog (user fell asleep) never keeps Kodi "busy"
# and blocks the screensaver/CEC standby from powering the TV off.
_TIMEOUT_SEC = 60


class _RatingDialog(xbmcgui.WindowXMLDialog):
    """Row of 5 star buttons; selecting the N-th fills the first N gold.

    Navigation (←/→) and confirm/cancel are driven entirely from Python so the
    cumulative highlight stays in sync (the API has no per-control onFocus).
    Mouse users can click a star directly (onClick).
    """

    def onInit(self):
        try:
            self.getControl(_HEADING_ID).setLabel(self.heading)
        except Exception:
            pass
        self.setFocusId(_FIRST_STAR_ID + self.value - 1)
        self._render()
        # Safety net: close the modal if nobody responds.
        self._timer = threading.Timer(getattr(self, 'timeout', _TIMEOUT_SEC), self._timed_out)
        self._timer.daemon = True
        self._timer.start()

    def _timed_out(self):
        self.result = None  # treated as "no rating", same as cancel
        self.close()

    def _cancel_timer(self):
        timer = getattr(self, '_timer', None)
        if timer is not None:
            timer.cancel()

    def _render(self):
        for i in range(1, 6):
            color = _GOLD if i <= self.value else _GREY
            try:
                self.getControl(_FIRST_STAR_ID + i - 1).setLabel(f'[COLOR {color}]{_STAR}[/COLOR]')
            except Exception:
                pass

    def onAction(self, action):
        aid = action.getId()
        if aid in _ACTION_BACK:
            self.result = None
            self._cancel_timer()
            self.close()
        elif aid == _ACTION_LEFT and self.value > 1:
            self.value -= 1
            self.setFocusId(_FIRST_STAR_ID + self.value - 1)
            self._render()
        elif aid == _ACTION_RIGHT and self.value < 5:
            self.value += 1
            self.setFocusId(_FIRST_STAR_ID + self.value - 1)
            self._render()

    def onClick(self, control_id):
        if _FIRST_STAR_ID <= control_id <= _FIRST_STAR_ID + 4:
            self.result = control_id - _FIRST_STAR_ID + 1
            self._cancel_timer()
            self.close()


def ask_rating_stars(heading, initial=5):
    """Show the graphical star picker.

    Returns: an int 1..5 on confirm, None on cancel, or False if the custom
    window could not be loaded (caller should fall back to a plain dialog).
    """
    try:
        dlg = _RatingDialog('DialogRating.xml', ADDON.getAddonInfo('path'), 'Default', '720p')
    except Exception as e:
        xbmc.log(f'[MyShows] rating window load failed: {e}', xbmc.LOGWARNING)
        return False
    dlg.heading = heading
    dlg.value = min(5, max(1, int(initial or 5)))
    dlg.result = None
    try:
        dlg.doModal()
        return dlg.result
    except Exception as e:
        xbmc.log(f'[MyShows] rating window error: {e}', xbmc.LOGWARNING)
        return False
    finally:
        del dlg
