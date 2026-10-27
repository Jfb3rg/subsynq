"""A cloud session: the user rents one server with the Start button, runs as many jobs as they like on it and ends it with Stop.

Every ordinary cloud job rents a server and waits 10-20 minutes while about 20 GB of models are downloaded (traffic is billed: 0.8-1.2 USD a
start). A session pays that once. The web page only writes a request; the worker rents, keeps and destroys the server (the same rental code
as a job, ai/worker.py `run_cloud_session`). The server is destroyed when the user presses Stop, after IDLE_SECONDS with no job, after a
technical fault in a job, and when the worker stops or restarts. The state lives in the `metadata` table so the web page and the worker share it.
"""
import json
import sqlite3
import time
from html import escape

STATE_KEY = 'cloud_session'
REQUEST_KEY = 'cloud_session_request'
JOB_ID = -1                     # the "job" id the rental code reports to while a session starts (ai/worker.py update_job, check_cancelled)
IDLE_SECONDS = 3600             # the user: "är den inaktiv i 1 timme så stängs moln gpun automatiskt av"
ACTIVE = ('starting', 'ready')


def load(database):
    """The session state: {'state': 'off' | 'starting' | 'ready' | 'failed', 'message', 'since', 'ready_at', 'last_activity', 'price', ...}."""
    try:
        with sqlite3.connect(database, timeout=10) as db:
            row = db.execute('SELECT value FROM metadata WHERE key=?', (STATE_KEY,)).fetchone()
        state = json.loads(row[0]) if row and row[0] else {}
    except (sqlite3.Error, ValueError, TypeError):
        state = {}
    return state if isinstance(state, dict) and state.get('state') else {'state': 'off'}


def save(database, state):
    with sqlite3.connect(database, timeout=10) as db:
        db.execute('INSERT OR REPLACE INTO metadata VALUES (?, ?)', (STATE_KEY, json.dumps(state, ensure_ascii=False)))
    return state


def update(database, **fields):
    state = load(database)
    state.update(fields)
    return save(database, state)


def request(database, action):
    """The web page asks the worker to start or stop the session."""
    if action not in ('start', 'stop'):
        raise ValueError('Okänd begäran för molnsessionen.')
    with sqlite3.connect(database, timeout=10) as db:
        db.execute('INSERT OR REPLACE INTO metadata VALUES (?, ?)', (REQUEST_KEY, action))
    state = load(database)
    if action == 'start' and state.get('state') not in ACTIVE:
        save(database, {'state': 'starting', 'message': 'Väntar på att arbetaren tar emot startbegäran', 'since': time.time()})
    elif action == 'stop' and state.get('state') in ACTIVE:
        update(database, stop_requested=True)


def pending(database):
    try:
        with sqlite3.connect(database, timeout=10) as db:
            row = db.execute('SELECT value FROM metadata WHERE key=?', (REQUEST_KEY,)).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def take(database):
    """The pending request (and removes it), or None."""
    action = pending(database)
    if action:
        with sqlite3.connect(database, timeout=10) as db:
            db.execute('DELETE FROM metadata WHERE key=?', (REQUEST_KEY,))
    return action


def idle_seconds(state, now=None):
    return max(0.0, (now or time.time()) - float(state.get('last_activity') or state.get('ready_at') or state.get('since') or 0))


def _clock(stamp):
    return time.strftime('%H:%M', time.localtime(stamp)) if stamp else ''


def render(state, preference, token, now=None):
    """The card on the overview and queue pages: the state, what it has cost so far and the Start or Stop button."""
    now = now or time.time()
    cloud = str(preference or '').startswith('cloud-')
    status = state.get('state', 'off')
    lines = []
    if status in ACTIVE and state.get('stop_requested'):
        lines.append('Stopp begärt: servern stängs så snart arbetaren är ledig (ett pågående jobb körs klart först).')
    if status == 'ready':
        minutes = int((now - float(state.get('ready_at') or state.get('since') or now)) // 60)
        lines.append(f"Redo sedan {_clock(state.get('ready_at'))} · {state.get('gpu') or 'server'}"
                     + (f" · {state['location']}" if state.get('location') else '') + f' · {minutes} min')
        price = state.get('price')
        if isinstance(price, (int, float)) and price > 0:
            hours = (now - float(state.get('since') or now)) / 3600
            lines.append(f'Kostnad hittills ca {price * hours:.2f} USD ({price:.3f} USD/h, plus nedladdningen vid starten)')
        closes = float(state.get('last_activity') or state.get('ready_at') or now) + IDLE_SECONDS
        lines.append(f'Stängs automatiskt kl {_clock(closes)} om inget jobb körs innan dess (1 timme utan jobb)')
        lines.append('Jobb du startar nu använder servern direkt, utan ny nedladdning.')
    elif status == 'starting':
        lines.append(f"Startar: {state.get('message') or 'hyr en server'}")
        lines.append('Modellerna laddas ner en gång (10–20 minuter). Du kan stoppa när som helst.')
    else:
        if state.get('message'):
            lines.append(str(state['message']))
        lines.append('Start hyr en molnserver och laddar ner modellerna en gång; den ligger kvar tills du trycker Stopp '
                     'eller i 1 timme utan jobb.' if cloud else 'Välj Moln-GPU under Hårdvara för att kunna starta en molnsession.')
    body = ''.join(f"<p class='muted' style='margin:4px 0'>{escape(line)}</p>" for line in lines)
    active = status in ACTIVE
    action, label = ('stop', 'Stoppa molnsession') if active else ('start', 'Starta molnsession')
    confirm = ('Stoppa molnsessionen och stäng servern?' if active
               else 'Hyr en molnserver nu? Den kostar per timme tills du trycker Stopp (eller 1 timme utan jobb).')
    button = ''
    if active or cloud:
        button = (f"<form action='/cloud/session/{action}' method='post' onsubmit=\"return confirm('{escape(confirm, quote=True)}')\">"
                  f"<input type='hidden' name='token' value='{escape(token or '', quote=True)}'>"
                  f"<button type='submit' class='small-button{' danger' if active else ''}'>{escape(label)}</button></form>")
    pill = {'ready': ('ok', 'Igång'), 'starting': ('neutral', 'Startar'), 'failed': ('dg', 'Misslyckades')}.get(status, ('neutral', 'Av'))
    return (f"<div class='card'><h2>Molnsession <span class='pill {pill[0]}'>{escape(pill[1])}</span></h2>{body}"
            f"<div style='margin-top:10px'>{button}</div></div>")
