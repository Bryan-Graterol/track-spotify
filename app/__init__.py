from flask import Flask
from flask_sqlalchemy import SQLAlchemy

from config import BASE_DIR, Config

db = SQLAlchemy()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    (BASE_DIR / "instance").mkdir(parents=True, exist_ok=True)

    db.init_app(app)

    from app.routes import bp as main_bp
    app.register_blueprint(main_bp)

    with app.app_context():
        db.create_all()

    return app
