from models.favorite import Favorite
from models.calendar_event import CalendarEvent


def get_user_favorite_ids(user_id, event_ids):
    """Read-only helper: returns the subset of event_ids the user has favorited."""
    if not user_id or not event_ids:
        return set()

    rows = (
        Favorite.query
        .filter(
            Favorite.user_id == user_id,
            Favorite.event_id.in_(event_ids)
        )
        .all()
    )

    return {row.event_id for row in rows}


def get_user_calendar_ids(user_id, event_ids):
    """Read-only helper: returns the subset of event_ids the user has added to their calendar."""
    if not user_id or not event_ids:
        return set()

    rows = (
        CalendarEvent.query
        .filter(
            CalendarEvent.user_id == user_id,
            CalendarEvent.event_id.in_(event_ids)
        )
        .all()
    )

    return {row.event_id for row in rows}
