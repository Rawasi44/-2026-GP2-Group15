"""
Explicit, reviewed migration step for Phase 1 of the Final Sprint spec.

app.py's db.create_all() deliberately EXCLUDES the new user_preferences
table (see app.py) -- simply starting the app must never silently apply a
new schema change during this review phase. This script is the explicit,
approved step that actually creates it.

NOT EXECUTED BY CLAUDE. Review migrations/0001_create_user_preferences.sql
first, back up the database, then run this yourself:

    python apply_pending_migrations.py

Safe to run more than once: it only ever creates user_preferences if it
doesn't already exist, and touches no other table or row.
"""

from app import create_app
from extensions import db
from models.user_preference import UserPreference


def run():
    app = create_app()
    with app.app_context():
        # Flask-SQLAlchemy's own db.create_all() takes no `tables` filter
        # (only bind_key) -- creating exactly one table goes through the
        # underlying SQLAlchemy MetaData/engine API instead.
        # checkfirst=True keeps this idempotent: it is a no-op if the
        # table already exists.
        db.metadata.create_all(
            bind=db.engine,
            tables=[UserPreference.__table__],
            checkfirst=True,
        )
    print(f"Ensured table exists: {UserPreference.__tablename__}")


if __name__ == "__main__":
    run()
