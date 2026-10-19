"""
Sprint 4 — Content-Based Recommendation System.

Flow (matches the GP2 report methodology):

    User Interactions (Favorites, Calendar, Ratings, Search History)
        -> Weighted User Profile

    Event Content (title + category + description)
        -> TF-IDF vectors

    User Profile Vector vs. Current Event Vectors
        -> Cosine Similarity
        -> Ranking
        -> Top personalized recommendations


Recommendation eligibility:
- Event.is_active must be True.
- The event must not have already ended.

Historical interactions with expired or inactive events are still allowed
to shape the user's preference profile because they are real signals of
what the user liked in the past. However, expired/inactive events are
never returned as recommendation results.

This module is read-only: it never creates, updates, or deletes any
Favorite, CalendarEvent, Rating, Search, or Event row.

Recently Viewed is intentionally NOT used as an interaction source.
"""

from datetime import date

from sqlalchemy import func, desc

from extensions import db
from models.event import Event
from models.favorite import Favorite
from models.calendar_event import CalendarEvent
from models.rating import Rating
from models.search import Search


try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

except ImportError:
    TfidfVectorizer = None
    cosine_similarity = None


# ---------------------------------------------------------------------------
# Centralized interaction weights
# ---------------------------------------------------------------------------

INTERACTION_WEIGHTS = {
    "calendar": 4,
    "favorite": 3,
    "rating_5": 5,
    "rating_4": 4,
    "rating_3": 2,
    "rating_low": 0,
    "search": 1,
}


RECOMMENDATION_LIMIT = 6


# ---------------------------------------------------------------------------
# Current-event eligibility
# ---------------------------------------------------------------------------

def _current_event_conditions(today=None):
    """
    SQL conditions for events that are eligible to be recommended.

    An event is eligible when:
    1. It is active.
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


# ---------------------------------------------------------------------------
# Event content representation
# ---------------------------------------------------------------------------

def _event_text(event):
    """
    Build the textual representation used by TF-IDF.

    Content:
        title + category + description

    Empty fields are safely ignored.
    """

    parts = [
        event.title,
        event.category,
        event.description,
    ]

    parts = [
        part.strip()
        for part in parts
        if (
            part
            and isinstance(part, str)
            and part.strip()
        )
    ]

    return " ".join(parts)


def _all_events():
    """
    Return all historical events, including expired/inactive events.

    This is intentional.

    Historical interactions with old events can still help identify
    a user's interests, even though those events are not eligible
    to be recommended again.
    """

    return Event.query.all()


def _active_events():
    """
    Return only events currently eligible for recommendation.

    This excludes:
    - soft-deleted events
    - events whose end date has already passed
    """

    return (
        Event.query
        .filter(
            *_current_event_conditions()
        )
        .all()
    )


def _build_vectorizer_and_matrix(events):
    """
    Fit one TF-IDF vectorizer over the supplied events.

    Returns:
        (vectorizer, matrix)

    Returns:
        (None, None)

    when there is no usable content.
    """

    if (
        not events
        or TfidfVectorizer is None
    ):
        return None, None

    texts = [
        _event_text(event)
        for event in events
    ]

    if not any(
        text.strip()
        for text in texts
    ):
        return None, None

    vectorizer = TfidfVectorizer(
        stop_words="english"
    )

    try:
        matrix = (
            vectorizer
            .fit_transform(
                texts
            )
        )

    except ValueError:
        return None, None

    return (
        vectorizer,
        matrix,
    )


# ---------------------------------------------------------------------------
# User interaction data
# ---------------------------------------------------------------------------

def _gather_event_interaction_weights(user_id):
    """
    Build:

        {
            event_id: combined_weight
        }

    from the current user's:
    - Favorites
    - Calendar
    - Ratings

    Multiple positive interactions with the same event are summed.
    """

    weights = {}

    # Favorites
    for favorite in (
        Favorite.query
        .filter_by(
            user_id=user_id
        )
        .all()
    ):

        weights[favorite.event_id] = (
            weights.get(
                favorite.event_id,
                0
            )
            + INTERACTION_WEIGHTS[
                "favorite"
            ]
        )

    # Personal Calendar
    for entry in (
        CalendarEvent.query
        .filter_by(
            user_id=user_id
        )
        .all()
    ):

        weights[entry.event_id] = (
            weights.get(
                entry.event_id,
                0
            )
            + INTERACTION_WEIGHTS[
                "calendar"
            ]
        )

    # Ratings
    rating_weight_by_value = {
        5: INTERACTION_WEIGHTS[
            "rating_5"
        ],
        4: INTERACTION_WEIGHTS[
            "rating_4"
        ],
        3: INTERACTION_WEIGHTS[
            "rating_3"
        ],
    }

    for rating in (
        Rating.query
        .filter_by(
            user_id=user_id
        )
        .all()
    ):

        weight = (
            rating_weight_by_value
            .get(
                rating.value,
                INTERACTION_WEIGHTS[
                    "rating_low"
                ]
            )
        )

        if weight > 0:

            weights[rating.event_id] = (
                weights.get(
                    rating.event_id,
                    0
                )
                + weight
            )

    return weights


def _gather_search_keywords(user_id):
    """
    Return the user's previous search keywords.

    They are combined into one text signal for the user profile.
    """

    rows = (
        Search.query
        .filter_by(
            user_id=user_id
        )
        .all()
    )

    return [
        row.keyword
        for row in rows
        if (
            row.keyword
            and row.keyword.strip()
        )
    ]


# ---------------------------------------------------------------------------
# Cold-start fallback
# ---------------------------------------------------------------------------

def _fallback_recommendations(
    active_events,
    limit,
    exclude_ids=None,
):
    """
    Cold-start fallback.

    Used when:
    - the user has no interaction history, or
    - TF-IDF cannot build a usable profile.

    Strategy:
    1. Current Top Rated events.
    2. Other current events.

    Expired events are never included.
    """

    exclude_ids = (
        exclude_ids
        or set()
    )

    # Only current, active Top Rated events.
    top_rated_rows = (
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
                "cnt"
            )
        )
        .join(
            Rating,
            Rating.event_id == Event.id
        )
        .filter(
            *_current_event_conditions()
        )
        .group_by(
            Event.id
        )
        .order_by(
            desc("avg_rating"),
            desc("cnt")
        )
        .all()
    )

    ordered = [
        row[0]
        for row in top_rated_rows
    ]

    ordered_ids = {
        event.id
        for event in ordered
    }

    # Add current events that do not yet appear
    # in the Top Rated results.
    ordered += [
        event
        for event in active_events
        if event.id not in ordered_ids
    ]

    result = []
    seen_ids = set()

    # Prefer events not already saved.
    for event in ordered:

        if event.id in seen_ids:
            continue

        if event.id in exclude_ids:
            continue

        result.append(
            event
        )

        seen_ids.add(
            event.id
        )

        if len(result) >= limit:
            return result

    # If the catalog is too small, allow previously
    # saved current events to fill the section.
    for event in ordered:

        if event.id in seen_ids:
            continue

        result.append(
            event
        )

        seen_ids.add(
            event.id
        )

        if len(result) >= limit:
            break

    return result


# ---------------------------------------------------------------------------
# Main recommendation entry point
# ---------------------------------------------------------------------------

def get_recommendations_for_user(
    user_id,
    limit=RECOMMENDATION_LIMIT,
):
    """
    Return up to `limit` personalized Event objects.

    Only events that are:
    - active
    - not expired

    can ever be returned.

    Historical expired events may still contribute to the user's
    profile if the user previously interacted with them.
    """

    if not user_id:
        return []

    # ---------------------------------------------------------
    # Candidate events
    #
    # These are already filtered so expired events never enter
    # the recommendation candidate pool.
    # ---------------------------------------------------------

    active_events = (
        _active_events()
    )

    if not active_events:
        return []

    # ---------------------------------------------------------
    # Gather personalization signals
    # ---------------------------------------------------------

    event_weights = (
        _gather_event_interaction_weights(
            user_id
        )
    )

    search_keywords = (
        _gather_search_keywords(
            user_id
        )
    )

    has_signal = (
        bool(event_weights)
        or bool(search_keywords)
    )

    # Events already explicitly saved by the user.
    fav_and_calendar_ids = {
        row.event_id
        for row in (
            Favorite.query
            .filter_by(
                user_id=user_id
            )
            .all()
        )
    } | {
        row.event_id
        for row in (
            CalendarEvent.query
            .filter_by(
                user_id=user_id
            )
            .all()
        )
    }

    # ---------------------------------------------------------
    # Cold Start
    # ---------------------------------------------------------

    if not has_signal:

        return _fallback_recommendations(
            active_events,
            limit
        )

    # ---------------------------------------------------------
    # Build TF-IDF vocabulary
    #
    # Historical events remain here deliberately because they
    # may represent the user's previous interests.
    # ---------------------------------------------------------

    all_events = (
        _all_events()
    )

    (
        vectorizer,
        matrix,
    ) = (
        _build_vectorizer_and_matrix(
            all_events
        )
    )

    if vectorizer is None:

        return _fallback_recommendations(
            active_events,
            limit,
            exclude_ids=(
                fav_and_calendar_ids
            ),
        )

    id_to_index = {
        event.id: index
        for index, event
        in enumerate(
            all_events
        )
    }

    # ---------------------------------------------------------
    # Weighted User Profile Vector
    # ---------------------------------------------------------

    user_vector = None

    for (
        event_id,
        weight,
    ) in event_weights.items():

        index = (
            id_to_index.get(
                event_id
            )
        )

        if index is None:
            continue

        weighted_row = (
            matrix[index]
            * weight
        )

        user_vector = (
            weighted_row
            if user_vector is None
            else user_vector
            + weighted_row
        )

    # ---------------------------------------------------------
    # Search History signal
    # ---------------------------------------------------------

    if (
        search_keywords
        and INTERACTION_WEIGHTS[
            "search"
        ] > 0
    ):

        search_text = " ".join(
            search_keywords
        )

        try:

            search_vector = (
                vectorizer.transform(
                    [
                        search_text
                    ]
                )
                * INTERACTION_WEIGHTS[
                    "search"
                ]
            )

            user_vector = (
                search_vector
                if user_vector is None
                else user_vector
                + search_vector
            )

        except ValueError:
            pass

    # ---------------------------------------------------------
    # No usable profile
    # ---------------------------------------------------------

    if user_vector is None:

        return _fallback_recommendations(
            active_events,
            limit,
            exclude_ids=(
                fav_and_calendar_ids
            ),
        )

    # ---------------------------------------------------------
    # Cosine Similarity
    #
    # IMPORTANT:
    # active_events contains current/non-expired events only.
    # Therefore expired events cannot appear here.
    # ---------------------------------------------------------

    active_indices = [
        id_to_index[event.id]
        for event in active_events
    ]

    active_matrix = (
        matrix[
            active_indices
        ]
    )

    similarities = (
        cosine_similarity(
            user_vector,
            active_matrix
        )[0]
    )

    scored = list(
        zip(
            active_events,
            similarities
        )
    )

    scored.sort(
        key=lambda pair: pair[1],
        reverse=True
    )

    ordered_events = [
        event
        for event, _score
        in scored
    ]

    # ---------------------------------------------------------
    # Prefer new events
    # ---------------------------------------------------------

    result = []
    seen_ids = set()

    for event in ordered_events:

        if (
            event.id
            in fav_and_calendar_ids
        ):
            continue

        result.append(
            event
        )

        seen_ids.add(
            event.id
        )

        if len(result) >= limit:
            return result

    # ---------------------------------------------------------
    # Top up with already-saved current events if needed.
    # ---------------------------------------------------------

    if len(result) < limit:

        for event in ordered_events:

            if event.id in seen_ids:
                continue

            result.append(
                event
            )

            seen_ids.add(
                event.id
            )

            if len(result) >= limit:
                break

    # ---------------------------------------------------------
    # Extremely sparse catalog fallback
    # ---------------------------------------------------------

    if len(result) < limit:

        fallback = (
            _fallback_recommendations(
                active_events,
                limit
            )
        )

        for event in fallback:

            if event.id in seen_ids:
                continue

            result.append(
                event
            )

            seen_ids.add(
                event.id
            )

            if len(result) >= limit:
                break

    return result[:limit]