'''
Talks to the Echo website. Off unless ECHO_API_URL and ECHO_SERVER_KEY are set,
in which case players are Echo accounts: their player code maps to an Echo
username and user ID, and unknown codes can't join.
'''
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request

API_URL = os.environ.get('ECHO_API_URL', '').rstrip('/')
SERVER_KEY = os.environ.get('ECHO_SERVER_KEY', '')

_players: dict[str, dict | None] = {}
_lock = threading.Lock()


def enabled() -> bool:
    return bool(API_URL and SERVER_KEY)


def _request(path: str, payload: dict | None = None) -> urllib.request.Request:
    return urllib.request.Request(
        API_URL + path,
        data=None if payload is None else json.dumps(payload).encode('utf-8'),
        headers={'content-type': 'application/json', 'x-echo-server-key': SERVER_KEY},
        method='GET' if payload is None else 'POST',
    )


def lookup(code: str) -> dict | None:
    '''The Echo account for a player code ({id, username, membership}), or None if there isn't one.'''
    if not enabled():
        return None
    with _lock:
        if code in _players:
            return _players[code]
    try:
        with urllib.request.urlopen(_request('/api/game/player?code=' + urllib.parse.quote(code)), timeout=10) as r:
            player = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        player = None
    # only answers are cached; a network error is raised so the join simply fails and can be retried
    with _lock:
        _players[code] = player
    return player


def is_allowed(code: str) -> bool:
    return not enabled() or lookup(code) is not None


def report_playtime(code: str, seconds: int) -> None:
    try:
        urllib.request.urlopen(_request('/api/game/playtime', {'user_code': code, 'seconds': seconds}), timeout=10).close()
    except Exception as e:
        print(f'[Echo] playtime report failed: {e}')
