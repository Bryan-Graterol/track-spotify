from datetime import datetime

from app import db


class Artist(db.Model):
    __tablename__ = "artists"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, unique=True)

    tracks = db.relationship("Track", back_populates="artist")


class Track(db.Model):
    __tablename__ = "tracks"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(500), nullable=False)
    album = db.Column(db.String(500))
    spotify_uri = db.Column(db.String(64), unique=True)
    artist_id = db.Column(db.Integer, db.ForeignKey("artists.id"), nullable=False)

    artist = db.relationship("Artist", back_populates="tracks")
    plays = db.relationship("PlayEvent", back_populates="track")

    __table_args__ = (
        db.Index("ix_tracks_name_artist", "name", "artist_id"),
    )


class PlayEvent(db.Model):
    __tablename__ = "play_events"

    id = db.Column(db.Integer, primary_key=True)
    track_id = db.Column(db.Integer, db.ForeignKey("tracks.id"), nullable=False)
    played_at = db.Column(db.DateTime, nullable=False)
    ms_played = db.Column(db.Integer, nullable=False, default=0)

    track = db.relationship("Track", back_populates="plays")

    __table_args__ = (
        db.UniqueConstraint("track_id", "played_at", "ms_played", name="uq_play_event"),
        db.Index("ix_play_events_played_at", "played_at"),
    )


class SpotifyToken(db.Model):
    """Single-row table holding the OAuth tokens for the connected account."""

    __tablename__ = "spotify_token"

    id = db.Column(db.Integer, primary_key=True)
    access_token = db.Column(db.String(500), nullable=False)
    refresh_token = db.Column(db.String(500), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)


class SyncLog(db.Model):
    """Raw JSON response from each /me/player/recently-played call, kept for inspection."""

    __tablename__ = "sync_logs"

    id = db.Column(db.Integer, primary_key=True)
    synced_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    raw_response = db.Column(db.Text, nullable=False)
    items_fetched = db.Column(db.Integer, nullable=False, default=0)
    plays_inserted = db.Column(db.Integer, nullable=False, default=0)
