import json
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from sqlalchemy import func

from app import db
from app.models import Artist, PlayEvent, SpotifyToken, SyncLog, Track
from app.spotify_api import (
    SpotifyScopeError,
    build_authorize_url,
    exchange_code_for_token,
    get_followed_artists,
    get_user_playlists,
    get_valid_access_token,
    save_token,
    sync_recent_plays,
)

bp = Blueprint("main", __name__)


@bp.app_context_processor
def inject_connection_status():
    return {"spotify_connected": SpotifyToken.query.first() is not None}

# Plays shorter than this are treated as skips and excluded from "top" rankings,
# but still counted in the raw history and overall stats.
MIN_MS_FOR_TOP = 30_000

RANGE_LABELS = {
    "7d": "Últimos 7 días",
    "30d": "Últimos 30 días",
    "90d": "Últimos 90 días",
    "year": "Este año",
    "all": "Todo el tiempo",
}

LIMIT_OPTIONS = [50, 100, 500]


def _range_start(range_key):
    now = datetime.utcnow()
    if range_key == "7d":
        return now - timedelta(days=7)
    if range_key == "30d":
        return now - timedelta(days=30)
    if range_key == "90d":
        return now - timedelta(days=90)
    if range_key == "year":
        return datetime(now.year, 1, 1)
    return None


def _apply_range(query, range_key):
    start = _range_start(range_key)
    if start:
        query = query.filter(PlayEvent.played_at >= start)
    return query


@bp.route("/")
def index():
    total_plays = db.session.query(func.count(PlayEvent.id)).scalar() or 0
    total_ms = db.session.query(func.coalesce(func.sum(PlayEvent.ms_played), 0)).scalar() or 0
    unique_tracks = db.session.query(func.count(func.distinct(PlayEvent.track_id))).scalar() or 0
    unique_artists = (
        db.session.query(func.count(func.distinct(Track.artist_id)))
        .join(PlayEvent, PlayEvent.track_id == Track.id)
        .scalar()
        or 0
    )

    recent = (
        db.session.query(PlayEvent, Track, Artist)
        .join(Track, PlayEvent.track_id == Track.id)
        .join(Artist, Track.artist_id == Artist.id)
        .order_by(PlayEvent.played_at.desc())
        .limit(15)
        .all()
    )

    top_tracks_30d = _top_tracks_query("30d").limit(5).all()
    top_artists_30d = _top_artists_query("30d").limit(5).all()

    stats = {
        "total_plays": total_plays,
        "total_hours": round(total_ms / 1000 / 60 / 60, 1),
        "unique_tracks": unique_tracks,
        "unique_artists": unique_artists,
    }

    return render_template(
        "index.html",
        stats=stats,
        recent=recent,
        top_tracks=top_tracks_30d,
        top_artists=top_artists_30d,
        has_data=total_plays > 0,
    )


def _top_tracks_query(range_key):
    q = (
        db.session.query(
            Track,
            Artist,
            func.count(PlayEvent.id).label("play_count"),
            func.sum(PlayEvent.ms_played).label("total_ms"),
        )
        .join(PlayEvent, PlayEvent.track_id == Track.id)
        .join(Artist, Track.artist_id == Artist.id)
        .filter(PlayEvent.ms_played >= MIN_MS_FOR_TOP)
        .group_by(Track.id)
        .order_by(func.count(PlayEvent.id).desc())
    )
    return _apply_range(q, range_key)


def _top_artists_query(range_key):
    q = (
        db.session.query(
            Artist,
            func.count(PlayEvent.id).label("play_count"),
            func.sum(PlayEvent.ms_played).label("total_ms"),
        )
        .join(Track, Track.artist_id == Artist.id)
        .join(PlayEvent, PlayEvent.track_id == Track.id)
        .filter(PlayEvent.ms_played >= MIN_MS_FOR_TOP)
        .group_by(Artist.id)
        .order_by(func.count(PlayEvent.id).desc())
    )
    return _apply_range(q, range_key)


def _top_albums_query(range_key):
    q = (
        db.session.query(
            Track.album,
            Artist,
            func.count(PlayEvent.id).label("play_count"),
            func.sum(PlayEvent.ms_played).label("total_ms"),
        )
        .join(PlayEvent, PlayEvent.track_id == Track.id)
        .join(Artist, Track.artist_id == Artist.id)
        .filter(PlayEvent.ms_played >= MIN_MS_FOR_TOP)
        .filter(Track.album.isnot(None))
        .group_by(Track.album, Artist.id)
        .order_by(func.count(PlayEvent.id).desc())
    )
    return _apply_range(q, range_key)


@bp.route("/top")
def top():
    kind = request.args.get("type", "tracks")
    range_key = request.args.get("range", "30d")
    if range_key not in RANGE_LABELS:
        range_key = "30d"

    limit = request.args.get("limit", 50, type=int)
    if limit not in LIMIT_OPTIONS:
        limit = 50

    if kind == "artists":
        rows = _top_artists_query(range_key).limit(limit).all()
    elif kind == "albums":
        rows = _top_albums_query(range_key).limit(limit).all()
    else:
        kind = "tracks"
        rows = _top_tracks_query(range_key).limit(limit).all()

    return render_template(
        "top.html",
        kind=kind,
        range_key=range_key,
        range_labels=RANGE_LABELS,
        limit=limit,
        limit_options=LIMIT_OPTIONS,
        rows=rows,
    )


@bp.route("/history")
def history():
    page = request.args.get("page", 1, type=int)
    per_page = 40

    pagination = (
        db.session.query(PlayEvent, Track, Artist)
        .join(Track, PlayEvent.track_id == Track.id)
        .join(Artist, Track.artist_id == Artist.id)
        .order_by(PlayEvent.played_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return render_template("history.html", pagination=pagination)


@bp.route("/login")
def login():
    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    url = build_authorize_url(
        client_id=current_app.config["SPOTIFY_CLIENT_ID"],
        redirect_uri=current_app.config["SPOTIFY_REDIRECT_URI"],
        scope=current_app.config["SPOTIFY_SCOPE"],
        state=state,
    )
    return redirect(url)


@bp.route("/callback")
def callback():
    error = request.args.get("error")
    if error:
        flash(f"Spotify denegó la conexión: {error}", "danger")
        return redirect(url_for("main.index"))

    state = request.args.get("state")
    if not state or state != session.pop("oauth_state", None):
        flash("La solicitud de conexión no es válida, intenta de nuevo.", "danger")
        return redirect(url_for("main.index"))

    code = request.args.get("code")
    payload = exchange_code_for_token(
        code=code,
        client_id=current_app.config["SPOTIFY_CLIENT_ID"],
        client_secret=current_app.config["SPOTIFY_CLIENT_SECRET"],
        redirect_uri=current_app.config["SPOTIFY_REDIRECT_URI"],
    )
    save_token(payload)
    flash("Cuenta de Spotify conectada.", "success")
    return redirect(url_for("main.index"))


@bp.route("/sync", methods=["POST"])
def sync():
    stats = sync_recent_plays(
        client_id=current_app.config["SPOTIFY_CLIENT_ID"],
        client_secret=current_app.config["SPOTIFY_CLIENT_SECRET"],
    )
    if stats is None:
        flash("Conecta tu cuenta de Spotify primero.", "warning")
        return redirect(url_for("main.login"))

    flash(
        f"Listo: {stats['plays_inserted']} reproducción(es) nueva(s) importada(s) "
        f"de las últimas {stats['fetched']} de Spotify.",
        "success",
    )
    return redirect(url_for("main.index"))


@bp.route("/raw")
def raw():
    logs = SyncLog.query.order_by(SyncLog.synced_at.desc()).limit(20).all()
    pretty_logs = [
        {
            "id": log.id,
            "synced_at": log.synced_at,
            "items_fetched": log.items_fetched,
            "plays_inserted": log.plays_inserted,
            "pretty_json": json.dumps(json.loads(log.raw_response), indent=2, ensure_ascii=False),
        }
        for log in logs
    ]
    return render_template("raw.html", logs=pretty_logs)


def _normalize_artist(a):
    images = a.get("images") or []
    return {
        "name": a.get("name") or "Sin nombre",
        "url": (a.get("external_urls") or {}).get("spotify", "#"),
        "image": images[-1]["url"] if images else None,
    }


def _normalize_playlist(p):
    images = p.get("images") or []
    owner = p.get("owner") or {}
    return {
        "name": p.get("name") or "Sin nombre",
        "url": (p.get("external_urls") or {}).get("spotify", "#"),
        "image": images[0]["url"] if images else None,
        "owner": owner.get("display_name") or "",
    }


@bp.route("/library")
def library():
    if SpotifyToken.query.first() is None:
        flash("Conecta tu cuenta de Spotify primero.", "warning")
        return redirect(url_for("main.login"))

    access_token = get_valid_access_token(
        client_id=current_app.config["SPOTIFY_CLIENT_ID"],
        client_secret=current_app.config["SPOTIFY_CLIENT_SECRET"],
    )

    try:
        artists = [_normalize_artist(a) for a in get_followed_artists(access_token)]
        playlists = [_normalize_playlist(p) for p in get_user_playlists(access_token)]
        scope_error = False
    except SpotifyScopeError:
        artists, playlists = [], []
        scope_error = True

    return render_template(
        "library.html",
        artists=artists,
        playlists=playlists,
        scope_error=scope_error,
    )
