import json
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app import db
from app.models import Artist, PlayEvent, SpotifyToken, SyncLog, Track

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
RECENTLY_PLAYED_URL = "https://api.spotify.com/v1/me/player/recently-played"
FOLLOWING_URL = "https://api.spotify.com/v1/me/following"
PLAYLISTS_URL = "https://api.spotify.com/v1/me/playlists"

MAX_LIBRARY_ITEMS = 200


class SpotifyScopeError(Exception):
    """Raised when the stored token doesn't have the scope required for a call."""


def build_authorize_url(client_id, redirect_uri, scope, state):
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_token(code, client_id, client_secret, redirect_uri):
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        auth=(client_id, client_secret),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def save_token(payload):
    expires_at = datetime.utcnow() + timedelta(seconds=payload["expires_in"] - 30)
    token = SpotifyToken.query.first()
    if token is None:
        token = SpotifyToken(
            access_token=payload["access_token"],
            refresh_token=payload["refresh_token"],
            expires_at=expires_at,
        )
        db.session.add(token)
    else:
        token.access_token = payload["access_token"]
        if payload.get("refresh_token"):
            token.refresh_token = payload["refresh_token"]
        token.expires_at = expires_at
    db.session.commit()
    return token


def get_valid_access_token(client_id, client_secret):
    """Returns a valid access token for the connected account, refreshing it
    if expired. Returns None if no account is connected yet."""

    token = SpotifyToken.query.first()
    if token is None:
        return None

    if token.expires_at > datetime.utcnow():
        return token.access_token

    resp = requests.post(
        TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": token.refresh_token},
        auth=(client_id, client_secret),
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()
    payload.setdefault("refresh_token", token.refresh_token)
    save_token(payload)
    return payload["access_token"]


def _parse_played_at(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)


def _record_sync_log(raw_json, items_fetched, plays_inserted):
    db.session.add(
        SyncLog(
            raw_response=json.dumps(raw_json),
            items_fetched=items_fetched,
            plays_inserted=plays_inserted,
        )
    )
    db.session.commit()


def sync_recent_plays(client_id, client_secret):
    """Fetches the last ~50 played tracks from the Spotify Web API and stores
    any that aren't already in the database. Returns a stats dict, or None if
    no account is connected."""

    access_token = get_valid_access_token(client_id, client_secret)
    if access_token is None:
        return None

    resp = requests.get(
        RECENTLY_PLAYED_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        params={"limit": 50},
        timeout=10,
    )
    resp.raise_for_status()
    raw_json = resp.json()
    items = raw_json.get("items", [])

    entries = []
    for item in items:
        track = item.get("track") or {}
        artists = track.get("artists") or []
        if not track.get("name") or not artists or not item.get("played_at"):
            continue
        entries.append(
            {
                "played_at": _parse_played_at(item["played_at"]),
                "ms_played": track.get("duration_ms", 0),
                "track_name": track["name"],
                "artist_name": artists[0]["name"],
                "album": (track.get("album") or {}).get("name"),
                "uri": track.get("uri"),
            }
        )

    if not entries:
        _record_sync_log(raw_json, len(items), 0)
        return {"fetched": len(items), "plays_inserted": 0}

    artist_names = {e["artist_name"] for e in entries}
    stmt = sqlite_insert(Artist).values([{"name": n} for n in artist_names])
    stmt = stmt.on_conflict_do_nothing(index_elements=["name"])
    db.session.execute(stmt)
    db.session.commit()

    artist_id_by_name = {
        a.name: a.id for a in Artist.query.filter(Artist.name.in_(artist_names)).all()
    }

    track_rows = {}
    for e in entries:
        key = e["uri"] or (e["track_name"], artist_id_by_name[e["artist_name"]])
        track_rows[key] = {
            "name": e["track_name"],
            "album": e["album"],
            "spotify_uri": e["uri"],
            "artist_id": artist_id_by_name[e["artist_name"]],
        }

    with_uri = [r for r in track_rows.values() if r["spotify_uri"]]
    without_uri = [r for r in track_rows.values() if not r["spotify_uri"]]

    if with_uri:
        stmt = sqlite_insert(Track).values(with_uri)
        stmt = stmt.on_conflict_do_nothing(index_elements=["spotify_uri"])
        db.session.execute(stmt)

    if without_uri:
        existing = {
            (t.name, t.artist_id)
            for t in Track.query.filter(Track.spotify_uri.is_(None)).all()
        }
        to_insert = [r for r in without_uri if (r["name"], r["artist_id"]) not in existing]
        if to_insert:
            db.session.execute(sqlite_insert(Track).values(to_insert))

    db.session.commit()

    all_tracks = Track.query.all()
    track_id_by_uri = {t.spotify_uri: t.id for t in all_tracks if t.spotify_uri}
    track_id_by_name_artist = {(t.name, t.artist_id): t.id for t in all_tracks}

    play_rows = []
    for e in entries:
        artist_id = artist_id_by_name[e["artist_name"]]
        track_id = (
            track_id_by_uri.get(e["uri"])
            if e["uri"]
            else track_id_by_name_artist.get((e["track_name"], artist_id))
        )
        if track_id is None:
            continue
        play_rows.append(
            {"track_id": track_id, "played_at": e["played_at"], "ms_played": e["ms_played"]}
        )

    inserted = 0
    if play_rows:
        before = db.session.query(PlayEvent).count()
        stmt = sqlite_insert(PlayEvent).values(play_rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["track_id", "played_at", "ms_played"])
        db.session.execute(stmt)
        db.session.commit()
        after = db.session.query(PlayEvent).count()
        inserted = after - before

    _record_sync_log(raw_json, len(items), inserted)
    return {"fetched": len(items), "plays_inserted": inserted}


def get_followed_artists(access_token, max_items=MAX_LIBRARY_ITEMS):
    """Returns the artists the user follows, most recently followed first."""

    artists = []
    after = None
    while len(artists) < max_items:
        params = {"type": "artist", "limit": 50}
        if after:
            params["after"] = after
        resp = requests.get(
            FOLLOWING_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
            timeout=10,
        )
        if resp.status_code == 403:
            raise SpotifyScopeError("missing user-follow-read scope")
        resp.raise_for_status()
        data = resp.json().get("artists", {})
        items = data.get("items", [])
        artists.extend(items)
        after = data.get("cursors", {}).get("after")
        if not after or not items:
            break
    return artists[:max_items]


def get_user_playlists(access_token, max_items=MAX_LIBRARY_ITEMS):
    """Returns the current user's playlists (owned and followed)."""

    playlists = []
    offset = 0
    while len(playlists) < max_items:
        resp = requests.get(
            PLAYLISTS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"limit": 50, "offset": offset},
            timeout=10,
        )
        if resp.status_code == 403:
            raise SpotifyScopeError("missing playlist-read-private scope")
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        playlists.extend(i for i in items if i is not None)
        if not data.get("next") or not items:
            break
        offset += 50
    return playlists[:max_items]
