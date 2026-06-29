import sys
import os
from urllib.parse import parse_qs

import xbmc
import xbmcaddon

ADDON = xbmcaddon.Addon()
_LIB = os.path.join(ADDON.getAddonInfo('path'), 'resources', 'lib')
if _LIB not in sys.path:  # guard: this file re-runs under reuselanguageinvoker
    sys.path.insert(0, _LIB)

xbmc.log(f'[MyShows] default.py invoked: argv={sys.argv}', xbmc.LOGINFO)

from navigation import route

if __name__ == '__main__':
    params = {}
    if len(sys.argv) > 2 and sys.argv[2]:
        query = sys.argv[2].lstrip('?')
        params = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(query).items()}
    xbmc.log(f'[MyShows] routing with params={params}', xbmc.LOGINFO)
    try:
        route(params)
    except Exception as e:
        import traceback
        xbmc.log(f'[MyShows] UNHANDLED ERROR in route(): {e}\n{traceback.format_exc()}', xbmc.LOGERROR)
        handle = int(sys.argv[1])
        if handle >= 0:  # RunPlugin invocations pass -1, nothing to close
            import xbmcplugin
            xbmcplugin.endOfDirectory(handle, succeeded=False)
