import json
import time
import xbmc
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import URLError, HTTPError

import store
import utils

RPC_URL = 'https://api.myshows.me/v2/rpc/'
# Movies live only on the v3 endpoint, which authenticates via a non-standard
# "authorization2: Bearer" header (plain "Authorization" is rejected there).
# The same apidoc OAuth token works on both v2 and v3.
RPC_URL_V3 = 'https://api.myshows.me/v3/rpc/'
OAUTH_URL = 'https://myshows.me/oauth/token'
# Public demo OAuth client documented by MyShows (the same one the Jellyfin
# plugin uses) — these are not secrets.
CLIENT_ID = 'apidoc'
CLIENT_SECRET = 'apidoc'
USER_AGENT = 'Kodi/MyShows.me plugin'
TIMEOUT = 15


class MyShowsApiError(Exception):
    pass


class MyShowsApi:
    def __init__(self):
        self._request_id = 0
        self.reload_tokens()

    def _next_id(self):
        self._request_id += 1
        return self._request_id

    # ── Tokens / auth ─────────────────────────────────────────────────────

    def reload_tokens(self):
        """Re-read tokens from settings.

        Tokens are shared between two long-lived processes (UI plugin and
        scrobbler service) through addon settings; re-reading before use keeps
        both sides current after either one logs in or refreshes.
        """
        self._access_token = store.get('access_token')
        self._refresh_token = store.get('refresh_token')
        try:
            self._token_expires = float(store.get('token_expires') or '0')
        except ValueError:
            self._token_expires = 0.0

    def is_authenticated(self):
        return bool(self._access_token)

    def ensure_authenticated(self):
        """Make sure we hold a usable access token; True on success.

        Order: fresh-enough token -> refresh -> password login with stored
        credentials. Safe to call often — does network only when needed.
        """
        self.reload_tokens()
        if self._access_token and time.time() < self._token_expires - 60:
            return True
        if self._refresh_token and self._try_refresh():
            return True
        return self._try_password_login()

    def login(self, username, password):
        data = {
            'grant_type': 'password',
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'username': username,
            'password': password,
        }
        try:
            result = self._make_oauth_request(data)
        except HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')
            raise MyShowsApiError(f'HTTP {e.code}: {body[:200]}')
        except URLError as e:
            raise MyShowsApiError(str(e.reason))
        self._save_tokens(result)
        return True

    def logout(self):
        self._access_token = ''
        self._refresh_token = ''
        self._token_expires = 0.0
        store.delete('access_token', 'refresh_token', 'token_expires', 'user_login')

    def _try_refresh(self):
        data = {
            'grant_type': 'refresh_token',
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'refresh_token': self._refresh_token,
        }
        try:
            self._save_tokens(self._make_oauth_request(data))
            return True
        except (MyShowsApiError, HTTPError, URLError, OSError) as e:
            xbmc.log(f'[MyShows] Token refresh failed: {e}', xbmc.LOGWARNING)
            return False

    def _try_password_login(self):
        username = utils.get_setting('username')
        password = utils.get_setting('password')
        if not username or not password:
            return False
        try:
            self.login(username, password)
            xbmc.log('[MyShows] Re-login with stored credentials succeeded', xbmc.LOGINFO)
            return True
        except MyShowsApiError as e:
            xbmc.log(f'[MyShows] Re-login failed: {e}', xbmc.LOGWARNING)
            return False

    def _make_oauth_request(self, data):
        body = urlencode(data).encode('utf-8')
        req = Request(OAUTH_URL, data=body, method='POST')
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        req.add_header('User-Agent', USER_AGENT)
        with urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode('utf-8')
        try:
            return json.loads(raw)
        except ValueError:
            raise MyShowsApiError('Invalid response from auth server')

    def _save_tokens(self, token_data):
        if 'access_token' not in token_data:
            raise MyShowsApiError(f'No access_token in response: {str(token_data)[:200]}')
        self._access_token = token_data['access_token']
        self._refresh_token = token_data.get('refresh_token', self._refresh_token)
        self._token_expires = time.time() + token_data.get('expires_in', 3600)
        store.set('access_token', self._access_token)
        store.set('refresh_token', self._refresh_token)
        store.set('token_expires', str(self._token_expires))

    # ── Transport ─────────────────────────────────────────────────────────

    def _rpc(self, method, params=None, v3=False, _retried=False):
        if not _retried:
            self.ensure_authenticated()
        payload = {
            'jsonrpc': '2.0',
            'method': method,
            'params': params or {},
            'id': self._next_id(),
        }
        url = RPC_URL_V3 if v3 else RPC_URL
        req = Request(url, data=json.dumps(payload).encode('utf-8'), method='POST')
        req.add_header('Content-Type', 'application/json')
        req.add_header('User-Agent', USER_AGENT)
        if self._access_token:
            # v2 reads Authorization; v3 reads authorization2. Sending both lets
            # one code path serve either endpoint — each ignores the other header.
            req.add_header('Authorization', f'Bearer {self._access_token}')
            req.add_header('authorization2', f'Bearer {self._access_token}')
            req.add_header('platform', 'desktop')

        try:
            with urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read().decode('utf-8')
        except HTTPError as e:
            # Token revoked/expired server-side: refresh or re-login, then retry once
            if e.code == 401 and not _retried and (self._try_refresh() or self._try_password_login()):
                return self._rpc(method, params, v3=v3, _retried=True)
            body = e.read().decode('utf-8', errors='replace')
            xbmc.log(f'[MyShows] RPC HTTP {e.code} for {method}: {body[:500]}', xbmc.LOGWARNING)
            raise MyShowsApiError(f'HTTP {e.code}')
        except URLError as e:
            raise MyShowsApiError(f'Network error: {e.reason}')

        try:
            data = json.loads(raw)
        except ValueError:
            raise MyShowsApiError(f'Invalid JSON response for {method}')

        if 'error' in data:
            err = data['error']
            msg = err.get('message', 'Unknown error') if isinstance(err, dict) else str(err)
            # v3 reports an expired/invalid token in the JSON-RPC body (HTTP 200)
            if isinstance(err, dict) and err.get('code') == 401 and not _retried \
                    and (self._try_refresh() or self._try_password_login()):
                return self._rpc(method, params, v3=v3, _retried=True)
            raise MyShowsApiError(msg)

        return data.get('result')

    # ── Profile ──────────────────────────────────────────────────────────

    def profile_get(self):
        return self._rpc('profile.Get', {})

    def profile_shows(self, login=None):
        params = {}
        if login:
            params['login'] = login
        return self._rpc('profile.Shows', params)

    def profile_watched_movies(self, page=0, page_size=100):
        return self._rpc('profile.WatchedMovies',
                         {'search': {}, 'page': page, 'pageSize': page_size}, v3=True)

    def profile_unwatched_movies(self, page=0, page_size=100):
        """Movies the user marked as 'will watch' (later)."""
        return self._rpc('profile.UnwatchedMovies',
                         {'search': {}, 'page': page, 'pageSize': page_size}, v3=True)

    def profile_episodes(self, show_id):
        """Watched episodes of a show for the current user."""
        return self._rpc('profile.Episodes', {'showId': int(show_id)})

    def unwatched_episodes(self, show_id=None):
        """Aired-but-unwatched episodes, grouped by show ({show, episodes[]})."""
        params = {}
        if show_id:
            params['showId'] = int(show_id)
        return self._rpc('lists.EpisodesUnwatched', params, v3=True)

    def profile_show_statuses(self, show_ids):
        return self._rpc('profile.ShowStatuses', {'showIds': show_ids})

    # ── Shows ─────────────────────────────────────────────────────────────

    def show_by_external_id(self, ext_id, source):
        """source: 'imdb', 'thetvdb', 'tvmaze', 'tvrage'"""
        return self._rpc('shows.GetByExternalId', {'id': int(ext_id), 'source': source})

    def show_by_id(self, show_id, with_episodes=True):
        return self._rpc('shows.GetById', {'showId': int(show_id), 'withEpisodes': with_episodes})

    def search_shows(self, query):
        # shows.Search takes a plain query string; shows.Get expects a {search:{...}} object
        return self._rpc('shows.Search', {'query': query})

    def shows_top(self, page=0, page_size=20):
        return self._rpc('shows.Top', {'page': page, 'pageSize': page_size})

    # ── Movies (v3 endpoint) ──────────────────────────────────────────────
    # The v2 API has no movie methods; everything below uses v3.

    def movie_by_id(self, movie_id):
        return self._rpc('movies.GetById', {'movieId': int(movie_id)}, v3=True)

    def search_movies(self, query, page=0, page_size=30):
        return self._rpc('movies.GetCatalog',
                         {'search': {'query': query}, 'page': page, 'pageSize': page_size}, v3=True)

    # ── Manage – Episodes ─────────────────────────────────────────────────

    def check_episode(self, episode_id):
        return self._rpc('manage.CheckEpisode', {'id': int(episode_id)})

    def uncheck_episode(self, episode_id):
        return self._rpc('manage.UnCheckEpisode', {'id': int(episode_id)})

    def rate_episode(self, episode_id, rating):
        return self._rpc('manage.RateEpisode', {'id': int(episode_id), 'rating': int(rating)})

    def sync_episodes_delta(self, show_id, checked_ids=None, unchecked_ids=None):
        return self._rpc('manage.SyncEpisodesDelta', {
            'showId': int(show_id),
            'checkedIds': [int(i) for i in (checked_ids or [])],
            'unCheckedIds': [int(i) for i in (unchecked_ids or [])],
        })

    # ── Manage – Shows ────────────────────────────────────────────────────

    def set_show_status(self, show_id, status):
        """status: 'watching', 'later', 'cancelled', 'remove' (per the API SMD)"""
        return self._rpc('manage.SetShowStatus', {'id': int(show_id), 'status': status})

    def rate_show(self, show_id, rating):
        return self._rpc('manage.RateShow', {'id': int(show_id), 'rating': int(rating)})

    # ── Manage – Movies (v3 endpoint) ─────────────────────────────────────

    # MyShows movie watch statuses: 'finished' = watched, 'later' = will watch,
    # 'remove' = remove from lists (verified live; '' returns "invalid status").
    # There is no separate watch/unwatch method.
    def set_movie_status(self, movie_id, status):
        return self._rpc('manage.SetMovieStatus', {'movieId': int(movie_id), 'status': status}, v3=True)

    def watch_movie(self, movie_id):
        return self.set_movie_status(movie_id, 'finished')

    def unwatch_movie(self, movie_id):
        return self.set_movie_status(movie_id, 'remove')

    def rate_movie(self, movie_id, rating):
        return self._rpc('manage.RateMovie', {'movieId': int(movie_id), 'rating': int(rating)}, v3=True)
