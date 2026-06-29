import os
import sys
import traceback

import xbmc
import xbmcaddon

ADDON = xbmcaddon.Addon()
sys.path.insert(0, os.path.join(ADDON.getAddonInfo('path'), 'resources', 'lib'))

from scrobbler import MyShowsPlayer  # noqa: E402

if __name__ == '__main__':
    monitor = xbmc.Monitor()
    player = MyShowsPlayer()

    xbmc.log('[MyShows] Service started', xbmc.LOGINFO)

    while not monitor.abortRequested():
        try:
            player.tick()
        except Exception:
            # A failed tick must never kill the service loop
            xbmc.log(f'[MyShows] tick failed:\n{traceback.format_exc()}', xbmc.LOGERROR)
        monitor.waitForAbort(5)

    xbmc.log('[MyShows] Service stopped', xbmc.LOGINFO)
