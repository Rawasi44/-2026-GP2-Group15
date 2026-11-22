from datetime import datetime
from extensions import db


class CalendarEvent(db.Model):
    __tablename__ = "calendar_events"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.user_id"),
        nullable=False
    )

    event_id = db.Column(
        db.Integer,
        db.ForeignKey("events.id"),
        nullable=False
    )

    scheduled_date = db.Column(
        db.Date,
        nullable=False
    )

    # ------------------------------------------------------------------
    # Final Sprint Priority 1.6 -- the exact approved attendance slot.
    #
    # REQUIRES migrations/0002_add_calendar_attendance_times.sql to be
    # run first. All four columns are nullable so every pre-existing
    # date-only row (and every row saved without a confirmed time)
    # remains valid: NULL simply means "date only, no time saved".
    #
    # A single "is this estimated" boolean cannot represent a MIXED
    # slot -- an official, unmovable start time with an AFAQ-estimated
    # end/duration (e.g. a football match with a verified 20:00 kickoff
    # but no listed official end time). Two independent flags cover
    # all three real cases, plus "no time saved":
    #
    #   start_time_is_estimated / end_time_is_estimated
    #     False -> that boundary is the event's own verified, listed
    #              value -- an official time, never a guess.
    #     True  -> AFAQ selected or estimated that boundary.
    #     None  -> no time was saved for this entry (both are always
    #              None/not-None together).
    #
    #   (False, False) -> fully official
    #   (True,  True)  -> fully estimated/flexible
    #   (False, True)  -> mixed: official start, AFAQ-estimated end
    # ------------------------------------------------------------------
    attendance_start_time = db.Column(
        db.Time,
        nullable=True
    )

    attendance_end_time = db.Column(
        db.Time,
        nullable=True
    )

    start_time_is_estimated = db.Column(
        db.Boolean,
        nullable=True
    )

    end_time_is_estimated = db.Column(
        db.Boolean,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "event_id",
            name="uq_calendar_user_event"
        ),
    )

    user = db.relationship(
        "User",
        backref="calendar_entries"
    )

    event = db.relationship(
        "Event",
        backref="in_calendars_of"
    )
