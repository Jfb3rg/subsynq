"""OpenSubtitles.com as an emergency fallback: a Swedish subtitle for EXACTLY this file, only when the user switched it on.

The user (2026-11-02): "som en absolut nödlösning" – SubSynq's own AI subtitle comes first; the other subtitle containers fetch wrong subtitles
because they search by title. Here a subtitle is taken only when it is tied to this very file:

1. the file's OpenSubtitles hash (size + first and last 64 KiB; OpenSubtitles reports `moviehash_match` for an upload made from the same
   release), or
2. the whole file name, when an upload's release name or file name is the same name (punctuation and case aside).

A title, season or episode alone is never enough. Machine and AI translations are skipped.

3. (2026-11-04) When neither matches -- a library renamed by Radarr/Sonarr ("Godzilla vs. Kong (2021) Remux-2160p.mkv") never has the
   release's own name, and remuxes are rarely hashed -- the film or episode is identified by the IMDb/TMDB id the library's own .nfo files
   give (Emby/Kodi: movie.nfo, tvshow.nfo plus the episode's season and episode), never by a title. Up to three of its Swedish subtitles
   are downloaded and each is checked against the speech in THIS file (app/subtitle_sync.py, run by the worker, which has the speech
   detector); only one whose timing fits is kept, moved by the measured constant offset or frame-rate factor. The file is written next to the video as
`<name>.opensub.sv.srt` and never replaces SubSynq's `.ai.sv.srt`. The API is api.opensubtitles.com/api/v1 (Api-Key and a descriptive
User-Agent on every call; /login gives a token and possibly another host for paying members; /subtitles searches; /download gives a link).
"""
import json
import os
import re
import struct
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SETTINGS_FILE = 'opensubtitles.json'
DEFAULT_HOST = 'api.opensubtitles.com'
SUFFIX = '.opensub.sv.srt'
HASH_CHUNK = 64 * 1024
_lock = threading.Lock()


class OpenSubtitlesError(RuntimeError):
    """A refusal or failure, with a Swedish message for the user."""


class NeedsTimingCheck(OpenSubtitlesError):
    """No exact match, but the library's .nfo identifies the film or episode: the worker checks candidates against the file's speech."""

    def __init__(self, identity):
        super().__init__('Ingen exakt träff; undertexter för ' + describe(identity) + ' kontrolleras mot ljudet i filen.')
        self.identity = identity


MAX_TIMING_CANDIDATES = 3


def _nfo_ids(path):
    """{'imdb': int, 'tmdb': int, 'season': int, 'episode': int} from an Emby/Kodi .nfo file (missing keys left out)."""
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')[:200_000]
    except OSError:
        return {}
    ids = {}
    for kind, value in re.findall(r'<uniqueid[^>]*type="(imdb|tmdb)"[^>]*>\s*([^<\s]+)\s*<', text, re.IGNORECASE):
        ids.setdefault(kind.lower(), value)
    for kind, tag in (('imdb', 'imdbid'), ('imdb', 'imdb_id'), ('tmdb', 'tmdbid')):
        match = re.search(rf'<{tag}>\s*([^<\s]+)\s*</{tag}>', text, re.IGNORECASE)
        if match:
            ids.setdefault(kind, match.group(1))
    result = {}
    imdb = re.fullmatch(r'tt(\d{5,10})', str(ids.get('imdb', '')).strip(), re.IGNORECASE)
    if imdb:
        result['imdb'] = int(imdb.group(1))
    if str(ids.get('tmdb', '')).strip().isdigit():
        result['tmdb'] = int(ids['tmdb'])
    for key in ('season', 'episode'):
        match = re.search(rf'<{key}>\s*(\d{{1,4}})\s*</{key}>', text)
        if match:
            result[key] = int(match.group(1))
    return result


def nfo_identity(media_path):
    """The film or episode as the library's own .nfo files identify it, or None.

    Episode: '<file>.nfo' gives season and episode, the nearest 'tvshow.nfo' above it the series' IMDb/TMDB id. Film: 'movie.nfo' or
    '<file>.nfo' with an IMDb/TMDB id. An episode .nfo without season and episode, or a series without an id, is no identity."""
    media = Path(media_path)
    own = _nfo_ids(media.with_suffix('.nfo'))
    show = next((ids for ids in (_nfo_ids(folder / 'tvshow.nfo') for folder in (media.parent, media.parent.parent, media.parent.parent.parent))
                 if ids.get('imdb') or ids.get('tmdb')), None)
    if show and own.get('season') is not None and own.get('episode') is not None:
        return {'kind': 'episode', 'imdb': show.get('imdb'), 'tmdb': show.get('tmdb'), 'season': own['season'], 'episode': own['episode']}
    if show:
        return None
    movie = _nfo_ids(media.parent / 'movie.nfo') if (media.parent / 'movie.nfo').is_file() else {}
    ids = movie if (movie.get('imdb') or movie.get('tmdb')) else own
    if ids.get('imdb') or ids.get('tmdb'):
        return {'kind': 'movie', 'imdb': ids.get('imdb'), 'tmdb': ids.get('tmdb')}
    return None


def describe(identity):
    name = f"tt{identity['imdb']:07d}" if identity.get('imdb') else f"TMDB {identity.get('tmdb')}"
    return (f"{name} säsong {identity['season']} avsnitt {identity['episode']}" if identity.get('kind') == 'episode' else name)


def identity_params(identity):
    if identity['kind'] == 'episode':
        base = {'parent_imdb_id': identity['imdb']} if identity.get('imdb') else {'parent_tmdb_id': identity['tmdb']}
        return {**base, 'season_number': identity['season'], 'episode_number': identity['episode']}
    return {'imdb_id': identity['imdb']} if identity.get('imdb') else {'tmdb_id': identity['tmdb']}


def _same_title(attributes, identity):
    """The result really is this film or episode (the search is by id; a result that says otherwise is dropped)."""
    details = attributes.get('feature_details') or {}
    def same(key, wanted):
        value = details.get(key)
        return wanted is None or value in (None, '') or int(value) == int(wanted)
    try:
        if identity['kind'] == 'episode':
            return (same('parent_imdb_id', identity.get('imdb')) and same('parent_tmdb_id', identity.get('tmdb'))
                    and same('season_number', identity['season']) and same('episode_number', identity['episode']))
        return same('imdb_id', identity.get('imdb')) and same('tmdb_id', identity.get('tmdb'))
    except (TypeError, ValueError):
        return False


_VERSION_WORDS = re.compile(r'(2160p|1080p|720p|576p|480p|remux|bluray|blu ray|bdrip|brrip|web dl|webdl|webrip|web|hdtv|dvdrip|dvd|proper|repack|extended|directors cut|unrated|imax)')


def timing_candidates(results, identity, media_name, *, limit=MAX_TIMING_CANDIDATES):
    """The Swedish subtitles of exactly this film/episode, the most likely version first (shared version words with the file name such
    as 1080p/BluRay/WEB, then the most downloaded), at most `limit`."""
    wanted = set(_VERSION_WORDS.findall(name_key(media_name)))
    ranked = []
    for item in results or ():
        attributes = item.get('attributes') or {}
        if not _usable(attributes) or not _same_title(attributes, identity):
            continue
        names = ' '.join(name_key(name) for name in [attributes.get('release') or ''] + [f.get('file_name') or '' for f in attributes.get('files') or ()])
        shared = len(wanted & set(_VERSION_WORDS.findall(names)))
        ranked.append((-shared, -int(attributes.get('download_count') or 0), _candidate(attributes, 'IMDb-id och tidskontroll')))
    ranked.sort(key=lambda entry: entry[:2])
    return [candidate for _, _, candidate in ranked[:limit]]


# --- settings ------------------------------------------------------------------------------------------------------------------------

def _path(config):
    return Path(config) / SETTINGS_FILE


def load_settings(config):
    """{'enabled', 'automatic', 'api_key', 'username', 'password'}; a missing or broken file is "off"."""
    try:
        data = json.loads(_path(config).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {'enabled': data.get('enabled') is True, 'automatic': data.get('automatic') is True,
            'api_key': str(data.get('api_key') or ''), 'username': str(data.get('username') or ''),
            'password': str(data.get('password') or '')}


def save_settings(config, *, enabled, automatic, api_key=None, username=None, password=None):
    """Store the choices; a secret left empty keeps the stored one (it is never shown again)."""
    current = load_settings(config)
    for name, value in (('api_key', api_key), ('username', username), ('password', password)):
        if value:
            value = value.strip() if name != 'password' else value
            if len(value) > 256 or any(ord(char) < 32 for char in value):
                raise ValueError('Ogiltigt värde för OpenSubtitles.')
            current[name] = value
    current.update(enabled=bool(enabled), automatic=bool(automatic))
    if current['enabled'] and not current['api_key']:
        raise ValueError('Ange API-nyckeln från OpenSubtitles för att slå på nödlösningen.')
    path = _path(config)
    descriptor, temporary = tempfile.mkstemp(prefix='.opensubtitles-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(current, output)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    with _lock:
        _tokens.clear()
    return current


def forget_settings(config):
    _path(config).unlink(missing_ok=True)
    with _lock:
        _tokens.clear()


# --- matching ------------------------------------------------------------------------------------------------------------------------

def movie_hash(path):
    """The OpenSubtitles hash: the file size plus the 64-bit little-endian words of its first and last 64 KiB, as 16 hex digits."""
    path = Path(path)
    size = path.stat().st_size
    if size < 2 * HASH_CHUNK:
        raise OpenSubtitlesError('Filen är för liten för OpenSubtitles filkontrollsumma.')
    value = size
    with path.open('rb') as handle:
        for offset in (0, size - HASH_CHUNK):
            handle.seek(offset)
            for (word,) in struct.iter_unpack('<Q', handle.read(HASH_CHUNK)):
                value = (value + word) & 0xFFFFFFFFFFFFFFFF
    return f'{value:016x}'


def name_key(name):
    """A file or release name without extension, case and separators: "Film.2024.1080p.WEB-DL" == "film 2024 1080p web dl"."""
    name = re.sub(r'\.(?:mkv|mp4|avi|m4v|mov|ts|m2ts|wmv|srt|sub|ass|ssa)$', '', str(name).strip(), flags=re.IGNORECASE)
    return ' '.join(re.split(r'[\W_]+', name.casefold())).strip()


def _usable(attributes):
    return (not attributes.get('machine_translated') and not attributes.get('ai_translated')
            and str(attributes.get('language') or '').lower() in ('sv', 'swe') and attributes.get('files'))


def choose(results_by_hash, results_by_name, media_name):
    """(candidate, how) from the two searches, or (None, why). `how` is 'filkontrollsumma' or 'filnamn'."""
    for item in results_by_hash or ():
        attributes = item.get('attributes') or {}
        if _usable(attributes) and attributes.get('moviehash_match') is True:
            return _candidate(attributes, 'filkontrollsumma'), None
    wanted = name_key(media_name)
    for item in results_by_name or ():
        attributes = item.get('attributes') or {}
        if not _usable(attributes):
            continue
        names = [attributes.get('release') or ''] + [file.get('file_name') or '' for file in attributes.get('files') or ()]
        if wanted and any(name_key(name) == wanted for name in names if name):
            return _candidate(attributes, 'filnamn'), None
    return None, ('Ingen svensk undertext på OpenSubtitles är kopplad till exakt den här filen '
                  '(varken filkontrollsumman eller hela filnamnet stämmer). Ingen titel-gissning görs.')


def _candidate(attributes, how):
    first = attributes['files'][0]
    return {'file_id': first.get('file_id'), 'release': attributes.get('release') or first.get('file_name') or '', 'how': how,
            'url': attributes.get('url') or '', 'hearing_impaired': bool(attributes.get('hearing_impaired'))}


# --- API -----------------------------------------------------------------------------------------------------------------------------

_tokens = {}   # username -> (token, host)


class Client:
    def __init__(self, settings, user_agent, *, opener=None):
        self.settings = settings
        self.user_agent = user_agent
        self.open = opener or (lambda request, timeout: urllib.request.urlopen(request, timeout=timeout))
        self.host = DEFAULT_HOST
        self.token = None

    def _call(self, method, path, *, params=None, body=None, retry=True):
        url = f'https://{self.host}/api/v1/{path}' + (f'?{urllib.parse.urlencode(params)}' if params else '')
        headers = {'Api-Key': self.settings['api_key'], 'User-Agent': self.user_agent, 'Accept': 'application/json'}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.open(request, 30) as response:
                return json.loads(response.read().decode('utf-8') or '{}')
        except urllib.error.HTTPError as error:
            if error.code == 401 and retry and self.settings.get('username'):
                with _lock:
                    _tokens.pop(self.settings['username'], None)
                self.token = None
                self.login()
                return self._call(method, path, params=params, body=body, retry=False)
            raise OpenSubtitlesError(_http_message(error.code, path)) from error
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise OpenSubtitlesError(f'OpenSubtitles svarade inte ({error.__class__.__name__}).') from error

    def login(self):
        """A token for downloads (and the host OpenSubtitles names for this member); without a username the API key alone is used."""
        username = self.settings.get('username')
        if not username:
            return
        with _lock:
            cached = _tokens.get(username)
        if cached:
            self.token, self.host = cached
            return
        answer = self._call('POST', 'login', body={'username': username, 'password': self.settings.get('password') or ''}, retry=False)
        self.token = answer.get('token')
        host = str(answer.get('base_url') or DEFAULT_HOST).replace('https://', '').strip('/')
        self.host = host if re.fullmatch(r'[a-z0-9.-]+\.opensubtitles\.com', host) else DEFAULT_HOST
        if not self.token:
            raise OpenSubtitlesError('Inloggningen på OpenSubtitles gav ingen nyckel; kontrollera användarnamn och lösenord.')
        with _lock:
            _tokens[username] = (self.token, self.host)

    def search(self, **params):
        return (self._call('GET', 'subtitles', params={**params, 'languages': 'sv'}) or {}).get('data') or []

    def download(self, file_id):
        """(subtitle bytes, downloads left today or None)."""
        answer = self._call('POST', 'download', body={'file_id': file_id, 'sub_format': 'srt'})
        link = answer.get('link')
        if not link or not str(link).startswith('https://'):
            raise OpenSubtitlesError('OpenSubtitles gav ingen nedladdningslänk.')
        request = urllib.request.Request(link, headers={'User-Agent': self.user_agent})
        try:
            with self.open(request, 60) as response:
                content = response.read(5 * 1024 * 1024 + 1)
        except (urllib.error.URLError, OSError) as error:
            raise OpenSubtitlesError('Undertexten från OpenSubtitles kunde inte hämtas.') from error
        if len(content) > 5 * 1024 * 1024:
            raise OpenSubtitlesError('Filen från OpenSubtitles är orimligt stor och sparades inte.')
        return content, answer.get('remaining')


def _http_message(code, path):
    return {401: 'OpenSubtitles nekade inloggningen: kontrollera API-nyckel, användarnamn och lösenord.',
            403: 'OpenSubtitles nekade åtkomst med den här API-nyckeln.',
            406: 'Dagens gräns för nedladdningar hos OpenSubtitles är nådd.',
            429: 'För många anrop till OpenSubtitles just nu; försök igen om en stund.'}.get(
        code, f'OpenSubtitles svarade med fel {code} ({path}).')


# --- the whole step ------------------------------------------------------------------------------------------------------------------

def subtitle_text(content):
    """The downloaded bytes as UTF-8 text, or an error when they are not an SRT subtitle."""
    for encoding in ('utf-8-sig', 'cp1252'):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise OpenSubtitlesError('Undertexten från OpenSubtitles har en okänd teckenkodning.')
    if '-->' not in text or not re.search(r'\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->', text):
        raise OpenSubtitlesError('Filen från OpenSubtitles är ingen SRT-undertext.')
    return text.replace('\r\n', '\n')


def mark_in_library(database, media_path):
    """The stored library row of a file that just got a subtitle from OpenSubtitles shows it at once (no scan needed). Never raises:
    the subtitle is saved either way and the next scan sees it."""
    import sqlite3
    try:
        with sqlite3.connect(database, timeout=10) as db:
            row = db.execute("SELECT value FROM metadata WHERE key='library_scan'").fetchone()
            if not row:
                return
            library = json.loads(row[0])
            for item in library.get('items') or []:
                if item.get('path') == str(media_path):
                    item['opensub_sv_exists'] = True
                    db.execute("INSERT OR REPLACE INTO metadata VALUES ('library_scan', ?)", (json.dumps(library),))
                    return
    except (sqlite3.Error, ValueError, TypeError):
        return


def target_path(media_path):
    media = Path(media_path)
    return media.with_name(media.stem + SUFFIX)


def fetch(config, media_path, user_agent, *, opener=None, speech=None):
    """Find, download and write the subtitle for exactly this file. Returns {'path', 'how', 'release', 'remaining'}; raises
    OpenSubtitlesError (with the reason) when nothing safe was found or something failed. Never touches any other subtitle.

    `speech(media) -> (speech intervals, windows)` is the worker's speech detector; without it a file that needs the timing check
    raises NeedsTimingCheck, so the web server can hand it to the worker."""
    settings = load_settings(config)
    if not settings['enabled'] or not settings['api_key']:
        raise OpenSubtitlesError('Nödlösningen med OpenSubtitles är inte påslagen under Inställningar.')
    media = Path(media_path)
    if not media.is_file():
        raise OpenSubtitlesError('Videofilen finns inte.')
    client = Client(settings, user_agent, opener=opener)
    client.login()
    by_hash = client.search(moviehash=movie_hash(media))
    by_name = [] if any((item.get('attributes') or {}).get('moviehash_match') is True for item in by_hash) else client.search(query=media.stem)
    candidate, why = choose(by_hash, by_name, media.name)
    timing = None
    if candidate is None:
        identity = nfo_identity(media)
        if identity is None:
            raise OpenSubtitlesError(why + ' Biblioteket har ingen .nfo med IMDb-id för filen, så ingen tidskontroll kunde göras.')
        if speech is None:
            raise NeedsTimingCheck(identity)
        candidates = timing_candidates(client.search(**identity_params(identity)), identity, media.name)
        if not candidates:
            raise OpenSubtitlesError(f'Ingen svensk undertext på OpenSubtitles för {describe(identity)}.')
        from subtitle_sync import shift, verdict
        intervals, windows = speech(media)
        tried, remaining = [], None
        for option in candidates:
            content, remaining = client.download(option['file_id'])
            try:
                text = subtitle_text(content)
            except OpenSubtitlesError:
                tried.append(f"{option['release'] or 'okänd utgåva'} (ingen SRT)")
                continue
            result = verdict(text, intervals, windows)
            if result['fits']:
                candidate, timing = option, result
                text = shift(text, result['offset'], result['scale'])
                break
            tried.append(f"{option['release'] or 'okänd utgåva'} (tiderna passar inte)")
        if candidate is None:
            raise OpenSubtitlesError(f"{len(tried)} svenska undertexter för {describe(identity)} provades men ingen passade tiderna i just den "
                                     f"här filen; ingen sparades. Provade: {'; '.join(tried)[:300]}"
                                     + (f' · {remaining} nedladdningar kvar i dag' if remaining is not None else ''))
    else:
        content, remaining = client.download(candidate['file_id'])
        text = subtitle_text(content)
    target = target_path(media)
    temporary = target.with_name(f'.{target.name}.{os.getpid()}.tmp')
    temporary.write_text(text, encoding='utf-8')
    os.replace(temporary, target)
    how = candidate['how']
    if timing and (timing['offset'] or timing['scale'] != 1.0):
        how += f" (flyttad {timing['offset']:+.1f} s" + (f", hastighet {timing['scale']:.3f}" if timing['scale'] != 1.0 else '') + ')'
    return {'path': str(target), 'how': how, 'release': candidate['release'], 'remaining': remaining, 'at': time.time(), 'timing': timing}


# --- web ------------------------------------------------------------------------------------------------------------------------------

def render_card(settings, token, notice=''):
    """Inställningar › Allmänt: switched off by default; secrets are never shown again."""
    from html import escape
    checked = lambda value: ' checked' if value else ''
    stored = lambda value: 'sparad' if value else 'inte angiven'
    token_field = f"<input type='hidden' name='token' value='{escape(token or '', quote=True)}'>"
    return (
        "<div class='card' id='opensubtitles'><h2>Nödlösning: OpenSubtitles</h2>"
        + (f"<p class='success' role='status'>{escape(notice)}</p>" if notice else '')
        + "<p class='muted'>Bara när SubSynqs egen AI-undertext inte går att göra. En svensk undertext hämtas endast om den hör till "
          "exakt den här filen: filens kontrollsumma, hela filnamnet (utgåvenamnet), eller filmens/avsnittets IMDb-id från bibliotekets "
          ".nfo-filer och sedan en kontroll av att replikernas tider stämmer med talet i just din fil (högst tre provas, en förskjuten "
          "undertext flyttas på plats). Titel, säsong och avsnitt räcker aldrig. "
          "Maskin- och AI-översatta undertexter tas inte. Filen sparas som <code>namn.opensub.sv.srt</code> och ersätter aldrig "
          "AI-undertexten.</p>"
        "<form action='/settings/opensubtitles' method='post' autocomplete='off'>" + token_field +
        "<input type='hidden' name='action' value='save'>"
        f"<label class='checkbox-row'><input type='checkbox' name='enabled' value='1'{checked(settings['enabled'])}>"
        "<span>Slå på nödlösningen</span></label>"
        f"<label class='checkbox-row'><input type='checkbox' name='automatic' value='1'{checked(settings['automatic'])}>"
        "<span>Hämta automatiskt när ett AI-jobb underkänns</span></label>"
        "<p class='muted'>Utan den här rutan hämtas bara när du trycker <strong>Hämta från OpenSubtitles</strong> vid en underkänd "
        "körning i kön.</p>"
        f"<label>API-nyckel <small class='muted'>({stored(settings['api_key'])})</small><br>"
        "<input type='password' name='api_key' autocomplete='off' style='width:100%;max-width:420px' placeholder='lämna tomt för att behålla'></label><br>"
        f"<label>Användarnamn <small class='muted'>({stored(settings['username'])})</small><br>"
        "<input type='text' name='username' autocomplete='off' style='width:100%;max-width:420px' placeholder='lämna tomt för att behålla'></label><br>"
        f"<label>Lösenord <small class='muted'>({stored(settings['password'])})</small><br>"
        "<input type='password' name='password' autocomplete='new-password' style='width:100%;max-width:420px' placeholder='lämna tomt för att behålla'></label>"
        "<p class='muted'>Nyckeln skapas under API consumers på opensubtitles.com. Med användarnamn och lösenord gäller ditt kontos "
        "nedladdningsgräns. Uppgifterna sparas bara i /config och visas aldrig igen.</p>"
        "<div style='display:flex;flex-wrap:wrap;gap:8px'><button type='submit' class='small-button'>Spara</button></div></form>"
        "<div style='display:flex;flex-wrap:wrap;gap:8px;margin-top:8px'>"
        "<form action='/settings/opensubtitles' method='post'>" + token_field +
        "<input type='hidden' name='action' value='test'><button type='submit' class='small-button'>Testa inloggningen</button></form>"
        "<form action='/settings/opensubtitles' method='post' onsubmit=\"return confirm('Ta bort nyckel, användarnamn och lösenord för "
        "OpenSubtitles?')\">" + token_field +
        "<input type='hidden' name='action' value='forget'><button type='submit' class='small-button danger'>Ta bort uppgifterna</button>"
        "</form></div></div>")


def test_login(config, user_agent, *, opener=None):
    """A login (when a username is stored) and a search that downloads nothing: proves the key and the account work."""
    settings = load_settings(config)
    if not settings['api_key']:
        raise OpenSubtitlesError('Ingen API-nyckel är sparad.')
    client = Client(settings, user_agent, opener=opener)
    client.login()
    client.search(query='test')
    return ('Inloggningen fungerar' if settings['username'] else 'API-nyckeln fungerar (inget användarnamn: anonyma gränser gäller)') + \
           f' · server {client.host}.'


_enabled_cache = {}


def enabled(config):
    """Whether the fallback is switched on; read again only when the settings file changes (the library renders thousands of rows)."""
    path = _path(config)
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return False
    cached = _enabled_cache.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1]
    value = load_settings(config)['enabled']
    _enabled_cache[str(path)] = (stamp, value)
    return value


def render_button(media_path, token, *, return_kind='', return_show=''):
    """The "Hämta från OpenSubtitles" button for one file (queue history and the library lists)."""
    from html import escape
    return ("<form action='/jobs/opensubtitles' method='post' class='preserve-seasons' "
            "onsubmit=\"return confirm('Hämta en svensk undertext från OpenSubtitles för exakt den här filen? Det räknas mot din "
            "nedladdningsgräns där.')\">"
            f"<input type='hidden' name='token' value='{escape(token or '', quote=True)}'>"
            f"<input type='hidden' name='media_path' value='{escape(media_path or '', quote=True)}'>"
            + (f"<input type='hidden' name='return_kind' value='{escape(return_kind, quote=True)}'>" if return_kind else '')
            + (f"<input type='hidden' name='return_show' value='{escape(return_show, quote=True)}'>" if return_show else '')
            + "<button type='submit' class='small-button'>Hämta från OpenSubtitles</button></form>")
