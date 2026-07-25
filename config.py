import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{BASE_DIR / 'instance' / 'spotify_track.db'}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SPOTIFY_CLIENT_ID = os.environ.get("client_id")
    SPOTIFY_CLIENT_SECRET = os.environ.get("client_secret")
    SPOTIFY_REDIRECT_URI = os.environ.get("redirect_uri", "http://127.0.0.1:5000/callback")
    SPOTIFY_SCOPE = (
        "user-read-recently-played "
        "user-follow-read "
        "playlist-read-private "
        "playlist-read-collaborative"
    )
