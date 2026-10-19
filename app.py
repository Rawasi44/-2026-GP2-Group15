from flask import Flask
from config import Config
from extensions import db, login_manager, mail


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)
    mail.init_app(app)

    login_manager.login_view = "auth.login"

    # Import models so SQLAlchemy knows all tables
    from models.user import User
    from models.event import Event
    from models.search import Search
    from models.rating import Rating
    from models.review import Review
    from models.favorite import Favorite
    from models.calendar_event import CalendarEvent

    # Import blueprints
    from routes.auth import auth_bp
    from routes.events import events_bp
    from routes.admin import admin_bp
    from routes.favorites import favorites_bp
    from routes.personal_calendar import calendar_bp
    from routes.smart_schedule import smart_schedule_bp

    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(events_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(favorites_bp)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(smart_schedule_bp)

    # Create tables if they do not exist
    with app.app_context():
        db.create_all()

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)