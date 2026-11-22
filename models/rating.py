from datetime import datetime
from extensions import db


class Rating(db.Model):
    __tablename__ = "ratings"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.user_id"), nullable=False)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    value = db.Column(db.Integer, nullable=False)  # 1 to 5
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "event_id", name="uq_rating_user_event"),
    )

    user = db.relationship("User", backref="ratings")
    event = db.relationship("Event", backref="ratings")
