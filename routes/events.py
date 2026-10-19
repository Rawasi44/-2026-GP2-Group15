from datetime import date, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from sqlalchemy import or_, func, desc
from flask_login import current_user

from models.search import Search
from extensions import db
from models.event import Event
from models.rating import Rating
from models.review import Review

from services.activity_service import (
    get_user_favorite_ids,
    get_user_calendar_ids,
)

from services.recommendation_service import get_recommendations_for_user


events_bp = Blueprint("events", __name__)


# ---------------------------------------------------------------------------
# Current / non-expired event helpers
# ---------------------------------------------------------------------------

def current_event_conditions(today=None):
    """
    Conditions used whenever events are displayed to users.

    An event is considered available when:
    1. It has not been soft-deleted.
    2. Its effective end date is today or later.

    If end_date is NULL, start_date is treated as the event's end date.
    """
    today = today or date.today()

    return (
        Event.is_active == True,
        func.coalesce(
            Event.end_date,
            Event.start_date
        ) >= today,
    )


def is_event_expired(event, today=None):
    """
    Return True when the event has already ended.

    Multi-day event:
        effective end = end_date

    Single-day event:
        effective end = start_date
    """
    today = today or date.today()

    effective_end_date = (
        event.end_date
        or event.start_date
    )

    return effective_end_date < today


# ---------------------------------------------------------------------------
# Rating helper
# ---------------------------------------------------------------------------

def build_event_ratings_map(event_ids):
    """
    Read-only helper for the UI.

    Returns:
        {
            event_id: {
                "avg": x.x,
                "count": n
            }
        }

    for events that have at least one rating.
    """
    if not event_ids:
        return {}

    rows = (
        db.session.query(
            Rating.event_id,
            func.avg(Rating.value).label("avg_rating"),
            func.count(Rating.id).label("ratings_count")
        )
        .filter(
            Rating.event_id.in_(event_ids)
        )
        .group_by(
            Rating.event_id
        )
        .all()
    )

    return {
        event_id: {
            "avg": round(
                float(avg_rating),
                1
            ),
            "count": ratings_count
        }
        for (
            event_id,
            avg_rating,
            ratings_count
        ) in rows
    }


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

@events_bp.route("/")
def home():
    today = date.today()

    # ---------------------------------------------------------
    # Main event cards
    # Only active AND non-expired events are shown.
    # ---------------------------------------------------------
    events = (
        Event.query
        .filter(
            *current_event_conditions(today)
        )
        .limit(6)
        .all()
    )

    # ---------------------------------------------------------
    # Top Rated
    # Expired events must not appear here either.
    # ---------------------------------------------------------
    top_rated_query = (
        db.session.query(
            Event,
            func.avg(
                Rating.value
            ).label(
                "avg_rating"
            ),
            func.count(
                Rating.id
            ).label(
                "ratings_count"
            )
        )
        .join(
            Rating,
            Rating.event_id == Event.id
        )
        .filter(
            *current_event_conditions(today)
        )
        .group_by(
            Event.id
        )
        .order_by(
            desc("avg_rating"),
            desc("ratings_count")
        )
        .limit(3)
        .all()
    )

    top_rated_events = [
        {
            "event": event,
            "avg_rating": round(
                float(avg_rating),
                1
            ),
            "ratings_count": ratings_count
        }
        for (
            event,
            avg_rating,
            ratings_count
        ) in top_rated_query
    ]

    # ---------------------------------------------------------
    # Ending Today
    #
    # COALESCE means:
    # - multi-day event -> end_date
    # - single-day event -> start_date
    # ---------------------------------------------------------
    ending_today_events = (
        Event.query
        .filter(
            Event.is_active == True,
            func.coalesce(
                Event.end_date,
                Event.start_date
            ) == today
        )
        .all()
    )

    # ---------------------------------------------------------
    # Personalized Recommendations — Sprint 4
    #
    # The recommendation service provides the personalized
    # ranking. We additionally remove expired events here so
    # old events can never appear on the Home page.
    # ---------------------------------------------------------
    if current_user.is_authenticated:
        recommendation_results = (
            get_recommendations_for_user(
                current_user.user_id
            )
        )

        recommended_events = [
            event
            for event in recommendation_results
            if (
                event.is_active
                and not is_event_expired(
                    event,
                    today
                )
            )
        ]

    else:
        recommended_events = []

    # ---------------------------------------------------------
    # Ratings shown on cards
    # ---------------------------------------------------------
    all_shown_ids = (
        [
            event.id
            for event in events
        ]
        + [
            item["event"].id
            for item in top_rated_events
        ]
        + [
            event.id
            for event in ending_today_events
        ]
        + [
            event.id
            for event in recommended_events
        ]
    )

    event_ratings = (
        build_event_ratings_map(
            all_shown_ids
        )
    )

    favorited_event_ids = (
        get_user_favorite_ids(
            current_user.user_id,
            all_shown_ids
        )
        if current_user.is_authenticated
        else set()
    )

    return render_template(
        "index.html",
        events=events,
        top_rated_events=top_rated_events,
        ending_today_events=ending_today_events,
        recommended_events=recommended_events,
        event_ratings=event_ratings,
        favorited_event_ids=favorited_event_ids
    )


# ---------------------------------------------------------------------------
# Events page
# ---------------------------------------------------------------------------

@events_bp.route("/events")
def events_page():
    search = (
        request.args
        .get(
            "search",
            ""
        )
        .strip()
    )

    category = (
        request.args
        .get(
            "category",
            ""
        )
        .strip()
    )

    date_filter = (
        request.args
        .get(
            "date_filter",
            ""
        )
        .strip()
    )

    today = date.today()

    # ---------------------------------------------------------
    # Base query:
    #
    # Every event shown on the Events page must be:
    # - active
    # - not expired
    # ---------------------------------------------------------
    query = (
        Event.query
        .filter(
            *current_event_conditions(today)
        )
    )

    # ---------------------------------------------------------
    # Search
    # ---------------------------------------------------------
    if search:
        words = [
            word
            for word in search.lower().split()
            if len(word) > 2
        ]

        conditions = []

        for word in words:
            conditions.append(
                Event.title.ilike(
                    f"%{word}%"
                )
            )

            conditions.append(
                Event.description.ilike(
                    f"%{word}%"
                )
            )

            conditions.append(
                Event.category.ilike(
                    f"%{word}%"
                )
            )

        if conditions:
            query = query.filter(
                or_(
                    *conditions
                )
            )

        if current_user.is_authenticated:
            new_search = Search(
                user_id=current_user.user_id,
                keyword=search
            )

            db.session.add(
                new_search
            )

            db.session.commit()

    # ---------------------------------------------------------
    # Category
    # ---------------------------------------------------------
    if category:
        query = query.filter(
            Event.category == category
        )

    # ---------------------------------------------------------
    # Date filters
    # ---------------------------------------------------------
    if date_filter == "today":
        query = query.filter(
            Event.start_date <= today,
            or_(
                Event.end_date == None,
                Event.end_date >= today
            )
        )

    elif date_filter == "tomorrow":
        tomorrow = (
            today
            + timedelta(
                days=1
            )
        )

        query = query.filter(
            Event.start_date <= tomorrow,
            or_(
                Event.end_date == None,
                Event.end_date >= tomorrow
            )
        )

    elif date_filter == "this_week":
        week_end = (
            today
            + timedelta(
                days=7
            )
        )

        query = query.filter(
            Event.start_date <= week_end,
            or_(
                Event.end_date == None,
                Event.end_date >= today
            )
        )

    events = query.all()

    # ---------------------------------------------------------
    # Category dropdown
    #
    # Don't show categories that only contain old/expired events.
    # ---------------------------------------------------------
    categories = (
        db.session.query(
            Event.category
        )
        .filter(
            *current_event_conditions(today)
        )
        .distinct()
        .all()
    )

    categories = [
        category_row[0]
        for category_row in categories
        if category_row[0]
    ]

    event_ratings = (
        build_event_ratings_map(
            [
                event.id
                for event in events
            ]
        )
    )

    favorited_event_ids = (
        get_user_favorite_ids(
            current_user.user_id,
            [
                event.id
                for event in events
            ]
        )
        if current_user.is_authenticated
        else set()
    )

    return render_template(
        "events.html",
        events=events,
        categories=categories,
        selected_search=search,
        selected_category=category,
        selected_date_filter=date_filter,
        event_ratings=event_ratings,
        favorited_event_ids=favorited_event_ids
    )


# ---------------------------------------------------------------------------
# Event details
# ---------------------------------------------------------------------------

@events_bp.route("/events/<int:event_id>")
def event_details(event_id):
    event = (
        Event.query
        .get_or_404(
            event_id
        )
    )

    # ---------------------------------------------------------
    # Do not allow users to browse:
    # - soft-deleted events
    # - expired events
    #
    # The database row itself is preserved.
    # ---------------------------------------------------------
    if (
        not event.is_active
        or is_event_expired(event)
    ):
        abort(404)

    ratings = (
        Rating.query
        .filter_by(
            event_id=event_id
        )
        .all()
    )

    ratings_count = len(
        ratings
    )

    average_rating = (
        round(
            sum(
                rating.value
                for rating in ratings
            )
            / ratings_count,
            1
        )
        if ratings_count > 0
        else None
    )

    current_user_rating = None
    is_favorited = False
    is_in_calendar = False

    if current_user.is_authenticated:
        existing = (
            Rating.query
            .filter_by(
                user_id=current_user.user_id,
                event_id=event_id
            )
            .first()
        )

        if existing:
            current_user_rating = (
                existing.value
            )

        favorite_ids = (
            get_user_favorite_ids(
                current_user.user_id,
                [
                    event_id
                ]
            )
        )

        is_favorited = (
            event_id
            in favorite_ids
        )

        calendar_ids = (
            get_user_calendar_ids(
                current_user.user_id,
                [
                    event_id
                ]
            )
        )

        is_in_calendar = (
            event_id
            in calendar_ids
        )

    reviews = (
        Review.query
        .filter_by(
            event_id=event_id
        )
        .order_by(
            Review.created_at.desc()
        )
        .all()
    )

    return render_template(
        "event_details.html",
        event=event,
        ratings=ratings,
        average_rating=average_rating,
        ratings_count=ratings_count,
        current_user_rating=current_user_rating,
        reviews=reviews,
        is_favorited=is_favorited,
        is_in_calendar=is_in_calendar,
    )


# ---------------------------------------------------------------------------
# Rating
# ---------------------------------------------------------------------------

@events_bp.route(
    "/events/<int:event_id>/rate",
    methods=["POST"]
)
def rate_event(event_id):
    if not current_user.is_authenticated:
        flash(
            "You must be logged in to rate an event.",
            "warning"
        )

        return redirect(
            url_for(
                "events.event_details",
                event_id=event_id
            )
        )

    event = (
        Event.query
        .get_or_404(
            event_id
        )
    )

    # Direct POST protection.
    if (
        not event.is_active
        or is_event_expired(event)
    ):
        flash(
            "This event has ended or is no longer available.",
            "warning"
        )

        return redirect(
            url_for(
                "events.events_page"
            )
        )

    try:
        value = int(
            request.form.get(
                "rating",
                0
            )
        )

    except ValueError:
        value = 0

    if (
        value < 1
        or value > 5
    ):
        flash(
            "Rating must be between 1 and 5.",
            "danger"
        )

        return redirect(
            url_for(
                "events.event_details",
                event_id=event_id
            )
        )

    existing = (
        Rating.query
        .filter_by(
            user_id=current_user.user_id,
            event_id=event_id
        )
        .first()
    )

    if existing:
        existing.value = value

        flash(
            "Your rating has been updated.",
            "success"
        )

    else:
        db.session.add(
            Rating(
                user_id=current_user.user_id,
                event_id=event_id,
                value=value
            )
        )

        flash(
            "Thank you for rating this event!",
            "success"
        )

    db.session.commit()

    return redirect(
        url_for(
            "events.event_details",
            event_id=event_id
        )
    )


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------

@events_bp.route(
    "/events/<int:event_id>/reviews",
    methods=["POST"]
)
def submit_review(event_id):
    if not current_user.is_authenticated:
        flash(
            "You must be logged in to write a review.",
            "warning"
        )

        return redirect(
            url_for(
                "events.event_details",
                event_id=event_id
            )
        )

    event = (
        Event.query
        .get_or_404(
            event_id
        )
    )

    # Direct POST protection.
    if (
        not event.is_active
        or is_event_expired(event)
    ):
        flash(
            "This event has ended or is no longer available.",
            "warning"
        )

        return redirect(
            url_for(
                "events.events_page"
            )
        )

    content = (
        request.form
        .get(
            "content",
            ""
        )
        .strip()
    )

    if not content:
        flash(
            "Review cannot be empty.",
            "danger"
        )

        return redirect(
            url_for(
                "events.event_details",
                event_id=event_id
            )
        )

    existing = (
        Review.query
        .filter_by(
            user_id=current_user.user_id,
            event_id=event_id
        )
        .first()
    )

    if existing:
        existing.content = content

        flash(
            "Your review has been updated.",
            "success"
        )

    else:
        db.session.add(
            Review(
                user_id=current_user.user_id,
                event_id=event_id,
                content=content
            )
        )

        flash(
            "Your review has been submitted!",
            "success"
        )

    db.session.commit()

    return redirect(
        url_for(
            "events.event_details",
            event_id=event_id
        )
    )