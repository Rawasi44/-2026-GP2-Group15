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
    from models.user_preference import UserPreference

    # Import blueprints
    from routes.auth import auth_bp
    from routes.events import events_bp
    from routes.admin import admin_bp
    from routes.favorites import favorites_bp
    from routes.personal_calendar import calendar_bp
    from routes.smart_schedule import smart_schedule_bp
    from routes.chatbot import chatbot_bp

    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(events_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(favorites_bp)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(smart_schedule_bp)
    app.register_blueprint(chatbot_bp)

    # Create tables if they do not exist.
    #
    # EXCEPT user_preferences: that table is new in this review phase and
    # must only be created through an explicit, reviewed step (run
    # migrations/0001_create_user_preferences.sql yourself, or run
    # apply_pending_migrations.py once you've approved it) -- simply
    # starting the app must never silently apply a new schema change.
    # Every other table keeps the exact behavior this project already had.
    with app.app_context():
        pending_tables = [
            table
            for table in db.metadata.sorted_tables
            if table.name != UserPreference.__tablename__
        ]
        # Flask-SQLAlchemy's own db.create_all() takes no `tables` filter
        # (only bind_key) -- selective creation goes through the
        # underlying SQLAlchemy MetaData/engine API instead.
        db.metadata.create_all(bind=db.engine, tables=pending_tables, checkfirst=True)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)