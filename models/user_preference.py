"""
UserPreference — explicit, chatbot-confirmed event-category preferences.

Relationship: one User has zero or more UserPreference rows; each row
belongs to exactly one User (one category per user, enforced by a unique
constraint so re-confirming the same category updates instead of
duplicating).

This is intentionally separate from implicit interaction data (Favorites,
Ratings, CalendarEvent, Search): those are never modified by this model.
UserPreference only stores what the user explicitly confirmed through the
chatbot — it never stores a dynamically calculated recommendation weight.
"""

from datetime import datetime
from extensions import db


class UserPreference(db.Model):
    __tablename__ = "user_preferences"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.user_id"),
        nullable=False
    )

    # Canonical category name — must match an actual Event.category value
    # in use at confirmation time (validated in services/preference_service.py,
    # never trusted blindly from chatbot/user input).
    category = db.Column(db.String(100), nullable=False)

    # Where this preference came from. Currently only "chatbot_explicit" is
    # produced, but the column exists so a future explicit source doesn't
    # require a schema change.
    source = db.Column(db.String(30), nullable=False, default="chatbot_explicit")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "category", name="uq_preference_user_category"),
    )

    user = db.relationship("User", backref="preferences")
