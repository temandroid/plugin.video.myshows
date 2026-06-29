import sys
from urllib.parse import urlencode

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin

import store
from api import MyShowsApi, MyShowsApiError
from cache import shows_cache, movies_cache, profile_cache
from utils import (
    string, notify, notify_error, log, log_error,
    ask_rating, ask_show_status, get_setting, safe_int,
    refresh_container, show_image_url, format_episode_title,
)

ADDON_URL = sys.argv[0]
HANDLE = int(sys.argv[1])


def plugin_url(**kwargs):
    return f'{ADDON_URL}?{urlencode(kwargs)}'


def _require_auth(api):
    # ensure_authenticated also re-logins with stored credentials if needed
    if not api.ensure_authenticated():
        notify_error(string(32017))
        return False
    return True


# ── Directory helpers ─────────────────────────────────────────────────────────

def _add_dir(label, url, is_folder=True, info=None, art=None, ctx_menu=None):
    li = xbmcgui.ListItem(label=label)
    if info:
        li.setInfo('video', info)
    if art:
        li.setArt(art)
    if ctx_menu:
        li.addContextMenuItems(ctx_menu)
    xbmcplugin.addDirectoryItem(HANDLE, url, li, is_folder)


def _add_episode_item(ep, show_id, watched_ids):
    ep_id = ep.get('id')
    season = ep.get('seasonNumber', 0)
    episode = ep.get('episodeNumber', 0)
    title = ep.get('title') or ep.get('titleOriginal') or ''
    air_date = ep.get('airDate', '')
    watched = ep_id in watched_ids

    label = format_episode_title(season, episode, title)
    if watched:
        label = f'[COLOR green]{label}[/COLOR]'

    info = {
        'mediatype': 'episode',
        'title': title,
        'season': season,
        'episode': episode,
        'aired': air_date,
        'playcount': 1 if watched else 0,
    }

    action = 'uncheck_episode' if watched else 'check_episode'
    toggle_label = string(32008) if watched else string(32007)

    ctx = [
        (toggle_label, f'RunPlugin({plugin_url(action=action, id=ep_id, show_id=show_id)})'),
        (string(32009), f'RunPlugin({plugin_url(action="rate_episode", id=ep_id)})'),
    ]

    li = xbmcgui.ListItem(label=label)
    li.setInfo('video', info)
    li.addContextMenuItems(ctx)
    # Click toggles watched state
    url = plugin_url(action='toggle_episode', id=ep_id, show_id=show_id, watched=int(watched))
    xbmcplugin.addDirectoryItem(HANDLE, url, li, False)


# ── Route handlers ────────────────────────────────────────────────────────────

def root_menu():
    api = MyShowsApi()
    user_login = store.get('user_login')
    log(f'root_menu: user_login={user_login!r} authenticated={api.is_authenticated()}', xbmc.LOGINFO)
    if user_login:
        status_label = f'[COLOR grey]{string(32032).format(user_login)}[/COLOR]'
    else:
        status_label = f'[COLOR grey]{string(32033)}[/COLOR]'

    _add_dir(status_label, plugin_url(action='login'), is_folder=False,
             ctx_menu=[(string(32029), f'RunPlugin({plugin_url(action="logout")})')])
    _add_dir(string(32049), plugin_url(action='new_episodes'))  # New episodes
    _add_dir(string(32000), plugin_url(action='my_shows'))  # My Shows
    _add_dir(string(32001), plugin_url(action='my_movies'))  # My Movies
    _add_dir(string(32002), plugin_url(action='search'))    # Search

    xbmcplugin.setContent(HANDLE, 'files')
    xbmcplugin.endOfDirectory(HANDLE)


def login_action():
    username = get_setting('username')
    password = get_setting('password')
    log(f'login_action: username_set={bool(username)} password_set={bool(password)}', xbmc.LOGINFO)
    if not username or not password:
        log('login_action: no credentials, opening settings', xbmc.LOGINFO)
        xbmcaddon.Addon().openSettings()
        return

    api = MyShowsApi()
    try:
        log('login_action: calling api.login()', xbmc.LOGINFO)
        api.login(username, password)
        log('login_action: login OK, getting profile', xbmc.LOGINFO)
        profile = api.profile_get()
        login = (profile or {}).get('login', username)
        log('login_action: profile fetched, login OK', xbmc.LOGINFO)
        store.set('user_login', login)
        notify(string(32030))
        profile_cache.clear()
        refresh_container()
    except MyShowsApiError as e:
        log_error(f'login_action: MyShowsApiError: {e}')
        notify_error(string(32031).format(str(e)))
    except Exception as e:
        log_error(f'login_action: unexpected error: {e}')
        import traceback
        log_error(traceback.format_exc())
        notify_error(string(32031).format(str(e)))


def logout_action():
    api = MyShowsApi()
    api.logout()
    profile_cache.clear()
    refresh_container()


# MyShows show watch statuses -> localized label id
_SHOW_STATUS_FILTERS = [
    ('watching', 32003),   # Смотрю
    ('later', 32004),      # Буду смотреть
    ('cancelled', 32005),  # Бросил
    ('finished', 32006),   # Просмотрено
]


def _load_profile_shows(api):
    cached = profile_cache.get('shows')
    if cached is None:
        try:
            cached = api.profile_shows() or {}
            log(f'profile.Shows -> {str(cached)[:300]}', xbmc.LOGINFO)
            profile_cache.set('shows', cached)
        except MyShowsApiError as e:
            notify_error(str(e))
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
            return None
    return _extract_profile_items(cached)


def my_shows(status=None):
    api = MyShowsApi()
    if not _require_auth(api):
        return

    items = _load_profile_shows(api)
    if items is None:
        return

    if not status:
        # Top level: one folder per watch status, with counts
        counts = {}
        for it in items:
            if isinstance(it, dict):
                st = it.get('watchStatus')
                counts[st] = counts.get(st, 0) + 1
        for st, label_id in _SHOW_STATUS_FILTERS:
            label = f'{string(label_id)}  [COLOR grey]({counts.get(st, 0)})[/COLOR]'
            _add_dir(label, plugin_url(action='my_shows', status=st))
        xbmcplugin.setContent(HANDLE, 'files')
        xbmcplugin.endOfDirectory(HANDLE)
        return

    shown = [it for it in items if isinstance(it, dict) and it.get('watchStatus') == status]
    if not shown:
        notify(string(32018))
    for item in shown:
        _add_show_item(item)

    xbmcplugin.setContent(HANDLE, 'tvshows')
    xbmcplugin.addSortMethod(HANDLE, xbmcplugin.SORT_METHOD_TITLE)
    xbmcplugin.endOfDirectory(HANDLE)


def _add_show_item(item):
    show = item.get('show') or item
    if not isinstance(show, dict):
        return
    show_id = show.get('id')
    title = show.get('title') or show.get('titleOriginal', '')
    image = show_image_url(show.get('image', ''))
    watched = item.get('watchedEpisodes', 0)
    total = item.get('totalEpisodes', 0) or show.get('totalEpisodes', 0)

    label = title
    if total:
        label += f'  [COLOR grey]({watched}/{total} {string(32040)})[/COLOR]'

    ctx = [
        (string(32010), f'RunPlugin({plugin_url(action="rate_show", id=show_id)})'),
        (string(32011), f'RunPlugin({plugin_url(action="set_show_status", id=show_id)})'),
        (string(32037), f'RunPlugin({plugin_url(action="refresh_profile")})'),
    ]
    art = {'poster': image, 'thumb': image, 'fanart': image}
    # Checkmark only when every episode is watched, not just some
    fully_watched = bool(total) and watched >= total
    info = {'mediatype': 'tvshow', 'title': title,
            'playcount': 1 if fully_watched else 0,
            'episode': total, 'watchedepisodes': watched}
    _add_dir(label, plugin_url(action='show', id=show_id), art=art, info=info, ctx_menu=ctx)


def _extract_profile_items(data):
    """Handle both list and {watching:[...], later:[...]} dict responses."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        result = []
        for key in ('watching', 'later', 'cancelled', 'completed'):
            result.extend(data.get(key, []))
        if not result:
            # Maybe it's a flat dict of show entries
            result = list(data.values()) if data else []
        return result
    return []


def new_episodes():
    """Aired-but-unwatched episodes from the shows the user is watching."""
    api = MyShowsApi()
    if not _require_auth(api):
        return
    try:
        groups = api.unwatched_episodes() or []
        log(f'lists.EpisodesUnwatched -> {len(groups)} groups', xbmc.LOGINFO)
    except MyShowsApiError as e:
        notify_error(str(e))
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    rows = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        show = group.get('show') or {}
        show_id = show.get('id')
        show_title = show.get('title') or show.get('titleOriginal', '')
        show_image = show_image_url(show.get('image', ''))
        for ep in (group.get('episodes') or []):
            if isinstance(ep, dict):
                rows.append((show_id, show_title, show_image, ep))

    rows.sort(key=lambda r: r[3].get('airDate') or '', reverse=True)  # newest aired first

    if not rows:
        notify(string(32018))
    for show_id, show_title, show_image, ep in rows:
        _add_unwatched_episode(show_id, show_title, show_image, ep)

    xbmcplugin.setContent(HANDLE, 'episodes')
    xbmcplugin.endOfDirectory(HANDLE)


def _add_unwatched_episode(show_id, show_title, show_image, ep):
    ep_id = ep.get('id')
    season = ep.get('seasonNumber', 0)
    episode = ep.get('episodeNumber', 0)
    ep_title = ep.get('title') or ''
    air = (ep.get('airDate') or '')[:10]
    ep_image = show_image_url(ep.get('image', '')) or show_image

    code = ep.get('shortName') or f's{safe_int(season):02d}e{safe_int(episode):02d}'
    label = f'{show_title}  [COLOR grey]{code}[/COLOR]'
    if ep_title:
        label += f' — {ep_title}'

    ctx = [
        (string(32007), f'RunPlugin({plugin_url(action="check_episode", id=ep_id, show_id=show_id)})'),
        (string(32009), f'RunPlugin({plugin_url(action="rate_episode", id=ep_id)})'),
        (string(32050), f'Container.Update({plugin_url(action="show", id=show_id)})'),
    ]
    info = {'mediatype': 'episode', 'tvshowtitle': show_title, 'title': ep_title,
            'season': safe_int(season), 'episode': safe_int(episode), 'aired': air, 'playcount': 0}
    art = {'thumb': ep_image, 'poster': show_image, 'fanart': show_image}

    li = xbmcgui.ListItem(label=label)
    li.setInfo('video', info)
    li.setArt(art)
    li.addContextMenuItems(ctx)
    # Click marks the episode watched (every row here is unwatched)
    xbmcplugin.addDirectoryItem(
        HANDLE, plugin_url(action='check_episode', id=ep_id, show_id=show_id), li, False)


def show_detail(show_id):
    api = MyShowsApi()
    if not _require_auth(api):
        return

    cache_key = f'show_{show_id}'
    show = shows_cache.get(cache_key)
    if show is None:
        try:
            show = api.show_by_id(show_id, with_episodes=True) or {}
            shows_cache.set(cache_key, show)
        except MyShowsApiError as e:
            notify_error(str(e))
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
            return

    episodes = show.get('episodes') or []
    watched_ids = _get_watched_episode_ids(api, show_id)

    by_season = {}
    for ep in episodes:
        sn = ep.get('seasonNumber', 0)
        if sn > 0:
            by_season.setdefault(sn, []).append(ep)

    title = show.get('title') or show.get('titleOriginal', '')
    image = show_image_url(show.get('image', ''))

    for season_num in sorted(by_season):
        eps = by_season[season_num]
        total = len(eps)
        watched = sum(1 for e in eps if e.get('id') in watched_ids)
        fully_watched = total and watched >= total

        label = string(32012).format(season_num)
        if total:
            label += f'  [COLOR grey]({watched}/{total})[/COLOR]'

        art = {'poster': image, 'thumb': image}
        # Season is "watched" only when every episode in it is watched
        info = {'mediatype': 'season', 'tvshowtitle': title, 'season': season_num,
                'episode': total, 'watchedepisodes': watched,
                'playcount': 1 if fully_watched else 0}
        ctx = [
            (string(32044), f'RunPlugin({plugin_url(action="mark_season", show_id=show_id, season=season_num, watched=1)})'),
            (string(32045), f'RunPlugin({plugin_url(action="mark_season", show_id=show_id, season=season_num, watched=0)})'),
        ]
        _add_dir(label, plugin_url(action='season', show_id=show_id, season=season_num),
                 art=art, info=info, ctx_menu=ctx)

    xbmcplugin.setContent(HANDLE, 'seasons')
    xbmcplugin.endOfDirectory(HANDLE)


def season_detail(show_id, season_num):
    api = MyShowsApi()
    if not _require_auth(api):
        return

    cache_key = f'show_{show_id}'
    show = shows_cache.get(cache_key)
    if show is None:
        try:
            show = api.show_by_id(show_id, with_episodes=True) or {}
            shows_cache.set(cache_key, show)
        except MyShowsApiError as e:
            notify_error(str(e))
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
            return

    episodes = [e for e in (show.get('episodes') or []) if e.get('seasonNumber') == int(season_num)]
    episodes.sort(key=lambda e: e.get('episodeNumber', 0))

    # Fetch which episodes are watched
    watched_ids = _get_watched_episode_ids(api, show_id)

    for ep in episodes:
        _add_episode_item(ep, show_id, watched_ids)

    xbmcplugin.setContent(HANDLE, 'episodes')
    xbmcplugin.addSortMethod(HANDLE, xbmcplugin.SORT_METHOD_EPISODE)
    xbmcplugin.endOfDirectory(HANDLE)


def mark_season(show_id, season_num, watched):
    """Check/uncheck every episode of a season in one batched API call."""
    api = MyShowsApi()
    if not _require_auth(api):
        return

    show = shows_cache.get(f'show_{show_id}')
    if show is None:
        try:
            show = api.show_by_id(show_id, with_episodes=True) or {}
            shows_cache.set(f'show_{show_id}', show)
        except MyShowsApiError as e:
            notify_error(str(e))
            return

    ep_ids = [e.get('id') for e in (show.get('episodes') or [])
              if e.get('seasonNumber') == int(season_num) and e.get('id')]
    if not ep_ids:
        return

    try:
        if watched:
            api.sync_episodes_delta(show_id, checked_ids=ep_ids)
        else:
            api.sync_episodes_delta(show_id, unchecked_ids=ep_ids)
        _invalidate_watched_cache(show_id)
        notify(string(32014) if watched else string(32015))
        refresh_container()
    except MyShowsApiError as e:
        notify_error(str(e))


def _get_watched_episode_ids(api, show_id):
    """Return set of watched episode IDs for this show."""
    cache_key = f'watched_{show_id}'
    cached = profile_cache.get(cache_key)
    if cached is not None:
        return set(cached)

    watched = set()
    try:
        eps = api.profile_episodes(show_id)
        log(f'profile.Episodes({show_id}) -> {str(eps)[:300]}', xbmc.LOGINFO)
        watched = _parse_watched_episodes(eps)
    except MyShowsApiError as e:
        log_error(f'profile.Episodes({show_id}): {e}, falling back to ShowStatuses')
        try:
            status = api.profile_show_statuses([int(show_id)])
            log(f'profile.ShowStatuses({show_id}) -> {str(status)[:300]}', xbmc.LOGINFO)
            watched = _parse_watched_ids(status, show_id)
        except MyShowsApiError:
            pass

    profile_cache.set(cache_key, list(watched), ttl=60)
    return watched


def _parse_watched_episodes(data):
    """Parse watched episode IDs from a profile.Episodes response (shape-tolerant)."""
    if isinstance(data, dict):
        data = data.get('episodes') or data.get('items') or []
    ids = set()
    if isinstance(data, list):
        for it in data:
            if isinstance(it, dict):
                ep_id = it.get('id') or it.get('episodeId')
                if ep_id:
                    ids.add(ep_id)
            else:
                ep_id = safe_int(it)
                if ep_id:
                    ids.add(ep_id)
    return ids


def _parse_watched_ids(status_data, show_id):
    """Parse watched episode IDs from profile.ShowStatuses response."""
    if not status_data:
        return set()
    # Expected: {showId: {watchedEpisodeIds: [...]}} or list of objects
    if isinstance(status_data, dict):
        show_status = status_data.get(str(show_id)) or status_data.get(int(show_id)) or {}
        return set(show_status.get('watchedEpisodeIds') or show_status.get('checkedEpisodeIds') or [])
    if isinstance(status_data, list):
        for item in status_data:
            if isinstance(item, dict) and item.get('showId') == int(show_id):
                return set(item.get('watchedEpisodeIds') or [])
    return set()


def check_episode(episode_id, show_id=None):
    api = MyShowsApi()
    if not _require_auth(api):
        return
    try:
        api.check_episode(episode_id)
        _invalidate_watched_cache(show_id)
        notify(string(32014))
        refresh_container()
    except MyShowsApiError as e:
        notify_error(str(e))


def uncheck_episode(episode_id, show_id=None):
    api = MyShowsApi()
    if not _require_auth(api):
        return
    try:
        api.uncheck_episode(episode_id)
        _invalidate_watched_cache(show_id)
        notify(string(32015))
        refresh_container()
    except MyShowsApiError as e:
        notify_error(str(e))


def rate_episode(episode_id):
    api = MyShowsApi()
    if not _require_auth(api):
        return
    rating = ask_rating()
    if rating is None:
        return
    try:
        api.rate_episode(episode_id, rating)
        notify(f'★ {rating}')
    except MyShowsApiError as e:
        notify_error(str(e))


def rate_show(show_id):
    api = MyShowsApi()
    if not _require_auth(api):
        return
    rating = ask_rating()
    if rating is None:
        return
    try:
        api.rate_show(show_id, rating)
        notify(f'★ {rating}')
    except MyShowsApiError as e:
        notify_error(str(e))


def set_show_status(show_id):
    api = MyShowsApi()
    if not _require_auth(api):
        return
    status = ask_show_status()
    if status is None:
        return
    try:
        api.set_show_status(show_id, status)
        profile_cache.clear()
        notify(string(32014))
        refresh_container()
    except MyShowsApiError as e:
        notify_error(str(e))


def _invalidate_watched_cache(show_id):
    if show_id:
        profile_cache.invalidate(f'watched_{show_id}')
    profile_cache.invalidate('shows')


def _invalidate_movies():
    profile_cache.invalidate('movies_watched')
    profile_cache.invalidate('movies_later')


def refresh_profile():
    profile_cache.clear()
    refresh_container()


# ── Movies ────────────────────────────────────────────────────────────────────

def my_movies(status=None):
    api = MyShowsApi()
    if not _require_auth(api):
        return

    if not status:
        # Top level: watched vs will-watch
        _add_dir(string(32006), plugin_url(action='my_movies', status='finished'))  # Просмотрено
        _add_dir(string(32004), plugin_url(action='my_movies', status='later'))     # Буду смотреть
        xbmcplugin.setContent(HANDLE, 'files')
        xbmcplugin.endOfDirectory(HANDLE)
        return

    finished = status == 'finished'
    cache_key = 'movies_watched' if finished else 'movies_later'
    cached = profile_cache.get(cache_key)
    if cached is None:
        try:
            cached = (api.profile_watched_movies() if finished
                      else api.profile_unwatched_movies()) or []
            log(f'movies[{status}] -> {str(cached)[:300]}', xbmc.LOGINFO)
            profile_cache.set(cache_key, cached)
        except MyShowsApiError as e:
            notify_error(str(e))
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
            return

    items = cached if isinstance(cached, list) else _extract_profile_items(cached)
    if not items:
        notify(string(32018))
    for item in items:
        _add_movie_item(item, finished)

    xbmcplugin.setContent(HANDLE, 'movies')
    xbmcplugin.addSortMethod(HANDLE, xbmcplugin.SORT_METHOD_TITLE)
    xbmcplugin.endOfDirectory(HANDLE)


def _add_movie_item(item, watched):
    if not isinstance(item, dict):
        return
    movie = item.get('movie') or item
    if not isinstance(movie, dict):
        return
    movie_id = movie.get('id')
    title = movie.get('title') or movie.get('titleOriginal', '')
    image = show_image_url(movie.get('image', ''))
    year = safe_int(movie.get('year'))

    label = title
    if year:
        label += f' ({year})'

    if watched:
        toggle_label = string(32008)  # Mark as unwatched
        toggle_url = plugin_url(action='toggle_movie', id=movie_id, watched=1)
    else:
        toggle_label = string(32007)  # Mark as watched
        toggle_url = plugin_url(action='toggle_movie', id=movie_id, watched=0)

    ctx = [
        (toggle_label, f'RunPlugin({toggle_url})'),
        (string(32046), f'RunPlugin({plugin_url(action="rate_movie", id=movie_id)})'),
        (string(32037), f'RunPlugin({plugin_url(action="refresh_profile")})'),
    ]
    art = {'poster': image, 'thumb': image, 'fanart': image}
    info = {'mediatype': 'movie', 'title': title, 'year': year,
            'playcount': 1 if watched else 0}
    # Click toggles watched state
    _add_dir(label, toggle_url, is_folder=False, art=art, info=info, ctx_menu=ctx)


def movie_detail(movie_id):
    api = MyShowsApi()
    if not _require_auth(api):
        return

    cache_key = f'movie_{movie_id}'
    movie = movies_cache.get(cache_key)
    if movie is None:
        try:
            movie = api.movie_by_id(movie_id) or {}
            log(f'movies.GetById({movie_id}) -> {str(movie)[:300]}', xbmc.LOGINFO)
            movies_cache.set(cache_key, movie)
        except MyShowsApiError as e:
            notify_error(str(e))
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
            return

    if isinstance(movie.get('movie'), dict):  # tolerate a {movie:{...}} wrapper
        movie = movie['movie']
    title = movie.get('title') or movie.get('titleOriginal', '')
    image = show_image_url(movie.get('image', ''))
    year = safe_int(movie.get('year'))
    plot = movie.get('plot') or movie.get('description', '')

    ctx = [
        (string(32007), f'RunPlugin({plugin_url(action="watch_movie", id=movie_id)})'),
        (string(32046), f'RunPlugin({plugin_url(action="rate_movie", id=movie_id)})'),
    ]

    label = title
    if year:
        label += f' ({year})'

    art = {'poster': image, 'thumb': image, 'fanart': image}
    info = {'mediatype': 'movie', 'title': title, 'year': year, 'plot': plot}
    # Click marks the movie as watched
    _add_dir(label, plugin_url(action='watch_movie', id=movie_id),
             art=art, info=info, ctx_menu=ctx, is_folder=False)

    xbmcplugin.setContent(HANDLE, 'movies')
    xbmcplugin.endOfDirectory(HANDLE)


def watch_movie(movie_id):
    api = MyShowsApi()
    if not _require_auth(api):
        return
    try:
        api.watch_movie(movie_id)
        _invalidate_movies()
        notify(string(32014))
        refresh_container()
    except MyShowsApiError as e:
        notify_error(str(e))


def unwatch_movie(movie_id):
    api = MyShowsApi()
    if not _require_auth(api):
        return
    try:
        api.unwatch_movie(movie_id)
        _invalidate_movies()
        notify(string(32015))
        refresh_container()
    except MyShowsApiError as e:
        notify_error(str(e))


def rate_movie(movie_id):
    api = MyShowsApi()
    if not _require_auth(api):
        return
    rating = ask_rating()
    if rating is None:
        return
    try:
        api.rate_movie(movie_id, rating)
        notify(f'★ {rating}')
    except MyShowsApiError as e:
        notify_error(str(e))


# ── Search ────────────────────────────────────────────────────────────────────

def search():
    _add_dir(string(32020), plugin_url(action='search_shows'))
    _add_dir(string(32021), plugin_url(action='search_movies'))
    xbmcplugin.endOfDirectory(HANDLE)


def search_shows(query=None):
    if not query:
        query = xbmcgui.Dialog().input(string(32019))
    if not query:
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    api = MyShowsApi()
    try:
        results = api.search_shows(query) or []
    except MyShowsApiError as e:
        notify_error(str(e))
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    if not results:
        notify(string(32018))

    for show in results:
        if not isinstance(show, dict):
            continue
        show_id = show.get('id')
        title = show.get('title') or show.get('titleOriginal', '')
        image = show_image_url(show.get('image', ''))

        ctx = [
            (string(32034), f'RunPlugin({plugin_url(action="set_show_status", id=show_id, status="watching")})'),
            (string(32035), f'RunPlugin({plugin_url(action="set_show_status", id=show_id, status="later")})'),
        ]
        art = {'poster': image, 'thumb': image}
        info = {'mediatype': 'tvshow', 'title': title}
        _add_dir(title, plugin_url(action='show', id=show_id), art=art, info=info, ctx_menu=ctx)

    xbmcplugin.setContent(HANDLE, 'tvshows')
    xbmcplugin.endOfDirectory(HANDLE)


def search_movies(query=None):
    if not query:
        query = xbmcgui.Dialog().input(string(32019))
    if not query:
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    api = MyShowsApi()
    try:
        results = api.search_movies(query) or []
    except MyShowsApiError as e:
        notify_error(str(e))
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    if not results:
        notify(string(32018))

    for entry in results:
        if not isinstance(entry, dict):
            continue
        # movies.GetCatalog wraps each result as {movie: {...}}
        movie = entry.get('movie') or entry
        if not isinstance(movie, dict):
            continue
        movie_id = movie.get('id')
        title = movie.get('title') or movie.get('titleOriginal', '')
        year = safe_int(movie.get('year'))
        image = show_image_url(movie.get('image', ''))

        label = title
        if year:
            label += f' ({year})'

        ctx = [
            (string(32007), f'RunPlugin({plugin_url(action="watch_movie", id=movie_id)})'),
            (string(32047), f'RunPlugin({plugin_url(action="set_movie_status", id=movie_id, status="later")})'),
        ]
        art = {'poster': image, 'thumb': image}
        info = {'mediatype': 'movie', 'title': title, 'year': year}
        _add_dir(label, plugin_url(action='movie', id=movie_id), art=art, info=info, ctx_menu=ctx)

    xbmcplugin.setContent(HANDLE, 'movies')
    xbmcplugin.endOfDirectory(HANDLE)


def set_movie_status(movie_id, status):
    api = MyShowsApi()
    if not _require_auth(api):
        return
    try:
        api.set_movie_status(movie_id, status)
        _invalidate_movies()
        notify(string(32051))  # neutral "Done" — status may be 'later', not watched
        refresh_container()
    except MyShowsApiError as e:
        notify_error(str(e))


# ── Router ────────────────────────────────────────────────────────────────────

# Actions that render a directory listing (they call endOfDirectory themselves);
# for everything else route() closes the handle, otherwise a click on an action
# item would leave Kodi waiting for a directory forever (busy spinner).
_DIRECTORY_ACTIONS = {
    'root', 'new_episodes', 'my_shows', 'my_movies', 'show', 'season', 'movie',
    'search', 'search_shows', 'search_movies',
}


def route(params):
    # Re-read invocation context: with <reuselanguageinvoker> the module
    # survives between calls while sys.argv changes on every invocation
    global ADDON_URL, HANDLE
    ADDON_URL = sys.argv[0]
    HANDLE = int(sys.argv[1])

    action = params.get('action', 'root')
    log(f'route called: action={action!r} params={params}', xbmc.LOGINFO)

    def p(key, default=None):
        v = params.get(key, default)
        return v[0] if isinstance(v, list) else v

    if action == 'login':
        login_action()
    elif action == 'logout':
        logout_action()
    elif action == 'new_episodes':
        new_episodes()
    elif action == 'my_shows':
        my_shows(p('status'))
    elif action == 'my_movies':
        my_movies(p('status'))
    elif action == 'show':
        show_detail(p('id'))
    elif action == 'season':
        season_detail(p('show_id'), safe_int(p('season'), 1))
    elif action == 'movie':
        movie_detail(p('id'))
    elif action == 'check_episode':
        check_episode(p('id'), p('show_id'))
    elif action == 'uncheck_episode':
        uncheck_episode(p('id'), p('show_id'))
    elif action == 'toggle_episode':
        if p('watched') == '1':
            uncheck_episode(p('id'), p('show_id'))
        else:
            check_episode(p('id'), p('show_id'))
    elif action == 'toggle_movie':
        if p('watched') == '1':
            unwatch_movie(p('id'))
        else:
            watch_movie(p('id'))
    elif action == 'mark_season':
        mark_season(p('show_id'), p('season'), p('watched') == '1')
    elif action == 'rate_episode':
        rate_episode(p('id'))
    elif action == 'rate_show':
        rate_show(p('id'))
    elif action == 'set_show_status':
        status = p('status')
        if status:
            # Called directly with a status (e.g. from search context menu)
            api = MyShowsApi()
            if _require_auth(api):
                try:
                    api.set_show_status(p('id'), status)
                    profile_cache.clear()
                    notify(string(32051))  # neutral "Done" (status may be 'later'/'watching')
                except MyShowsApiError as e:
                    notify_error(str(e))
        else:
            set_show_status(p('id'))
    elif action == 'watch_movie':
        watch_movie(p('id'))
    elif action == 'unwatch_movie':
        unwatch_movie(p('id'))
    elif action == 'rate_movie':
        rate_movie(p('id'))
    elif action == 'set_movie_status':
        set_movie_status(p('id'), p('status'))
    elif action == 'refresh_profile':
        refresh_profile()
    elif action == 'search':
        search()
    elif action == 'search_shows':
        search_shows(p('query'))
    elif action == 'search_movies':
        search_movies(p('query'))
    else:
        action = 'root'
        root_menu()

    if action not in _DIRECTORY_ACTIONS and HANDLE >= 0:
        # Action item activated from a listing — release the directory handle
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False, updateListing=True)
