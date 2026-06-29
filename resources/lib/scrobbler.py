import json
import re

import xbmc

from api import MyShowsApi, MyShowsApiError
from cache import shows_cache, movies_cache, profile_cache
from utils import (
    notify, string, log, log_warning, parse_ext_id, ask_rating,
    get_setting_bool, get_setting_int,
)


def _jsonrpc(method, params=None):
    payload = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}
    try:
        return json.loads(xbmc.executeJSONRPC(json.dumps(payload))).get('result')
    except ValueError:
        return None


# Season/episode parsed from a label like "s19e02 | Cluster" or "19x02"
_SE_PATTERNS = (
    re.compile(r'[sS](\d{1,3})\s*[eE](\d{1,3})'),
    re.compile(r'(?<!\d)(\d{1,2})x(\d{1,3})(?!\d)'),
)


def _title_parts(*titles):
    """Split combined "Рус / Eng" titles (as kino.pub sends) into ordered,
    de-duplicated query candidates, original case preserved."""
    parts, seen = [], set()
    for t in titles:
        if not t:
            continue
        for piece in str(t).replace(' / ', '/').split('/'):
            piece = piece.strip()
            key = piece.lower()
            if piece and key not in seen:
                seen.add(key)
                parts.append(piece)
    return parts


_NORM_RE = re.compile(r'[^0-9a-zа-яё]+')


def _norm(title):
    """Lowercase, drop punctuation, collapse spaces — for title equality."""
    return ' '.join(_NORM_RE.sub(' ', str(title or '').lower()).split())


def _names_match(names, expected):
    """True only on EXACT normalized title equality.

    Substring matching is deliberately avoided: 'Fallen' must not match
    'Transformers: Revenge of the Fallen'. ID verification (when available)
    handles same-title disambiguation.
    """
    exp = {_norm(e) for e in expected}
    exp.discard('')
    got = {_norm(n) for n in names}
    got.discard('')
    return bool(exp & got)


class _CurrentMedia:
    __slots__ = ('kind', 'show_id', 'episode_id', 'movie_id', 'scrobbled')

    def __init__(self):
        self.clear()

    def clear(self):
        self.kind = None          # 'episode' or 'movie'
        self.show_id = None
        self.episode_id = None
        self.movie_id = None
        self.scrobbled = False


class MyShowsPlayer(xbmc.Player):
    """Watches playback and marks episodes/movies as watched on MyShows.

    Player callbacks arrive on Kodi's announce thread, so they only flip
    flags; all network requests and dialogs run in tick() on the service
    thread (see service.py). Blocking the announce thread stalls every
    other addon's callbacks, and modal dialogs from it can deadlock.
    """

    def __init__(self):
        super().__init__()
        self._media = _CurrentMedia()
        self._api = MyShowsApi()
        self._pending_detect = False
        self._stopped = False
        self._ended_natural = False
        self._rate_pending = None  # (kind, id) to rate once the player closes

    # ── Playback events (announce thread: flags only) ─────────────────────

    def onAVStarted(self):
        self._media.clear()
        self._stopped = False
        self._ended_natural = False
        self._rate_pending = None
        self._pending_detect = True

    def onPlayBackStopped(self):
        # Manual stop: if the threshold was passed, tick() already marked watched.
        self._pending_detect = False
        self._stopped = True
        self._ended_natural = False

    def onPlayBackError(self):
        self._pending_detect = False
        self._media.clear()
        self._rate_pending = None

    def onPlayBackEnded(self):
        # Played to the very end — mark watched in tick() even if a tick never
        # caught the threshold (short clip, threshold near 100%).
        self._pending_detect = False
        self._stopped = True
        self._ended_natural = True

    # ── Service loop ──────────────────────────────────────────────────────

    def tick(self):
        if self._pending_detect and self.isPlayingVideo():
            self._pending_detect = False
            self._detect_media()

        # Playback finished: finalise watched state, then ask for a rating
        # (now that the player has closed — never over the running video).
        if self._stopped:
            self._stopped = False
            if self._media.kind and not self._media.scrobbled and self._ended_natural:
                self._do_scrobble()
            self._media.clear()
            self._ask_pending_rating()
            return

        if not self._media.kind or self._media.scrobbled:
            return
        if not self.isPlayingVideo():
            return
        try:
            total = self.getTotalTime()
            current = self.getTime()
        except Exception:
            return  # player went away between checks
        if total <= 0:
            return
        if current / total * 100 >= get_setting_int('scrobble_at', 80):
            self._do_scrobble()  # mark watched silently; rating waits for stop

    # ── Media detection ───────────────────────────────────────────────────

    def _detect_media(self):
        if not get_setting_bool('scrobble_enabled'):
            return
        if not self._api.ensure_authenticated():
            log('Not authenticated, skipping detection', xbmc.LOGINFO)
            return

        item = self._get_playing_item() or {}
        log(f'Playing item: {str(item)[:300]}', xbmc.LOGINFO)
        itype = item.get('type', '')
        season, episode = self._season_episode(item)

        # SxxExx (incl. parsed from the label) wins over a movie id, because
        # kino.pub plays episodes with type 'unknown', empty titles and the
        # season/episode only in the label.
        if itype == 'episode' or (season > 0 and episode > 0):
            self._detect_episode(item, season, episode)
        elif itype == 'movie' or self._movie_ids_present(item):
            self._detect_movie(item)
        else:
            log('Could not classify playing item, skipping', xbmc.LOGINFO)

    @staticmethod
    def _season_episode(item):
        """Resolve season/episode from item fields, then InfoLabels, then the
        label text (kino.pub sends -1/-1 and only "s19e02 | ..." in the label)."""
        def _pos(v):
            return v if isinstance(v, int) and v > 0 else 0

        season, episode = _pos(item.get('season')), _pos(item.get('episode'))
        if season and episode:
            return season, episode
        try:
            season = season or int(xbmc.getInfoLabel('VideoPlayer.Season') or 0)
            episode = episode or int(xbmc.getInfoLabel('VideoPlayer.Episode') or 0)
        except ValueError:
            season = episode = 0
        if season > 0 and episode > 0:
            return season, episode
        for text in (item.get('label'), xbmc.getInfoLabel('VideoPlayer.Label'),
                     xbmc.getInfoLabel('ListItem.Label')):
            if not text:
                continue
            for pattern in _SE_PATTERNS:
                m = pattern.search(str(text))
                if m:
                    return int(m.group(1)), int(m.group(2))
        return 0, 0

    @staticmethod
    def _get_playing_item():
        players = _jsonrpc('Player.GetActivePlayers') or []
        pid = next((pl.get('playerid') for pl in players if pl.get('type') == 'video'), None)
        if pid is None:
            return None
        result = _jsonrpc('Player.GetItem', {
            'playerid': pid,
            'properties': ['uniqueid', 'season', 'episode', 'showtitle',
                           'tvshowid', 'imdbnumber', 'title', 'originaltitle', 'year'],
        }) or {}
        return result.get('item')

    @staticmethod
    def _movie_ids_present(item):
        uniq = item.get('uniqueid') or {}
        return bool(uniq.get('imdb') or item.get('imdbnumber')
                    or xbmc.getInfoLabel('VideoPlayer.IMDBNumber'))

    def _detect_episode(self, item, season, episode):
        if season <= 0 or episode <= 0:
            log('Episode without season/episode numbers, skipping', xbmc.LOGINFO)
            return

        ext_ids = self._collect_show_ids(item)
        show_title = item.get('showtitle') or xbmc.getInfoLabel('VideoPlayer.TVShowTitle')
        expected = _title_parts(show_title, item.get('originaltitle'))
        expected_lower = [p.lower() for p in expected]
        log(f'Episode: {show_title!r} S{season}E{episode} ids={ext_ids}', xbmc.LOGINFO)

        show = self._resolve_show(ext_ids, expected_lower)
        if not show and expected and get_setting_bool('resolve_by_title'):
            # kino.pub & co. give no reliable id but a good title — search by it
            show = self._resolve_show_by_title(expected, expected_lower)
        if not show:
            log(f'Show not found on MyShows: {show_title!r} ids={ext_ids}', xbmc.LOGINFO)
            return

        show_id = show.get('id')
        self._media.show_id = show_id

        if get_setting_bool('set_watching_on_start'):
            self._mark_watching_once(show_id)

        ep_obj = self._find_episode(show_id, season, episode)
        if ep_obj:
            self._media.kind = 'episode'
            self._media.episode_id = ep_obj.get('id')
            log(f'Episode resolved: id={self._media.episode_id}', xbmc.LOGINFO)
        else:
            log(f'S{season}E{episode} not found in MyShows show {show_id}', xbmc.LOGINFO)

    def _detect_movie(self, item):
        title = item.get('title') or xbmc.getInfoLabel('VideoPlayer.Title')
        original = (item.get('originaltitle') or xbmc.getInfoLabel('VideoPlayer.OriginalTitle')
                    or title)
        year = item.get('year') or 0
        if not year:
            try:
                year = int(xbmc.getInfoLabel('VideoPlayer.Year') or 0)
            except ValueError:
                year = 0
        uniq = item.get('uniqueid') or {}
        # kino.pub & co. expose a bare number that may be an IMDB or Kinopoisk id;
        # collect both candidate ids and verify against the movie later.
        want_ids = set()
        for raw in (uniq.get('imdb'), item.get('imdbnumber'),
                    xbmc.getInfoLabel('VideoPlayer.IMDBNumber'),
                    uniq.get('kinopoisk'), uniq.get('kp')):
            v = parse_ext_id(raw)
            if v:
                want_ids.add(v)

        parts = _title_parts(original, title)
        log(f'Movie: parts={parts} year={year} ids={sorted(want_ids)}', xbmc.LOGINFO)

        # MyShows has no movie-by-external-id; match the catalog by title,
        # then confirm by IMDB/Kinopoisk id (and/or year) to avoid same-word hits.
        movie = self._resolve_movie(parts, year, want_ids)
        if not movie:
            log(f'Movie not found on MyShows: {parts} ({year})', xbmc.LOGINFO)
            return

        self._media.kind = 'movie'
        self._media.movie_id = movie.get('id')
        log(f'Movie resolved: id={self._media.movie_id}', xbmc.LOGINFO)

    def _collect_show_ids(self, item):
        """Build [(ext_id, source, strong), ...] for the show.

        strong=True for ids we trust outright (tt-prefixed IMDB, numeric
        TVDB/TMDB from a real uniqueid). strong=False for a bare number dumped
        into imdbnumber — it may be a prefix-less IMDB id OR a foreign id (e.g.
        kino.pub uses uniqueid {'unknown': <id>}), so the caller must verify it
        against the title before trusting it.
        """
        uniq = {}
        tvshowid = item.get('tvshowid', -1)
        if isinstance(tvshowid, int) and tvshowid >= 0:
            details = (_jsonrpc('VideoLibrary.GetTVShowDetails', {
                'tvshowid': tvshowid,
                'properties': ['uniqueid', 'imdbnumber'],
            }) or {}).get('tvshowdetails') or {}
            uniq = dict(details.get('uniqueid') or {})
            if details.get('imdbnumber'):
                uniq.setdefault('imdb', details['imdbnumber'])
        for key, value in (item.get('uniqueid') or {}).items():
            uniq.setdefault(key, value)
        raw_imdb = uniq.get('imdb') or item.get('imdbnumber') or xbmc.getInfoLabel('VideoPlayer.IMDBNumber')

        ids = []
        if str(raw_imdb).startswith('tt'):          # genuine IMDB id
            v = parse_ext_id(raw_imdb)
            if v:
                ids.append((v, 'imdb', True))
        tvdb = parse_ext_id(uniq.get('tvdb') or uniq.get('thetvdb'))
        if tvdb:
            ids.append((tvdb, 'thetvdb', True))
        tmdb = parse_ext_id(uniq.get('tmdb') or uniq.get('themoviedb'))
        if tmdb:
            ids.append((tmdb, 'themoviedb', True))
        if not str(raw_imdb).startswith('tt'):       # bare number — unverified
            v = parse_ext_id(raw_imdb)
            if v:
                ids.append((v, 'imdb', False))
        return ids

    # ── MyShows resolution ────────────────────────────────────────────────

    def _resolve_show(self, ext_ids, expected_lower):
        for ext_id, source, strong in ext_ids:
            cache_key = f'ext_{source}_{ext_id}'
            show = shows_cache.get(cache_key)
            if show is None:
                try:
                    show = self._api.show_by_external_id(ext_id, source)
                except MyShowsApiError as e:
                    log_warning(f'shows.GetByExternalId({source}, {ext_id}): {e}')
                    continue
                if show:
                    shows_cache.set(cache_key, show)
            if not show:
                continue
            if strong or not expected_lower or self._show_title_ok(show, expected_lower):
                return show
            log(f'Weak id {source}:{ext_id} -> {show.get("title")!r} mismatches '
                f'{expected_lower}, ignoring', xbmc.LOGINFO)
        return None

    def _resolve_show_by_title(self, queries, expected_lower):
        for query in queries:
            try:
                results = self._api.search_shows(query) or []
            except MyShowsApiError as e:
                log_warning(f'shows.Search({query!r}): {e}')
                continue
            for show in results:
                if isinstance(show, dict) and self._show_title_ok(show, expected_lower):
                    log(f'Show resolved by title {query!r} -> id={show.get("id")}', xbmc.LOGINFO)
                    return show
        return None

    @staticmethod
    def _show_title_ok(show, expected_lower):
        names = {str(show.get('titleOriginal', '')).lower(), str(show.get('title', '')).lower()}
        return _names_match(names, expected_lower)

    def _resolve_movie(self, parts, year, want_ids=None):
        if not parts:
            return None
        want_ids = want_ids or set()
        cache_key = f'movie_q_{_norm(parts[0])}_{year}_{min(want_ids) if want_ids else 0}'
        cached = movies_cache.get(cache_key)
        if cached is not None:
            return cached or None  # cached False -> known miss

        # Collect EXACT-title candidates (dedup by id) from the first query that hits
        candidates, seen = [], set()
        for query in parts:
            try:
                results = self._api.search_movies(query) or []
            except MyShowsApiError as e:
                log_warning(f'movies.GetCatalog({query!r}): {e}')
                continue
            for entry in results:
                movie = entry.get('movie') if isinstance(entry, dict) else None
                movie = movie or entry
                if not isinstance(movie, dict):
                    continue
                mid = movie.get('id')
                if mid in seen:
                    continue
                if _names_match({movie.get('titleOriginal'), movie.get('title')}, parts):
                    seen.add(mid)
                    candidates.append(movie)
            if candidates:
                break

        match = self._pick_movie(candidates, year, want_ids) if candidates else None
        movies_cache.set(cache_key, match or False, ttl=(24 * 3600 if match else 6 * 3600))
        return match

    def _pick_movie(self, candidates, year, want_ids):
        # 1) confirm by IMDB/Kinopoisk id (strongest). A bare kino.pub id can be
        #    either kind, so test it against both id fields of each candidate.
        if want_ids:
            for c in candidates[:5]:
                full = self._movie_full(c.get('id')) or {}
                have = {parse_ext_id(full.get('imdbId')), parse_ext_id(full.get('kinopoiskId'))}
                have.discard(None)
                if want_ids & have:
                    return c
        # 2) disambiguate by year
        if year:
            for c in candidates:
                if c.get('year') == year:
                    return c
        # 3) fall back to best catalog relevance
        if len(candidates) > 1:
            log(f'Movie ambiguous, picking top hit {candidates[0].get("id")} '
                f'from {[c.get("id") for c in candidates]}', xbmc.LOGINFO)
        return candidates[0]

    def _movie_full(self, movie_id):
        if not movie_id:
            return None
        cache_key = f'moviefull_{movie_id}'
        full = movies_cache.get(cache_key)
        if full is None:
            try:
                full = self._api.movie_by_id(movie_id) or {}
                movies_cache.set(cache_key, full)
            except MyShowsApiError as e:
                log_warning(f'movies.GetById({movie_id}): {e}')
                return None
        return full

    def _find_episode(self, show_id, season, episode_num):
        cache_key = f'show_{show_id}'
        show = shows_cache.get(cache_key)
        if show is None:
            try:
                show = self._api.show_by_id(show_id, with_episodes=True) or {}
                shows_cache.set(cache_key, show)
            except MyShowsApiError as e:
                log_warning(f'shows.GetById({show_id}): {e}')
                return None
        for ep in (show.get('episodes') or []):
            if ep.get('seasonNumber') == season and ep.get('episodeNumber') == episode_num:
                return ep
        return None

    def _mark_watching_once(self, show_id):
        # Avoid re-sending "watching" for every episode of a binge session
        marker = f'watching_set_{show_id}'
        if profile_cache.get(marker):
            return
        try:
            self._api.set_show_status(show_id, 'watching')
            profile_cache.set(marker, True, ttl=6 * 3600)
        except MyShowsApiError as e:
            log_warning(f'manage.SetShowStatus({show_id}): {e}')

    # ── Scrobbling ────────────────────────────────────────────────────────

    def _do_scrobble(self):
        """Mark watched immediately; remember what to rate after the player closes."""
        self._media.scrobbled = True
        if self._media.kind == 'episode' and self._media.episode_id:
            self._scrobble_episode()
        elif self._media.kind == 'movie' and self._media.movie_id:
            self._scrobble_movie()

    def _scrobble_episode(self):
        ep_id = self._media.episode_id
        try:
            self._api.check_episode(ep_id)
        except MyShowsApiError as e:
            log_warning(f'manage.CheckEpisode({ep_id}): {e}')
            return
        _invalidate_watched(self._media.show_id)
        notify(string(32041))
        log(f'Episode {ep_id} marked as watched', xbmc.LOGINFO)
        self._rate_pending = ('episode', ep_id)

    def _scrobble_movie(self):
        movie_id = self._media.movie_id
        try:
            self._api.watch_movie(movie_id)
        except MyShowsApiError as e:
            log_warning(f'manage.SetMovieStatus({movie_id}, finished): {e}')
            return
        _invalidate_movies()
        notify(string(32042))
        log(f'Movie {movie_id} marked as watched', xbmc.LOGINFO)
        self._rate_pending = ('movie', movie_id)

    def _ask_pending_rating(self):
        pending, self._rate_pending = self._rate_pending, None
        if not pending or not get_setting_bool('show_rating_dialog'):
            return
        kind, media_id = pending
        # No blocking select() fallback here — the user may have walked away.
        rating = ask_rating(allow_fallback=False)  # player closed — shows over the UI
        if rating is None:
            return
        try:
            if kind == 'episode':
                self._api.rate_episode(media_id, rating)
            else:
                self._api.rate_movie(media_id, rating)
        except MyShowsApiError as e:
            log_warning(f'rate {kind} {media_id}: {e}')


def _invalidate_watched(show_id):
    if show_id:
        profile_cache.invalidate(f'watched_{show_id}')
    profile_cache.invalidate('shows')


def _invalidate_movies():
    profile_cache.invalidate('movies_watched')
    profile_cache.invalidate('movies_later')
